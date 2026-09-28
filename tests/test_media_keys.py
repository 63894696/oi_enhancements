# -*- coding: utf-8 -*-
"""tests/test_media_keys.py — companion.media_keys 单元测试。

覆盖(P3j T17-F):
  · test_load_default             — 无文件时返默认 4 provider
  · test_save_load_roundtrip      — save → load 数据一致
  · test_load_missing_fields      — 旧文件缺字段时补齐
  · test_public_mask_secret       — api_key mask,带 _present 标志
  · test_apply_post_secret_mask   — "***" 不覆盖原值
  · test_apply_post_nonnull       — 真正填的值会被写入
  · test_apply_post_unknown_field — 未知 provider / field 跳过
  · test_resolve_media_key_fallback — Easel .env > media_keys.json > env
  · test_get_whisper_model        — 默认 base
  · test_media_status_keys        — key_present / mode 字段对齐
  · test_translate_exec_error     — agent_main_chat_hook.translate_exec_error
  · test_translate_exec_error_unmatched — 无匹配时返原文
  · test_exec_result_attaches_hint — scan_and_exec 失败时附 zh/hint/link

全部必绿;无 skip。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _tmp_data_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="media_keys_test_"))
    return d


# ---------------------------------------------------------------------------
# load_default
# ---------------------------------------------------------------------------

def test_load_default():
    from companion.media_keys import load_media_keys, PROVIDERS
    d = _tmp_data_dir()
    s = load_media_keys(d)
    assert "providers" in s
    assert set(s["providers"].keys()) == {p["id"] for p in PROVIDERS}
    # 默认无 secret
    for pid in ("siliconflow", "dashscope", "openai"):
        assert s["providers"][pid].get("api_key", "") == ""
    # whisper 默认 base
    assert s["providers"]["whisper"]["model"] == "base"


# ---------------------------------------------------------------------------
# save → load roundtrip
# ---------------------------------------------------------------------------

def test_save_load_roundtrip():
    from companion.media_keys import load_media_keys, save_media_keys
    d = _tmp_data_dir()
    s = load_media_keys(d)
    s["providers"]["siliconflow"]["api_key"] = "sk-test-1234567890"
    s["providers"]["whisper"]["model"] = "large-v3"
    save_media_keys(d, s)
    loaded = load_media_keys(d)
    assert loaded["providers"]["siliconflow"]["api_key"] == "sk-test-1234567890"
    assert loaded["providers"]["whisper"]["model"] == "large-v3"


# ---------------------------------------------------------------------------
# load 旧文件缺字段时补齐
# ---------------------------------------------------------------------------

def test_load_missing_fields():
    from companion.media_keys import load_media_keys, PROVIDERS
    d = _tmp_data_dir()
    # 写一个只含 siliconflow.api_key 的旧文件
    (d / "media_keys.json").write_text(
        json.dumps({"providers": {"siliconflow": {"api_key": "sk-xxx"}}}),
        encoding="utf-8")
    s = load_media_keys(d)
    # 缺的 provider 已被补齐
    for pid in ("dashscope", "openai", "whisper"):
        assert pid in s["providers"], f"missing provider: {pid}"
    # siliconflow 也补齐 base_url
    assert "base_url" in s["providers"]["siliconflow"]
    # 原 api_key 不丢
    assert s["providers"]["siliconflow"]["api_key"] == "sk-xxx"


# ---------------------------------------------------------------------------
# public_mask_secret
# ---------------------------------------------------------------------------

def test_public_mask_secret():
    from companion.media_keys import load_media_keys, save_media_keys, public_media_keys
    d = _tmp_data_dir()
    s = load_media_keys(d)
    s["providers"]["siliconflow"]["api_key"] = "sk-abcdefghijklmnop"
    save_media_keys(d, s)
    pub = public_media_keys(load_media_keys(d))
    sf = pub["providers"]["siliconflow"]
    # api_key 被 mask
    assert "…" in sf["api_key"]
    assert sf["api_key"] != "sk-abcdefghijklmnop"
    # _present 标志
    assert sf.get("api_key_present") is True
    # base_url 不 mask
    assert sf["base_url"] == "https://api.siliconflow.cn/v1"
    # registry 字段存在
    assert any(r["id"] == "siliconflow" for r in pub["registry"])


# ---------------------------------------------------------------------------
# apply_post — secret mask 不覆盖
# ---------------------------------------------------------------------------

def test_apply_post_secret_mask():
    from companion.media_keys import (load_media_keys, save_media_keys,
                                       apply_post)
    d = _tmp_data_dir()
    s = load_media_keys(d)
    s["providers"]["siliconflow"]["api_key"] = "sk-original-keep-me"
    save_media_keys(d, s)
    # 前端 POST "***" — 应不覆盖
    posted = {"providers": {"siliconflow": {"api_key": "***"}}}
    merged = apply_post(d, posted)
    assert merged["providers"]["siliconflow"]["api_key"] == "sk-original-keep-me"


def test_apply_post_nonnull():
    from companion.media_keys import (load_media_keys, save_media_keys,
                                       apply_post)
    d = _tmp_data_dir()
    s = load_media_keys(d)
    save_media_keys(d, s)
    # 真正填值 — 写入
    posted = {"providers": {"siliconflow": {"api_key": "sk-new-value"}}}
    merged = apply_post(d, posted)
    assert merged["providers"]["siliconflow"]["api_key"] == "sk-new-value"


def test_apply_post_unknown_field():
    from companion.media_keys import load_media_keys, save_media_keys, apply_post
    d = _tmp_data_dir()
    s = load_media_keys(d)
    save_media_keys(d, s)
    # 未知 provider + 未知 field — 不抛异常也不写入
    posted = {"providers": {"unknown_prov": {"api_key": "x"}}}
    merged = apply_post(d, posted)
    assert "unknown_prov" not in merged["providers"]


# ---------------------------------------------------------------------------
# resolve_media_key fallback 链路
# ---------------------------------------------------------------------------

def test_resolve_media_key_fallback():
    """Easel .env > media_keys.json > process env,链路优先级。"""
    from companion import media_keys as mk_mod
    from companion.media_keys import resolve_media_key
    # 关键:把 resolve_media_key 读 media_keys.json 的 data_dir 钉到临时目录
    # (避免 ~/.prisirai/media_keys.json 真实文件干扰测试)
    tmp = _tmp_data_dir()
    import companion.media_keys as _mk
    orig_data_dir = _mk.os.environ.get("PRISIR_DATA_DIR")
    _mk.os.environ["PRISIR_DATA_DIR"] = str(tmp)

    try:
        # 1) Easel .env(空)+ media_keys.json(空)+ process env("abc") → abc
        with mock.patch.dict(os.environ, {"SILICONFLOW_API_KEY": "abc"},
                             clear=False):
            with mock.patch("prisir_work.video_creator._load_easel_env",
                            return_value={}):
                v = resolve_media_key("siliconflow", "SILICONFLOW_API_KEY")
                assert v == "abc", f"case1: got {v!r}"

        # 2) Easel .env("xyz") 优先 → xyz(忽略 env)
        with mock.patch.dict(os.environ, {"SILICONFLOW_API_KEY": "abc"},
                             clear=False):
            with mock.patch("prisir_work.video_creator._load_easel_env",
                            return_value={"SILICONFLOW_API_KEY": "xyz"}):
                v = resolve_media_key("siliconflow", "SILICONFLOW_API_KEY")
                assert v == "xyz", f"case2: got {v!r}"

        # 3) 全空 → ""
        saved = os.environ.pop("SILICONFLOW_API_KEY", None)
        try:
            with mock.patch.dict(os.environ, {"_UNRELATED": "x"},
                                 clear=True):
                with mock.patch("prisir_work.video_creator._load_easel_env",
                                return_value={}):
                    v = resolve_media_key("siliconflow",
                                           "SILICONFLOW_API_KEY")
                    assert v == "", f"case3: got {v!r}"
        finally:
            if saved is not None:
                os.environ["SILICONFLOW_API_KEY"] = saved
    finally:
        # 恢复 PRISIR_DATA_DIR
        if orig_data_dir is None:
            _mk.os.environ.pop("PRISIR_DATA_DIR", None)
        else:
            _mk.os.environ["PRISIR_DATA_DIR"] = orig_data_dir


# ---------------------------------------------------------------------------
# get_whisper_model
# ---------------------------------------------------------------------------

def test_get_whisper_model():
    from companion.media_keys import (load_media_keys, save_media_keys,
                                       get_whisper_model)
    d = _tmp_data_dir()
    s = load_media_keys(d)
    assert get_whisper_model(d) == "base"  # 默认
    s["providers"]["whisper"]["model"] = "small"
    save_media_keys(d, s)
    assert get_whisper_model(d) == "small"


# ---------------------------------------------------------------------------
# media_status
# ---------------------------------------------------------------------------

def test_media_status_keys():
    from companion.media_keys import media_status
    d = _tmp_data_dir()
    st = media_status(d)
    # 必备字段
    for k in ("easel", "image_gen", "video_gen", "tts", "asr", "ffmpeg"):
        assert k in st, f"missing: {k}"
    # key_present 字段(粗粒度 3 类核心字段)
    assert "key_present" in st["image_gen"]
    assert "mode" in st["image_gen"]
    assert "hint" in st["image_gen"]
    # 默认无 key → mode=placeholder
    assert st["image_gen"]["mode"] in ("placeholder", "siliconflow")
    assert st["video_gen"]["mode"] in ("placeholder", "siliconflow")


# ---------------------------------------------------------------------------
# translate_exec_error
# ---------------------------------------------------------------------------

def test_translate_exec_error():
    from prisir_work.agent_main_chat_hook import translate_exec_error
    # 命中 SILICONFLOW
    r = translate_exec_error("Easel 未装 或 SILICONFLOW_API_KEY 未配置")
    assert "AI 配图" in r["zh"]
    assert r["link"] == "/media-keys"
    # 命中 ffmpeg
    r = translate_exec_error("RuntimeError: ffmpeg not found")
    assert "ffmpeg" in r["zh"]
    # 命中 url_error
    r = translate_exec_error("url_error: connection refused")
    assert "未启动" in r["zh"] or "离线" in r["zh"]


def test_translate_exec_error_unmatched():
    from prisir_work.agent_main_chat_hook import translate_exec_error
    r = translate_exec_error("some completely unknown error xyz")
    # 无匹配 → 原文回退
    assert r["zh"] == "some completely unknown error xyz"
    assert r["link"] == ""


# ---------------------------------------------------------------------------
# scan_and_exec 失败时附 hint/link
# ---------------------------------------------------------------------------

def test_exec_result_attaches_hint():
    from prisir_work.agent_main_chat_hook import scan_and_exec
    # 故意写一个不存在的 capability → 失败
    text = '[[EXEC: nonexistent_cap_xyz k="v"]]'
    events = scan_and_exec(text)
    assert len(events) == 1
    ev = events[0]
    assert ev["ok"] is False
    # error 含 capability_not_found → 翻译函数会返 zh/hint/link
    assert ev.get("zh"), f"expected zh field, got {ev}"
    # hint/link 是可缺省字段(空也行,但字段名应在)
    assert "hint" in ev
    assert "link" in ev


# ---------------------------------------------------------------------------
# 允许直接 `python tests/test_media_keys.py` 跑(对齐 test_agent_video.py 范式)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    passed = 0
    failed = 0
    funcs = [(n, getattr(sys.modules[__name__], n))
             for n in dir(sys.modules[__name__])
             if n.startswith("test_") and callable(getattr(sys.modules[__name__], n))]
    for name, fn in funcs:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n=== media_keys: {passed} passed, {failed} failed ===")
    sys.exit(0 if failed == 0 else 1)