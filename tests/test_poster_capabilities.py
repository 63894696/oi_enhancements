"""
tests/test_poster_capabilities.py — P3j Phase C 测试(2026-09-27)。

验证:
  1. import poster_capabilities → 3 capability + 3 endpoint 注册成功
  2. endpoint handler 在扩展不可用时降级返 ok=False
  3. endpoint handler 在扩展返回 {result:{html,meta}} 时正确透传
  4. intent_summary 含 3 个 capability 的 EXEC 写法示例
  5. scan_and_exec 识别 [[EXEC: poster.smart theme="..."]] 标记
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

# 让 prisir_work / companion / tests 都可 import
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── mock prisiragent_cli / prisIragent_web 避免拖它们真 import(它们 import 大链路,测试不需要)
# poster_capabilities._make_handler 用 `from prisiragent_web import _ext_rpc_call` 延迟 import,
# patch 用 mock.patch("prisiragent_web._ext_rpc_call", ..., create=True),但 mock 需要模块先存在。
# 这里预占 stub:让 prisiragent_web 被 import 时不抛 ModuleNotFoundError,且后续 patch 可用。
_fake_ext_rpc_call = mock.MagicMock(name="_ext_rpc_call")
sys.modules["prisiragent_cli"] = mock.MagicMock(name="prisiragent_cli")
sys.modules["prisiragent_web"] = mock.MagicMock(name="prisiragent_web")
sys.modules["prisiragent_web"]._ext_rpc_call = _fake_ext_rpc_call

from prisir_work import capability as cap_mod  # noqa: E402
from prisir_work import endpoints as ep_mod     # noqa: E402
from prisir_work import poster_capabilities     # noqa: E402,F401
from prisir_work.agent_main_chat_hook import (  # noqa: E402
    parse_exec_markers, scan_and_exec,
)


# ── 1. 注册结果 ───────────────────────────────────────────────────
def test_1_capability_registry():
    ids = [c["id"] for c in cap_mod.list_capabilities()]
    for need in ("poster.smart", "poster.spec", "poster.ai_design"):
        assert need in ids, f"missing capability: {need}"
        e = cap_mod.get(need)
        assert e["risk"] == "L0", f"{need} should be L0"
        assert e["endpoint"].startswith("/poster/"), f"{need} bad endpoint"
    print("✓ 3 capability registered as L0")


def test_2_endpoint_registry():
    paths = list(ep_mod._REGISTRY.keys())
    for need in ("/poster/smart", "/poster/spec", "/poster/ai_design"):
        assert need in paths, f"missing endpoint: {need}"
        e = ep_mod._REGISTRY[need]
        assert e["method"] == "POST"
        assert e["risk"] == "L0"
        assert e["auth"] is True
    print("✓ 3 endpoint registered (POST /poster/* L0 auth=True)")


# ── 2. handler 降级(扩展未跑) ─────────────────────────────────────
def test_3_handler_ext_down():
    # handler 内部 `from prisiragent_web import _ext_rpc_call` — patch 源模块
    fake_rv = {"error": "ext_not_running: handraw-style-prompter"}
    with mock.patch("prisiragent_web._ext_rpc_call",
                    return_value=fake_rv, create=True):
        payload, status = ep_mod._REGISTRY["/poster/smart"]["handler"](
            {"theme": "秋天的第一杯奶茶"}
        )
    assert status == 200
    assert payload["ok"] is False
    assert "ext_not_running" in payload["error"]
    assert payload["html"] == ""  # 降级不给空 html 让前端不渲染卡
    assert payload["warning"] == "ext_unavailable"
    print("✓ handler degrades cleanly when ext down")


# ── 3. handler 正常路径(扩展返回 {result:{...}}) ───────────────────
def test_4_handler_normal():
    fake_html = '<div class="ext-card ext-poster"><pre data-role="zh">风格名称:001号风格...</pre></div>'
    fake_result = {"result": {
        "type": "card",
        "html": fake_html,
        "meta": {"style": 1, "color": "C-01", "theme": "秋天的第一杯奶茶"},
    }}
    with mock.patch("prisiragent_web._ext_rpc_call", return_value=fake_result, create=True):
        payload, status = ep_mod._REGISTRY["/poster/smart"]["handler"](
            {"theme": "秋天的第一杯奶茶"}
        )
    assert status == 200
    assert payload["ok"] is True
    assert payload["html"] == fake_html, "should forward html unchanged"
    assert payload["meta"]["style"] == 1
    assert payload["meta"]["color"] == "C-01"
    assert payload["type"] == "card"
    print("✓ handler forwards html + meta from ext")


# ── 3b. handler 处理扩展返回的 border case ───────────────────────
def test_5_handler_ext_returns_garbage():
    cases = [
        None,                            # 不是 dict
        {"result": None},                # result 不是 dict
        {"result": "string"},            # result 是 str
        {},                              # 缺 result 字段
    ]
    for case in cases:
        with mock.patch("prisiragent_web._ext_rpc_call", return_value=case, create=True):
            payload, status = ep_mod._REGISTRY["/poster/spec"]["handler"](
                {"subject": "x", "style": "041"}
            )
        assert status == 200, f"bad case {case}: status={status}"
        assert payload["ok"] is False, f"bad case {case}: should be ok=False"
    print("✓ handler defends against 4 garbage input shapes")


# ── 4. intent_summary 含完整 EXEC 提示 ───────────────────────────
def test_6_intent_summary():
    s = poster_capabilities.intent_summary()
    assert "poster.smart" in s
    assert "poster.spec" in s
    assert "poster.ai_design" in s
    assert "[[EXEC: poster.smart" in s
    assert "[[EXEC: poster.spec" in s
    assert "[[EXEC: poster.ai_design" in s
    assert "041" in s, "should mention style 041 example"
    assert "C-25" in s, "should mention color C-25 example"
    assert "秋天的第一杯奶茶" in s, "should mention theme example"
    print(f"✓ intent_summary contains all 3 EXEC examples (len={len(s)})")


# ── 5. EXEC 解析 + scan_and_exec 路径 ────────────────────────────
def test_7_parse_exec_markers():
    txt = ('好的,我来给你出个秋日海报:'
           '\n[[EXEC: poster.smart theme="秋天的第一杯奶茶"]]\n')
    markers = parse_exec_markers(txt)
    assert len(markers) == 1
    assert markers[0].capability == "poster.smart"
    assert markers[0].args == {"theme": "秋天的第一杯奶茶"}
    print("✓ EXEC parser reads poster markers")


def test_8_scan_and_exec_poster_l0_direct():
    """L0 poster 不弹确认卡,直接 execute → 调 endpoint handler → 拿 result。"""
    txt = '[[EXEC: poster.spec subject="春节回家" style="041" color="C-25" layout="SC-001"]]'
    fake_result = {"result": {
        "type": "card",
        "html": "<div>fake</div>",
        "meta": {"style": 41, "color": "C-25", "layout": "SC-001"},
    }}
    with mock.patch("prisiragent_web._ext_rpc_call", return_value=fake_result, create=True):
        events = scan_and_exec(txt)
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "capability_exec_result"
    assert ev["capability"] == "poster.spec"
    assert ev["ok"] is True
    assert ev["result"]["html"] == "<div>fake</div>"
    print("✓ L0 poster EXEC goes direct (no confirm)")


def test_9_scan_and_exec_poster_ext_down():
    """扩展 down 时 scan_and_exec 不抛,推 capability_exec_result ok=False。"""
    txt = '[[EXEC: poster.smart theme="x"]]'
    with mock.patch("prisiragent_web._ext_rpc_call",
                    return_value={"error": "timeout: handraw-style-prompter.poster.smart"},
                    create=True):
        events = scan_and_exec(txt)
    assert len(events) == 1
    ev = events[0]
    assert ev["ok"] is False
    assert "timeout" in ev["error"]
    print("✓ scan_and_exec surfaces ext timeout cleanly")


# ── 6. capability 必须可以 search 命中 ───────────────────────────
def test_10_capability_search():
    hits = cap_mod.search("海报")
    poster_hits = [h for h in hits if h["id"].startswith("poster.")]
    assert len(poster_hits) == 3, f"expected 3 poster hits, got {len(poster_hits)}"
    for h in poster_hits:
        assert h["risk"] == "L0"
    print("✓ capability.search('海报') returns all 3 poster caps")


# ── runner ───────────────────────────────────────────────────────
if __name__ == "__main__":
    test_1_capability_registry()
    test_2_endpoint_registry()
    test_3_handler_ext_down()
    test_4_handler_normal()
    test_5_handler_ext_returns_garbage()
    test_6_intent_summary()
    test_7_parse_exec_markers()
    test_8_scan_and_exec_poster_l0_direct()
    test_9_scan_and_exec_poster_ext_down()
    test_10_capability_search()
    print("\n所有 10 组断言通过 ✅")