"""prisir_work/public_apis_cn_capabilities.py — 国内 API 资源检索能力(P3j Phase C,2026-10-02)。

定位:把 ext-public-apis-cn-promo 四个 registerCommand 挂进 PrisirWork 主对话能力体系,
     让 LLM 识别「找个国内 API / 国内可访问的 API / 中文 API」类需求,自动 emit
     [[EXEC: api_cn.find ...]] → 调 Node 扩展子进程拿资源卡片 → ui.inject.card 注入。

设计与 public-apis 平行,但:
  - 命令前缀 api_cn.*(避免与公共 API 的 api.* 冲突)
  - 中文 keywords 优先(api_cn.find 在用户说「国内」/「中文」时优先触发)
  - 数据来源 llf007/public-apis-cn(中文社区维护的国内可访问 API 清单)

用例:
    from prisir_work.public_apis_cn_capabilities import register_all, intent_summary
"""
from __future__ import annotations

import logging

log = logging.getLogger("prisir_work.public_apis_cn_capabilities")

__all__ = [
    "register_all",
    "intent_summary",
    "EXT_ID",
    "PUBLIC_APIS_CN_CAPABILITIES",
]


# 扩展 ID
EXT_ID = "public-apis-cn-promo"


# ---------------------------------------------------------------------------
# 4 capability 元数据 — 全部 L0
# ---------------------------------------------------------------------------

PUBLIC_APIS_CN_CAPABILITIES = (
    {
        "id":          "api_cn.find",
        "title":       "国内 API 搜索(关键词 + 分类)",
        "endpoint":    "/api_cn/find",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("国内 API", "中国 API", "中文 API", "国内可访问",
                         "国内接口", "中文接口", "中国接口", "国内免费的 API",
                         "国内服务", "中国服务", "国内公开 API"),
        "confirm":     "",
        "args_help":   "query(可选);category(可选,如 '天气');limit(默认 10)",
        "example":     "找个国内可访问的天气 API",
    },
    {
        "id":          "api_cn.list_categories",
        "title":       "列出国内 API 全部分类",
        "endpoint":    "/api_cn/list_categories",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("国内 API 分类", "中国 API 分类", "中文 API 分类",
                         "国内有哪些 API"),
        "confirm":     "",
        "args_help":   "无参数",
        "example":     "国内 API 都分哪些类?",
    },
    {
        "id":          "api_cn.detail",
        "title":       "查单个国内 API 详情(精确 / 模糊)",
        "endpoint":    "/api_cn/detail",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("国内 API 详情", "中国 API 详情", "查国内 API"),
        "confirm":     "",
        "args_help":   "name(必填,API 名,如 '高德地图' / '心知天气API')",
        "example":     "高德地图 API 怎么用?",
    },
    {
        "id":          "api_cn.random",
        "title":       "国内 API 随机推荐",
        "endpoint":    "/api_cn/random",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("随机国内 API", "随机来一个国内 API", "国内 API 推荐"),
        "confirm":     "",
        "args_help":   "category(可选)",
        "example":     "随机推荐一个国内天气 API",
    },
)


def register_all() -> int:
    n = 0
    try:
        from . import capability as _cap  # noqa: PLC0415
        from . import endpoints as _ep    # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log.warning("[api-cn-cap] import registry failed: %s", e)
        return 0

    for c in PUBLIC_APIS_CN_CAPABILITIES:
        try:
            _cap.register_capability(
                c["id"], title=c["title"], endpoint=c["endpoint"],
                method=c["method"], risk=c["risk"], auth=c["auth"],
                keywords=c["keywords"], confirm=c["confirm"],
            )
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("[api-cn-cap] register %s failed: %s", c["id"], e)

    for c in PUBLIC_APIS_CN_CAPABILITIES:
        ep_path = c["endpoint"]
        if ep_path in _ep._REGISTRY:
            continue
        cap_id = c["id"]
        _ep.register(ep_path, method=c["method"], risk=c["risk"], auth=c["auth"])(
            _make_handler(cap_id)
        )
    return n


def _make_handler(cap_id: str):
    method = cap_id.split(".", 1)[1] if "." in cap_id else cap_id

    def _handler(body: dict) -> tuple[dict, int]:
        body = body or {}
        try:
            from prisiragent_web import _ext_rpc_call  # noqa: PLC0415
        except Exception as e:  # noqa: BLE001
            return ({"ok": False, "error": f"ext_bridge_unavailable: {e}",
                     "html": "", "meta": {}}, 200)

        result = _ext_rpc_call(EXT_ID, method, body, timeout=4.0)

        if not isinstance(result, dict):
            return ({"ok": False, "error": f"bad_ext_result: {type(result).__name__}",
                     "html": "", "meta": {}}, 200)

        if "error" in result:
            return ({"ok": False, "error": result["error"],
                     "html": "", "meta": {}, "warning": "ext_unavailable"}, 200)

        payload = result.get("result")
        if not isinstance(payload, dict):
            return ({"ok": False, "error": "ext_returned_no_result",
                     "html": "", "meta": {}}, 200)

        return ({"ok": True, **payload}, 200)

    return _handler


def intent_summary() -> str:
    return """\
我手头有一份 llf007/public-apis-cn 的国内可访问 API 资源库(54 分类 1493 条,中文描述+国内可用,认证/HTTPS 双档),本地只读。

🇨🇳 国内 API 搜索(关键词 + 分类):
  · "找个国内可访问的天气 API"  → api_cn.find query="天气"
  · "国内地图 API"  → api_cn.find category="地理编码"
  · "国内有没有图片 API"  → api_cn.find query="图片"

📚 列分类:
  · "国内 API 都分哪些类"  → api_cn.list_categories

🎯 查单个 API 详情:
  · "高德地图 API 怎么用"  → api_cn.detail name="高德地图"
  · "心知天气API"  → api_cn.detail name="心知天气API"

🎲 随机推荐:
  · "随机来一个国内 API"  → api_cn.random
  · "随机推荐一个国内天气 API"  → api_cn.random category="天气"

需要我用 EXEC 标记触发 — 在回复末尾追加一行:
  [[EXEC: api_cn.find query="天气" limit="10"]]
  [[EXEC: api_cn.find category="地理编码"]]
  [[EXEC: api_cn.list_categories]]
  [[EXEC: api_cn.detail name="高德地图"]]
  [[EXEC: api_cn.random category="天气"]]
参数全部用引号字符串。

⚠️ 用户说「国内」/「中文」/「国内可访问」/「中国」才调本能力;问「国际公共 API」改用 api.* 能力(可问)。
⚠️ 数据快照 2026-10-02,具体 API endpoint 可能变动,卡片里附链接让用户核验最新状态。"""


_n = register_all()
log.info("[api-cn-cap] auto-registered %d capability + endpoint", _n)