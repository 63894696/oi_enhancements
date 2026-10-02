"""prisir_work/selfhost_capabilities.py — 自部署软件资源检索能力(P3j Phase C,2026-10-02)。

定位:把 ext-awesome-selfhosted-promo 四个 registerCommand 挂进 PrisirWork 主对话能力体系,
     让 LLM 识别「自建 X / 自部署 Y / 开源替代 Z」类需求,自动 emit
     [[EXEC: selfhost.find ...]] → 调 Node 扩展子进程拿资源卡片 → ui.inject.card 注入会话。

设计与已有能力平行,但:
  - 命令前缀 selfhost.*(避免与 free.* / api.* / selfhost.* 冲突)
  - 关键词侧重「自部署 / 自建 / 开源替代 / Nextcloud / Bitwarden」(区别于 free.* 的「免费 SaaS」)
  - 数据来源 awesome-selfhosted/awesome-selfhosted(95 cat / 1260 svc,带 licenses/languages/warning)

用例:
    from prisir_work.selfhost_capabilities import register_all, intent_summary
"""
from __future__ import annotations

import logging

log = logging.getLogger("prisir_work.selfhost_capabilities")

__all__ = [
    "register_all",
    "intent_summary",
    "EXT_ID",
    "SELFHOST_CAPABILITIES",
]


# 扩展 ID
EXT_ID = "awesome-selfhosted-promo"


# ---------------------------------------------------------------------------
# 4 capability 元数据 — 全部 L0
# ---------------------------------------------------------------------------

SELFHOST_CAPABILITIES = (
    {
        "id":          "selfhost.find",
        "title":       "自部署软件搜索(关键词 + 分类 + 证书 + 语言)",
        "endpoint":    "/selfhost/find",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("自部署", "自建", "self-host", "self hosted",
                         "开源替代", "私有部署", "自托管",
                         "替代 SaaS", "nextcloud", "bitwarden", "joplin",
                         "alternative", "开源自部署", "本地部署"),
        "confirm":     "",
        "args_help":   "query(可选);license(可选,如 'MIT');language(可选,如 'Docker');limit(默认 10)",
        "example":     "找个自部署的网盘",
    },
    {
        "id":          "selfhost.list_categories",
        "title":       "列出自部署软件全部分类",
        "endpoint":    "/selfhost/list_categories",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("自部署分类", "自部署软件分类", "自托管分类",
                         "开源自部署分类", "self-host categories"),
        "confirm":     "",
        "args_help":   "无参数",
        "example":     "自部署软件都分哪些类?",
    },
    {
        "id":          "selfhost.detail",
        "title":       "查单个自部署软件详情",
        "endpoint":    "/selfhost/detail",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("自部署详情", "自部署软件详情", "自托管详情",
                         "开源自部署详情"),
        "confirm":     "",
        "args_help":   "name(必填,软件名,如 'Nextcloud' / 'Bitwarden')",
        "example":     "Nextcloud 是用什么语言写的?",
    },
    {
        "id":          "selfhost.random",
        "title":       "自部署软件随机推荐",
        "endpoint":    "/selfhost/random",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("随机自部署", "随机来一个自部署", "推荐个自部署"),
        "confirm":     "",
        "args_help":   "category(可选)",
        "example":     "随机推荐一个自部署网盘",
    },
)


def register_all() -> int:
    n = 0
    try:
        from . import capability as _cap  # noqa: PLC0415
        from . import endpoints as _ep    # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log.warning("[selfhost-cap] import registry failed: %s", e)
        return 0

    for c in SELFHOST_CAPABILITIES:
        try:
            _cap.register_capability(
                c["id"], title=c["title"], endpoint=c["endpoint"],
                method=c["method"], risk=c["risk"], auth=c["auth"],
                keywords=c["keywords"], confirm=c["confirm"],
            )
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("[selfhost-cap] register %s failed: %s", c["id"], e)

    for c in SELFHOST_CAPABILITIES:
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
我手头有一份 awesome-selfhosted/awesome-selfhosted 的自部署软件资源库(95 分类 1260 条,带 License/Language/不维护标记),本地只读。

🏠 自部署软件搜索(关键词 + 分类 + 证书 + 语言):
  · "找个自部署的网盘"  → selfhost.find query="网盘 file sharing"
  · "自建密码管理器"  → selfhost.find query="password manager"
  · "MIT 证书的笔记工具"  → selfhost.find license="MIT"
  · "Docker 部署的 Web 应用"  → selfhost.find language="Docker"
  · "Nextcloud 替代"  → selfhost.find query="nextcloud"

📚 列分类:
  · "自部署软件都分哪些类"  → selfhost.list_categories

🎯 查单个软件详情:
  · "Nextcloud 是用什么语言写的"  → selfhost.detail name="Nextcloud"
  · "Matomo 怎么部署"  → selfhost.detail name="Matomo"
  · "Postiz 还维护吗"  → selfhost.detail name="Postiz"

🎲 随机推荐:
  · "随机来一个自部署软件"  → selfhost.random
  · "随机推荐一个分析工具"  → selfhost.random category="Analytics"

需要我用 EXEC 标记触发 — 在回复末尾追加一行:
  [[EXEC: selfhost.find query="nextcloud" limit="5"]]
  [[EXEC: selfhost.find license="MIT"]]
  [[EXEC: selfhost.find language="Docker"]]
  [[EXEC: selfhost.list_categories]]
  [[EXEC: selfhost.detail name="Nextcloud"]]
  [[EXEC: selfhost.random category="Analytics"]]
参数全部用引号字符串。

⚠️ 用户问「自部署 / 自建 / 自托管 / 开源替代 SaaS」才调本能力;问「免费 SaaS」按 free.* 走。
⚠️ 数据快照 2026-10-02,卡片里有 source_code_url 链接让用户核验最新状态;`⚠` 标记表示项目不维护。"""


_n = register_all()
log.info("[selfhost-cap] auto-registered %d capability + endpoint", _n)