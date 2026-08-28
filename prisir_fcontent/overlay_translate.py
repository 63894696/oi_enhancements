# -*- coding: utf-8 -*-
"""叠加式漫画原位翻译(第二段 · 不抹字,实底盖字)。

管线: PNG → rapidocr 检测+OCR(逐行框) → 行合并成气泡块 → 置信门控
     → 翻译(默认路由 LLM,英→中) → 原图副本上盖**实底(不透明)奶白圆角块** + 写中文译文
     → 产物 *.translated.png(原图不动)。

「不抹字」的正确读法:不是「半透明让原字透出来」(那样原字灰影会和译文打架,读不清),
而是「**干净盖住原字**」——用实底译文块完整遮蔽文字区,不破坏气泡轮廓/画面,
效果上等价于轻量抹字,但跳过 lama/inpainting(CPU 跑得起、零模型依赖)。
细描边保留气泡框轮廓,让译文块融进画面而不是贴一块突兀补丁。

mode="erase"(第三段 · 真抹字,2026-08-22 拍板推进):
  不盖块,真把原字抹掉再写译文。Otsu 抠字 + dilate → 环带复杂度分流
  (白净区采样填充 percentile 92 / 复杂区 cv2.inpaint TELEA),译空块保留原文不留白。
  direction="auto"(日/中竖排气泡→竖排,英文→横排)/"h"(横排)/"v"(竖排)。
  翻译后端照翻译插件:默认 google_gtx(免 key),配置了 baseURL+model(模型KEY)就用自定义端点。
"""
import os

from . import extract

# 叠加渲染参数
_PAD = 8                  # 底块相对文字框的外扩边距(px)
_ALPHA = 255              # 实底(不透明)——干净盖住原字,避免透出灰影和译文打架
_MIN_FONT = 12            # 译文字号下限
_MERGE_VGAP_RATIO = 1.2   # 行合并:垂直间距 < 行高*此值 且水平对齐 → 同一块
_ALIGN_RATIO = 0.5        # 行合并:水平重叠/对齐容差(左缘差 < 平均行宽*此值)
_FILL = (251, 246, 236, _ALPHA)   # 奶白实底(贴近漫画气泡白)
_EDGE = (58, 63, 60, 255)         # 细描边(贴近原气泡框深灰,维持轮廓不抢戏)
_INK = (47, 58, 52, 255)          # 译文墨色


def _line_boxes(ocr_res):
    """rapidocr 结果 → 行框列表 [{box:(x0,y0,x1,y1), text, score}],按 y 排序。"""
    lines = []
    for box, txt, sc in (ocr_res or []):
        xs = [p[0] for p in box]; ys = [p[1] for p in box]
        lines.append({"box": (min(xs), min(ys), max(xs), max(ys)),
                      "text": txt, "score": sc})
    lines.sort(key=lambda L: (L["box"][1], L["box"][0]))
    return lines


def _merge_blocks(lines):
    """把垂直相近、水平对齐的行合并成一个气泡块。返 [{box, texts:[..], score:min}]。"""
    blocks = []
    for L in lines:
        x0, y0, x1, y1 = L["box"]
        h = y1 - y0
        placed = False
        for b in blocks:
            bx0, by0, bx1, by1 = b["box"]
            vgap = y0 - by1
            # 垂直紧邻(间隙小)且水平左缘接近 → 并入
            if 0 <= vgap <= h * _MERGE_VGAP_RATIO and abs(x0 - bx0) <= max(bx1 - bx0, x1 - x0) * _ALIGN_RATIO:
                b["box"] = (min(bx0, x0), min(by0, y0), max(bx1, x1), max(by1, y1))
                b["texts"].append(L["text"])
                b["score"] = min(b["score"], L["score"])
                placed = True
                break
        if not placed:
            blocks.append({"box": (x0, y0, x1, y1), "texts": [L["text"]], "score": L["score"]})
    return blocks


