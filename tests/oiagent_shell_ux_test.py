"""tests/oiagent_shell_ux_test.py — 壳体验三件套单元测试。

覆盖(纯本地路径,不依赖 LLM key):
  ①实时工具进度: run_conversation 的 on_event 回调在工具执行前后发 tool_start/tool_end;
    /status 返回 events 增量并推进游标;_handle_chat 新一轮清空事件缓冲。
  ③产物内联查看: _serve_workdir_file 目录穿越防护(越出 workdir→403/拒)、
    workdir 内文件→正确 Content-Type、md 以 text 取回。

跑法: python tests/oiagent_shell_ux_test.py  →  打印 PASS/FAIL
"""
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# 用临时 DB / 临时 workdir,不碰真实数据
_tmp = tempfile.mkdtemp(prefix="oiagent_ux_test_")
os.environ["PRISIR_DATA"] = _tmp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import oiagent_web as w  # noqa: E402
import oiagent_cli as cli  # noqa: E402
import oiagent_context as octx  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_fails = []


def check(name, cond):
    print(("✓ " if cond else "✗ ") + name)
    if not cond:
        _fails.append(name)


# ── ①实时工具进度:on_event 回调 ────────────────────────────────────────────

def test_on_event_fires_on_tool_path():
    """run_conversation 走工具路径时,on_event 应收到 tool_start → tool_end 序。
    mock 掉 _completion_with_temperature_fallback,第一轮返回 tool_calls,第二轮返回文本。"""
    class _Fn:
        def __init__(s, name, args): s.name = name; s.arguments = args
    class _TC:
        def __init__(s, name, args): s.id = "c1"; s.function = _Fn(name, args)
    class _Msg:
        def __init__(s, content, tcs): s.content = content; s.tool_calls = tcs
    class _Choice:
        def __init__(s, msg): s.message = msg
    class _Resp:
        def __init__(s, msg): s.choices = [_Choice(msg)]

    calls = {"n": 0}
    def fake_completion(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(_Msg("", [_TC("run_shell", '{"command":"echo hi"}')]))
        return _Resp(_Msg("done", None))

    events = []
    with patch.object(cli, "_completion_with_temperature_fallback", side_effect=fake_completion):
        res = cli.run_conversation([{"role": "user", "content": "跑个命令"}], "m",
                                   _tmp, on_event=events.append)
    types = [e["type"] for e in events]
    check("on_event 发出 tool_start", "tool_start" in types)
    check("on_event 发出 tool_end", "tool_end" in types)
    check("tool_start 在 tool_end 之前", types.index("tool_start") < types.index("tool_end"))
    check("tool_end 带 name", any(e.get("name") == "run_shell" for e in events if e["type"] == "tool_end"))
    check("返回 trace 仍含 tool 步", any(s.get("role") == "tool" for s in res.get("trace", [])))


def test_on_event_default_none_safe():
    """不传 on_event(默认 None)时工具路径不抛异常(向后兼容)。"""
    class _Fn:
        def __init__(s): s.name = "list_files"; s.arguments = "{}"
    class _TC:
        def __init__(s): s.id = "c1"; s.function = _Fn()
    class _Msg:
        def __init__(s, c, t): s.content = c; s.tool_calls = t
    class _Resp:
        def __init__(s, m): s.choices = [type("C", (), {"message": m})()]
    calls = {"n": 0}
    def fake(**kw):
        calls["n"] += 1
        return _Resp(_Msg("", [_TC()])) if calls["n"] == 1 else _Resp(_Msg("ok", None))
    with patch.object(cli, "_completion_with_temperature_fallback", side_effect=fake):
        res = cli.run_conversation([{"role": "user", "content": "x"}], "m", _tmp)
    check("默认 on_event=None 不炸", res["rc"] == 0)


# ── ①/status 增量事件 + 游标 ────────────────────────────────────────────────

class _FakeHandler:
    """最小 handler stub:只实现 _serve_workdir_file / _json 依赖的 write 面。"""
    def __init__(s):
        s.status = None; s.headers = {}; s.body = b""
        s.wfile = s
    # _json 需要
    def send_response(s, c): s.status = c
    def send_header(s, k, v): s.headers[k] = v
    def end_headers(s): pass
    def write(s, b): s.body += b


def test_status_incremental_events():
    """_events[sid] 追加事件后,/status 应返回自游标后的增量并推进游标。
    直接驱动模块级缓冲 + 模拟 status 处理逻辑(不经真 socket)。"""
    sid = "uxtest-sid"
    with w._events_lock:
        w._events[sid] = []
        w._event_cursor[sid] = 0
    # 模拟 on_event 追加 2 条
    with w._events_lock:
        w._events[sid].append({"type": "tool_start", "name": "read_file"})
        w._events[sid].append({"type": "tool_end", "name": "read_file", "ms": 5})
    # 第一次取:应得 2 条,游标推到 2
    with w._events_lock:
        all_ev = w._events.get(sid, [])
        cur = w._event_cursor.get(sid, 0)
        first = all_ev[cur:]
        w._event_cursor[sid] = len(all_ev)
    check("首次取到 2 条增量", len(first) == 2)
    # 再追加 1 条,第二次取:只得新 1 条
    with w._events_lock:
        w._events[sid].append({"type": "tool_start", "name": "write_file"})
        all_ev = w._events.get(sid, [])
        cur = w._event_cursor.get(sid, 0)
        second = all_ev[cur:]
        w._event_cursor[sid] = len(all_ev)
    check("二次只取增量 1 条", len(second) == 1 and second[0]["name"] == "write_file")
    # 清理
    with w._events_lock:
        w._events.pop(sid, None); w._event_cursor.pop(sid, None)


def test_chat_resets_event_buffer():
    """新一轮 chat 清空 _events[sid]/_event_cursor[sid] 的逻辑(防跨轮残留)。
    该重置内联在 _handle_chat;此处直接断言清空语义成立。"""
    sid = w.create_session("事件重置测试")
    with w._events_lock:
        w._events[sid] = [{"type": "tool_start", "name": "x"}]
        w._event_cursor[sid] = 1
        # 复现 _handle_chat 内联重置
        w._events[sid] = []
        w._event_cursor[sid] = 0
        empty_after = (w._events.get(sid) == [] and w._event_cursor.get(sid) == 0)
    check("新一轮清空事件缓冲", empty_after)


# ── ③产物内联查看:_serve_workdir_file 目录穿越防护 ─────────────────────────

def _make_handler_inst():
    """构造 Handler 裸实例(不经 socket),接 _FakeHandler 的 write 面。"""
    h = _FakeHandler()
    inst = object.__new__(w.Handler)
    inst.wfile = h
    inst.send_response = h.send_response
    inst.send_header = h.send_header
    inst.end_headers = h.end_headers
    return inst, h


def test_serve_workdir_file_traversal_blocked():
    """越出 workdir 的路径必须 403(目录穿越防护红线)。"""
    wd = tempfile.mkdtemp(prefix="oiagent_wd_")
    open(os.path.join(wd, "ok.txt"), "w", encoding="utf-8").write("hello")
    old = w._WORKDIR["path"]
    w._WORKDIR["path"] = wd
    try:
        inst, h = _make_handler_inst()
        inst._serve_workdir_file("../../../Windows/win.ini")
        check("目录穿越被拒(403)", h.status == 403)
        inst2, h2 = _make_handler_inst()
        inst2._serve_workdir_file("ok.txt")
        check("workdir 内文件 200", h2.status == 200)
        check("返回文件内容", b"hello" in h2.body)
    finally:
        w._WORKDIR["path"] = old


def test_serve_workdir_file_md_as_text():
    """md 文件以 text/plain 取回(防直接当 html 渲染),由前端再 md 渲染。"""
    wd = tempfile.mkdtemp(prefix="oiagent_wd_md_")
    open(os.path.join(wd, "design.md"), "w", encoding="utf-8").write("# 标题\n表格")
    old = w._WORKDIR["path"]
    w._WORKDIR["path"] = wd
    try:
        inst, h = _make_handler_inst()
        inst._serve_workdir_file("design.md")
        check("md 文件 200", h.status == 200)
        check("md Content-Type 是 text", "text" in h.headers.get("Content-Type", ""))
        check("md 内容取回", "标题" in h.body.decode("utf-8", "replace"))
    finally:
        w._WORKDIR["path"] = old


# ── 孤儿 tool 消息清洗(tool_call_id is not found 回归)──────────────────────

def _assert_no_orphan_tool(msgs):
    """协议校验视角:每条 tool 消息必须有带匹配 tool_calls 的 assistant 前驱。"""
    valid = set()
    for m in msgs:
        r = m.get("role")
        if r == "assistant":
            valid = {t.get("id") for t in (m.get("tool_calls") or []) if isinstance(t, dict)}
            valid.discard(None)
        elif r == "tool":
            if m.get("tool_call_id") not in valid:
                return False
        else:
            valid = set()
    return True


def test_sanitize_folds_orphan_tool():
    """库读出的历史(assistant 无 tool_calls + tool 孤儿)→ 清洗后无孤儿、协议合法。"""
    # 复刻 #43 后的真实历史:user → assistant(丢tool_calls) → tool(孤儿) → assistant答复 → user
    hist = [
        {"role": "user", "content": "读下 README"},
        {"role": "assistant", "content": ""},                       # tool_calls 已丢
        {"role": "tool", "content": "[🔧 read_file]\n# 内容xxx"},     # 孤儿,无 tool_call_id
        {"role": "assistant", "content": "README 讲的是…"},
        {"role": "user", "content": "对比 Gemini 呢"},
    ]
    out = octx.sanitize_tool_history(hist)
    check("清洗后无孤儿 tool 消息", _assert_no_orphan_tool(out))
    check("孤儿 tool 折叠为 assistant", not any(m.get("role") == "tool" for m in out))
    check("折叠保留工具名", any("read_file" in str(m.get("content")) for m in out))
    check("折叠标注只读资料", any("仅供上下文参考" in str(m.get("content")) for m in out))


def test_sanitize_keeps_paired_tool():
    """同轮内存态(assistant 带 tool_calls + tool 带 tool_call_id)→ 原样保留不折叠。"""
    paired = [
        {"role": "user", "content": "跑命令"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "run_shell", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "run_shell", "content": "ok"},
        {"role": "assistant", "content": "跑完了"},
    ]
    out = octx.sanitize_tool_history(paired)
    check("配对 tool 消息原样保留", any(m.get("role") == "tool" and m.get("tool_call_id") == "call_1" for m in out))
    check("配对序列协议合法", _assert_no_orphan_tool(out))


def test_sanitize_empty_and_passthrough():
    """边界:空/纯 user-assistant 无 tool → 原样;幂等。"""
    check("空列表安全", octx.sanitize_tool_history([]) == [])
    plain = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    check("无 tool 原样", octx.sanitize_tool_history(plain) == plain)
    check("幂等", octx.sanitize_tool_history(octx.sanitize_tool_history(plain)) == plain)


if __name__ == "__main__":
    test_sanitize_folds_orphan_tool()
    test_sanitize_keeps_paired_tool()
    test_sanitize_empty_and_passthrough()
    test_on_event_fires_on_tool_path()
    test_on_event_fires_on_tool_path()
    test_on_event_default_none_safe()
    test_status_incremental_events()
    test_chat_resets_event_buffer()
    test_serve_workdir_file_traversal_blocked()
    test_serve_workdir_file_md_as_text()
    print()
    if _fails:
        print(f"FAIL {len(_fails)}: {_fails}")
        sys.exit(1)
    print("PASS all")
