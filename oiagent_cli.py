"""oiagent-cli — minimal conversational agent shell for SPIKE #2 benchmark.

Bridges the gap: oiagent has MCP tools + GUI shell but no headless
"task -> autonomous execute -> deliver" CLI. This is the minimal loop:

  prompt -> LLM (litellm / OpenRouter) -> tool_call -> dispatch -> feed back -> repeat

Tools (code-editing, not the ops MCP set): read_file / write_file / run_shell / list_files.
Self-contained: only needs litellm on the instance. Keys via env (OPENROUTER_API_KEY) only.

CLI mirrors aider so spike2-run-with-keys.py can drive it:
  python oiagent_cli.py --message-file PROMPT --model MODEL [--max-turns N] [--workdir DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

# windowed(PyInstaller --noconsole)下 sys.stdout/stderr 为 None,reconfigure 会 AttributeError。
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

MAX_TURNS_DEFAULT = 30
SHELL_TIMEOUT = 60

# 工具结果入库粒度:单条 tool 输出入库前截断到此字符数(留头尾+省略标记),
# 防爆库;完整大输出时效短,跨轮只需「做过什么+关键结论」。
TOOL_STORE_MAX = 4000


def truncate_tool_output(text: str, limit: int = TOOL_STORE_MAX) -> str:
    """截断长工具输出供入库:留头 (limit-~500) + 省略标记 + 尾 500。"""
    s = str(text or "")
    if len(s) <= limit:
        return s
    head = s[: limit - 520]
    tail = s[-500:]
    return f"{head}\n…[截断,原 {len(s)} 字符]…\n{tail}"


# ---------------- tools ----------------
def _t_read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:  # noqa: BLE001
        return f"[read_file error] {e}"


# 写入追踪(2026-08-24):成品对话链的「落盘校验」detect 层。开发链靠任务显式声明
# 「改动文件:」清单校验,成品对话链没有这个概念 → 改为追踪 write_file 实际写了什么。
# workdir -> [(path, ts), ...];web 层在答复后 pop 取用做落盘校验 + 改后检测(下轮注入)。
# 追踪失败静默,绝不影响 write_file 主功能。
_WRITE_TRACKER: dict[str, list] = {}


def pop_written_files(workdir: str) -> list:
    """读回并清空该 workdir 本轮 write_file 写过的文件清单 [(path, ts), ...]。"""
    try:
        return _WRITE_TRACKER.pop(workdir, [])
    except Exception:  # noqa: BLE001
        return []


def _t_write_file(path: str, content: str, workdir: str = "") -> str:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        # 落盘成功后追踪(失败静默,不影响主功能返回值)
        try:
            _WRITE_TRACKER.setdefault(workdir or os.path.dirname(os.path.abspath(path)),
                                      []).append((os.path.abspath(path), time.time()))
        except Exception:  # noqa: BLE001
            pass
        return f"[write_file ok] {path} ({len(content)} bytes)"
    except Exception as e:  # noqa: BLE001
        return f"[write_file error] {e}"


def _t_run_shell(command: str, workdir: str) -> str:
    try:
        r = subprocess.run(command, cwd=workdir, capture_output=True,
                           timeout=SHELL_TIMEOUT, shell=True)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        return f"[rc={r.returncode}]\nstdout:\n{out}\nstderr:\n{err}"
    except subprocess.TimeoutExpired:
        return "[run_shell timeout]"
    except Exception as e:  # noqa: BLE001
        return f"[run_shell error] {e}"


def _t_list_files(path: str, workdir: str) -> str:
    try:
        base = os.path.join(workdir, path) if not os.path.isabs(path) else path
        items = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache", ".git")]
            for fn in files:
                items.append(os.path.relpath(os.path.join(root, fn), workdir))
            if len(items) > 200:
                break
        return "\n".join(sorted(items)) or "[empty]"
    except Exception as e:  # noqa: BLE001
        return f"[list_files error] {e}"


# ---------- search_files:优先调 Everything 的 es.exe(NTFS 全盘极速属性搜) ----------
# es.exe 依赖本机 Everything.exe 服务在跑;不在 PATH 时尝试常见落地路径。
# 找不到 es.exe 或服务未运行 → 回退到 os.walk 按文件名子串匹配(慢但稳)。
_ES_KNOWN_PATHS = (
    r"D:\down\es-temp\ES-extracted\es.exe",
    r"D:\down\Everything系统搜索工具\Everything-1.4.1.969.x64\es.exe",
    r"C:\Program Files\Everything\es.exe",
    r"C:\Program Files (x86)\Everything\es.exe",
)


def _find_es_exe() -> str:
    """解析 es.exe 路径:PATH → 已知落地路径。找不到返回 ""。"""
    try:
        import shutil
        p = shutil.which("es.exe") or shutil.which("es")
        if p:
            return p
    except Exception:  # noqa: BLE001
        pass
    for cand in _ES_KNOWN_PATHS:
        if os.path.isfile(cand):
            return cand
    return ""


def _t_search_files(query: str, workdir: str, limit: int = 50) -> str:
    """按文件名/路径关键词搜索。es.exe 可用则全盘秒级搜,否则 workdir 下走文件系统。"""
    q = (query or "").strip()
    if not q:
        return "[search_files error] empty query"
    limit = max(1, min(int(limit or 50), 200))
    es = _find_es_exe()
    if es:
        try:
            r = subprocess.run([es, "-n", str(limit), q], capture_output=True, timeout=20)
            out = (r.stdout or b"").decode("utf-8", "replace").strip()
            if r.returncode == 0 and out:
                return out
            # es.exe 报错(常见:Everything 服务未启动) → 落到 walk
        except Exception:  # noqa: BLE001
            pass
    # 回退:workdir 下按文件名子串匹配
    try:
        hits = []
        ql = q.lower()
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache", ".git", "node_modules")]
            for fn in files:
                if ql in fn.lower():
                    hits.append(os.path.join(root, fn))
                    if len(hits) >= limit:
                        return "\n".join(hits)
        return "\n".join(hits) or "[no match]"
    except Exception as e:  # noqa: BLE001
        return f"[search_files error] {e}"


# ---------- 本机自建文件索引(prisir_findex,不依赖 Everything) ----------
# 目标机可能没装 Everything → search_files 的 es.exe 快路径不可用。
# prisir_findex 是 Rust cdylib 自建索引(只存元数据),用户显式开启后全盘子串秒搜。
# 这里走 Python ctypes 壳;未开启/未编译时优雅降级返回提示,不报错。
_FINDEX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prisir_findex")


def _findex():
    """惰性加载 Findex 单例;不可用返回 None。"""
    try:
        if _FINDEX_DIR not in sys.path:
            sys.path.insert(0, _FINDEX_DIR)
        from shell_findex import Findex  # noqa: PLC0415
        return Findex.shared()
    except Exception:  # noqa: BLE001
        return None


def _t_local_file_search(query: str, limit: int = 30) -> str:
    """本机自建索引搜文件(不依赖 Everything)。需用户已开启本机文件搜索。"""
    q = (query or "").strip()
    if not q:
        return "[local_file_search error] empty query"
    fx = _findex()
    if fx is None:
        return ("[local_file_search 不可用] 索引引擎未就绪(prisir_findex.dll 未编译或加载失败)。"
                "可改用 search_files(全盘 walk 较慢)或 list_files。")
    st = fx.status()
    if not st.get("enabled"):
        return ("[local_file_search 未开启] 本机文件搜索索引尚未建立。"
                "请用户在菜单开启「本机文件搜索」建索引后再用;或先用 search_files 兜底。")
    res = fx.search(q, limit)
    hits = res.get("hits", [])
    if not hits:
        return "[no match]"
    import datetime
    lines = []
    tot = res.get("total", 0)
    if tot < 0 or tot > len(hits):
        cnt = f"{len(hits)}+" if tot < 0 else str(tot)
        lines.append(f"[共约 {cnt} 条匹配,以下为最相关的前 {len(hits)} 条;可收紧关键词]")
    for h in hits:
        mt = datetime.datetime.fromtimestamp(h.get("mtime", 0)).strftime("%Y-%m-%d %H:%M")
        tag = "[目录] " if h.get("is_dir") else ""
        lines.append(f"{tag}{h['path']}  ({h.get('size',0)}B, {mt})")
    return "\n".join(lines)


_FCONTENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prisir_fcontent")


def _fcontent():
    """惰性加载 Fcontent 单例;模块不可用返回 None。"""
    try:
        parent = os.path.dirname(_FCONTENT_DIR)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        from prisir_fcontent import Fcontent  # noqa: PLC0415
        return Fcontent.shared()
    except Exception:  # noqa: BLE001
        return None


def _t_local_content_search(query: str, limit: int = 30) -> str:
    """按内容搜文件(docx/pdf/pptx/txt/md/代码等),带匹配片段。需用户已开启内容搜索。"""
    q = (query or "").strip()
    if not q:
        return "[local_content_search error] empty query"
    fc = _fcontent()
    if fc is None:
        return ("[local_content_search 不可用] 内容搜索模块加载失败(prisir_fcontent)。"
                "可改用 search_files 按文件名兜底。")
    st = fc.status()
    if not st.get("enabled"):
        return ("[local_content_search 未开启] 内容索引尚未建立。"
                "请用户在「本机内容搜索」开启并授权目录后再用;或先用 local_file_search 按文件名兜底。")
    res = fc.search(q, limit)
    hits = res.get("hits", [])
    if not hits:
        return "[no match]"
    import datetime
    lines = []
    tot = res.get("total", 0)
    if tot > len(hits):
        lines.append(f"[共约 {tot} 条匹配,以下为最相关的前 {len(hits)} 条;可收紧关键词]")
    for h in hits:
        mt = datetime.datetime.fromtimestamp(h.get("mtime", 0)).strftime("%Y-%m-%d %H:%M")
        snip = (h.get("snippet") or "").replace("**", "")
        lines.append(f"{h['path']}  ({mt})")
        if snip:
            lines.append(f"    …{snip}…")
    return "\n".join(lines)


# ---------- AnyTXT Searcher 运行时探测(可选,零打包) ----------
# AnyTXT 是本机已装就免费用的全文搜索引擎,强项是 docx/pdf/ppt 全文 + 图片 OCR,
# 正好补 fcontent 不打包 docx/pdf/pptx 库的短板。运行时探测 127.0.0.1:9920 JSON-RPC:
# 在就用、索引空就诚实给无结果、服务不在就提示用户。绝不影响主流程。
# 协议(官方论坛核实):POST http://127.0.0.1:9920/ 头 Accept+Content-Type: application/json,
# method 是完整服务名 ATRpcServer.Searcher.V1.GetResult,params 套 input 对象。
_ANYTXT_URL = "http://127.0.0.1:9920/"


def _anytxt_rpc(method: str, inp: dict, timeout: float = 12.0):
    """调 AnyTXT JSON-RPC。返 (ok, output_dict, err_reason)。全程不抛。"""
    try:
        import urllib.request  # noqa: PLC0415
        body = json.dumps({
            "id": 1, "jsonrpc": "2.0", "method": method,
            "params": {"input": inp},
        }).encode("utf-8")
        req = urllib.request.Request(
            _ANYTXT_URL, data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"})
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
        d = json.loads(raw)
        if d.get("error"):
            return False, {}, f"rpc error {d['error'].get('code')}: {d['error'].get('message')}"
        out = (d.get("result") or {}).get("data", {}).get("output", {})
        return True, out, ""
    except Exception as e:  # noqa: BLE001
        return False, {}, str(e)


def _t_anytxt_search(query: str, limit: int = 20) -> str:
    """经 AnyTXT 按正文全文搜(含 docx/pdf/ppt/图片OCR)。补 fcontent 的格式短板。"""
    q = (query or "").strip()
    if not q:
        return "[anytxt_search error] empty query"
    ok, out, err = _anytxt_rpc("ATRpcServer.Searcher.V1.GetResult", {
        "pattern": q, "filterDir": "*", "filterExt": "*",
        "lastModifyBegin": 0, "lastModifyEnd": 2147483647,
        "limit": str(max(1, min(int(limit or 20), 100))), "offset": 0, "order": 0,
    })
    if not ok:
        return (f"[anytxt_search 不可用] 本机 AnyTXT 服务未响应({err})。"
                "AnyTXT Searcher 需已安装并运行;没装可忽略,改用 local_content_search(探囊)。")
    files = out.get("files", [])
    count = out.get("count", 0)
    if not files:
        return ("[no match] AnyTXT 无匹配(可能该目录未入 AnyTXT 索引,"
                "或关键词无命中)。可改用 local_content_search / local_file_search。")
    lines = [f"[AnyTXT 共 {count} 条匹配,前 {len(files)} 条]"]
    import datetime
    for f in files:
        path = f.get("file", "")
        lm = f.get("lastModify", 0)
        try:
            mt = datetime.datetime.fromtimestamp(int(lm)).strftime("%Y-%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            mt = ""
        sz = f.get("size", 0)
        lines.append(f"{path}  ({mt}, {sz}B)")
    return "\n".join(lines)


# ---------- Web 搜索(2026-08-25 用户拍板补;免 key 默认,用户可自配 key 升级) ----------
# 原则(用户在系统提示词里拍板):不主动预设任何海内外厂商端点;KEY 属用户自管。
# 双引擎自动按网络环境选(2026-08-25 无 VPN 实测):
#   DDG api.duckduckgo.com 国内无 VPN 000 连不上;Bing 302 自动跳 cn.bing.com 200。
#   海外/有 VPN 时 DDG Instant Answer 百科型更准;国内直连时 Bing RSS 全文型更实用。
#   每次搜索前 4s 探测 DDG 可达性,通则 DDG 否则 Bing;探测结果 60s 缓存不重复探。
# 若用户要更强/更全搜索,引导其自配 key(Bing/Google/SerpAPI 等)后在 env 里升级。
_BING_RSS = "https://cn.bing.com/search"
_DDG_API = "https://api.duckduckgo.com/"
_ENGINE_CACHE: dict = {"ts": 0.0, "use_ddg": False}
_ENGINE_CACHE_TTL = 60.0


def _pick_search_engine() -> str:
    """探测 DDG 可达性,选 web 搜索引擎。返 'ddg' 或 'bing'。60s 缓存。"""
    now = time.time()
    if now - _ENGINE_CACHE["ts"] < _ENGINE_CACHE_TTL:
        return "ddg" if _ENGINE_CACHE["use_ddg"] else "bing"
    use_ddg = False
    try:
        import urllib.request as _ur  # noqa: PLC0415
        req = _ur.Request(_DDG_API + "?q=ping&format=json",
                          headers={"User-Agent": "Mozilla/5.0"}, method="HEAD")
        with _ur.urlopen(req, timeout=4):
            use_ddg = True
    except Exception:  # noqa: BLE001
        use_ddg = False
    _ENGINE_CACHE["ts"] = now
    _ENGINE_CACHE["use_ddg"] = use_ddg
    return "ddg" if use_ddg else "bing"


def _ddg_search(q: str, limit: int) -> str:
    """DuckDuckGo Instant Answer(海外/有 VPN 用,百科型)。返格式化结果或 ''。"""
    import urllib.request as _ur  # noqa: PLC0415
    from urllib.parse import urlencode  # noqa: PLC0415
    qs = urlencode({"q": q, "format": "json", "no_html": 1, "skip_disambig": 1,
                    "no_redirect": 1, "t": "prisir-agent"})
    req = _ur.Request(_DDG_API + "?" + qs,
                      headers={"User-Agent": "Mozilla/5.0 (PrisirAgent)"})
    with _ur.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    results = []
    abstract = (data.get("AbstractText") or "").strip()
    heading = (data.get("Heading") or "").strip()
    src = (data.get("AbstractSource") or "").strip()
    if abstract:
        results.append(f"【{heading or q}】{abstract}" + (f"(来源:{src})" if src else ""))
    for rt in (data.get("RelatedTopics") or [])[:max(1, limit)]:
        if isinstance(rt, dict):
            t = (rt.get("Text") or "").strip()
            u = (rt.get("FirstURL") or "").strip()
            if t:
                results.append(t + (f"  → {u}" if u else ""))
    return "\n".join(results[:max(1, limit)])


def _bing_search(q: str, limit: int) -> str:
    """Bing RSS(国内直连用,全文型)。返格式化结果或 ''。"""
    import urllib.request as _ur  # noqa: PLC0415
    from urllib.parse import urlencode  # noqa: PLC0415
    import xml.etree.ElementTree as ET  # noqa: PLC0415
    qs = urlencode({"q": q, "format": "rss"})
    req = _ur.Request(_BING_RSS + "?" + qs,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with _ur.urlopen(req, timeout=15) as r:
        xml_data = r.read().decode("utf-8")
    results = []
    root = ET.fromstring(xml_data)
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        if title:
            line = title
            if desc:
                line += f" — {desc[:120]}"
            if link:
                line += f"  → {link}"
            results.append(line)
    return "\n".join(results[:max(1, limit)])


def _t_web_search(query: str, limit: int = 8) -> str:
    """联网搜索(自动选 DDG/Bing,免 key、按网络环境路由)。失败返诚实提示。"""
    q = (query or "").strip()
    if not q:
        return "[web_search error] empty query"
    limit = max(1, int(limit or 8))
    engine = _pick_search_engine()
    try:
        out = _ddg_search(q, limit) if engine == "ddg" else _bing_search(q, limit)
    except Exception as e:  # noqa: BLE001
        return (f"[web_search 不可用] {engine.upper()} 连接失败: {e}。"
                "联网搜索需网络可达对应引擎;若持续失败请检查网络。")
    if not out:
        return (f"[web_search 无结果] {engine.upper()} 对「{q}」没有返回有效结果。"
                "建议:换更具体的关键词重试;或自配全文搜索 API key(Bing/Google/SerpAPI 等)升级。")
    return out


# ---------- 翻译(复用翻译插件后端:google_gtx 免 key) ----------
# 后端照 custom-hover-translate/extension/src/engines.js 的 callGoogleGtx,
# 与 oiagent_web._google_gtx_translate 同源(agent 工具链独立一份,不跨进程 import web 层)。
# 免 key、直连 translate.googleapis.com、只出本机到 Google 翻译(用户已知插件默认行为)。
def _google_gtx_translate(text: str, src_lang: str = "auto", dst: str = "zh") -> str:
    """google_gtx 免 key 翻译。失败返 ""。"""
    try:
        import urllib.request as _ur  # noqa: PLC0415
        from urllib.parse import urlencode  # noqa: PLC0415
        qs = urlencode({"client": "gtx", "sl": src_lang or "auto",
                        "tl": dst or "zh", "dt": "t", "q": text})
        url = "https://translate.googleapis.com/translate_a/single?" + qs
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        out = ""
        if isinstance(data, list) and data and isinstance(data[0], list):
            for seg in data[0]:
                if isinstance(seg, list) and isinstance(seg[0], str):
                    out += seg[0]
        return out.strip()
    except Exception:  # noqa: BLE001
        return ""


def _gtx_lang(code: str) -> str:
    """把 agent 传的宽松语言名/码收敛成 gtx 接受的码。"""
    c = (code or "").strip().lower()
    m = {"中文": "zh-CN", "汉语": "zh-CN", "zh": "zh-CN", "chinese": "zh-CN",
         "英文": "en", "英语": "en", "english": "en",
         "日文": "ja", "日语": "ja", "japanese": "ja",
         "韩文": "ko", "韩语": "ko", "korean": "ko",
         "法文": "fr", "德文": "de", "西班牙文": "es", "俄文": "ru",
         "繁体": "zh-TW", "繁体中文": "zh-TW"}
    return m.get(c, c or "auto")


def _t_translate_document(path: str, target_lang: str = "zh", workdir: str = "") -> str:
    """文档翻译:读 docx/pdf/pptx/txt/md → 分段 google_gtx 翻译 → 写 <原名>.<lang>.<ext> 译文文件。
    纯文本往返(不保留 docx 格式),docx/pdf/pptx 需本机装了对应解析库(fcontent extract 复用)。"""
    p = (path or "").strip()
    if not p:
        return "[translate_document error] empty path"
    if not os.path.exists(p):
        return f"[translate_document error] 文件不存在: {p}"
    # 读正文(复用 fcontent extract,零额外打包)
    try:
        if os.path.dirname(_FCONTENT_DIR) not in sys.path:
            sys.path.insert(0, os.path.dirname(_FCONTENT_DIR))
        from prisir_fcontent import extract as _fx  # noqa: PLC0415
        text = _fx.extract_text(p, ocr=False)
    except Exception as e:  # noqa: BLE001
        return f"[translate_document 不可用] 内容抽取模块加载失败: {e}"
    if not text:
        return (f"[translate_document 不支持] 无法从 {os.path.basename(p)} 抽出文本"
                "(不支持的格式/加密/缺解析库)。txt/md 直接支持;docx/pdf/pptx 需本机有解析库。")
    dst = _gtx_lang(target_lang)
    # 分段翻译(gtx 单段不宜过长,按 ~1500 字切块,段落边界优先)
    paras, chunks, cur = text.split("\n"), [], ""
    for para in paras:
        if len(cur) + len(para) + 1 > 1500 and cur:
            chunks.append(cur)
            cur = para
        else:
            cur = (cur + "\n" + para) if cur else para
    if cur:
        chunks.append(cur)
    out_parts, failed = [], 0
    for ck in chunks:
        if not ck.strip():
            continue
        t = _google_gtx_translate(ck, "auto", dst)
        if t:
            out_parts.append(t)
        else:
            failed += 1
            out_parts.append(ck)  # 该段翻译失败保留原文,不丢内容
    base, ext = os.path.splitext(p)
    lang_tag = dst.replace("-", "").lower()
    out_path = base + "." + lang_tag + ext
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(out_parts))
        try:
            _WRITE_TRACKER.setdefault(workdir or os.path.dirname(os.path.abspath(out_path)),
                                     []).append((os.path.abspath(out_path), time.time()))
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        return f"[translate_document error] 写译文失败: {e}"
    note = f"(其中 {failed} 段翻译失败保留原文)" if failed else ""
    if failed == len(chunks):
        # 全部段失败 = gtx 不可达(国内无 VPN 常态)。诚实给替代方案,不留半成品。
        try:
            os.remove(out_path)
        except Exception:  # noqa: BLE001
            pass
        return ("[translate_document 不可用] google_gtx 后端不可达(国内无 VPN 时 Google 翻译连不上)。"
                "替代:① 联网后重试;② 访问 https://pdftranslator.org/zh 在线翻译;"
                "③ 用对话直接翻译(我可读文件逐段译)。")
    return (f"[translate_document ok] 已译 {len(p)} 字 → {out_path} {note}\n"
            f"译文是纯文本(不保留原排版)。后端 google_gtx 免 key,内容经 Google 翻译。")


def _t_translate_image(path: str, target_lang: str = "zh", workdir: str = "") -> str:
    """图片翻译(保留):OCR 图中文字 → 翻译 → 真抹字叠加译文,产 *.translated.png(原图不动)。
    需本机装 rapidocr_onnxruntime(否则诚实提示)。复用 fcontent overlay_translate。"""
    p = (path or "").strip()
    if not p:
        return "[translate_image error] empty path"
    if not os.path.exists(p):
        return f"[translate_image error] 文件不存在: {p}"
    try:
        if os.path.dirname(_FCONTENT_DIR) not in sys.path:
            sys.path.insert(0, os.path.dirname(_FCONTENT_DIR))
        from prisir_fcontent import overlay_translate as _ovt  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return f"[translate_image 不可用] 模块加载失败: {e}"
    dst = _gtx_lang(target_lang)
    # 注入翻译函数:google_gtx(免 key)
    def _tr(s: str) -> str:
        return _google_gtx_translate(s, "auto", dst)
    try:
        res = _ovt.overlay_translate(p, _tr, dst=dst)
    except Exception as e:  # noqa: BLE001
        return (f"[translate_image 不可用] OCR/叠加失败: {e}。"
                "图片翻译需本机装 rapidocr_onnxruntime(默认不带)。")
    if not res.get("ok"):
        return f"[translate_image 失败] {res.get('error','?')}: {res.get('hint','')}"
    out = res.get("out_path") or res.get("out") or ""
    if not out:
        # gtx 全失败(OCR 出了但翻译空)= 国内无 VPN。诚实给替代。
        return ("[translate_image 不可用] OCR 成功但 google_gtx 翻译不可达(国内无 VPN)。"
                "替代:① 联网后重试;② https://pdftranslator.org/zh 在线翻译;"
                "③ 我直接读图翻译告诉你内容。")
    return (f"[translate_image ok] 已抹字叠加译文 → {out}\n"
            f"识别 {res.get('lines', '?')} 行,译文叠加在原图上(原图未动)。")


def _t_read_file_head(path: str, max_chars: int = 4000) -> str:
    """读文件开头若干字符,供「搜到后取上下文」。截断防爆上下文。"""
    p = (path or "").strip()
    if not p:
        return "[read_file_head error] empty path"
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            data = f.read(max(200, min(int(max_chars or 4000), 20000)))
        return data
    except Exception as e:  # noqa: BLE001
        return f"[read_file_head error] {e}"


def _t_file_reputation(path: str) -> str:
    """协助查毒(只查不删):本地算 SHA256 → 云端只传哈希查信誉(MalwareBazaar/VirusTotal)。
    只给判定+建议,绝不上传文件本体、绝不替用户删除。key 从 keyring 取,不回显。"""
    p = (path or "").strip()
    if not p:
        return "[file_reputation error] empty path"
    try:
        if _FINDEX_DIR not in sys.path:
            sys.path.insert(0, _FINDEX_DIR)
        import reputation  # noqa: PLC0415
        from fastlane.providers.llm_prisir import PrisirKeyStore  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return f"[file_reputation 不可用] 查毒模块加载失败: {e}"
    h = reputation.hash_file(p)
    if not h.get("ok"):
        return f"[file_reputation error] {h.get('error','hash failed')}"
    ks = PrisirKeyStore()
    mbk = (ks.get_key("malwarebazaar") or {}).get("api_key", "")
    vtk = (ks.get_key("virustotal") or {}).get("api_key", "")
    lines = [f"文件: {p}", f"SHA256: {h['sha256']}", f"大小: {h.get('size',0)}B"]
    # MalwareBazaar(配 key 才查)
    if mbk:
        mb = reputation.query_malwarebazaar(sha256=h["sha256"], api_key=mbk)
        if mb.get("found"):
            lines.append(f"MalwareBazaar: ⛔ 已知恶意({mb.get('signature','未知家族')}, 首见 {mb.get('first_seen','?')})")
        elif mb.get("ok"):
            lines.append("MalwareBazaar: 未收录(查不到≠安全)")
        else:
            lines.append(f"MalwareBazaar: 查询失败 {mb.get('error','')}")
    else:
        lines.append("MalwareBazaar: 未配 key")
    # VirusTotal(配 key 才查)
    if vtk:
        vt = reputation.query_virustotal_hash(h["sha256"], vtk)
        if vt.get("found"):
            lines.append(f"VirusTotal: {vt.get('malicious',0)}/{vt.get('total',0)} 引擎报毒"
                         f"({vt.get('meaningful_name') or '见文件名'})")
        elif vt.get("ok"):
            lines.append("VirusTotal: 无此文件记录(未知文件,可考虑用户授权后上传分析)")
        else:
            lines.append(f"VirusTotal: 查询失败 {vt.get('error','')}")
    else:
        lines.append("VirusTotal: 未配 key")
    lines.append("—— 判定建议:仅依据云端信誉,供用户参考;是否删除/隔离由用户决定,本工具不执行。")
    return "\n".join(lines)


# ---------- 思考档位抽象(off/low/medium/high) ----------
# 各家「思考/推理」参数不统一:GPT/Codex=reasoning_effort(low/medium/high),
# Claude=thinking{budget_tokens}, Kimi/Qwen=enable_thinking, K3 等无档位。
# 利用 litellm.drop_params=True(不识别的参数静默丢弃),统一同时下发
# reasoning_effort + thinking 预算,由 litellm 按 provider 各自翻译/丢弃。
THINK_LEVELS = ("off", "low", "medium", "high")
_THINK_BUDGET = {"off": 0, "low": 1024, "medium": 4096, "high": 16384}


def _think_kwargs(think_level: str) -> dict:
    """把统一档位翻译成 litellm kwargs。off → 尽量关思考。"""
    lvl = (think_level or "").lower()
    if lvl not in THINK_LEVELS:
        return {}
    if lvl == "off":
        return {"reasoning_effort": "minimal", "thinking": {"type": "disabled"}}
    return {
        "reasoning_effort": lvl,  # low/medium/high(GPT/Codex 系)
        "thinking": {"type": "enabled", "budget_tokens": _THINK_BUDGET[lvl]},  # Claude 系
    }


def _completion_with_temperature_fallback(**kwargs):
    """调 litellm.completion,对「temperature 取值受限」的模型自动降级重试。

    部分平台(如 kimi coding 系列)只接受固定 temperature(如仅允许 1),
    对硬编码的 0.7/0 报 "invalid temperature: only 1 is allowed"。
    策略:先用期望温度;若报 invalid temperature,则改为「不带 temperature」让服务端用默认。
    配合 litellm.drop_params=True,不支持的参数会被丢弃而不是报错。
    """
    import litellm
    litellm.drop_params = True
    try:
        return litellm.completion(**kwargs)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "temperature" in msg and ("invalid" in msg or "only" in msg or "not supported" in msg):
            kwargs.pop("temperature", None)
            return litellm.completion(**kwargs)
        raise


def _t_parallel_ask(questions: list, model: str, workdir: str) -> str:
    """最小并行问答(2026-08-24,对标 Hermes 子代理并行的收益点,不碰其架构):
    把 2-4 个相互独立的子问题并发抛给 LLM 纯问答(禁工具/独立单条历史),合并成单轮
    工具结果返回主对话。省的是串行问的 wall-clock;子问答不带工具,天然无危险动作不弹卡。
    Hermes 的「RPC 折叠零上下文成本」在此弱化为「一次工具调用成本」。"""
    qs = [str(q).strip() for q in questions if str(q).strip()][:4]
    if len(qs) < 2:
        return "[parallel_ask] 需要 2-4 个相互独立的子问题"
    if not model:
        return "[parallel_ask] 无可用模型"
    import litellm
    from concurrent.futures import ThreadPoolExecutor

    def _one(q: str) -> str:
        try:
            resp = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": q}],
                temperature=0.7)
            return (resp.choices[0].message.content or "").strip() or "[空回答]"
        except Exception as e:  # noqa: BLE001
            return f"[该子问题出错] {type(e).__name__}: {e}"

    # 并发跑(纯 IO 等待,GIL 无碍);任一失败不影响其他。
    with ThreadPoolExecutor(max_workers=len(qs)) as ex:
        answers = list(ex.map(_one, qs))
    parts = ["[并行问答结果 — 各子问题独立回答,已合并]"]
    for i, (q, a) in enumerate(zip(qs, answers), 1):
        parts.append(f"\n## 子问题 {i}:{q}\n{a}")
    return "\n".join(parts)


TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file and return its contents.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Write text content to a file (creates parent dirs).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "run_shell",
        "description": "Run a shell command in the working directory and return rc/stdout/stderr.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "list_files",
        "description": "List files under a path (relative to workdir).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search_files",
        "description": "Search files by name/path keyword across the whole disk (via Everything es.exe if available, else walk workdir). Faster than list_files for finding a file whose location you don't know.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "filename/path keyword to search"},
            "limit": {"type": "integer", "description": "max results, default 50"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "local_file_search",
        "description": "Search local files by name/path keyword using the self-built index (prisir_findex, no Everything needed). Requires the user to have enabled 本机文件搜索 (index built). Fast whole-disk substring search across scanned drives. Use when the user mentions a file/资料 but doesn't remember where it is.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "filename/path keyword to search"},
            "limit": {"type": "integer", "description": "max results, default 30"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "local_content_search",
        "description": "Search local files by CONTENT (docx/pdf/pptx/txt/md/code), returns matching snippets. Uses the optional content-index module (prisir_fcontent), requires the user to have enabled 本机内容搜索 and authorized directories. Use when the user mentions a document/资料 by what it says, not its name. For name/path search use local_file_search.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "content keyword to search"},
            "limit": {"type": "integer", "description": "max results, default 30"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "anytxt_search",
        "description": "Search local files by CONTENT via the AnyTXT Searcher service (127.0.0.1:9920), if installed and running on this machine. Strong on docx/pdf/ppt full text and image OCR — use it to complement local_content_search (prisir_fcontent) when the user searches Office documents or scans. Read-only, hash-free, no upload. If it reports unavailable, fall back to local_content_search / local_file_search.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "content keyword to search"},
            "limit": {"type": "integer", "description": "max results, default 20"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web via Bing RSS (cn.bing.com, free, no key, works in mainland China without VPN). Use ONLY when the user explicitly asks to 搜一下/联网查/搜索/查资料 — do NOT search proactively. Returns titles+snippets+links. For local file/content search use findex/fcontent/anytxt tools instead. If the user needs stronger/custom search, tell them they can self-configure a search API key (Bing/Google/SerpAPI etc.) — keys are user-managed, never preset.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "search keyword"},
            "limit": {"type": "integer", "description": "max results, default 8"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "translate_document",
        "description": "Translate a local document (docx/pdf/pptx/txt/md) into the target language and write a translated file alongside the original (<name>.<lang>.<ext>). Plain-text round-trip (original layout not preserved). Uses the free google_gtx backend — content goes to Google Translate, tell the user. docx/pdf/pptx need their parser libs present; txt/md always work. Use when the user asks to 翻译文档/把这个文件翻译成X.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "absolute path of the document"},
            "target_lang": {"type": "string", "description": "target language, e.g. zh/en/ja/ko or 中文/英文, default zh"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "translate_image",
        "description": "Translate text inside an image (screenshot/scan/comic): OCR the text, translate it, erase the original text and overlay the translation, producing <name>.translated.png (original untouched). Requires rapidocr_onnxruntime installed locally (otherwise it will say so honestly). Use when the user asks to 翻译图片/这张图里写的是什么并翻译.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "absolute path of the image"},
            "target_lang": {"type": "string", "description": "target language, default zh"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "read_file_head",
        "description": "Read the first N chars of a file to get context after locating it (e.g. via local_file_search). Truncates to avoid blowing up context.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "absolute file path"},
            "max_chars": {"type": "integer", "description": "max chars to read, default 4000"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "file_reputation",
        "description": "Check a file's safety reputation (assist malware checking). Computes its SHA256 locally (read-only), then queries cloud reputation by HASH ONLY (MalwareBazaar / VirusTotal, if their free API keys are configured). NEVER uploads the file body, NEVER deletes/quarantines — returns verdict + suggestion; the decision stays with the user.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "absolute file path"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "parallel_ask",
        "description": "Ask 2-4 INDEPENDENT sub-questions concurrently and return their answers merged. Use ONLY when the user's request decomposes into multiple independent questions that don't depend on each other's answers (e.g. 'compare A and B', 'explain X and also Y'). Sub-questions are pure Q&A (no tools, no file access) answered in parallel, saving wall-clock vs asking sequentially. Do NOT use for a single question, sequential steps, or anything needing tools/files.",
        "parameters": {"type": "object", "properties": {
            "questions": {"type": "array", "items": {"type": "string"},
                          "description": "2-4 mutually independent sub-questions"}},
            "required": ["questions"]}}},
]


# v1.0 权限闸总开关。对话模式(run_conversation)默认开;benchmark 自主模式
# (run_agent)无人确认,置 False 跳过闸。不就地改 run_agent 的分支判断,只读此开关。
PERM_GATE_ENABLED = True


def dispatch(name: str, args: dict, workdir: str, on_confirm=None, model: str = "") -> str:
    # ---- v1.0 权限闸:写/执行/删除类工具真正执行前先过 coworker 引擎 ----
    # on_confirm: 可选回调 fn({tool,risk,reason,preview}) -> bool;None=无法弹卡,
    #             此时 requires_approval 的动作一律按拒绝处理(fail-closed)。
    if PERM_GATE_ENABLED:
        try:
            from perm_gate import GATED_TOOLS, check
            if name in GATED_TOOLS:
                verdict = check(name, args, workdir)
                if not verdict["allow"]:
                    preview = ""
                    if isinstance(args, dict):
                        preview = (args.get("command") or args.get("path") or "")[:300]
                    if verdict.get("requires_approval") and on_confirm is not None:
                        try:
                            ok = bool(on_confirm({
                                "tool": name,
                                "risk": verdict.get("risk_level", "exec"),
                                "reason": verdict.get("reason", ""),
                                "preview": preview,
                            }))
                        except Exception:  # noqa: BLE001 — 确认回调异常按拒绝
                            ok = False
                        if not ok:
                            return f"[{name} 被用户拒绝] {verdict.get('reason','')}"
                    else:
                        return f"[{name} 被权限闸拦截] {verdict.get('reason','')}"
        except Exception:  # noqa: BLE001 — 闸自身故障:fail-closed 拒写/执行
            if name in ("run_shell", "write_file", "delete_file"):
                return f"[{name} 被权限闸拦截] gate unavailable (fail-closed)"
    if name == "read_file":
        p = args.get("path", "")
        p = p if os.path.isabs(p) else os.path.join(workdir, p)
        return _t_read_file(p)
    if name == "write_file":
        p = args.get("path", "")
        p = p if os.path.isabs(p) else os.path.join(workdir, p)
        return _t_write_file(p, args.get("content", ""), workdir)
    if name == "run_shell":
        return _t_run_shell(args.get("command", ""), workdir)
    if name == "list_files":
        return _t_list_files(args.get("path", "."), workdir)
    if name == "search_files":
        return _t_search_files(args.get("query", ""), workdir, args.get("limit", 50))
    if name == "local_file_search":
        return _t_local_file_search(args.get("query", ""), args.get("limit", 30))
    if name == "local_content_search":
        return _t_local_content_search(args.get("query", ""), args.get("limit", 30))
    if name == "anytxt_search":
        return _t_anytxt_search(args.get("query", ""), args.get("limit", 20))
    if name == "web_search":
        return _t_web_search(args.get("query", ""), args.get("limit", 8))
    if name == "translate_document":
        return _t_translate_document(args.get("path", ""), args.get("target_lang", "zh"), workdir)
    if name == "translate_image":
        return _t_translate_image(args.get("path", ""), args.get("target_lang", "zh"), workdir)
    if name == "read_file_head":
        return _t_read_file_head(args.get("path", ""), args.get("max_chars", 4000))
    if name == "file_reputation":
        return _t_file_reputation(args.get("path", ""))
    if name == "parallel_ask":
        return _t_parallel_ask(args.get("questions") or [], model, workdir)
    return f"[unknown tool {name}]"


SYSTEM = (
    "You are oiagent, an autonomous coding agent in a benchmark. "
    "Work fully autonomously: use the provided tools to read files, write files, and run shell commands. "
    "Never ask the user for confirmation or for files — everything you need is in the working directory. "
    "Always create the exact output files the task requires. "
    "When the task is 100% complete and all required files are written, reply with the single word DONE."
)

# 对话模式(Prisir AI):助手问答,不强制 DONE,保留多轮历史,工具可选
# 2026-08-28 改名:对外统一自称「Prisir(湃睿思) AI」,不再用 oiagent 这个开发态代号。
# 产品矩阵里 Prisir AI 是桌面端对话壳(Win/Linux/macOS),Prisir Browser 是浏览器,
# 别再把对话壳叫成 Prisir Browser——训练语料跟产品名混淆,模型会跟着错。
SYSTEM_CHAT = (
    "你是 Prisir(湃睿思) AI——一个本地对话助手,装在 Prisir AI 桌面壳里"
    "(Win/Linux/macOS,无账号、本地优先,key 也不上传)。"
    "自我介绍或被问到名字时,始终用「Prisir(湃睿思) AI」,不要再说「oiagent」或其他开发代号。"
    "回答用户问题时直接、清晰,用用户的语言(中文问中文答、英文问英文答)。"
    "可用工具读/写文件、跑 shell、搜文件,但纯问答直接 prose 即可,回答要聚焦、结构好。\n\n"
    "Attachments: the user can attach files to a message. Text/code files arrive inlined under "
    "'--- 附件 name ---' markers (truncated at 12k chars); images arrive as multimodal content you can see directly. "
    "A GIF arrives as its first frame only — you cannot watch the animation, so say so honestly if asked. "
    "Audio/video are NOT auto-transcribed: if the user wants subtitles/transcription, offer to run the local "
    "ffmpeg + faster-whisper pipeline via run_shell when those tools are available (check the available-tools list), "
    "or write a script the user can run.\n\n"
    "Tools: prefer search_files (Everything es.exe on Windows; prisir_findex on Linux/macOS, "
    "whole-disk instant) over list_files when locating a file whose location you don't know; "
    "use list_files for browsing a known directory tree.\n\n"
    "Diagrams: the chat UI renders Markdown AND Mermaid. When explaining structure, flow, sequence, "
    "state, or architecture, PREFER a ```mermaid diagram over long prose — it is far more intuitive "
    "than text. Use the right type: flowchart (graph TD/LR) for processes & architecture, "
    "sequenceDiagram for interaction/order, stateDiagram-v2 for state machines, erDiagram for data, "
    "gantt for schedules, classDiagram for types. Keep diagrams focused (one idea each, ≤ ~15 nodes); "
    "add a one-line caption before/after. Node labels may be Chinese. Only emit valid mermaid syntax."
)


def run_conversation(messages: list, model: str, workdir: str, max_turns: int = 20,
                     use_tools: bool = True, think_level: str = "",
                     system_extra: str = "", on_event=None, on_confirm=None) -> dict:
    """对话模式:接受完整多轮 messages([{role,content}]),返回单轮 assistant 回复。

    与 run_agent 的区别:
      - 不注入"自主任务 + DONE"系统提示,改用 SYSTEM_CHAT 助手提示
      - 返回最后一轮 assistant content,不循环到 DONE
      - 仍允许工具调用(max_turns 内自动续跑,默认 20 — 够 ffmpeg→脚本→跑→收尾这类多步任务),
        收到无工具调用的文本回复即返回
      - think_level: off/low/medium/high,空=不指定(用平台默认)
      - system_extra: 额外系统块(harness 宪法/记忆召回),拼在 SYSTEM_CHAT 之后
      - on_event: 可选回调 fn(dict),在工具执行前后发实时进度事件(壳端轮询展示用):
          {"type":"tool_start","name":..., "args_preview":...} 与
          {"type":"tool_end","name":..., "ms":..., "ok":bool, "output_preview":...}
        默认 None(壳外路径不受影响)。回调内不抛异常(包裹 try)。
      - on_confirm: 可选回调 fn({tool,risk,reason,preview}) -> bool,权限闸命中
        需确认动作时阻塞等用户批准(壳端弹卡)。None=无法弹卡,需确认动作一律按拒绝。

    返回 dict 新增 `trace`: 当轮新增的中间步 [{role:"tool"|"assistant", content, name?}]
    (tool 结果已按 TOOL_STORE_MAX 截断供入库),供调用方(壳)落库激活跨轮 masking;
    `out` 仍是最终答复文本(不含中间步)。
    """
    import litellm
    litellm.drop_params = True
    system = SYSTEM_CHAT + (("\n\n" + system_extra) if system_extra.strip() else "")
    msgs = [{"role": "system", "content": system}] + list(messages)
    trace: list = []  # 当轮 tool/assistant 中间步(供壳入库)
    t0 = time.time()
    turns = 0
    tools = TOOLS if use_tools else None
    think_kw = _think_kwargs(think_level)
    for _ in range(max(1, max_turns)):
        turns += 1
        try:
            kwargs = dict(model=model, messages=msgs, temperature=0.7)
            kwargs.update(think_kw)
            if tools:
                kwargs.update(tools=tools, tool_choice="auto")
            resp = _completion_with_temperature_fallback(**kwargs)
        except Exception as e:  # noqa: BLE001
            return {"rc": 2, "out": f"[llm error] {type(e).__name__}: {e}", "turns": turns,
                    "ms": int((time.time() - t0) * 1000), "trace": trace}
        msg = resp.choices[0].message
        am = {"role": "assistant", "content": msg.content or ""}
        tcs = getattr(msg, "tool_calls", None)
        if tcs:
            am["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tcs]
        msgs.append(am)

        if not tcs:
            # 无工具调用 → 这就是答复,返回
            return {"rc": 0, "out": (msg.content or "").strip(), "turns": turns,
                    "ms": int((time.time() - t0) * 1000), "trace": trace}

        for tc in tcs:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            _tname = tc.function.name
            if on_event:
                # tool_start 的 on_event 异常必须传播:estop 靠在此抛 _EstopInterrupt
                # 中断工具链(工具边界停)。若吞掉,中断信号到不了外层,工具照常执行。
                _ap = json.dumps(args, ensure_ascii=False)
                on_event({"type": "tool_start", "name": _tname,
                          "args_preview": _ap[:200]})
            _te0 = time.time()
            result = dispatch(_tname, args, workdir, on_confirm=on_confirm, model=model)
            _tms = int((time.time() - _te0) * 1000)
            msgs.append({"role": "tool", "tool_call_id": tc.id,
                         "name": _tname, "content": result})
            # 轨迹(供壳入库激活跨轮 masking): 截断后存,带工具名保可追溯
            trace.append({"role": "tool", "name": _tname,
                          "content": truncate_tool_output(result)})
            if on_event:
                try:
                    on_event({"type": "tool_end", "name": _tname, "ms": _tms,
                              "ok": not result.startswith("["),
                              "output_preview": truncate_tool_output(result)[:300]})
                except Exception:  # noqa: BLE001
                    pass

    # 工具循环到头仍未给文本答复 → 取最后一条 assistant 文本
    last = next((m["content"] for m in reversed(msgs) if m.get("role") == "assistant" and m.get("content")), "")
    return {"rc": 0, "out": last or "[no reply]", "turns": turns,
            "ms": int((time.time() - t0) * 1000), "trace": trace}


def run_agent(prompt: str, model: str, workdir: str, max_turns: int) -> dict:
    import litellm
    litellm.drop_params = True
    # benchmark 自主模式:无人可确认,关掉权限闸(全局开关在本次调用内置 False)。
    global PERM_GATE_ENABLED
    _prev_gate = PERM_GATE_ENABLED
    PERM_GATE_ENABLED = False
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt + f"\n\n[working directory: {workdir}]"},
    ]
    t0 = time.time()
    turns = 0
    try:
        for _ in range(max_turns):
            turns += 1
            try:
                resp = _completion_with_temperature_fallback(model=model, messages=messages, tools=TOOLS,
                                          tool_choice="auto", temperature=0)
            except Exception as e:  # noqa: BLE001
                return {"rc": 2, "out": f"[llm error] {type(e).__name__}: {e}", "turns": turns,
                        "ms": int((time.time() - t0) * 1000)}
            msg = resp.choices[0].message
            # record assistant message (with tool_calls if any)
            am = {"role": "assistant", "content": msg.content or ""}
            tcs = getattr(msg, "tool_calls", None)
            if tcs:
                am["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tcs]
            messages.append(am)

            if not tcs:
                text = (msg.content or "").strip()
                if "DONE" in text.upper():
                    return {"rc": 0, "out": text, "turns": turns, "ms": int((time.time() - t0) * 1000)}
                # no tool call and not done: nudge once to keep it autonomous
                messages.append({"role": "user", "content":
                                 "Continue autonomously. Use tools to act on files. Reply DONE only when all required files exist."})
                continue

            for tc in tcs:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:  # noqa: BLE001
                    args = {}
                result = dispatch(tc.function.name, args, workdir)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "name": tc.function.name, "content": result})

        return {"rc": 1, "out": "[max turns reached]", "turns": turns, "ms": int((time.time() - t0) * 1000)}
    finally:
        PERM_GATE_ENABLED = _prev_gate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--message-file")
    ap.add_argument("--message")
    ap.add_argument("--model", default=os.environ.get("SPIKE2_OIAGENT_MODEL", "openrouter/deepseek/deepseek-chat"))
    ap.add_argument("--max-turns", type=int, default=MAX_TURNS_DEFAULT)
    ap.add_argument("--workdir", default=os.getcwd())
    a = ap.parse_args()
    if a.message_file:
        prompt = open(a.message_file, encoding="utf-8").read()
    elif a.message:
        prompt = a.message
    else:
        prompt = sys.stdin.read()
    res = run_agent(prompt, a.model, a.workdir, a.max_turns)
    print(res["out"])
    print(f"\n[oiagent-cli rc={res['rc']} turns={res['turns']} ms={res['ms']}]", file=sys.stderr)
    sys.exit(0 if res["rc"] == 0 else 1)


if __name__ == "__main__":
    main()
