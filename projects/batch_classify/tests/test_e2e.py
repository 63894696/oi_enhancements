#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# test_e2e.py — M3.69 batch_classify 端到端测试(2026-09-24)
#
# 目的:
#   - 验证 main.py 能跑通 + 输出 CSV
#   - 用 tempfile 造 3 个测试文件,跑 disk_cleanup spec
#   - 不依赖外部网络(LoRA 已下好)
#
# 跑法:
#   python tests/test_e2e.py
#
# 设计:
#   - 用 Python tempfile.mkdtemp 造隔离目录(测试结束自动清理)
#   - 写 3 个文件:.log / .tmp / .old
#   - 跑 main.py --root <dir> --spec disk_cleanup --format csv --output <csv>
#   - 验证 CSV 头有 'top_risk' 列 + ≥ 1 行数据
#
#   故意不验证具体 risk 类别(LoRA 输出会随模型版本漂移),
#   只验证 shape / 字段 / 不报错 / CSV 可解析。
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_MAIN = str(_PROJECT / "main.py")
_REPO = _PROJECT.parent.parent  # oi_enhancements
_COMPANION = _REPO / "companion"

# companion/ 在 sys.path 里(adapter_registry 需要)
ENV = {
    **__import__("os").environ,
    "PYTHONPATH": f"{_COMPANION};{_PROJECT}",
}


