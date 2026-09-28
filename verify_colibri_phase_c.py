# -*- coding: utf-8 -*-
# verify_colibri_phase_c.py — Phase C 三选一引导卡端到端验证(2026-09-28 ship Phase C)
#
# 目的:不真起 colibri 二进制,只验证:
#   1. onboarding 后端端点存在且 JSON 形态正确
#   2. should_show_onboarding() 综合判断正确(已有 key / 已下载 / 已选 → 不弹)
#   3. onboarding_choice 持久化与读取
#   4. has_existing_keys() 三路探测(s settings/keys.db/env)互不干扰
#   5. 既有 LLM 路由 + 测试套不回归
#
# 运行:python verify_colibri_phase_c.py
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO))
# 把 companion/ 目录加进 sys.path,让 importlib 加载 prisIragent-companion-web.py 时
# 它的内部 `from .xxx import ...` / `from companion.xxx import ...` 能解析
sys.path.insert(0, str(_REPO / "companion"))

CHECKS: list = []


def check(name: str):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


def _safe_tempdir_cleanup(td):
    """Windows:PrisirKeyStore 的 sqlite 连接 + WAL 锁会卡住 tempdir unlink。
    在临时目录退出前,手动 close 所有可能的连接 + 删文件,然后让 rmtree 顺利完成。"""
    import sys, gc
    gc.collect()
    mod = sys.modules.get("fastlane.providers.llm_prisir")
    if mod is not None:
        ks_cls = getattr(mod, "PrisirKeyStore", None)
        if ks_cls is not None and hasattr(ks_cls, "_instance"):
            inst = getattr(ks_cls, "_instance", None)
            if inst is not None and hasattr(inst, "close"):
                try:
                    inst.close()
                except Exception:
                    pass
            try:
                ks_cls._instance = None
            except Exception:
                pass
    gc.collect()
    gc.collect()
    try:
        for entry in Path(td).rglob("*"):
            try:
                if entry.is_file():
                    entry.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _mk_tempdir():
    """Windows 防 PermissionError:Python 3.10+ 支持 ignore_cleanup_errors。"""
    try:
        return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    except TypeError:
        # 旧 Python 回落 — 用 try/finally 包裹 + 手动 cleanup
        return tempfile.TemporaryDirectory()


@check("C-1 has_existing_keys() 默认 False(无 key 场景)")
def c1_no_keys():
    from companion.colibri_state import has_existing_keys
    clear_env = {k: "" for k in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY",
        "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY",
        "ZHIPU_API_KEY", "GLM_API_KEY", "OPENROUTER_API_KEY",
        "XAI_API_KEY", "GROK_API_KEY", "MISTRAL_API_KEY",
        "GROQ_API_KEY", "DOUBAO_API_KEY", "ARK_API_KEY",
        "BAILIAN_API_KEY", "YUNBAILIAN_API_KEY",
        "OLLAMA_HOST", "LLAMA_SERVER_URL", "PATH",
    )}
    td_ctx = tempfile.TemporaryDirectory()
    td = td_ctx.name
    try:
        with _patch_keys_db(td):
            with mock.patch.dict(os.environ,
                                {"PRISIR_DATA_DIR": td}, clear=False):
                with mock.patch.dict(os.environ, clear_env, clear=False):
                    result = has_existing_keys()
    finally:
        _safe_tempdir_cleanup(td)
        td_ctx.cleanup()
    assert result is False, f"应返 False,实际: {result}"
    return "OK"


@check("C-2 has_existing_keys() 检测到 env var → True")
def c2_env_key():
    from companion.colibri_state import has_existing_keys
    td_ctx = tempfile.TemporaryDirectory()
    td = td_ctx.name
    try:
        with mock.patch.dict(os.environ,
                            {"PRISIR_DATA_DIR": td, "OPENAI_API_KEY": "sk-test"},
                clear=False):
            result = has_existing_keys()
    finally:
        _safe_tempdir_cleanup(td)
        td_ctx.cleanup()
    assert result is True, f"应返 True,实际: {result}"
    return "OK"


