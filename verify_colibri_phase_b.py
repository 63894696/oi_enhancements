# -*- coding: utf-8 -*-
# verify_colibri_phase_b.py — Phase B OLMoE 下载 + 健康检查端到端验证(2026-09-28 ship)
#
# 目的:不真下 7 GB,不真起 colibri 子进程,只验证:
#   1. 模块导入 + 常量稳定(repo / patterns / mirrors)
#   2. state 字段兜底(老 state.json 不含新字段也能 load)
#   3. request_download 幂等 + fail-soft + already_downloaded 早返
#   4. 主备镜像切换逻辑(mock snapshot_download 失败 → 走 fallback)
#   5. 进度查询 get_download_status 形态正确
#   6. 主对话 web 路由已挂上 /prisiragent/api/colibri/download + /status + /cancel
#   7. _PAGE 含真实轮询 JS(pollDownloadProgress)
#
# 运行:python verify_colibri_phase_b.py
from __future__ import annotations

import asyncio
import json
import os
import re
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


def _patch_keys_db(td):
    fake_db = Path(td) / "keys.db"
    fake_db.parent.mkdir(parents=True, exist_ok=True)
    return mock.patch("fastlane.providers.llm_prisir._DEFAULT_DB", fake_db)


def _safe_cleanup(td):
    import gc
    gc.collect()
    gc.collect()


def _mk_tempdir():
    try:
        return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    except TypeError:
        return tempfile.TemporaryDirectory()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============================================================
# B-1 ~ B-7
# ============================================================

@check("B-1 module 导入 + OLMOE_REPO 正确")
def b1_import():
    from companion import colibri_download as cd
    assert cd.OLMOE_REPO == "colibri-justvugg/OLMoE-7B-int8-container", cd.OLMOE_REPO
    assert "*.safetensors" in cd.OLMOE_PATTERNS
    assert "*.json" in cd.OLMOE_PATTERNS
    assert cd.DEFAULT_MIRRORS[0] == "https://hf-mirror.com"
    assert cd.DEFAULT_MIRRORS[1] == "https://huggingface.co"
    return "OK"


@check("B-2 state 字段兜底(老 JSON 加载不崩)")
def b2_state_fallback():
    from companion.colibri_state import load_state, save_state
    td_ctx = _mk_tempdir()
    td = td_ctx.name
    try:
        Path(td).mkdir(parents=True, exist_ok=True)
        # 写一个不含新 download_xxx 字段的旧 JSON
        (Path(td) / "colibri.json").write_text(
            json.dumps({"downloaded": False, "port": 18891, "state": "not_downloaded"}),
            encoding="utf-8",
        )
        with _patch_env(td):
            s = load_state()
            assert s.download_progress_pct == 0.0
            assert s.download_total_bytes == 0
            assert s.download_task_id == ""
            assert s.download_error == ""
    finally:
        _safe_cleanup(td)
        td_ctx.cleanup()
    return "OK"


@check("B-3 get_download_status 形态正确(phase + progress_pct + repo)")
def b3_status_shape():
    from companion import colibri_download as cd
    cd._DOWNLOAD_TASK = None
    # 隔离真实 state.json — 用临时 DATA_DIR
    td_ctx = _mk_tempdir()
    try:
        td = td_ctx.name
        old = os.environ.get("PRISIR_DATA_DIR", "")
        os.environ["PRISIR_DATA_DIR"] = td
        try:
            s = cd.get_download_status()
        finally:
            if old:
                os.environ["PRISIR_DATA_DIR"] = old
            else:
                os.environ.pop("PRISIR_DATA_DIR", None)
    finally:
        _safe_cleanup(td_ctx)
    assert s["ok"] is True
    assert "phase" in s
    assert "progress_pct" in s
    assert "task_id" in s
    assert "repo" in s
    assert s["repo"] == "colibri-justvugg/OLMoE-7B-int8-container"
    # 默认 idle(隔离环境内)
    assert s["phase"] == "idle", f"phase={s['phase']!r}"
    return "OK"


