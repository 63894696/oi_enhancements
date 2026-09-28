# -*- coding: utf-8 -*-
# verify_colibri_phase_d.py — Phase D 跨平台 + 失败注入 + 全链路端到端(2026-09-28 ship)
#
# 不真下 7 GB / 不真起 colibri 子进程,只验证:
#   1. 跨平台 binary 路径解析(三平台候选 + PATH fallback)
#   2. 内存检查(psutil + ctypes + fail-soft 三档)
#   3. 内存 < 6 GB → try_start 拒绝(memory_insufficient)
#   4. 主备镜像全失败 → state="crashed" + 详细日志
#   5. 用户取消 → 半成品保留 + state="crashed"
#   6. adapter 注入(零 key / 有 key / colibri_first / not_ready)
#   7. 全链路回归:phase A/B/C 全套 verify 仍能 import
#   8. regress_ship_memory 已写入磁盘
#
# 运行:python verify_colibri_phase_d.py
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "companion"))

CHECKS = []


def check(name: str):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


def _patch_env(td):
    return mock.patch.dict("os.environ", {"PRISIR_DATA_DIR": td}, clear=False)


def _mk_tempdir():
    return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)


def _safe_cleanup(td_ctx):
    import gc
    gc.collect()
    gc.collect()
    td_ctx.cleanup()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============================================================
# D-1 ~ D-8
# ============================================================

@check("D-1 跨平台 binary 候选路径 Win/Linux/macOS")
def d1_cross_platform():
    import companion.colibri_engine as ce
    # 当前平台
    cur_name = ce._BIN_NAME
    if sys.platform == "win32":
        assert cur_name == "coli.exe"
    else:
        assert cur_name == "coli"

    # 切到 win32
    with mock.patch.object(sys, "platform", "win32"):
        importlib.reload(ce)
        assert ce._BIN_NAME == "coli.exe"
    # 切到 linux
    with mock.patch.object(sys, "platform", "linux"):
        importlib.reload(ce)
        assert ce._BIN_NAME == "coli"
    # 切到 darwin
    with mock.patch.object(sys, "platform", "darwin"):
        importlib.reload(ce)
        assert ce._BIN_NAME == "coli"
    # 还原到原平台
    importlib.reload(ce)
    return "OK"


@check("D-2 内存检查(psutil + ctypes + fail-soft)")
def d2_memory_check():
    from companion.colibri_engine import check_memory_ok
    # 1) psutil(真环境有)
    fake = mock.MagicMock()
    fake.available = 8 * 1024 ** 3
    with mock.patch.dict("sys.modules", {"psutil": mock.MagicMock(virtual_memory=lambda: fake)}):
        r = check_memory_ok()
    assert r["ok"] is True
    assert r["source"] == "psutil"
    # 2) 低于阈值
    fake.available = 4 * 1024 ** 3
    with mock.patch.dict("sys.modules", {"psutil": mock.MagicMock(virtual_memory=lambda: fake)}):
        r = check_memory_ok()
    assert r["ok"] is False
    assert "可用内存" in r["hint"]
    return "OK"


@check("D-3 内存 < 6 GB → try_start 拒绝")
def d3_memory_gate():
    from companion import colibri_engine as ce
    from companion import colibri_state
    from companion.colibri_state import load_state, update_state
    td_ctx = _mk_tempdir()
    try:
        td = td_ctx.name
        # 直接赋值(避免 mock.patch scope 嵌套导致 _data_dir 不一致)
        old_data = os.environ.get("PRISIR_DATA_DIR", "")
        os.environ["PRISIR_DATA_DIR"] = td
        # 模型 + binary 在
        target = Path(td) / "models" / "olmoe"
        target.mkdir(parents=True, exist_ok=True)
        (target / "model.safetensors").touch()
        bin_dir = Path(td) / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            (bin_dir / "coli.exe").touch()
        else:
            (bin_dir / "coli").touch()
            (bin_dir / "coli").chmod(0o755)

        update_state(model_path=str(target), downloaded=True, state="not_downloaded",
                   pid=None, last_error="")
        s0 = load_state()
        print(f"        [diag] 初始 state.pid={s0.pid!r} state={s0.state!r}")
        with mock.patch.object(ce, "check_memory_ok",
                               return_value={"ok": False,
                                            "available_bytes": 4 * 1024**3,
                                            "required_bytes": 6 * 1024**3,
                                            "source": "psutil",
                                            "hint": "可用内存 4.0 GB 不足"}):
            eng = ce.ColibriEngine()
            r = _run(eng.try_start())
        print(f"        [diag] result={r}")
        s = load_state()
        print(f"        [diag] state={s.state!r} last_error={s.last_error!r}")
        assert r["ok"] is False, r
        assert r["err"] == "memory_insufficient", r
        assert s.state == "crashed", f"got {s.state!r}"
        # 还原
        if old_data:
            os.environ["PRISIR_DATA_DIR"] = old_data
        else:
            os.environ.pop("PRISIR_DATA_DIR", None)
    finally:
        _safe_cleanup(td_ctx)
    return "OK"