def _run_main(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """跑 main.py 子进程,捕获 stdout / stderr。"""
    return subprocess.run(
        [sys.executable, _MAIN, *args],
        capture_output=True, text=True, encoding="utf-8",
        env=ENV, timeout=timeout,
    )


def test_dry_run() -> None:
    """dry-run 模式:--root + --spec disk_cleanup + --limit 5 + --dry-run"""
    with tempfile.TemporaryDirectory(prefix="bc_dryrun_") as tmp:
        tmp_path = Path(tmp)
        # 造 5 个测试文件
        for i in range(5):
            (tmp_path / f"test_{i}.log").write_text(
                f"dummy content {i}", encoding="utf-8")

        print(f"[test_dry_run] tmp={tmp_path}")
        r = _run_main(
            "--root", str(tmp_path),
            "--spec", "disk_cleanup",
            "--limit", "5",
            "--dry-run",
        )
        print(f"[test_dry_run] rc={r.returncode}")
        print(f"[test_dry_run] stderr (tail 10):")
        for line in r.stderr.strip().splitlines()[-10:]:
            print(f"  | {line}")

        assert r.returncode == 0, f"非零退出: {r.returncode}\nstderr:\n{r.stderr}"
        # dry-run 应该产出 5 行 CSV
        assert "top_risk" in r.stdout, "CSV 缺表头 top_risk"
        rows = list(csv.DictReader(io.StringIO(r.stdout)))
        assert len(rows) == 5, f"dry-run 应 5 行,实得 {len(rows)}"
        # 字段验证
        for row in rows:
            for key in ["item_id", "spec", "safe", "low", "medium", "high",
                        "critical", "top_risk", "action"]:
                assert key in row, f"CSV 缺字段: {key}"
        print(f"[test_dry_run] PASSED ({len(rows)} 行)")


def test_csv_output() -> None:
    """写 CSV 到文件 + 验证内容。"""
    with tempfile.TemporaryDirectory(prefix="bc_csv_") as tmp:
        tmp_path = Path(tmp)
        # 造 3 个测试文件(每个有不同的扩展名 / 大小 / 年龄)
        (tmp_path / "app.log").write_text("dummy log\n" * 100, encoding="utf-8")
        (tmp_path / "old.tmp").write_text("x" * 1024, encoding="utf-8")
        (tmp_path / "data.bin").write_text("binary stuff", encoding="utf-8")

        out_csv = tmp_path / "report.csv"
        print(f"[test_csv_output] tmp={tmp_path} out={out_csv}")
        r = _run_main(
            "--root", str(tmp_path),
            "--spec", "disk_cleanup",
            "--output", str(out_csv),
            "--format", "csv",
        )
        print(f"[test_csv_output] rc={r.returncode}")
        for line in r.stderr.strip().splitlines()[-5:]:
            print(f"  | {line}")

        assert r.returncode == 0, f"非零退出: {r.returncode}\nstderr:\n{r.stderr}"
        assert out_csv.exists(), f"CSV 未生成: {out_csv}"

        # 读 CSV
        text = out_csv.read_text(encoding="utf-8")
        rows = list(csv.DictReader(io.StringIO(text)))
        assert len(rows) >= 1, f"CSV 应 ≥ 1 行,实得 {len(rows)}\n{text[:500]}"
        # 5 类分布字段
        required = {"safe", "low", "medium", "high", "critical", "top_risk",
                    "action", "spec"}
        missing = required - set(rows[0].keys())
        assert not missing, f"CSV 缺字段: {missing}"
        # 概率列应可解析为 float
        for row in rows:
            for k in ["safe", "low", "medium", "high", "critical"]:
                v = float(row[k])
                assert 0.0 <= v <= 1.0 + 1e-6, f"{k} 概率越界: {v}"
        print(f"[test_csv_output] PASSED ({len(rows)} 行 CSV)")


def test_text_safety() -> None:
    """--text + --spec safety"""
    print(f"[test_text_safety]")
    r = _run_main(
        "--text", "rm -rf C:/Windows",
        "--spec", "safety",
    )
    print(f"[test_text_safety] rc={r.returncode}")
    for line in r.stderr.strip().splitlines()[-5:]:
        print(f"  | {line}")

    assert r.returncode == 0, f"非零退出: {r.returncode}\nstderr:\n{r.stderr}"
    assert "top_risk" in r.stdout, "CSV 缺 top_risk"
    rows = list(csv.DictReader(io.StringIO(r.stdout)))
    assert len(rows) == 1, f"text 模式应 1 行,实得 {len(rows)}"
    row = rows[0]
    # safety spec 不带 action 字段,但 top_risk 必有值(或 parse_fail)
    assert row["top_risk"] in {"safe", "low", "medium", "high", "critical",
                                "parse_fail"}, \
        f"top_risk 非法: {row['top_risk']}"
    print(f"[test_text_safety] PASSED (risk={row['top_risk']})")


def test_help() -> None:
    """--help 应不报错"""
    print(f"[test_help]")
    r = _run_main("--help")
    assert r.returncode == 0, f"--help 应 0 退出: {r.returncode}"
    assert "batch_classify" in r.stdout.lower(), "help 缺程序名"
    print(f"[test_help] PASSED")


def test_list_specs() -> None:
    """--list 应输出所有 spec"""
    print(f"[test_list_specs]")
    r = _run_main("--list")
    assert r.returncode == 0, f"--list 应 0 退出: {r.returncode}"
    # 至少应列出 disk_cleanup / tempfile / safety 这些常驻 spec
    for s in ["safety", "tempfile", "disk_cleanup"]:
        assert s in r.stdout, f"--list 缺 spec: {s}"
    print(f"[test_list_specs] PASSED")


def test_markdown_output() -> None:
    """Markdown 输出"""
    with tempfile.TemporaryDirectory(prefix="bc_md_") as tmp:
        tmp_path = Path(tmp)
        for i in range(3):
            (tmp_path / f"file_{i}.log").write_text("content", encoding="utf-8")
        out_md = tmp_path / "report.md"
        print(f"[test_markdown_output] tmp={tmp_path}")
        r = _run_main(
            "--root", str(tmp_path),
            "--spec", "disk_cleanup",
            "--output", str(out_md),
            "--format", "md",
        )
        assert r.returncode == 0, f"md 退出: {r.returncode}\nstderr:\n{r.stderr}"
        assert out_md.exists(), "md 文件未生成"
        text = out_md.read_text(encoding="utf-8")
        assert "# Batch Classify Report" in text, "md 缺主标题"
        assert "5 类分布" in text, "md 缺 5 类分布表"
        print(f"[test_markdown_output] PASSED ({len(text)} 字符)")


def test_multi_spec() -> None:
    """多 spec 联合:--specs disk_cleanup,tempfile"""
    with tempfile.TemporaryDirectory(prefix="bc_multi_") as tmp:
        tmp_path = Path(tmp)
        for i in range(3):
            (tmp_path / f"file_{i}.tmp").write_text("temp", encoding="utf-8")
        print(f"[test_multi_spec] tmp={tmp_path}")
        r = _run_main(
            "--root", str(tmp_path),
            "--specs", "disk_cleanup,tempfile",
            "--limit", "3",
        )
        print(f"[test_multi_spec] rc={r.returncode}")
        assert r.returncode == 0, f"multi 退出: {r.returncode}\nstderr:\n{r.stderr}"
        rows = list(csv.DictReader(io.StringIO(r.stdout)))
        assert len(rows) == 3, f"multi 应 3 行,实得 {len(rows)}"
        # spec 字段应是 disk_cleanup+tempfile(merge 后)
        for row in rows:
            assert "+" in row["spec"], f"multi merge 失败: {row['spec']}"
        print(f"[test_multi_spec] PASSED ({len(rows)} 行)")


def main() -> int:
    tests = [
        test_help,
        test_list_specs,
        test_dry_run,
        test_csv_output,
        test_markdown_output,
        test_multi_spec,
        test_text_safety,
    ]
    passed = 0
    failed = 0
    for t in tests:
        print(f"\n=== {t.__name__} ===")
        try:
            t()
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {t.__name__}: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n=== 总结 ===")
    print(f"通过 {passed}/{len(tests)},失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())