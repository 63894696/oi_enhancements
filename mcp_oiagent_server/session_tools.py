"""session_tools.py — v0.24 Session Memory Injector

4 个 MCP tool 实现:
- session_load_context:按 cwd/namespace 自动加载相关记忆子集
- memory_namespace_set:注册 namespace(关联 cwd 白名单)
- memory_namespace_list:列出所有已注册 namespace
- memory_audit:审计 MEMORY.md + OI Memory,标记 dead link / 过载条目

设计原则(参照 .claude/rules):
- 返回 dict 不抛异常(MCP handler 包 try/except)
- 错误格式:{"ok": False, "error": "...", "stage": "..."}
- 不重写:复用 OIMemory + cognee_tools
- backward compatible:namespace 参数全 optional
- traceback[:500] 方便 debug
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import traceback
from pathlib import Path

logger = logging.getLogger("mcp_oiagent_server.session_tools")

# ===== 路径常量(参照 .claude/rules/common.md 真路径惯例)=====
OI_ENHANCEMENTS_ROOT = Path("C:/Users/Administrator/oi_enhancements")
MEMORY_DIR = Path("C:/Users/Administrator/.claude/projects/C--Users-Administrator/memory")
MEMORY_MD = MEMORY_DIR / "MEMORY.md"

# OI Memory DB 位置(从 oi_memory.py DEFAULT_DB_PATH 推断)
OI_MEMORY_DB = Path.home() / ".oi" / "memory.db"


def _err(stage: str, exc: Exception) -> str:
    """统一错误格式(参 .claude/rules/python.md 错误处理)"""
    return json.dumps({
        "ok": False,
        "error": str(exc),
        "stage": stage,
        "traceback": traceback.format_exc()[:500],
    }, ensure_ascii=False)


# ============================================================================
# 1. namespace 推断
# ============================================================================

def infer_namespace_from_cwd(cwd: str) -> str | None:
    """cwd 路径推断 namespace(v0.25.1 Phase B 之前的实现,保留作 fallback)。

    规则:
    - D:/AureonCloud/proton/cognee/ → "cognee"(取最末段)
    - D:/AureonCloud/proton/aureon-federation/ → "aureon-federation"
    - D:/AureonCloud/(只 2 段,根 + 1 子) → None(置信度太低)
    - 其他未识别路径 → None

    最小深度阈值:3 段(如 D:/X/Y 才推断)。2 段路径(D:/X)太宽泛,不推断。
    """
    if not cwd:
        return None
    try:
        p = Path(cwd.replace("\\", "/"))
    except Exception:
        return None
    if not p.parts or p.parts[-1] in ("/", ""):
        return None
    # 至少 3 段深度才推断(避免 D:/AureonCloud 这种根目录被误判)
    if len(p.parts) < 3:
        return None
    name = p.name
    if not name or name in (".", ".."):
        return None
    return name


def resolve_namespace(cwd: str, explicit_namespace: str | None = None) -> str | None:
    """v0.25.1 Phase B:namespace 解析(优先 registry 映射,fallback 末段推断)。

    解析顺序:
    1. explicit_namespace 参数(最高优先级)
    2. OI Memory L1 namespace registry 的 cwd_whitelist 映射
       - 遍历已注册 namespace,看 cwd 是否在任一 cwd_whitelist 中
       - 命中返回该 namespace
    3. infer_namespace_from_cwd(cwd)(末段推断,向后兼容)
    4. None(全局 recall)

    Args:
        cwd: 当前工作目录
        explicit_namespace: 手动覆盖(优先级最高)

    Returns:
        namespace 字符串,或 None(需 fallback 全量)
    """
    # 1. explicit 优先
    if explicit_namespace:
        return explicit_namespace
    if not cwd:
        return None

    # 2. 查 OI Memory L1 namespace registry
    #    P1 per-agent:registry 映射属共享基础设施,不过滤 visible_to
    #    注:registry 路由不受 per-agent 隔离影响,始终不过滤(2026-08-03 用户拍板);
    #    per-agent 隔离只作用于 list/audit 的可见性,不作用于路由解析。
    try:
        sys.path.insert(0, str(OI_ENHANCEMENTS_ROOT))
        from memory.oi_memory import OIMemory
        mem = OIMemory()
        # 找所有 L1 层的 namespace:xxx 记忆
        registry = mem.list_by_layer("L1", limit=200, visible_to=None)
        cwd_norm = cwd.replace("\\", "/").rstrip("/")
        # 按 cwd_whitelist 长度 DESC 排序(更具体的优先)
        candidates = []
        for m in registry:
            if not m.title.startswith("namespace:"):
                continue
            try:
                d = json.loads(m.content)
            except Exception:
                continue
            for allowed_cwd in d.get("cwd_whitelist", []):
                allowed_norm = allowed_cwd.replace("\\", "/").rstrip("/")
                if cwd_norm == allowed_norm or cwd_norm.startswith(allowed_norm + "/"):
                    candidates.append((len(allowed_norm), d.get("namespace")))
                    break
        # 选最具体的(最长 prefix)
        if candidates:
            candidates.sort(key=lambda x: -x[0])
            return candidates[0][1]
    except Exception as exc:
        logger.warning(f"查 namespace registry 失败: {exc}")

    # 3. fallback 末段推断
    return infer_namespace_from_cwd(cwd)


def _resolved_via_registry(cwd: str, namespace: str | None) -> bool:
    """检查 namespace 是否通过 registry 映射解析(非 explicit 也非末段推断)"""
    if not cwd or not namespace:
        return False
    try:
        sys.path.insert(0, str(OI_ENHANCEMENTS_ROOT))
        from memory.oi_memory import OIMemory
        mem = OIMemory()
        # P1 per-agent:同 resolve_namespace,registry 映射不过滤 visible_to
        # 注:registry 路由不受 per-agent 隔离影响,始终不过滤(2026-08-03 用户拍板);
        # per-agent 隔离只作用于 list/audit 的可见性,不作用于路由解析。
        registry = mem.list_by_layer("L1", limit=200, visible_to=None)
        cwd_norm = cwd.replace("\\", "/").rstrip("/")
        for m in registry:
            if not m.title.startswith("namespace:"):
                continue
            try:
                d = json.loads(m.content)
            except Exception:
                continue
            if d.get("namespace") != namespace:
                continue
            for allowed_cwd in d.get("cwd_whitelist", []):
                allowed_norm = allowed_cwd.replace("\\", "/").rstrip("/")
                if cwd_norm == allowed_norm or cwd_norm.startswith(allowed_norm + "/"):
                    return True
    except Exception:
        pass
    return False


def _resolution_method(cwd: str, namespace: str | None, explicit_namespace: str | None) -> str:
    """记录 namespace 解析方式(用于 audit)"""
    if explicit_namespace:
        return "explicit"
    if not namespace:
        return "none"
    if _resolved_via_registry(cwd, namespace):
        return "registry"
    return "cwd_suffix"


# ============================================================================
# 2. memory_audit_impl(Phase 0 主用)
# ============================================================================

def memory_audit_impl(
    check_path_exists: bool = True,
    dry_run: bool = True,
    sample_size: int = 0,
    agent_id: str = "",  # P1 per-agent:可选调用方自报身份,空=不过滤(向后兼容)
) -> str:
    """审计 MEMORY.md 和 OI Memory,标记死链、过载条目。

    Args:
        check_path_exists: 是否实际验证文件路径(MEMORY.md 链接的 .md 文件)
        dry_run: True=不删不改,只产出报告(永远 True 在 v0.24)
        sample_size: 0=全量,>0=随机抽样(快速预览)
        agent_id: 可选,调用方自报 agent 身份;非空时 OI Memory 扫描只含
                  全局共享 + 该 agent 私有条目(防君子不防小人)

    Returns:
        JSON 字符串,字段:
        - ok: True
        - dry_run: True
        - findings:
            - dead_links: [{file, abs, line}, ...]
            - over_accessed: [{id, layer, title, access_count}, ...]
            - total_memory_md_links: int
        - summary: {dead_link_count, over_accessed_count, sampled}
    """
    try:
        findings: dict = {
            "dead_links": [],
            "over_accessed": [],
            "total_memory_md_links": 0,
        }

        # 1. 扫 MEMORY.md 链接(格式:`[title](filename.md)` 或 `[title](path/to/file.md)`)
        if MEMORY_MD.exists() and check_path_exists:
            text = MEMORY_MD.read_text(encoding="utf-8")
            # 匹配 [xxx](yyy.md) 或 [xxx](yyy.md#anchor)
            link_pattern = re.compile(r"\[([^\]]+)\]\(([^\)]+\.md)(?:#[^\)]*)?\)")
            for line_no, line in enumerate(text.splitlines(), start=1):
                for m in link_pattern.finditer(line):
                    link = m.group(2)
                    # MEMORY.md 是相对路径(同目录)→ 拼绝对路径
                    abs_path = (MEMORY_DIR / link).resolve()
                    findings["total_memory_md_links"] += 1
                    if not abs_path.exists():
                        findings["dead_links"].append({
                            "file": link,
                            "abs": str(abs_path),
                            "line": line_no,
                            "title": m.group(1).strip(),
                        })
                    if sample_size and findings["total_memory_md_links"] >= sample_size:
                        break
                if sample_size and findings["total_memory_md_links"] >= sample_size:
                    break

        # 2. 扫 OI Memory 中 access_count > 50 的 L0/L1 条目(防霸榜)
        try:
            sys.path.insert(0, str(OI_ENHANCEMENTS_ROOT))
            from memory.oi_memory import OIMemory  # type: ignore
            mem = OIMemory()
            for layer in ("L0", "L1"):
                # P1 per-agent:agent_id 非空 → 只看全局 + 该 agent 私有;空 → None 不过滤
                vis = agent_id or None
                for m in mem.list_by_layer(layer, limit=200, visible_to=vis):
                    if m.access_count > 50:
                        findings["over_accessed"].append({
                            "id": m.id,
                            "layer": m.layer,
                            "title": m.title,
                            "access_count": m.access_count,
                        })
            # 顺便统计 layer 分布
            stats = mem.stats()
            findings["oi_memory_stats"] = stats
        except Exception as e:
            findings["oi_memory_error"] = f"扫 OI Memory 失败:{e}"

        return json.dumps({
            "ok": True,
            "dry_run": dry_run,
            "findings": findings,
            "summary": {
                "dead_link_count": len(findings["dead_links"]),
                "over_accessed_count": len(findings["over_accessed"]),
                "total_memory_md_links": findings["total_memory_md_links"],
                "dead_link_pct": (
                    round(len(findings["dead_links"]) / max(findings["total_memory_md_links"], 1) * 100, 2)
                ),
                "sampled": bool(sample_size),
            },
        }, ensure_ascii=False)

    except Exception as exc:
        return _err("memory_audit", exc)


# ============================================================================
# 3. memory_namespace_set / list_impl
# ============================================================================

def memory_namespace_set_impl(
    namespace: str,
    cwd: str = "",
    cwd_whitelist: list[str] | None = None,
    max_context_chars: int = 1500,
    agent_id: str = "",  # P1 per-agent:可选调用方自报身份,空=全局共享(向后兼容)
) -> str:
    """注册一个 namespace。

    用 OIMemory L1 层持久化(避免新建独立存储)。

    Args:
        agent_id: 可选,调用方自报 agent 身份;非空时该 namespace 条目
                  归属该 agent(该 agent + 所有不传 visible_to 的调用方可见;
                  仅对传了其他 visible_to 的调用方隐藏——自报身份,防君子不防小人)

    Returns:
        {"ok": True, "namespace": "xxx", "id": <oi_memory_id>}
    """
    try:
        if not namespace:
            return json.dumps({"ok": False, "error": "namespace 不能为空", "stage": "validate"})
        sys.path.insert(0, str(OI_ENHANCEMENTS_ROOT))
        from memory.oi_memory import OIMemory  # type: ignore
        mem = OIMemory()
        payload = {
            "namespace": namespace,
            "cwd_whitelist": cwd_whitelist or ([cwd] if cwd else []),
            "max_context_chars": max_context_chars,
            "created_at": time.time(),
        }
        mid = mem.store(
            layer="L1",
            title=f"namespace:{namespace}",
            content=json.dumps(payload, ensure_ascii=False),
            tags=["namespace", "v0.24"],
            owner_agent=agent_id,  # P1 per-agent:空字符串=全局共享
        )
        return json.dumps({
            "ok": True,
            "namespace": namespace,
            "id": mid,
            "stored_in": "OI Memory L1",
        }, ensure_ascii=False)
    except Exception as exc:
        return _err("namespace_set", exc)


def memory_namespace_list_impl(include_stats: bool = False, agent_id: str = "") -> str:
    """列出所有已注册 namespace。

    Args:
        agent_id: 可选,调用方自报 agent 身份;非空时只列全局共享 + 该 agent 私有的
                  namespace 条目(防君子不防小人)

    Returns:
        {"ok": True, "namespaces": [{name, cwd_whitelist, max_context_chars, created_at}], "total": N}
    """
    try:
        sys.path.insert(0, str(OI_ENHANCEMENTS_ROOT))
        from memory.oi_memory import OIMemory  # type: ignore
        mem = OIMemory()
        items = mem.list_by_layer("L1", limit=500, visible_to=agent_id or None)
        namespaces = []
        for it in items:
            if not it.title.startswith("namespace:"):
                continue
            try:
                d = json.loads(it.content)
            except Exception:
                continue
            namespaces.append({
                "name": d.get("namespace"),
                "cwd_whitelist": d.get("cwd_whitelist", []),
                "max_context_chars": d.get("max_context_chars", 1500),
                "created_at": d.get("created_at"),
                "oi_memory_id": it.id,
            })
        result = {
            "ok": True,
            "namespaces": namespaces,
            "total": len(namespaces),
        }
        if include_stats:
            result["oi_memory_layer_dist"] = mem.stats().get("by_layer", {})
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return _err("namespace_list", exc)


# ============================================================================
# 4. session_load_context_impl
# ============================================================================

def session_load_context_impl(
    session_id: str,
    query: str,
    cwd: str = "",
    top_k: int = 5,
    explicit_namespace: str | None = None,
    max_chars: int = 1500,
) -> str:
    """按 session 上下文自动加载相关记忆子集。

    Args:
        session_id: 会话 ID(必填)
        query: 召回 query
        cwd: 当前工作目录(用于 namespace 推断)
        top_k: 召回条数
        explicit_namespace: 手动覆盖 namespace(优先级最高)
        max_chars: 截断到 N 字符(防 recall 结果淹没 system prompt)

    Returns:
        {
            "ok": True,
            "namespace": "xxx" | None,
            "session_id": "xxx",
            "hits": [...],
            "count": N,
            "truncated": bool,
            "total_chars": N,
            "fallback_used": bool,  # True 表示无 namespace,走了全量
        }
    """
    try:
        # Step 1: namespace 解析(v0.25.1 Phase B:registry 优先,末段推断 fallback)
        namespace = resolve_namespace(cwd, explicit_namespace)
        fallback_used = namespace is None
        # 记录解析方式(用于审计)
        resolution_method = _resolution_method(cwd, namespace, explicit_namespace)

        # Step 2: 调 cognee_recall(本地 import 避免循环)
        sys.path.insert(0, str(OI_ENHANCEMENTS_ROOT / "mcp_oiagent_server"))
        from cognee_tools import cognee_recall_impl  # type: ignore

        if fallback_used:
            # 走默认全量 recall(向后兼容)
            recall_json = cognee_recall_impl(query=query, top_k=top_k)
        else:
            # 走 namespace 隔离
            dataset_name = f"ns_{namespace.replace('-', '_')}"
            recall_json = cognee_recall_impl(query=query, session_id=None, top_k=top_k, dataset_name=dataset_name)

        recall_data = json.loads(recall_json)
        hits = recall_data.get("hits", recall_data.get("results", []))

        # Step 3: 截断到 max_chars
        total_chars = 0
        kept = []
        for h in hits:
            content = h.get("content", "") or h.get("text", "")
            h_chars = len(content)
            if total_chars + h_chars > max_chars:
                break
            kept.append(h)
            total_chars += h_chars

        return json.dumps({
            "ok": True,
            "namespace": namespace,
            "resolution_method": resolution_method,  # v0.25.1:explicit/registry/cwd_suffix/none
            "session_id": session_id,
            "hits": kept,
            "count": len(kept),
            "truncated": len(kept) < len(hits),
            "total_chars": total_chars,
            "fallback_used": fallback_used,
            "original_count": len(hits),
        }, ensure_ascii=False)
    except Exception as exc:
        return _err("session_load_context", exc)


# ── Dynamic Registry Exports (v0.38) ────────────────────────────

TOOL_DEFS = [
    {
        "name": "session_load_context",
        "description": (
            "按 cwd 自动加载记忆 — 四层召回(KG + AnyTXT + Everything + MEMORY.md)"
            " + 中文关键词映射,覆盖 100% 项目。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "会话 ID"},
                "query": {"type": "string", "description": "搜索查询"},
                "cwd": {"type": "string", "description": "工作目录(namespace 自动推断)"},
                "namespace": {"type": "string", "description": "命名空间(可选,覆盖自动推断)"},
                "top_k": {"type": "integer", "default": 5},
                "max_chars": {"type": "integer", "default": 1500},
            },
            "required": ["session_id", "query"],
        },
    },
    {
        "name": "memory_namespace_set",
        "description": "注册 namespace — 将 cwd 映射到命名空间,用于上下文注入",
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "cwd": {"type": "string"},
                "max_chars": {"type": "integer", "default": 1500},
                "agent_id": {
                    "type": "string",
                    "default": "",
                    "description": "可选,调用方自报 agent 身份(防君子不防小人);"
                                   "非空时该 namespace 归属此 agent,空=全局共享",
                },
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "memory_namespace_list",
        "description": "列出所有已注册的 namespace 及其统计",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_stats": {"type": "boolean", "default": False},
                "agent_id": {
                    "type": "string",
                    "default": "",
                    "description": "可选,调用方自报 agent 身份;非空时只列全局共享 + 该 agent 私有条目",
                },
            },
            "required": [],
        },
    },
    {
        "name": "memory_audit",
        "description": "审计 MEMORY.md 链接 + OI Memory 霸榜条目",
        "inputSchema": {
            "type": "object",
            "properties": {
                "check_path_exists": {"type": "boolean", "default": True},
                "dry_run": {"type": "boolean", "default": True},
                "sample_size": {"type": "integer", "default": 0},
                "agent_id": {
                    "type": "string",
                    "default": "",
                    "description": "可选,调用方自报 agent 身份;非空时 OI Memory 扫描只含全局共享 + 该 agent 私有条目",
                },
            },
            "required": [],
        },
    },
]

HANDLERS = {
    "session_load_context": session_load_context_impl,
    "memory_namespace_set": memory_namespace_set_impl,
    "memory_namespace_list": memory_namespace_list_impl,
    "memory_audit": memory_audit_impl,
}


# ============================================================================
# Standalone CLI(测试用)
# ============================================================================

def _cli():
    import argparse
    p = argparse.ArgumentParser(description="v0.24 session_tools standalone CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_audit = sub.add_parser("audit", help="审计 MEMORY.md + OI Memory")
    p_audit.add_argument("--no-check-path", action="store_true")
    p_audit.add_argument("--sample", type=int, default=0)

    p_set = sub.add_parser("namespace-set", help="注册 namespace")
    p_set.add_argument("namespace")
    p_set.add_argument("--cwd", default="")
    p_set.add_argument("--max-chars", type=int, default=1500)

    sub.add_parser("namespace-list", help="列出所有 namespace")

    p_load = sub.add_parser("load-context", help="按 cwd 自动加载记忆")
    p_load.add_argument("session_id")
    p_load.add_argument("query")
    p_load.add_argument("--cwd", default="")
    p_load.add_argument("--namespace", default=None)
    p_load.add_argument("--top-k", type=int, default=5)
    p_load.add_argument("--max-chars", type=int, default=1500)

    args = p.parse_args()
    if args.cmd == "audit":
        out = memory_audit_impl(
            check_path_exists=not args.no_check_path,
            dry_run=True,
            sample_size=args.sample,
        )
    elif args.cmd == "namespace-set":
        out = memory_namespace_set_impl(
            namespace=args.namespace,
            cwd=args.cwd,
            max_context_chars=args.max_chars,
        )
    elif args.cmd == "namespace-list":
        out = memory_namespace_list_impl(include_stats=True)
    elif args.cmd == "load-context":
        out = session_load_context_impl(
            session_id=args.session_id,
            query=args.query,
            cwd=args.cwd,
            explicit_namespace=args.namespace,
            top_k=args.top_k,
            max_chars=args.max_chars,
        )
    print(out)


if __name__ == "__main__":
    _cli()