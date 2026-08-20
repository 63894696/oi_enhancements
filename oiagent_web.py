"""oiagent_web.py — Prisir AI 对话模式 web UI(无账号、本地持久化、Perplexity 式导出)

对话模式(用户指令:"OIagent一定要设置为对话模式,对话页面要仿造做出截图箭头中的导出"):
  - 多轮对话,历史持久化到本地 SQLite(刷新/重启不丢)
  - ⋯ 菜单仿 Perplexity: 固定的 / 重命名会话 / 导出为PDF / 衍生为Markdown / 导出为DOCX / 删除
  - 每次 AI 回答末尾自动带 2-5 个延续话题(学 Perplexity)
  - Prisir AI 路由: 用户自填 OpenAI/Anthropic/自定义端点 key,智能分任务调模型
  - 无账号、无云同步: 一切数据只存本地

Usage:
  python oiagent_web.py [--port 18802] [--strategy smart]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import html
import json
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oiagent_cli import run_conversation  # noqa: E402
from fastlane.providers.llm_prisir import (  # noqa: E402
    PrisirKeyStore, PrisirRouter, generate_followups, list_endpoint_models,
)

WEB_HOST = "127.0.0.1"
WEB_PORT = int(os.environ.get("OIAGENT_WEB_PORT", "18802"))
DEFAULT_MODEL = os.environ.get("OIAGENT_MODEL", "dashscope/qwen3-coder-plus-2025-09-23")
DEFAULT_WORKDIR = os.environ.get("OIAGENT_WORKDIR", os.getcwd())
DEFAULT_STRATEGY = os.environ.get("PRISIR_STRATEGY", "smart")

# Obsidian 经验导出(路线 B):提炼对话成经验文档落 vault。
# 与 team_lead_tools.OBSIDIAN_VAULT 同源,env OBSIDIAN_VAULT 可覆盖。
OBSIDIAN_VAULT = Path(os.environ.get(
    "OBSIDIAN_VAULT", r"C:/Users/Administrator/Documents/ObsidianVault"))
OBSIDIAN_EXPERIENCES_DIR = OBSIDIAN_VAULT / "experiences"

_DB_DIR = Path(os.environ.get("PRISIR_DATA", str(Path.home() / ".local" / "share" / "prisir")))
_DB_DIR.mkdir(parents=True, exist_ok=True)
_CHAT_DB = _DB_DIR / "chats.db"

_key_store = PrisirKeyStore()
_router = PrisirRouter(_key_store)

# ============================================================
# harness 接线(对话壳版):宪法契约 + OIMemory 记忆召回
# 复用 oiagent 协作链路的两个既有件,不重造:
#   - docs/prisir-dev-constitution.md(契约,同 oiagent_dev_consumer._load_constitution)
#   - memory/oi_memory.py OIMemory.recall(dev_lessons/历史上下文,同 oi_memory_hooks)
# 对话壳不是开发团队执行 agent,故注入「壳适配」的纪律提示而非完整开发宪法;
# 记忆召回默认开(OIAGENT_RECALL=0 关),失败一律静默不阻塞对话。
# ============================================================
_REPO_ROOT = Path(__file__).resolve().parent
_CONSTITUTION_PATH = _REPO_ROOT / "docs" / "prisir-dev-constitution.md"
_OI_MEM: object | None = None


def _load_constitution() -> str:
    try:
        return _CONSTITUTION_PATH.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return ""


def _get_oi_memory():
    """惰性单例。memory/ 不在包路径,显式插 sys.path;不可用则 None。"""
    global _OI_MEM
    if _OI_MEM is not None:
        return _OI_MEM
    try:
        mem_dir = str(_REPO_ROOT / "memory")
        if mem_dir not in sys.path:
            sys.path.insert(0, mem_dir)
        from oi_memory import OIMemory  # noqa: PLC0415
        _OI_MEM = OIMemory()
    except Exception:  # noqa: BLE001
        _OI_MEM = None
    return _OI_MEM


def _shell_system_prompt(user_text: str) -> str:
    """组壳对话的系统提示:纪律 preamble + 记忆召回块 + 本机环境块。全程失败静默。"""
    parts = []
    constitution = _load_constitution()
    if constitution:
        parts.append(
            "你是 oiagent,运行在本地对话壳(Prisir Shell)。下面【项目宪法】是硬性技术契约,"
            "涉及凭证/密钥/网络/代码正确性时以它为准,违反即返工;普通问答不影响。\n\n"
            "【项目宪法】\n" + constitution)
    # 本机环境:可用工具 + 已配端点,让模型不用猜/不用现查
    env_block = _local_env_block()
    if env_block:
        parts.append(env_block)
    # 记忆召回:按本轮问题召回相关历史/dev_lessons
    if os.environ.get("OIAGENT_RECALL", "1") != "0":
        mem = _get_oi_memory()
        if mem is not None and user_text.strip():
            try:
                hits = mem.recall(user_text, n=4, visible_to="oi-shell")
                if hits:
                    lines = ["[记忆召回 — 相关历史/经验]"]
                    for i, h in enumerate(hits, 1):
                        snippet = (h.content or "")[:180].replace("\n", " ")
                        lines.append(f"  {i}. [{h.layer}] {h.title}: {snippet}")
                    lines.append("[召回结束]")
                    parts.append("\n".join(lines))
            except Exception:  # noqa: BLE001
                pass
    return "\n\n".join(parts)


# ============================================================
# 本机环境发现(任务#36):让模型知道本机已装什么、已配哪些端点,不用现查现猜
# 每 60s 重扫一次(PATH/已装软件可能变);任何一步失败都静默降级,不阻塞对话。
# ============================================================
_ENV_CACHE: dict = {"ts": 0.0, "text": ""}
_ENV_CACHE_TTL = 60.0

# 要探测的本机工具: name -> 候选解析方式
_LOCAL_TOOL_CANDIDATES = (
    ("ffmpeg", ("ffmpeg",)),
    ("whisper (OpenAI ASR)", ("whisper",)),
    ("es.exe (Everything 全盘搜索)", ("es.exe", "es")),
)


def _detect_local_tools() -> list[str]:
    """扫 PATH + 已知路径,返回已就位的本机工具描述行。"""
    import shutil  # noqa: PLC0415
    lines = []
    for label, cmds in _LOCAL_TOOL_CANDIDATES:
        found = ""
        for c in cmds:
            p = shutil.which(c)
            if p:
                found = p
                break
        # es.exe 常不在 PATH,补已知落地路径
        if not found and label.startswith("es.exe"):
            for cand in (
                r"D:\down\es-temp\ES-extracted\es.exe",
                r"D:\down\Everything系统搜索工具\Everything-1.4.1.969.x64\es.exe",
                r"C:\Program Files\Everything\es.exe",
            ):
                if os.path.isfile(cand):
                    found = cand
                    break
        if found:
            lines.append(f"  - {label}: {found}")
    # Python 包级工具(无独立 exe 也可调用)
    try:
        import importlib.util  # noqa: PLC0415
        if importlib.util.find_spec("faster_whisper") is not None:
            lines.append("  - faster-whisper (Python 包, 音视频转字幕, 比 whisper 快): 已装")
    except Exception:  # noqa: BLE001
        pass
    try:
        import imageio_ffmpeg  # noqa: PLC0415
        lines.append(f"  - ffmpeg (imageio-ffmpeg 自带): {imageio_ffmpeg.get_ffmpeg_exe()}")
    except Exception:  # noqa: BLE001
        pass
    return lines


def _configured_endpoints() -> list[str]:
    """列出已配置的模型端点(只示 base_url + model + 有无 key,绝不回显 key 本体)。"""
    lines = []
    try:
        for p in _key_store.list_platforms():
            if not p.get("has_key"):
                continue
            base = p.get("base_url") or "(默认)"
            model = p.get("model") or "(未设)"
            proto = (p.get("meta") or {}).get("proto", "")
            proto_tag = f" [{proto}协议]" if proto else ""
            lines.append(f"  - {p['platform']}: {base} 模型={model}{proto_tag} key={p.get('key_hint','***')}")
    except Exception:  # noqa: BLE001
        pass
    return lines


# 模型池/CCSwitch 注册表(用户既有资产,周更):读 ~/.cc-switch/model_pool.json,
# 给模型一份「还有哪些端点可路由、各自强弱」的清单。只读,绝不回显 key。
_MODEL_POOL_JSON = Path.home() / ".cc-switch" / "model_pool.json"


def _model_pool_block() -> list[str]:
    """摘 model_pool.json:可用模型名 + provider + 强弱标签。读不到/解析失败返回 []。"""
    try:
        if not _MODEL_POOL_JSON.is_file():
            return []
        d = json.loads(_MODEL_POOL_JSON.read_text(encoding="utf-8"))
        models = d.get("models") if isinstance(d, dict) else None
        if not isinstance(models, dict) or not models:
            return []
        lines = [f"模型池注册表(每周探活, 共 {len(models)} 个, 文件 ~/.cc-switch/model_pool.json; 可让路由层按 key_env 换端点):"]
        # 只列前若干 + 标不可用的跳过 key 细节,按 provider 归组精简
        shown = 0
        for name, m in models.items():
            if shown >= 12:
                lines.append(f"  … 其余 {len(models)-shown} 个见文件")
                break
            if not isinstance(m, dict):
                continue
            prov = m.get("provider", "?")
            strengths = ",".join(m.get("strengths", [])[:3]) or "通用"
            # _unavailable = 上次周探时该模型的 env key 未配/不可达,不代表模型本身无效
            # (用户可能经自定义端点带自有 key 在用,如 minimax-m3);故标 key 状态而非「不可用」
            keystate = " [池env-key未配]" if m.get("_unavailable") else ""
            lines.append(f"  - {name} ({prov}): 长项 {strengths}{keystate}")
            shown += 1
        return lines
    except Exception:  # noqa: BLE001
        return []


def _local_env_block() -> str:
    """组 [本机环境] 块:可用工具 + 已配端点。带 60s 缓存。"""
    now = time.time()
    if _ENV_CACHE["text"] and (now - _ENV_CACHE["ts"]) < _ENV_CACHE_TTL:
        return _ENV_CACHE["text"]
    tools = _detect_local_tools()
    endpoints = _configured_endpoints()
    if not tools and not endpoints:
        return ""
    parts = ["[本机环境 — 已可用,不用现查]"]
    if tools:
        parts.append("本机已装工具(可直接通过 run_shell 调用):")
        parts.extend(tools)
        parts.append("  音视频转字幕工作流: ffmpeg 抽音轨 → faster-whisper 转写 → .srt")
    if endpoints:
        parts.append("已配置模型端点(对话壳路由用,key 不回显):")
        parts.extend(endpoints)
    pool = _model_pool_block()
    if pool:
        parts.extend(pool)
    parts.append("[环境结束]")
    text = "\n".join(parts)
    _ENV_CACHE["text"] = text
    _ENV_CACHE["ts"] = now
    return text

# 运行中会话的内存锁/状态(结果落 SQLite,运行状态在内存)
_running: dict[str, bool] = {}
_running_lock = threading.Lock()

# 工作目录(可被 /api/workdir 覆盖,内存态;工具调用以此为 cwd)
_WORKDIR = {"path": DEFAULT_WORKDIR}

# 附件大小护栏:文本最多内联 12k 字符,超出截断提示;图片走多模态 content
_ATTACH_TEXT_MAX = 12000
_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _build_user_content(user_text: str, attachments: list):
    """把用户文本 + 附件组装成发给模型的 content。
    文本/代码附件 → 内联进文本(带文件名标注);图片 → OpenAI 多模态 content 列表。
    attachments: [{name, text?, data_base64?, mime?}](由 /api/upload 或前端直传)
    """
    atts = [a for a in (attachments or []) if isinstance(a, dict)]
    if not atts:
        return user_text
    images = [a for a in atts if a.get("data_base64") and (
        (a.get("mime") or "").startswith("image/") or
        os.path.splitext(a.get("name", ""))[1].lower() in _IMG_EXT)]
    texts = [a for a in atts if a not in images and (a.get("text") or a.get("data_base64"))]
    if images:
        # 多模态 content 列表(OpenAI/Claude 通用格式,litellm 透传)
        content = [{"type": "text", "text": user_text}]
        for a in texts:  # 文本附件先并进 text 块
            pass
        for a in images:
            mime = a.get("mime") or "image/png"
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{a['data_base64']}"}})
        if texts:
            blob = _inline_text_attachments(texts)
            content[0]["text"] = (user_text + blob) if blob else user_text
        return content
    # 纯文本附件 → 内联
    return user_text + _inline_text_attachments(texts)


def _inline_text_attachments(atts: list) -> str:
    parts = []
    for a in atts:
        name = a.get("name", "file")
        txt = a.get("text", "")
        if not txt and a.get("data_base64"):
            try:
                txt = base64.b64decode(a["data_base64"]).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                txt = ""
        if len(txt) > _ATTACH_TEXT_MAX:
            txt = txt[:_ATTACH_TEXT_MAX] + f"\n…[截断,原 {len(txt)} 字符]"
        if txt.strip():
            parts.append(f"\n\n--- 附件 {name} ---\n{txt}")
    return "".join(parts)


# ============================================================
# 会话持久化(SQLite)
# ============================================================
def _db():
    c = sqlite3.connect(_CHAT_DB)
    c.execute("""CREATE TABLE IF NOT EXISTS sessions(
        id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '新会话',
        pinned INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL DEFAULT 0,
        updated INTEGER NOT NULL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
        role TEXT NOT NULL, content TEXT NOT NULL, followups TEXT NOT NULL DEFAULT '[]',
        ts INTEGER NOT NULL DEFAULT 0)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_sess ON messages(session_id, id)")
    return c


