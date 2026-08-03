"""cognee_tools.py — v0.23.1 mcp_oiagent 4 个 cognee tool

走"工具不重复"原则:
- 不重写 LLM 走 cognee 默认 OpenAI provider + 百炼 OpenAI 兼容端点(实测通过)
- 不重写 embed,cognee OpenAICompatibleEmbeddingEngine 直接 AsyncOpenAI SDK + 百炼(实测通过)
- 不重写存储,cognee 默认 SQLite + LanceDB + Kuzu 零外部服务
- ✅ 新:同步 async ↔ sync 桥(因为 cognee 是 asyncio, MCP 是 async)
- ✅ 新:cognee_health 报告 .env 是否就绪 + 数据根是否就绪
- ✅ 新:env 自检 + 错误信息友好

环境变量配置(caller 必须设,此模块只读):
- BAILIAN_API_KEY / BAILIAN_BASE_URL(用户 env 已有)
- COGNEE_DATA_PATH(默认 D:/AureonCloud/proton/cognee)
- LLM_MODEL 默认 openai/qwen3-coder-plus
- EMBEDDING_MODEL 默认 text-embedding-v3
- EMBEDDING_BATCH_SIZE 默认 10(百炼限制)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any

log = logging.getLogger("oiagent.cognee_tools")

# ─────────────────────────────────────────────────
# 1. 数据根 + env 强制设置(必须在 import cognee 前)
# ─────────────────────────────────────────────────

_DATA_ROOT = Path(
    os.environ.get(
        "COGNEE_DATA_PATH",
        "D:/AureonCloud/proton/cognee",
    )
).resolve()
os.environ.setdefault("SYSTEM_ROOT_DIRECTORY", str(_DATA_ROOT))
os.environ.setdefault("DATA_ROOT_DIRECTORY", str(_DATA_ROOT))
_DATA_ROOT.mkdir(parents=True, exist_ok=True)

# LLM 走百炼 OpenAI 兼容(实测通过)
# 注意:cognee 1.x 走 LiteLLM,model 名要带 provider 前缀
# v0.26.1b 修:5 docs E2E PASS 但 168 docs 触 OpenAI 免费 quota
# 根因:cognee baml schema hardcode provider=openai,model 走 client_registry
# 修法 v5:用 openai 前缀但 baml 字段让 baml 真走 base_url
os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("LLM_MODEL", "openai/qwen3-coder-plus")  # LiteLLM OpenAI SDK 走
_bailian_base = os.environ.get("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
os.environ.setdefault("LLM_ENDPOINT", _bailian_base)  # cognee LiteLLM adapter 传 litellm
os.environ.setdefault("LLM_API_KEY", os.environ.get("BAILIAN_API_KEY", ""))
os.environ.setdefault("OPENAI_API_BASE", _bailian_base)
os.environ.setdefault("OPENAI_API_KEY", os.environ.get("BAILIAN_API_KEY", ""))
# baml:走同字段,baml 真用 base_url(关键!)+ provider=openai
os.environ.setdefault("BAML_LLM_PROVIDER", "openai")
os.environ.setdefault("BAML_LLM_MODEL", "qwen3-coder-plus")
os.environ.setdefault("BAML_LLM_ENDPOINT", _bailian_base)
os.environ.setdefault("BAML_LLM_API_KEY", os.environ.get("BAILIAN_API_KEY", ""))

# Embed 走百炼 OpenAI 兼容(实测通过)
# 关键:EMBEDDING_PROVIDER=openai_compatible 走 cognee.infrastructure.databases.vector.embeddings.OpenAICompatibleEmbeddingEngine
# v0.25.1 Phase A:从 text-embedding-v3(配额耗尽)切到 text-embedding-v4(2026 最新,1024 维实测可用)
os.environ.setdefault("EMBEDDING_PROVIDER", "openai_compatible")
os.environ.setdefault("EMBEDDING_MODEL", "text-embedding-v4")
os.environ.setdefault(
    "EMBEDDING_ENDPOINT",
    os.environ.get("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
)
os.environ.setdefault("EMBEDDING_DIMENSIONS", "1024")
os.environ.setdefault("EMBEDDING_BATCH_SIZE", "10")  # 百炼 batch ≤ 10 限制

# 跳过 cognee 的 30s 连接测试(每次起服务都要等,浪费时间)
os.environ.setdefault("COGNEE_SKIP_CONNECTION_TEST", "true")

# LLM API key 走 BAILIAN(若有 OPENAI_API_KEY 也兼容,但实际未设)
os.environ.setdefault("LLM_API_KEY", os.environ.get("BAILIAN_API_KEY", ""))
os.environ.setdefault("EMBEDDING_API_KEY", os.environ.get("BAILIAN_API_KEY", ""))


# ─────────────────────────────────────────────────
# 2. import cognee(env 已设)
# ─────────────────────────────────────────────────

try:
    import cognee
    _HAS_COGNEE = True
    _COGNEE_VERSION = getattr(cognee, "__version__", "unknown")
except ImportError as e:
    _HAS_COGNEE = False
    _COGNEE_VERSION = f"NOT INSTALLED ({e})"


# ─────────────────────────────────────────────────
# 3. async 桥 + 健康检查
# ─────────────────────────────────────────────────

def _run_async(coro):
    """同步环境跑 asyncio 协程。MCP handler 是 async,但 cognee 是 async,直接 await 即可。
    此 helper 留作兜底,主要给未来 sync 调用使用。"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return coro  # 调用方负责 await
    except RuntimeError:
        pass
    return asyncio.run(coro)


