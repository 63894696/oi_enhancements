# -*- coding: utf-8 -*-
"""OCR 质量定量基线:造一组带真值的测试图 → 跑 RapidOCR → 算 CER + 关键词召回 + 耗时。

用途:给「默认开不开 OCR / 用哪档模型(mobile vs server)」提供数据准绳。
跑法:  python ocr_eval.py            # 默认内置模型(PP-OCRv4 mobile 级)
       python ocr_eval.py --rec models/xxx_rec.onnx --det models/xxx_det.onnx  # 换 server 模型对比

指标:
  CER  = 字符错误率(编辑距离/真值字符数),越小越好;中文主要看它(字错→搜索漏)。
  召回 = 每个图预设的「用户会搜的关键词」能否在 OCR 结果里命中(检索可用性)。
  置信 = RapidOCR 每行 score 均值;< 0.8 的行按「低可信」记(应门控不入库)。
"""
import argparse, os, sys, tempfile, time

# ---------- CER(Levenshtein 编辑距离 / 真值长度) ----------
def _edit_dist(a: str, b: str) -> int:
    la, lb = len(a), len(b)
    if la == 0: return lb
    if lb == 0: return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]

def cer(gt: str, hyp: str) -> float:
    g = "".join(gt.split())  # 去空白(空格不算字符错误)
    h = "".join(hyp.split())
    if not g:
        return 0.0 if not h else 1.0
    return _edit_dist(g, h) / len(g)


# ---------- 造带真值的测试图 ----------
def _font(sz):
    from PIL import ImageFont
    for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
               r"C:\Windows\Fonts\simsun.ttc"):
        if os.path.exists(fp):
            return ImageFont.truetype(fp, sz)
    return ImageFont.load_default()

# (名字, 真值文本, 字号, 旋转角, 加噪) — 覆盖清晰/小字/倾斜/噪声四档
CASES = [
    ("clear_doc",   "季度报告:营收增长稳健,市场份额扩大。", 30, 0, False),
    ("clear_en",    "Revenue Growth Report 2026", 30, 0, False),
    ("small_text",  "本机内容搜索逐目录授权", 16, 0, False),
    ("rotated",     "探囊取物毫秒即得", 26, 4, False),
    ("noisy",       "本地优先不出本机", 26, 0, True),
    ("mixed",       "本地优先 local-first 隐私", 24, 0, False),
]
# 每个图预设的检索关键词(验「字错了还能不能搜到」)
KEYWORDS = {
    "clear_doc": ["季度报告", "市场份额"],
    "clear_en":  ["Revenue", "2026"],
    "small_text": ["内容搜索", "授权"],
    "rotated":   ["探囊", "毫秒"],
    "noisy":     ["本地优先", "本机"],
    "mixed":     ["本地优先", "local-first"],
}


def render(text, size, angle, noisy, out_path):
    from PIL import Image, ImageDraw
    f = _font(size)
    pad = 20
    tmp = Image.new("RGB", (10, 10))
    d = ImageDraw.Draw(tmp)
    w, h = d.textbbox((0, 0), text, font=f)[2:]  # 量文本宽高
    img = Image.new("RGB", (w + pad * 2, h + pad * 2 + 10), "white")
    d = ImageDraw.Draw(img)
    d.text((pad, pad), text, font=f, fill="black")
    if angle:
        img = img.rotate(angle, expand=True, fillcolor="white")
    if noisy:
        import random
        px = img.load()
        rnd = random.Random(42)
        for _ in range(int(img.width * img.height * 0.02)):  # 2% 噪点
            x, y = rnd.randrange(img.width), rnd.randrange(img.height)
            px[x, y] = (rnd.randrange(255),) * 3
    img.save(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--det", default=None, help="自定义 det 模型(server 档对比)")
    ap.add_argument("--rec", default=None, help="自定义 rec 模型(server 档对比)")
    ap.add_argument("--cls", default=None, help="自定义 cls 模型")
    args = ap.parse_args()

    from rapidocr_onnxruntime import RapidOCR
    kw = {}
    if args.det: kw["det_model_path"] = args.det
    if args.rec: kw["rec_model_path"] = args.rec
    if args.cls: kw["cls_model_path"] = args.cls
    t0 = time.time()
    ocr = RapidOCR(**kw)
    load_s = time.time() - t0

    tmp = tempfile.mkdtemp(prefix="ocr_eval_")
    model_tag = "server" if (args.det or args.rec) else "builtin(PP-OCRv4)"
    print(f"模型: {model_tag}  首载 {load_s:.1f}s\n")
    print(f"{'用例':<12} {'CER':>7} {'召回':>6} {'置信':>6} {'耗时':>6}  关键词命中")
    print("-" * 72)

    total_cer, total_recall, n = 0.0, 0.0, 0
    try:
        for name, gt, size, angle, noisy in CASES:
            img = os.path.join(tmp, name + ".png")
            render(gt, size, angle, noisy, img)
            t1 = time.time()
            res, _ = ocr(img)
            dt = time.time() - t1
            hyp = "".join(r[1] for r in res) if res else ""
            conf = (sum(r[2] for r in res) / len(res)) if res else 0.0
            c = cer(gt, hyp)
            kws = KEYWORDS[name]
            hits = [k for k in kws if k.replace(" ", "") in hyp.replace(" ", "")]
            rec = len(hits) / len(kws) if kws else 1.0
            total_cer += c; total_recall += rec; n += 1
            flag = "" if c < 0.05 else ("  ⚠低质" if c >= 0.2 else "  ·")
            print(f"{name:<12} {c*100:>5.1f}% {rec*100:>5.0f}% {conf:>5.2f} {dt*1000:>5.0f}ms"
                  f"  {'/'.join(hits) or '无'}{flag}")
            if c >= 0.05:
                print(f"    真值: {gt}")
                print(f"    识别: {hyp}")
        print("-" * 72)
        print(f"{'平均':<12} {total_cer/n*100:>5.1f}% {total_recall/n*100:>5.0f}%")
        print("\n判读: 平均 CER<5% 且 召回>90% → 该档可用于默认开 OCR;")
        print("      清晰档(clear_*)应 CER<1%;small/noisy/rotated 是质量分界。")
        return 0
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