def _now() -> int:
    return int(time.time())


def create_session(title: str = "新会话") -> str:
    sid = uuid.uuid4().hex[:12]
    with _db() as c:
        c.execute("INSERT INTO sessions(id,title,pinned,created,updated) VALUES(?,?,?,?,?)",
                  (sid, title, 0, _now(), _now()))
    return sid


def get_session(sid: str):
    with _db() as c:
        return c.execute("SELECT id,title,pinned,created,updated FROM sessions WHERE id=?", (sid,)).fetchone()


def list_sessions():
    with _db() as c:
        rows = c.execute(
            "SELECT id,title,pinned,created,updated FROM sessions ORDER BY pinned DESC, updated DESC").fetchall()
    return [{"id": r[0], "title": r[1], "pinned": bool(r[2]), "created": r[3], "updated": r[4]} for r in rows]


def add_message(sid: str, role: str, content: str, followups=None) -> None:
    with _db() as c:
        c.execute("INSERT INTO messages(session_id,role,content,followups,ts) VALUES(?,?,?,?,?)",
                  (sid, role, content, json.dumps(followups or [], ensure_ascii=False), _now()))
        c.execute("UPDATE sessions SET updated=? WHERE id=?", (_now(), sid))


def get_messages(sid: str):
    with _db() as c:
        rows = c.execute(
            "SELECT role,content,followups,ts FROM messages WHERE session_id=? ORDER BY id", (sid,)).fetchall()
    return [{"role": r[0], "content": r[1], "followups": json.loads(r[2] or "[]"), "ts": r[3]} for r in rows]


def rename_session(sid: str, title: str) -> None:
    with _db() as c:
        c.execute("UPDATE sessions SET title=?, updated=? WHERE id=?", (title, _now(), sid))


def pin_session(sid: str, pinned: bool) -> None:
    with _db() as c:
        c.execute("UPDATE sessions SET pinned=? WHERE id=?", (1 if pinned else 0, sid))


def delete_session(sid: str) -> None:
    with _db() as c:
        c.execute("DELETE FROM messages WHERE session_id=?", (sid,))
        c.execute("DELETE FROM sessions WHERE id=?", (sid,))


