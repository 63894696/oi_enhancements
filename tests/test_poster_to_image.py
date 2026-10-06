"""
tests/test_poster_to_image.py — P3j Phase D 测试(2026-09-27)。

验证:
  1. import poster_to_image_capability → 1 capability + 1 endpoint 注册
  2. _combine_prompt 三种 lang 拼接策略
  3. handler 缺 prompt → 降级 ok=False,error=missing_prompt
  4. handler video_creator 不可用 → 降级 ok=False,error 含 video_creator_unavailable
  5. handler image-gen creator 不在 registry → 降级 ok=False,error=image_gen_creator_not_registered
  6. handler creator.ready=False → 降级 ok=False,error=image_gen_not_ready
  7. handler creator.create() 失败 → 透传 r.error
  8. handler creator.create() 成功 → 透传 image_path
  9. handler 自定义 output 路径 → 自动 mkdir
 10. capability.search('出图') 命中 image-gen.from_poster_prompt
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 跟 Phase C 测试同款 stub — 不拖整个大链路
sys.modules["prisiragent_cli"] = mock.MagicMock(name="prisiragent_cli")

from prisir_work import capability as cap_mod  # noqa: E402
from prisir_work import endpoints as ep_mod     # noqa: E402
from prisir_work import poster_to_image_capability as p2i  # noqa: E402,F401
# 注意:不要 here import video_creator — session 里其他 test 跑过它,
# 提前 import 会让 sys.modules["prisir_work.video_creator"] 缓存真模块,
# test_5 的 mock.patch.dict(..., None) 会被 Python cache 短路 → handler
# 拿到真模块 + creator.ready=False → 报 image_gen_not_ready。
# 删去后 test_5 唯一一次 import video_creator 的尝试就落在 mock.patch.dict 里,
# sys.modules=None → handler 内 `from . import video_creator` 抛 ImportError
# → 走 video_creator_unavailable 分支。


# ── 1. 注册 ──────────────────────────────────────────────────────
def test_1_capability_registry():
    ids = [c["id"] for c in cap_mod.list_capabilities()]
    assert "image-gen.from_poster_prompt" in ids
    e = cap_mod.get("image-gen.from_poster_prompt")
    assert e["risk"] == "L0"
    assert e["endpoint"] == "/image-gen/from_poster_prompt"
    print("✓ image-gen.from_poster_prompt registered as L0")


def test_2_endpoint_registry():
    paths = list(ep_mod._REGISTRY.keys())
    assert "/image-gen/from_poster_prompt" in paths
    e = ep_mod._REGISTRY["/image-gen/from_poster_prompt"]
    assert e["method"] == "POST"
    assert e["risk"] == "L0"
    assert e["auth"] is True
    print("✓ /image-gen/from_poster_prompt endpoint registered")


# ── 2. prompt 拼接策略 ──────────────────────────────────────────
def test_3_combine_prompt_lang_strategies():
    zh = "风格:041 Dr. Seuss,克莱因蓝,主体:秋天奶茶"
    en = "Style 041 Dr. Seuss, Klein blue, subject: autumn milk tea"
    # both(默认)
    out = p2i._combine_prompt(zh, en, "both")
    assert out.startswith(zh), "both: zh 应该在前面"
    assert "EN:" in out and en in out, "both: EN 标记必须有"
    # zh only
    assert p2i._combine_prompt(zh, en, "zh") == zh
    # en only
    assert p2i._combine_prompt(zh, en, "en") == en
    # zh only(空 en)
    assert p2i._combine_prompt(zh, "", "both") == zh
    # en only(空 zh)
    assert p2i._combine_prompt("", en, "both") == en
    # 都空
    assert p2i._combine_prompt("", "", "both") == ""
    # 未知 lang → both
    assert p2i._combine_prompt(zh, en, "xx") == out
    print("✓ _combine_prompt handles 6 lang + edge cases")


# ── 3. handler 缺 prompt 降级 ───────────────────────────────────
def test_4_handler_missing_prompt():
    payload, status = ep_mod._REGISTRY["/image-gen/from_poster_prompt"]["handler"](
        {"output": "C:/tmp/x.png"}
    )
    assert status == 200
    assert payload["ok"] is False
    assert "missing_prompt" in payload["error"]
    assert payload["image_path"] == ""
    print("✓ handler rejects when both prompts empty")


# ── 4. video_creator 模块 import 失败 ──────────────────────────
def test_5_handler_video_creator_unavailable(monkeypatch):
    # poster_to_image_capability handler 用 _resolve_video_creator() 解析
    # video_creator 模块,优先查 sys.modules["prisir_work.video_creator"]。
    # 把 sys.modules 里这一项换成「任何 attribute 都抛 ImportError」的 boom,
    # handler 调 vc.get(...) 时 boom.__getattr__("get") 抛 ImportError,
    # 被外层 try 捕获 → 返回 video_creator_unavailable 分支。
    #
    # 注意:之前 `from . import video_creator as vc` 走 Python importlib
    # 内部 SourceFileLoader,绕过 sys.modules[None/boom] 仍返回真模块 ——
    # 这是 Python 设计如此。改为 sys.modules 优先路径后,sys.modules 污染
    # 能生效(handler 第 160 行有详细注释)。
    import importlib

    class _BoomModule:
        """任何 attribute access 都抛 ImportError — 模拟 module 不可用。"""
        def __getattr__(self, name):
            raise ImportError(f"_BoomModule: simulated video_creator unavailable (attr={name})")
        def __bool__(self):
            return True

    boom = _BoomModule()
    saved = sys.modules.get("prisir_work.video_creator")
    sys.modules["prisir_work.video_creator"] = boom
    importlib.invalidate_caches()
    try:
        payload, status = ep_mod._REGISTRY["/image-gen/from_poster_prompt"]["handler"](
            {"prompt_zh": "主体:奶茶", "output": "C:/tmp/x.png"}
        )
    finally:
        if saved is not None:
            sys.modules["prisir_work.video_creator"] = saved
        else:
            sys.modules.pop("prisir_work.video_creator", None)
        importlib.invalidate_caches()
    assert status == 200
    assert payload["ok"] is False
    assert "video_creator_unavailable" in payload["error"], f"got {payload}"
    print("✓ handler degrades when video_creator missing")


# ── 5. image-gen creator 不在 registry ────────────────────────
def test_6_handler_creator_not_registered():
    # 用真 video_creator 但 get('image-gen') 返 None
    with mock.patch("prisir_work.video_creator.get", return_value=None):
        payload, status = ep_mod._REGISTRY["/image-gen/from_poster_prompt"]["handler"](
            {"prompt_zh": "主体:奶茶", "output": "C:/tmp/x.png"}
        )
    assert status == 200
    assert payload["ok"] is False
    assert payload["error"] == "image_gen_creator_not_registered"
    print("✓ handler degrades when image-gen not registered")


# ── 6. creator.ready=False ─────────────────────────────────────
def test_7_handler_creator_not_ready():
    fake_creator = mock.MagicMock()
    fake_creator.ready = False
    fake_creator.status.return_value = {"ready": False, "hint": "缺 SILICONFLOW_API_KEY"}
    with mock.patch("prisir_work.video_creator.get", return_value=fake_creator):
        payload, status = ep_mod._REGISTRY["/image-gen/from_poster_prompt"]["handler"](
            {"prompt_zh": "主体:奶茶", "output": "C:/tmp/x.png"}
        )
    assert status == 200
    assert payload["ok"] is False
    assert payload["error"] == "image_gen_not_ready"
    assert "SILICONFLOW" in payload["hint"]
    print("✓ handler degrades when image-gen not ready + carries hint")


# ── 7. creator.create 失败 ─────────────────────────────────────
def test_8_handler_create_failed():
    fake_creator = mock.MagicMock()
    fake_creator.ready = True
    # 模拟 VideoCreateResult 结构
    from prisir_work.video_creator import VideoCreateResult
    fake_creator.create.return_value = VideoCreateResult(
        ok=False, creator="image-gen",
        error="rc=1: prompt rejected",
        raw={"stderr_tail": "ERROR: invalid token"},
    )
    with mock.patch("prisir_work.video_creator.get", return_value=fake_creator):
        payload, status = ep_mod._REGISTRY["/image-gen/from_poster_prompt"]["handler"](
            {"prompt_zh": "主体:奶茶", "output": "C:/tmp/poster.png"}
        )
    assert status == 200
    assert payload["ok"] is False
    assert "rc=1" in payload["error"]
    assert "stderr_tail" in payload
    assert "invalid token" in payload["stderr_tail"]
    print("✓ handler surfaces creator error + stderr tail")


# ── 8. creator.create 成功 ─────────────────────────────────────
def test_9_handler_create_success(tmp_path=None):
    fake_creator = mock.MagicMock()
    fake_creator.ready = True
    from prisir_work.video_creator import VideoCreateResult
    output = "C:/tmp/prisir_test_poster.png"
    fake_creator.create.return_value = VideoCreateResult(
        ok=True, creator="image-gen",
        artifact={"image_paths": [output]},
    )
    with mock.patch("prisir_work.video_creator.get", return_value=fake_creator):
        payload, status = ep_mod._REGISTRY["/image-gen/from_poster_prompt"]["handler"](
            {"prompt_zh": "风格:041 Dr. Seuss", "prompt_en": "Style 041 Dr. Seuss",
             "output": output, "size": "1024x1024", "lang": "both"}
        )
    assert status == 200
    assert payload["ok"] is True
    from pathlib import Path as _P
    assert _P(payload["image_path"]) == _P(output), f"paths differ: {payload['image_path']!r} vs {output!r}"
    assert payload["image_paths"] == [output]
    assert payload["size"] == "1024x1024"
    assert payload["lang"] == "both"
    # 验证 create() 被以 combined prompt 调
    call = fake_creator.create.call_args
    assert "Dr. Seuss" in call.kwargs["prompt"]
    assert _P(call.kwargs["output"]) == _P(output)
    assert call.kwargs["size"] == "1024x1024"
    print("✓ handler success path forwards combined prompt + image_paths")


# ── 9. 默认 output 路径 + 父目录 mkdir ────────────────────────
def test_10_handler_default_output_path(tmp_path=None):
    """output 缺省 → ~/Pictures/prisIr-posters/poster-*.png + 自动 mkdir。"""
    fake_creator = mock.MagicMock()
    fake_creator.ready = True
    from prisir_work.video_creator import VideoCreateResult
    fake_creator.create.return_value = VideoCreateResult(
        ok=True, creator="image-gen",
        artifact={"image_paths": []},
    )
    with mock.patch("prisir_work.video_creator.get", return_value=fake_creator):
        payload, status = ep_mod._REGISTRY["/image-gen/from_poster_prompt"]["handler"](
            {"prompt_zh": "主体:奶茶"}  # 没 output
        )
    assert status == 200
    assert payload["ok"] is True
    norm = payload["image_path"].replace("\\", "/")
    assert "Pictures/prisIr-posters/poster-" in norm, f"got {payload['image_path']!r}"
    # mkdir 已发生(Pictures/prisIr-posters 存在)
    out_dir = Path.home() / "Pictures" / "prisIr-posters"
    assert out_dir.is_dir(), f"expected {out_dir} to exist after handler ran"
    print(f"✓ handler default output → {payload['image_path']}")


# ── 10. capability.search 命中 ─────────────────────────────────
def test_11_capability_search_hits():
    for q in ("出图", "海报出图", "render", "draw"):
        hits = cap_mod.search(q)
        ids = [h["id"] for h in hits]
        assert "image-gen.from_poster_prompt" in ids, f"query={q!r} missed"
    print("✓ capability.search('出图'/'海报出图'/'render'/'draw') all hit")


if __name__ == "__main__":
    test_1_capability_registry()
    test_2_endpoint_registry()
    test_3_combine_prompt_lang_strategies()
    test_4_handler_missing_prompt()
    test_5_handler_video_creator_unavailable()
    test_6_handler_creator_not_registered()
    test_7_handler_creator_not_ready()
    test_8_handler_create_failed()
    test_9_handler_create_success()
    test_10_handler_default_output_path()
    test_11_capability_search_hits()
    print("\n所有 11 组断言通过 ✅")
