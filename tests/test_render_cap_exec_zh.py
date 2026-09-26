# -*- coding: utf-8 -*-
"""tests/test_render_cap_exec_zh.py — T17-H 前端接 zh/hint/link 验证。

前端 `renderCapExecResult` 是 JS,这里走两侧夹击验证:
  1. 后端产出:agent_main_chat_hook.build_exec_result 失败时必含 zh/hint/link
     三字段(已由 T17-C ship 保证;这里 case 直接打钉)
  2. 前端桥:companion/static/app.js 必须读 m.zh/hint/link 三字段并写中文 + 链接
  3. CSS 兜底:companion/static/guohua-theme.css 必须有 .cap-exec-zh 样式
  4. URL focus:wechat-publisher index.html 必须含 autoFocusFromUrl + media-keys 锚

全部必绿;无 skip。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 1. 后端产出:scan_and_exec 失败 → zh/hint/link 在 ev dict 里
# ---------------------------------------------------------------------------

def test_build_exec_result_attaches_zh_hint_link():
    from prisir_work.agent_main_chat_hook import scan_and_exec
    events = scan_and_exec('[[EXEC: nope_cap_xyz k="v"]]')
    assert len(events) == 1
    ev = events[0]
    assert ev["ok"] is False
    # 字段必在(dict 形式;allow 空字串)
    assert "zh" in ev, f"missing zh: {ev}"
    assert "hint" in ev
    assert "link" in ev
    # 必有非空 zh(translate_exec_error 在 capability_not_found 上返原文)
    assert ev["zh"], f"expected non-empty zh, got {ev}"


# ---------------------------------------------------------------------------
# 2. 前端桥:app.js 必读 m.zh/hint/link 三个字段 + 中文渲染 + 链接渲染
# ---------------------------------------------------------------------------

def test_app_js_renders_zh_and_link():
    app_js = (REPO_ROOT / "companion" / "static" / "app.js").read_text(
        encoding="utf-8")
    # 找到 renderCapExecResult 函数体
    m = re.search(r"function renderCapExecResult\(m\)\s*\{(.*?)^\}", app_js,
                  re.DOTALL | re.MULTILINE)
    assert m, "renderCapExecResult not found"
    body = m.group(1)
    # 必读 m.zh
    assert re.search(r"!\s*ok\s*&&\s*m\.zh", body), \
        "renderCapExecResult 不读 m.zh"
    # 必读 m.link
    assert re.search(r"!\s*ok\s*&&\s*m\.link", body), \
        "renderCapExecResult 不读 m.link"
    # 必渲染中文 div(cap-exec-zh class)
    assert "cap-exec-zh" in body, "missing cap-exec-zh class"
    # 必构造 localhost:18899 跳转链接
    assert "localhost:18899" in body, "missing publisherBase 18899"
    # 必含 focus= URL param
    assert "?focus=" in body or "focus=" in body, \
        "missing focus URL param"


# ---------------------------------------------------------------------------
# 3. CSS 兜底:.cap-exec-zh 样式必在 guohua-theme.css
# ---------------------------------------------------------------------------

def test_css_cap_exec_zh_exists():
    css = (REPO_ROOT / "companion" / "static" / "guohua-theme.css").read_text(
        encoding="utf-8")
    assert ".cap-exec-zh" in css, "missing .cap-exec-zh CSS rule"


# ---------------------------------------------------------------------------
# 4. URL focus:autoFocusFromUrl + media-keys-card 在 wechat-publisher
# ---------------------------------------------------------------------------

def test_wechat_publisher_autofocus_url():
    html = (REPO_ROOT / "companion" / "prisIragent-wechat-publisher" /
            "static" / "index.html").read_text(encoding="utf-8")
    assert "autoFocusFromUrl" in html, "missing autoFocusFromUrl JS"
    assert "?focus=" in html, "missing focus URL param usage"
    assert "media-keys-card" in html, \
        "missing media-keys-card id mapping"


# ---------------------------------------------------------------------------
# 5. wechat-publisher 含 testMediaProvider + 🔬 测试按钮
# ---------------------------------------------------------------------------

def test_wechat_publisher_test_button():
    html = (REPO_ROOT / "companion" / "prisIragent-wechat-publisher" /
            "static" / "index.html").read_text(encoding="utf-8")
    assert "testMediaProvider" in html, "missing testMediaProvider function"
    assert "🔬 测试" in html, "missing 🔬 测试 button text"
    assert "/api/media/test" in html, "missing /api/media/test route call"


# ---------------------------------------------------------------------------
# 允许直接 python tests/test_render_cap_exec_zh.py 跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    passed = 0
    failed = 0
    funcs = [(n, getattr(sys.modules[__name__], n))
             for n in dir(sys.modules[__name__])
             if n.startswith("test_") and callable(getattr(sys.modules[__name__], n))]
    for name, fn in funcs:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n=== render_cap_exec_zh: {passed} passed, {failed} failed ===")
    sys.exit(0 if failed == 0 else 1)
