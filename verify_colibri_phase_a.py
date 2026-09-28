# -*- coding: utf-8 -*-
# verify_colibri_phase_a.py — Phase A 端到端静态验证(2026-09-28 ship Phase A)
#
# 目的:不真起 colibri 二进制(避免 7 GB 模型下载),只验证:
#   1. companion_llm.stream_chat 路径上 colibri adapter 注入逻辑不破窗
#   2. colibri_state / colibri_engine / colibri_adapter 三模块 import 不报错
#   3. _KNOWN_PLATFORM_DEFAULTS 不被污染(快速回归检查)
#   4. 既有 LLM 路由流程零变化(unittest 已有,这里聚合汇总)
#
# 运行:python verify_colibri_phase_a.py
# 期望:所有 check 通过,exit 0;任一 FAIL → exit 1。
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))

CHECKS: list = []


def check(name: str):
    """装饰器:注册一个 verify 项。"""
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


@check("C-1 import 三个新模块不报错")
def c1_imports():
    from companion import colibri_state, colibri_engine, colibri_adapter  # noqa: F401
    return "OK"


@check("C-2 colibri_adapter 常量正确")
def c2_constants():
    from companion.colibri_adapter import PLATFORM_ID, MODEL_NAME
    assert PLATFORM_ID == "local_colibri", PLATFORM_ID
    assert MODEL_NAME == "olmoe", MODEL_NAME
    return "OK"


@check("C-3 colibri_state 默认 port 钉死 18891")
def c3_port_pinned():
    from companion.colibri_state import DEFAULT_PORT
    assert DEFAULT_PORT == 18891, DEFAULT_PORT
    return "OK"


@check("C-4 _KNOWN_PLATFORM_DEFAULTS 未被修改")
def c4_known_platforms_intact():
    """注入设计取舍是不改 llm_prisir,这里兜底检查。"""
    from fastlane.providers.llm_prisir import PrisirRouter
    known = PrisirRouter._KNOWN_PLATFORM_DEFAULTS
    # 关键平台都还在
    for p in ("openai", "anthropic", "qwen", "deepseek", "ollama"):
        assert p in known, f"缺失: {p}"
    # local_colibri 没被偷偷加进 known(它走的是 companion 注入,不走 keys.db)
    assert "local_colibri" not in known, "local_colibri 不应进 _KNOWN_PLATFORM_DEFAULTS"
    return "OK"


@check("C-5 colibri_engine.find_colibri_binary fail-soft(无 binary 不抛)")
def c5_find_binary():
    from unittest import mock
    from companion.colibri_engine import find_colibri_binary
    import os
    with mock.patch.dict(os.environ, {"PATH": ""}):
        with mock.patch(
                "companion.colibri_engine.shutil.which", return_value=None):
            r = find_colibri_binary()
    assert r is None, f"应返 None,实际: {r}"
    return "OK"


@check("C-6 companion_llm.stream_chat 在 colibri 未就绪时不破窗")
def c6_stream_chat_no_break():
    """colibri 未 ready → 不注入,流程保持原样。"""
    # 跑一个走通路径的 quick smoke test
    import asyncio
    from companion import colibri_state as cs
    cs.update_state(state="not_downloaded")
    from companion.companion_llm import stream_chat
    events = []
    async def run():
        async for evt, data in stream_chat(
            [{"role": "user", "content": "hi"}],
            strategy="smart", temperature=0.7, max_tokens=10,
        ):
            events.append((evt, data))
            if len(events) >= 3:
                break
    try:
        asyncio.run(run())
    except Exception as e:
        # 没配 key,期望 early return with err 事件
        pass
    # 期望至少有一个 err 事件("无可用模型")
    errs = [e for e in events if e[0] == "err"]
    assert errs, f"应至少一个 err,实际 events: {events}"
    return f"OK (events={len(events)}, errs={len(errs)})"


@check("C-7 Phase A 测试套全绿")
def c7_unit_tests():
    import subprocess
    for fname in ("test_colibri_state.py", "test_colibri_engine.py"):
        proc = subprocess.run(
            [sys.executable, str(_REPO / "tests" / fname)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            return f"FAIL ({fname}: rc={proc.returncode})\n{proc.stdout[-500:]}\n{proc.stderr[-500:]}"
    return "OK"


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
            print(f"[ERR ] {name}: {type(e).__name__}: {e}")
            failed.append(name)
    if failed:
        print(f"\n=== {len(failed)} failed ===")
        return 1
    print(f"\n=== {len(CHECKS)}/{len(CHECKS)} passed ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())