# ============================================================
# 对话执行(后台线程 → asyncio 跑 router + followups)
# ============================================================
def _run_chat_thread(sid: str, user_text: str, strategy: str, model: str, workdir: str,
                     think_level: str = "", attachments: list | None = None):
    try:
        history = get_messages(sid)
        msgs = [{"role": m["role"], "content": m["content"]} for m in history]
        # 组入附件:文本内联、图片走多模态
        content = _build_user_content(user_text, attachments)
        # harness 接线:宪法纪律 + 记忆召回(壳适配系统块,失败静默)
        sys_extra = _shell_system_prompt(user_text)

        use_router = bool(_router.available_platforms())
        if use_router:
            # Prisir 路由: 用 router 选定平台后,把该平台模型映射到 litellm model 串
            pick = _router.route(msgs + [{"role": "user", "content": user_text}], strategy)
            platform, cfg = pick["platform"], pick["cfg"]
            lm = _litellm_model_for(platform, cfg, pick["task_type"])
            res = run_conversation(msgs + [{"role": "user", "content": content}], lm, workdir,
                                   think_level=think_level, system_extra=sys_extra)
            answer = res["out"]
            used = f"{platform}:{cfg['model']}"
        else:
            res = run_conversation(msgs + [{"role": "user", "content": content}], model, workdir,
                                   think_level=think_level, system_extra=sys_extra)
            answer = res["out"]
            used = model

        followups = []
        if len(answer) < 6000:  # 对话太长到底就不再推荐
            followups = asyncio.run(generate_followups(_router, user_text, answer, strategy=strategy)) \
                if use_router else []

        add_message(sid, "assistant", answer, followups)
        # 首轮自动生成标题
        sess = get_session(sid)
        if sess and sess[1] == "新会话":
            rename_session(sid, user_text[:24])
        _set_meta(sid, {"last_model": used, "rc": res["rc"]})
    except Exception as e:  # noqa: BLE001
        add_message(sid, "assistant", f"[错误] {type(e).__name__}: {e}", [])
    finally:
        with _running_lock:
            _running[sid] = False


def _litellm_model_for(platform: str, cfg: dict, task_type: str) -> str:
    """把 router 选定的平台映射成 litellm model 串,并注入 key/base 到 env。

    自定义端点按 cfg.meta.proto 区分协议:
      - openai(默认): OpenAI 兼容,走 openai/{model} + OPENAI_API_BASE → POST {base}/chat/completions
      - anthropic:     Anthropic Messages,走 anthropic/{model} + ANTHROPIC_BASE_URL → POST {base}/v1/messages
    """
    model = cfg["model"]
    if task_type == "fast" and cfg.get("fast_model"):
        model = cfg["fast_model"]
    if platform == "openai":
        os.environ["OPENAI_API_KEY"] = cfg["api_key"]
        return f"openai/{model}"
    if platform == "anthropic":
        os.environ["ANTHROPIC_API_KEY"] = cfg["api_key"]
        return f"anthropic/{model}"
    # 自定义端点:按协议分派
    proto = (cfg.get("meta") or {}).get("proto", "openai")
    base = cfg["base_url"].rstrip("/")
    if proto == "anthropic":
        os.environ["ANTHROPIC_API_KEY"] = cfg["api_key"] or "sk-local"
        os.environ["ANTHROPIC_BASE_URL"] = base
        return f"anthropic/{model}"
    # openai 兼容(默认)
    os.environ["OPENAI_API_KEY"] = cfg["api_key"] or "sk-local"
    os.environ["OPENAI_API_BASE"] = base
    return f"openai/{model}"


_SESS_META: dict[str, dict] = {}


def _set_meta(sid: str, m: dict) -> None:
    _SESS_META.setdefault(sid, {}).update(m)


def _get_meta(sid: str) -> dict:
    return _SESS_META.get(sid, {})


# ============================================================
# 导出(Markdown / PDF / DOCX)
# ============================================================
def _export_markdown(sid: str) -> str:
    sess = get_session(sid)
    title = sess[1] if sess else "会话"
    lines = [f"# {title}\n"]
    for m in get_messages(sid):
        if m["role"] == "user":
            lines.append(f"\n## 🧑 用户\n\n{m['content']}\n")
        elif m["role"] == "assistant":
            lines.append(f"\n## 🤖 oiagent\n\n{m['content']}\n")
            if m["followups"]:
                lines.append("\n**延续话题:** " + " / ".join(m["followups"]) + "\n")
    return "\n".join(lines)