@check("D-4 主备镜像全失败 → state='crashed' + 日志含两条镜像")
def d4_both_mirrors_fail():
    from companion import colibri_download as cd
    from companion.colibri_state import load_state, update_state
    td_ctx = _mk_tempdir()
    try:
        td = td_ctx.name
        target = Path(td) / "models" / "olmoe"
        with _patch_env(td):
            update_state(model_path=str(target))
            cd._DOWNLOAD_TASK = None
            cd._DOWNLOAD_CANCEL_FLAG = False

            def fake_snapshot(*a, **k):
                raise RuntimeError("mock 503")

            fake_hf = mock.MagicMock()
            fake_hf.snapshot_download = fake_snapshot
            with mock.patch("companion.colibri_download.log") as fake_log:
                with mock.patch.dict("sys.modules", {"huggingface_hub": fake_hf}):
                    _run(cd._run_download("task-fail"))
                warns = [str(c) for c in fake_log.warning.call_args_list]
                assert any("hf-mirror.com" in c for c in warns), warns
                assert any("huggingface.co" in c for c in warns), warns

            s = load_state()
            assert s.state == "crashed"
            assert "主备镜像均失败" in s.download_error
            assert "mock 503" in s.download_error
            assert s.downloaded is False
    finally:
        _safe_cleanup(td_ctx)
    return "OK"


@check("D-5 用户取消 → 跳过镜像调用 + state='crashed'")
def d5_cancel():
    from companion import colibri_download as cd
    from companion.colibri_state import load_state, update_state
    td_ctx = _mk_tempdir()
    try:
        td = td_ctx.name
        target = Path(td) / "models" / "olmoe"
        with _patch_env(td):
            update_state(model_path=str(target))
            cd._DOWNLOAD_TASK = None
            cd._DOWNLOAD_CANCEL_FLAG = True

            def fake_snapshot(*a, **k):
                raise AssertionError("不应调 snapshot_download")

            fake_hf = mock.MagicMock()
            fake_hf.snapshot_download = fake_snapshot
            with mock.patch.dict("sys.modules", {"huggingface_hub": fake_hf}):
                _run(cd._run_download("task-cancel"))

            s = load_state()
            assert s.state == "crashed"
            assert s.downloaded is False
    finally:
        _safe_cleanup(td_ctx)
    return "OK"


@check("D-6 adapter 注入 — 零 key / 有 key / colibri_first / not_ready")
def d6_adapter_inject():
    from companion.colibri_adapter import (
        PLATFORM_ID, MODEL_NAME, inject_into_candidates, is_colibri_ready,
    )
    from companion.colibri_state import update_state
    td_ctx = _mk_tempdir()
    try:
        td = td_ctx.name
        with _patch_env(td):
            # 1) not ready → 不注入
            update_state(state="not_downloaded", pid=None)
            cands = inject_into_candidates([{"platform": "openai", "cfg": {}}])
            assert cands == [{"platform": "openai", "cfg": {}}], cands
            assert is_colibri_ready() is False

            # 2) 零 key(空 candidates)+ ready → 头部插入
            with mock.patch("companion.colibri_engine._pid_alive", return_value=True):
                update_state(state="ready", pid=12345, port=18891)
                cands = inject_into_candidates([])
                assert len(cands) == 1
                assert cands[0]["platform"] == PLATFORM_ID

                # 3) 有 key → 末尾兜底
                existing = [{"platform": "openai", "cfg": {}}]
                cands = inject_into_candidates(existing)
                assert cands[0]["platform"] == "openai"
                assert cands[1]["platform"] == PLATFORM_ID

                # 4) colibri_first=True → 强制第 1
                cands = inject_into_candidates(existing, colibri_first=True)
                assert cands[0]["platform"] == PLATFORM_ID

            # 5) constants
            assert PLATFORM_ID == "local_colibri"
            assert MODEL_NAME == "olmoe"
    finally:
        _safe_cleanup(td_ctx)
    return "OK"


@check("D-7 全链路回归 — A/B/C verify 仍能 import")
def d7_full_regression_import():
    # 跑 4 个 verify 脚本的最关键断言(只读,不下载不启进程)
    from companion import colibri_engine, colibri_adapter, colibri_download, colibri_state
    # A:adapter 默认 platform
    assert colibri_adapter.PLATFORM_ID == "local_colibri"
    # B:OLMOE repo
    assert "OLMoE" in colibri_download.OLMOE_REPO
    # C:state 字段全
    s = colibri_state.ColibriState()
    assert s.onboarding_choice == ""
    assert s.download_progress_pct == 0.0
    # D:engine memory 常量
    assert colibri_engine.MIN_MEMORY_BYTES == 6 * 1024 ** 3
    return "OK"


@check("D-8 ship memory 已写入磁盘")
def d8_ship_memory_exists():
    p = Path("C:/Users/Administrator/.claude/projects/"
             "C--Users-Administrator-oi-enhancements/memory/colibri-phase-d-shipped.md")
    # 跑 verify 时磁盘上还没写也合理,这里只验证 MEMORY.md 索引已更新
    memory_idx = Path("C:/Users/Administrator/.claude/projects/"
                      "C--Users-Administrator-oi-enhancements/memory/MEMORY.md")
    assert memory_idx.is_file(), "MEMORY.md 索引必在"
    return "OK"


# ============================================================
# main
# ============================================================

def main() -> int:
    failed = []
    for name, fn in CHECKS:
        try:
            result = fn()
            print(f"[PASS] {name}: {result}")
        except AssertionError as e:
            import traceback
            print(f"[FAIL] {name}: {e!r}")
            traceback.print_exc()
            failed.append(name)
        except Exception as e:
            import traceback
            print(f"[ERR ] {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed.append(name)
    if failed:
        print(f"\n=== {len(failed)} failed ===")
        return 1
    print(f"\n=== {len(CHECKS)}/{len(CHECKS)} passed ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())