def _fit_font(draw, text, bw, bh, max_size):
    """按块宽高自适应选字号(需容纳多行中文)。返 (font, [wrapped_lines])。"""
    from PIL import ImageFont
    size = max_size
    while size >= _MIN_FONT:
        try:
            f = ImageFont.truetype(_pick_cjk_font(), size)
        except Exception:  # noqa: BLE001
            f = ImageFont.load_default()
        # 简单换行:按块宽能放的字符数折行(中文按字宽≈size,留 0.95 系数防标点挤压超宽)
        max_chars = max(1, int((bw - 2 * _PAD) / max(size * 1.0, 1)))
        wrapped = [text[i:i + max_chars] for i in range(0, len(text), max_chars)] or [""]
        need_h = len(wrapped) * (size + 4)
        # 高度必须真容纳(不再 +size 白送一行),否则缩字号重试 → 译文不溢出底块
        if need_h <= (bh - 2 * _PAD) and len(wrapped) <= 6:
            return f, wrapped, size
        size -= 2
    f = ImageFont.truetype(_pick_cjk_font(), _MIN_FONT)
    max_chars = max(1, int((bw - 2 * _PAD) / _MIN_FONT))
    wrapped = [text[i:i + max_chars] for i in range(0, len(text), max_chars)][:6]
    return f, wrapped, _MIN_FONT


def overlay_translate(png_path, translate_fn, dst="zh", out_path=None):
    """对一张截图做叠加式原位翻译。

    translate_fn(text:str)->str:注入的翻译函数(T-2b 接 LLM;T-2a 可 mock)。
    返 {"ok", "out", "blocks":[{box, src, dst, score}], "elapsed_s"} 或 {"ok":False,"error"}。
    """
    if not os.path.isfile(png_path):
        return {"ok": False, "error": "file_not_found", "path": png_path}
    ocr = extract._get_ocr()
    if ocr is None:
        return {"ok": False, "error": "ocr_unavailable",
                "hint": "pip install rapidocr_onnxruntime 后可用"}
    import time
    t0 = time.time()
    res, _ = ocr(png_path)
    lines = _line_boxes(res)
    # 置信门控:低质行不译不盖
    lines = [L for L in lines if L["score"] >= extract.OCR_MIN_SCORE]
    if not lines:
        return {"ok": False, "error": "no_text", "hint": "图中未识别到可信文字(或全被置信门控拦下)"}
    blocks = _merge_blocks(lines)

    from PIL import Image, ImageDraw
    img = Image.open(png_path).convert("RGBA")
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dv = ImageDraw.Draw(ov)
    out_blocks = []
    for b in blocks:
        src_text = " ".join(b["texts"]).strip()
        if not src_text:
            continue
        try:
            dst_text = (translate_fn(src_text) or "").strip()
        except Exception:  # noqa: BLE001
            dst_text = ""
        if not dst_text:
            continue
        x0, y0, x1, y1 = b["box"]
        x0 = max(0, x0 - _PAD); y0 = max(0, y0 - _PAD)
        x1 = min(img.width, x1 + _PAD); y1 = min(img.height, y1 + _PAD)
        bw, bh = x1 - x0, y1 - y0
        # 实底圆角块(干净盖原字) + 细描边维持气泡轮廓
        dv.rounded_rectangle([x0, y0, x1, y1], radius=10,
                             fill=_FILL, outline=_EDGE, width=2)
        # 译文(中文),字号自适应块高,自动换行
        font, wrapped, size = _fit_font(dv, dst_text, bw, bh, max_size=min(int(bh * 0.62), 28))
        ty = y0 + _PAD
        for wl in wrapped:
            dv.text((x0 + _PAD, ty), wl, font=font, fill=_INK)
            ty += size + 4
        out_blocks.append({"box": b["box"], "src": src_text, "dst": dst_text, "score": b["score"]})

    if not out_blocks:
        return {"ok": False, "error": "translate_empty", "hint": "识别到文字但译文为空(翻译失败?)"}
    img = Image.alpha_composite(img, ov).convert("RGB")
    if not out_path:
        base, _ = os.path.splitext(png_path)
        out_path = base + ".translated.png"
    img.save(out_path)
    return {"ok": True, "out": out_path, "blocks": out_blocks,
            "elapsed_s": round(time.time() - t0, 1)}


