"""l4_web.py — Agent-First OS L4 反馈层(对话反馈 GUI,阶段 2)

总纲 §2.4:L4 是**唯一面向人的层**,职责是"呈现对话与结果,接收确认/追问/新指令"。
关键原则:**必须有对话反馈 GUI**——用户要能看到 agent 干了什么、结果如何;这是整套
系统对外的唯一界面,也是"信任"的来源。**不做**密集菜单/按钮/表单——那些是被取代的东西。

本模块是一个 stdlib-only 的 Web 对话窗:
  - 单页对话界面(嵌入式 HTML/CSS/JS,无构建步骤)。
  - 后端只做**薄代理**:把用户意图转发给 L3 daemon(aureon-oiagent, action=ask),
    把 daemon 的工具执行结果渲染成结构化反馈。
  - L4↔L3 契约(§3.1):{做了什么(tool trace), 结果(answer), 是否需要确认, 可追问的选项}。
  - 副作用工具的审批在 L3 已完成(policy_check_daemon);L4 依据工具结果里的
    `approved` 标记呈现"已审批"提示——不重复实现审批,只反馈。

阶段 2 范围(用户已定):复用 daemon、本地回环(127.0.0.1)。远程/VPS 接入留后续。
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# 上下文窗口估算(P0 本地核心·上下文用量):复用壳端零成本 token 估算 + 窗口表。
# 模块与 localllm 同目录(oi_enhancements),直接 import;失败则用量接口退化(仅消息条数)。
try:
    import oiagent_context as _ctx
except Exception:  # noqa: BLE001
    _ctx = None

# L3 daemon(阶段 0/1 复用)
DAEMON_URL = "http://127.0.0.1:18791/"
# L4 自身监听(默认本地回环;远程接入经 SSH 隧道,不直接绑公网)
L4_HOST = os.environ.get("L4_HOST", "127.0.0.1")
L4_PORT = 18800
# 当前模型(与 aureon-oiagent DEFAULT_MODEL 对齐,用于上下文窗口估算)。
# daemon 每轮回包也带 model,此处仅作 /api/context_usage 的查询基准。
L4_MODEL = os.environ.get("L4_MODEL", "claude-opus-4-8")

# ── 访问令牌(远程接入安全边界的第一步)────────────────────────────────
# 令牌文件默认在 aureon 数据目录;不存在则首次启动自动生成并打印一次。
# 设 L4_TOKEN 环境变量可覆盖。设 L4_TOKEN="" 显式关闭(仅本地调试用,不推荐)。
_TOKEN_FILE = Path.home() / ".local" / "share" / "aureon" / "l4_token"


def _load_token() -> str:
    env = os.environ.get("L4_TOKEN")
    if env is not None:
        return env
    try:
        if _TOKEN_FILE.exists():
            return _TOKEN_FILE.read_text(encoding="utf-8").strip()
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        tok = secrets.token_urlsafe(24)
        _TOKEN_FILE.write_text(tok, encoding="utf-8")
        try:
            os.chmod(_TOKEN_FILE, 0o600)
        except OSError:
            pass
        return tok
    except Exception:  # noqa: BLE001
        # 文件不可写时退回内存随机令牌(进程级,重启即变)
        return secrets.token_urlsafe(24)


_ACCESS_TOKEN = _load_token()

# 对话历史持久化(jsonl,每行一条 message;重启可恢复)
_HISTORY_DIR = Path.home() / ".local" / "share" / "aureon" / "l4_history"

# per-session 对话历史:{session_id: [ {role, content}, ... ]}
_SESSIONS: dict[str, list[dict[str, Any]]] = {}
_SESSIONS_LOCK = threading.Lock()
_MAX_HISTORY = 40  # 每条 session 保留的 user/assistant 轮数上限


def _history_file(session_id: str) -> Path:
    # session_id 只留安全字符,防路径穿越
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_") or "anon"
    return _HISTORY_DIR / f"{safe}.jsonl"


def _persist(session_id: str, role: str, content: Any) -> None:
    """追加一条到磁盘(jsonl)。失败静默(持久化是增强,不阻断对话)。"""
    try:
        _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        with _history_file(session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"role": role, "content": content}, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _load_persisted(session_id: str) -> list[dict[str, Any]]:
    """从磁盘恢复历史(内存未命中时)。只取最近 _MAX_HISTORY*2 条。"""
    f = _history_file(session_id)
    if not f.exists():
        return []
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
        out = []
        for ln in lines[-_MAX_HISTORY * 2:]:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
        return out
    except Exception:  # noqa: BLE001
        return []


def _get_history(session_id: str) -> list[dict[str, Any]]:
    with _SESSIONS_LOCK:
        if session_id not in _SESSIONS:
            _SESSIONS[session_id] = _load_persisted(session_id)
        return _SESSIONS[session_id]


def _append_history(session_id: str, role: str, content: Any) -> None:
    with _SESSIONS_LOCK:
        hist = _SESSIONS.setdefault(session_id, _load_persisted(session_id))
        hist.append({"role": role, "content": content})
        # 只裁剪最老的,保留最近 _MAX_HISTORY 条
        if len(hist) > _MAX_HISTORY * 2:
            del hist[: len(hist) - _MAX_HISTORY * 2]
    _persist(session_id, role, content)


# ────────────────────────────────────────────────────────────────────── #
# 反馈派生:把 daemon 的 answer/content 转成 L4 结构化反馈
# ────────────────────────────────────────────────────────────────────── #

# ────────────────────────────────────────────────────────────────────── #
# 任务回执(医嘱式):任务在别处发起(IME @Agent / 语音)且结果在键盘收起后才出
# 时,落一条回执;对话壳(前台/启动)拉取渲染成"✅ 已加日程… / ⚠️ 失败…"气泡。
# 纯本地:JSONL 存 l4_receipts/<sid>.jsonl,GET /api/receipts 消费式拉取(读后清空)。
# ────────────────────────────────────────────────────────────────────── #

_RECEIPTS_DIR = Path.home() / ".local" / "share" / "aureon" / "l4_receipts"
_MAX_RECEIPTS = 50


def _receipt_file(session_id: str) -> Path:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_") or "anon"
    return _RECEIPTS_DIR / f"{safe}.jsonl"


def _write_receipt(session_id: str, status: str, text: str,
                   request: str = "", did: list[str] | None = None) -> None:
    """追加一条回执。status: ok | fail | blocked。失败静默(不阻断对话)。"""
    try:
        _RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
        f = _receipt_file(session_id)
        # 裁剪:超上限先读尾保留
        if f.exists():
            lines = f.read_text(encoding="utf-8").splitlines()
            if len(lines) >= _MAX_RECEIPTS:
                f.write_text("\n".join(lines[-(_MAX_RECEIPTS - 1):]) + "\n", encoding="utf-8")
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "status": status,
               "text": text, "request": request[:200], "did": (did or [])[:6]}
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _read_receipts(session_id: str) -> list[dict[str, Any]]:
    """消费式拉取:读出后清空文件(已读不再重投)。"""
    f = _receipt_file(session_id)
    if not f.exists():
        return []
    try:
        out = [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
        f.unlink()  # 消费:删文件
        return out
    except Exception:  # noqa: BLE001
        return []


# ────────────────────────────────────────────────────────────────────── #
# P0 本地核心(2026-08-26):多会话列表 / 上下文用量 / estop-cancel
# 设计:session 管理下沉 l4_web(方案 b),让 adb reverse 离线也能多会话。
#   - 多会话:历史即 <sid>.jsonl 文件,枚举目录即会话列表;切换=对话壳换 session_id。
#   - 上下文用量:复用 oiagent_context 零成本 token 估算 + 窗口表,不依赖 daemon。
#   - estop-cancel:l4_web 是同步等待(无流式事件边界),无法真正中断 daemon 工具链;
#     故做「客户端放弃等待」(cancel),连接标记可关闭,daemon 结果到达即丢弃,UI 立即解锁。
#     真正中断工具链(ollama abort / tool 边界)留待后续(需 daemon 流式接口)。
# ────────────────────────────────────────────────────────────────────── #


def _list_sessions() -> list[dict[str, Any]]:
    """枚举历史文件得会话列表,按最后修改时间倒序(最近活跃在前)。

    title 取该会话首条 user 消息(截 30 字),无则退化为 session_id。
    """
    out: list[dict[str, Any]] = []
    try:
        if not _HISTORY_DIR.exists():
            return out
        files = [f for f in _HISTORY_DIR.glob("*.jsonl") if f.is_file()]
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files:
            sid = f.stem
            title = sid
            n = 0
            try:
                for ln in f.read_text(encoding="utf-8").splitlines():
                    ln = ln.strip()
                    if not ln:
                        continue
                    n += 1
                    if title == sid:
                        try:
                            m = json.loads(ln)
                            if m.get("role") == "user":
                                c = m.get("content")
                                if isinstance(c, list):  # 多模态取 text 块
                                    c = " ".join(str(b.get("text", "")) for b in c if isinstance(b, dict))
                                title = (str(c) or sid)[:30] or sid
                        except Exception:  # noqa: BLE001
                            pass
            except Exception:  # noqa: BLE001
                continue
            out.append({
                "id": sid,
                "title": title,
                "n": n,
                "updated": time.strftime("%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
            })
    except Exception:  # noqa: BLE001
        pass
    return out


def _context_usage(session_id: str) -> dict[str, Any]:
    """当前会话上下文用量(估算)。复用 oiagent_context;缺模块则退化为条数统计。"""
    hist = _get_history(session_id)
    if _ctx is None:
        return {"ok": True, "session_id": session_id, "messages": len(hist),
                "known": False, "advise": "oiagent_context 模块不可用,仅统计条数"}
    u = _ctx.usage_for(hist, L4_MODEL)
    return {
        "ok": True,
        "session_id": session_id,
        "model": L4_MODEL,
        "messages": len(hist),
        "used": u["used"],
        "window": u["window"],
        "ratio": u["ratio"],
        "pct": int(round(u["ratio"] * 100)),
        "near_full": u["near_full"],
        "known": u["known"],
        "advise": u.get("advise"),
    }


# estop-cancel:进行中的 /api/chat 连接注册表 {session_id: conn}(读阶段可被 close)
_CHAT_CONN: dict[str, Any] = {}
_CHAT_CONN_LOCK = threading.Lock()
# 等待集:urlopen 阻塞期(连接未建立)的 session,estop 在此期标记取消
_CHAT_PENDING: set[str] = set()
_CHAT_PENDING_LOCK = threading.Lock()


def _register_conn(session_id: str, conn: Any) -> None:
    with _CHAT_CONN_LOCK:
        _CHAT_CONN[session_id] = conn


def _unregister_conn(session_id: str, conn: Any) -> None:
    with _CHAT_CONN_LOCK:
        if _CHAT_CONN.get(session_id) is conn:
            _CHAT_CONN.pop(session_id, None)


def _cancel_conn(session_id: str) -> bool:
    """关闭该会话进行中的 daemon 连接 → l4_web 读端抛错 → /api/chat 立即返回。

    这是「客户端放弃等待」(cancel),不真正杀 daemon 工具链(见上注释)。
    两阶段:读阶段(连接在注册表)→ 直接 close;等待期(连接未建立)→ 移出等待集标记取消。
    """
    with _CHAT_CONN_LOCK:
        conn = _CHAT_CONN.pop(session_id, None)
    with _CHAT_PENDING_LOCK:
        was_pending = session_id in _CHAT_PENDING
        _CHAT_PENDING.discard(session_id)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return True
    return was_pending


def _derive_feedback(resp: dict[str, Any]) -> dict[str, Any]:
    """从 daemon 的 {answer, content, tool_trace, stop_reason, rounds, model} 提取反馈四要素。

    tool_trace 是 daemon 在本轮 ask 实际执行过的工具 [{name,input,ok,error}] ——
    这是 L4"做了什么"的真实来源(daemon 在 _ask_with_tools 里执行工具后回填)。
    """
    trace = resp.get("tool_trace") or []
    did: list[str] = []
    tool_names: list[str] = []
    for t in trace:
        name = t.get("name", "?")
        tool_names.append(name)
        inp = json.dumps(t.get("input", {}), ensure_ascii=False)[:80]
        mark = "" if t.get("ok") in (True, None) else " ✗"
        did.append(f"{name}({inp}){mark}")
    answer = resp.get("answer", "")
    stop = resp.get("stop_reason", "")

    # 是否需要确认:answer 里含明显"请确认/是否"提示 → 标记(轻量启发,L3 审批已完成)
    needs_confirm = any(k in answer for k in ("请确认", "是否确认", "确认执行", "要不要我", "需要我继续"))

    # 可追问的选项:依据 stop_reason + answer 给 2-3 个自然下一步
    followups: list[str] = []
    if "simplex" in " ".join(tool_names) or "simplex" in answer.lower():
        followups = ["列出我的 SimpleX 联系人", "生成一个关联邀请", "查看服务器状态"]
    elif stop == "max_rounds":
        followups = ["继续", "总结一下你刚才做了什么"]
    else:
        followups = ["继续", "再说明白一点"]

    return {
        "did": did,                    # 做了什么(工具调用轨迹)
        "tool_names": tool_names,
        "result": answer,              # 结果
        "needs_confirm": needs_confirm,  # 是否需要确认
        "followups": followups,        # 可追问的选项
        "stop_reason": stop,
        "rounds": resp.get("rounds"),
        "model": resp.get("model"),
    }


def _stopped_feedback(session_id: str) -> dict[str, Any]:
    """estop-cancel 命中的统一返回:落「已停止」历史 + 结构化反馈。"""
    _append_history(session_id, "assistant", "(已被用户停止)")
    return {"ok": True, "feedback": {
        "did": [], "tool_names": [], "result": "⏹ 已停止(放弃等待后台结果)。",
        "needs_confirm": False, "followups": ["换个说法", "继续"],
        "stop_reason": "cancelled", "rounds": None, "model": None}}


def _ask_daemon(session_id: str, user_text: str) -> dict[str, Any]:
    """把一轮对话发给 L3 daemon(action=ask),返回结构化反馈。"""
    _append_history(session_id, "user", user_text)
    history = _get_history(session_id)
    payload = {
        "action": "ask",
        "messages": [{"role": m["role"], "content": m["content"]} for m in history],
        "max_tool_rounds": 6,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        DAEMON_URL, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    conn = None
    # estop-cancel:urlopen 会阻塞到 daemon 返回首部(可能数十秒,期间连接未建立)。
    # 故先入「等待集」,让 /api/estop 在等待期就能标记取消;连接建立后升级注册表。
    with _CHAT_PENDING_LOCK:
        _CHAT_PENDING.add(session_id)
    try:
        conn = urllib.request.urlopen(req, timeout=180)
        with _CHAT_PENDING_LOCK:
            cancelled_early = session_id not in _CHAT_PENDING  # estop 已在等待期触发
        if cancelled_early:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            return _stopped_feedback(session_id)
        _register_conn(session_id, conn)   # 升级:读阶段可被 estop 直接 close
        with conn as r:
            resp = json.loads(r.read().decode("utf-8"))
        with _CHAT_CONN_LOCK:
            cancelled_read = session_id not in _CHAT_CONN      # estop 在读阶段触发
        if cancelled_read:
            return _stopped_feedback(session_id)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        with _CHAT_CONN_LOCK, _CHAT_PENDING_LOCK:
            was_cancelled = (session_id not in _CHAT_CONN) or (session_id not in _CHAT_PENDING)
        if was_cancelled:
            return _stopped_feedback(session_id)
        _write_receipt(session_id, "fail", f"任务未能执行:连不上引擎({reason})。请确认 daemon 在跑。", user_text)
        return {
            "ok": False,
            "error": f"无法连接 L3 daemon({DAEMON_URL}): {e}",
            "diagnosable": "确认 aureon-oiagent daemon 在跑: GET http://127.0.0.1:18791/health",
        }
    except Exception as e:  # 读端被 close() 中断等
        with _CHAT_CONN_LOCK, _CHAT_PENDING_LOCK:
            was_cancelled = (session_id not in _CHAT_CONN) or (session_id not in _CHAT_PENDING)
        if was_cancelled:
            return _stopped_feedback(session_id)
        _write_receipt(session_id, "fail", f"任务执行中断:{e}", user_text)
        return {"ok": False, "error": f"daemon 读取异常: {e}"}
    finally:
        with _CHAT_PENDING_LOCK:
            _CHAT_PENDING.discard(session_id)
        if conn is not None:
            _unregister_conn(session_id, conn)

    if "error" in resp and "answer" not in resp:
        _write_receipt(session_id, "fail", f"任务未能执行:{resp.get('error')}", user_text)
        return {"ok": False, "error": resp.get("error"), "raw": resp}

    fb = _derive_feedback(resp)
    # 把 assistant 答案落进历史(供下一轮上下文)
    _append_history(session_id, "assistant", fb["result"])

    # 回执:任务发起方常不在前台(IME/语音),结果落队列待对话壳拉取
    status = "blocked" if fb.get("needs_confirm") else ("fail" if fb.get("stop_reason") == "max_rounds" else "ok")
    head = fb["did"][0] if fb["did"] else ""
    # 医嘱式简短:结果截 300 字,超出末尾标注(完整回答在对话页)
    full = fb["result"] or ""
    snippet = full[:300] + ("…(详情见对话)" if len(full) > 300 else "")
    if status == "ok":
        rtext = "✅ 已完成「%s」%s。%s" % (user_text[:40], ("· " + head) if head else "", snippet)
    elif status == "blocked":
        rtext = "⚠️ 需要你确认「%s」。%s" % (user_text[:40], snippet)
    else:
        rtext = "⚠️ 未完成「%s」(步数耗尽)。%s" % (user_text[:40], snippet)
    _write_receipt(session_id, status, rtext, user_text, fb["did"])
    return {"ok": True, "feedback": fb}


# ────────────────────────────────────────────────────────────────────── #
# 嵌入式单页 UI(对话反馈窗)
# ────────────────────────────────────────────────────────────────────── #

_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OIagent · 对话</title>
<style>
  :root { --bg:#eaf5ff; --panel:#ffffff; --bubble-u:#2f9df4; --bubble-a:#ffffff;
          --txt:#1f2937; --dim:#5b6b7c; --accent:#2f9df4; --ok:#1e9e5a; --warn:#c47f2a; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt); font:15px/1.5 -apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif; }
  #wrap { max-width:760px; margin:0 auto; height:100vh; display:flex; flex-direction:column; }
  header { padding:12px 16px; border-bottom:1px solid #b3d7f5; display:flex; align-items:center; gap:10px; }
  header .dot { width:9px; height:9px; border-radius:50%; background:var(--ok); }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  header .st { margin-left:auto; font-size:12px; color:var(--dim); }
  #chat { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:14px; }
  .msg { max-width:85%; padding:10px 13px; border-radius:12px; white-space:pre-wrap; word-break:break-word; }
  .msg.user { align-self:flex-end; background:var(--bubble-u); color:#fff; border:1px solid #1f7fd0; }
  .msg.agent { align-self:flex-start; background:var(--bubble-a); border:1px solid #b3d7f5; }
  .trace { align-self:flex-start; font-size:12px; color:var(--dim); background:#e8f4fd;
           border-left:3px solid var(--accent); padding:7px 10px; border-radius:6px; max-width:85%; }
  .trace b { color:var(--accent); font-weight:600; }
  .confirm { align-self:flex-start; display:flex; gap:8px; }
  .confirm button { background:var(--warn); border:none; color:#111; padding:7px 14px; border-radius:8px; cursor:pointer; font-weight:600; }
  .confirm button.no { background:#dceefb; color:var(--txt); }
  .followups { align-self:flex-start; display:flex; gap:8px; flex-wrap:wrap; }
  .followups button { background:transparent; border:1px solid #b3d7f5; color:var(--dim);
                      padding:6px 12px; border-radius:16px; cursor:pointer; font-size:13px; }
  .followups button:hover { border-color:var(--accent); color:var(--accent); }
  #composer { display:flex; gap:10px; padding:14px 16px; border-top:1px solid #b3d7f5; }
  #in { flex:1; background:var(--panel); border:1px solid #b3d7f5; color:var(--txt);
        border-radius:10px; padding:11px 13px; font-size:15px; outline:none; }
  #in:focus { border-color:var(--accent); }
  #send { background:var(--accent); border:none; color:#fff; padding:0 20px; border-radius:10px; cursor:pointer; font-weight:600; }
  #send:disabled { opacity:.5; cursor:default; }
  .sys { align-self:center; font-size:12px; color:var(--dim); }
  .err { align-self:flex-start; background:#fdeaea; border-left:3px solid #d66; padding:9px 12px; border-radius:6px; max-width:85%; }
  .err .retry { margin-top:6px; background:#c13a3a; border:none; color:#fff; padding:5px 12px; border-radius:6px; cursor:pointer; font-size:13px; }
  .working { align-self:flex-start; font-size:13px; color:var(--dim); display:flex; align-items:center; gap:8px; }
  .working .spin { width:12px; height:12px; border:2px solid #b3d7f5; border-top-color:var(--accent); border-radius:50%; animation:sp 0.8s linear infinite; }
  @keyframes sp { to { transform:rotate(360deg); } }
  /* markdown-ish 渲染 */
  .msg.agent table { border-collapse:collapse; margin:6px 0; font-size:13px; }
  .msg.agent th, .msg.agent td { border:1px solid #b3d7f5; padding:4px 9px; text-align:left; }
  .msg.agent th { background:#e8f4fd; }
  .msg.agent code { background:#e8f4fd; padding:1px 5px; border-radius:4px; font-family:ui-monospace,Consolas,monospace; font-size:13px; }
  .msg.agent pre { background:#e8f4fd; padding:9px 11px; border-radius:8px; overflow-x:auto; }
  .msg.agent pre code { background:none; padding:0; }
  .msg.agent b, .msg.agent strong { color:#12395b; }
</style>
</head>
<body>
<div id="wrap">
  <header>
    <span class="dot" id="livedot"></span>
    <h1>PrisirAI</h1>
    <span class="st" id="status">连接中…</span>
  </header>
  <div id="chat"></div>
  <div id="composer">
    <input id="in" placeholder="说点什么…(例如:列出我的 SimpleX 联系人 / 生成一个关联邀请)" autofocus>
    <button id="send">发送</button>
  </div>
</div>
<script>
const SID = (()=>{ let s = localStorage.getItem('l4_sid'); if(!s){ s = 's_'+Math.random().toString(36).slice(2); localStorage.setItem('l4_sid', s);} return s; })();
/* token:URL ?token= 优先,存 localStorage;之后所有 API 调用带 X-L4-Token header */
const TOKEN = (()=>{ const q = new URLSearchParams(location.search).get('token');
  if(q){ localStorage.setItem('l4_token', q); history.replaceState({},'',location.pathname); return q; }
  return localStorage.getItem('l4_token') || ''; })();
function authHeaders(extra){ const h = Object.assign({}, extra||{}); if(TOKEN) h['X-L4-Token'] = TOKEN; return h; }
const chat = document.getElementById('chat'), inp = document.getElementById('in'), btn = document.getElementById('send');

function el(cls, text){ const d=document.createElement('div'); d.className=cls; if(text!=null) d.textContent=text; return d; }
function scrollEnd(){ chat.scrollTop = chat.scrollHeight; }
function setStatus(t, ok){ document.getElementById('status').textContent = t; document.getElementById('livedot').style.background = ok===false ? '#d66' : 'var(--ok)'; }

async function health(){
  try { const r = await fetch('/api/health'); const j = await r.json();
    setStatus(j.daemon ? '已连接 L3' : 'L3 未连接', j.daemon); } catch(e){ setStatus('L4 异常', false); }
}

function addTrace(did){
  if(!did || !did.length) return;
  const t = el('trace'); t.innerHTML = '<b>做了什么</b>  ' + did.map(x=>escapeHtml(x)).join('<br>');
  chat.appendChild(t);
}
function escapeHtml(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

/* 轻量 markdown 渲染:代码块/行内码/粗体/表格。输入已是 agent 文本(先 escape 再还原标记)。 */
function renderMD(src){
  let s = escapeHtml(src);
  // 代码块 ```...```
  s = s.replace(/```([\s\S]*?)```/g, (m,p)=> '<pre><code>'+p.replace(/^\n+|\n+$/g,'')+'</code></pre>');
  // 行内码 `...`
  s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  // 粗体 **...**
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
  // 表格:连续以 | 开头的行
  const lines = s.split('\n'); let out=[], i=0;
  while(i<lines.length){
    if(/^\s*\|.*\|\s*$/.test(lines[i])){
      let rows=[];
      while(i<lines.length && /^\s*\|.*\|\s*$/.test(lines[i])){ rows.push(lines[i]); i++; }
      // 去掉分隔行 |---|
      const bodyRows = rows.filter(r=>!(/^\s*\|[\s:\-|]+\|\s*$/.test(r)));
      let html='<table>';
      bodyRows.forEach((r,idx)=>{
        const cells = r.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(c=>c.trim());
        const tag = idx===0 ? 'th' : 'td';
        html += '<tr>'+cells.map(c=>`<${tag}>`+c+`</${tag}>`).join('')+'</tr>';
      });
      html+='</table>'; out.push(html);
    } else { out.push(lines[i]); i++; }
  }
  return out.join('\n').replace(/\n/g,'<br>');
}

function addAgent(text){
  const d = el('msg agent'); d.innerHTML = renderMD(text); chat.appendChild(d);
}

function addConfirm(){
  const c = el('confirm');
  const yes = document.createElement('button'); yes.textContent='确认';
  const no = document.createElement('button'); no.textContent='取消'; no.className='no';
  yes.onclick = ()=>{ c.remove(); send('确认'); };
  no.onclick = ()=>{ c.remove(); send('取消'); };
  c.appendChild(yes); c.appendChild(no); chat.appendChild(c);
}
function addFollowups(list){
  if(!list || !list.length) return;
  const f = el('followups');
  list.forEach(txt=>{ const b=document.createElement('button'); b.textContent=txt;
    b.onclick=()=>{ f.remove(); send(txt); }; f.appendChild(b); });
  chat.appendChild(f);
}

function showWorking(){
  const w = el('working');
  w.innerHTML = '<span class="spin"></span><span>agent 正在干活(调工具中)…</span>';
  chat.appendChild(w); scrollEnd();
  return w;
}

function showError(text, diagnosable, retryText){
  const e = el('err');
  e.textContent = text + (diagnosable? ('\n提示: '+diagnosable):'');
  if(retryText){
    const b = document.createElement('button'); b.className='retry'; b.textContent='重试';
    b.onclick = ()=>{ e.remove(); send(retryText); };
    e.appendChild(b);
  }
  chat.appendChild(e);
}

async function send(text){
  text = (text==null? inp.value : text).trim();
  if(!text) return;
  inp.value=''; btn.disabled=true;
  chat.appendChild(el('msg user', text)); scrollEnd();
  const working = showWorking();
  try{
    const r = await fetch('/api/chat', { method:'POST', headers: authHeaders({'Content-Type':'application/json'}),
      body: JSON.stringify({ session_id: SID, text }) });
    const j = await r.json(); working.remove();
    if(!j.ok){ showError(j.error||'出错了', j.diagnosable, text); }
    else{
      const fb = j.feedback;
      addTrace(fb.did);
      if(fb.result) addAgent(fb.result);
      if(fb.needs_confirm) addConfirm();
      addFollowups(fb.followups);
    }
  }catch(e){ working.remove(); showError('网络错误: '+e, 'L4 或 L3 daemon 可能已断开', text); }
  btn.disabled=false; inp.focus(); scrollEnd();
}

/* 启动时恢复历史(持久化在 L4 server) */
async function restoreHistory(){
  try{
    const r = await fetch('/api/history?session_id='+encodeURIComponent(SID), { headers: authHeaders() });
    const j = await r.json();
    (j.messages||[]).forEach(m=>{
      if(m.role==='user') chat.appendChild(el('msg user', typeof m.content==='string'? m.content : JSON.stringify(m.content)));
      else if(m.role==='assistant') addAgent(typeof m.content==='string'? m.content : JSON.stringify(m.content));
    });
    if(j.messages && j.messages.length) chat.appendChild(el('sys','—— 以上为历史记录 ——'));
    scrollEnd();
  }catch(e){}
}

btn.onclick = ()=>send();
inp.addEventListener('keydown', e=>{ if(e.key==='Enter') send(); });
health(); setInterval(health, 15000);
restoreHistory();
chat.appendChild(el('sys','这是 L4 反馈窗——agent 在 L3 干活,这里只看结果与对话。'));
</script>
</body>
</html>
"""


