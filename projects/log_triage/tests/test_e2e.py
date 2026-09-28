#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# tests/test_e2e.py — M3.69 log_triage e2e 验证(2026-09-24)
#
# 跑:
#   python tests/test_e2e.py
#
# 流程:
#   1. 造 50 行 fake .log(JSONL + 纯文本混合, 含各种 severity)
#   2. 在**同一进程内**依次跑 5 个场景(共享 LoRA 缓存,_LOADED 不重载)
#   3. 验证 --help / text / json / md / --min-severity 全部正确
#
# 设计:
#   - 进程内调 main()(不用 subprocess)— 因 CPU 推理 15s/事件,subprocess 重启模型会拖到 1h+
#   - 用 monkey-patched sys.argv 传 CLI 参数
#   - 不依赖 Windows Event Log(用 .log 文件流)
from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import sys
import tempfile
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
_MAIN = _PROJECT / "main.py"

# 确保 src + companion 在 path
sys.path.insert(0, str(_PROJECT / "src"))
sys.path.insert(0, str(_PROJECT.parent.parent / "companion"))

# 避免与同名顶层 main.py(本机的 gitpython dev tool)冲突 → 按路径加载
_spec_main = importlib.util.spec_from_file_location("log_triage_main", _MAIN)
_mod_main = importlib.util.module_from_spec(_spec_main)
_spec_main.loader.exec_module(_mod_main)
cli_main = _mod_main.main
build_parser = _mod_main.build_parser

# 50 行 fake 日志:30 条 info + 10 warn + 8 error + 2 critical
_FAKE_LINES = []
for i in range(30):
    _FAKE_LINES.append({
        "ts": f"2026/09/24 1{i % 10}:00:00",
        "level": "info",
        "content": f"User login successful. user_id={1000 + i}",
    })
for i in range(10):
    _FAKE_LINES.append({
        "ts": f"2026/09/24 12:{10 + i}:00",
        "level": "warning",
        "content": f"Connection slow. retry_count={i}",
    })
for i in range(8):
    _FAKE_LINES.append({
        "ts": f"2026/09/24 13:{10 + i}:00",
        "level": "error",
        "content": f"Database connection failed: timeout after 30s. attempt={i + 1}",
    })
for i in range(2):
    _FAKE_LINES.append({
        "ts": f"2026/09/24 14:0{i}:00",
        "level": "critical",
        "content": f"KERNEL PANIC: out of memory. subsystem=mem_alloc pid={4000 + i}",
    })


def _build_fake_log() -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="log_triage_e2e_"))
    log_path = tmpdir / "fake.log"
    with log_path.open("w", encoding="utf-8") as f:
        for line in _FAKE_LINES[:30]:
            f.write(json.dumps({
                "ts": line["ts"], "level": line["level"],
                "msg": line["content"],
            }, ensure_ascii=False) + "\n")
        for line in _FAKE_LINES[30:]:
            f.write(f"{line['ts']} [{line['level'].upper()}] {line['content']}\n")
    return log_path


def _run_cli(args: list[str], output_path: Path | None = None) -> tuple[int, str, str]:
    """进程内跑 main(),返 (exit_code, stdout, stderr)。"""
    if output_path is not None:
        args = args + ["--output", str(output_path)]
    saved_argv = sys.argv
    sys.argv = ["main.py"] + args
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                rc = cli_main(args)
            except SystemExit as e:
                rc = int(e.code) if e.code is not None else 0
            except Exception as e:  # noqa: BLE001
                err_buf.write(f"\n[exception] {type(e).__name__}: {e}\n")
                rc = 99
    finally:
        sys.argv = saved_argv
    return rc, out_buf.getvalue(), err_buf.getvalue()


def test_help() -> bool:
    print("=" * 60)
    print("[1/5] --help")
    print("=" * 60)
    # --help argparse 会 SystemExit(0) — 用 build_parser 直接验证
    ap = build_parser()
    h = ap.format_help()
    expected = ["--channel", "--file", "--since", "--spec",
                "--min-severity", "--top", "--format", "--output"]
    missing = [k for k in expected if k not in h]
    if missing:
        print(f"  ✗ 缺少参数: {missing}")
        return False
    print(f"  ✓ --help OK ({len(h.splitlines())} 行)")
    return True


def test_text_output(log_path: Path) -> bool:
    print("=" * 60)
    print("[2/5] text 输出")
    print("=" * 60)
    t0 = time.time()
    rc, out, err = _run_cli(["--file", str(log_path), "--format", "text",
                             "--since", "24h", "--spec", "log"])
    print(f"  耗时 {time.time()-t0:.1f}s, exit={rc}")
    if rc != 0:
        print(f"  ✗ stderr={err[:500]}")
        return False
    for must in ["5 类分布", "待办", "critical", "safe"]:
        if must not in out:
            print(f"  ✗ 输出缺 {must!r}")
            return False
    print(f"  ✓ text OK ({len(out.splitlines())} 行)")
    print("  --- 前 12 行 ---")
    for line in out.splitlines()[:12]:
        print(f"  {line}")
    return True


