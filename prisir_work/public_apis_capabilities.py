"""prisir_work/public_apis_capabilities.py — 公共 API 资源检索能力(P3j Phase C,2026-10-02)。

定位:把 ext-public-apis-promo 四个 registerCommand 挂进 PrisirWork 主对话能力体系,
     让 LLM 识别「找个免费 API / 推荐天气 API / 列 API 分类」类需求,自动 emit
     [[EXEC: api.find ...]] → 调 Node 扩展子进程拿资源卡片 HTML → ui.inject.card 注入会话。

设计:
  · **4 capability 全 L0**(只查本地 JSON 资源库,不出图不发外,符合红线③)
  · **endpoint handler 通过 _ext_rpc_call** 同步调 Node 扩展,timeout=4s(LLM 流式上下文)
  · **handler 直接转发扩展 result**(JSON 字段透传 html/meta)
  · **intent_summary 给 LLM 看** — 用自然语言说「能做什么 + EXEC 怎么写」

数据来源:public-apis/public-apis README(主对话+当前交互,2026-10-02 快照)
     51 分类 / 1953 条 API,字段含 Auth/HTTPS/CORS 三档。

用例:
    from prisir_work.public_apis_capabilities import register_all, intent_summary
    register_all()            # 副作用:往 capability._REGISTRY + endpoints._REGISTRY 写
    msgs = [{"role": "system", "content": intent_summary()}]
"""
from __future__ import annotations

import logging

log = logging.getLogger("prisir_work.public_apis_capabilities")

__all__ = [
    "register_all",
    "intent_summary",
    "EXT_ID",
    "PUBLIC_APIS_CAPABILITIES",
]


# 扩展 ID(主进程 spawn Node 子进程时用)
EXT_ID = "public-apis-promo"


# ---------------------------------------------------------------------------
# 4 capability 元数据 — 全部 L0
# ---------------------------------------------------------------------------

PUBLIC_APIS_CAPABILITIES = (
    {
        "id":          "api.find",
        "title":       "公共 API 搜索(关键词 + 分类双过滤)",
        "endpoint":    "/api/find",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("api", "API", "接口", "公开 API", "公共 API",
                         "free api", "free public api", "找 API", "公开接口",
                         "免费 API", "公共 API 端点", "open API", "endpoint"),
        "confirm":     "",
        "args_help":   "query(可选);category(可选,如 'Weather');limit(默认 10)",
        "example":     "找个免费的天气 API",
    },
    {
        "id":          "api.list_categories",
        "title":       "列出公共 API 全部分类",
        "endpoint":    "/api/list_categories",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("API 分类", "公开 API 分类", "公共 API 分类",
                         "api categories", "公开接口分类"),
        "confirm":     "",
        "args_help":   "无参数",
        "example":     "公共 API 都分哪些类别?",
    },
    {
        "id":          "api.detail",
        "title":       "查单个公共 API 详情(精确 / 模糊)",
        "endpoint":    "/api/detail",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("API 详情", "API 文档", "API detail", "公开 API 详情",
                         "查 API"),
        "confirm":     "",
        "args_help":   "name(必填,API 名,如 'OpenWeatherMap' / 'Cat Facts')",
        "example":     "OpenWeatherMap 这个 API 怎么用?",
    },
    {
        "id":          "api.random",
        "title":       "公共 API 随机推荐(分类内 / 全局)",
        "endpoint":    "/api/random",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("随机 API", "随便来一个 API", "推荐个 API",
                         "surprise api", "随机推荐 API"),
        "confirm":     "",
        "args_help":   "category(可选)",
        "example":     "随机来一个公共 API",
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
        log.warning("[api-cap] import registry failed: %s", e)
        return 0

    for c in PUBLIC_APIS_CAPABILITIES:
        try:
            _cap.register_capability(
                c["id"], title=c["title"], endpoint=c["endpoint"],
                method=c["method"], risk=c["risk"], auth=c["auth"],
                keywords=c["keywords"], confirm=c["confirm"],
            )
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("[api-cap] register %s failed: %s", c["id"], e)

    for c in PUBLIC_APIS_CAPABILITIES:
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

        # api.find / list_categories / detail / random 都可能返 type=text
        return ({"ok": True, **payload}, 200)

    return _handler


# ---------------------------------------------------------------------------
# intent_summary — 喂给 LLM 的能力介绍 + EXEC 提示
# ---------------------------------------------------------------------------

def intent_summary() -> str:
    """返回给 build_messages 拼进 system 第三段:公共 API 能力清单 + EXEC 写法示例。"""
    return """\
我手头有一份 public-apis/public-apis 的公共 API 资源库(51 分类 1953 条,含 Auth/HTTPS/CORS 三档),本地只读,响应快。

🔌 公共 API 搜索(关键词 + 分类):
  · "找个免费的天气 API"  → api.find query="weather"
  · "CDN 加速相关的 API"  → api.find category="Development"
  · "免费图床推荐"  → api.find query="image"

📚 列分类(让用户知道有哪些类可选):
  · "公共 API 都分哪些类"  → api.list_categories

🎯 查单个 API 详情:
  · "OpenWeatherMap 这个 API 怎么用"  → api.detail name="OpenWeatherMap"
  · "Cat Facts 是什么"  → api.detail name="Cat Facts"

🎲 随机推荐(惊喜):
  · "随机来一个 API"  → api.random
  · "随机推荐一个天气 API"  → api.random category="Weather"

需要我用 EXEC 标记触发 — 在回复末尾追加一行:
  [[EXEC: api.find query="weather" limit="10"]]
  [[EXEC: api.find category="Development"]]
  [[EXEC: api.list_categories]]
  [[EXEC: api.detail name="OpenWeatherMap"]]
  [[EXEC: api.random category="Weather"]]
参数全部用引号字符串。用户没指定分类时,先用 api.list_categories 让用户挑,或直接 api.find 用 query 模糊搜索。

⚠️ 用户问「公共 API / 公开 API / free api」时才调本能力;问「国内可访问的 API」改用 api_cn.* 能力(可问)。
⚠️ 数据快照 2026-10-02,具体 API endpoint 可能变动,卡片里附链接让用户核验最新状态。"""


# ---------------------------------------------------------------------------
# Module-level auto register — import 一次就把 4 capability + 4 endpoint 注入主进程。
# ---------------------------------------------------------------------------
_n = register_all()
log.info("[api-cap] auto-registered %d capability + endpoint", _n)