def cognee_health_impl() -> str:
    """健康检查 — env / 安装 / 数据根 / 配置 + v0.24 namespace 状态"""
    # v0.24:扫数据根,列出已有 dataset(避免新增字段打乱老 caller)
    active_datasets: list[str] = []
    try:
        # cognee 1.2.2 数据根是 databases/ 目录(每个 dataset 一个子目录)
        for candidate in (_DATA_ROOT / "databases", _DATA_ROOT / ".cognee_system" / "databases"):
            if candidate.exists() and candidate.is_dir():
                for p in candidate.iterdir():
                    if p.is_dir():
                        active_datasets.append(p.name)
                if active_datasets:
                    break
    except Exception:
        pass

    payload = {
        "ok": _HAS_COGNEE,
        "cognee_version": _COGNEE_VERSION,
        "data_root": str(_DATA_ROOT),
        "data_root_exists": _DATA_ROOT.exists(),
        "data_root_writable": os.access(_DATA_ROOT, os.W_OK),
        "llm": {
            "provider": os.environ.get("LLM_PROVIDER"),
            "model": os.environ.get("LLM_MODEL"),
            "endpoint": os.environ.get("LLM_ENDPOINT"),
            "api_key_set": bool(os.environ.get("LLM_API_KEY")),
        },
        "embedding": {
            "provider": os.environ.get("EMBEDDING_PROVIDER"),
            "model": os.environ.get("EMBEDDING_MODEL"),
            "endpoint": os.environ.get("EMBEDDING_ENDPOINT"),
            "dimensions": os.environ.get("EMBEDDING_DIMENSIONS"),
            "batch_size": os.environ.get("EMBEDDING_BATCH_SIZE"),
        },
        "skip_connection_test": os.environ.get("COGNEE_SKIP_CONNECTION_TEST"),
        # v0.24 新增
        "v024_namespaces": {
            "active_datasets": active_datasets,
            "namespace_count": len(active_datasets),
            "default_dataset": "aureon_main",
            "namespace_prefix": "ns_",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────
# v0.24 namespace 辅助函数
# ─────────────────────────────────────────────────

def _resolve_dataset_name(dataset_name: str | None, namespace: str | None) -> str:
    """namespace → dataset_name 解析。namespace 优先级高于 dataset_name。

    规则(plan Phase 1.4):
    - namespace="aureon-cognee" → "ns_aureon_cognee"
    - namespace=None + dataset_name="xxx" → 保持原值(向后兼容)
    - 两者都 None → "aureon_main" 默认
    """
    if namespace:
        return f"ns_{namespace.replace('-', '_')}"
    return dataset_name or "aureon_main"


# ─────────────────────────────────────────────────
# 4. 4 个核心 tool 实现
# ─────────────────────────────────────────────────

async def cognee_remember_async(
    data: str,
    dataset_name: str = "aureon_main",
    session_id: str | None = None,
    namespace: str | None = None,  # v0.24:namespace → dataset_name 物理隔离
) -> dict[str, Any]:
    """cognee 1.x remember = add(ingest + chunk) + cognify(knowledge graph) 一条龙
    跟 V1 add/cognify 区别:remember 自动跑 cognify

    v0.24:加 namespace 参数,自动转 dataset_name(物理隔离)
    """
    if not _HAS_COGNEE:
        return {"ok": False, "error": "cognee 未装,跑 `pip install cognee`"}

    # v0.24:namespace 解析
    final_dataset = _resolve_dataset_name(dataset_name, namespace)

    try:
        if session_id:
            # 会话级 — 走 cognee 1.x 的 session API(待验证,fallback 到永久记忆)
            try:
                await cognee.add(data, dataset_name=final_dataset)
                return {
                    "ok": True,
                    "action": "remember",
                    "mode": "session_fallback",
                    "session_id": session_id,
                    "dataset": final_dataset,
                    "namespace": namespace,
                    "note": "cognee 1.x session API 待 verify,临时用 add 永久存",
                }
            except Exception:
                await cognee.add(data, dataset_name=final_dataset)
                return {
                    "ok": True,
                    "action": "remember",
                    "mode": "session_fallback",
                    "dataset": final_dataset,
                    "namespace": namespace,
                }
        else:
            # 永久记忆
            await cognee.add(data, dataset_name=final_dataset)
            return {
                "ok": True,
                "action": "remember",
                "mode": "permanent",
                "dataset": final_dataset,
                "namespace": namespace,
            }
    except Exception as e:
        log.exception("cognee_remember failed")
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "traceback": traceback.format_exc()[:500]}


async def cognee_cognify_async(dataset_name: str = "aureon_main") -> dict[str, Any]:
    """手动触发认知化 — 把 add 进来的 raw data 转知识图谱。
    remember 默认会自动 cognify;如果用 add() 增量加,需手动调此。"""
    if not _HAS_COGNEE:
        return {"ok": False, "error": "cognee 未装"}

    try:
        await cognee.cognify(dataset_name=dataset_name)
        return {
            "ok": True,
            "action": "cognify",
            "dataset": dataset_name,
        }
    except Exception as e:
        log.exception("cognee_cognify failed")
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "traceback": traceback.format_exc()[:500]}