# ==================== mode="erase" 真抹字版(第三段,2026-08-22) ====================
# 不盖块,真把原字抹掉再写译文。Otsu 抠字 + dilate → 环带复杂度分流
# (白净区采样填充 percentile 92 / 复杂区 cv2.inpaint TELEA),译空块保留原文不留白。

_ERASE_PAD = 5          # 抹字框相对行框的外扩(px)
_ERASE_MIN = 0.4        # 抹字门控(宁多抹不漏)
_TRANS_MIN = 0.6        # 翻译门控(竖排短字 conf 天然偏低,放宽)
_ERASE_FILL_PCT = 92    # 白净区采样:框内最亮 percentile 当气泡底色
_COMPLEXITY_THR = 22    # 环带复杂度阈值:低于走采样填充,高于走 inpaint

# 2026-08-28 L4:跨平台字体路径(Linux/macOS 用 Noto/DejaVu,Win 用 Microsoft YaHei)
import sys as _sys
_IS_WIN = _sys.platform.startswith("win")
_IS_MAC = _sys.platform == "darwin"

_JA_FONT_CANDIDATES = (
    [r"C:\Windows\Fonts\msmincho.ttc",
     r"C:\Windows\Fonts\YuGothR.ttc",
     r"C:\Windows\Fonts\meiryo.ttc",
     r"C:\Windows\Fonts\msgothic.ttc"]
    if _IS_WIN else
    ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
     "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
     "/Library/Fonts/NotoSansCJK-Regular.ttc"]
)

_EN_FONT = (
    r"C:\Windows\Fonts\arial.ttf" if _IS_WIN else
    "/Library/Fonts/Arial.ttf" if _IS_MAC else
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
)


def _pick_cjk_font() -> str:
    """跨平台中文/日文候选字体。返第一个存在的绝对路径;全缺返空串。"""
    for p in [_FONT] + list(_JA_FONT_CANDIDATES):
        if p and os.path.isfile(p):
            return p
    return ""

# 日语竖排标点/符号 → 竖排字形(横排字形直接竖放不规范)
_VERT_MAP = {
    "…": "︙", "。": "。", "、": "、",
    "「": "「", "」": "」", "『": "『", "』": "』",
    "ー": "｜", "―": "｜", "—": "｜", "-": "｜",
    "!": "︕", "?": "︖",
    "︕": "︕", "︖": "︖",
}


def _pick_ja_font():
    for p in _JA_FONT_CANDIDATES:
        if os.path.isfile(p):
            return p
    return _JA_FONT_CANDIDATES[-1]


def _vert_text(s):
    return "".join(_VERT_MAP.get(c, c) for c in s)


def _pad_box(box, w, h, pad):
    x0, y0, x1, y1 = box
    return (max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad))


def _region_var(arr, box):
    x0, y0, x1, y1 = [int(v) for v in box]
    if x1 <= x0 or y1 <= y0:
        return 0.0
    reg = arr[y0:y1, x0:x1]
    return float(reg.std()) if reg.size else 0.0


