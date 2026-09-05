"""能力门面层(F1):把端点白名单升级为「能力注册表」,对外统一 search/execute。

定位(见 prisirwork-foundation-integration-design §3):
- 浏览器/agent 不需知道背后是 wallet 还是 prisiragent 团队——一律 search 发现、execute 调用。
- 权限门槛不新造:能力标注 risk(L0-L3)+ auth,实际执行仍走 endpoints 白名单 + token,
  本层只做「能力抽象 + 发现 + 路由」,不绕过红线③。

能力三层抽象(借 openwork connector/skill/plugin):
- 本文件的「能力」= skill 粒度(可被 agent 发现/调用的最小单元)。
- 每个能力绑定一个 endpoint path(底层 connector 实现);打包成 plugin 是 F6 的事。

能力 entry:
  id          : 点分能力名("wallet.status" / "prisiragent.dispatch" …)
  title       : 一句话人话描述(agent 发现时展示)
  risk        : "L0" 只读免确认 / "L1" 内嵌卡 / "L2" 全回显 / "L3" 安全对话框(口令/A2H)
  auth        : 是否需 X-OI-Token(与端点一致;L3 另由授权门把守,见设计 §4)
  endpoint    : 实际执行的端点 path(必须在 endpoints 注册表内)
  method      : 该端点的 HTTP 方法("GET"/"POST")
  keywords    : 发现检索词(中文/英文/别名),search 据此匹配
  confirm     : 授权门提示语(risk>=L1 时给扩展/shell 渲染确认卡用;L0 为 "")
"""
from __future__ import annotations

from typing import Any

# 能力注册表:id → entry。execute 时按 endpoint 路由回 endpoints 白名单。
_REGISTRY: dict[str, dict[str, Any]] = {}


def register_capability(cid: str, *, title: str, endpoint: str, method: str = "GET",
                        risk: str = "L0", auth: bool = True,
                        keywords: tuple[str, ...] = (), confirm: str = "") -> None:
    """登记一个能力。endpoint 必须是 endpoints 注册表里的白名单端点(否则 execute 仍 404)。"""
    _REGISTRY[cid] = {
        "id": cid, "title": title, "endpoint": endpoint, "method": method.upper(),
        "risk": risk, "auth": auth, "keywords": tuple(keywords), "confirm": confirm,
    }


def get(cid: str) -> dict[str, Any] | None:
    return _REGISTRY.get(cid)


def list_capabilities() -> list[dict[str, Any]]:
    """全量能力目录(不含实现;供 search 与调试)。按 id 排序保证稳定输出。"""
    return [
        {
            "id": e["id"], "title": e["title"], "risk": e["risk"], "auth": e["auth"],
            "endpoint": e["endpoint"], "method": e["method"],
            "keywords": list(e["keywords"]), "confirm": e["confirm"],
        }
        for _, e in sorted(_REGISTRY.items())
    ]


def search(query: str) -> list[dict[str, Any]]:
    """能力发现:按 id/title/keywords 子串匹配(大小写不敏感)。空 query 返回全部。

    返回精简条目(发现阶段只需 id/title/risk/confirm,不暴露 endpoint 细节之外的信息)。
    """
    q = (query or "").strip().lower()
    out = []
    for e in sorted(_REGISTRY.values(), key=lambda x: x["id"]):
        if not q:
            hit = True
        else:
            hay = " ".join([e["id"], e["title"], *e["keywords"]]).lower()
            hit = q in hay
        if hit:
            out.append({
                "id": e["id"], "title": e["title"], "risk": e["risk"],
                "auth": e["auth"], "confirm": e["confirm"],
            })
    return out