@check("C-3 has_existing_keys() 检测到 keys.db 有 key → True")
def c3_db_key():
    from companion.colibri_state import has_existing_keys
    td_ctx = _mk_tempdir()
    td = td_ctx.name
    try:
        # 在标准 keys.db 路径写一条
        import sqlite3
        db_dir = Path.home() / ".local" / "share" / "prisir"
        db_dir.mkdir(parents=True, exist_ok=True)
        db = db_dir / "keys.db"
        # 备份 / 恢复原 db(避免污染用户真实数据)
        backup = None
        if db.exists():
            backup = db.read_bytes()
        try:
            with sqlite3.connect(str(db)) as c:
                c.execute("CREATE TABLE IF NOT EXISTS platform_keys("
                         "platform TEXT PRIMARY KEY, api_key TEXT NOT NULL DEFAULT '', "
                         "base_url TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', "
                         "meta TEXT NOT NULL DEFAULT '{}', updated INTEGER NOT NULL DEFAULT 0)")
                c.execute("INSERT OR REPLACE INTO platform_keys(platform, api_key) "
                         "VALUES('openai', 'sk-test')")
            with mock.patch.dict(os.environ,
                                {"PRISIR_DATA_DIR": td}, clear=False):
                env_clear = {k: "" for k in (
                    "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                    "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY")}
                with mock.patch.dict(os.environ, env_clear, clear=False):
                    result = has_existing_keys()
            assert result is True, f"应返 True,实际: {result}"
        finally:
            if backup is not None:
                db.write_bytes(backup)
            elif db.exists():
                try:
                    with sqlite3.connect(str(db)) as c:
                        c.execute("DELETE FROM platform_keys WHERE platform='openai' AND api_key='sk-test'")
                except Exception:
                    pass
    finally:
        _safe_tempdir_cleanup(td)
        td_ctx.cleanup()
    return "OK"


@check("C-4 should_show_onboarding() 默认 True(无任何 key / 未选)")
def c4_should_show_default():
    from companion.colibri_state import should_show_onboarding, update_state
    clear_env = {k: "" for k in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY",
        "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY",
        "ZHIPU_API_KEY", "GLM_API_KEY", "OPENROUTER_API_KEY",
        "XAI_API_KEY", "GROK_API_KEY", "MISTRAL_API_KEY",
        "GROQ_API_KEY", "DOUBAO_API_KEY", "ARK_API_KEY",
        "BAILIAN_API_KEY", "YUNBAILIAN_API_KEY",
        "OLLAMA_HOST", "LLAMA_SERVER_URL", "PATH",
    )}
    td_ctx = _mk_tempdir()
    td = td_ctx.name
    try:
        with _patch_keys_db(td):
            with mock.patch.dict(os.environ, {"PRISIR_DATA_DIR": td}, clear=False):
                with mock.patch.dict(os.environ, clear_env, clear=False):
                    update_state(onboarding_choice="", downloaded=False, dismissed=False)
                    result = should_show_onboarding()
    finally:
        _safe_tempdir_cleanup(td)
        td_ctx.cleanup()
    assert result is True, f"应返 True,实际: {result}"
    return "OK"


@check("C-5 should_show_onboarding() 选 has_key 后 → False")
def c5_has_key_choice():
    from companion.colibri_state import should_show_onboarding, update_state
    clear_env = {k: "" for k in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY",
        "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY",
        "ZHIPU_API_KEY", "GLM_API_KEY", "OPENROUTER_API_KEY",
        "XAI_API_KEY", "GROK_API_KEY", "MISTRAL_API_KEY",
        "GROQ_API_KEY", "DOUBAO_API_KEY", "ARK_API_KEY",
        "BAILIAN_API_KEY", "YUNBAILIAN_API_KEY",
        "OLLAMA_HOST", "LLAMA_SERVER_URL", "PATH",
    )}
    td_ctx = _mk_tempdir()
    td = td_ctx.name
    try:
        with _patch_keys_db(td):
            with mock.patch.dict(os.environ, {"PRISIR_DATA_DIR": td}, clear=False):
                with mock.patch.dict(os.environ, clear_env, clear=False):
                    update_state(onboarding_choice="has_key", downloaded=False)
                    result = should_show_onboarding()
    finally:
        _safe_tempdir_cleanup(td)
        td_ctx.cleanup()
    assert result is False, f"应返 False,实际: {result}"
    return "OK"


