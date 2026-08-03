"""oiagent-team-lead 派单插件 (v0.1, 2026-07-23)

挂在 mcp_oiagent_server actor 下,提供 3 个工具:
- dispatch: 按任务描述查 routing.yaml → 返回 {agent, pool, mode}
- race: 同 prompt 并发多模型,先返回的进 trace
- trace: 写入派单决策到已有 trace 机制(jsonl append-only)

设计原则:
- 0-token 决策:dispatch 走关键词匹配,不调 LLM
- 模型池引用:model 字段只引用 pool 名,不写死 provider/model
- 默认 cheap_lottery (Race 模式抽奖层),失败再升级

复用:
- ~/.claude/mcp_oiagent_routing.yaml  (routing 表)
- ~/.claude/oiagent_harness_training/  (trace 输出目录)
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import asyncio
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

# ── 路径 ────────────────────────────────────────────
ROUTING_PATH = Path.home() / ".claude" / "mcp_oiagent_routing.yaml"
TRACE_DIR = Path.home() / ".claude" / "oiagent_harness_training"
# v0.48 OIagent 团队协作经验保存
OBSIDIAN_VAULT = Path(os.environ.get(
    "OBSIDIAN_VAULT",
    r"C:/Users/Administrator/Documents/ObsidianVault",
))
OBSIDIAN_EXPERIENCES_DIR = OBSIDIAN_VAULT / "experiences"
OBSIDIAN_EXPERIENCES_DIR.mkdir(parents=True, exist_ok=True)



def _err(stage: str, exc: Exception) -> str:
    """统一错误格式 (跟 .claude/rules/common.md 约定一致)"""
    return json.dumps(
        {
            "ok": False,
            "error": str(exc),
            "stage": stage,
            "traceback": str(exc.__traceback__)[:500] if exc.__traceback__ else "",
        },
        ensure_ascii=False,
    )


def _load_routing() -> dict:
    """读 routing.yaml —— 用 PyYAML(全局已装 6.0.3,无新增依赖)"""
    if not ROUTING_PATH.exists():
        raise FileNotFoundError(f"routing table not found: {ROUTING_PATH}")

    with ROUTING_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── dispatch ─────────────────────────────────────────
def _match_rule(rules: list, task_text: str) -> dict:
    """关键词匹配 —— 命中第一条即返回"""
    task_lower = task_text.lower()

    for rule in rules:
        if "default" in rule:
            continue  # 默认规则最后处理
        match = rule.get("match", {})
        keywords = match.get("keywords", [])
        for kw in keywords:
            if kw.lower() in task_lower:
                return {
                    "agent": rule.get("agent"),
                    "pool": rule.get("pool"),
                    "mode": rule.get("mode"),
                    "intent": match.get("intent"),
                    "matched_keyword": kw,
                    "rule_hit": True,
                    "agent_config": rule.get("agent_config"),  # CrewAI 三件套
                    "extra": rule.get("extra"),
                }

    # default 兜底
    for rule in rules:
        if "default" in rule:
            return {
                "agent": rule["default"].get("agent"),
                "pool": rule["default"].get("pool"),
                "mode": rule["default"].get("mode"),
                "intent": "default",
                "matched_keyword": None,
                "rule_hit": False,
                "agent_config": rule["default"].get("agent_config"),
                "extra": rule["default"].get("extra"),
            }
    raise ValueError("no default rule in routing.yaml")


def dispatch_impl(task: str) -> str:
    """按任务描述查 routing.yaml,返回派单决策(agent / pool / mode)

    B1:失败时降级到默认 agent,不抛异常
    """
    try:
        if not task or not task.strip():
            raise ValueError("task 不能为空")

        routing = _load_routing()
        rules = routing.get("rules", [])
        pools = routing.get("model_pool", {})

        decision = _match_rule(rules, task)
        decision["task"] = task
        decision["pool_models"] = pools.get(decision["pool"], [])
        decision["routing_version"] = routing.get("version")

        # 写 trace
        _append_trace(
            event="dispatch",
            payload=decision,
        )

        return json.dumps({"ok": True, **decision}, ensure_ascii=False)
    except Exception as e:
        # B1: 失败降级到默认 agent(Explore),写 trace
        fallback = {
            "ok": True,  # 降级仍返 ok=True,告诉调用方走 fallback
            "agent": "Explore",
            "pool": "cheap_lottery",
            "mode": "engineer",
            "intent": "default",
            "matched_keyword": None,
            "rule_hit": False,
            "agent_config": None,
            "extra": None,
            "task": task,
            "pool_models": [],
            "routing_version": None,
            "fallback": True,
            "error": str(e),
        }
        _append_trace(
            event="dispatch_fallback",
            payload={
                "task": task,
                "error": str(e),
                "fallback_agent": "Explore",
            },
        )
        return json.dumps(fallback, ensure_ascii=False)


# ── race ─────────────────────────────────────────────
# 模型 endpoint 映射表
# 2026-07-23: cc-switch 15721 实测只是 health 端点,不是模型代理。
# race 改成**直连各家**(OpenAI 兼容协议 + Anthropic 原生协议)
ENDPOINT_MAP = {
    "ollama": {
        "base_url": "https://ollama.com/v1",
        "api_key_envs": ["OLLAMA_API_KEY", "OLLAMA_API_KEY2"],
        "protocol": "openai",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_envs": ["OPENROUTER_API_KEY"],
        "protocol": "openai",
    },
    "minimax": {
        "base_url": "https://api.minimax.chat/v1",
        "api_key_envs": ["MINIMAX_API_KEY"],
        "protocol": "openai",
        "display_name": "MiniMax-M3",
    },
    "m3": {  # alias of minimax(routing.yaml 写作 'm3:minimax')
        "alias_of": "minimax",
    },
    "agnes": {
        "base_url": "https://apihub.agnes-ai.com/v1",
        "api_key_envs": ["AGNES_API_KEY", "AGNES_API_KEY2"],
        "protocol": "openai",
        "display_name": "agnes-2.0-flash",
    },
    "k3": {
        # K3 = Moonshot Kimi(K3 是它在 oiagent 里的命名)
        # 2026-07-23:用户澄清两个端点
        #   - MOONSHOT_BASE_URL    = 按量 (PAYG)
        #   - MOONSHOTDY_BASE_URL  = 月包订阅 (等用户恢复后设 env)
        # race 时按 model spec 区分:k3_sub → 订阅端点;k3_payg → 按量端点
        # 这里只配默认(按量),sub 由 _resolve_endpoint 里的 variant 切换
        "base_url_env": "MOONSHOT_BASE_URL",
        "api_key_envs": ["MOONSHOT_API_KEY"],
        "protocol": "openai",
        "sub_base_url_env": "MOONSHOTDY_BASE_URL",  # 订阅端点(可能未设)
        "sub_api_key_envs": ["MOONSHOTDY_API_KEY"],  # 订阅 key(可能未设)
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "api_key_envs": ["ANTHROPIC_API_KEY"],
        "protocol": "anthropic",
    },
}


def _parse_model_spec(model_spec: str) -> dict:
    """解析 'ollama/deepseek-coder:6.7b' / 'm3:minimax' / 'anthropic:claude-opus-4-8'

    规则:
      - "/" 永远是真分隔符(ollama/openrouter 用它)
      - ":" 只在第一个 "/" **之前**才是分隔符,否则是 model id 的一部分(如 'gpt-oss:120b')

    返回 {provider, rest, model_spec}
    """
    if "/" in model_spec:
        # ollama/deepseek-coder:6.7b 或 openrouter/xxx/yyy:free
        provider, rest = model_spec.split("/", 1)
        return {"provider": provider, "rest": rest, "model_spec": model_spec}
    if ":" in model_spec:
        # m3:minimax 或 k3:k3_sub 或 anthropic:claude-opus-4-8
        provider, rest = model_spec.split(":", 1)
        return {"provider": provider, "rest": rest, "model_spec": model_spec}
    return {"provider": "unknown", "rest": model_spec, "model_spec": model_spec}


def _resolve_endpoint(model_spec: str) -> dict:
    """把 model spec 解析成可调用的 endpoint 配置"""
    parsed = _parse_model_spec(model_spec)
    provider = parsed["provider"]
    cfg = ENDPOINT_MAP.get(provider)
    if not cfg:
        return {"error": f"unknown provider: {provider}", "model_spec": model_spec}
    # 处理 alias
    if "alias_of" in cfg:
        provider = cfg["alias_of"]
        cfg = ENDPOINT_MAP.get(provider)
        if not cfg:
            return {"error": f"alias target missing: {provider}", "model_spec": model_spec}

    # K3 特殊处理:订阅 vs 按量走不同 endpoint
    #   k3:k3_sub → MOONSHOTDY_BASE_URL (月包)
    #   k3:k3_payg → MOONSHOT_BASE_URL (按量)
    if provider == "k3" and parsed["rest"] in ("k3_sub", "k3_payg"):
        if parsed["rest"] == "k3_sub":
            base_url_env = cfg.get("sub_base_url_env", "")
            api_key_envs = cfg.get("sub_api_key_envs", [])
            variant = "sub"
        else:
            base_url_env = cfg.get("base_url_env", "")
            api_key_envs = cfg.get("api_key_envs", [])
            variant = "payg"
        base_url = os.environ.get(base_url_env, "")
        api_key = ""
        for env_name in api_key_envs:
            api_key = os.environ.get(env_name, "")
            if api_key:
                break
        if not base_url:
            return {"error": f"k3 {variant} 端点未设(env: {base_url_env},等月包恢复后注入)", "model_spec": model_spec}
        if not api_key:
            return {"error": f"k3 {variant} key 未设(env: {api_key_envs})", "model_spec": model_spec}
    else:
        base_url = cfg.get("base_url") or os.environ.get(cfg.get("base_url_env", ""), "")
        api_key = ""
        for env_name in cfg.get("api_key_envs", []):
            api_key = os.environ.get(env_name, "")
            if api_key:
                break

        if not base_url:
            return {"error": f"no base_url for {provider} (env: {cfg.get('base_url_env')})", "model_spec": model_spec}
        if not api_key:
            return {"error": f"no api_key for {provider} (env: {cfg.get('api_key_envs')})", "model_spec": model_spec}

    # model id 推导:大多数情况下 rest 就是 model id(ollama/deepseek-coder:6.7b 这种)
    # m3:minimax 情况:provider=minimax, rest=minimax,这就是 model id
    # k3:k3_sub:provider=k3, rest=k3_sub,variant name 跟实际 model 同名
    model_id = parsed["rest"]

    return {
        "provider": provider,
        "model_id": model_id,
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "protocol": cfg.get("protocol", "openai"),
        "model_spec": model_spec,
    }


async def _race_one(model_spec: str, prompt: str, timeout_s: int) -> dict:
    """调一个模型,带超时。先返回的进 trace,其余取消。

    协议:
      - openai: POST {base_url}/chat/completions, model={model_id}, messages=[{user, content}]
      - anthropic: POST {base_url}/messages, model={model_id}, messages=[{user, content}], max_tokens
    """
    start = time.time()
    endpoint = _resolve_endpoint(model_spec)
    if "error" in endpoint:
        return {
            "model": model_spec,
            "ok": False,
            "error": endpoint["error"],
            "elapsed_ms": 0,
        }

    protocol = endpoint["protocol"]
    base_url = endpoint["base_url"]
    api_key = endpoint["api_key"]
    model_id = endpoint["model_id"]

    # 在线程池里跑同步 HTTP,避免阻塞 event loop
    loop = asyncio.get_event_loop()

    def _do_request() -> dict:
        try:
            if protocol == "openai":
                url = f"{base_url}/chat/completions"
                body = json.dumps({
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1,
                    "stream": False,
                }).encode()
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    data = json.loads(resp.read())
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    return {"ok": True, "result": content, "raw": data}

            elif protocol == "anthropic":
                url = f"{base_url}/messages"
                body = json.dumps({
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1,
                }).encode()
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    data = json.loads(resp.read())
                    content = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
                    return {"ok": True, "result": content, "raw": data}

            else:
                return {"ok": False, "error": f"unknown protocol: {protocol}"}

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            return {"ok": False, "error": f"HTTP {e.code}: {body or e.reason}"}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return {"ok": False, "error": str(e)}

    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _do_request),
            timeout=timeout_s,
        )
        elapsed = time.time() - start
        out = {
            "model": model_spec,
            "ok": result.get("ok", False),
            "elapsed_ms": int(elapsed * 1000),
        }
        if result.get("ok"):
            out["result"] = (result.get("result") or "")[:200]
            # 只在 debug 模式下保留 raw(会很大)
            # out["raw"] = result.get("raw")
        else:
            out["error"] = result.get("error")
        return out
    except asyncio.TimeoutError:
        return {
            "model": model_spec,
            "ok": False,
            "error": "timeout",
            "elapsed_ms": int((time.time() - start) * 1000),
        }


async def _race_async(prompt: str, models: list[str], timeout_s: int, concurrency: int) -> dict:
    """并发跑多个模型,先返回的赢"""
    sem = asyncio.Semaphore(concurrency)

    async def gated(model):
        async with sem:
            return await _race_one(model, prompt, timeout_s)

    tasks = [asyncio.create_task(gated(m)) for m in models]
    # 等待第一个完成
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    winner = list(done)[0].result() if done else None

    # 取消 pending
    for t in pending:
        t.cancel()

    return {
        "winner": winner,
        "losers_count": len(pending),
        "total_models": len(models),
    }


def race_impl(prompt: str, models: list[str] | None = None, timeout_s: int = 60) -> str:
    """Race 模式:同 prompt 并发多模型,先返回的进 trace

    models=None 时从 routing.yaml 的 race.enabled_pool 取(默认 cheap_lottery)
    models 可以是 list[str](直接传模型),也可以是 string(当作 pool 名,从 model_pool 取)
    """
    try:
        if not prompt or not prompt.strip():
            raise ValueError("prompt 不能为空")

        routing = _load_routing()
        race_cfg = routing.get("race", {})
        pools = routing.get("model_pool", {})

        if models is None:
            models = race_cfg.get("enabled_pool", [])

        # 如果 models 是单个字符串,当作 pool 名展开
        if isinstance(models, str):
            pool_models = pools.get(models, [])
            if not pool_models:
                raise ValueError(f"pool '{models}' 不存在或为空")
            models = pool_models

        if not models:
            raise ValueError("models 列表为空,且 routing.yaml race.enabled_pool 也为空")

        timeout_s = timeout_s or race_cfg.get("timeout_s", 60)
        concurrency = race_cfg.get("concurrency", len(models))

        result = asyncio.run(_race_async(prompt, models, timeout_s, concurrency))

        _append_trace(
            event="race",
            payload={
                "prompt": prompt,
                "models": models,
                "winner": result["winner"],
                "losers_count": result["losers_count"],
            },
        )

        return json.dumps({"ok": True, **result}, ensure_ascii=False)
    except Exception as e:
        return _err("race", e)


# ── trace ────────────────────────────────────────────
def _append_trace(event: str, payload: dict) -> None:
    """trace → jsonl append-only + 异步 ingest 到 cognee(借鉴 Mem0 自动 ingest)

    主流程不阻塞:jsonl 必须写成功;cognee ingest 在后台跑,失败 fallback 到 jsonl
    """
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    trace_file = TRACE_DIR / f"{time.strftime('%Y-%m-%d')}.jsonl"
    record = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
        **payload,
    }
    with trace_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # P0-2: 异步 fire-and-forget ingest 到 cognee
    # 把 trace 序列化成人类可读的事实,让 cognee 后续可语义检索
    _ingest_trace_async(record)


def _format_trace_for_cognee(record: dict) -> str:
    """把 trace 记录转成 cognee 可语义检索的事实文本"""
    event = record.get("event", "?")
    intent = record.get("intent", "?")
    agent = record.get("agent", "?")
    pool = record.get("pool", "?")
    task = record.get("task", "")
    extra = []
    if record.get("matched_keyword"):
        extra.append(f"关键词='{record['matched_keyword']}'")
    if record.get("winner"):
        winner = record["winner"]
        if isinstance(winner, dict):
            extra.append(f"winner={winner.get('model', '?')}")
    extra_s = ";".join(extra)
    return (
        f"[oiagent trace] event={event} intent={intent} agent={agent} pool={pool} "
        f"task='{task[:80]}' {extra_s}"
    )


def _ingest_trace_async(record: dict) -> None:
    """后台跑 cognee.remember,失败 fallback

    设计:不抛异常,不阻塞主流程。失败时:
    1. 尝试写 cognee-ready jsonl(等 cognee 库修了 Python 3.12 兼容后再批量 ingest)
    2. stderr 一行警告,不影响主 trace jsonl
    """
    import threading

    def _do():
        # ── 双轨:既调 cognee,也写 cognee-ready jsonl,任一失败不影响另一个 ──

        # 1. 调 cognee(当前 Python 3.12 上 tenacity 库不兼容,会失败)
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from cognee_tools import cognee_remember_impl
            text = _format_trace_for_cognee(record)
            r = cognee_remember_impl(
                data=text,
                dataset_name="oiagent_harness_training",
            )
            if '"ok": true' not in r and '"ok":true' not in r:
                print(f"[trace-ingest] cognee ingest 非 ok 返回: {r[:200]}", file=sys.stderr)
        except Exception as e:
            # 不报警:cognee 库当前 Python 3.12 上 broken,等修了再启用
            pass

        # 2. 写 cognee-ready jsonl(以后批量 ingest 用)
        try:
            cognee_ready_dir = TRACE_DIR / "cognee_ready"
            cognee_ready_dir.mkdir(parents=True, exist_ok=True)
            ready_file = cognee_ready_dir / f"{time.strftime('%Y-%m-%d')}.jsonl"
            text = _format_trace_for_cognee(record)
            with ready_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"text": text, "source": "trace", "ts": record.get("ts")}, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[trace-ingest] cognee_ready jsonl 失败: {e}", file=sys.stderr)

    threading.Thread(target=_do, daemon=True).start()


def trace_impl(event: str, payload: dict) -> str:
    """手动写一条 trace (供外部 hook / skill 调用)"""
    try:
        _append_trace(event, payload)
        return json.dumps({"ok": True, "appended": True}, ensure_ascii=False)
    except Exception as e:
        return _err("trace", e)


# ── Dynamic Registry Exports ─────────────────────────
TOOL_DEFS = [
    {
        "name": "team_lead_dispatch",
        "description": (
            "oiagent-team-lead 派单:按任务描述查 ~/.claude/mcp_oiagent_routing.yaml,"
            "返回 {agent, pool, mode, matched_keyword},0-token 关键词匹配,"
            "同时写一条 trace 到 ~/.claude/oiagent_harness_training/<date>.jsonl"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "任务描述文本(中英混合,子串匹配)"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "team_lead_race",
        "description": (
            "Race 模式:同 prompt 并发调多模型,先返回的赢。"
            "models=None 时从 routing.yaml race.enabled_pool 取(默认 cheap_lottery)。"
            "并发数/超时在 routing.yaml race 段配置。"
            "写一条 trace。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "要发的 prompt"},
                "models": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选,模型列表;缺省从 routing.yaml race.enabled_pool 取",
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "可选,单模型超时(秒);缺省 60",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "team_lead_trace",
        "description": (
            "手动写一条 trace 到 ~/.claude/oiagent_harness_training/<date>.jsonl。"
            "供外部 hook / skill 调用,作为 oiagent 反推 IDE-无关 harness 的训练数据。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "event": {"type": "string", "description": "事件名,如 dispatch/race/review/feedback"},
                "payload": {"type": "object", "description": "事件负载(JSON 对象)"},
            },
            "required": ["event", "payload"],
        },
    },
]


HANDLERS = {
    "team_lead_dispatch": dispatch_impl,
    "team_lead_race": race_impl,
    "team_lead_trace": trace_impl,
}


# ============================================================
# Standalone CLI
# ============================================================
def _cli():
    import argparse

    p = argparse.ArgumentParser(description="oiagent-team-lead CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_d = sub.add_parser("dispatch", help="查 routing.yaml 派单")
    p_d.add_argument("task", help="任务描述")

    p_r = sub.add_parser("race", help="Race 模式并发多模型")
    p_r.add_argument("prompt", help="prompt")
    p_r.add_argument("--models", nargs="+", help="模型列表(可选)")
    p_r.add_argument("--timeout", type=int, default=60, help="单模型超时秒")

    p_t = sub.add_parser("trace", help="写 trace")
    p_t.add_argument("event", help="事件名")
    p_t.add_argument("--payload", required=True, help="JSON 字符串")

    args = p.parse_args()

    if args.cmd == "dispatch":
        print(dispatch_impl(args.task))
    elif args.cmd == "race":
        print(race_impl(args.prompt, models=args.models, timeout_s=args.timeout))
    elif args.cmd == "trace":
        payload = json.loads(args.payload)
        print(trace_impl(args.event, payload))


if __name__ == "__main__":
    _cli()

# =============================================================================
# v0.48 — 团队协作 Agent 经验保存
# =============================================================================
def _save_team_experience_to_obsidian(event: str, payload: dict) -> None:
    """保存团队协作经验到 Obsidian。

    触发条件:
      1. dispatch 事件 + agent_config 存在
      2. race 事件 + winner 存在
      3. trace 事件 (手动触发)
    """
    try:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d")
        ts_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        # 提取关键信息
        agent = payload.get("agent", "unknown")
        pool = payload.get("pool", "unknown")
        task = payload.get("task", "")
        intent = payload.get("intent", "")
        matched_kw = payload.get("matched_keyword", "")

        # 构建标题
        title = f"OIagent 团队协作经验 ({now_str})"
        if intent:
            title = f"OIagent {intent} 经验 ({now_str})"

        # 提取核心经验
        tl_dr_parts = []
        core_exp = []
        gotchas = []

        # 从 payload 中提取经验
        if agent_config := payload.get("agent_config"):
            role = agent_config.get("role", "")
            goal = agent_config.get("goal", "")
            backstory = agent_config.get("backstory", "")
            if role:
                tl_dr_parts.append(f"角色: {role}")
            if goal:
                core_exp.append(f"目标: {goal[:80]}")
            if backstory:
                core_exp.append(f"背景: {backstory[:80]}")

        # 从 task 中提取关键词
        if task:
            keywords = [kw for kw in ["总结", "经验", "教训", "踩坑", "结论"] if kw in task]
            if keywords:
                tl_dr_parts.extend(keywords)

        # 构建 frontmatter
        tags = ["经验", "团队协作", agent]
        if gotchas:
            tags.append("踩坑")

        frontmatter = (
            "---\n"
            f"title: {title}\n"
            f"domain: OIagent/团队协作\n"
            f"date: '{now_str}'\n"
            f"created_at: '{ts_str}'\n"
            "tags:\n"
            "  - 经验\n"
            + "\n".join(f"  - {t}" for t in tags) + "\n"
            "status: 已存档\n"
            "source_skill: team-lead-experience\n"
            "related: [[note-to-obsidian]]\n"
            "---\n"
        )

        # 构建正文
        body_lines = [
            f"# {title}", "",
            "## TL;DR",
            *(f"- {p}" for p in tl_dr_parts[:5]), "",
            "## 核心经验",
            *core_exp[:8], "",
            "## 配置信息",
            f"- Agent: {agent}",
            f"- Pool: {pool}",
            f"- Intent: {intent}",
            *(f"- 匹配关键词: {matched_kw}" if matched_kw else ""),
            "",
            "## 关联",
            "- [[note-to-obsidian]]",
            "- [[mcp_oiagent_routing]]",
            "",
            "## 原始事件",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2)[:2000],
            "```", "",
        ]

        # 写入文件
        import re as _re
        safe_title = _re.sub(r'[<>:"/\|?*]', "", title)
        safe_title = _re.sub(r"\s+", " ", safe_title).strip()
        filename = f"{safe_title}.md"
        filepath = OBSIDIAN_EXPERIENCES_DIR / filename

        if filepath.exists():
            base, ext = os.path.splitext(filename)
            filename = f"{base}_{datetime.now().strftime('%H%M%S')}{ext}"
            filepath = OBSIDIAN_EXPERIENCES_DIR / filename

        filepath.write_text(frontmatter + "\n".join(body_lines), encoding="utf-8")
        print(f"[team-lead] Experience saved: {filepath} (agent={agent}, event={event})")

    except Exception as e:
        print(f"[team-lead] Experience save failed (non-fatal): {type(e).__name__}: {e}")
