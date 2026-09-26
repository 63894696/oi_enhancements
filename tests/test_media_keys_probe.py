# -*- coding: utf-8 -*-
"""tests/test_media_keys_probe.py — companion.media_keys.probe_provider 单元测试。

覆盖(P3j T18-D):
  · test_probe_unknown_provider        — 未知 id 直接返 {ok:False, hint}
  · test_probe_siliconflow_ok          — 200 OK → ok=True + status=200 + latency>0
  · test_probe_siliconflow_401         — 401 → ok=False + hint 含"鉴权失败"
  · test_probe_siliconflow_timeout     — URLError(reason=timeout) → ok=False + status=-1
  · test_probe_openai_base_url_override — 自定义 base_url 拼接到 /models
  · test_probe_whisper_pkg_missing     — faster_whisper 不可导入 → ok=False
  · test_probe_whisper_cache_ok        — faster_whisper 已装 + cache 目录存在 → ok=True

全部必绿;无 skip。probe_provider 是同步 + 走 urllib.request.urlopen,用
mock.patch 替换,不发真请求。
"""
from __future__ import annotations

import sys
import tempfile
import urllib.error as urllib_error
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status: int, body: str = ""):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self, n: int = -1) -> bytes:
        if n < 0 or n >= len(self._body):
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeHTTPError(urllib_error.HTTPError):
    def __init__(self, code: int, msg: str = "err"):
        # HTTPError(url, code, msg, hdrs, fp) — fp 可以 None
        super().__init__("http://test", code, msg, None, None)
        self._msg = msg.encode("utf-8")

    def read(self, n: int = -1) -> bytes:
        return self._msg

    def def_fp(self, *args, **kwargs):  # 不真被调
        return None


# ---------------------------------------------------------------------------
# unknown provider
# ---------------------------------------------------------------------------

def test_probe_unknown_provider():
    from companion.media_keys import probe_provider
    r = probe_provider("nope_provider_xyz", "sk-xxx", "", 1.0)
    assert r["ok"] is False
    assert "未知" in r["hint"]


# ---------------------------------------------------------------------------
# siliconflow 200
# ---------------------------------------------------------------------------

def test_probe_siliconflow_ok():
    from companion.media_keys import probe_provider
    with mock.patch(
        "urllib.request.urlopen",
        return_value=_FakeResp(200, '{"data":[{"id":"x"}]}')):
        r = probe_provider("siliconflow", "sk-good-key",
                           "", 1.0)
    assert r["ok"] is True
    assert r["status"] == 200
    assert r["mode"] == "siliconflow"
    assert r["latency_ms"] >= 0
    assert "有效" in r["hint"]


# ---------------------------------------------------------------------------
# siliconflow 401
# ---------------------------------------------------------------------------

def test_probe_siliconflow_401():
    from companion.media_keys import probe_provider
    with mock.patch(
        "urllib.request.urlopen",
        side_effect=_FakeHTTPError(401, "unauthorized")):
        r = probe_provider("siliconflow", "sk-bad-key", "", 1.0)
    assert r["ok"] is False
    assert r["status"] == 401
    assert "鉴权失败" in r["hint"] or "撤销" in r["hint"]


# ---------------------------------------------------------------------------
# siliconflow timeout (URLError reason=timeout)
# ---------------------------------------------------------------------------

def test_probe_siliconflow_timeout():
    from companion.media_keys import probe_provider
    with mock.patch(
        "urllib.request.urlopen",
        side_effect=urllib_error.URLError(reason="timed out")):
        r = probe_provider("siliconflow", "sk-x", "", 1.0)
    assert r["ok"] is False
    assert r["status"] == -1


# ---------------------------------------------------------------------------
# openai 自定义 base_url 拼接 /models
# ---------------------------------------------------------------------------

def test_probe_openai_base_url_override():
    from companion.media_keys import probe_provider
    captured: dict = {}
    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization", "")
        return _FakeResp(200, "{}")
    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        r = probe_provider("openai", "sk-x",
                           "https://my-proxy.example.com/v1", 1.0)
    assert r["ok"] is True
    assert captured["url"] == "https://my-proxy.example.com/v1/models"
    assert captured["auth"].startswith("Bearer sk-")


# ---------------------------------------------------------------------------
# whisper faster-whisper 不可导入 → ok=False
# ---------------------------------------------------------------------------

def test_probe_whisper_pkg_missing():
    from companion.media_keys import probe_provider
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("mock missing")
        return real_import(name, *args, **kwargs)
    with mock.patch("builtins.__import__", side_effect=fake_import):
        r = probe_provider("whisper", "", "", 1.0)
    assert r["ok"] is False
    assert "faster-whisper" in r["hint"]


# ---------------------------------------------------------------------------
# whisper 完整链路 ok
# ---------------------------------------------------------------------------

def test_probe_whisper_cache_ok():
    from companion.media_keys import probe_provider
    with tempfile.TemporaryDirectory() as tmp:
        # 模拟 cache 目录存在
        cache_dir = Path(tmp) / "huggingface" / "hub"
        cache_dir.mkdir(parents=True)
        with mock.patch.dict(sys.modules, {"faster_whisper": mock.MagicMock()}):
            # patch _WHISPER_CACHE_DIRS 第一个路径指向 tmp
            from companion import media_keys as _mk
            orig = list(_mk._WHISPER_CACHE_DIRS)
            _mk._WHISPER_CACHE_DIRS.clear()
            _mk._WHISPER_CACHE_DIRS.append(cache_dir)
            try:
                r = probe_provider("whisper", "", "", 1.0)
            finally:
                _mk._WHISPER_CACHE_DIRS.clear()
                _mk._WHISPER_CACHE_DIRS.extend(orig)
    assert r["ok"] is True
    assert r["mode"] == "whisper"


# ---------------------------------------------------------------------------
# 允许直接 `python tests/test_media_keys_probe.py` 跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
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
    print(f"\n=== media_keys_probe: {passed} passed, {failed} failed ===")
    sys.exit(0 if failed == 0 else 1)
