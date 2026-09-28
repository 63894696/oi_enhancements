#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_e2e.py — cleanup_suggest 端到端测试 (M3.69)

不写 unit test,直接造临时目录跑主流程验证:
1. dry-run + limit:扫前 N 文件不报错
2. JSON 输出格式合法 + 含 distribution/suggestion 字段
3. text 输出含 5 类分布
4. send2trash 真集成(装上,import ok)
5. 路径不存在友好报错
6. argparse --help 输出完整

用法:
    cd C:/Users/Administrator/oi_enhancements/projects/cleanup_suggest
    python tests/test_e2e.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.cleanup_suggest import (  # noqa: E402
    RISK_LEVELS,
    build_distribution,
    build_suggestion,
    scan_directory,
)


def _section(title: str) -> None:
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    raise AssertionError(msg)


def test_send2trash_installed():
    _section("TEST 1: send2trash 安装检查")
    try:
        from send2trash import send2trash  # noqa: F401
        _ok("send2trash 已安装")
    except ImportError as e:
        _fail(f"send2trash 未装: {e}")


def test_path_not_exists():
    _section("TEST 2: 路径不存在友好报错")
    result = subprocess.run(
        [sys.executable, str(_ROOT / "main.py"), "--root", "Z:/nope/never/here"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 2 and "路径不存在" in result.stderr:
        _ok(f"exit=2 + 友好提示\n        stderr: {result.stderr.strip()[:100]}")
    else:
        _fail(f"未报错: rc={result.returncode} stderr={result.stderr[:200]}")


def test_help():
    _section("TEST 3: --help 输出完整")
    result = subprocess.run(
        [sys.executable, str(_ROOT / "main.py"), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0 and "--root" in result.stdout \
            and "--auto-apply" in result.stdout and "--format" in result.stdout:
        _ok("help 含 --root / --auto-apply / --format")
    else:
        _fail(f"--help 不完整: rc={result.returncode}\n{result.stdout[:300]}")


def test_scan_directory_real():
    """不需要 model:只测 scan_directory + build_distribution。"""
    _section("TEST 4: scan_directory + build_distribution 纯函数")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # 造 5 个测试文件
        sizes = [
            ("a.tmp", 100),
            ("b.log", 2048),
            ("c.cache", 50),
            ("d.dll", 4096),
            ("e.txt", 10),
        ]
        for name, sz in sizes:
            (td_path / name).write_bytes(b"x" * sz)

        files = scan_directory(td_path)
        if len(files) == 5:
            _ok(f"扫到 5 个文件: {[f.name for f in files]}")
        else:
            _fail(f"期望 5 个,实际 {len(files)}")

        # 造假 verdicts 测分布统计
        from src.cleanup_suggest import FileVerdict
        verdicts = []
        for f, (_, sz) in zip(files, sizes):
            verdicts.append(FileVerdict(
                path=str(f),
                size_bytes=sz,
                age_days=10,
                risk="safe" if "tmp" in f.name or "cache" in f.name else "low",
            ))

        dist = build_distribution(verdicts)
        total_count = sum(d["count"] for d in dist.values())
        if total_count == 5:
            _ok(f"分布求和=5: {dist}")
        else:
            _fail(f"分布求和={total_count} ≠ 5")

        sug = build_suggestion(verdicts)
        if sug["auto_delete_safe_low"]["count"] == 5:
            _ok(f"建议 auto_delete_safe_low=5 文件")
        else:
            _fail(f"建议异常: {sug}")


def test_dry_run():
    """真跑模型:dry-run + limit 2,跑通(模型推理 ~50s/文件,长 timeout)。"""
    _section("TEST 5: dry-run + --limit 2")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for i in range(5):
            (td_path / f"f{i}.tmp").write_text("x" * 100)
        result = subprocess.run(
            [sys.executable, str(_ROOT / "main.py"),
             "--root", str(td_path),
             "--dry-run", "--limit", "2",
             "--no-progress"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            _ok(f"exit=0 stderr={result.stderr.strip()[:120]}")
        else:
            _fail(f"exit={result.returncode}\nstderr={result.stderr[:300]}")


def test_json_output():
    """真跑模型:JSON 输出含 distribution + suggestion(limit 3 文件,长 timeout)。"""
    _section("TEST 6: JSON 输出格式")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for name in ["a.tmp", "b.log", "c.cache"]:
            (td_path / name).write_bytes(b"x" * 1024)

        out_path = Path(td) / "report.json"
        result = subprocess.run(
            [sys.executable, str(_ROOT / "main.py"),
             "--root", str(td_path),
             "--limit", "3",
             "--format", "json",
             "--output", str(out_path),
             "--no-progress"],
            capture_output=True, text=True, timeout=450,
        )
        if result.returncode != 0:
            _fail(f"exit={result.returncode}\nstderr={result.stderr[:300]}")

        if not out_path.exists():
            _fail(f"JSON 文件未生成: {out_path}")

        try:
            data = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            _fail(f"JSON 不合法: {e}")

        for key in ("root", "scanned_files", "scanned_size_gb",
                    "distribution", "suggestion", "applied"):
            if key not in data:
                _fail(f"缺字段: {key}")
        for level in RISK_LEVELS:
            if level not in data["distribution"]:
                _fail(f"distribution 缺 {level}")
        for key in ("auto_delete_safe_low", "medium_user_confirm",
                    "skip_high_critical"):
            if key not in data["suggestion"]:
                _fail(f"suggestion 缺 {key}")

        _ok(f"JSON 合法, 扫了 {data['scanned_files']} 文件, "
            f"auto_delete={data['suggestion']['auto_delete_safe_low']['count']}")


def test_text_output():
    """text 模式:含 5 类分布字符(limit 2 文件,长 timeout)。"""
    _section("TEST 7: text 模式输出")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for i in range(3):
            (td_path / f"f{i}.tmp").write_bytes(b"x" * 512)

        result = subprocess.run(
            [sys.executable, str(_ROOT / "main.py"),
             "--root", str(td_path),
             "--limit", "2",
             "--format", "text",
             "--no-progress"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            _fail(f"exit={result.returncode}\nstderr={result.stderr[:300]}")

        out = result.stdout
        if "5 类分布" not in out:
            _fail(f"text 模式输出缺 '5 类分布':\n{out[:400]}")
        if "建议" not in out:
            _fail(f"text 模式输出缺 '建议'")
        _ok("text 模式输出含 '5 类分布' + '建议'")


def main():
    print(f"项目根: {_ROOT}")
    print(f"Python: {sys.version.split()[0]}")
    test_send2trash_installed()
    test_path_not_exists()
    test_help()
    test_scan_directory_real()
    test_dry_run()
    test_json_output()
    test_text_output()
    print(f"\n{'='*60}\n  ALL E2E TESTS PASSED\n{'='*60}")


if __name__ == "__main__":
    main()