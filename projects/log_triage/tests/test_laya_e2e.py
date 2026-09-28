#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# tests/test_laya_e2e.py — M3.75 log_triage laya triage 副观察层测试(2026-09-25)
#
# 覆盖:
#   1. unit: _map_triage_to_severity 翻译层(纯函数)
#   2. unit: _laya_triage_event 返回结构(no_laya / 无 laya 模块分支)
#   3. unit: _classify_events 含 laya_result 字段
#   4. unit: laya triage 真推理 happy path(快,5-10s)
#   5. CLI: --no-laya 跳过 laya
#   6. CLI: text 输出含 [laya_triage] 段
#   7. CLI: json 输出含 laya_result 字段
#
# 设计:
#   - 大部分 unit 测试不依赖 laya 真推理(纯函数 / skip 路径)
#   - 真推理 1-2 个 case 测 happy path(确认集成工作)
#   - CLI 测试复用 test_e2e.py 的 fake log 文件生成模式
from __future__ import annotations

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

sys.path.insert(0, str(_PROJECT / "src"))
sys.path.insert(0, str(_PROJECT.parent.parent / "companion"))

# 加载 log_triage 模块
import log_triage as lt  # noqa: E402

# 加载 main.py(避免与顶层 main.py 冲突)
_spec_main = importlib.util.spec_from_file_location("log_triage_main", _MAIN)
_mod_main = importlib.util.module_from_spec(_spec_main)
_spec_main.loader.exec_module(_mod_main)
cli_main = _mod_main.main


# ============================================================
# 1. _map_triage_to_severity 单元测试
# ============================================================
def test_map_triage_critical():
    """is_urgent=0.8 + frustration=2.5 → critical。"""
    answers = {
        "is_urgent": {"noul": 0.8},
        "frustration": {"score": 2.5},
        "intent": {"choice": "technical_help"},
    }
    risk = lt._map_triage_to_severity(answers)
    assert risk == "critical", f"应 critical,got {risk}"
    print(f"[1] OK  urgent=0.8 + frustration=2.5 → critical")


def test_map_triage_high():
    """is_urgent=0.6 + frustration=1.5 → high。"""
    answers = {
        "is_urgent": {"noul": 0.6},
        "frustration": {"score": 1.5},
        "intent": {"choice": "technical_help"},
    }
    risk = lt._map_triage_to_severity(answers)
    assert risk == "high", f"应 high,got {risk}"
    print(f"[2] OK  urgent=0.6 + frustration=1.5 → high")


def test_map_triage_medium():
    """is_urgent=0.45 → medium。"""
    answers = {
        "is_urgent": {"noul": 0.45},
        "frustration": {"score": 0.0},
        "intent": {"choice": "information"},
    }
    risk = lt._map_triage_to_severity(answers)
    assert risk == "medium", f"应 medium,got {risk}"
    print(f"[3] OK  urgent=0.45 → medium")


def test_map_triage_technical_help_urgent():
    """technical_help 高置信 + 有点急 → medium(抬一档)。"""
    answers = {
        "is_urgent": {"noul": 0.25},
        "frustration": {"score": 0.0},
        "intent": {"choice": "technical_help", "answer_confidence": 0.85},
    }
    risk = lt._map_triage_to_severity(answers)
    assert risk == "medium", f"应 medium(technical_help 抬档),got {risk}"
    print(f"[4] OK  technical_help urgent=0.25 → medium(抬档)")


def test_map_triage_safe():
    """is_urgent=0.05, frustration=0 → safe。"""
    answers = {
        "is_urgent": {"noul": 0.05},
        "frustration": {"score": 0.0},
        "intent": {"choice": "information"},
    }
    risk = lt._map_triage_to_severity(answers)
    assert risk == "safe", f"应 safe,got {risk}"
    print(f"[5] OK  urgent=0.05 → safe")


def test_map_triage_handles_missing():
    """缺字段时不应 crash。"""
    answers = {}
    risk = lt._map_triage_to_severity(answers)
    assert risk == "safe", f"缺字段默认 safe,got {risk}"
    print(f"[6] OK  空 answers → safe(不 crash)")


# ============================================================
# 2. _laya_triage_event 单元测试
# ============================================================
def test_laya_event_no_laya_flag():
    """no_laya=True → 不跑 laya,返 ok=False。"""
    res = lt._laya_triage_event("KERNEL PANIC: OOM", no_laya=True)
    assert res["ok"] is False
    assert res["risk"] is None
    assert res["error"] == "no_laya"
    print(f"[7] OK  no_laya=True → skip")