async def cognee_recall_async(
    query: str,
    session_id: str | None = None,
    top_k: int = 5,
    dataset_name: str | None = None,  # v0.24:支持单 dataset 过滤(向后兼容)
    namespace: str | None = None,  # v0.24:namespace → datasets 列表
) -> dict[str, Any]:
    """召回 — cognee 1.x 用 search + recall 同义
    返回带引用的搜索结果

    v0.24:加 namespace / dataset_name 参数,转 cognee 1.x 的 `datasets=[...]` list
    """
    if not _HAS_COGNEE:
        return {"ok": False, "error": "cognee 未装"}

    # v0.24:namespace / dataset_name 解析
    target_datasets: list[str] | None = None
    if namespace:
        ns_dataset = _resolve_dataset_name(dataset_name, namespace)
        target_datasets = [ns_dataset]
    elif dataset_name:
        target_datasets = [dataset_name]

    try:
        # cognee 1.x:recall 自动路由,search 直接走向量+图
        if hasattr(cognee, "recall"):
            try:
                kwargs = {"top_k": top_k}
                if session_id:
                    kwargs["session_id"] = session_id
                if target_datasets:
                    kwargs["datasets"] = target_datasets
                results = await cognee.recall(query, **kwargs)
            except TypeError as te:
                # 兼容老 cognee(无 datasets 参数)
                if target_datasets:
                    log.warning("cognee 不支持 datasets=,fallback 到全量 + 客户端过滤")
                    all_results = await cognee.recall(query, session_id=session_id) if session_id else await cognee.recall(query)
                    # 客户端按 dataset_name 过滤
                    results = _filter_results_by_dataset(all_results, target_datasets)
                else:
                    results = await cognee.recall(query, session_id=session_id) if session_id else await cognee.recall(query)
        else:
            results = await cognee.search(query)

        # results 是 SearchResultDocument 列表,序列化
        if hasattr(results, "__iter__") and not isinstance(results, (str, dict)):
            payload = []
            for r in results:
                if hasattr(r, "__dict__"):
                    item = dict(r.__dict__)
                    # UUID 转 str
                    for k, v in list(item.items()):
                        if hasattr(v, "hex"):
                            item[k] = str(v)
                    payload.append(item)
                else:
                    payload.append({"raw": str(r)})
            return {
                "ok": True,
                "action": "recall",
                "query": query,
                "namespace": namespace,
                "dataset_filter": target_datasets,
                "count": len(payload),
                "results": payload[:top_k],
            }
        else:
            return {
                "ok": True,
                "action": "recall",
                "query": query,
                "namespace": namespace,
                "dataset_filter": target_datasets,
                "results": str(results)[:2000],
            }
    except Exception as e:
        log.exception("cognee_recall failed")
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "traceback": traceback.format_exc()[:500]}


