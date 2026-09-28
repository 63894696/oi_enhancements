"""
tests/test_free_for_dev_capabilities.py — free-for-dev Phase C 测试(2026-09-27)。

验证:
  1. import free_for_dev_capabilities → 4 capability + 4 endpoint 注册成功
  2. endpoint handler 在扩展不可用时降级返 ok=False
  3. endpoint handler 在扩展返回 {result:{type:"text"}} 时也正确透传(纯文本路径)
  4. endpoint handler 在扩展返回 {result:{type:"card",html,meta}} 时透传卡片
  5. intent_summary 含 4 个 capability 的 EXEC 写法示例
  6. scan_and_exec 识别 [[EXEC: free.find ...]] 标记
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# mock 大链路 — handler 内 `from prisiragent_web import _ext_rpc_call` 延迟 import
_fake_ext_rpc_call = mock.MagicMock(name="_ext_rpc_call")
sys.modules["prisiragent_cli"] = mock.MagicMock(name="prisiragent_cli")
sys.modules["prisiragent_web"] = mock.MagicMock(name="prisiragent_web")
sys.modules["prisiragent_web"]._ext_rpc_call = _fake_ext_rpc_call

from prisir_work import capability as cap_mod  # noqa: E402
from prisir_work import endpoints as ep_mod     # noqa: E402
from prisir_work import free_for_dev_capabilities  # noqa: E402,F401
from prisir_work.agent_main_chat_hook import (  # noqa: E402
    parse_exec_markers, scan_and_exec,
)


# ── 1. 注册 ──────────────────────────────────────────────────────
def test_1_capability_registry():
    ids = [c["id"] for c in cap_mod.list_capabilities()]
    for need in ("free.find", "free.list_categories", "free.detail", "free.random"):
        assert need in ids, f"missing capability: {need}"
        e = cap_mod.get(need)
        assert e["risk"] == "L0", f"{need} should be L0"
        assert e["endpoint"].startswith("/free/"), f"{need} bad endpoint"
    print("✓ 4 capability registered as L0")


def test_2_endpoint_registry():
    paths = list(ep_mod._REGISTRY.keys())
    for need in ("/free/find", "/free/list_categories", "/free/detail", "/free/random"):
        assert need in paths, f"missing endpoint: {need}"
        e = ep_mod._REGISTRY[need]
        assert e["method"] == "POST"
        assert e["risk"] == "L0"
        assert e["auth"] is True
    print("✓ 4 endpoint registered (POST /free/* L0 auth=True)")


# ── 2. handler 降级(扩展未跑) ─────────────────────────────────────
def test_3_handler_ext_down():
    fake_rv = {"error": "ext_not_running: free-for-dev-promo"}
    with mock.patch("prisiragent_web._ext_rpc_call", return_value=fake_rv, create=True):
        payload, status = ep_mod._REGISTRY["/free/find"]["handler"]({"query": "postgres"})
    assert status == 200
    assert payload["ok"] is False
    assert "ext_not_running" in payload["error"]
    assert payload["html"] == ""
    assert payload["warning"] == "ext_unavailable"
    print("✓ handler degrades cleanly when ext down")


# ── 3. handler 正常路径 — text 类型(扩展返「没找到」之类) ───────
def test_4_handler_normal_text():
    """free.find/query 没命中时扩展返 type:text,handler 应透传 type/text 字段。"""
    fake_result = {"result": {
        "type": "text",
        "text": "🔍 没找到匹配的资源",
        "meta": {},
    }}
    with mock.patch("prisiragent_web._ext_rpc_call", return_value=fake_result, create=True):
        payload, status = ep_mod._REGISTRY["/free/find"]["handler"]({"query": "zzz不存在zzz"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["type"] == "text"
    assert "没找到" in payload["text"]
    print("✓ handler forwards type=text (no-match path)")


# ── 4. handler 正常路径 — card 类型(扩展返资源卡片) ─────────────
def test_5_handler_normal_card():
    fake_html = '<div class="ext-card ext-free" data-ext="free-for-dev-promo"><span>GitHub</span></div>'
    fake_result = {"result": {
        "type": "card",
        "html": fake_html,
        "meta": {"query": "git", "category": "Source Code Repos",
                 "total": 5, "returned": 5,
                 "item_names": ["GitHub", "GitLab", "Bitbucket"]},
    }}
    with mock.patch("prisiragent_web._ext_rpc_call", return_value=fake_result, create=True):
        payload, status = ep_mod._REGISTRY["/free/find"]["handler"]({"query": "git"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["type"] == "card"
    assert payload["html"] == fake_html
    assert payload["meta"]["total"] == 5
    assert "GitHub" in payload["meta"]["item_names"]
    print("✓ handler forwards html + meta from ext (card path)")


# ── 5. handler 处理扩展返回的 border case ───────────────────────
def test_6_handler_garbage_inputs():
    cases = [
        None,                            # 不是 dict
        {"result": None},                # result 不是 dict
        {"result": "string"},            # result 是 str
        {},                              # 缺 result 字段
    ]
    for case in cases:
        with mock.patch("prisiragent_web._ext_rpc_call", return_value=case, create=True):
            payload, status = ep_mod._REGISTRY["/free/list_categories"]["handler"]({})
        assert status == 200, f"bad case {case}: status={status}"
        assert payload["ok"] is False, f"bad case {case}: should be ok=False"
    print("✓ handler defends against 4 garbage input shapes")


# ── 6. intent_summary 含完整 EXEC 提示 ───────────────────────────
def test_7_intent_summary():
    s = free_for_dev_capabilities.intent_summary()
    for need in ("free.find", "free.list_categories", "free.detail", "free.random"):
        assert need in s, f"intent_summary should mention {need}"
    assert "[[EXEC: free.find" in s
    assert "[[EXEC: free.list_categories" in s
    assert "[[EXEC: free.detail" in s
    assert "[[EXEC: free.random" in s
    # 应提到 Postgres / 关键词示例
    assert "postgres" in s, "should mention postgres example"
    assert "CDN" in s, "should mention CDN example"
    assert "snapshot" in s.lower() or "2026" in s, "should mention data snapshot"
    print(f"✓ intent_summary contains all 4 EXEC examples (len={len(s)})")


# ── 7. EXEC 解析 ────────────────────────────────────────────────
def test_8_parse_exec_markers():
    txt = ('免费数据库推荐:\n[[EXEC: free.find query="postgres" limit="5"]]\n')
    markers = parse_exec_markers(txt)
    assert len(markers) == 1
    assert markers[0].capability == "free.find"
    assert markers[0].args == {"query": "postgres", "limit": "5"}
    print("✓ EXEC parser reads free markers")


def test_9_scan_and_exec_free_l0_direct():
    """L0 free 不弹确认卡,直接 execute → 调 endpoint handler → 拿 result。"""
    txt = '[[EXEC: free.list_categories]]'
    fake_result = {"result": {
        "type": "card",
        "html": "<div>57 个分类</div>",
        "meta": {"total": 57, "category_names": ["CMS", "CI and CD"]},
    }}
    with mock.patch("prisiragent_web._ext_rpc_call", return_value=fake_result, create=True):
        events = scan_and_exec(txt)
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "capability_exec_result"
    assert ev["capability"] == "free.list_categories"
    assert ev["ok"] is True
    assert ev["result"]["meta"]["total"] == 57
    print("✓ L0 free EXEC goes direct (no confirm)")


def test_10_scan_and_exec_free_ext_down():
    """扩展 down 时 scan_and_exec 不抛,推 capability_exec_result ok=False。"""
    txt = '[[EXEC: free.find query="x"]]'
    with mock.patch("prisiragent_web._ext_rpc_call",
                    return_value={"error": "timeout: free-for-dev-promo.free.find"},
                    create=True):
        events = scan_and_exec(txt)
    assert len(events) == 1
    ev = events[0]
    assert ev["ok"] is False
    assert "timeout" in ev["error"]
    print("✓ scan_and_exec surfaces ext timeout cleanly")


# ── 8. capability 必须可以 search 命中 ───────────────────────────
def test_11_capability_search():
    hits = cap_mod.search("免费")
    free_hits = [h for h in hits if h["id"].startswith("free.")]
    assert len(free_hits) == 4, f"expected 4 free hits, got {len(free_hits)}"
    for h in free_hits:
        assert h["risk"] == "L0"
    # 也试英文 keyword
    hits_en = cap_mod.search("free")
    free_en = [h for h in hits_en if h["id"].startswith("free.")]
    assert len(free_en) == 4, f"expected 4 free hits for 'free', got {len(free_en)}"
    print("✓ capability.search('免费'/'free') returns all 4 free caps")


if __name__ == "__main__":
    test_1_capability_registry()
    test_2_endpoint_registry()
    test_3_handler_ext_down()
    test_4_handler_normal_text()
    test_5_handler_normal_card()
    test_6_handler_garbage_inputs()
    test_7_intent_summary()
    test_8_parse_exec_markers()
    test_9_scan_and_exec_free_l0_direct()
    test_10_scan_and_exec_free_ext_down()
    test_11_capability_search()
    print("\n所有 11 组断言通过 ✅")
