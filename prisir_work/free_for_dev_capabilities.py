"""prisir_work/free_for_dev_capabilities.py — 免费资源检索能力(P3j Phase C,2026-09-27)。

定位:把 ext-free-for-dev-promo 四个 registerCommand 挂进 PrisirWork 主对话能力体系
     (capability + endpoint + intent_summary),让 LLM 在主对话里能识别「找个免费
     Postgres / 推荐一个图床 / 列下设计工具分类」等需求,自动输出
     [[EXEC: free.find ...]] → 调扩展拿资源卡片 HTML → ui.inject.card 注入会话。

设计:
  · **4 capability 全 L0**(只产资源卡片,不出图不发外,符合红线③)。
  · **endpoint handler 通过 _ext_rpc_call** 同步调 Node 扩展子进程,timeout=4s
    (LLM 流式上下文,不能阻塞太久;失败降级返 200 + ok=False + warning)。
  · **handler 直接转发扩展 result** 不另起一层面包(JSON 字段透传 html / meta)。
  · **intent_summary 给 LLM 看** — 用自然语言说「能做什么 + EXEC 怎么写」。
  · **不挂 onSessionMessage** — 全部走 EXEC 标记协议,跟 poster / video 同链路。

用例:
    from prisir_work.free_for_dev_capabilities import register_all, intent_summary
    register_all()            # 副作用:往 capability._REGISTRY + endpoints._REGISTRY 写
    msgs = [{"role": "system", "content": intent_summary()}]
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("prisir_work.free_for_dev_capabilities")

__all__ = [
    "register_all",
    "intent_summary",
    "EXT_ID",
    "FREE_FOR_DEV_CAPABILITIES",
]


# 扩展 ID(主进程 spawn Node 子进程时用)
EXT_ID = "free-for-dev-promo"


# ---------------------------------------------------------------------------
# 4 capability 元数据 — 全部 L0(只查本地 JSON 资源库,不出图不发外)
# ---------------------------------------------------------------------------

FREE_FOR_DEV_CAPABILITIES = (
    {
        "id":          "free.find",
        "title":       "免费资源搜索(关键词 + 分类双过滤)",
        "endpoint":    "/free/find",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("免费", "free", "免费资源", "找免费", "免费工具",
                         "推荐", "免费的 X", "有没有免费的"),
        "confirm":     "",
        "args_help":   "query(可选);category(可选,如 'CDN and Protection');limit(默认 10)",
        "example":     "找个免费的 Postgres 数据库",
    },
    {
        "id":          "free.list_categories",
        "title":       "列出免费资源全部分类",
        "endpoint":    "/free/list_categories",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("免费资源分类", "免费分类", "有什么免费", "免费工具分类",
                         "free categories"),
        "confirm":     "",
        "args_help":   "无参数",
        "example":     "免费资源都分哪些类别?",
    },
    {
        "id":          "free.detail",
        "title":       "查单个免费资源详情(精确 / 模糊)",
        "endpoint":    "/free/detail",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("免费 X 详情", "免费 X 怎么样", "free detail",
                         "看看 X 的免费额度"),
        "confirm":     "",
        "args_help":   "name(必填,服务名,如 GitHub / AWS > CloudFront)",
        "example":     "GitHub 免费的额度是多少?",
    },
    {
        "id":          "free.random",
        "title":       "免费资源随机推荐(分类内 / 全局)",
        "endpoint":    "/free/random",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("随机推荐", "随便来一个", "给我推荐一个免费",
                         "惊喜一下", "surprise me"),
        "confirm":     "",
        "args_help":   "category(可选)",
        "example":     "随机推荐一个免费 AI 工具",
    },
)


# ---------------------------------------------------------------------------
# 注册入口(副作用)
# ---------------------------------------------------------------------------

def register_all() -> int:
    """把 4 个 capability + endpoint 注册到主进程 registry。返回成功数。"""
    n = 0
    try:
        from . import capability as _cap  # noqa: PLC0415
        from . import endpoints as _ep    # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log.warning("[free-cap] import registry failed: %s", e)
        return 0

    for c in FREE_FOR_DEV_CAPABILITIES:
        try:
            _cap.register_capability(
                c["id"], title=c["title"], endpoint=c["endpoint"],
                method=c["method"], risk=c["risk"], auth=c["auth"],
                keywords=c["keywords"], confirm=c["confirm"],
            )
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("[free-cap] register %s failed: %s", c["id"], e)

    for c in FREE_FOR_DEV_CAPABILITIES:
        ep_path = c["endpoint"]
        if ep_path in _ep._REGISTRY:
            continue
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
            return ({"ok": False, "error": result["error"],
                     "html": "", "meta": {}, "warning": "ext_unavailable"}, 200)

        # 扩展返 {result: {type, html, meta, ...}}
        payload = result.get("result")
        if not isinstance(payload, dict):
            return ({"ok": False, "error": "ext_returned_no_result",
                     "html": "", "meta": {}}, 200)

        # free.find / free.list_categories / free.detail / free.random 都可能返 type=text
        # (找不到、参数缺失、模糊匹配多结果)— 前端看 type 决定渲染卡片还是纯文本气泡
        return ({"ok": True, **payload}, 200)

    return _handler


# ---------------------------------------------------------------------------
# intent_summary — 喂给 LLM 的能力介绍 + EXEC 提示
# ---------------------------------------------------------------------------

def intent_summary() -> str:
    """返回给 build_messages 拼进 system 第三段:免费资源能力清单 + EXEC 写法示例。"""
    return """\
我手头有一份 ripienaar/free-for-dev 的免费资源导航库(57 分类 1300+ 条,SaaS/PaaS/IaaS 等),本地只读,响应快。

🔍 免费资源搜索(关键词 + 分类):
  · "找个免费的 Postgres 数据库"  → free.find query="postgres"
  · "CDN 免费的有什么"  → free.find category="CDN and Protection"
  · "免费图床推荐"  → free.find query="图床 image hosting"

📚 列分类(让用户知道有哪些类可选):
  · "免费资源都分哪些类"  → free.list_categories

🎯 查单个资源详情:
  · "GitHub 免费的额度是多少"  → free.detail name="GitHub"
  · "AWS CloudFront 免费多少"  → free.detail name="Amazon Web Services > CloudFront"

🎲 随机推荐(惊喜):
  · "随机来一个免费 AI 工具"  → free.random category="Generative AI"

需要我用 EXEC 标记触发 — 在回复末尾追加一行:
  [[EXEC: free.find query="postgres" limit="10"]]
  [[EXEC: free.find category="CDN and Protection"]]
  [[EXEC: free.list_categories]]
  [[EXEC: free.detail name="GitHub"]]
  [[EXEC: free.random category="Generative AI"]]
参数全部用引号字符串。用户没指定分类时,先用 free.list_categories 让用户挑,或直接 free.find 用 query 模糊搜索。

⚠️ 用户问「免费」时才触发;问付费、订阅、定价不调本能力。
⚠️ 数据快照 2026-09-27,具体免费额度可能变化,卡片里附链接让用户自己核验最新政策。"""


# ---------------------------------------------------------------------------
# Module-level auto register — import 一次就把 4 capability + 4 endpoint 注入主进程。
# ---------------------------------------------------------------------------
_n = register_all()
log.info("[free-cap] auto-registered %d capability + endpoint", _n)