def _export_html_for_pdf(sid: str) -> str:
    """打印友好 HTML(前端 window.print 或浏览器另存 PDF)"""
    sess = get_session(sid)
    title = html.escape(sess[1] if sess else "会话")
    parts = [f"<h1>{title}</h1>"]
    for m in get_messages(sid):
        role = "用户" if m["role"] == "user" else "oiagent"
        css = "user" if m["role"] == "user" else "agent"
        parts.append(f'<div class="msg {css}"><div class="role">{role}</div>'
                     f'<div class="body">{html.escape(m["content"]).replace(chr(10), "<br>")}</div></div>')
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:'Segoe UI','Microsoft YaHei',sans-serif;max-width:800px;margin:24px auto;padding:0 16px;color:#2f3a34}}
h1{{font-size:20px}}.msg{{margin:14px 0;padding:12px 16px;border-radius:12px;border:1px solid #d8cfbc}}
.msg.user{{background:#b23a30;color:#fbf6ec}}.msg.agent{{background:#fbf8f1}}
.role{{font-size:11px;opacity:.7;margin-bottom:6px}}.body{{line-height:1.6;white-space:pre-wrap}}
@media print{{.msg{{border:none}}}}</style></head><body>{''.join(parts)}
<script>window.onload=()=>window.print()</script></body></html>"""


def _export_docx(sid: str) -> bytes | None:
    """python-docx 可用则生成真 DOCX;否则返回 None(前端退化为 HTML .doc)"""
    try:
        from docx import Document  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    sess = get_session(sid)
    doc = Document()
    doc.add_heading(sess[1] if sess else "会话", 0)
    for m in get_messages(sid):
        role = "用户" if m["role"] == "user" else "oiagent"
        doc.add_heading(role, level=2)
        doc.add_paragraph(m["content"])
    import io
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _export_word_html(sid: str) -> str:
    """Word 兼容 HTML(.doc fallback)"""
    sess = get_session(sid)
    title = html.escape(sess[1] if sess else "会话")
    parts = [f"<h1>{title}</h1>"]
    for m in get_messages(sid):
        role = "用户" if m["role"] == "user" else "oiagent"
        parts.append(f"<h2>{role}</h2><p>{html.escape(m['content']).replace(chr(10), '<br>')}</p>")
    return ("<html xmlns:o='urn:schemas-microsoft-com:office:office' "
            "xmlns:w='urn:schemas-microsoft-com:office:word'><head><meta charset='utf-8'></head><body>"
            + "".join(parts) + "</body></html>")


# ============================================================
# 经验提炼存 Obsidian(路线 B)
# ============================================================
_EXPERIENCE_PROMPT = """把下面这段人机对话提炼成一篇「经验文档」,供日后检索复用。
只输出一个 JSON 对象(不要 markdown 代码围栏,不要任何额外文字),字段:
{
  "title": "一句话标题(≤30字,概括这次对话解决的核心问题)",
  "tldr": ["≤3 条要点,每条一句话"],
  "core": ["≤8 条核心经验/做法/结论,每条一句话,具体可执行"],
  "gotchas": ["踩坑/教训,没有就空数组"],
  "tags": ["3-6 个检索标签,短词"],
  "project": "涉及的项目名,看不出就空串"
}
要求:提炼**做法和结论**,不要复述对话过程;gotchas 只写真正踩到的坑。

对话内容:
---
%s
---"""


def _build_experience_doc(sid: str, distilled: dict) -> str:
    """套 frontmatter 模板(参照 team_lead_tools._save_team_experience_to_obsidian)。"""
    from datetime import datetime
    now_d = datetime.now().strftime("%Y-%m-%d")
    now_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    sess = get_session(sid)
    conv_title = sess[1] if sess else "会话"

    title = (distilled.get("title") or conv_title or "oiagent 对话经验").strip()
    tldr = [str(x) for x in (distilled.get("tldr") or [])][:3]
    core = [str(x) for x in (distilled.get("core") or [])][:8]
    gotchas = [str(x) for x in (distilled.get("gotchas") or [])]
    tags = [str(x) for x in (distilled.get("tags") or [])][:6]
    project = (distilled.get("project") or "").strip()

    all_tags = ["经验"] + [t for t in tags if t and t != "经验"]
    fm_lines = ["---", f"title: {title}", f"date: {now_d}", f"created_at: '{now_ts}'"]
    if project:
        fm_lines.append(f"project: {project}")
    fm_lines.append("tags:")
    fm_lines += [f"  - {t}" for t in all_tags]
    fm_lines += ["status: 已存档", "source_skill: oiagent-shell-experience",
                 f"related: [[{conv_title}]]" if conv_title else "related: []", "---"]

    body = [f"# {title}", ""]
    if tldr:
        body += ["## TL;DR"] + [f"- {p}" for p in tldr] + [""]
    if core:
        body += ["## 核心经验"] + [f"- {p}" for p in core] + [""]
    if gotchas:
        body += ["## 踩坑"] + [f"- {p}" for p in gotchas] + [""]
    body += ["## 原始对话", ""]
    for m in get_messages(sid):
        role = "🧑 用户" if m["role"] == "user" else "🤖 oiagent"
        body += [f"**{role}**", "", m["content"], ""]
    return "\n".join(fm_lines) + "\n\n" + "\n".join(body)


def _fallback_experience_doc(sid: str) -> str:
    """提炼失败兜底:默认 frontmatter + 原始对话(不丢数据,提炼是增值)。"""
    return _build_experience_doc(sid, {"title": None, "tldr": [], "core": [],
                                       "gotchas": [], "tags": [], "project": ""})


def _distill_experience(sid: str) -> dict:
    """调当前会话模型提炼对话成结构化经验。失败返回 {}(调用方走兜底)。

    复用 _run_chat_thread 的模型解析(router 优先),think_level 强制 low
    (提炼不需要高思考,省 token)。use_tools=False(纯文本提炼)。
    """
    history = get_messages(sid)
    if not history:
        return {}
    conv = "\n\n".join(
        f"{'用户' if m['role'] == 'user' else 'oiagent'}: {m['content']}"
        for m in history)
    prompt = _EXPERIENCE_PROMPT % conv[:12000]  # 截断防爆 context

    msgs = [{"role": "user", "content": prompt}]
    try:
        if _router.available_platforms():
            pick = _router.route(msgs, DEFAULT_STRATEGY)
            lm = _litellm_model_for(pick["platform"], pick["cfg"], pick["task_type"])
        else:
            lm = DEFAULT_MODEL
        res = run_conversation(msgs, lm, _WORKDIR["path"],
                               think_level="low", use_tools=False)
        text = res["out"].strip()
        # 剥 markdown 代码围栏(模型可能包裹 ```json ... ```)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {}
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_experience_to_obsidian(sid: str) -> dict:
    """提炼 + 落 vault。返回 {ok, filepath?, title?, distilled?, error?}。"""
    try:
        OBSIDIAN_EXPERIENCES_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"vault 目录不可写: {e}"}

    distilled = _distill_experience(sid)
    used_fallback = not distilled
    doc = _build_experience_doc(sid, distilled) if distilled else _fallback_experience_doc(sid)

    from datetime import datetime
    sess = get_session(sid)
    title = (distilled.get("title") if distilled else None) or (sess[1] if sess else "会话") or "经验"
    # 命名 YYYY-MM-DD-<slug>.md,slug 取标题去非法字符
    slug = re.sub(r'[\\/:*?"<>|]', "", title)[:40].strip() or "经验"
    slug = re.sub(r"\s+", "-", slug)
    filename = f"{datetime.now().strftime('%Y-%m-%d')}-{slug}.md"
    filepath = OBSIDIAN_EXPERIENCES_DIR / filename
    if filepath.exists():  # 重名追加时分秒
        filepath = OBSIDIAN_EXPERIENCES_DIR / (
            f"{datetime.now().strftime('%Y-%m-%d')}-{slug}-"
            f"{datetime.now().strftime('%H%M%S')}.md")
    try:
        filepath.write_text(doc, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"写入失败: {e}"}
    return {"ok": True, "filepath": str(filepath), "title": title,
            "distilled": not used_fallback}


# ============================================================
# 页面
# ============================================================
_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>oiagent · Prisir AI</title>
<style>
  :root {
    --gh-paper:#f6f1e7; --gh-paper-2:#efe8da; --gh-paper-3:#e7dfce; --gh-surface:#fbf8f1;
    --gh-ink:#2f3a34; --gh-ink-soft:#5b6a61; --gh-ink-faint:#8a968e; --gh-line:#d8cfbc;
    --gh-green:#6c7c72; --gh-green-deep:#4a5c52; --gh-seal:#b23a30;
    --gh-user-bg:#b23a30; --gh-user-fg:#fbf6ec; --gh-agent-bg:#fbf8f1; --gh-focus:#4a5c52;
    --gh-radius:10px; --gh-radius-lg:14px; --gh-shadow:0 1px 3px rgba(74,92,82,.12);
    --gh-font:'Segoe UI','Microsoft YaHei',system-ui,sans-serif;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  html,body { height:100%; }
  body { font-family:var(--gh-font); color:var(--gh-ink);
    background:var(--gh-paper) url('/oiagent/assets/guohua_bg_wide.png') center bottom/cover fixed no-repeat;
    display:flex; flex-direction:column; height:100vh; }

  #topbar { display:flex; align-items:center; gap:12px; padding:10px 18px;
    background:rgba(246,241,231,.85); backdrop-filter:blur(6px); border-bottom:1px solid var(--gh-line); }
  #brand { display:flex; align-items:center; gap:10px; }
  #brand img { width:26px; height:26px; border-radius:6px; box-shadow:var(--gh-shadow); }
  #brand .name { font-size:16px; font-weight:600; color:var(--gh-green-deep); }
  #topbar .spacer { flex:1; }
  .topbtn { padding:6px 12px; border-radius:8px; border:1px solid var(--gh-line);
    background:var(--gh-surface); color:var(--gh-green-deep); font-size:13px; cursor:pointer; }
  .topbtn:hover { border-color:var(--gh-green-deep); }
  #strategy-label { font-size:12px; color:var(--gh-ink-faint); }

  #main { flex:1; display:flex; min-height:0; }
  #rail { width:250px; border-right:1px solid var(--gh-line); background:rgba(239,232,218,.5);
    padding:12px; overflow-y:auto; display:flex; flex-direction:column; gap:4px; }
  #rail h2 { font-size:12px; color:var(--gh-ink-faint); text-transform:uppercase; letter-spacing:1px; margin:4px 2px 8px; }
  .sess { padding:8px 10px; border-radius:8px; font-size:13px; color:var(--gh-ink-soft);
    cursor:pointer; border:1px solid transparent; display:flex; align-items:center; gap:6px; }
  .sess:hover { background:var(--gh-surface); }
  .sess.active { background:var(--gh-surface); border-color:var(--gh-line); color:var(--gh-ink); box-shadow:var(--gh-shadow); }
  .sess .t { flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .sess .pin { color:var(--gh-seal); font-size:12px; }

  #conv { flex:1; display:flex; flex-direction:column; min-width:0; }
  #conv-head { display:flex; align-items:center; gap:10px; padding:10px 28px; border-bottom:1px solid var(--gh-line); }
  #conv-title { font-size:15px; font-weight:600; flex:1; }
  /* Perplexity ⋯ 菜单 */
  #menu-wrap { position:relative; }
  #menu-btn { width:30px; height:30px; border-radius:50%; border:1px solid var(--gh-line);
    background:var(--gh-surface); cursor:pointer; font-size:16px; color:var(--gh-ink-soft); }
  #menu { position:absolute; right:0; top:36px; background:var(--gh-surface); border:1px solid var(--gh-line);
    border-radius:10px; box-shadow:0 8px 24px rgba(74,92,82,.18); min-width:190px; z-index:50; display:none; overflow:hidden; }
  #menu.open { display:block; }
  #menu .mi { padding:10px 14px; font-size:13px; cursor:pointer; display:flex; gap:10px; align-items:center; }
  #menu .mi:hover { background:var(--gh-paper-2); }
  #menu .mi.danger { color:var(--gh-seal); }
  #menu .divider { height:1px; background:var(--gh-line); }

  #messages { flex:1; overflow-y:auto; padding:24px 28px; display:flex; flex-direction:column; gap:14px; }
  .msg { max-width:78%; padding:12px 16px; border-radius:var(--gh-radius-lg);
    white-space:pre-wrap; word-break:break-word; line-height:1.6; font-size:14.5px; box-shadow:var(--gh-shadow); }
  .msg.user { align-self:flex-end; background:var(--gh-user-bg); color:var(--gh-user-fg); border-bottom-right-radius:4px; }
  .msg.agent { align-self:flex-start; background:var(--gh-agent-bg); border:1px solid var(--gh-line); border-bottom-left-radius:4px; }
  .msg .meta { font-size:11px; color:var(--gh-ink-faint); margin-top:8px; }
  .msg.user .meta { color:rgba(251,246,236,.75); }

  /* 延续话题(Perplexity) */
  .followups { align-self:flex-start; max-width:78%; display:flex; flex-direction:column; gap:6px; margin-top:-6px; }
  .followups .fu-title { font-size:11px; color:var(--gh-ink-faint); margin-bottom:2px; }
  .fu { padding:8px 12px; background:var(--gh-surface); border:1px solid var(--gh-line); border-radius:8px;
    font-size:13px; cursor:pointer; transition:all .15s; }
  .fu:hover { background:var(--gh-paper-2); border-color:var(--gh-focus); }

  #composer { padding:16px 28px 20px; }
  #composer .box { display:flex; gap:10px; align-items:flex-start; background:var(--gh-surface);
    border:1px solid var(--gh-line); border-radius:var(--gh-radius-lg); padding:10px 12px; box-shadow:var(--gh-shadow); }
  #composer .box:focus-within { border-color:var(--gh-focus); }
  #input { flex:1; border:none; outline:none; resize:none; background:transparent;
    color:var(--gh-ink); font-size:14.5px; font-family:var(--gh-font); line-height:1.5;
    max-height:160px; min-height:44px; }
  #send { padding:9px 18px; border-radius:9px; border:none; background:var(--gh-green-deep);
    color:#fbf6ec; font-size:14px; cursor:pointer; }
  #send:hover { background:var(--gh-green); }
  #send:disabled { background:var(--gh-paper-3); color:var(--gh-ink-faint); cursor:not-allowed; }
  .composer-bar { display:flex; flex-direction:column; gap:6px; align-items:stretch; padding-top:2px; }
  #think-level { padding:6px 8px; border-radius:8px; border:1px solid var(--gh-line);
    background:var(--gh-surface); color:var(--gh-ink); font-size:12px; cursor:pointer; }
  #attach-btn { padding:6px 10px; border-radius:8px; border:1px solid var(--gh-line);
    background:var(--gh-surface); color:var(--gh-ink); font-size:14px; cursor:pointer; }
  #attach-btn:hover { border-color:var(--gh-green-deep); }
  #attach-row { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; }
  .atchip { display:inline-flex; align-items:center; gap:6px; padding:4px 8px; font-size:12px;
    background:var(--gh-paper-2); border:1px solid var(--gh-line); border-radius:999px; color:var(--gh-ink); }
  .atchip button { border:none; background:none; color:var(--gh-seal); cursor:pointer; font-size:13px; padding:0; }
  #status { padding:0 28px 8px; font-size:12px; color:var(--gh-ink-soft); min-height:18px; }
  .spinner { display:inline-block; width:13px; height:13px; border:2px solid var(--gh-paper-3);
    border-top-color:var(--gh-green-deep); border-radius:50%; animation:spin .8s linear infinite;
    vertical-align:middle; margin-right:6px; }
  @keyframes spin { to { transform:rotate(360deg); } }

  /* key 配置弹层 */
  #keymodal { position:fixed; inset:0; background:rgba(47,58,52,.4); display:none; z-index:100;
    align-items:center; justify-content:center; }
  #keymodal.open { display:flex; }
  #keymodal .card { background:var(--gh-paper); border-radius:14px; padding:24px; width:520px; max-width:92vw;
    max-height:86vh; overflow-y:auto; box-shadow:0 12px 40px rgba(0,0,0,.25); }
  #keymodal h3 { font-size:16px; color:var(--gh-green-deep); margin-bottom:4px; }
  #keymodal .sub { font-size:12px; color:var(--gh-ink-faint); margin-bottom:16px; }
  .kf { margin-bottom:14px; }
  .kf label { font-size:13px; font-weight:600; display:block; margin-bottom:4px; }
  .kf .hint { font-size:11px; color:var(--gh-ink-faint); margin-bottom:6px; }
  .kf input { width:100%; padding:9px 12px; border:1px solid var(--gh-line); border-radius:8px;
    font-size:13px; font-family:monospace; background:var(--gh-surface); color:var(--gh-ink); }
  .kf input:focus { outline:none; border-color:var(--gh-focus); }
  #keymodal .row { display:flex; gap:10px; justify-content:flex-end; margin-top:18px; }
  #keylist { margin-top:10px; font-size:12px; }
  #keylist .k { padding:6px 8px; background:var(--gh-paper-2); border-radius:6px; margin-bottom:4px;
    display:flex; justify-content:space-between; }
  #keylist .k button { border:none; background:none; color:var(--gh-seal); cursor:pointer; }

  /* 通用内嵌对话框(Electron sandbox 禁用原生 prompt/confirm) */
  #dlg { position:fixed; inset:0; background:rgba(47,58,52,.4); display:none; z-index:200;
    align-items:center; justify-content:center; }
  #dlg.open { display:flex; }
  #dlg .card { background:var(--gh-paper); border-radius:14px; padding:22px; width:420px; max-width:92vw;
    box-shadow:0 12px 40px rgba(0,0,0,.25); }
  #dlg h3 { font-size:15px; color:var(--gh-green-deep); margin-bottom:4px; }
  #dlg .sub { font-size:12px; color:var(--gh-ink-faint); margin-bottom:14px; }

  @media (max-width:760px){ #rail{display:none} .msg{max-width:92%} }
</style>
</head>
<body>
<div id="topbar">
  <div id="brand">
    <img src="/oiagent/assets/secbrowser_icon_48.png" alt="icon">
    <span class="name">oiagent · Prisir AI</span>
  </div>
  <div class="spacer"></div>
  <span id="strategy-label"></span>
  <button class="topbtn" onclick="openKeys()">🔑 模型 Key</button>
  <button class="topbtn" onclick="newSession()">+ 新会话</button>
</div>
<div id="main">
  <div id="rail">
    <h2>会话</h2>
    <div id="sess-list"></div>
  </div>
  <div id="conv">
    <div id="conv-head">
      <div id="conv-title">新会话</div>
      <div id="menu-wrap">
        <button id="menu-btn" onclick="toggleMenu(event)">⋯</button>
        <div id="menu">
          <div class="mi" onclick="pinSession()">📌 <span id="pin-label">固定的</span></div>
          <div class="mi" onclick="renameSession()">✏️ 重命名会话</div>
          <div class="divider"></div>
          <div class="mi" onclick="exportAs('pdf')">📄 导出为PDF</div>
          <div class="mi" onclick="exportAs('md')">📝 衍生为Markdown</div>
          <div class="mi" onclick="exportAs('docx')">📃 导出为DOCX</div>
          <div class="mi" onclick="saveExperience()">💎 存为经验(Obsidian)</div>
          <div class="divider"></div>
          <div class="mi danger" onclick="deleteSession()">🗑️ 删除</div>
        </div>
      </div>
    </div>
    <div id="messages"></div>
    <div id="status"></div>
    <div id="composer">
      <div id="attach-row"></div>
      <div class="box">
        <textarea id="input" rows="2" placeholder="问点什么… (Enter 发送,Shift+Enter 换行)"></textarea>
        <div class="composer-bar">
          <select id="think-level" title="思考档位:无档位的模型(如 K3)会自动忽略">
            <option value="">思考:默认</option>
            <option value="off">思考:关闭</option>
            <option value="low">思考:低</option>
            <option value="medium">思考:中</option>
            <option value="high">思考:高</option>
          </select>
          <button id="attach-btn" type="button" title="附加文件(文本内联/图片多模态)">📎</button>
          <input id="attach-input" type="file" multiple style="display:none">
          <button id="send" onclick="sendMessage()">发送</button>
        </div>
      </div>
    </div>
  </div>
</div>

<div id="keymodal">
  <div class="card">
    <h3>模型端点</h3>
    <div class="sub">无账号:key 只存本地。任意云端平台(OpenAI/Anthropic/Kimi/MiniMax/Agnes…)
      都按「自定义端点」填,选对协议即可。</div>
    <div class="kf">
      <label>自定义端点</label>
      <div class="hint">协议:openai=OpenAI 兼容(/chat/completions);anthropic=Anthropic Messages(/v1/messages)。<br>
        base_url 填到版本前缀即可,如 https://api.kimi.com/coding/v1、https://api.minimaxi.com/anthropic、
        https://api.anthropic.com、http://127.0.0.1:11434/v1</div>
      <select id="k-custom-proto" style="width:100%;padding:9px 12px;border:1px solid var(--gh-line);border-radius:8px;font-size:13px;background:var(--gh-surface);color:var(--gh-ink);margin-bottom:6px">
        <option value="openai">openai(OpenAI 兼容,多数平台)</option>
        <option value="anthropic">anthropic(Claude / MiniMax anthropic 端点)</option>
      </select>
      <input id="k-custom-url" type="text" placeholder="base_url,如 https://...">
      <input id="k-custom-key" type="password" placeholder="key(本地可空)" style="margin-top:6px">
      <div style="display:flex;gap:6px;margin-top:6px">
        <input id="k-custom-model" type="text" list="k-model-list" placeholder="模型名(可手填或拉取)" style="flex:1">
        <button class="topbtn" type="button" onclick="pullModels()" title="从端点拉取可选模型">拉取</button>
      </div>
      <datalist id="k-model-list"></datalist>
      <div id="k-model-hint" style="font-size:11px;color:var(--gh-ink-faint);margin-top:4px"></div>
    </div>
    <div class="kf">
      <label>工作目录</label>
      <div class="hint">oiagent 读写文件/跑命令的基准目录(影响 read_file/run_shell 相对路径)</div>
      <div style="display:flex;gap:6px">
        <input id="k-workdir" type="text" placeholder="如 C:\path\to\project" style="flex:1">
        <button class="topbtn" type="button" onclick="saveWorkdir()">应用</button>
      </div>
      <div id="k-workdir-hint" style="font-size:11px;color:var(--gh-ink-faint);margin-top:4px"></div>
    </div>
    <div class="row">
      <button class="topbtn" onclick="saveKeys()">保存</button>
      <button class="topbtn" onclick="closeKeys()">关闭</button>
    </div>
    <div id="keylist"></div>
  </div>
</div>

<!-- 通用内嵌对话框:Electron sandbox 渲染进程里 window.prompt/confirm 被禁用,改用 DOM 模态 -->
<div id="dlg">
  <div class="card">
    <h3 id="dlg-title"></h3>
    <div class="sub" id="dlg-sub"></div>
    <input id="dlg-input" type="text" style="display:none;width:100%;padding:9px 12px;border:1px solid var(--gh-line);border-radius:8px;font-size:13px;background:var(--gh-surface);color:var(--gh-ink)">
    <div class="row" style="display:flex;gap:10px;justify-content:flex-end;margin-top:18px">
      <button class="topbtn" id="dlg-ok">确定</button>
      <button class="topbtn" id="dlg-cancel">取消</button>
    </div>
  </div>
</div>

<script>
let sessionId = null;
let sessions = [];
let polling = false;

async function api(path, opts) {
  const r = await fetch('/oiagent/api' + path, opts);
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r;
}

function esc(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

function addMsg(role, text, followups) {
  const box = document.getElementById('messages');
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.textContent = text;
  box.appendChild(d);
  if (followups && followups.length) {
    const fu = document.createElement('div');
    fu.className = 'followups';
    fu.innerHTML = '<div class="fu-title">延续话题</div>';
    followups.forEach(f => {
      const el = document.createElement('div');
      el.className = 'fu';
      el.textContent = f;
      el.onclick = () => { document.getElementById('input').value = f; sendMessage(); };
      fu.appendChild(el);
    });
    box.appendChild(fu);
  }
  box.scrollTop = box.scrollHeight;
}

function setStatus(html){ document.getElementById('status').innerHTML = html; }

async function loadSessions() {
  sessions = await api('/sessions');
  const list = document.getElementById('sess-list');
  list.innerHTML = '';
  sessions.forEach(s => {
    const el = document.createElement('div');
    el.className = 'sess' + (s.id === sessionId ? ' active' : '');
    el.innerHTML = (s.pinned ? '<span class="pin">📌</span>' : '') + '<span class="t">' + esc(s.title) + '</span>';
    el.onclick = () => switchSession(s.id);
    list.appendChild(el);
  });
}

async function switchSession(id) {
  sessionId = id;
  const r = await api('/history?session_id=' + id);
  document.getElementById('messages').innerHTML = '';
  document.getElementById('conv-title').textContent = r.title || '会话';
  document.getElementById('pin-label').textContent = r.pinned ? '取消固定' : '固定的';
  r.messages.forEach(m => addMsg(m.role, m.content, m.followups));
  loadSessions();
}

async function newSession() {
  // 惰性新建:不在此落库,等 sendMessage 首发时才 POST /new,避免删除后残留空"新会话"行。
  sessionId = null;
  document.getElementById('messages').innerHTML = '';
  document.getElementById('conv-title').textContent = '新会话';
  setStatus('');
  document.getElementById('send').disabled = false;
  loadSessions();
}

function toggleMenu(e){ e.stopPropagation(); document.getElementById('menu').classList.toggle('open'); }
document.addEventListener('click', () => document.getElementById('menu').classList.remove('open'));

async function pinSession(){ if(!sessionId) return; await api('/pin', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId})});
  const r = await api('/history?session_id='+sessionId); document.getElementById('pin-label').textContent = r.pinned?'取消固定':'固定的'; loadSessions(); }
/* ---- 通用内嵌对话框:Electron sandbox 渲染进程禁用 window.prompt/confirm ---- */
function openDlg(opts){
  return new Promise(resolve => {
    const dlg = document.getElementById('dlg');
    document.getElementById('dlg-title').textContent = opts.title || '';
    document.getElementById('dlg-sub').textContent = opts.sub || '';
    const inp = document.getElementById('dlg-input');
    inp.style.display = opts.input ? 'block' : 'none';
    inp.value = opts.value || '';
    document.getElementById('dlg-ok').textContent = opts.okText || '确定';
    dlg.classList.add('open');
    if (opts.input) setTimeout(() => inp.focus(), 30);
    const done = (val) => { dlg.classList.remove('open');
      document.getElementById('dlg-ok').onclick = null;
      document.getElementById('dlg-cancel').onclick = null;
      inp.onkeydown = null; resolve(val); };
    document.getElementById('dlg-ok').onclick = () => done(opts.input ? inp.value.trim() : true);
    document.getElementById('dlg-cancel').onclick = () => done(opts.input ? null : false);
    if (opts.input) inp.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); done(inp.value.trim()); } };
  });
}
function dlgPrompt(title, value){ return openDlg({title:title, input:true, value:value, okText:'保存'}); }
function dlgConfirm(title, sub){ return openDlg({title:title, sub:sub, okText:'删除'}); }

async function renameSession(){ if(!sessionId) return;
  const t = await dlgPrompt('重命名会话', document.getElementById('conv-title').textContent);
  if(!t) return;
  await api('/rename', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId,title:t})});
  document.getElementById('conv-title').textContent = t; loadSessions(); }
async function deleteSession(){ if(!sessionId) return;
  const yes = await dlgConfirm('删除此会话?','「'+document.getElementById('conv-title').textContent+'」将不可恢复。');
  if(!yes) return;
  await api('/delete', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sessionId})}); newSession(); }

function exportAs(fmt){
  if(!sessionId){alert('先开始一个会话');return;}
  const url='/oiagent/api/export?session_id='+sessionId+'&fmt='+fmt;
  if(fmt==='pdf'){
    // PDF 走打印友好页,需可见窗口供用户另存;新tab保留
    window.open(url,'_blank'); return;
  }
  // md/docx 是 attachment 下载:用隐藏 <a download> 同源点击,
  // 不开 _blank 新窗 → 修掉「下载后残留空白窗」的 bug。
  const a=document.createElement('a');
  a.href=url; a.download=''; document.body.appendChild(a);
  a.click(); a.remove();
}

// ---- 经验提炼存 Obsidian(路线 B) ----
function toast(msg, ok=true){
  let t=document.getElementById('exp-toast');
  if(!t){
    t=document.createElement('div'); t.id='exp-toast';
    t.style.cssText='position:fixed;bottom:28px;left:50%;transform:translateX(-50%);'
      +'padding:10px 18px;border-radius:10px;font-size:13px;z-index:9999;max-width:70vw;'
      +'box-shadow:0 4px 16px rgba(0,0,0,.18);transition:opacity .3s;word-break:break-all;';
    document.body.appendChild(t);
  }
  t.style.background= ok ? '#2f3a34' : '#b23a30';
  t.style.color='#fbf6ec';
  t.textContent=msg; t.style.opacity='1';
  clearTimeout(t._h);
  t._h=setTimeout(()=>{ t.style.opacity='0'; }, 4200);
}

async function saveExperience(){
  if(!sessionId){alert('先开始一个会话');return;}
  document.getElementById('menu').classList.remove('open');
  toast('💎 正在提炼经验并存入 Obsidian …(用当前模型)');
  try{
    const r = await api('/experience', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({session_id: sessionId})
    });
    if(r && r.ok){
      const note = r.distilled ? '' : '(提炼失败,已存原始对话)';
      toast('✅ 已存 Obsidian: ' + (r.title||'') + ' ' + note);
    } else {
      toast('❌ 存经验失败: ' + ((r&&r.error)||'未知错误'), false);
    }
  }catch(e){
    toast('❌ 存经验异常: ' + e.message, false);
  }
}

async function sendMessage() {
  const input = document.getElementById('input');
  const btn = document.getElementById('send');
  const text = input.value.trim();
  const atts = _attachments.slice();
  if (!text && !atts.length) return;
  if (!sessionId) { const r = await api('/new',{method:'POST'}); sessionId = r.session_id; }
  input.value = '';
  btn.disabled = true;
  addMsg('user', text + (atts.length ? ' ' + atts.map(a=>'[附件:'+a.name+']').join(' ') : ''));
  _attachments = []; renderAttach();
  setStatus('<span class="spinner"></span>思考中…');
  const thinkLevel = (document.getElementById('think-level')||{}).value || '';
  await api('/chat', {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({message:text, session_id:sessionId, think_level:thinkLevel, attachments:atts})});
  if (!polling) pollResult();
}

async function pollResult() {
  polling = true;
  while (sessionId) {
    await new Promise(r => setTimeout(r, 900));
    const r = await api('/status?session_id=' + sessionId);
    if (!r.running) {
      const h = await api('/history?session_id=' + sessionId);
      document.getElementById('messages').innerHTML = '';
      document.getElementById('conv-title').textContent = h.title || '会话';
      h.messages.forEach(m => addMsg(m.role, m.content, m.followups));
      setStatus('');
      document.getElementById('send').disabled = false;
      loadSessions();
      break;
    }
  }
  polling = false;
}

function openKeys(){ document.getElementById('keymodal').classList.add('open'); renderKeys(); loadWorkdir(); }
function closeKeys(){ document.getElementById('keymodal').classList.remove('open'); }
async function loadWorkdir(){
  const r = await api('/info');
  document.getElementById('k-workdir').value = r.workdir || '';
}
async function saveWorkdir(){
  const hint = document.getElementById('k-workdir-hint');
  const wd = document.getElementById('k-workdir').value.trim();
  if(!wd){ hint.textContent = '工作目录不能为空'; return; }
  const r = await api('/workdir', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workdir:wd})});
  if(r.ok){ hint.textContent = '已应用:' + r.workdir; }
  else { hint.textContent = r.error || '设置失败'; }
}

/* ---- 附件:文本内联 / 图片多模态 ---- */
let _attachments = [];
const _IMG_EXT = ['.png','.jpg','.jpeg','.gif','.webp','.bmp'];
document.getElementById('attach-btn').addEventListener('click', () => document.getElementById('attach-input').click());
document.getElementById('attach-input').addEventListener('change', async (e) => {
  const files = Array.from(e.target.files || []);
  for (const f of files) {
    const ext = ('.' + (f.name.split('.').pop() || '')).toLowerCase();
    const isImg = _IMG_EXT.includes(ext) || (f.type || '').startsWith('image/');
    const b64 = await new Promise((res) => {
      const rd = new FileReader();
      rd.onload = () => res(String(rd.result).split(',')[1] || '');
      rd.readAsDataURL(f);
    });
    _attachments.push({ name: f.name, mime: f.type || (isImg ? 'image/png' : 'text/plain'), data_base64: b64 });
  }
  e.target.value = '';
  renderAttach();
});
function renderAttach(){
  const row = document.getElementById('attach-row');
  row.innerHTML = '';
  _attachments.forEach((a, i) => {
    const chip = document.createElement('span'); chip.className = 'atchip';
    chip.innerHTML = '📎 ' + esc(a.name) + ' <button type="button" title="移除">×</button>';
    chip.querySelector('button').onclick = () => { _attachments.splice(i, 1); renderAttach(); };
    row.appendChild(chip);
  });
}
async function pullModels(){
  const hint = document.getElementById('k-model-hint');
  const url = document.getElementById('k-custom-url').value.trim();
  const key = document.getElementById('k-custom-key').value.trim();
  if(!url){ hint.textContent = '先填 base_url 再拉取'; return; }
  hint.textContent = '拉取中…';
  try {
    const r = await api('/models?base_url='+encodeURIComponent(url)+'&api_key='+encodeURIComponent(key));
    const dl = document.getElementById('k-model-list');
    dl.innerHTML = '';
    if(r.ok && r.models && r.models.length){
      r.models.forEach(m => { const o=document.createElement('option'); o.value=m; dl.appendChild(o); });
      hint.textContent = '拉到 '+r.models.length+' 个模型,点模型名输入框下拉选择';
      if(r.models.length && !document.getElementById('k-custom-model').value)
        document.getElementById('k-custom-model').value = r.models[0];
    } else {
      hint.textContent = '未拉到('+(r.error||'空')+'),可继续手填模型名';
    }
  } catch(e){ hint.textContent = '拉取失败:'+e; }
}
async function saveKeys(){
  const body = {
    custom_proto: document.getElementById('k-custom-proto').value,
    custom_url: document.getElementById('k-custom-url').value.trim(),
    custom_key: document.getElementById('k-custom-key').value.trim(),
    custom_model: document.getElementById('k-custom-model').value.trim(),
  };
  await api('/keys', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  renderKeys();
}
async function renderKeys(){
  const ks = await api('/keys');
  const el = document.getElementById('keylist');
  el.innerHTML = ks.length ? '<div class="sub" style="margin:8px 0 4px">已配置:</div>' : '';
  ks.forEach(k => {
    const d = document.createElement('div'); d.className='k';
    d.innerHTML = '<span>'+esc(k.platform)+' '+esc(k.key_hint)+'</span><button onclick="delKey(\''+k.platform+'\')">删除</button>';
    el.appendChild(d);
  });
}
async function delKey(p){ await api('/keys/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platform:p})}); renderKeys(); }

document.getElementById('input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

(async () => {
  const r = await api('/info');
  document.getElementById('strategy-label').textContent = '路由: ' + r.strategy + (r.platforms.length ? ' · ' + r.platforms.join('/') : ' · 未配key');
  await loadSessions();
  if (sessions.length) switchSession(sessions[0].id);
})();
</script>
</body>
</html>
"""


# ============================================================
# HTTP 处理
# ============================================================
def _content_disposition(filename: str) -> str:
    """构造 RFC5987 双格式 Content-Disposition 值。

    BaseHTTPRequestHandler.send_header 用 latin-1 严格编码,直接塞中文文件名会
    UnicodeEncodeError 崩掉整个响应(导出挂起/空回复/浏览器拿不到文件名 →
    回退 URL 末段 'export',Windows 下甚至落成 .lnk 快捷方式而不是 .md)。
    正确做法:ASCII 兜底名(给老客户端)+ filename*=UTF-8''<percent-encoded>
    (现代浏览器优先采用,支持中文)。
    """
    # ASCII 兜底:非 ASCII 字符替换为 _,压掉引号/反斜杠/分号防头注入
    fallback = re.sub(r'[^\x20-\x7e]', "_", filename)
    fallback = fallback.replace("\\", "_").replace('"', "_").replace(";", "_").strip() or "download"
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: N802
        pass

    def _json(self, data, code: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, s: str, code: int = 200, filename: str | None = None):
        body = s.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if filename:
            self.send_header("Content-Disposition", _content_disposition(filename))
        self.end_headers()
        self.wfile.write(body)

    def _download(self, data: bytes, mime: str, filename: str):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", _content_disposition(filename))
        self.end_headers()
        self.wfile.write(data)

    def _asset(self, name: str):
        safe = os.path.basename(name)
        p = Path(__file__).resolve().parent / "assets" / safe
        if not p.is_file():
            self._json({"error": "not found"}, 404)
            return
        mime = ("image/png" if safe.endswith(".png") else
                "image/x-icon" if safe.endswith(".ico") else
                "text/css" if safe.endswith(".css") else "application/octet-stream")
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ---------------- GET ----------------
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        if path in ("/", "/index.html"):
            self._html(_PAGE)
        elif path.startswith("/oiagent/assets/"):
            self._asset(path[len("/oiagent/assets/"):])
        elif path == "/oiagent/api/info":
            self._json({"strategy": DEFAULT_STRATEGY, "workdir": _WORKDIR["path"],
                        "platforms": _router.available_platforms()})
        elif path == "/oiagent/api/sessions":
            self._json(list_sessions())
        elif path == "/oiagent/api/history":
            sid = (qs.get("session_id") or [""])[0]
            sess = get_session(sid)
            if not sess:
                self._json({"error": "not found"}, 404)
                return
            self._json({"id": sid, "title": sess[1], "pinned": bool(sess[2]),
                        "messages": get_messages(sid)})
        elif path == "/oiagent/api/status":
            sid = (qs.get("session_id") or [""])[0]
            with _running_lock:
                running = _running.get(sid, False)
            self._json({"running": running, "meta": _get_meta(sid)})
        elif path == "/oiagent/api/keys":
            self._json(_key_store.list_platforms())
        elif path == "/oiagent/api/models":
            # 拉取端点模型列表:优先用查询参数里的 base_url/key(未保存时),
            # 否则用已存的 custom 端点。只回模型名,不回显完整 key。
            q_base = (qs.get("base_url") or [""])[0].strip()
            q_key = (qs.get("api_key") or [""])[0].strip()
            if q_base:
                base, key = q_base, q_key
            else:
                rec = _key_store.get_key("custom") or {}
                base, key = rec.get("base_url", ""), rec.get("api_key", "")
            self._json(list_endpoint_models(base, key))
        elif path == "/oiagent/api/export":
            self._handle_export(qs)
        else:
            self._json({"error": "not found"}, 404)

    def _handle_export(self, qs):
        sid = (qs.get("session_id") or [""])[0]
        fmt = (qs.get("fmt") or ["md"])[0]
        sess = get_session(sid)
        if not sess:
            self._json({"error": "not found"}, 404)
            return
        # 复用视频笔记命名逻辑(video-study.js:223):
        # 取标题,替换非法字符 [\\/:*?"<>|] → _,截断 60 字符
        title = (sess[1] or "会话").strip()
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)[:60]
        if not safe_title:
            safe_title = "oiagent对话"
        base = safe_title
        if fmt == "md":
            self._download(_export_markdown(sid).encode("utf-8"), "text/markdown; charset=utf-8", f"{base}.md")
        elif fmt == "pdf":
            # 打印友好 HTML → 浏览器另存 PDF(无 reportlab 依赖的稳妥路径)
            self._html(_export_html_for_pdf(sid))
        elif fmt == "docx":
            data = _export_docx(sid)
            if data:
                self._download(data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", f"{base}.docx")
            else:
                self._download(_export_word_html(sid).encode("utf-8"), "application/msword", f"{base}.doc")
        else:
            self._json({"error": "unknown fmt"}, 400)

    # ---------------- POST ----------------
    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/oiagent/api/new":
            self._json({"session_id": create_session()})
        elif path == "/oiagent/api/chat":
            self._handle_chat(body)
        elif path == "/oiagent/api/rename":
            rename_session(body.get("session_id", ""), body.get("title", "")[:60])
            self._json({"ok": True})
        elif path == "/oiagent/api/pin":
            sid = body.get("session_id", "")
            sess = get_session(sid)
            if sess:
                pin_session(sid, not bool(sess[2]))
            self._json({"ok": True})
        elif path == "/oiagent/api/delete":
            delete_session(body.get("session_id", ""))
            self._json({"ok": True})
        elif path == "/oiagent/api/keys":
            self._handle_save_keys(body)
        elif path == "/oiagent/api/keys/delete":
            _key_store.delete_key(body.get("platform", ""))
            self._json({"ok": True})
        elif path == "/oiagent/api/workdir":
            wd = (body.get("workdir") or "").strip()
            if not wd:
                self._json({"ok": False, "error": "empty workdir"}, 400)
                return
            p = os.path.abspath(os.path.expanduser(wd))
            if not os.path.isdir(p):
                self._json({"ok": False, "error": f"目录不存在: {p}"}, 400)
                return
            _WORKDIR["path"] = p
            self._json({"ok": True, "workdir": p})
        elif path == "/oiagent/api/experience":
            # 经验提炼存 Obsidian(路线 B)。同步 LLM 调用,前端已置 loading。
            sid = body.get("session_id", "")
            if not get_session(sid):
                self._json({"ok": False, "error": "会话不存在"}, 404)
                return
            self._json(_save_experience_to_obsidian(sid))
        else:
            self._json({"error": "not found"}, 404)

    def _handle_chat(self, body: dict):
        message = (body.get("message") or "").strip()
        attachments = body.get("attachments") or []
        sid = body.get("session_id") or ""
        if not message and not attachments:
            self._json({"error": "empty message"}, 400)
            return
        if not get_session(sid):
            sid = create_session()
        with _running_lock:
            if _running.get(sid):
                self._json({"error": "already running", "session_id": sid}, 409)
                return
            _running[sid] = True
        # 落库的是用户可见文本 + 附件名标注(附件本体不存库,避免膨胀)
        att_note = (" " + " ".join(f"[附件:{a.get('name','file')}]" for a in attachments
                                   if isinstance(a, dict))) if attachments else ""
        add_message(sid, "user", message + att_note)
        strategy = body.get("strategy", DEFAULT_STRATEGY)
        think_level = (body.get("think_level") or "").strip().lower()
        t = threading.Thread(target=_run_chat_thread,
                             args=(sid, message, strategy, DEFAULT_MODEL, _WORKDIR["path"],
                                   think_level, attachments),
                             daemon=True)
        t.start()
        self._json({"session_id": sid, "status": "running"})

    def _handle_save_keys(self, body: dict):
        # 统一只收「自定义端点」。任意平台(OpenAI/Anthropic/Kimi/MiniMax/Agnes…)
        # 都是 base_url+key+model+协议,不再单列 openai/anthropic 两个字段。
        proto = (body.get("custom_proto") or "openai").strip().lower()
        if proto not in ("openai", "anthropic"):
            proto = "openai"
        if body.get("custom_url"):
            _key_store.set_key("custom", body.get("custom_key", "") or "sk-local",
                               base_url=body["custom_url"], model=body.get("custom_model", ""),
                               meta={"proto": proto})
        self._json({"ok": True, "platforms": _router.available_platforms()})


def main():
    global DEFAULT_MODEL, DEFAULT_WORKDIR, DEFAULT_STRATEGY
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=WEB_PORT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--workdir", default=DEFAULT_WORKDIR)
    ap.add_argument("--strategy", default=DEFAULT_STRATEGY)
    args = ap.parse_args()
    DEFAULT_MODEL, DEFAULT_WORKDIR, DEFAULT_STRATEGY = args.model, args.workdir, args.strategy

    srv = ThreadingHTTPServer((WEB_HOST, args.port), Handler)
    print(f"oiagent 对话模式: http://{WEB_HOST}:{args.port}  路由={DEFAULT_STRATEGY}  数据={_CHAT_DB}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