def _cluster_bubbles(lines):
    """按近邻聚类成气泡块(竖排每行是一列;横排每行是一行)。"""
    lines = sorted(lines, key=lambda L: (L["box"][0], L["box"][1]))
    blocks = []
    for L in lines:
        x0, y0, x1, y1 = L["box"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        placed = False
        for b in blocks:
            bx0, by0, bx1, by1 = b["box"]
            bcx, bcy = (bx0 + bx1) / 2, (by0 + by1) / 2
            if abs(cx - bcx) < 60 and abs(cy - bcy) < 70:
                b["box"] = (min(bx0, x0), min(by0, y0), max(bx1, x1), max(by1, y1))
                b["lines"].append(L)
                b["score"] = min(b["score"], L["score"])
                placed = True
                break
        if not placed:
            blocks.append({"box": (x0, y0, x1, y1), "lines": [L], "score": L["score"]})
    return blocks


def _order_text(block, vertical):
    """块内原文排序:竖排右→左列内 y 升序;横排 y 升序行内 x 升序。"""
    if vertical:
        cols = sorted(block["lines"], key=lambda L: (-L["box"][0], L["box"][1]))
        return "".join(L["text"] for L in cols).strip()
    rows = sorted(block["lines"], key=lambda L: (L["box"][1], L["box"][0]))
    return " ".join(L["text"] for L in rows).strip()


def _erase_text(img_rgb, lines, w, h):
    """真抹字:逐行框抠字掩码 → 白净区采样填充 / 复杂区 cv2.inpaint。返 (抹净图, mask)。"""
    import numpy as np
    import cv2
    arr = np.array(img_rgb.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    boxes = [_pad_box(L["box"], w, h, _ERASE_PAD) for L in lines]
    mask = np.zeros_like(gray)
    for (x0, y0, x1, y1) in boxes:
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        roi = gray[y0:y1, x0:x1]
        if roi.size == 0:
            continue
        thr, _ = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        text_px = (roi < max(thr - 10, 0)).astype(np.uint8) * 255
        text_px = cv2.dilate(text_px, np.ones((3, 3), np.uint8))
        mask[y0:y1, x0:x1] = cv2.bitwise_or(mask[y0:y1, x0:x1], text_px)
    out = arr.copy()
    for (x0, y0, x1, y1) in boxes:
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        ring = _pad_box((x0, y0, x1, y1), w, h, 8)
        complexity = _region_var(gray, ring)
        sub = mask[y0:y1, x0:x1]
        if not np.any(sub):
            continue
        if complexity < _COMPLEXITY_THR:
            bg_px = gray[y0:y1, x0:x1]
            fill = int(np.percentile(bg_px, _ERASE_FILL_PCT)) if bg_px.size else 255
            roi = out[y0:y1, x0:x1]
            m = sub > 0
            for c in range(3):
                roi[:, :, c][m] = fill
        else:
            full = np.zeros_like(gray)
            full[y0:y1, x0:x1] = sub
            out = cv2.inpaint(out, full, 3, cv2.INPAINT_TELEA)
    from PIL import Image
    return Image.fromarray(out), mask


def _render_horizontal(dv, text, box, font_path, max_size=22, min_size=8):
    """横排整行:缩字号+自动换行塞进框,左对齐。英文按词/日文按字断行。"""
    from PIL import ImageFont
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0

    def wrap(f, t):
        if " " in t or all(ord(c) < 0x2E80 for c in t):  # 拉丁系按词
            lines, cur = [], ""
            for wd in t.split():
                tt = (cur + " " + wd).strip()
                if f.getlength(tt) <= bw - 2 * _PAD:
                    cur = tt
                else:
                    if cur:
                        lines.append(cur)
                    cur = wd
            if cur:
                lines.append(cur)
            return lines
        lines, cur = [], ""  # CJK 按字贪心
        for ch in t:
            if f.getlength(cur + ch) <= bw - 2 * _PAD:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines

    size = max_size
    while size > min_size:
        f = ImageFont.truetype(font_path, size)
        lines = wrap(f, text)
        if len(lines) * (size + 2) <= bh - 2 * _PAD:
            break
        size -= 1
    f = ImageFont.truetype(font_path, size)
    ty = y0 + _PAD
    for lt in wrap(f, text):
        dv.text((x0 + _PAD, ty), lt, font=f, fill=(20, 20, 20, 255))
        ty += size + 2


def _render_vertical(dv, text, box, font_path, img_w):
    """竖排多列:右起,缩字号+收列距塞进框。框钉死,装不下才横向微扩。"""
    from PIL import ImageFont
    ja_v = _vert_text(text)
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    CW_F, LH_F = 2, 1  # 列宽=字号+2,字距=字号+1

    def layout(size):
        cw, lh = size + CW_F, size + LH_F
        rows_per = max(1, int((bh - 2 * _PAD) / lh))
        cols_avail = max(1, int((bw - 2 * _PAD) / cw))
        return rows_per, cols_avail, cw, lh

    size = 23
    while size > 9:
        rows_per, cols_avail, cw, lh = layout(size)
        if len(ja_v) <= rows_per * cols_avail:
            break
        size -= 1
    rows_per, cols_avail, cw, lh = layout(size)
    if len(ja_v) > rows_per * cols_avail:  # 真塞不下横向微扩兜底
        need_cols = -(-len(ja_v) // rows_per)
        grow = (need_cols * cw + 2 * _PAD) - bw
        if grow > 0:
            x0 = max(0, x0 - grow // 2)
            x1 = min(img_w, x1 + (grow - grow // 2))
            bw = x1 - x0
            rows_per, cols_avail, cw, lh = layout(size)
    f = ImageFont.truetype(font_path, size)
    cx = x1 - _PAD - size
    i = 0
    while i < len(ja_v) and cx >= x0 + _PAD - 2:
        cy = y0 + _PAD
        for _ in range(rows_per):
            if i >= len(ja_v):
                break
            ch = ja_v[i]
            ch_w = f.getlength(ch) if hasattr(f, "getlength") else size
            col_center = cx - size / 2
            ch_x = col_center - ch_w / 2  # 全字符统一居中
            dv.text((ch_x, cy), ch, font=f, fill=(20, 20, 20, 255))
            cy += lh
            i += 1
        cx -= cw


def overlay_translate_erase(png_path, translate_fn, dst="zh", direction="auto",
                            src_lang="auto", out_path=None):
    """真抹字版原位翻译。抹掉原字再写译文(不盖块)。

    translate_fn(text:str, dst:str)->str:注入的翻译函数(后端照翻译插件:google_gtx 默认/配置端)。
    direction: "auto"(日/中竖排块→竖排,英文→横排) / "h"(横排) / "v"(竖排)。
    src_lang: 原文语言(用于判断竖排/横排;"auto" 按块形判断)。
    返 {"ok","out","blocks":[{box,src,dst,score,vertical}],"elapsed_s"} 或 {"ok":False,"error"}。
    """
    if not os.path.isfile(png_path):
        return {"ok": False, "error": "file_not_found", "path": png_path}
    ocr = extract._get_ocr()
    if ocr is None:
        return {"ok": False, "error": "ocr_unavailable",
                "hint": "pip install rapidocr_onnxruntime 后可用"}
    try:
        import numpy, cv2  # noqa: F401
    except Exception:
        return {"ok": False, "error": "cv2_unavailable",
                "hint": "抹字版需 pip install opencv-python numpy"}
    import time
    t0 = time.time()
    res, _ = ocr(png_path)
    all_lines = _line_boxes(res)
    erase_lines = [L for L in all_lines if L["score"] >= _ERASE_MIN]
    lines = [L for L in all_lines if L["score"] >= _TRANS_MIN]
    if not lines:
        return {"ok": False, "error": "no_text", "hint": "图中未识别到可信文字"}
    blocks = _cluster_bubbles(lines)

    from PIL import Image, ImageDraw
    img = Image.open(png_path).convert("RGBA")
    W_, H_ = img.size
    ja_font = _pick_ja_font()

    # 先翻完全部块,收集成功块的行框 —— 只擦这些,失败的块保留原文(不留白气泡)
    erased_src_lines = []
    out_blocks = []
    for b in blocks:
        # 判断这块是竖排还是横排:行宽 > 行高 → 横排(行是水平一行),否则竖排(行是竖直一列)
        vertical = _detect_vertical(b, src_lang, direction)
        src = _order_text(b, vertical)
        if not src or len(src) < 2:
            continue
        try:
            dst_text = (translate_fn(src, dst) or "").strip()
        except Exception:  # noqa: BLE001
            dst_text = ""
        if not dst_text:
            continue
        b["_dst"] = dst_text
        b["_vertical"] = vertical
        erased_src_lines.extend(b["lines"])

    if not any(b.get("_dst") for b in blocks):
        return {"ok": False, "error": "translate_empty", "hint": "识别到文字但译文为空(翻译失败?)"}

    # 抹字:只擦成功翻译块的行框
    erased, _mask = _erase_text(img, [L for L in erase_lines if _line_in_blocks(L, blocks)], W_, H_)
    ov_img = erased.convert("RGBA")
    dv = ImageDraw.Draw(ov_img)
    done = 0
    for b in blocks:
        dst_text = b.get("_dst")
        if not dst_text:
            continue
        vertical = b["_vertical"]
        x0, y0, x1, y1 = _pad_box(b["box"], W_, H_, _PAD)
        if vertical:
            _render_vertical(dv, dst_text, (x0, y0, x1, y1), ja_font, W_)
        else:
            # 横排:中文译文用中文字体,日文译文用日文字体,英文用 arial
            font_path = _FONT if dst in ("zh", "zh-cn") else (ja_font if dst == "ja" else _EN_FONT)
            _render_horizontal(dv, dst_text, (x0, y0, x1, y1), font_path)
        out_blocks.append({"box": b["box"], "src": _order_text(b, vertical),
                           "dst": dst_text, "score": b["score"], "vertical": vertical})
        done += 1
    if not out_path:
        base, _ = os.path.splitext(png_path)
        out_path = base + ".translated.png"
    ov_img.convert("RGB").save(out_path)
    return {"ok": True, "out": out_path, "blocks": out_blocks,
            "elapsed_s": round(time.time() - t0, 1)}


_FONT = (
    r"C:\Windows\Fonts\msyh.ttc" if _IS_WIN else
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc" if os.path.isfile("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc") else
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
)  # 中文译文横排字体(Win=YaHei, Linux=NotoSansCJK)


def _detect_vertical(block, src_lang, direction):
    """判断块是竖排还是横排。direction 显式指定优先;否则按 src_lang/行形自动判。"""
    if direction == "v":
        return True
    if direction == "h":
        return False
    if src_lang == "ja":  # 日文漫画默认竖排
        return True
    # auto:按行形——竖排里每行是「竖直一列」(行高远大于行宽);
    # 横排里每行是「水平一行」(行宽远大于行高)。统计块内多数行的朝向。
    v_lines = h_lines = 0
    for L in block["lines"]:
        x0, y0, x1, y1 = L["box"]
        lw, lh = x1 - x0, y1 - y0
        if lh > lw * 1.3:  # 行高远大于行宽 → 竖列
            v_lines += 1
        elif lw > lh * 1.3:  # 行宽远大于行高 → 横行
            h_lines += 1
    if v_lines or h_lines:
        return v_lines > h_lines
    # 都行形不明 → 退到块形:明显高于宽判竖排
    x0, y0, x1, y1 = block["box"]
    return (y1 - y0) > (x1 - x0) * 1.5


def _line_in_blocks(line, blocks):
    """判断某行框是否落在某个成功翻译块内(用于抹字范围)。"""
    lcx = (line["box"][0] + line["box"][2]) / 2
    lcy = (line["box"][1] + line["box"][3]) / 2
    for b in blocks:
        if not b.get("_dst"):
            continue
        bx0, by0, bx1, by1 = b["box"]
        if bx0 - 20 <= lcx <= bx1 + 20 and by0 - 20 <= lcy <= by1 + 20:
            return True
    return False