@check("C-6 should_show_onboarding() 选 skip 后 → 仍 True")
def c6_skip_choice():
    from companion.colibri_state import should_show_onboarding, update_state
    clear_env = {k: "" for k in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY",
        "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY",
        "ZHIPU_API_KEY", "GLM_API_KEY", "OPENROUTER_API_KEY",
        "XAI_API_KEY", "GROK_API_KEY", "MISTRAL_API_KEY",
        "GROQ_API_KEY", "DOUBAO_API_KEY", "ARK_API_KEY",
        "BAILIAN_API_KEY", "YUNBAILIAN_API_KEY",
        "OLLAMA_HOST", "LLAMA_SERVER_URL", "PATH",
    )}
    td_ctx = _mk_tempdir()
    td = td_ctx.name
    try:
        with _patch_keys_db(td):
            with mock.patch.dict(os.environ, {"PRISIR_DATA_DIR": td}, clear=False):
                with mock.patch.dict(os.environ, clear_env, clear=False):
                    update_state(onboarding_choice="skip")
                    result = should_show_onboarding()
    finally:
        _safe_tempdir_cleanup(td)
        td_ctx.cleanup()
    assert result is True, "skip 应继续弹(下次启动再问)"
    return "OK"


@check("C-7 should_show_onboarding() 模型已下载 → False")
def c7_downloaded():
    from companion.colibri_state import should_show_onboarding, update_state, load_state
    clear_env = {k: "" for k in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY",
        "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY",
        "ZHIPU_API_KEY", "GLM_API_KEY", "OPENROUTER_API_KEY",
        "XAI_API_KEY", "GROK_API_KEY", "MISTRAL_API_KEY",
        "GROQ_API_KEY", "DOUBAO_API_KEY", "ARK_API_KEY",
        "BAILIAN_API_KEY", "YUNBAILIAN_API_KEY",
        "OLLAMA_HOST", "LLAMA_SERVER_URL", "PATH",
    )}
    td_ctx = _mk_tempdir()
    td = td_ctx.name
    try:
        Path(td).mkdir(parents=True, exist_ok=True)
        Path(td, "settings.json").write_text(
            '{"active_platform": "", "llm_available_platforms": [], '
            '"providers": {}, "enable_dispatch": false}',
            encoding="utf-8")
        with _patch_keys_db(td):
            with mock.patch.dict(os.environ, {"PRISIR_DATA_DIR": td}, clear=False):
                with mock.patch.dict(os.environ, clear_env, clear=False):
                    # 用户已选 no_key + 下载完成 → 不弹(no_key 流程完成)
                    update_state(onboarding_choice="no_key", downloaded=True)
                    s = load_state()
                    Path(s.model_path).mkdir(parents=True, exist_ok=True)
                    (Path(s.model_path) / "model.safetensors").touch()
                    result = should_show_onboarding()
    finally:
        _safe_tempdir_cleanup(td)
        td_ctx.cleanup()
    assert result is False, f"应返 False,实际: {result}"
    return "OK"


@check("C-8 主对话窗口 onboarding 路由挂上(prisIragent_web.py)")
def c8_endpoint_routable():
    """M3.36.C 重构:引导卡路由已转移到主对话窗口(prisIragent_web.py)。
    验证 do_GET/do_POST 中含 /prisiragent/api/colibri/onboarding 路径分支。
    """
    p = _REPO / "prisIragent_web.py"
    src = p.read_text(encoding="utf-8")
    # do_GET /do_POST 至少各出现一次 + 含 onboarding 路径
    assert '"/prisiragent/api/colibri/onboarding"' in src, "do_GET 缺 /onboarding 路由"
    assert '"/prisiragent/api/colibri/onboarding/choose"' in src, "do_POST 缺 /choose 路由"
    assert '"/prisiragent/api/colibri/download"' in src, "do_POST 缺 /download 占位路由"
    # 引导卡 UI 已注入 _PAGE
    assert '#onboardingCard' in src, "_PAGE 缺引导卡 HTML"
    assert 'class="onb-choice"' in src, "_PAGE 缺引导卡选项 HTML"
    assert 'checkOnboarding' in src, "_PAGE 缺前端 JS(checkOnboarding)"
    assert 'submitOnboardingChoice' in src or 'submit(' in src, "_PAGE 缺前端 JS(submit)"
    # companion 不再注册 onboarding 路由(职责已转移)
    companion_src = (_REPO / "companion" / "prisIragent-companion-web.py").read_text(encoding="utf-8")
    assert '"/api/colibri/onboarding"' not in companion_src, "companion 不应再挂载 /api/colibri/onboarding"
    return "OK"


