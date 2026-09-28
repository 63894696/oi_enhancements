#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_perf_subprocess.py — M3.78 PrisirAI perf_conf_v3 子进程守护测试

覆盖:
  1. unit: perf_subprocess_runner.run_once() 返 exit code + stdout JSON 含全部字段
  2. unit: process_controller_tools._run_perf_subprocess() 解析 stdout JSON → dict
  3. unit: DEFAULT_WATCHDOG_CONFIG 含 3 个新 perf_guard 字段
  4. unit: watchdog_start_impl() 默认 cfg 写入 3 个新字段
  5. unit: _handle_perf_risk(critical + conf≥0.6) → 调 perf_guard._trigger_kill_mode
  6. unit: _handle_perf_risk(low) → 不触发(返 None)
  7. unit: _handle_perf_risk(critical + conf<0.6) → 不触发 + 写 history "perf_guard_low_conf"
  8. unit: subprocess 隔离 — 父进程 RSS 在调用前后不显著增长

设计:
  - 大部分 mock 化 perf_guard._trigger_kill_mode(避免拉 base model)
  - perf_subprocess_runner.run_once() 测试时用 monkeypatch 把 imports 替换成 fake
  - subprocess.run() 用 mock 替代真子进程,验证 stdout 解析逻辑
  - 跑速目标 ~5-10s(无 ML)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SERVER = _HERE.parent
sys.path.insert(0, str(_SERVER))

import process_controller_tools as p  # noqa: E402


# ============================================================
# 工具函数
# ============================================================
def _setup_watchdog_state(perf_guard_conf_threshold: float = 0.60,
                         perf_guard_enabled: bool = True) -> None:
    """在 _watchdog_state 写一个测试 cfg。"""
    p._watchdog_state["running"] = False
    p._watchdog_state["history"] = []
    p._watchdog_state["config"] = dict(p.DEFAULT_WATCHDOG_CONFIG)
    p._watchdog_state["config"]["perf_guard_conf_threshold"] = perf_guard_conf_threshold
    p._watchdog_state["config"]["perf_guard_enabled"] = perf_guard_enabled


def _fake_risk_result(risk: str = "critical", risk_conf: float = 0.9,
                      action: str = "alert", latency_ms: int = 5000) -> dict:
    """模拟 perf_subprocess_runner stdout JSON。"""
    return {
        "ok": True,
        "risk": risk,
        "action": action,
        "risk_conf": risk_conf,
        "action_conf": 0.9,
        "jailbreak": "no",
        "latency_ms": latency_ms,
        "parse_fail": False,
        "backend": "perf_conf_v3",
        "sample": {
            "ts": time.time(),
            "cpu": {"pct": 75.0},
            "memory": {"used_pct": 65.0},
            "net": {"nics": [{"nic": "Wi-Fi", "isup": 1}]},
            "crash": {"bugcheck_count": 1, "kp41_count": 0},
            "system": {"uptime_s": 1200.0},
        },
        "error": None,
    }


# ============================================================
# 1. unit: perf_subprocess_runner.run_once() → 返 exit code + 写 stdout JSON
# ============================================================
def test_perf_subprocess_runner_module_loads():
    """perf_subprocess_runner.py 可 import + 暴露 run_once()."""
    import perf_subprocess_runner as r
    assert hasattr(r, "run_once"), "缺 run_once()"
    print(f"[1] OK  perf_subprocess_runner.run_once() 存在")


def test_perf_subprocess_runner_run_once_emits_valid_json():
    """run_once() 调后 stdout 含 ok/risk/risk_conf 字段 + 返 exit code。"""
    import perf_subprocess_runner as r

    fake_out = {"risk": "critical", "risk_conf": 0.85, "action": "alert"}
    fake_sample = {"ts": time.time(), "cpu": {"pct": 50.0}, "memory": {"used_pct": 60.0}}

    # monkeypatch 4 个核心依赖(imports 在 run_once 函数体内,target 是源模块):
    with mock.patch.object(r, "_emit") as mock_emit:
        with mock.patch("adapter_registry.get_adapter", return_value="fake-adapter"):
            with mock.patch("classify_perf.classify_perf",
                            return_value={**fake_out, "action_conf": 0.9,
                                          "jailbreak": "no", "parse_fail": False}):
                with mock.patch("perf_collector.collect_with_event",
                                return_value=fake_sample):
                    rc = r.run_once()
                    assert rc == 0
                    assert mock_emit.call_count == 1
                    payload = mock_emit.call_args[0][0]
                    assert payload["ok"] is True
                    assert payload["risk"] == "critical"
                    assert payload["risk_conf"] == 0.85
                    assert payload["backend"] == "perf_conf_v3"
                    assert payload["error"] is None
                    assert "sample" in payload
                    print(f"[2] OK  run_once() exit=0, risk={payload['risk']} "
                          f"conf={payload['risk_conf']:.2f}")