def test_json_output(log_path: Path, out_path: Path) -> bool:
    print("=" * 60)
    print("[3/5] JSON 输出")
    print("=" * 60)
    t0 = time.time()
    rc, out, err = _run_cli(["--file", str(log_path), "--format", "json",
                             "--since", "24h", "--spec", "log_conf",
                             "--output", str(out_path)])
    print(f"  耗时 {time.time()-t0:.1f}s, exit={rc}")
    if rc != 0:
        print(f"  ✗ stderr={err[:500]}")
        return False
    if not out_path.exists():
        print(f"  ✗ 输出文件未写出: {out_path}")
        return False
    try:
        d = json.loads(out_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  ✗ JSON parse 失败: {e}")
        return False
    for must in ["spec", "total", "distribution", "events",
                 "min_severity", "since"]:
        if must not in d:
            print(f"  ✗ 缺字段: {must}")
            return False
    if d["total"] != 50:
        print(f"  ✗ total 不对: {d['total']} (期望 50)")
        return False
    dist = d["distribution"]
    for sev in ["safe", "low", "medium", "high", "critical"]:
        if sev not in dist:
            print(f"  ✗ 分布缺 risk: {sev}")
            return False
    n_classified = sum(dist.values())
    if n_classified + d["parse_fail"] != 50:
        print(f"  ✗ 分布+parse_fail != 50 ({n_classified}+{d['parse_fail']})")
        return False
    print(f"  ✓ JSON OK: total={d['total']} dist={dist} "
          f"parse_fail={d['parse_fail']} events={len(d['events'])}")
    return True


def test_markdown_output(log_path: Path, out_path: Path) -> bool:
    print("=" * 60)
    print("[4/5] Markdown 输出")
    print("=" * 60)
    t0 = time.time()
    rc, out, err = _run_cli(["--file", str(log_path), "--format", "md",
                             "--since", "24h", "--spec", "log_conf",
                             "--output", str(out_path)])
    print(f"  耗时 {time.time()-t0:.1f}s, exit={rc}")
    if rc != 0:
        print(f"  ✗ stderr={err[:500]}")
        return False
    if not out_path.exists():
        print(f"  ✗ 输出文件未写出: {out_path}")
        return False
    text = out_path.read_text(encoding="utf-8")
    for must in ["# Log Triage Report", "5 类分布", "| 等级 |"]:
        if must not in text:
            print(f"  ✗ Markdown 缺 {must!r}")
            return False
    print(f"  ✓ Markdown OK ({len(text.splitlines())} 行, {len(text)} 字节)")
    print(f"  path: {out_path}")
    return True


def test_min_severity(log_path: Path) -> bool:
    print("=" * 60)
    print("[5/5] --min-severity=high 过滤")
    print("=" * 60)
    t0 = time.time()
    rc, out, err = _run_cli(["--file", str(log_path), "--format", "json",
                             "--since", "24h", "--spec", "log_conf",
                             "--min-severity", "high"])
    print(f"  耗时 {time.time()-t0:.1f}s, exit={rc}")
    if rc != 0:
        print(f"  ✗ stderr={err[:500]}")
        return False
    try:
        d = json.loads(out)
    except json.JSONDecodeError as e:
        print(f"  ✗ JSON parse 失败: {e}")
        return False
    events = d.get("events", [])
    bad = [e for e in events
           if e.get("risk") not in ("high", "critical")]
    if bad:
        print(f"  ✗ 过滤漏掉: {[(e['risk'], e.get('source')) for e in bad[:5]]}")
        return False
    print(f"  ✓ min-severity=high 生效,events={len(events)},"
          f" risks={set(e.get('risk') for e in events)}")
    return True


def main() -> int:
    log_path = _build_fake_log()
    print(f"[setup] fake.log @ {log_path}")
    print(f"        共 50 行(30 JSONL info + 10 warn + 8 error + 2 critical)")
    print()

    out_dir = Path(tempfile.mkdtemp(prefix="log_triage_out_"))
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"

    t_total = time.time()
    results = [
        ("help", test_help()),
        ("text", test_text_output(log_path)),
        ("json", test_json_output(log_path, json_path)),
        ("md", test_markdown_output(log_path, md_path)),
        ("min-severity", test_min_severity(log_path)),
    ]
    print()
    print("=" * 60)
    print(f"总耗时: {time.time()-t_total:.1f}s")
    print("结果汇总:")
    for name, ok in results:
        print(f"  {'✓' if ok else '✗'} {name}")
    print("=" * 60)
    print(f"JSON 输出:   {json_path}")
    print(f"Markdown:    {md_path}")
    print(f"fake.log:    {log_path}")

    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())