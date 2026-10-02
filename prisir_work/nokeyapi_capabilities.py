"""prisir_work/nokeyapi_capabilities.py — 免 key/试用/开源 API 资源检索能力(P3j Phase C,2026-10-02)。

定位:把 ext-n0shake-public-apis-promo 四个 registerCommand 挂进 PrisirWork 主对话能力体系,
     让 LLM 识别「免 key API / 免注册 API / 免授权 API / 开源 API / 试用 API」类需求,自动 emit
     [[EXEC: nokeyapi.find ...]] → 调 Node 扩展子进程拿资源卡片 → ui.inject.card 注入会话。

设计与 public-apis / api_cn 平行,但:
  - 命令前缀 nokeyapi.*(避免与 api.* / api_cn.* 冲突)
  - 关键词侧重「免 key / 免注册 / 开源 / 试用」(区别于 api.* 的「公开 API / 国际公开」)
  - 数据来源 n0shake/Public-APIs(56 cat / 481 svc,3 列 markdown 表 + open_trial 字段)

用例:
    from prisir_work.nokeyapi_capabilities import register_all, intent_summary
"""
from __future__ import annotations

import logging

log = logging.getLogger("prisir_work.nokeyapi_capabilities")

__all__ = [
    "register_all",
    "intent_summary",
    "EXT_ID",
    "NOKEYAPI_CAPABILITIES",
]


# 扩展 ID
EXT_ID = "n0shake-public-apis-promo"


# ---------------------------------------------------------------------------
# 4 capability 元数据 — 全部 L0
# ---------------------------------------------------------------------------

NOKEYAPI_CAPABILITIES = (
    {
        "id":          "nokeyapi.find",
        "title":       "免 key / 试用 / 开源 API 搜索(关键词 + 分类)",
        "endpoint":    "/nokeyapi/find",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("免 key", "免 key API", "免注册 API", "免授权 API",
                         "免费 API", "no key api", "keyless api",
                         "试用 API", "trial API", "开源 API",
                         "open source api", "open source", "免费试用"),
        "confirm":     "",
        "args_help":   "query(可选);category(可选,如 'Music');limit(默认 10)",
        "example":     "找个免 key 的音乐 API",
    },
    {
        "id":          "nokeyapi.list_categories",
        "title":       "列出免 key API 全部分类",
        "endpoint":    "/nokeyapi/list_categories",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("免 key API 分类", "无 key API 分类", "试用 API 分类",
                         "开源 API 分类", "keyless categories"),
        "confirm":     "",
        "args_help":   "无参数",
        "example":     "免 key API 都分哪些类?",
    },
    {
        "id":          "nokeyapi.detail",
        "title":       "查单个免 key API 详情",
        "endpoint":    "/nokeyapi/detail",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("免 key API 详情", "无 key API 详情", "开源 API 详情",
                         "试用 API 详情"),
        "confirm":     "",
        "args_help":   "name(必填,API 名,如 'Spotify' / 'GitHub Licenses API')",
        "example":     "Spotify 这个 API 需要 key 吗?",
    },
    {
        "id":          "nokeyapi.random",
        "title":       "免 key API 随机推荐",
        "endpoint":    "/nokeyapi/random",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("随机免 key API", "随机来一个免 key API", "推荐个免 key API"),
        "confirm":     "",
        "args_help":   "category(可选)",
        "example":     "随机推荐一个免 key 音乐 API",
    },
)


def register_all() -> int:
    n = 0
    try:
        from . import capability as _cap  # noqa: PLC0415
        from . import endpoints as _ep    # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log.warning("[nokeyapi-cap] import registry failed: %s", e)
        return 0

    for c in NOKEYAPI_CAPABILITIES:
        try:
            _cap.register_capability(
                c["id"], title=c["title"], endpoint=c["endpoint"],
                method=c["method"], risk=c["risk"], auth=c["auth"],
                keywords=c["keywords"], confirm=c["confirm"],
            )
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("[nokeyapi-cap] register %s failed: %s", c["id"], e)

    for c in NOKEYAPI_CAPABILITIES:
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
我手头有一份 n0shake/Public-APIs 的免 key/试用/开源 API 资源库(56 分类 481 条,带 Open/Trial 三档标记),本地只读,响应快。

🔓 免 key API 搜索(关键词 + 分类):
  · "找个免 key 的音乐 API"  → nokeyapi.find query="music"
  · "免 key 的天气 API"  → nokeyapi.find query="weather"
  · "试用 API 推荐"  → nokeyapi.find query="trial"
  · "open source api"  → nokeyapi.find query="open source"

📚 列分类:
  · "免 key API 都分哪些类"  → nokeyapi.list_categories

🎯 查单个 API 详情:
  · "Spotify 需要 key 吗"  → nokeyapi.detail name="Spotify"
  · "DitchCarbon API"  → nokeyapi.detail name="DitchCarbon API"
  · "Open Web Analytics 是什么"  → nokeyapi.detail name="Open Web Analytics"

🎲 随机推荐:
  · "随机来一个免 key API"  → nokeyapi.random
  · "随机推荐一个免 key 音乐 API"  → nokeyapi.random category="Music"

需要我用 EXEC 标记触发 — 在回复末尾追加一行:
  [[EXEC: nokeyapi.find query="weather" limit="10"]]
  [[EXEC: nokeyapi.find category="Music"]]
  [[EXEC: nokeyapi.list_categories]]
  [[EXEC: nokeyapi.detail name="Spotify"]]
  [[EXEC: nokeyapi.random category="Music"]]
参数全部用引号引号。

⚠️ 用户问「免 key / 免注册 / 无需 key / 试用 / 开源」才调本能力;问「国内可访问的」按 api_cn.* 走;问「国际公共 API」按 api.* 走。
⚠️ 数据快照 2026-10-02,卡片里有 Open/Trial 标记 + 来自链接,用户可核验最新状态。"""


_n = register_all()
log.info("[nokeyapi-cap] auto-registered %d capability + endpoint", _n)