# ────────────────────────────────────────────────────────────────────── #
# HTTP 处理
# ────────────────────────────────────────────────────────────────────── #

class L4Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静音默认访问日志
        pass

    # ── 令牌校验 ────────────────────────────────────────────────────
    def _authorized(self) -> bool:
        """X-L4-Token header 或 ?token= query。L4_TOKEN="" 时显式关闭(仅本地)。"""
        if _ACCESS_TOKEN == "":
            return True
        from urllib.parse import urlparse, parse_qs
        if self.headers.get("X-L4-Token", "") == _ACCESS_TOKEN:
            return True
        q = parse_qs(urlparse(self.path).query)
        return (q.get("token") or [""])[0] == _ACCESS_TOKEN

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._json({"ok": False, "error": "unauthorized: 需要有效 token",
                    "diagnosable": "URL 加 ?token=<token>,或请求头 X-L4-Token。token 在 ~/.local/share/aureon/l4_token"}, 401)
        return False

    def _json(self, obj: Any, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            if not self._require_auth():
                return
            self._html(_PAGE)
        elif path == "/api/health":
            # health 不需要 token(供探活),但不泄露敏感信息
            daemon_ok = False
            try:
                with urllib.request.urlopen(DAEMON_URL.rstrip("/") + "/health", timeout=3) as r:
                    daemon_ok = r.status == 200
            except Exception:  # noqa: BLE001
                daemon_ok = False
            self._json({"ok": True, "daemon": daemon_ok})
        elif path == "/api/history":
            if not self._require_auth():
                return
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            sid = (q.get("session_id") or [""])[0]
            if not sid:
                self._json({"ok": False, "error": "session_id required"}, 400)
                return
            self._json({"ok": True, "messages": _get_history(sid)})
        elif path == "/api/receipts":
            if not self._require_auth():
                return
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            sid = (q.get("session_id") or [""])[0]
            if not sid:
                self._json({"ok": False, "error": "session_id required"}, 400)
                return
            self._json({"ok": True, "receipts": _read_receipts(sid)})
        elif path == "/api/sessions":
            # P0 多会话:枚举历史文件,按活跃度倒序
            if not self._require_auth():
                return
            self._json({"ok": True, "sessions": _list_sessions()})
        elif path == "/api/context_usage":
            # P0 上下文用量:零成本估算(oiagent_context)
            if not self._require_auth():
                return
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            sid = (q.get("session_id") or [""])[0]
            if not sid:
                self._json({"ok": False, "error": "session_id required"}, 400)
                return
            self._json(_context_usage(sid))
        else:
            self._json({"error": "unknown path"}, 404)

    def do_POST(self) -> None:
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path == "/api/estop":
            # P0 estop-cancel:放弃等待该会话进行中的后台结果,立即解锁 UI
            if not self._require_auth():
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:  # noqa: BLE001
                req = {}
            sid = (req.get("session_id") or "").strip()
            if not sid:
                self._json({"ok": False, "error": "session_id required"}, 400)
                return
            stopped = _cancel_conn(sid)
            self._json({"ok": True, "stopped": stopped,
                        "note": "客户端已放弃等待;daemon 后台结果(若仍产出)将被丢弃"})
            return
        if path != "/api/chat":
            self._json({"error": "unknown path"}, 404)
            return
        if not self._require_auth():
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:  # noqa: BLE001
            self._json({"ok": False, "error": "invalid json"}, 400)
            return
        session_id = req.get("session_id") or ("anon_" + uuid.uuid4().hex[:8])
        text = (req.get("text") or "").strip()
        if not text:
            self._json({"ok": False, "error": "empty text"}, 400)
            return
        result = _ask_daemon(session_id, text)
        self._json(result)


def run(host: str = L4_HOST, port: int = L4_PORT) -> None:
    srv = ThreadingHTTPServer((host, port), L4Handler)
    if _ACCESS_TOKEN:
        print(f"L4 反馈窗就绪: http://{host}:{port}/?token={_ACCESS_TOKEN}")
        print(f"  (token 已存 {_TOKEN_FILE};远程接入: ssh -L {port}:127.0.0.1:{port} <本机> 后开此 URL)")
    else:
        print(f"L4 反馈窗就绪: http://{host}:{port}  (token 已禁用 — 仅限本地)")
    print(f"  代理 L3 daemon: {DAEMON_URL}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    import os
    import sys
    # 优先 PORT env,再 sys.argv,再默认 L4_PORT
    p = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else L4_PORT))
    run(port=p)
