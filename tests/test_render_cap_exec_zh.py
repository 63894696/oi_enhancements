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
# P3j T19: 全配置面提示 + 纯开源模式 banner
# ---------------------------------------------------------------------------

def test_t19_intro_block_present():
    """P3j T19: deps-intro 块说明多模型协作 + 零 key 也能用。"""
    html = (REPO_ROOT / "companion" / "prisIragent-wechat-publisher" /
            "static" / "index.html").read_text(encoding="utf-8")
    assert "deps-intro" in html, "missing deps-intro block"
    assert "多模型协作" in html, "missing 多模型协作 in intro"
    assert "不填任何 key" in html or "完全不填" in html, \
        "missing 零 key 也能用 message"


def test_t19_oss_disclosure_present():
    """P3j T19: 折叠说明 — 纯开源模式(零 key + ffmpeg + faster-whisper)。"""
    html = (REPO_ROOT / "companion" / "prisIragent-wechat-publisher" /
            "static" / "index.html").read_text(encoding="utf-8")
    assert "deps-oss" in html, "missing deps-oss (纯开源模式) disclosure"
    assert "纯开源模式" in html, "missing 纯开源模式 title"
    assert "ffmpeg" in html, "missing ffmpeg in disclosure"
    assert "faster-whisper" in html, "missing faster-whisper in disclosure"


def test_t19_deps_matrix_present():
    """P3j T19: 4 环节矩阵 — 配图/视频/配音/转字幕 + JS 渲染。"""
    html = (REPO_ROOT / "companion" / "prisIragent-wechat-publisher" /
            "static" / "index.html").read_text(encoding="utf-8")
    assert "video-deps-matrix" in html, "missing video-deps-matrix container"
    assert "setDepsMatrix" in html, "missing setDepsMatrix JS function"
    assert "_matrixRow" in html, "missing _matrixRow helper"
    # 4 环节图标 + label
    for icon_label in ("🖼️", "🎬", "🗣️", "🎤", "⚙️"):
        assert icon_label in html, f"missing icon {icon_label}"
    # 推荐文案
    assert "SiliconFlow" in html, "missing SiliconFlow recommendation"
    assert "edge-tts" in html or "edge TTS" in html, "missing edge-tts recommendation"


def test_t19_matrix_cta_focus():
    """P3j T19: 矩阵 CTA 点 → 展开配置卡 + 滚动 + focus 输入框。"""
    html = (REPO_ROOT / "companion" / "prisIragent-wechat-publisher" /
            "static" / "index.html").read_text(encoding="utf-8")
    assert "_focusProviderCard" in html, "missing _focusProviderCard JS"
    assert "data-pid" in html, "missing data-pid attribute (CTA 锚点)"
    assert "scrollIntoView" in html, "missing scrollIntoView in CTA"


def test_t19_css_classes_present():
    """P3j T19: CSS 类 — deps-intro / deps-matrix / deps-oss。"""
    css = (REPO_ROOT / "companion" / "prisIragent-wechat-publisher" /
           "static" / "index.html").read_text(encoding="utf-8")
    # 静态扫 inline style/CSS — index.html 含 <style> 块
    assert ".deps-intro" in css, "missing .deps-intro CSS"
    assert ".deps-matrix" in css, "missing .deps-matrix CSS"
    assert ".deps-matrix-row" in css or "deps-matrix-row" in css, \
        "missing .deps-matrix-row CSS"
    assert ".deps-oss" in css, "missing .deps-oss CSS"
    assert "deps-matrix-status-ok" in css, \
        "missing .deps-matrix-status-ok CSS"
    assert "deps-matrix-status-miss" in css, \
        "missing .deps-matrix-status-miss CSS"


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
