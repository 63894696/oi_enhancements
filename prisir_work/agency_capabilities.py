"""prisir_work/agency_capabilities.py — 264 agency-agents 角色查询能力(P3j Phase 1.6,2026-09-28)。

定位:把 extensions/agency-roles/data/{divisions,roles,meta}.json 三件套挂进 PrisirWork
     主对话能力体系(capability + endpoint + intent_summary),让 LLM 在主对话里能识别
     「找个 React 工程师角色 / 列下 engineering division 有什么 / 给我这个角色的完整人格
     prompt」等需求,自动输出 [[EXEC: agency.search ...]] → 读本地 JSON → 返角色卡片。

设计:
  · **3 capability 全 L0**(只读本地 JSON,不出图不发外,符合红线③)。
  · **endpoint handler 同步读 JSON** — 用 Path + json.load,无子进程无外网;fail-soft 降级。
  · **搜索策略**:query 命中 div / name / slug / tags / description 任一字段即算 hit,
    score = div(3) > name(2) > tag(1.5) > desc(1),返 top-N。
  · **intent_summary 简洁** — 不堆 personamarkdown,只说明 EXEC 怎么写。
  · **不挂 onSessionMessage** — 全部走 EXEC 标记协议,跟 poster / free / video 同链路。

用例:
    from prisir_work.agency_capabilities import register_all, intent_summary
    register_all()            # 副作用:往 capability._REGISTRY + endpoints._REGISTRY 写
    msgs = [{"role": "system", "content": intent_summary()}]
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("prisir_work.agency_capabilities")

__all__ = [
    "register_all",
    "intent_summary",
    "AGENCY_CAPABILITIES",
]


# 数据源(项目内 extensions/agency-roles/data/)
_DATA_ROOT = Path(__file__).resolve().parent.parent / "extensions" / "agency-roles" / "data"


# ---------------------------------------------------------------------------
# 3 capability 元数据 — 全部 L0(只查本地 JSON,不出图不发外)
# ---------------------------------------------------------------------------

AGENCY_CAPABILITIES = (
    {
        "id":          "agency.list_divisions",
        "title":       "列出 agency-agents 全部分组(18 个 division,264 角色)",
        "endpoint":    "/agency/list_divisions",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("agency", "divisions", "分组", "角色分类",
                         "agency divisions", "agent divisions"),
        "confirm":     "",
        "args_help":   "无参数",
        "example":     "agency-agents 都分哪些组?",
    },
    {
        "id":          "agency.search",
        "title":       "按关键词 / division / 标签搜索 agent 角色",
        "endpoint":    "/agency/search",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("agency", "角色", "agent", "角色搜索", "找角色",
                         "agency search", "agent role", "找 agent",
                         "招一个", "需要个", "什么角色适合"),
        "confirm":     "",
        "args_help":   "query(必填,关键词);division(可选,如 engineering);limit(默认 10)",
        "example":     "找个 React 工程师角色",
    },
    {
        "id":          "agency.detail",
        "title":       "查单个 agent 角色的完整 persona(可作 system prompt)",
        "endpoint":    "/agency/detail",
        "method":      "POST",
        "risk":        "L0",
        "auth":        True,
        "keywords":    ("agency", "角色详情", "角色 prompt", "agent persona",
                         "角色系统提示", "agent prompt"),
        "confirm":     "",
        "args_help":   "slug(必填,如 engineering-frontend-developer)",
        "example":     "给我 engineering-frontend-developer 的完整 persona",
    },
)


# ---------------------------------------------------------------------------
# 数据加载(模块级缓存,启动一次)
# ---------------------------------------------------------------------------

_CACHE: dict[str, Any] = {}


def _load_data(force: bool = False) -> dict[str, Any]:
    """从 extensions/agency-roles/data/ 加载三件套,失败返空。"""
    if _CACHE and not force:
        return _CACHE
    out: dict[str, Any] = {"divisions": [], "roles": [], "meta": {}}
    for key in ("divisions", "roles", "meta"):
        p = _DATA_ROOT / f"{key}.json"
        try:
            out[key] = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("[agency-cap] load %s.json failed: %s", key, e)
    # roles.json 是 {roles: [...]},扁平化
    if isinstance(out["roles"], dict) and "roles" in out["roles"]:
        out["roles"] = out["roles"]["roles"]
    _CACHE.update(out)
    return out


# ---------------------------------------------------------------------------
# 注册入口(副作用)
# ---------------------------------------------------------------------------

def register_all() -> int:
    """把 3 个 capability + endpoint 注册到主进程 registry。返回成功数。"""
    n = 0
    try:
        from . import capability as _cap  # noqa: PLC0415
        from . import endpoints as _ep    # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log.warning("[agency-cap] import registry failed: %s", e)
        return 0

    # 1) capability
    for c in AGENCY_CAPABILITIES:
        try:
            _cap.register_capability(
                c["id"], title=c["title"], endpoint=c["endpoint"],
                method=c["method"], risk=c["risk"], auth=c["auth"],
                keywords=c["keywords"], confirm=c["confirm"],
            )
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("[agency-cap] register %s failed: %s", c["id"], e)

    # 2) endpoint handler(只注册一次 — 重复会覆盖)
    for c in AGENCY_CAPABILITIES:
        ep_path = c["endpoint"]
        if ep_path in _ep._REGISTRY:
            continue
        cap_id = c["id"]
        _ep.register(ep_path, method=c["method"], risk=c["risk"], auth=c["auth"])(
            _make_handler(cap_id)
        )

    # 3) 触发一次数据预加载(快速失败可见)
    try:
        d = _load_data()
        log.info("[agency-cap] loaded %d roles / %d divisions",
                 len(d.get("roles") or []),
                 len(d.get("divisions", {}).get("divisions", [])))
    except Exception as e:  # noqa: BLE001
        log.warning("[agency-cap] preload failed: %s", e)

    return n


def _make_handler(cap_id: str):
    """构造 endpoint handler:读本地 JSON + fail-soft。"""
    method = cap_id.split(".", 1)[1] if "." in cap_id else cap_id

    def _handler(body: dict) -> tuple[dict, int]:
        body = body or {}
        data = _load_data()
        roles = data.get("roles") or []
        divisions_obj = data.get("divisions") or {}
        divisions = divisions_obj.get("divisions", []) if isinstance(divisions_obj, dict) else divisions_obj
        meta = data.get("meta") or {}

        if not roles:
            return ({"ok": False, "error": "agency_data_unavailable",
                     "hint": "extensions/agency-roles/data/*.json not loaded"},
                    200)

        if method == "list_divisions":
            # 返 divisions 摘要 + 各 division 的 n_roles
            divs = []
            for d in divisions:
                if not isinstance(d, dict):
                    continue
                divs.append({
                    "name":  d.get("name", ""),
                    "label": d.get("label", ""),
                    "icon":  d.get("icon", ""),
                    "color": d.get("color", ""),
                    "n_roles": d.get("n_roles", 0),
                })
            return ({"ok": True, "divisions": divs,
                     "total_divisions": len(divs),
                     "total_roles": len(roles),
                     "source": meta.get("source", ""),
                     "license": meta.get("license", "")}, 200)

        if method == "search":
            query = (body.get("query") or "").strip().lower()
            division = (body.get("division") or "").strip().lower()
            try:
                limit = max(1, min(50, int(body.get("limit") or 10)))
            except (TypeError, ValueError):
                limit = 10
            if not query and not division:
                return ({"ok": False,
                         "error": "missing_query_or_division",
                         "hint": "至少传 query 或 division 之一"}, 200)
            hits = []
            for r in roles:
                if not isinstance(r, dict):
                    continue
                if division and r.get("div", "").lower() != division:
                    continue
                score = 0.0
                hay = {
                    "div":   (r.get("div") or "").lower(),
                    "slug":  (r.get("slug") or "").lower(),
                    "name":  (r.get("name") or "").lower(),
                    "desc":  (r.get("description") or "").lower(),
                }
                if query:
                    if query in hay["name"]:
                        score += 2.0
                    if query in hay["div"]:
                        score += 3.0
                    if query in hay["slug"]:
                        score += 1.5
                    if query in hay["desc"]:
                        score += 1.0
                    for t in r.get("tags") or []:
                        if query in (t or "").lower():
                            score += 1.5
                            break
                    if score == 0:
                        continue
                else:
                    # 只按 division 过滤时给一个保底 score
                    score = 1.0
                hits.append((score, r))
            hits.sort(key=lambda x: (-x[0], x[1].get("slug", "")))
            top = hits[:limit]
            results = [{
                "div": r.get("div"),
                "slug": r.get("slug"),
                "name": r.get("name"),
                "emoji": r.get("emoji"),
                "color": r.get("color"),
                "description": r.get("description", "")[:160],
                "vibe": r.get("vibe", "")[:120],
                "tags": (r.get("tags") or [])[:5],
                "score": round(score, 2),
            } for score, r in top]
            return ({"ok": True, "results": results,
                     "total": len(hits), "returned": len(results),
                     "license": meta.get("license", "")}, 200)

        if method == "detail":
            slug = (body.get("slug") or "").strip().lower()
            if not slug:
                return ({"ok": False,
                         "error": "missing_slug",
                         "hint": "传 slug(从 agency.search 结果里取)"},
                        200)
            # 精确 slug 匹配优先,fallback 模糊
            match = None
            for r in roles:
                if (r.get("slug") or "").lower() == slug:
                    match = r
                    break
            if match is None:
                # 模糊:slug 包含 query
                fuzzy = [r for r in roles
                         if slug in (r.get("slug") or "").lower()]
                if len(fuzzy) == 1:
                    match = fuzzy[0]
                elif len(fuzzy) > 1:
                    return ({"ok": False, "error": "ambiguous_slug",
                             "candidates": [r.get("slug") for r in fuzzy[:5]]},
                            200)
            if match is None:
                return ({"ok": False,
                         "error": "role_not_found",
                         "hint": "先用 agency.search 查 slug"},
                        200)
            return ({"ok": True,
                     "div": match.get("div"),
                     "slug": match.get("slug"),
                     "name": match.get("name"),
                     "emoji": match.get("emoji"),
                     "color": match.get("color"),
                     "description": match.get("description"),
                     "vibe": match.get("vibe"),
                     "tags": match.get("tags") or [],
                     "persona_md": match.get("persona_md", ""),
                     "persona_tokens_est": match.get("persona_tokens_est"),
                     "persona_chars": match.get("persona_chars"),
                     "source_path": match.get("source_path"),
                     "license": meta.get("license", "")}, 200)

        return ({"ok": False, "error": f"unknown_method: {method}"}, 200)

    return _handler


# ---------------------------------------------------------------------------
# intent_summary — 喂给 LLM 的能力介绍 + EXEC 提示
# ---------------------------------------------------------------------------

def intent_summary() -> str:
    """返回给 build_messages 拼进 system:agency 角色库能力清单 + EXEC 写法示例。"""
    return """\