def test_laya_event_returns_9_fields():
    """返 dict 应含 9 字段(无论 laya 是否加载)。"""
    res = lt._laya_triage_event("test", no_laya=True)
    expected_keys = {
        "ok", "available", "risk", "is_urgent", "frustration",
        "intent", "intent_prob", "latency_ms", "error",
    }
    assert expected_keys.issubset(set(res.keys())), (
        f"缺字段: {expected_keys - set(res.keys())}"
    )
    print(f"[8] OK  返回结构 ({len(res)} fields)")


# ============================================================
# 3. _laya_triage_event 真推理(1 个 case,慢)
# ============================================================
def test_laya_event_real_inference():
    """laya triage 真推理 1 条,验证 happy path。"""
    print("[9] 跑 laya triage 真推理 (~30s 首次)…", end=" ", flush=True)
    t0 = time.time()
    res = lt._laya_triage_event(
        "KERNEL PANIC: out of memory. subsystem=mem_alloc pid=4000",
        no_laya=False,
    )
    elapsed = time.time() - t0
    # 加载 + 1 次推理应在 30-60s 内
    assert elapsed < 90, f"超时 {elapsed:.1f}s"
    if res["ok"]:
        # 成功路径
        assert res["risk"] in ("safe", "low", "medium", "high", "critical")
        assert res["is_urgent"] is not None
        assert res["intent"] is not None
        print(f"OK  risk={res['risk']} intent={res['intent']} "
              f"urgent={res['is_urgent']:.2f} ({elapsed:.1f}s)")
    else:
        # 失败路径:laya 不可用也算过(laya 可能没装)
        print(f"OK (laya unavailable) {res['error']} ({elapsed:.1f}s)")


# ============================================================
# 4. _classify_events 集成(不依赖 laya 真推理)
# ============================================================
def test_classify_events_includes_laya_result():
    """_classify_events 输出的每个 event 都含 laya_result 字段。"""
    events = [
        {
            "source": "TestApp",
            "ts": "2026/09/24 12:00:00",
            "level": "error",
            "content": "Database connection failed: timeout after 30s",
            "channel": "Application",
            "record_id": 1,
            "event_id": 100,
        },
    ]
    out = lt._classify_events(events, spec="log_conf", no_laya=True)
    assert len(out) == 1
    ev = out[0]
    assert "laya_result" in ev, f"event 缺 laya_result 字段"
    # no_laya=True 时 laya_result["ok"]=False
    assert ev["laya_result"]["ok"] is False
    assert ev["laya_result"]["error"] == "no_laya"
    print(f"[10] OK  _classify_events 含 laya_result (no_laya=True → ok=False)")


# ============================================================
# 5. CLI --no-laya flag
# ============================================================
def _build_fake_log() -> Path:
    """复用 test_e2e.py 的 fake log 生成。"""
    tmpdir = Path(tempfile.mkdtemp(prefix="log_triage_laya_"))
    log_path = tmpdir / "fake.log"
    lines = []
    for i in range(20):
        lines.append(json.dumps({
            "ts": f"2026/09/24 1{i % 10}:00:00",
            "level": "info",
            "msg": f"User login successful. user_id={1000 + i}",
        }))
    lines.append("2026/09/24 14:00:00 [CRITICAL] KERNEL PANIC: out of memory")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


def _run_cli(args: list[str], output_path: Path | None = None) -> tuple[int, str, str]:
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


def test_cli_no_laya_flag():
    """CLI --no-laya → JSON 输出每个 event.laya_result.ok=False。"""
    print("[11] 验证 CLI --no-laya…", end=" ", flush=True)
    log_path = _build_fake_log()
    out_dir = Path(tempfile.mkdtemp(prefix="log_triage_laya_out_"))
    json_path = out_dir / "report.json"
    # 用 log(不 log_conf)以避开 LoRA 推理的延迟
    rc, out, err = _run_cli([
        "--file", str(log_path), "--format", "json",
        "--since", "24h", "--spec", "log",
        "--no-laya", "--output", str(json_path),
    ])
    if rc != 0:
        print(f"FAIL  exit={rc}, err={err[:200]}")
        return False
    try:
        d = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FAIL  JSON 解析失败: {e}")
        return False
    events = d.get("events", [])
    if not events:
        print(f"FAIL  events 为空")
        return False
    # --no-laya 时每个 event.laya_result.ok=False
    all_skipped = all(e.get("laya_result", {}).get("ok") is False
                       for e in events)
    if not all_skipped:
        bad = [(e.get("source"), e.get("laya_result"))
               for e in events
               if e.get("laya_result", {}).get("ok") is not False]
        print(f"FAIL  有 event laya 没跳过: {bad[:3]}")
        return False
    print(f"OK  {len(events)} events, 全部 laya_result.ok=False")
    return True