def test_perf_subprocess_runner_run_once_returns_1_on_error():
    """adapter 加载失败 → exit code 1 + payload ok=False。"""
    import perf_subprocess_runner as r

    with mock.patch.object(r, "_emit") as mock_emit:
        # adapter 加载炸(get_adapter 在源模块 adapter_registry)
        with mock.patch("adapter_registry.get_adapter",
                        side_effect=RuntimeError("adapter missing")):
            rc = r.run_once()
            assert rc == 1
            payload = mock_emit.call_args[0][0]
            assert payload["ok"] is False
            assert "adapter 加载失败" in payload["error"]
            print(f"[3] OK  run_once() exit=1, error={payload['error'][:50]}")


# ============================================================
# 2. unit: process_controller_tools._run_perf_subprocess() 解析 stdout JSON
# ============================================================
def test_run_perf_subprocess_parses_stdout_json():
    """_run_perf_subprocess() 解析子进程 stdout JSON → dict。"""
    fake_payload = _fake_risk_result(risk="critical", risk_conf=0.88)
    fake_stdout = json.dumps(fake_payload).encode("utf-8")
    fake_result = mock.MagicMock()
    fake_result.stdout = fake_stdout
    fake_result.returncode = 0
    fake_result.stderr = b""

    with mock.patch("subprocess.run", return_value=fake_result):
        out = p._run_perf_subprocess()
        assert out is not None
        assert out["ok"] is True
        assert out["risk"] == "critical"
        assert out["risk_conf"] == 0.88
        assert out["backend"] == "perf_conf_v3"
        print(f"[4] OK  _run_perf_subprocess() 解析 stdout JSON, "
              f"risk={out['risk']} conf={out['risk_conf']}")


def test_run_perf_subprocess_returns_none_on_timeout():
    """子进程 timeout → 返 None(不抛异常)。"""
    with mock.patch("subprocess.run", side_effect=TimeoutError("timeout")):
        out = p._run_perf_subprocess()
        assert out is None
        print(f"[5] OK  _run_perf_subprocess() timeout → None")


def test_run_perf_subprocess_returns_none_on_bad_json():
    """stdout 不是 JSON → 返 None。"""
    fake_result = mock.MagicMock()
    fake_result.stdout = b"not json"
    fake_result.returncode = 0
    fake_result.stderr = b""

    with mock.patch("subprocess.run", return_value=fake_result):
        out = p._run_perf_subprocess()
        assert out is None
        print(f"[6] OK  _run_perf_subprocess() bad JSON → None")


# ============================================================
# 3. unit: DEFAULT_WATCHDOG_CONFIG 含 3 个新 perf_guard 字段
# ============================================================
def test_default_watchdog_config_has_perf_guard_fields():
    """DEFAULT_WATCHDOG_CONFIG 含 perf_guard_enabled / interval / conf_threshold。"""
    cfg = p.DEFAULT_WATCHDOG_CONFIG
    assert "perf_guard_enabled" in cfg, "缺 perf_guard_enabled"
    assert "perf_guard_interval_rounds" in cfg, "缺 perf_guard_interval_rounds"
    assert "perf_guard_conf_threshold" in cfg, "缺 perf_guard_conf_threshold"
    assert cfg["perf_guard_enabled"] is True
    assert cfg["perf_guard_interval_rounds"] == 6
    assert cfg["perf_guard_conf_threshold"] == 0.60
    print(f"[7] OK  DEFAULT_WATCHDOG_CONFIG perf_guard_enabled={cfg['perf_guard_enabled']}, "
          f"interval={cfg['perf_guard_interval_rounds']}, "
          f"conf_threshold={cfg['perf_guard_conf_threshold']}")


