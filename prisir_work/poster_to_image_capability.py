"""prisir_work/poster_to_image_capability.py — poster 卡片 → image-gen 出图闭环(P3j Phase D,2026-09-27)。

定位:把 handraw-style 卡片 HTML 里的双语 prompt 喂给 video_creator.ImageGenCreator,
     形成「主对话 → poster 卡片 → 一键出图」完整 UX。

设计:
  · **1 capability L0**:`image-gen.from_poster_prompt { prompt_zh, prompt_en, output, size, lang }`
    — 用户看到 poster 卡片说「出图」时,LLM emit EXEC 触发本能力。
  · **handler 同步调 video_creator.get("image-gen").create(...)** — 复用 P3j T11 已 ship 的
    ImageGenCreator(走 Easel ai_image.py subprocess,SILICONFLOW_API_KEY),失败降级 200 + ok=False。
  · **prompt 拼接策略**:`lang=zh|en|both`(默认 both → "zh。\n\nEN: <en>"),中英双语交给模型更稳。
  · **output 必填** — 路径由前端/调用方给(典型:主对话 capture / 用户的 Pictures 目录),
    测试里 mock 路径。
  · **不进 capability 注入**:poster_to_image 是 poster 之后的下游动作,走 EXEC 协议自动触发,
    不污染 LLM system prompt 长度。但 intent_summary 仍写一行提示 LLM「用户要出图时跟 EXEC」。

用例:
    from prisir_work.poster_to_image_capability import register_all, intent_summary
    register_all()            # 副作用:往 capability._REGISTRY + endpoints._REGISTRY 写
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("prisir_work.poster_to_image_capability")


# 模块级 import video_creator + 保留相对引用备用 —
# 让 sys.modules 污染能拦到:测试里若 sys.modules["prisir_work.video_creator"]
# 被换成 boom 对象,_resolve_video_creator 优先走 sys.modules 检查就能拿到 boom。
# (原方案用 `from . import video_creator as vc` 在函数体内每次执行,
# Python importlib 走 SourceFileLoader 绕过 sys.modules[None/boom] 检查,
# 仍返回真模块 → 测试无法模拟不可用。)
try:
    from . import video_creator as _video_creator_module  # type: ignore
except Exception:  # noqa: BLE001
    _video_creator_module = None  # 让 _resolve_video_creator 走 sys.modules fallback


def _resolve_video_creator():
    """解析 video_creator 模块 — 优先 sys.modules(可被测试污染),
    再回退到模块级相对引用。"""
    cached = sys.modules.get("prisir_work.video_creator")
    if cached is not None:
        return cached
    return _video_creator_module

__all__ = [
    "register_all",
    "intent_summary",
    "POSTER_TO_IMAGE_CAPABILITIES",
]


# ---------------------------------------------------------------------------
# capability 元数据 — L0(本地出图,不出外,SILICONFLOW 经 Easel 子进程)
# ---------------------------------------------------------------------------

POSTER_TO_IMAGE_CAPABILITIES = (
    {
        "id":       "image-gen.from_poster_prompt",
        "title":    "poster 卡片 prompt 一键出图(中英双语)",
        "endpoint": "/image-gen/from_poster_prompt",
        "method":   "POST",
        "risk":     "L0",
        "auth":     True,
        "keywords": ("海报出图", "出图", "生成图片", "image-gen", "poster", "出海报",
                     "t2i", "画出来", "render", "draw"),
        "confirm":  "",
        "args_help": (
            "prompt_zh(必填,中文 prompt,从 poster 卡片 <pre data-role=\"zh\"> 抓);"
            "prompt_en(可选,英文 prompt);output(必填,产物 png 路径);"
            "size(可选,默认 1024x1024);lang(可选,zh|en|both,默认 both)"
        ),
        "example": (
            "[[EXEC: image-gen.from_poster_prompt "
            "prompt_zh=\"风格:041 Dr. Seuss...\" "
            "prompt_en=\"Style 041...\" "
            "output=\"C:/Users/me/Pictures/poster.png\" "
            "size=\"1024x1024\" lang=\"both\"]]"
        ),
    },
)


# ---------------------------------------------------------------------------
# prompt 拼接 + 路径处理
# ---------------------------------------------------------------------------

def _combine_prompt(prompt_zh: str, prompt_en: str, lang: str) -> str:
    """按 lang 拼接中英 prompt。空字符串自动跳过。"""
    zh = (prompt_zh or "").strip()
    en = (prompt_en or "").strip()
    if lang == "zh":
        return zh or en
    if lang == "en":
        return en or zh
    # both(默认)
    if zh and en:
        return f"{zh}\n\nEN: {en}"
    return zh or en


def _default_output(size_tag: str = "") -> str:
    """output 缺省 → ~/Pictures/prisIr-posters/poster-{ts}.png,自动 mkdir。"""
    import time
    base = Path.home() / "Pictures" / "prisIr-posters"
    base.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    return str(base / f"poster-{ts}{size_tag}.png")


# ---------------------------------------------------------------------------
# 注册入口
# ---------------------------------------------------------------------------

def register_all() -> int:
    """注册 1 capability + 1 endpoint handler。返回成功数(通常 1)。"""
    n = 0
    try:
        from . import capability as _cap  # noqa: PLC0415
        from . import endpoints as _ep    # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log.warning("[poster2img] import registry failed: %s", e)
        return 0

    for c in POSTER_TO_IMAGE_CAPABILITIES:
        try:
            _cap.register_capability(
                c["id"], title=c["title"], endpoint=c["endpoint"],
                method=c["method"], risk=c["risk"], auth=c["auth"],
                keywords=c["keywords"], confirm=c["confirm"],
            )
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("[poster2img] register %s failed: %s", c["id"], e)

    for c in POSTER_TO_IMAGE_CAPABILITIES:
        ep_path = c["endpoint"]
        if ep_path in _ep._REGISTRY:
            continue
        cap_id = c["id"]
        _ep.register(ep_path, method=c["method"], risk=c["risk"], auth=c["auth"])(
            _make_handler(cap_id)
        )
    return n


def _make_handler(cap_id: str):
    def _handler(body: dict) -> tuple[dict, int]:
        body = body or {}
        prompt_zh = (body.get("prompt_zh") or "").strip()
        prompt_en = (body.get("prompt_en") or "").strip()
        lang = (body.get("lang") or "both").strip().lower()
        if lang not in ("zh", "en", "both"):
            lang = "both"
        size = (body.get("size") or "1024x1024").strip()
        output = (body.get("output") or "").strip()

        if not (prompt_zh or prompt_en):
            return ({"ok": False, "error": "missing_prompt: prompt_zh 或 prompt_en 必填其一",
                     "image_path": ""}, 200)

        if not output:
            output = _default_output()

        # output 路径准备(允许父目录自动创建)
        try:
            Path(output).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            output = str(Path(output).expanduser().resolve())
        except Exception as e:  # noqa: BLE001
            return ({"ok": False, "error": f"bad_output_path: {e}",
                     "image_path": ""}, 200)

        combined = _combine_prompt(prompt_zh, prompt_en, lang)

        # 调 video_creator.ImageGenCreator
        # 用 _resolve_video_creator 替代 `from . import video_creator as vc` —
        # 后者在函数体内每次执行,Python importlib 走 SourceFileLoader 绕过
        # sys.modules[None] / sys.modules[boom] 仍能加载真模块,导致测试无法
        # 模拟不可用分支。sys.modules 优先路径让测试能注入 boom/MagicMock。
        try:
            vc = _resolve_video_creator()
            if vc is None:
                raise ImportError("video_creator module-level import failed")
            creator = vc.get("image-gen")
        except Exception as e:  # noqa: BLE001
            return ({"ok": False, "error": f"video_creator_unavailable: {e}",
                     "image_path": ""}, 200)
        if creator is None:
            return ({"ok": False, "error": "image_gen_creator_not_registered",
                     "image_path": ""}, 200)
        if not creator.ready:
            # 给前端友好降级(走 status() 拿 hint)
            try:
                hint = creator.status().get("hint", "")
            except Exception:
                hint = ""
            return ({"ok": False, "error": "image_gen_not_ready",
                     "hint": hint, "image_path": ""}, 200)

        try:
            r = creator.create(prompt=combined, output=output, size=size, count=1)
        except Exception as e:  # noqa: BLE001
            return ({"ok": False, "error": f"image_gen_raised: {type(e).__name__}: {e}",
                     "image_path": ""}, 200)

        if not r.ok:
            err_msg = r.error or "image_gen_failed"
            # 加上 stdout_tail / stderr_tail 摘要便于排查
            raw = r.raw or {}
            tail = (raw.get("stderr_tail") or "")[-300:]
            return ({"ok": False, "error": err_msg,
                     "image_path": "", "stderr_tail": tail}, 200)

        # 成功 — 透传 artifact
        artifact = r.artifact or {}
        image_paths = artifact.get("image_paths") or ([output] if Path(output).is_file() else [])
        return ({"ok": True,
                 "image_path": output,
                 "image_paths": image_paths,
                 "size": size, "lang": lang,
                 "artifact": artifact}, 200)

    return _handler


# ---------------------------------------------------------------------------
# intent_summary — 喂给 LLM 的下游动作提示
# ---------------------------------------------------------------------------

def intent_summary() -> str:
    return """\
🎨 poster 卡片出图(可选下游):
  · 用户说「出图 / 画出来 / 生成这张海报」时,跟 EXEC 触发 image-gen.from_poster_prompt
  · prompt_zh/prompt_en 从 poster 卡片的 <pre data-role="zh">/<pre data-role="en"> 抓
  · 示例:
    [[EXEC: image-gen.from_poster_prompt prompt_zh="风格:041 Dr. Seuss..." prompt_en="Style 041..." output="C:/Users/me/Pictures/poster.png" size="1024x1024" lang="both"]]
  · 走 Easel 子进程 image-gen,需要 SILICONFLOW_API_KEY(在 ~/work/zju_easel/.env)
  · 没要求出图时不要触发(只是看看 prompt 用 poster.smart/spec/ai_design 即可)"""


# ---------------------------------------------------------------------------
# Module-level auto-register
# ---------------------------------------------------------------------------

_n = register_all()
log.info("[poster2img] auto-registered %d capability + endpoint", _n)
