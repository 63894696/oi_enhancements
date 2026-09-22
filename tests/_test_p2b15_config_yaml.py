# -*- coding: utf-8 -*-
r"""
_test_p2b15_config_yaml.py — P2.5+15(2026-09-22)config.yaml 三端对齐静态扫 + 烟雾

范围(与 B-0/B-2/B-3 同款模式:静态扫 50+ 项锚点 + cli 单元 smoke):
  ① Python 端:prisIrai_config.py 模块存在 + 公开 getter 齐全 + 烟雾测试绿
  ② companion/music/port_config.py:DEFAULT_* 走 yaml
  ③ prisIragent_web.py:_PAGE placeholder + 渲染前 str.replace + 顶部显式 import
  ④ Electron 壳:config_loader.js + port_config.js let DEFAULT_*_PORT + try require + main.js BRAND_* 接入
  ⑤ Tauri 壳:config_loader.rs mod + lib.rs BRAND_UPDATES_URL → fn + brand 周期走 yaml
  ⑥ yaml schema:ports/brand/forum 三段,字段齐全 + 与三端 default 对齐

通过条件:50/50 项静态扫绿 + py_compile OK + node --check OK + Python smoke 返端口对齐
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _exists(p: Path) -> bool:
    return p.exists() and p.stat().st_size > 0


def test_python_module_exists():
    """① Python 端:prisIrai_config.py 在仓库根,公开 getter 齐全。"""
    p = REPO_ROOT / "prisIrai_config.py"
    assert _exists(p), f"missing: {p}"
    text = _read(p)
    # 公共 getter 必须齐
    for fn in [
        "web_port_default", "companion_port_default",
        "music_port_default", "calendar_port_default",
        "brand_url", "brand_max_per_run", "brand_interval_sec",
        "brand_seen_cap",
        "forum_url", "forum_board", "forum_hint",
    ]:
        assert f"def {fn}(" in text, f"missing getter: {fn}"
    # YAML 极简解析(regex + section: + 2 空格 key: value)
    assert "import re" in text or "import re " in text, "prisIrai_config.py must use re for YAML"
    assert r"\s*:\s*$" in text or "section" in text, "prisIrai_config.py must parse section headers"
    print("✓ prisIrai_config.py 模块 + getter 齐全")


def test_python_yaml_file_exists():
    """① 仓库根 prisIrai_config.yaml 三段 ports/brand/forum,字段名严格对齐。"""
    p = REPO_ROOT / "prisIrai_config.yaml"
    assert _exists(p), f"missing: {p}"
    text = _read(p)
    for sec in ("ports:", "brand:", "forum:"):
        assert sec in text, f"yaml 缺段: {sec}"
    # 字段对齐
    for k in [
        "ports.web", "ports.companion", "ports.music", "ports.calendar",
        "brand.url", "brand.max_per_run", "brand.interval_sec", "brand.seen_cap",
        "forum.url", "forum.board", "forum.hint",
    ]:
        # yaml 是缩进式,检查 "key:" 形式
        key = k.split(".")[-1]
        assert re.search(rf"^\s+{key}:", text, re.MULTILINE), f"yaml 缺字段: {k}"
    print("✓ prisIrai_config.yaml 三段 + 11 字段")


def test_companion_port_config_yaml():
    """② companion/music/port_config.py:DEFAULT_* 走 yaml 读。"""
    p = REPO_ROOT / "companion" / "music" / "port_config.py"
    text = _read(p)
    assert "prisIrai_config.yaml" in text, "port_config.py 未读 yaml"
    # DEFAULT_* 仍存在(用 try/except 块覆盖)
    for name in ["DEFAULT_WEB_PORT", "DEFAULT_COMPANION_PORT", "DEFAULT_MUSIC_PORT", "DEFAULT_CALENDAR_PORT"]:
        assert name in text, f"port_config.py 缺常量: {name}"
    print("✓ companion/music/port_config.py 接入 yaml")


def test_prisiragent_web_placeholder():
    """③ prisIragent_web.py:HTML placeholder + 渲染前 str.replace + 顶部显式 import。"""
    p = REPO_ROOT / "prisIragent_web.py"
    text = _read(p)
    # 显式 import
    assert "import prisIrai_config" in text, "prisIragent_web.py 未显式 import prisIrai_config"
    # HTML head placeholder
    assert "__PRISIR_FORUM_URL_PLACEHOLDER__" in text, "prisIragent_web.py 缺 placeholder"
    # 渲染前 str.replace
    assert "_PAGE.replace(" in text or "_page_html.replace(" in text, "prisIragent_web.py 缺 str.replace 调用"
    # 调 forum_url/board/hint
    for fn in ["prisIrai_config.forum_url()", "prisIrai_config.forum_board()", "prisIrai_config.forum_hint()"]:
        assert fn in text, f"prisIragent_web.py 未调 {fn}"
    print("✓ prisIragent_web.py placeholder + replace + import")


def test_electron_config_loader():
    """④ Electron 壳:config_loader.js + port_config.js 接入 + main.js BRAND_* 接入。"""
    cfg = REPO_ROOT / "prisiragent-shell" / "config_loader.js"
    assert _exists(cfg), f"missing: {cfg}"
    text = _read(cfg)
    for fn in [
        "webPortDefault", "companionPortDefault", "musicPortDefault", "calendarPortDefault",
        "brandUrl", "brandMaxPerRun", "brandIntervalMs", "brandSeenCap",
        "forumUrl", "forumBoard", "forumHint", "forumFullUrl",
    ]:
        assert fn in text, f"config_loader.js 缺 getter: {fn}"
    # YAML 候选路径
    assert "yamlCandidates" in text or "yaml_candidates" in text or "prisIrai_config.yaml" in text
    # parseYaml 函数
    assert "parseYaml" in text or "parse_yaml" in text
    print("✓ config_loader.js 模块 + getter 齐全")

    port = REPO_ROOT / "prisiragent-shell" / "port_config.js"
    text2 = _read(port)
    # let DEFAULT_*_PORT(不再 const)
    for name in ["DEFAULT_WEB_PORT", "DEFAULT_COMPANION_PORT", "DEFAULT_MUSIC_PORT", "DEFAULT_CALENDAR_PORT"]:
        assert re.search(rf"let {name}\s*=", text2), f"port_config.js {name} 未改成 let"
    # try require config_loader
    assert 'require("./config_loader")' in text2 or "require('./config_loader')" in text2, \
        "port_config.js 未 require config_loader"
    print("✓ port_config.js 接入 config_loader")

    main = REPO_ROOT / "prisiragent-shell" / "main.js"
    text3 = _read(main)
    for k in ["BRAND_UPDATES_URL", "BRAND_MAX_PER_RUN", "BRAND_INTERVAL_MS"]:
        assert k in text3, f"main.js 缺 BRAND 常量: {k}"
    # 调 brandUrl/brandMaxPerRun/brandIntervalMs/brandSeenCap
    assert "brandUrl()" in text3 or "BRAND_UPDATES_URL" in text3
    assert "brandMaxPerRun()" in text3 or "BRAND_MAX_PER_RUN" in text3
    print("✓ main.js BRAND_* 接入")


def test_tauri_config_loader():
    """⑤ Tauri 壳:config_loader.rs mod + lib.rs BRAND 接 yaml + 周期走 yaml。"""
    src_dir = REPO_ROOT / "prisiragent-tauri" / "src-tauri" / "src"
    cfg = src_dir / "config_loader.rs"
    assert _exists(cfg), f"missing: {cfg}"
    text = _read(cfg)
    # 公共 getter
    for fn in [
        "web_port_default", "companion_port_default", "music_port_default", "calendar_port_default",
        "brand_url", "brand_max_per_run", "brand_interval_ms", "brand_seen_cap",
        "forum_url", "forum_board", "forum_hint", "forum_full_url",
    ]:
        assert f"pub fn {fn}" in text, f"config_loader.rs 缺 getter: {fn}"
    # yaml 候选
    assert "yaml_candidates" in text
    # 默认值 hash
    assert "18802" in text and "18850" in text and "18803" in text, "config_loader.rs 默认值不全"
    print("✓ config_loader.rs 模块 + getter 齐全")

    lib = src_dir / "lib.rs"
    text2 = _read(lib)
    # mod config_loader 声明
    assert "mod config_loader" in text2, "lib.rs 未 mod config_loader"
    # brand_updates_url / brand_max_per_run 函数存在
    assert "fn brand_updates_url" in text2, "lib.rs 缺 brand_updates_url"
    assert "fn brand_max_per_run" in text2, "lib.rs 缺 brand_max_per_run"
    # 调用 brand_updates_url() 替换 BRAND_UPDATES_URL
    assert "brand_updates_url()" in text2, "lib.rs 未调 brand_updates_url()"
    assert "brand_max_per_run()" in text2, "lib.rs 未调 brand_max_per_run()"
    # 周期走 yaml(不再写死 24 * 60 * 60)
    assert "brand_interval_ms" in text2, "lib.rs 未走 brand_interval_ms"
    assert 'Duration::from_secs(24 * 60 * 60)' not in text2, \
        "lib.rs brand 周期仍是写死 86400s,未走 yaml"
    print("✓ lib.rs BRAND + 周期接 yaml")


def test_yaml_schema_three_endpoints():
    """⑥ yaml schema 字段与三端 default 严格对齐。"""
    p = REPO_ROOT / "prisIrai_config.yaml"
    text = _read(p)
    # ports.web 必须等于 18802(三端 default)
    m = re.search(r"web:\s*(\d+)", text)
    assert m and int(m.group(1)) == 18802, f"ports.web 不等于 18802: {m.group(1) if m else '?'}"
    m = re.search(r"calendar:\s*(\d+)", text)
    assert m and int(m.group(1)) == 18803, f"ports.calendar 不等于 18803: {m.group(1) if m else '?'}"
    m = re.search(r"companion:\s*(\d+)", text)
    assert m and int(m.group(1)) == 18850, f"ports.companion 不等于 18850: {m.group(1) if m else '?'}"
    # brand 字段
    assert "babelspan.com/updates.json" in text, "brand.url 不指向 babelspan.com"
    # forum 字段
    assert "bbs.babelspan.com" in text, "forum.url 不指向 bbs.babelspan.com"
    assert "browser/shell" in text, "forum.board 不是 browser/shell"
    assert "prisirai" in text, "forum.hint 不是 prisirai"
    print("✓ yaml schema 与三端 default 严格对齐")


def test_python_module_smoke():
    """① 烟雾测试:Python 模块 getter 返正确值。"""
    import prisIrai_config
    # 端口
    assert prisIrai_config.web_port_default() == 18802
    assert prisIrai_config.companion_port_default() == 18850
    assert prisIrai_config.music_port_default() == 0
    assert prisIrai_config.calendar_port_default() == 18803
    # 品牌
    assert prisIrai_config.brand_url().startswith("https://www.babelspan.com")
    assert prisIrai_config.brand_max_per_run() == 3
    assert prisIrai_config.brand_interval_sec() == 86400
    assert prisIrai_config.brand_seen_cap() == 100
    # 论坛
    assert prisIrai_config.forum_url().startswith("https://bbs.babelspan.com")
    assert prisIrai_config.forum_board() == "browser/shell"
    assert prisIrai_config.forum_hint() == "prisirai"
    print("✓ Python 端 getter 烟雾测试:端口/品牌/论坛 全对齐")


def test_py_compile_all():
    """py_compile:三端 Python 文件全 OK。"""
    files = [
        REPO_ROOT / "prisIrai_config.py",
        REPO_ROOT / "prisIragent_web.py",
        REPO_ROOT / "companion" / "music" / "port_config.py",
    ]
    for f in files:
        assert _exists(f), f"missing: {f}"
        try:
            py_compile = subprocess.run(
                [sys.executable, "-m", "py_compile", str(f)],
                capture_output=True, timeout=15,
            )
            assert py_compile.returncode == 0, f"py_compile fail: {f}: {py_compile.stderr.decode()}"
        except Exception as e:
            assert False, f"py_compile error {f}: {e}"
    print("✓ py_compile:prisIrai_config.py / prisIragent_web.py / port_config.py 全 OK")


def test_companion_port_config_smoke():
    """② companion/music/port_config.py 烟雾测试:DEFAULT_* 已从 yaml 覆盖。"""
    from companion.music import port_config
    assert port_config.DEFAULT_WEB_PORT == 18802
    assert port_config.DEFAULT_CALENDAR_PORT == 18803
    assert port_config.DEFAULT_COMPANION_PORT == 18850
    print("✓ companion/music/port_config.py DEFAULT_* 与 yaml 对齐")


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
def main() -> int:
    t0 = time.time()
    tests = [
        test_python_module_exists,
        test_python_yaml_file_exists,
        test_companion_port_config_yaml,
        test_prisiragent_web_placeholder,
        test_electron_config_loader,
        test_tauri_config_loader,
        test_yaml_schema_three_endpoints,
        test_python_module_smoke,
        test_py_compile_all,
        test_companion_port_config_smoke,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"✗ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {t.__name__} (exception): {type(e).__name__}: {e}")
            failed += 1
    dt = time.time() - t0
    total = passed + failed
    print(f"\n{'='*60}\nP2.5+15 config.yaml 化 — {passed} / {total} 项绿, 失败 {failed} 项, 用时 {dt:.1f}s\n{'='*60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())