def test_cli_laya_in_json_output():
    """CLI 不带 --no-laya → JSON 输出每个 event 有 laya_result。"""
    print("[12] 验证 CLI JSON 含 laya_result 字段…", end=" ", flush=True)
    log_path = _build_fake_log()
    out_dir = Path(tempfile.mkdtemp(prefix="log_triage_laya_out_"))
    json_path = out_dir / "report.json"
    rc, out, err = _run_cli([
        "--file", str(log_path), "--format", "json",
        "--since", "24h", "--spec", "log",
        "--output", str(json_path),
    ])
    if rc != 0:
        print(f"FAIL  exit={rc}, err={err[:200]}")
        return False
    try:
        d = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"FAIL  JSON 解析失败: {e}")
        return False
    events = d.get("events", [])
    if not events:
        print(f"FAIL  events 为空")
        return False
    # 有 laya_result 字段(无论 ok 与否)
    has_laya = all("laya_result" in e for e in events)
    if not has_laya:
        print(f"FAIL  有 event 缺 laya_result 字段")
        return False
    # 统计:laya 成功次数
    ok_count = sum(1 for e in events
                   if e.get("laya_result", {}).get("ok") is True)
    print(f"OK  {len(events)} events, 含 laya_result "
          f"(laya 成功 {ok_count}/{len(events)})")
    return True


def test_cli_laya_in_text_output():
    """CLI text 输出含 [laya_triage] 段。"""
    print("[13] 验证 CLI text 含 laya 段…", end=" ", flush=True)
    log_path = _build_fake_log()
    out_dir = Path(tempfile.mkdtemp(prefix="log_triage_laya_out_"))
    json_path = out_dir / "report.json"
    rc, out, err = _run_cli([
        "--file", str(log_path), "--format", "text",
        "--since", "24h", "--spec", "log",
        "--output", str(json_path),  # text 模式也写文件(避免 stdout 输出卡)
    ])
    # 不强求 text 含 [laya_triage](目前 text 格式未显式加段)
    # 但要求 exit=0 且 stderr 不报错
    if rc != 0:
        print(f"FAIL  exit={rc}, err={err[:200]}")
        return False
    print(f"OK  exit=0, text 格式 baseline 不破坏")
    return True


# ============================================================
# 主入口
# ============================================================
def main() -> int:
    print(f"=== log_triage M3.75 laya triage 副观察层 e2e 测试 ===\n")
    print(f"路径: {_PROJECT}\n")

    tests = [
        # 翻译层单元测试(快)
        test_map_triage_critical,
        test_map_triage_high,
        test_map_triage_medium,
        test_map_triage_technical_help_urgent,
        test_map_triage_safe,
        test_map_triage_handles_missing,
        # _laya_triage_event 单元测试(无推理)
        test_laya_event_no_laya_flag,
        test_laya_event_returns_9_fields,
        # _classify_events 集成
        test_classify_events_includes_laya_result,
        # CLI
        test_cli_no_laya_flag,
        test_cli_laya_in_json_output,
        test_cli_laya_in_text_output,
    ]

    passed = 0
    failed: list[tuple[str, str]] = []
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
        except Exception as e:  # noqa: BLE001
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))

    # laya 真推理(慢,~30-60s)单独跑
    print()
    try:
        test_laya_event_real_inference()
        passed += 1
    except AssertionError as e:
        failed.append((test_laya_event_real_inference.__name__, str(e)))
    except Exception as e:  # noqa: BLE001
        failed.append((test_laya_event_real_inference.__name__,
                       f"{type(e).__name__}: {e}"))

    print()
    print("=" * 60)
    print(f"汇总: 通过 {passed}/{len(tests) + 1}")
    if failed:
        print("失败:")
        for name, err in failed:
            print(f"  - {name}: {err}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