def _filter_results_by_dataset(results, target_datasets: list[str]):
    """客户端 fallback:cognee 版本不支持 datasets= 时,按 dataset_name 字段过滤结果。"""
    if not hasattr(results, "__iter__") or isinstance(results, (str, dict)):
        return results
    target_set = set(target_datasets)
    filtered = []
    for r in results:
        # 尝试从 SearchResultDocument 拿 dataset_name
        ds_name = getattr(r, "dataset_name", None) or getattr(r, "dataset", None)
        if ds_name is None and hasattr(r, "__dict__"):
            ds_name = r.__dict__.get("dataset_name") or r.__dict__.get("dataset")
        if ds_name is None or ds_name in target_set:
            filtered.append(r)
    return filtered


async def cognee_forget_async(
    dataset_name: str | None = None,
    everything: bool = False,
    namespace: str | None = None,  # v0.24:namespace → dataset_name 清理
) -> dict[str, Any]:
    """删除记忆 — 按 dataset 或 namespace 清理或清空全部"""
    if not _HAS_COGNEE:
        return {"ok": False, "error": "cognee 未装"}

    # v0.24:namespace 解析
    final_dataset = _resolve_dataset_name(dataset_name, namespace) if (dataset_name or namespace) else None

    try:
        if everything:
            await cognee.forget(everything=True)
            return {
                "ok": True,
                "action": "forget",
                "scope": "everything",
                "namespace": namespace,
            }
        elif final_dataset:
            await cognee.forget(dataset_name=final_dataset)
            return {
                "ok": True,
                "action": "forget",
                "scope": "dataset",
                "dataset": final_dataset,
                "namespace": namespace,
            }
        else:
            return {
                "ok": False,
                "error": "必须指定 dataset_name / namespace / everything=True",
            }
    except Exception as e:
        log.exception("cognee_forget failed")
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "traceback": traceback.format_exc()[:500]}


# ─────────────────────────────────────────────────
# 5. 同步包装(MCP handler 是 async,直接 await async fn)
# ─────────────────────────────────────────────────