# ============================================================
# 4. unit: watchdog_start_impl() 默认 cfg 写入 3 个新字段
# ============================================================
def test_watchdog_start_impl_writes_perf_guard_cfg():
    """watchdog_start_impl() 在 cfg 里写入 3 个 perf_guard 字段。"""
    # monkeypatch 实际的 watchdog 启动(避免真起线程)
    with mock.patch.object(p, "_watchdog_state", new={"running": False,
                                                       "history": [],
                                                       "config": {},
                                                       "thread": None,
                                                       "stop_event": None}):
        with mock.patch("threading.Thread") as mock_thread:
            # 让 Thread.start() 不真跑
            mock_thread.return_value.start = mock.MagicMock()
            r = p.watchdog_start_impl(interval_sec=5, kill_mode="off")
            data = json.loads(r)
            assert data["ok"] is True, f"watchdog_start 失败: {data}"
            cfg = data.get("config", {})
            assert cfg["perf_guard_enabled"] is True
            assert cfg["perf_guard_interval_rounds"] == 6
            assert cfg["perf_guard_conf_threshold"] == 0.60
            print(f"[8] OK  watchdog_start_impl cfg perf_guard_enabled="
                  f"{cfg['perf_guard_enabled']}")


# ============================================================
# 5. unit: _handle_perf_risk(critical + conf≥0.6) → 调 _trigger_kill_mode
# ============================================================
def test_handle_perf_risk_critical_triggers_kill_mode():
    """critical + conf≥0.6 → 调 perf_guard._trigger_kill_mode 建议模式 + 写 history。

    M3.87 P0-2:_trigger_kill_mode 默认 auto_apply=False 返建议对象,
    blacklist_added 不存在,blacklist 内存列表不动。前端需 user 一键 apply 才真加。
    """
    _setup_watchdog_state(perf_guard_conf_threshold=0.60)

    fake_kill_evt = {
        "action": "kill_mode_triggered",
        "suggested_actions": {
            "blacklist_add": [
                {"name": "fakeproc1", "source": "auto"},
                {"name": "fakeproc2", "source": "auto"},
            ],
        },
        "auto_applied": False,  # M3.87 建议模式标志
    }
    blacklist: list[str] = []

    with mock.patch.dict(sys.modules, {"perf_guard": mock.MagicMock(
            _trigger_kill_mode=mock.MagicMock(return_value=fake_kill_evt))}):
        result = p._handle_perf_risk(_fake_risk_result(risk="critical",
                                                       risk_conf=0.88),
                                     blacklist)

    assert result is not None, "critical + conf≥0.6 应触发 _trigger_kill_mode"
    # M3.87 建议模式:不自动加进内存 blacklist
    assert "fakeproc1" not in blacklist, (
        "建议模式不应自动加 fakeproc1,等 user 确认"
    )
    assert "fakeproc2" not in blacklist, (
        "建议模式不应自动加 fakeproc2,等 user 确认"
    )
    # history 写入了(新 action 名)
    history_actions = [h.get("action") for h in p._watchdog_state["history"]]
    assert "perf_guard_blacklist_suggest" in history_actions, (
        f"history 缺 perf_guard_blacklist_suggest: {history_actions}"
    )
    # auto_applied=False 也写入 history
    last_history = p._watchdog_state["history"][-1]
    assert last_history["auto_applied"] is False
    print(f"[9] OK  critical+conf=0.88 → 触发 _trigger_kill_mode 建议模式,"
          f" history action={history_actions[-1]}, auto_applied=False")


# ============================================================
# 6. unit: _handle_perf_risk(low) → 不触发
# ============================================================
def test_handle_perf_risk_low_does_not_trigger():
    """risk=low → 返 None,不调 _trigger_kill_mode,不写 history。"""
    _setup_watchdog_state()

    with mock.patch("perf_guard._trigger_kill_mode") as mock_kill:
        result = p._handle_perf_risk(_fake_risk_result(risk="low", risk_conf=0.9),
                                      [])
        assert result is None
        mock_kill.assert_not_called()
        assert len(p._watchdog_state["history"]) == 0, (
            f"low risk 不应写 history: {p._watchdog_state['history']}"
        )
        print(f"[10] OK  low risk → None, history 不动")


