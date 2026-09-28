"""prisir_work/poster_capabilities.py — 手绘海报 prompt 能力(P3j Phase C,2026-09-27)。

定位:把 ext-handraw-style-prompter 三个 registerCommand 挂进 PrisirWork 主对话能力体系
     (capability + endpoint + intent_summary),让 LLM 在主对话里能识别「做个海报 /
     041号风格 / 城市夜景独白」等需求,自动输出 [[EXEC: poster.spec ...]] → 调扩展拿 prompt
     卡片 HTML → ui.inject.card 注入会话。

设计:
  · **3 capability 全 L0**(只产 prompt 卡片,不出图不发外,符合红线③)。
  · **endpoint handler 通过 _ext_rpc_call** 同步调 Node 扩展子进程,timeout=4s
    (LLM 流式上下文,不能阻塞太久;失败降级返 200 + ok=False + warning)。
  · **handler 直接转发扩展 result** 不另起一层面包(JSON 字段透传 html / meta);
    scan_and_exec 的 execute 路径只读 ok 字段做风险门,前端读 html 字段渲染卡片。
  · **intent_summary 给 LLM 看** — 用自然语言说「能做什么 + EXEC 怎么写」,与
    P3j T16-B video 同源风格。
  · **不挂 onSessionMessage** — 全部走 EXEC 标记协议,跟 video 能力同链路。

用例:
    from prisir_work.poster_capabilities import register_all, intent_summary
    register_all()            # 副作用:往 capability._REGISTRY + endpoints._REGISTRY 写
    msgs = [{"role": "system", "content": intent_summary()}]
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("prisir_work.poster_capabilities")

__all__ = [
    "register_all",
    "intent_summary",
    "EXT_ID",
    "POSTER_CAPABILITIES",
]


# 扩展 ID(主进程 spawn Node 子进程时用)
EXT_ID = "handraw-style-prompter"


# ---------------------------------------------------------------------------
# 3 capability 元数据 — 全部 L0(只产 prompt,不出图不发外)
# ---------------------------------------------------------------------------

POSTER_CAPABILITIES = (
    {
        "id":          "poster.smart",
        "title":       "手绘海报智能推荐(从主题自动匹配风格 + 颜色)",
        "endpoint":    "/poster/smart",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("海报", "宣传", "封面", "poster", "prompt", "手绘", "海报设计"),
        "confirm":     "",
        "args_help":   "theme(必填,主题描述,中文/英文均可)",
        "example":     "做个海报,主题:秋天的第一杯奶茶",
    },
    {
        "id":          "poster.spec",
        "title":       "手绘海报精确指定(用户已选风格编号 / 颜色 / 版式)",
        "endpoint":    "/poster/spec",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("041", "海报", "风格编号", "精确", "海报设计", "指定风格"),
        "confirm":     "",
        "args_help":   "subject(必填);style/color/layout 三个可选编号",
        "example":     "用 041 号风格 + C-25 爱马仕橙 + SC-001 排版,做个春节回家的海报",
    },
    {
        "id":          "poster.ai_design",
        "title":       "AI 海报设计(8 字段结构化:场景 / 受众 / 密度 / 情绪 ...)",
        "endpoint":    "/poster/ai_design",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("海报设计", "八字段", "AI 设计", "poster"),
        "confirm":     "",
        "args_help":   "subject(必填);scene/audience/density/emotion/color/style/layout 可选",
        "example":     "AI 海报设计,主体咖啡店手账,场景雨后街道,受众都市女性,密度 4,情绪温暖",
    },
)


# ---------------------------------------------------------------------------
# 注册入口(副作用)
# ---------------------------------------------------------------------------

def register_all() -> int:
    """把 3 个 capability + endpoint 注册到主进程 registry。返回成功数。

    安全:重复 register 会覆盖既有同名 entry(能力 ID 唯一,endpoint 唯一)。
    返回 0 通常是 endpoint handler 已存在或扩展未启,不影响主进程启动。
    """
    n = 0
    try:
        from . import capability as _cap  # noqa: PLC0415
        from . import endpoints as _ep    # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log.warning("[poster-cap] import registry failed: %s", e)
        return 0

    # 1) capability
    for c in POSTER_CAPABILITIES:
        try:
            _cap.register_capability(
                c["id"], title=c["title"], endpoint=c["endpoint"],
                method=c["method"], risk=c["risk"], auth=c["auth"],
                keywords=c["keywords"], confirm=c["confirm"],
            )
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("[poster-cap] register %s failed: %s", c["id"], e)

    # 2) endpoint handler(只注册一次 — 重复会覆盖)
    for c in POSTER_CAPABILITIES:
        ep_path = c["endpoint"]
        if ep_path in _ep._REGISTRY:
            continue
        # 闭包捕获 cap_id
        cap_id = c["id"]
        _ep.register(ep_path, method=c["method"], risk=c["risk"], auth=c["auth"])(
            _make_handler(cap_id)
        )
    return n


def _make_handler(cap_id: str):
    """构造一个 endpoint handler:调 Node 扩展子进程,失败降级返 200 + ok=False。"""
    method = cap_id.split(".", 1)[1] if "." in cap_id else cap_id

    def _handler(body: dict) -> tuple[dict, int]:
        body = body or {}
        try:
            from prisiragent_web import _ext_rpc_call  # noqa: PLC0415
        except Exception as e:  # noqa: BLE001
            return ({"ok": False, "error": f"ext_bridge_unavailable: {e}",
                     "html": "", "meta": {}}, 200)

        # 调用扩展子进程 — 4s 超时
        result = _ext_rpc_call(EXT_ID, method, body, timeout=4.0)

        if not isinstance(result, dict):
            return ({"ok": False, "error": f"bad_ext_result: {type(result).__name__}",
                     "html": "", "meta": {}}, 200)

        if "error" in result:
            # 扩展未启动 / 超时 / 进程崩 — 给前端友好降级
            return ({"ok": False, "error": result["error"],
                     "html": "", "meta": {}, "warning": "ext_unavailable"}, 200)

        # 扩展返 {result: {type, html, meta, ...}}
        payload = result.get("result")
        if not isinstance(payload, dict):
            return ({"ok": False, "error": "ext_returned_no_result",
                     "html": "", "meta": {}}, 200)

        # 把扩展的 type=card 透传,前端读 html 字段
        return ({"ok": True, **payload}, 200)

    return _handler


# ---------------------------------------------------------------------------
# intent_summary — 喂给 LLM 的能力介绍 + EXEC 提示
# ---------------------------------------------------------------------------

def intent_summary() -> str:
    """返回给 build_messages 拼进 system 第二段:海报能力清单 + EXEC 写法示例。"""
    return """\