我本地装了一份 msitarzewski/agency-agents 角色库(264 个 agent 角色,18 个 division,MIT 只读),
适合「需要某种角色的人怎么思考 / 写什么 / 注意什么」这类需求。

🎭 agency 角色查询:
  · "agency-agents 都分哪些组?"  → agency.list_divisions
  · "找个 React 工程师角色"  → agency.search query="React" division="engineering"
  · "找个 GIS 数据分析师"  → agency.search query="GIS" division="gis"
  · "给我 engineering-frontend-developer 的完整 persona"  → agency.detail slug="engineering-frontend-developer"

需要我用 EXEC 标记触发 — 在回复末尾追加一行:
  [[EXEC: agency.list_divisions]]
  [[EXEC: agency.search query="React" division="engineering" limit="5"]]
  [[EXEC: agency.search query="GIS" limit="10"]]
  [[EXEC: agency.detail slug="engineering-frontend-developer"]]
参数全部用引号字符串。查 persona 后可作为 system prompt 喂给子 agent(它本身就是设计好的人设 prompt)。

⚠️ 264 个角色快照 2026-09-27;persona 是英文;中文场景用前先让模型本地化。
⚠️ MIT 许可,商用 OK,但 persona 内容仅作参考,真人专家输出仍优先。"""


# ---------------------------------------------------------------------------
# Module-level auto register — import 一次就把 3 capability + 3 endpoint 注入主进程。
# ---------------------------------------------------------------------------
_n = register_all()
log.info("[agency-cap] auto-registered %d capability + endpoint", _n)