# ============================================================
# 7. unit: _handle_perf_risk(critical + conf<0.6) → 不触发 + 写 perf_guard_low_conf
# ============================================================
def test_handle_perf_risk_critical_low_conf_does_not_trigger():
    """critical 但 conf<阈值 → 不触发,但写 history 标记低 conf。"""
    _setup_watchdog_state(perf_guard_conf_threshold=0.60)

    with mock.patch("perf_guard._trigger_kill_mode") as mock_kill:
        result = p._handle_perf_risk(_fake_risk_result(risk="critical",
                                                        risk_conf=0.4),
                                      [])
        assert result is None
        mock_kill.assert_not_called()
        # history 写了 "perf_guard_low_conf" 一条
        actions = [h.get("action") for h in p._watchdog_state["history"]]
        assert "perf_guard_low_conf" in actions, (
            f"history 缺 perf_guard_low_conf: {actions}"
        )
        print(f"[11] OK  critical + conf=0.40(<0.6) → None + 写 perf_guard_low_conf")


# ============================================================
# 8. unit: subprocess 隔离(父进程 RSS 不涨)
# ============================================================
def test_subprocess_isolation_parent_rss_stable():
    """调 perf_subprocess_runner 子进程后,父进程 RSS 未明显增长(无 ML 加载)。"""
    import os

    try:
        import psutil
        rss_before = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except ImportError:
        print("[12] SKIP  psutil 未装,跳过 RSS 测试")
        return

    # fake 子进程(stdout 是合法 JSON)
    fake_payload = _fake_risk_result(risk="safe", risk_conf=0.95)
    fake_result = mock.MagicMock()
    fake_result.stdout = json.dumps(fake_payload).encode("utf-8")
    fake_result.returncode = 0
    fake_result.stderr = b""

    with mock.patch("subprocess.run", return_value=fake_result):
        for _ in range(3):  # 跑 3 次,确保累积效应
            out = p._run_perf_subprocess()
            assert out is not None

    rss_after = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    delta = rss_after - rss_before
    # 因为是 mock 子进程,RSS 应该几乎不变(< 5MB)
    assert delta < 5.0, f"父进程 RSS 涨 {delta:.1f}MB,疑似 adapter 加载到父进程"
    print(f"[12] OK  父进程 RSS delta={delta:.2f}MB(< 5MB),subprocess 隔离生效")


# ============================================================
# 主入口
# ============================================================
def main() -> int:
    print(f"=== process_controller_tools M3.78 perf_guard 集成 e2e 测试 ===\n")
    print(f"路径: {_SERVER}\n")

    tests = [
        # 1. perf_subprocess_runner unit
        test_perf_subprocess_runner_module_loads,
        test_perf_subprocess_runner_run_once_emits_valid_json,
        test_perf_subprocess_runner_run_once_returns_1_on_error,
        # 2. _run_perf_subprocess 解析
        test_run_perf_subprocess_parses_stdout_json,
        test_run_perf_subprocess_returns_none_on_timeout,
        test_run_perf_subprocess_returns_none_on_bad_json,
        # 3. config 字段
        test_default_watchdog_config_has_perf_guard_fields,
        # 4. watchdog_start_impl cfg 写入
        test_watchdog_start_impl_writes_perf_guard_cfg,
        # 5-7. _handle_perf_risk
        test_handle_perf_risk_critical_triggers_kill_mode,
        test_handle_perf_risk_low_does_not_trigger,
        test_handle_perf_risk_critical_low_conf_does_not_trigger,
        # 8. subprocess 隔离
        test_subprocess_isolation_parent_rss_stable,
    ]

    passed = 0
    failed: list[tuple[str, str]] = []
    for t_func in tests:
        try:
            t_func()
            passed += 1
        except AssertionError as e:
            failed.append((t_func.__name__, str(e)))
        except Exception as e:  # noqa: BLE001
            failed.append((t_func.__name__, f"{type(e).__name__}: {e}"))

    print()
    print("=" * 60)
    print(f"汇总: 通过 {passed}/{len(tests)}")
    if failed:
        print("失败:")
        for name, err in failed:
            print(f"  - {name}: {err}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())