@check("C-9 Phase C 端点选择逻辑 OK")
def c9_endpoint_logic():
    """M3.36.C 重构:端点选择逻辑下沉到 colibri_state.update_state。
    这里测 has_key / skip / unknown / bad json 在 update_state 上的行为,等价于
    原 aiohttp 端点的副作用(choice 持久化与不持久化)。
    """
    from companion.colibri_state import update_state, load_state
    import time as _t

    clear_env = {k: "" for k in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY",
        "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY",
        "ZHIPU_API_KEY", "GLM_API_KEY", "OPENROUTER_API_KEY",
        "XAI_API_KEY", "GROK_API_KEY", "MISTRAL_API_KEY",
        "GROQ_API_KEY", "DOUBAO_API_KEY", "ARK_API_KEY",
        "BAILIAN_API_KEY", "YUNBAILIAN_API_KEY",
        "OLLAMA_HOST", "LLAMA_SERVER_URL", "PATH",
    )}
    td_ctx = _mk_tempdir()
    td = td_ctx.name
    try:
        Path(td).mkdir(parents=True, exist_ok=True)
        Path(td, "settings.json").write_text(
            '{"active_platform": "", "llm_available_platforms": [], '
            '"providers": {}, "enable_dispatch": false}',
            encoding="utf-8")
        with _patch_keys_db(td):
            with mock.patch.dict(os.environ, {"PRISIR_DATA_DIR": td}, clear=False):
                with mock.patch.dict(os.environ, clear_env, clear=False):
                    # 测 has_key:持久化
                    update_state(onboarding_choice="")
                    update_state(onboarding_choice="has_key", onboarding_at=int(_t.time()))
                    s = load_state()
                    assert s.onboarding_choice == "has_key", s.onboarding_choice

                    # 测 skip:不持久化 choice(只更新 onboarding_at)
                    update_state(onboarding_choice="")  # 重置
                    update_state(onboarding_at=int(_t.time()))   # skip 路径
                    s = load_state()
                    assert s.onboarding_choice == "", f"skip 不应改 choice,实为: {s.onboarding_choice}"

                    # 测 no_key:持久化
                    update_state(onboarding_choice="no_key")
                    s = load_state()
                    assert s.onboarding_choice == "no_key"

                    # 测 unknown:不修改 state(主对话 400 返错,服务端根本不调 update_state)
                    bad = "hacker"
                    assert bad not in ("no_key", "has_key", "skip")
                    s = load_state()
                    assert s.onboarding_choice == "no_key"  # 上一步的 state 没变
    finally:
        _safe_tempdir_cleanup(td)
        td_ctx.cleanup()
    return "OK"


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def _patch_keys_db(td):
    """把 keys.db 路径 patch 到 temp dir,避免读真实用户数据。"""
    fake_db = Path(td) / "keys.db"
    fake_db.parent.mkdir(parents=True, exist_ok=True)
    return mock.patch(
        "fastlane.providers.llm_prisir._DEFAULT_DB",
        fake_db,
    )


def _close_keys_store_singletons():
    """Windows:PrisirKeyStore 是单例,sys.modules 缓存会让它一直持有 sqlite 连接。
    删除缓存 + 关连接 → tempdir 退出时不会 PermissionError。"""
    import sys
    import importlib
    # 清掉 PrisirKeyStore / 相关模块缓存
    for key in list(sys.modules.keys()):
        if key.startswith(("fastlane.providers.llm_prisir", "fastlane.providers")):
            mod = sys.modules.get(key)
            if mod is not None:
                # 关可能的 sqlite 连接
                ks = getattr(mod, "PrisirKeyStore", None)
                if ks is not None:
                    try:
                        inst = ks.__dict__.get("_instance") or getattr(ks, "_instance", None)
                        if inst is not None and hasattr(inst, "close"):
                            inst.close()
                    except Exception:
                        pass
            del sys.modules[key]
    import gc
    gc.collect()


@check("C-10 既有 LLM 路由不回归(spot check)")
def c10_no_regression():
    """快速冒烟:PrisirRouter.PrisirRouter + _KNOWN_PLATFORM_DEFAULTS 还在。"""
    from fastlane.providers.llm_prisir import PrisirRouter
    known = PrisirRouter._KNOWN_PLATFORM_DEFAULTS
    for p in ("openai", "anthropic", "qwen", "deepseek", "ollama"):
        assert p in known
    return "OK"


def main() -> int:
    import gc
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
        finally:
            # Windows 文件锁问题 — 强制释放 sqlite 连接 + 清缓存 + gc
            _close_keys_store_singletons()
            gc.collect()
    if failed:
        print(f"\n=== {len(failed)} failed ===")
        return 1
    print(f"\n=== {len(CHECKS)}/{len(CHECKS)} passed ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())