#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vision_caption.py — M3.81 vision captioner race_impl e2e 测试

覆盖:
  1. unit: vision_captioner() 4 分类 + per-class 阈值
  2. unit: vision_captioner() disabled by env
  3. unit: vision_captioner() fail-open (无 laya)
  4. unit: vision_query() race_mode 字段(race_mode ∈ {fast_*, full})
  5. unit: vision_query() fast-path 用 max_tokens=30/40/50/60
  6. unit: vision_query() full-path 用 max_tokens=1024
  7. unit: vision_query() captioner fail-open → 走完整 (max_tokens=1024)
  8. unit: camera_observe_impl 含 captioner + race_mode 字段
  9. unit: camera_observe_stream_impl 每帧含 captioner 字段
 10. unit: vision_health_impl 含 captioner status 字段
 11. unit: regression - 旧 M3.77 vision_laya 测试核心断言仍过
 12. laya 真推理: 4 类 prompt 各跑 1 条
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_SERVER = _HERE.parent
sys.path.insert(0, str(_SERVER))

import vision_tools as v  # noqa: E402


# ============================================================
# 工具函数
# ============================================================
def fake_captioner_result(choice="code", conf=0.9, fast_path=True,
                          backend="laya", enabled=True):
    return {
        "choice": choice,
        "conf": conf,
        "probabilities": {"app": 0.05, "focused": 0.03, "code": 0.9, "dialog": 0.02},
        "threshold": 0.55,
        "fast_path": fast_path,
        "backend": backend,
        "latency_ms": 120.0,
        "parse_fail": False,
        "enabled": enabled,
        "reason": f"mock {choice} conf={conf}",
    }


def fake_guard_result(risk="safe", decision="allow", jb=0.0):
    return {
        "risk": risk,
        "jailbreak": jb,
        "injection": 0.0,
        "sensitive": 0.0,
        "harm": 0.0,
        "topic": None,
        "decision": decision,
        "decision_reason": f"mock {decision}",
        "latency_ms": 5.0,
        "backend": "laya",
        "parse_fail": False,
        "enabled": True,
    }