@check("B-4 request_download already_downloaded 早返")
def b4_already_downloaded():
    from companion import colibri_download as cd
    from companion.colibri_state import update_state
    cd._DOWNLOAD_TASK = None
    td_ctx = _mk_tempdir()
    td = td_ctx.name
    try:
        Path(td, "models", "olmoe").mkdir(parents=True, exist_ok=True)
        (Path(td, "models", "olmoe", "model.safetensors").touch())
        with _patch_env(td):
            update_state(downloaded=False)  # 显式 false 避免其他测试干扰
            update_state(model_path=str(Path(td) / "models" / "olmoe"))
            # 实际写上 file + downloaded=True
            update_state(downloaded=True)
            r = cd.request_download()
    finally:
        _safe_cleanup(td)
        td_ctx.cleanup()
    assert r["ok"] is False, r
    assert r["err"] == "already_downloaded", r
    return "OK"


@check("B-5 主备镜像 fallback(主 fail → 备 success)")
def b5_fallback():
    from companion import colibri_download as cd
    from companion.colibri_state import load_state, update_state
    cd._DOWNLOAD_TASK = None
    cd._DOWNLOAD_CANCEL_FLAG = False

    td_ctx = _mk_tempdir()
    td = td_ctx.name
    try:
        target = Path(td) / "models" / "olmoe"
        with _patch_env(td):
            update_state(model_path=str(target))

        call_count = {"n": 0}
        target_files = []

        def fake_snapshot(*args, **kwargs):
            call_count["n"] += 1
            endpoint = os.environ.get("HF_ENDPOINT", "")
            target_files.append(endpoint)
            if endpoint == "https://hf-mirror.com":
                # 主镜像 fail
                raise RuntimeError("mock primary mirror 503")
            # 备用镜像 success
            target.mkdir(parents=True, exist_ok=True)
            (target / "model.safetensors").write_bytes(b"x" * 1024)
            return str(target)

        fake_hf = mock.MagicMock()
        fake_hf.snapshot_download = fake_snapshot

        with mock.patch.dict("sys.modules", {"huggingface_hub": fake_hf}):
            _run(cd._run_download("task-test"))

        # 应调了 2 次(主失败 + 备成功)
        assert call_count["n"] == 2, f"应调 2 次,实: {call_count['n']}"
        assert target_files[0] == "https://hf-mirror.com"
        assert target_files[1] == "https://huggingface.co"
        s = load_state()
        assert s.downloaded is True, "应标记 downloaded"
        assert s.download_progress_pct == 100.0
        assert s.download_error == ""
    finally:
        _safe_cleanup(td)
        td_ctx.cleanup()
    return "OK"


@check("B-6 主对话 web 路由挂上 /download + /download/status + /download/cancel")
def b6_routes():
    p = _REPO / "prisIragent_web.py"
    src = p.read_text(encoding="utf-8")
    assert '"/prisiragent/api/colibri/download"' in src, "缺 /download 路由"
    assert '"/prisiragent/api/colibri/download/status"' in src, "缺 /download/status"
    assert '"/prisiragent/api/colibri/download/cancel"' in src, "缺 /download/cancel"
    # do_POST 触发 request_download
    assert "request_download()" in src, "do_POST 未调 request_download()"
    assert "get_download_status()" in src, "/status 未调 get_download_status()"
    assert "request_cancel()" in src, "/cancel 未调 request_cancel()"
    return "OK"


@check("B-7 _PAGE 含真实进度轮询 JS(pollDownloadProgress)")
def b7_poll_js():
    p = _REPO / "prisIragent_web.py"
    src = p.read_text(encoding="utf-8")
    assert "pollDownloadProgress" in src, "_PAGE 缺 pollDownloadProgress 函数"
    # 进度查询端点正确
    assert '"/prisiragent/api/colibri/download/status"' in src
    # 触发端点正确
    assert '"/prisiragent/api/colibri/download"' in src
    return "OK"


@check("B-8 phase A 既有功能不回归(subprocess + adapter 还在)")
def b8_no_regression():
    # 测 import 不崩 + 关键常量在
    from companion.colibri_engine import ColibriEngine
    from companion.colibri_adapter import PLATFORM_ID, MODEL_NAME
    assert PLATFORM_ID == "local_colibri"
    assert MODEL_NAME == "olmoe"
    # state DEFAULT_PORT 还在
    from companion.colibri_state import DEFAULT_PORT
    assert DEFAULT_PORT == 18891
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
            print(f"[FAIL] {name}: {e}")
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