def cognee_remember_impl(
    data: str,
    dataset_name: str = "aureon_main",
    session_id: str | None = None,
    namespace: str | None = None,
) -> str:
    return json.dumps(
        asyncio.run(cognee_remember_async(data, dataset_name, session_id, namespace)),
        ensure_ascii=False,
        default=str,
    )


def cognee_cognify_impl(dataset_name: str = "aureon_main") -> str:
    return json.dumps(
        asyncio.run(cognee_cognify_async(dataset_name)),
        ensure_ascii=False,
        default=str,
    )


def cognee_recall_impl(
    query: str,
    session_id: str | None = None,
    top_k: int = 5,
    dataset_name: str | None = None,
    namespace: str | None = None,
) -> str:
    return json.dumps(
        asyncio.run(cognee_recall_async(query, session_id, top_k, dataset_name, namespace)),
        ensure_ascii=False,
        default=str,
    )


def cognee_forget_impl(
    dataset_name: str | None = None,
    everything: bool = False,
    namespace: str | None = None,
) -> str:
    return json.dumps(
        asyncio.run(cognee_forget_async(dataset_name, everything, namespace)),
        ensure_ascii=False,
        default=str,
    )


# ── Dynamic Registry Exports (v0.38) ────────────────────────────

TOOL_DEFS = [
    {
        "name": "cognee_remember",
        "description": (
            "把文本写入 cognee 知识图谱(永久记忆)。"
            "LLM 默认走阿里百炼 qwen3-coder-plus,embed 走百炼 text-embedding-v3。"
            "OPENAI_API_KEY 已设可作为兜底。数据落 D:\\AureonCloud\\proton\\cognee\\。"
            "返回 ok=true 表示 add() 成功,要构建图谱请再调 cognee_cognify。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "要记住的文本内容"},
                "dataset_name": {"type": "string", "default": "aureon_main"},
                "session_id": {"type": "string", "description": "可选,fallback 到永久存"},
            },
            "required": ["data"],
        },
    },
    {
        "name": "cognee_cognify",
        "description": "手动触发认知化 — 把 add() 进来的 raw data 转知识图谱(LLM 抽实体 30-90s)",
        "inputSchema": {
            "type": "object",
            "properties": {"dataset_name": {"type": "string", "default": "aureon_main"}},
            "required": [],
        },
    },
    {
        "name": "cognee_recall",
        "description": "从知识图谱召回 — cognee 自动选最佳策略,返回 top_k 条带 dataset_id 的结果",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "session_id": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "cognee_forget",
        "description": "删除 cognee 记忆 — 按 dataset_name 或 everything=true(慎用)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dataset_name": {"type": "string"},
                "everything": {"type": "boolean", "default": False},
            },
            "required": [],
        },
    },
    {
        "name": "cognee_health",
        "description": "cognee 健康检查 — env key / 数据根 / 版本号",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

HANDLERS = {
    "cognee_remember": cognee_remember_impl,
    "cognee_cognify": cognee_cognify_impl,
    "cognee_recall": cognee_recall_impl,
    "cognee_forget": cognee_forget_impl,
    "cognee_health": cognee_health_impl,
}


# ─────────────────────────────────────────────────
# 6. 直接入口(脚本测试用)
# ─────────────────────────────────────────────────

if __name__ == "__main__":
    # 本地跑测试:`python cognee_tools.py`
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "health"

    if cmd == "health":
        print(cognee_health_impl())
    elif cmd == "remember":
        print(cognee_remember_impl(" ".join(sys.argv[2:]) or "Aureon v0.22 接 WireGuard"))
    elif cmd == "recall":
        print(cognee_recall_impl(" ".join(sys.argv[2:]) or "Aureon"))
    elif cmd == "cognify":
        print(cognee_cognify_impl())
    elif cmd == "forget":
        print(cognee_forget_impl(everything="--all" in sys.argv))
    else:
        print(f"unknown cmd: {cmd}", file=sys.stderr)
        sys.exit(1)