我可以帮你做手绘风格的海报 prompt(中英双语,你可以复制去 Midjourney / DALL-E / gpt-image-2 等工具出图):

🎨 手绘海报智能推荐:
  · "做个海报,主题:秋天的第一杯奶茶"
  · "来个海报,亲子周末"

🎯 精确指定(用户已选编号):
  · 风格 001~278(手绘风格,如 041=Dr. Seuss, 042=Beatrix Potter)
  · 颜色 C-01~C-36(36 种单色主题色,如 C-01=克莱因蓝, C-25=爱马仕橙)
  · 版式 SC-001~SC-020(社媒卡)/ IG-001~IG-033(信息图)/ SB-001~SB-068(漫画分镜)
  · "用 041 号风格 + C-25 爱马仕橙 + SC-001 排版,做个春节回家的海报"

🪄 AI 海报设计(8 字段结构化):
  · "AI 海报设计,主体咖啡店手账,场景雨后街道,受众都市女性,密度 4,情绪温暖"

需要我用 EXEC 标记触发 — 在回复末尾追加一行(只在用户明确要海报时):
  [[EXEC: poster.smart theme="秋天的第一杯奶茶"]]
  [[EXEC: poster.spec subject="春节回家" style="041" color="C-25" layout="SC-001"]]
  [[EXEC: poster.ai_design subject="咖啡店手账" scene="雨后街道" audience="都市女性" density="4" emotion="温暖"]]
参数全部用引号字符串。用户没指定风格/颜色时,走 poster.smart 自动推荐。"""


# ---------------------------------------------------------------------------
# Module-level auto register — import 一次就把 3 capability + 3 endpoint 注入主进程。
# 主进程(prisIragent_web.py / companion)只要 `from prisir_work import poster_capabilities`
# 就生效,不需要再调 register_all()(仍然提供以备调试)。
# ---------------------------------------------------------------------------
_n = register_all()
log.info("[poster-cap] auto-registered %d capability + endpoint", _n)