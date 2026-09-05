"""MCP server v0.1 — 动态工具注册 + 静态 fallback

走方向 2 决策:
- 不动 OI agent 主进程(那是 GUI)
- 不动 cursor-harness 本身
- MCP server 只做身份验证 + 工具暴露 + 错误捕获

v0.38 改进:
- 借鉴 openseek MCP dynamic tools/list 模式
- 工具模块导出 TOOL_DEFS + HANDLERS,server.py 自动发现
- 新增工具只需创建新 .py 文件,无需改 server.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# 把 cursor-harness 拉进来
_HARNESS_SRC = Path("D:/cursor-harness/src")
if _HARNESS_SRC.exists() and str(_HARNESS_SRC) not in sys.path:
    sys.path.insert(0, str(_HARNESS_SRC))

# 把 oi_enhancements 拉进来(为 cursor_harness_adapter)
_OI_ROOT = Path("C:/Users/Administrator/oi_enhancements")
if str(_OI_ROOT) not in sys.path:
    sys.path.insert(0, str(_OI_ROOT))

# MCP 协议
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

# v0.38 动态工具注册
from dynamic_registry import register_all, get_handler, get_registry_summary

# cursor-harness adapter
from cursor_harness_adapter import get_harness_for_oi, get_harness_for_claude

# v0.23.1 cognee 5 tool — 走百炼 LLM + 百炼 embed,数据落 D:\AureonCloud\proton\cognee
from cognee_tools import (
    cognee_health_impl,
    cognee_remember_impl,
    cognee_cognify_impl,
    cognee_recall_impl,
    cognee_forget_impl,
)
# v0.23.4 视觉 2 tool — 走百炼 qwen-vl-max,Windows 桌面 + MuMu 截图
from vision_tools import (
    camera_observe_impl,
    vision_health_impl,
    camera_observe_stream_impl,  # v0.24.2 stream 视觉
)
# v0.24 session 4 tool — namespace 自动加载 + MEMORY.md 审计
from session_tools import (
    session_load_context_impl,
    memory_namespace_set_impl,
    memory_namespace_list_impl,
    memory_audit_impl,
)
# v0.25 task queue 5 tool — Claude 写 task + Prisiragent 调度 + 状态管理
from task_tools import (
    task_submit_impl,
    task_status_impl,
    task_list_impl,
    task_cancel_impl,
    task_mark_impl,
)
# v0.32 knowledge graph 4 tool — NetworkX 知识图谱查询
from kg_tools import (
    kg_query_impl,
    kg_entity_info_impl,
    kg_neighbors_impl,
    kg_health_impl,
    kg_dedup_all_impl,
    kg_merge_entities_impl,
    kg_bfs_expand_impl,
    kg_add_relationship_impl,
    _embedding_dedup,
)


# 默认 cwd(跟 reviews/ 报告目录对齐)
DEFAULT_CWD = "D:/cursor-agent-cli"


def _health_payload() -> dict[str, Any]:
    """health check — 报告 key 是否就绪 + cwd 是否可访问"""
    oi_key = bool(os.environ.get("BAILIAN_API_KEY"))
    claude_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    cwd_exists = Path(DEFAULT_CWD).exists()
    # v0.23.1 — 追加 cognee health
    cognee_status = json.loads(cognee_health_impl())
    # v0.23.4 — 追加 vision health
    vision_status = json.loads(vision_health_impl())
    return {
        "alive": True,
        "version": "0.34.0",  # bump: kg_bfs_expand/kg_add_relationship/kg_embedding_dedup + Mandol-inspired enhancements
        "default_cwd": DEFAULT_CWD,
        "cwd_exists": cwd_exists,
        "model_keys": {
            "BAILIAN_API_KEY": oi_key,
            "ANTHROPIC_API_KEY": claude_key,
        },
        "harness_path": str(_HARNESS_SRC),
        "oi_root": str(_OI_ROOT),
        "cognee": {
            "version": cognee_status.get("cognee_version"),
            "ok": cognee_status.get("ok"),
            "data_root": cognee_status.get("data_root"),
        },
        "vision": {
            "bailian_base": vision_status.get("bailian_base"),
            "bailian_key_set": vision_status.get("bailian_key_set"),
            "default_source": vision_status.get("default_source"),
            "default_model": vision_status.get("default_model"),
            "capture_test_ok": vision_status.get("capture_test_ok"),
        },
    }
    oi_key = bool(os.environ.get("BAILIAN_API_KEY"))
    claude_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    cwd_exists = Path(DEFAULT_CWD).exists()
    return {
        "alive": True,
        "version": "0.1",
        "default_cwd": DEFAULT_CWD,
        "cwd_exists": cwd_exists,
        "model_keys": {
            "BAILIAN_API_KEY": oi_key,
            "ANTHROPIC_API_KEY": claude_key,
        },
        "harness_path": str(_HARNESS_SRC),
        "oi_root": str(_OI_ROOT),
    }


def _run_harness(harness_factory, prompt: str, cwd: str | None) -> str:
    """跑 harness.run() 并捕获错误。结果序列化成 JSON 字符串返给 MCP client"""
    target_cwd = cwd or DEFAULT_CWD
    target_path = Path(target_cwd)
    if not target_path.exists():
        return json.dumps({"ok": False, "error": f"cwd 不存在: {target_cwd}"})

    try:
        h = harness_factory()
        result = h.run(prompt=prompt, cwd=target_cwd)
        # HarnessResult 是 dataclass,to dict
        if hasattr(result, "__dict__"):
            payload = dict(result.__dict__)
        elif isinstance(result, dict):
            payload = result
        else:
            payload = {"raw": str(result)}
        return json.dumps(
            {
                "ok": True,
                "cwd": target_cwd,
                "result": payload,
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        return json.dumps(
            {
                "ok": False,
                "cwd": target_cwd,
                "error": f"{type(e).__name__}: {e}",
            },
            ensure_ascii=False,
        )


def _text_content(text: str) -> types.TextContent:
    """统一构造 TextContent — mcp.types.Content 是 union,不能直接调用"""
    return types.TextContent(type="text", text=text)


def build_server() -> Server:
    server = Server("prisiragent-mcp")

    # ── 动态工具注册 (v0.38) ──────────────────────────────
    # 借鉴 openseek MCP dynamic tools/list 模式:
    # 每个工具模块导出 TOOL_DEFS + HANDLERS,自动发现注册
    # 新增工具只需创建新 .py 文件,无需改 server.py
    _dynamic_tools = register_all(Path(__file__).parent)

    # 静态工具(非模块化,保留硬编码)
    _static_tools: list[types.Tool] = [
        types.Tool(
            name="run_oi_review",
            description=(
                "用 qwen-max via DashScope + cursor-harness 跑代码审查 / 调研类任务。"
                "返回完整 run() 结果(plan + tool calls + 文本输出)。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "给 harness 的任务描述,跟 Cursor Cloud Agent 输入同等粒度",
                    },
                    "cwd": {
                        "type": "string",
                        "description": f"工作目录,默认 {DEFAULT_CWD}",
                    },
                },
                "required": ["prompt"],
            },
        ),
        types.Tool(
            name="run_claude_review",
            description=(
                "用 Claude Opus 4.8 + Anthropic + cursor-harness 跑代码审查 / 调研类任务。"
                "ANTHROPIC_API_KEY 必须是 Opus 4.8 能调的 key。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "cwd": {
                        "type": "string",
                        "description": f"工作目录,默认 {DEFAULT_CWD}",
                    },
                },
                "required": ["prompt"],
            },
        ),
        types.Tool(
            name="list_models",
            description="列出此 MCP server 暴露的双 harness。",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="health",
            description="健康检查:env key 是否就绪 / cwd 是否存在 / adapter 路径",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="kg_embedding_dedup",
            description=(
                "双信号实体去重 — 结合字符串相似度 (difflib) + Bailian embedding 语义相似度。"
                "Mandol UnifiedFactPipeline entity matching: 两个信号都同意才建议合并,减少误判。"
                "返回所有疑似重复对及其双信号分数。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "threshold": {"type": "number", "default": 0.85, "description": "embedding 余弦相似度阈值"},
                },
                "required": [],
            },
        ),
    ]

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        # 动态工具 + 静态 fallback
        return _dynamic_tools + _static_tools
    async def handle_list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="run_oi_review",
                description=(
                    "用 qwen-max via DashScope + cursor-harness 跑代码审查 / 调研类任务。"
                    "返回完整 run() 结果(plan + tool calls + 文本输出)。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "给 harness 的任务描述,跟 Cursor Cloud Agent 输入同等粒度",
                        },
                        "cwd": {
                            "type": "string",
                            "description": f"工作目录,默认 {DEFAULT_CWD}",
                        },
                    },
                    "required": ["prompt"],
                },
            ),
            types.Tool(
                name="run_claude_review",
                description=(
                    "用 Claude Opus 4.8 + Anthropic + cursor-harness 跑代码审查 / 调研类任务。"
                    "ANTHROPIC_API_KEY 必须是 Opus 4.8 能调的 key。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "cwd": {
                            "type": "string",
                            "description": f"工作目录,默认 {DEFAULT_CWD}",
                        },
                    },
                    "required": ["prompt"],
                },
            ),
            types.Tool(
                name="list_models",
                description="列出此 MCP server 暴露的双 harness。",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="health",
                description="健康检查:env key 是否就绪 / cwd 是否存在 / adapter 路径",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            # ────────────────────────────────────────
            # v0.23.1 cognee 5 tool(走百炼 LLM + 百炼 embed,OPENAI 兜底)
            # ────────────────────────────────────────
            types.Tool(
                name="cognee_remember",
                description=(
                    "把文本写入 cognee 知识图谱(永久记忆)。"
                    "LLM 默认走阿里百炼 qwen3-coder-plus,embed 走百炼 text-embedding-v3。"
                    "OPENAI_API_KEY 已设可作为兜底。数据落 D:\\AureonCloud\\proton\\cognee\\。"
                    "返回 ok=true 表示 add() 成功,要构建图谱请再调 cognee_cognify。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "data": {"type": "string", "description": "要记住的文本内容"},
                        "dataset_name": {"type": "string", "default": "aureon_main"},
                        "session_id": {"type": "string", "description": "可选,fallback 到永久存"},
                    },
                    "required": ["data"],
                },
            ),
            types.Tool(
                name="cognee_cognify",
                description="手动触发认知化 — 把 add() 进来的 raw data 转知识图谱(LLM 抽实体 30-90s)",
                inputSchema={
                    "type": "object",
                    "properties": {"dataset_name": {"type": "string", "default": "aureon_main"}},
                    "required": [],
                },
            ),
            types.Tool(
                name="cognee_recall",
                description="从知识图谱召回 — cognee 自动选最佳策略,返回 top_k 条带 dataset_id 的结果",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "session_id": {"type": "string"},
                        "top_k": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="cognee_forget",
                description="删除 cognee 记忆 — 按 dataset_name 或 everything=true(慎用)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "dataset_name": {"type": "string"},
                        "everything": {"type": "boolean", "default": False},
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="cognee_health",
                description="cognee 健康检查 — env key / 数据根 / 版本号",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            # ────────────────────────────────────────
            # v0.23.4 vision 2 tool(走百炼 qwen-vl-max,Windows 桌面 + MuMu 截图)
            # ────────────────────────────────────────
            types.Tool(
                name="camera_observe",
                description=(
                    "单帧视觉观察 — 截图 + 视觉 LLM。"
                    "source: windows_desktop(Windows 桌面截图,PowerShell+System.Drawing) "
                    "或 mumu_screencap(MuMu 模拟器,adb)。"
                    "视觉模型走百炼 qwen-vl-max,直连,不绕 cc-switch。"
                    "返回 description(文字描述)+ usage(token 用量)。"
                    "save_image=True 会附 base64(默认 False 节省 token)。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": ["windows_desktop", "mumu_screencap"],
                            "default": "windows_desktop",
                        },
                        "prompt": {
                            "type": "string",
                            "default": "描述这张图,重点关注屏幕上的应用、文字、状态",
                        },
                        "save_image": {
                            "type": "boolean",
                            "default": False,
                            "description": "是否附 base64 图像(默认 False 节省 token)",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="vision_health",
                description="视觉能力健康检查 — 截图 + 百炼视觉模型可达性",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            # ────────────────────────────────────────
            # v0.24.2 stream 视觉 — 连续 N 帧 + 每帧送视觉 LLM
            # ────────────────────────────────────────
            types.Tool(
                name="camera_observe_stream",
                description=(
                    "连续 N 帧视觉观察 — 模拟实时视觉。"
                    "frames 抓几帧(默认 3),interval_sec 帧间隔(默认 1s)。"
                    "每帧独立调百炼 qwen-vl-max,返回 N 条 description + usage。"
                    "可观察屏幕内容变化(打字、滚动、动画)。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": ["windows_desktop", "mumu_screencap"],
                            "default": "windows_desktop",
                        },
                        "prompt": {"type": "string", "default": "描述这张图,重点关注屏幕上的应用、文字、状态"},
                        "frames": {"type": "integer", "default": 3, "description": "抓几帧"},
                        "interval_sec": {"type": "number", "default": 1.0, "description": "帧间隔秒"},
                    },
                    "required": [],
                },
            ),
            # ────────────────────────────────────────
            # v0.24 Session Memory Injector 4 tool
            # 能力:按 cwd/namespace 自动加载相关记忆子集
            # 不动 MEMORY.md;只加能力
            # ────────────────────────────────────────
            types.Tool(
                name="session_load_context",
                description=(
                    "按 session 上下文(cwd 自动推断 namespace)加载相关记忆子集。"
                    "namespace 走 cognee dataset 物理隔离。"
                    "无 namespace 时降级到全量 recall(向后兼容)。"
                    "返回 hits 数组 + count + truncated + fallback_used。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "会话 ID(必填)"},
                        "query": {"type": "string", "description": "召回 query"},
                        "cwd": {"type": "string", "default": "", "description": "当前工作目录(用于 namespace 推断)"},
                        "top_k": {"type": "integer", "default": 5},
                        "explicit_namespace": {"type": "string", "description": "手动覆盖 namespace(优先级最高)"},
                        "max_chars": {"type": "integer", "default": 1500, "description": "召回结果字符上限(防淹没 system prompt)"},
                    },
                    "required": ["session_id", "query"],
                },
            ),
            types.Tool(
                name="memory_namespace_set",
                description=(
                    "注册 namespace 到 OI Memory L1 层。"
                    "关联 cwd 白名单,max_context_chars 控制后续 recall 截断。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "namespace": {"type": "string", "description": "namespace 名(如 'aureon-cognee')"},
                        "cwd": {"type": "string", "default": ""},
                        "cwd_whitelist": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "允许的 cwd 白名单",
                        },
                        "max_context_chars": {"type": "integer", "default": 1500},
                    },
                    "required": ["namespace"],
                },
            ),
            types.Tool(
                name="memory_namespace_list",
                description=(
                    "列出所有已注册 namespace。"
                    "include_stats=true 时附带 OI Memory layer 分布。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "include_stats": {"type": "boolean", "default": False},
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="memory_audit",
                description=(
                    "审计 MEMORY.md 死链 + OI Memory 过载条目。"
                    "v0.24 dry_run=true 永远不删不改,只产出报告。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "check_path_exists": {"type": "boolean", "default": True},
                        "dry_run": {"type": "boolean", "default": True},
                        "sample_size": {"type": "integer", "default": 0, "description": "0=全量,>0=抽样"},
                    },
                    "required": [],
                },
            ),
            # ────────────────────────────────────────
            # v0.25 Task Queue 5 tool(Claude 写 task,Prisiragent 调度)
            # 设计:Claude 只管记忆链接 + 写 task;Prisiragent 自己读 queue 跑
            # ────────────────────────────────────────
            types.Tool(
                name="task_submit",
                description=(
                    "Claude 提交一个 task 到 OI Memory task queue。"
                    "depends_on 中的 task_id 全部 done 后,task 才进入 pending 状态。"
                    "namespace 默认 'tasks'。"
                    "Prisiragent 调度时通过 task_list --ready 拉 ready task 跑。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "任务标题(简短)"},
                        "content": {"type": "string", "description": "任务描述(详细,Prisiragent 拿到后能直接跑)"},
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "依赖的 task_id 列表",
                        },
                        "priority": {"type": "integer", "default": 0, "description": "数字越大越优先"},
                        "namespace": {"type": "string", "default": "tasks"},
                        "max_retries": {"type": "integer", "default": 3, "description": "Prisiragent 失败重试次数"},
                    },
                    "required": ["title", "content"],
                },
            ),
            types.Tool(
                name="task_status",
                description="查 task 详情(按 task_id)。返 status/retry_count/depends_on/result/error_msg。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer", "description": "要查的 task_id"},
                    },
                    "required": ["task_id"],
                },
            ),
            types.Tool(
                name="task_list",
                description=(
                    "拉 task 列表。"
                    "ready=True 时返 depends_on 全 done 的 pending task(Prisiragent 调度入口)。"
                    "blocked=True 时返 status=blocked 的 task(Claude 拉取入口)。"
                    "或按 status='pending/running/done/blocked/cancelled' 过滤。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "description": "pending/running/done/blocked/cancelled"},
                        "ready": {"type": "boolean", "default": False, "description": "拉 ready task"},
                        "blocked": {"type": "boolean", "default": False, "description": "拉 blocked task"},
                        "namespace": {"type": "string", "default": "tasks"},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="task_cancel",
                description="取消 task(status → cancelled),reason append 到 content。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer"},
                        "reason": {"type": "string", "default": ""},
                    },
                    "required": ["task_id"],
                },
            ),
            types.Tool(
                name="task_mark",
                description=(
                    "统一 mark 接口。"
                    "action=running:task 进入运行态。"
                    "action=done:task 完成,result append 到 content,自动解锁依赖此 task 的 blocked 任务。"
                    "action=retry-fail:失败次数+1,error_msg append;超 max_retries 自动转 blocked。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer"},
                        "action": {"type": "string", "enum": ["running", "done", "retry-fail"]},
                        "result": {"type": "string", "default": "", "description": "action=done 时的 result 内容"},
                        "error_msg": {"type": "string", "default": "", "description": "action=retry-fail 时的失败原因"},
                    },
                    "required": ["task_id", "action"],
                },
            ),
            # ────────────────────────────────────────
            # v0.32 Knowledge Graph 4 tool — NetworkX 知识图谱查询
            # ────────────────────────────────────────
            types.Tool(
                name="kg_query",
                description=(
                    "搜索知识图谱 — 按 query 在实体名/描述中模糊匹配,返回 top_k 个匹配实体及其 relation 摘要。"
                    "namespace 可选过滤(aureon_arch/aureon_experiences/aureon_knowledge)。"
                    "图谱当前有 {nodes} 节点 + {edges} 边。"
                ).format(nodes="PLACEHOLDER_NODES", edges="PLACEHOLDER_EDGES"),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词(实体名/描述模糊匹配)"},
                        "namespace": {
                            "type": "string",
                            "enum": ["aureon_arch", "aureon_experiences", "aureon_knowledge", ""],
                            "default": "",
                            "description": "限定 namespace 过滤,空=全量",
                        },
                        "top_k": {"type": "integer", "default": 10, "description": "返回最多结果数"},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="kg_entity_info",
                description="获取实体详情 — 名称、描述、来源文档、入边/出边关系(1-hop)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string", "description": "实体 ID(支持模糊匹配)"},
                    },
                    "required": ["entity_id"],
                },
            ),
            types.Tool(
                name="kg_neighbors",
                description="获取实体的 N-hop 邻居关系 — 返回入边和出边的邻居及关系类型",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string", "description": "实体 ID"},
                        "depth": {"type": "integer", "default": 1, "description": "搜索深度(1=直接邻居,2=2-hop)"},
                    },
                    "required": ["entity_id"],
                },
            ),
            types.Tool(
                name="kg_health",
                description="知识图谱健康检查 — 节点数、边数、文件大小",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            # v0.33: KG 模糊去重扫描
            types.Tool(
                name="kg_dedup_all",
                description="扫描所有实体对，报告名称相似的待合并对。force=true 时自动合并相似度>=0.95 的对。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "force": {"type": "boolean", "default": False, "description": "自动合并高度相似实体"},
                    },
                    "required": [],
                },
            ),
            # v0.33: KG 实体合并
            types.Tool(
                name="kg_merge_entities",
                description="手动合并两个实体: 将 source 的边/属性合并到 target，删除 source。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_id": {"type": "string", "description": "要合并掉的实体 ID"},
                        "target_id": {"type": "string", "description": "保留的目标实体 ID"},
                    },
                    "required": ["source_id", "target_id"],
                },
            ),
            # ────────────────────────────────────────
            # v0.34 KG 增强 (Mandol-inspired)
            # ────────────────────────────────────────
            types.Tool(
                name="kg_bfs_expand",
                description=(
                    "BFS 图扩展 — 从种子节点向外扩展 N-hop 邻居。"
                    "Mandol HybridRetriever BFS expansion pattern。"
                    "返回扩展后的节点列表 + 边信息，用于多跳关系发现。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "seed_ids": {"type": "string", "description": "逗号分隔的实体 ID 列表"},
                        "hops": {"type": "integer", "default": 1, "description": "BFS 深度 (默认 1)"},
                        "per_seed": {"type": "integer", "default": 3, "description": "每个种子扩展的邻居数 (默认 3)"},
                    },
                    "required": ["seed_ids"],
                },
            ),
            types.Tool(
                name="kg_add_relationship",
                description=(
                    "添加实体间关系边 — 支持 CAUSES/CAUSED_BY/PREFERS/EVIDENCED_BY/SEMANTIC_SIMILAR。"
                    "Mandol 分层记忆模型的基础设施：高层抽象记忆需要有向边连接回 base memory。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_id": {"type": "string", "description": "源实体 ID 或名称"},
                        "target_id": {"type": "string", "description": "目标实体 ID 或名称"},
                        "rel_type": {
                            "type": "string",
                            "enum": ["CAUSES", "CAUSED_BY", "PREFERS", "EVIDENCED_BY", "SEMANTIC_SIMILAR", "RELATED_TO"],
                            "description": "关系类型",
                        },
                        "properties": {"type": "string", "default": "{}", "description": "JSON 字符串，边的附加属性"},
                    },
                    "required": ["source_id", "target_id", "rel_type"],
                },
            ),
            types.Tool(
                name="kg_embedding_dedup",
                description=(
                    "双信号实体去重 — 结合字符串相似度 (difflib) + Bailian embedding 语义相似度。"
                    "Mandol UnifiedFactPipeline entity matching: 两个信号都同意才建议合并，减少误判。"
                    "返回所有疑似重复对及其双信号分数。"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "threshold": {"type": "number", "default": 0.85, "description": "embedding 余弦相似度阈值"},
                    },
                    "required": [],
                },
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[types.TextContent]:
        args = arguments or {}
        try:
            # ── 动态分发 (v0.38) ──────────────────────────────
            # 借鉴 openseek tools/call 模式:
            # 先从动态注册表查找 handler,找不到再用静态分支 fallback
            handler = get_handler(name)
            if handler is not None:
                # 动态工具: 直接从 args 调用 impl 函数
                import inspect
                sig = inspect.signature(handler)
                kwargs = {}
                for pname, param in sig.parameters.items():
                    if pname in ("self",):
                        continue
                    if pname in args:
                        kwargs[pname] = args[pname]
                    elif param.default is not inspect.Parameter.empty:
                        kwargs[pname] = param.default
                    else:
                        kwargs[pname] = None
                result = handler(**kwargs)
                return [_text_content(result)]

            # ── 静态 fallback ─────────────────────────────────
            # 非模块化工具(如 harness 调用)保留硬编码分发
            if name == "run_oi_review":
                text = _run_harness(
                    get_harness_for_oi,
                    prompt=str(args.get("prompt", "")),
                    cwd=args.get("cwd"),
                )
                return [_text_content(text)]
            elif name == "run_claude_review":
                text = _run_harness(
                    get_harness_for_claude,
                    prompt=str(args.get("prompt", "")),
                    cwd=args.get("cwd"),
                )
                return [_text_content(text)]
            elif name == "list_models":
                return [
                    _text_content(
                        json.dumps(
                            [
                                {
                                    "name": "qwen-max",
                                    "driver": "cursor_harness_adapter.get_harness_for_oi",
                                    "provider": "DashScope(OpenAI 兼容)",
                                    "tool": "run_oi_review",
                                },
                                {
                                    "name": "claude-opus-4-8",
                                    "driver": "cursor_harness_adapter.get_harness_for_claude",
                                    "provider": "Anthropic",
                                    "tool": "run_claude_review",
                                },
                            ],
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                ]
            elif name == "health":
                return [
                    _text_content(
                        json.dumps(_health_payload(), ensure_ascii=False, indent=2)
                    )
                ]
            # ────────────────────────────────────────
            # v0.23.1 cognee 5 tool 调用分支
            # ────────────────────────────────────────
            elif name == "cognee_remember":
                return [_text_content(
                    cognee_remember_impl(
                        data=str(args.get("data", "")),
                        dataset_name=str(args.get("dataset_name", "aureon_main")),
                        session_id=args.get("session_id"),
                    )
                )]
            elif name == "cognee_cognify":
                return [_text_content(
                    cognee_cognify_impl(
                        dataset_name=str(args.get("dataset_name", "aureon_main")),
                    )
                )]
            elif name == "cognee_recall":
                return [_text_content(
                    cognee_recall_impl(
                        query=str(args.get("query", "")),
                        session_id=args.get("session_id"),
                        top_k=int(args.get("top_k", 5)),
                    )
                )]
            elif name == "cognee_forget":
                return [_text_content(
                    cognee_forget_impl(
                        dataset_name=args.get("dataset_name"),
                        everything=bool(args.get("everything", False)),
                    )
                )]
            elif name == "cognee_health":
                return [_text_content(cognee_health_impl())]
            # ────────────────────────────────────────
            # v0.23.4 vision 2 tool 调用分支
            # ────────────────────────────────────────
            elif name == "camera_observe":
                return [_text_content(
                    camera_observe_impl(
                        source=str(args.get("source", "windows_desktop")),
                        prompt=str(args.get("prompt", "描述这张图,重点关注屏幕上的应用、文字、状态")),
                        save_image=bool(args.get("save_image", False)),
                    )
                )]
            elif name == "vision_health":
                return [_text_content(vision_health_impl())]
            elif name == "camera_observe_stream":
                return [_text_content(
                    camera_observe_stream_impl(
                        source=str(args.get("source", "windows_desktop")),
                        prompt=str(args.get("prompt", "描述这张图,重点关注屏幕上的应用、文字、状态")),
                        frames=int(args.get("frames", 3)),
                        interval_sec=float(args.get("interval_sec", 1.0)),
                    )
                )]
            # ────────────────────────────────────────
            # v0.24 Session Memory Injector 4 tool 分支
            # ────────────────────────────────────────
            elif name == "session_load_context":
                return [_text_content(
                    session_load_context_impl(
                        session_id=str(args.get("session_id", "")),
                        query=str(args.get("query", "")),
                        cwd=str(args.get("cwd", "")),
                        top_k=int(args.get("top_k", 5)),
                        explicit_namespace=args.get("explicit_namespace"),
                        max_chars=int(args.get("max_chars", 1500)),
                    )
                )]
            elif name == "memory_namespace_set":
                return [_text_content(
                    memory_namespace_set_impl(
                        namespace=str(args.get("namespace", "")),
                        cwd=str(args.get("cwd", "")),
                        cwd_whitelist=args.get("cwd_whitelist"),
                        max_context_chars=int(args.get("max_context_chars", 1500)),
                    )
                )]
            elif name == "memory_namespace_list":
                return [_text_content(
                    memory_namespace_list_impl(
                        include_stats=bool(args.get("include_stats", False)),
                    )
                )]
            elif name == "memory_audit":
                return [_text_content(
                    memory_audit_impl(
                        check_path_exists=bool(args.get("check_path_exists", True)),
                        dry_run=bool(args.get("dry_run", True)),
                        sample_size=int(args.get("sample_size", 0)),
                    )
                )]
            # ────────────────────────────────────────
            # v0.25 Task Queue 5 tool 分支
            # ────────────────────────────────────────
            elif name == "task_submit":
                return [_text_content(
                    task_submit_impl(
                        title=str(args.get("title", "")),
                        content=str(args.get("content", "")),
                        depends_on=args.get("depends_on"),
                        priority=int(args.get("priority", 0)),
                        namespace=str(args.get("namespace", "tasks")),
                        max_retries=int(args.get("max_retries", 3)),
                    )
                )]
            elif name == "task_status":
                return [_text_content(
                    task_status_impl(task_id=args.get("task_id"))
                )]
            elif name == "task_list":
                return [_text_content(
                    task_list_impl(
                        status=args.get("status"),
                        ready=bool(args.get("ready", False)),
                        blocked=bool(args.get("blocked", False)),
                        namespace=str(args.get("namespace", "tasks")),
                        limit=int(args.get("limit", 20)),
                    )
                )]
            elif name == "task_cancel":
                return [_text_content(
                    task_cancel_impl(
                        task_id=int(args.get("task_id", 0)),
                        reason=str(args.get("reason", "")),
                    )
                )]
            elif name == "task_mark":
                return [_text_content(
                    task_mark_impl(
                        task_id=int(args.get("task_id", 0)),
                        action=str(args.get("action", "")),
                        result=str(args.get("result", "")),
                        error_msg=str(args.get("error_msg", "")),
                    )
                )]
            # ────────────────────────────────────────
            # v0.32 Knowledge Graph 4 tool 分支
            # ────────────────────────────────────────
            elif name == "kg_query":
                return [_text_content(
                    kg_query_impl(
                        query=str(args.get("query", "")),
                        namespace=str(args.get("namespace", "")),
                        top_k=int(args.get("top_k", 10)),
                    )
                )]
            elif name == "kg_entity_info":
                return [_text_content(
                    kg_entity_info_impl(
                        entity_id=str(args.get("entity_id", "")),
                    )
                )]
            elif name == "kg_neighbors":
                return [_text_content(
                    kg_neighbors_impl(
                        entity_id=str(args.get("entity_id", "")),
                        depth=int(args.get("depth", 1)),
                    )
                )]
            elif name == "kg_health":
                return [_text_content(kg_health_impl())]
            elif name == "kg_dedup_all":
                return [_text_content(
                    kg_dedup_all_impl(force=bool(args.get("force", False)))
                )]
            elif name == "kg_merge_entities":
                return [_text_content(
                    kg_merge_entities_impl(
                        source_id=str(args.get("source_id", "")),
                        target_id=str(args.get("target_id", "")),
                    )
                )]
            # ────────────────────────────────────────
            # v0.34 KG 增强 (Mandol-inspired)
            # ────────────────────────────────────────
            elif name == "kg_bfs_expand":
                return [_text_content(
                    kg_bfs_expand_impl(
                        seed_ids=str(args.get("seed_ids", "")),
                        hops=int(args.get("hops", 1)),
                        per_seed=int(args.get("per_seed", 3)),
                    )
                )]
            elif name == "kg_add_relationship":
                return [_text_content(
                    kg_add_relationship_impl(
                        source_id=str(args.get("source_id", "")),
                        target_id=str(args.get("target_id", "")),
                        rel_type=str(args.get("rel_type", "RELATED_TO")),
                        properties=str(args.get("properties", "{}")),
                    )
                )]
            elif name == "kg_embedding_dedup":
                return [_text_content(
                    json.dumps(
                        _embedding_dedup(
                            threshold=float(args.get("threshold", 0.85)),
                        ),
                        ensure_ascii=False,
                        indent=2,
                    )
                )]
            else:
                return [
                    _text_content(
                        json.dumps({"ok": False, "error": f"未知 tool: {name}"})
                    )
                ]
        except Exception as e:
            return [
                _text_content(
                    json.dumps(
                        {"ok": False, "error": f"{type(e).__name__}: {e}"},
                        ensure_ascii=False,
                    )
                )
            ]

    return server


async def _main():
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main():
    import asyncio
    asyncio.run(_main())


if __name__ == "__main__":
    main()
