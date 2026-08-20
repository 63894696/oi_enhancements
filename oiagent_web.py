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
import html
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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

_DB_DIR = Path(os.environ.get("PRISIR_DATA", str(Path.home() / ".local" / "share" / "prisir")))
_DB_DIR.mkdir(parents=True, exist_ok=True)
_CHAT_DB = _DB_DIR / "chats.db"

_key_store = PrisirKeyStore()
_router = PrisirRouter(_key_store)

# 运行中会话的内存锁/状态(结果落 SQLite,运行状态在内存)
_running: dict[str, bool] = {}
_running_lock = threading.Lock()


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
                     think_level: str = ""):
    try:
        history = get_messages(sid)
        msgs = [{"role": m["role"], "content": m["content"]} for m in history]

        use_router = bool(_router.available_platforms())
        if use_router:
            # Prisir 路由: 用 router 选定平台后,把该平台模型映射到 litellm model 串
            pick = _router.route(msgs + [{"role": "user", "content": user_text}], strategy)
            platform, cfg = pick["platform"], pick["cfg"]
            lm = _litellm_model_for(platform, cfg, pick["task_type"])
            res = run_conversation(msgs + [{"role": "user", "content": user_text}], lm, workdir,
                                   think_level=think_level)
            answer = res["out"]
            used = f"{platform}:{cfg['model']}"
        else:
            res = run_conversation(msgs + [{"role": "user", "content": user_text}], model, workdir,
                                   think_level=think_level)
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
  #composer .box { display:flex; gap:10px; align-items:flex-end; background:var(--gh-surface);
    border:1px solid var(--gh-line); border-radius:var(--gh-radius-lg); padding:10px 12px; box-shadow:var(--gh-shadow); }
  #composer .box:focus-within { border-color:var(--gh-focus); }
  #input { flex:1; border:none; outline:none; resize:none; background:transparent;
    color:var(--gh-ink); font-size:14.5px; font-family:var(--gh-font); line-height:1.5; max-height:160px; }
  #send { padding:9px 18px; border-radius:9px; border:none; background:var(--gh-green-deep);
    color:#fbf6ec; font-size:14px; cursor:pointer; }
  #send:hover { background:var(--gh-green); }
  #send:disabled { background:var(--gh-paper-3); color:var(--gh-ink-faint); cursor:not-allowed; }
  .composer-bar { display:flex; flex-direction:column; gap:6px; align-items:stretch; }
  #think-level { padding:6px 8px; border-radius:8px; border:1px solid var(--gh-line);
    background:var(--gh-surface); color:var(--gh-ink); font-size:12px; cursor:pointer; }
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
          <div class="divider"></div>
          <div class="mi danger" onclick="deleteSession()">🗑️ 删除</div>
        </div>
      </div>
    </div>
    <div id="messages"></div>
    <div id="status"></div>
    <div id="composer">
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

function exportAs(fmt){ if(!sessionId){alert('先开始一个会话');return;} window.open('/oiagent/api/export?session_id='+sessionId+'&fmt='+fmt, '_blank'); }

async function sendMessage() {
  const input = document.getElementById('input');
  const btn = document.getElementById('send');
  const text = input.value.trim();
  if (!text) return;
  if (!sessionId) { const r = await api('/new',{method:'POST'}); sessionId = r.session_id; }
  input.value = '';
  btn.disabled = true;
  addMsg('user', text);
  setStatus('<span class="spinner"></span>思考中…');
  const thinkLevel = (document.getElementById('think-level')||{}).value || '';
  await api('/chat', {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({message:text, session_id:sessionId, think_level:thinkLevel})});
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

function openKeys(){ document.getElementById('keymodal').classList.add('open'); renderKeys(); }
function closeKeys(){ document.getElementById('keymodal').classList.remove('open'); }
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
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def _download(self, data: bytes, mime: str, filename: str):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
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
            self._json({"strategy": DEFAULT_STRATEGY, "workdir": DEFAULT_WORKDIR,
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
        if not get_session(sid):
            self._json({"error": "not found"}, 404)
            return
        base = f"oiagent-{sid}"
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
        else:
            self._json({"error": "not found"}, 404)

    def _handle_chat(self, body: dict):
        message = (body.get("message") or "").strip()
        sid = body.get("session_id") or ""
        if not message:
            self._json({"error": "empty message"}, 400)
            return
        if not get_session(sid):
            sid = create_session()
        with _running_lock:
            if _running.get(sid):
                self._json({"error": "already running", "session_id": sid}, 409)
                return
            _running[sid] = True
        add_message(sid, "user", message)
        strategy = body.get("strategy", DEFAULT_STRATEGY)
        think_level = (body.get("think_level") or "").strip().lower()
        t = threading.Thread(target=_run_chat_thread,
                             args=(sid, message, strategy, DEFAULT_MODEL, DEFAULT_WORKDIR, think_level),
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
