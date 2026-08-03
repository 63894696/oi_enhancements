"""Cursor API backend — oiagent harness 新能力来源

2026-07-23 调研发现:
- CURSOR_API_KEY + CURSOR_BASE_URL 在 env
- 端点:GET /v0/models, GET /v0/agents, POST /v0/agents(推测 /v0/agents/{id} 状态)
- 鉴权:Authorization: Bearer <key>
- prompt 字段是 {text: "..."} object
- 必须 source.repository(可访问的 git 仓库 + default branch)
- 19 个模型:claude-opus-4-8-thinking-high 是最强的

oiagent 用这个 adapter:
1. 把 Cursor 当"另一类 LLM provider"(类似 ollama / moonshot)接入 race / dispatch
2. 用 background agent 跑 IDE 之外的任务(开发 / 测试 / review)

## 已知限制(2026-07-23 实测)
- **create_agent 必须接 GitHub App** — 必须先在 https://cursor.com/settings 接 GitHub,
  授权当前账号访问目标仓库,然后才能 POST /v0/agents(否则 400 "Failed to determine default branch")
- **Cursor API 没有 chat 端点** — 只能用 background agent(单次 API key 鉴权,不能像 OpenAI 那样并发 chat)
- 已知模型:claude-opus-4-8-thinking-high / gpt-5.6-sol-xhigh / claude-fable-5-thinking-xhigh 等 19 个

env 依赖:
- CURSOR_API_KEY(必填)
- CURSOR_BASE_URL(默认 https://api.cursor.com)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = os.environ.get("CURSOR_BASE_URL", "https://api.cursor.com").rstrip("/")


def _err(stage: str, exc: Exception) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": str(exc),
            "stage": stage,
            "traceback": str(exc.__traceback__)[:500] if exc.__traceback__ else "",
        },
        ensure_ascii=False,
    )


def _request(path: str, method: str = "GET", payload: dict | None = None, timeout: int = 15) -> dict:
    """统一 cursor API 调用"""
    key = os.environ.get("CURSOR_API_KEY", "")
    if not key:
        return {"ok": False, "error": "CURSOR_API_KEY 未设", "stage": "auth"}

    url = f"{BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return {"ok": True, "status": resp.status, "data": json.loads(body)}
            except json.JSONDecodeError:
                return {"ok": True, "status": resp.status, "data": body}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return {"ok": False, "status": e.code, "error": body or e.reason, "stage": "http"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"ok": False, "error": str(e), "stage": "network"}


# ── MCP 工具 ──
def cursor_list_models_impl() -> str:
    """列 Cursor 可用模型"""
    try:
        r = _request("/v0/models")
        return json.dumps(r, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("cursor_list_models", e)


def cursor_list_agents_impl() -> str:
    """列当前已创建的 background agents"""
    try:
        r = _request("/v0/agents")
        return json.dumps(r, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("cursor_list_agents", e)


def cursor_create_agent_impl(
    prompt_text: str,
    model: str = "gpt-5.4-high-fast",
    repository: str = "",
    auto_create_pr: bool = False,
    branch_name: str = "",
) -> str:
    """创建 Cursor background agent

    args:
      prompt_text: 任务描述
      model: 19 个可选模型之一(默认 gpt-5.4-high-fast)
      repository: git URL(必填,Cursor 要能 clone 才有 default branch)
      auto_create_pr: 完成后自动开 PR(可选,**当前 schema 不识别,会报 unrecognized_keys**)
      branch_name: agent 工作的分支名(可选,**当前 schema 不识别,会报 unrecognized_keys**)

    注意:2026-07-23 实测 Cursor API 当前 schema 只接 prompt + model + source.repository
    其它字段(branch_name / auto_create_pr / source.branch) 都被识别为 unrecognized_keys
    """
    try:
        payload: dict[str, Any] = {
            "prompt": {"text": prompt_text},
            "model": model,
        }
        if repository:
            payload["source"] = {"repository": repository}
        # branch_name / auto_create_pr 当前 schema 不支持,先不传
        # if branch_name:
        #     payload["branch_name"] = branch_name
        # if auto_create_pr:
        #     payload["auto_create_pr"] = auto_create_pr

        r = _request("/v0/agents", method="POST", payload=payload, timeout=20)
        return json.dumps(r, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("cursor_create_agent", e)


# ── Dynamic Registry Exports ─────────────────────────
TOOL_DEFS = [
    {
        "name": "cursor_list_models",
        "description": "列 Cursor 后端可用模型(19 个,含 claude-opus-4-8-thinking-high 等)",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cursor_list_agents",
        "description": "列当前已创建的 Cursor background agents",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cursor_create_agent",
        "description": (
            "创建 Cursor background agent(在 Cursor 后端开一个异步任务)"
            "注意:必须有可访问的 git 仓库(repository 参数),Cursor 才能 clone"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt_text": {"type": "string", "description": "任务描述"},
                "model": {
                    "type": "string",
                    "description": "模型 id,默认 gpt-5.4-high-fast。可选:claude-opus-4-8-thinking-high / claude-fable-5-thinking-xhigh 等",
                },
                "repository": {
                    "type": "string",
                    "description": "git URL(Cursor 会 clone,必须有 default branch)",
                },
                "auto_create_pr": {
                    "type": "boolean",
                    "description": "完成后自动开 PR(可选)",
                },
                "branch_name": {
                    "type": "string",
                    "description": "agent 工作的分支名(可选)",
                },
            },
            "required": ["prompt_text"],
        },
    },
]


HANDLERS = {
    "cursor_list_models": cursor_list_models_impl,
    "cursor_list_agents": cursor_list_agents_impl,
    "cursor_create_agent": cursor_create_agent_impl,
}


# ============================================================
# Standalone CLI
# ============================================================
def _cli():
    import argparse

    p = argparse.ArgumentParser(description="Cursor API backend — oiagent harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("models", help="列可用模型")
    sub.add_parser("agents", help="列已创建的 agents")

    p_create = sub.add_parser("create", help="创建 background agent")
    p_create.add_argument("prompt", help="任务描述")
    p_create.add_argument("--model", default="gpt-5.4-high-fast", help="模型")
    p_create.add_argument("--repository", default="", help="git URL")
    p_create.add_argument("--branch", default="", help="工作分支")

    args = p.parse_args()

    if args.cmd == "models":
        print(cursor_list_models_impl())
    elif args.cmd == "agents":
        print(cursor_list_agents_impl())
    elif args.cmd == "create":
        print(cursor_create_agent_impl(
            prompt_text=args.prompt,
            model=args.model,
            repository=args.repository,
            branch_name=args.branch,
        ))


if __name__ == "__main__":
    _cli()