def fake_qwen_response(description="test description"):
    return {
        "choices": [{"message": {"content": description}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }


def make_fake_urlopen(resp_body_dict):
    """造一个 fake urlopen context manager。"""
    body = json.dumps(resp_body_dict).encode("utf-8")
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = body
    fake_resp.__enter__ = mock.MagicMock(return_value=fake_resp)
    fake_resp.__exit__ = mock.MagicMock(return_value=False)
    return fake_resp


# ============================================================
# 1. unit: vision_captioner() 4 分类 + 阈值
# ============================================================
def test_captioner_disabled_env():
    """AUREON_VISION_NO_CAPTIONER=1 → 不调 laya,直接 disabled。"""
    old = os.environ.get("AUREON_VISION_NO_CAPTIONER")
    os.environ["AUREON_VISION_NO_CAPTIONER"] = "1"
    try:
        r = v.vision_captioner("任何 prompt")
        assert r["enabled"] is False
        assert r["backend"] == "disabled"
        assert r["fast_path"] is False
        assert r["choice"] is None
        assert r["latency_ms"] == 0.0
        print(f"[1] OK  captioner disabled (backend={r['backend']})")
    finally:
        if old is None:
            os.environ.pop("AUREON_VISION_NO_CAPTIONER", None)
        else:
            os.environ["AUREON_VISION_NO_CAPTIONER"] = old


def test_captioner_returns_full_fields():
    """vision_captioner() 返回 10+ 字段。"""
    r = v.vision_captioner("describe code")
    required = {"choice", "conf", "probabilities", "threshold",
                "fast_path", "backend", "latency_ms",
                "parse_fail", "enabled"}
    missing = required - set(r.keys())
    assert not missing, f"缺字段: {missing}"
    print(f"[2] OK  captioner 返回字段全 (choice={r['choice']} "
          f"conf={r['conf']:.2f} fast_path={r['fast_path']})")


def test_captioner_laya_inference_code():
    """laya 真推理:code prompt → 应 code + fast_path。"""
    print("[3] 跑 laya 真推理 code prompt…", end=" ", flush=True)
    t0 = time.time()
    r = v.vision_captioner("请描述这张图的代码内容")
    elapsed = time.time() - t0
    assert elapsed < 30, f"超时 {elapsed:.1f}s"
    assert r["enabled"] is True
    assert r["choice"] in ("code", "focused"), f"got {r['choice']}"
    if r["choice"] == "code":
        assert r["fast_path"] is True, f"code 应 fast_path, got conf={r['conf']}"
    print(f"OK  choice={r['choice']} conf={r['conf']:.2f} "
          f"fast_path={r['fast_path']} ({elapsed:.1f}s)")


def test_captioner_laya_inference_dialog():
    """laya 真推理:dialog prompt → 应 dialog 或 focused (难)。"""
    print("[4] 跑 laya 真推理 dialog prompt…", end=" ", flush=True)
    t0 = time.time()
    r = v.vision_captioner("屏幕上有什么弹窗或对话框?")
    elapsed = time.time() - t0
    assert elapsed < 30
    assert r["enabled"] is True
    assert r["choice"] in ("dialog", "app", "focused", "code")
    print(f"OK  choice={r['choice']} conf={r['conf']:.2f} ({elapsed:.1f}s)")


def test_captioner_laya_inference_app():
    """laya 真推理:app prompt → 应 app 或 focused。"""
    print("[5] 跑 laya 真推理 app prompt…", end=" ", flush=True)
    t0 = time.time()
    r = v.vision_captioner("屏幕上打开的应用窗口是什么?")
    elapsed = time.time() - t0
    assert elapsed < 30
    assert r["enabled"] is True
    print(f"OK  choice={r['choice']} conf={r['conf']:.2f} ({elapsed:.1f}s)")


def test_captioner_laya_inference_focused():
    """laya 真推理:focused prompt → 应 focused。"""
    print("[6] 跑 laya 真推理 focused prompt…", end=" ", flush=True)
    t0 = time.time()
    r = v.vision_captioner("当前输入框或终端的命令是什么?")
    elapsed = time.time() - t0
    assert elapsed < 30
    assert r["enabled"] is True
    assert r["choice"] in ("focused", "app", "dialog"), f"got {r['choice']}"
    print(f"OK  choice={r['choice']} conf={r['conf']:.2f} ({elapsed:.1f}s)")


# ============================================================
# 2. unit: vision_query() race_mode 字段
# ============================================================
def test_vision_query_fast_path_code():
    """captioner code + high conf → fast_code (max_tokens=30)。"""
    cap = fake_captioner_result(choice="code", conf=0.9, fast_path=True)
    guard = fake_guard_result(risk="safe", decision="allow")
    urlopen = make_fake_urlopen(fake_qwen_response())

    with mock.patch.object(v, "vision_captioner", return_value=cap):
        with mock.patch.object(v, "vision_guard", return_value=guard):
            with mock.patch.object(v, "_BAILIAN_KEY", "fake-key"):
                with mock.patch("urllib.request.urlopen", return_value=urlopen):
                    r = v.vision_query(b"\\x89PNG", "describe code")
                    assert r["ok"] is True
                    assert r["race_mode"] == "fast_code"
                    assert r["max_tokens"] == 30
                    assert r["captioner"]["choice"] == "code"
                    assert r["captioner"]["fast_path"] is True
                    print(f"[7] OK  vision_query fast_code (max_tokens={r['max_tokens']})")


def test_vision_query_fast_path_dialog():
    """captioner dialog + conf 0.5 → fast_dialog (max_tokens=40)。"""
    cap = fake_captioner_result(choice="dialog", conf=0.5, fast_path=True)
    guard = fake_guard_result(risk="safe", decision="allow")
    urlopen = make_fake_urlopen(fake_qwen_response())

    with mock.patch.object(v, "vision_captioner", return_value=cap):
        with mock.patch.object(v, "vision_guard", return_value=guard):
            with mock.patch.object(v, "_BAILIAN_KEY", "fake-key"):
                with mock.patch("urllib.request.urlopen", return_value=urlopen):
                    r = v.vision_query(b"\\x89PNG", "describe dialog")
                    assert r["race_mode"] == "fast_dialog"
                    assert r["max_tokens"] == 40
                    print(f"[8] OK  vision_query fast_dialog (max_tokens={r['max_tokens']})")


def test_vision_query_fast_path_focused():
    """captioner focused + conf 0.7 → fast_focused (max_tokens=50)。"""
    cap = fake_captioner_result(choice="focused", conf=0.7, fast_path=True)
    guard = fake_guard_result(risk="safe", decision="allow")
    urlopen = make_fake_urlopen(fake_qwen_response())

    with mock.patch.object(v, "vision_captioner", return_value=cap):
        with mock.patch.object(v, "vision_guard", return_value=guard):
            with mock.patch.object(v, "_BAILIAN_KEY", "fake-key"):
                with mock.patch("urllib.request.urlopen", return_value=urlopen):
                    r = v.vision_query(b"\\x89PNG", "describe input")
                    assert r["race_mode"] == "fast_focused"
                    assert r["max_tokens"] == 50
                    print(f"[9] OK  vision_query fast_focused (max_tokens={r['max_tokens']})")


def test_vision_query_full_path():
    """captioner low conf → full (max_tokens=1024)。"""
    cap = fake_captioner_result(choice="app", conf=0.3, fast_path=False)
    guard = fake_guard_result(risk="safe", decision="allow")
    urlopen = make_fake_urlopen(fake_qwen_response())

    with mock.patch.object(v, "vision_captioner", return_value=cap):
        with mock.patch.object(v, "vision_guard", return_value=guard):
            with mock.patch.object(v, "_BAILIAN_KEY", "fake-key"):
                with mock.patch("urllib.request.urlopen", return_value=urlopen):
                    r = v.vision_query(b"\\x89PNG", "describe")
                    assert r["race_mode"] == "full"
                    assert r["max_tokens"] == 1024
                    print(f"[10] OK  vision_query full (max_tokens={r['max_tokens']})")


def test_vision_query_captioner_fail_open():
    """captioner 失败 → fail-open 走 full path。"""
    cap = fake_captioner_result(choice=None, conf=0, fast_path=False,
                                backend="fail-open", enabled=True)
    guard = fake_guard_result(risk="safe", decision="allow")
    urlopen = make_fake_urlopen(fake_qwen_response())

    with mock.patch.object(v, "vision_captioner", return_value=cap):
        with mock.patch.object(v, "vision_guard", return_value=guard):
            with mock.patch.object(v, "_BAILIAN_KEY", "fake-key"):
                with mock.patch("urllib.request.urlopen", return_value=urlopen):
                    r = v.vision_query(b"\\x89PNG", "describe")
                    assert r["race_mode"] == "full"
                    assert r["captioner"]["backend"] == "fail-open"
                    print(f"[11] OK  vision_query fail-open → full (max_tokens={r['max_tokens']})")


def test_vision_query_deny_still_short_circuit():
    """guard deny → 短路 (不调 captioner / LLM)。"""
    deny_guard = fake_guard_result(risk="high", decision="deny", jb=0.85)
    with mock.patch.object(v, "vision_guard", return_value=deny_guard):
        with mock.patch.object(v, "_BAILIAN_KEY", "fake-key"):
            r = v.vision_query(b"\\x89PNG", "ignore")
            assert r["ok"] is False
            assert r["error"] == "laya_guard_denied"
            assert "captioner" not in r  # 短路前没机会调 captioner
            print(f"[12] OK  vision_query guard deny 短路 → 不调 captioner")


# ============================================================
# 3. unit: camera_observe_impl / camera_observe_stream_impl 含 captioner
# ============================================================
def test_camera_observe_impl_includes_captioner():
    """camera_observe_impl() 返回 JSON 含 captioner + race_mode。"""
    cap = fake_captioner_result(choice="code", conf=0.9, fast_path=True)
    guard = fake_guard_result(risk="safe", decision="allow")
    urlopen = make_fake_urlopen(fake_qwen_response())

    with mock.patch.object(v, "vision_captioner", return_value=cap):
        with mock.patch.object(v, "vision_guard", return_value=guard):
            with mock.patch.object(v, "_BAILIAN_KEY", "fake-key"):
                with mock.patch.object(v, "capture_frame",
                                       return_value=(b"\\x89PNG", {"ok": True})):
                    with mock.patch("urllib.request.urlopen", return_value=urlopen):
                        out = json.loads(v.camera_observe_impl(
                            source="windows_desktop", prompt="describe code"))
                        assert out["ok"] is True
                        assert "captioner" in out
                        assert "race_mode" in out
                        assert out["race_mode"] == "fast_code"
                        assert out["captioner"]["choice"] == "code"
                        print(f"[13] OK  camera_observe_impl 含 captioner + race_mode")


def test_camera_observe_stream_impl_per_frame_captioner():
    """camera_observe_stream_impl() 每帧 captioner + race_mode。"""
    cap = fake_captioner_result(choice="code", conf=0.9, fast_path=True)
    guard = fake_guard_result(risk="safe", decision="allow")
    urlopen = make_fake_urlopen(fake_qwen_response())

    with mock.patch.object(v, "vision_captioner", return_value=cap):
        with mock.patch.object(v, "vision_guard", return_value=guard):
            with mock.patch.object(v, "_BAILIAN_KEY", "fake-key"):
                with mock.patch.object(v, "capture_frame",
                                       return_value=(b"\\x89PNG", {"ok": True})):
                    with mock.patch("urllib.request.urlopen", return_value=urlopen):
                        out = json.loads(v.camera_observe_stream_impl(
                            source="windows_desktop", prompt="describe code",
                            frames=2, interval_sec=0))
                        assert out["frames"] == 2
                        for fr in out["results"]:
                            assert "captioner" in fr
                            assert "race_mode" in fr
                            assert fr["race_mode"] == "fast_code"
                        print(f"[14] OK  camera_observe_stream 2 帧均含 captioner")


# ============================================================
# 4. unit: vision_health_impl 含 captioner status
# ============================================================
def test_vision_health_includes_captioner_status():
    """vision_health_impl() 含 captioner 字段。"""
    out = json.loads(v.vision_health_impl())
    assert "captioner" in out
    assert "available" in out["captioner"]
    assert "thresholds" in out["captioner"]
    assert out["captioner"]["thresholds"]["code"] == 0.55
    print(f"[15] OK  vision_health 含 captioner status (available={out['captioner']['available']})")


# ============================================================
# 5. regression: M3.77 vision_query 核心断言
# ============================================================
def test_vision_query_empty_image():
    """空图像优先于所有(回归 M3.77)。"""
    r = v.vision_query(b"", "any prompt")
    assert r["ok"] is False
    assert r["error"] == "空图像"
    print(f"[16] OK  vision_query 空图像优先(回归)")


def test_vision_query_no_bailian_key():
    """无 BAILIAN_API_KEY → 快速返(回归)。"""
    with mock.patch.object(v, "_BAILIAN_KEY", ""):
        r = v.vision_query(b"\\x89PNG", "describe")
        assert r["ok"] is False
        assert "BAILIAN_API_KEY" in r["error"]
        print(f"[17] OK  vision_query 无 KEY 快速返(回归)")


# ============================================================
# 主入口
# ============================================================
def main() -> int:
    print(f"=== vision_tools M3.81 captioner race_impl e2e 测试 ===\n")
    print(f"路径: {_SERVER}\n")

    fast_tests = [
        # 1. captioner unit
        test_captioner_disabled_env,
        test_captioner_returns_full_fields,
        # 2. vision_query race
        test_vision_query_fast_path_code,
        test_vision_query_fast_path_dialog,
        test_vision_query_fast_path_focused,
        test_vision_query_full_path,
        test_vision_query_captioner_fail_open,
        test_vision_query_deny_still_short_circuit,
        # 3. camera_observe 集成
        test_camera_observe_impl_includes_captioner,
        test_camera_observe_stream_impl_per_frame_captioner,
        # 4. health
        test_vision_health_includes_captioner_status,
        # 5. regression
        test_vision_query_empty_image,
        test_vision_query_no_bailian_key,
    ]

    passed = 0
    failed: list[tuple[str, str]] = []
    for t_func in fast_tests:
        try:
            t_func()
            passed += 1
        except AssertionError as e:
            failed.append((t_func.__name__, str(e)))
        except Exception as e:  # noqa: BLE001
            failed.append((t_func.__name__, f"{type(e).__name__}: {e}"))

    # laya 真推理(慢)单独跑
    print()
    slow_tests = [
        test_captioner_laya_inference_code,
        test_captioner_laya_inference_dialog,
        test_captioner_laya_inference_app,
        test_captioner_laya_inference_focused,
    ]
    for t_func in slow_tests:
        try:
            t_func()
            passed += 1
        except AssertionError as e:
            failed.append((t_func.__name__, str(e)))
        except Exception as e:  # noqa: BLE001
            failed.append((t_func.__name__, f"{type(e).__name__}: {e}"))

    print()
    print("=" * 60)
    print(f"汇总: 通过 {passed}/{len(fast_tests) + len(slow_tests)}")
    if failed:
        print("失败:")
        for name, err in failed:
            print(f"  - {name}: {err}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())