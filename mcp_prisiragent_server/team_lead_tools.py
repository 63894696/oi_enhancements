"""prisiragent-team-lead 派单插件 (v0.1, 2026-07-23)

挂在 mcp_prisiragent_server actor 下,提供 3 个工具:
- dispatch: 按任务描述查 routing.yaml → 返回 {agent, pool, mode}
- race: 同 prompt 并发多模型,先返回的进 trace
- trace: 写入派单决策到已有 trace 机制(jsonl append-only)

设计原则:
- 0-token 决策:dispatch 走关键词匹配,不调 LLM
- 模型池引用:model 字段只引用 pool 名,不写死 provider/model
- 默认 cheap_lottery (Race 模式抽奖层),失败再升级

复用:
- ~/.claude/mcp_prisiragent_routing.yaml  (routing 表)
- ~/.claude/prisiragent_harness_training/  (trace 输出目录)
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
ROUTING_PATH = Path.home() / ".claude" / "mcp_prisiragent_routing.yaml"
TRACE_DIR = Path.home() / ".claude" / "prisiragent_harness_training"
# v0.48 Prisiragent 团队协作经验保存
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
    """读 routing.yaml —— 用 PyYAML(全局已装 6.0.3,无新增依赖)

    注:文件不存在时返空 dict,让 caller 走 laya 兜底(M3.76 设计)
    """
    if not ROUTING_PATH.exists():
        return {"rules": [], "model_pool": {}, "version": None,
                "_missing": True, "_path": str(ROUTING_PATH)}

    with ROUTING_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return {"rules": [], "model_pool": {}, "version": None,
                    "_corrupt": True}
        data.setdefault("rules", [])
        data.setdefault("model_pool", {})
        return data


# ── laya 兜底层(M3.76 新增) ──────────────────────────
_LAYA_OK = False           # 启动时探测一次 laya 是否可用
_LAYA_ROUTER = None
_LAYA_ROUTER_QS = None
_LAYA_ROUTER_LOADED = False


def _probe_laya() -> None:
    """启动时探测 laya 是否可用。设置 _LAYA_OK。失败不抛异常。"""
    global _LAYA_OK
    try:
        import laya  # noqa: F401
        _LAYA_OK = True
    except Exception:
        _LAYA_OK = False


def _ensure_laya_router():
    """懒加载 laya Router + router_questions preset(M3.73 同款)。
    失败返回 (None, None),不抛异常。

    设计:首次调用时若 _LAYA_OK 未探测,先调 _probe_laya()。
    这样直接调 _laya_route_to_prisir() 的外部调用也能正常用。
    """
    global _LAYA_ROUTER, _LAYA_ROUTER_QS, _LAYA_ROUTER_LOADED
    if not _LAYA_OK and not _LAYA_ROUTER_LOADED:
        # 首次进入,先探测一次
        _probe_laya()
    if not _LAYA_OK:
        return None, None
    if _LAYA_ROUTER_LOADED:
        return _LAYA_ROUTER, _LAYA_ROUTER_QS
    try:
        from laya import Router as _LR, router_questions as _rq
        _LAYA_ROUTER = _LR(default="multilingual", preload=True)
        _LAYA_ROUTER_QS = _rq()
    except Exception as e:  # noqa: BLE001
        print(f"[team_lead] laya 加载失败: {e}", file=sys.stderr)
        _LAYA_ROUTER = None
        _LAYA_ROUTER_QS = None
    _LAYA_ROUTER_LOADED = True
    return _LAYA_ROUTER, _LAYA_ROUTER_QS


# laya 4 维 (domain/difficulty/needs_tools/is_sensitive) → PrisirAI intent 翻译表
# 与 M3.73 intent_router 同思路,但目标从 (intent, task) 变成 PrisirAI 15 intent
_LAYA_INTENT_MAP = {
    # domain=code → 在路由 yaml 内进一步用关键词细分
    "code_python":        ("python_review",     ["python", "异步", "async", "await", "协程", "依赖", "pip"]),
    "code_security":      ("security",          ["secret", "auth", "注入", "xss", "csrf", "密码", "token", "vulnerability", "ssl"]),
    "code_architecture":  ("architecture",      ["架构", "architecture", "蓝图", "选型", "微服务", "gateway"]),
    "code_llm_integration": ("llm_integration", ["llm", "vendor", "streaming", "prompt", "mcp_oiagent", "rag"]),
    "code_review":        ("code_review",       ["审查", "review", "找 bug", "找bug", "bug", "漏洞"]),
    "code_harness":       ("harness_code_review", ["harness", "cursor harness", "l4 模式", "pattern guided"]),
    "code_implement":     ("code_implement",    None),  # 默认 fallback
    # domain=writing
    "plan":               ("plan",              ["计划", "plan", "路线图", "步骤", "拆解", "阶段"]),
    "content":            ("content",           ["tts", "语音", "字幕", "视频", "会议", "transcript", "dubbing"]),
    # domain=factual_lookup
    "search":             ("search",            ["论文", "文献", "paper", "citation", "学术", "sciverse"]),
    "explore_text":       ("explore",           ["搜", "找", "where", "列", "看看", "在哪"]),
    # domain=data_analysis
    "process_control":    ("process_control",   ["进程", "cpu", "kill", "调度", "watchdog", "probalance", "ananicy", "processlasso"]),
    # domain=other / general_knowledge
    "vision":             ("vision",            ["图片", "截图", "visual", "image", "ocr", "看图", "识别"]),
    "default":            ("default",           None),
}

# 强制 override(M3.73 OPS_HINTS 同款,避免 laya 把"清理 D 盘"误判 code)
# 在 laya 分流之前先扫一遍,命中关键词 → 直接 intent(不再走 laya 分流)
# 词典顺序:覆盖 > 窄,优先 process_control(运维);其它走 plan/search/security
_OPS_FORCE_OVERRIDE = {
    "process_control": [
        "进程", "kill ", "kill-", "kill进程",
        "watchdog", "调度", "probalance", "ananicy", "processlasso",
        "清理", "盘", "垃圾", "卸载", "删除文件", "磁盘",
        "rm ", "rm-", "del ", "del-",
        "性能", "监控", "cpu", "内存", "端口",
        "registry", "注册表", "防火墙", "网络", "开机", "蓝屏", "重启", "关机",
        "备份", "还原", "服务",
    ],
    "plan":            ["计划", "plan", "步骤", "拆解", "阶段", "路线图"],
    "search":          ["论文", "sciverse", "文献", "paper", "citation", "学术"],
    "security":        ["漏洞", "注入", "vulnerability", "secret", "auth", "xss", "csrf"],
}


def _laya_route_to_prisir(task_text: str, laya_floor: float = 0.60) -> dict:
    """laya router_questions() 4 维 → PrisirAI intent。

    返回 dict,见下方 schema。注意:**不抛异常**,失败返 available=True 但 intent=None。
    """
    base = {
        "intent": None, "conf": 0.0, "latency_ms": 0.0,
        "domain": "", "needs_tools": 0.0, "is_sensitive": 0.0,
        "difficulty": 0.0, "available": False, "matched_via": None,
        "error": None,
    }
    router, qs = _ensure_laya_router()
    if router is None:
        return {**base, "error": "no_laya"}
    t0 = time.time()
    try:
        state = {"request": (task_text or "")[:800]}
        res = router.predict(state, qs)
        elapsed_ms = (time.time() - t0) * 1000
        answers = res.get("answers") or {}
        if not answers:
            return {**base, "available": True, "error": "empty",
                    "latency_ms": elapsed_ms}
        domain = (answers.get("domain", {}).get("choice") or "").strip()
        domain_probs = answers.get("domain", {}).get("probabilities") or {}
        conf = answers.get("domain", {}).get("answer_confidence", 0.0) or 0.0
        if not conf and domain_probs:
            conf = max(domain_probs.values()) if domain_probs else 0.0
        diff = answers.get("difficulty", {}).get("score", 0.0) or 0.0
        needs_tools = answers.get("needs_tools", {}).get("noul", 0.0) or 0.0
        is_sensitive = answers.get("is_sensitive", {}).get("noul", 0.0) or 0.0

        text_lower = (task_text or "").lower()

        # 阶段 A:强制 override(laya 误判救援)
        for intent, kws in _OPS_FORCE_OVERRIDE.items():
            if any(kw in text_lower for kw in kws):
                return {
                    **base, "intent": intent, "conf": 1.0,
                    "domain": domain, "difficulty": diff,
                    "needs_tools": needs_tools, "is_sensitive": is_sensitive,
                    "available": True, "matched_via": "override",
                    "latency_ms": elapsed_ms,
                }

        # 阶段 B:laya domain 分流
        if domain == "code":
            for intent_key in ("code_python", "code_security", "code_architecture",
                               "code_llm_integration", "code_harness", "code_review"):
                intent_name, kws = _LAYA_INTENT_MAP[intent_key]
                if kws and any(kw in text_lower for kw in kws):
                    return {**base, "intent": intent_name, "conf": conf,
                            "domain": domain, "difficulty": diff,
                            "needs_tools": needs_tools, "is_sensitive": is_sensitive,
                            "available": True, "matched_via": "laya_code",
                            "latency_ms": elapsed_ms}
            return {**base, "intent": "code_implement", "conf": conf,
                    "domain": domain, "difficulty": diff,
                    "needs_tools": needs_tools, "is_sensitive": is_sensitive,
                    "available": True, "matched_via": "laya_code_default",
                    "latency_ms": elapsed_ms}

        elif domain == "writing":
            for intent_key in ("plan", "content"):
                intent_name, kws = _LAYA_INTENT_MAP[intent_key]
                if kws and any(kw in text_lower for kw in kws):
                    return {**base, "intent": intent_name, "conf": conf,
                            "domain": domain, "difficulty": diff,
                            "needs_tools": needs_tools, "is_sensitive": is_sensitive,
                            "available": True, "matched_via": "laya_writing",
                            "latency_ms": elapsed_ms}
            return {**base, "intent": "plan", "conf": conf,
                    "domain": domain, "difficulty": diff,
                    "needs_tools": needs_tools, "is_sensitive": is_sensitive,
                    "available": True, "matched_via": "laya_writing_default",
                    "latency_ms": elapsed_ms}

        elif domain == "factual_lookup":
            intent_name, kws = _LAYA_INTENT_MAP["search"]
            if kws and any(kw in text_lower for kw in kws):
                return {**base, "intent": "search", "conf": conf,
                        "domain": domain, "difficulty": diff,
                        "needs_tools": needs_tools, "is_sensitive": is_sensitive,
                        "available": True, "matched_via": "laya_factual",
                        "latency_ms": elapsed_ms}
            return {**base, "intent": "explore", "conf": conf,
                    "domain": domain, "difficulty": diff,
                    "needs_tools": needs_tools, "is_sensitive": is_sensitive,
                    "available": True, "matched_via": "laya_factual_default",
                    "latency_ms": elapsed_ms}

        elif domain == "data_analysis":
            intent_name, kws = _LAYA_INTENT_MAP["process_control"]
            if kws and any(kw in text_lower for kw in kws):
                return {**base, "intent": "process_control", "conf": conf,
                        "domain": domain, "difficulty": diff,
                        "needs_tools": needs_tools, "is_sensitive": is_sensitive,
                        "available": True, "matched_via": "laya_data",
                        "latency_ms": elapsed_ms}
            return {**base, "intent": "explore", "conf": conf,
                    "domain": domain, "difficulty": diff,
                    "needs_tools": needs_tools, "is_sensitive": is_sensitive,
                    "available": True, "matched_via": "laya_data_default",
                    "latency_ms": elapsed_ms}

        elif domain in ("other", "general_knowledge"):
            intent_name, kws = _LAYA_INTENT_MAP["vision"]
            if kws and any(kw in text_lower for kw in kws):
                return {**base, "intent": "vision", "conf": conf,
                        "domain": domain, "difficulty": diff,
                        "needs_tools": needs_tools, "is_sensitive": is_sensitive,
                        "available": True, "matched_via": "laya_vision",
                        "latency_ms": elapsed_ms}

        # 兜底:不识别 / 置信度太低
        if conf < laya_floor:
            return {**base, "conf": conf, "domain": domain, "difficulty": diff,
                    "needs_tools": needs_tools, "is_sensitive": is_sensitive,
                    "available": True, "error": f"conf<{laya_floor}",
                    "latency_ms": elapsed_ms}
        return {**base, "intent": "default", "conf": conf,
                "domain": domain, "difficulty": diff,
                "needs_tools": needs_tools, "is_sensitive": is_sensitive,
                "available": True, "matched_via": "laya_default",
                "latency_ms": elapsed_ms}

    except Exception as e:  # noqa: BLE001
        return {**base, "available": True, "error": str(e)[:200],
                "latency_ms": (time.time() - t0) * 1000}


# ============================================================
# M3.84 二次分流换 head(laya 优先 + 关键词 fallback)
# ============================================================
# M3.82 关键教训:laya zero-shot 在结构化多类细分上 0% ACC,
# 但 laya.guard_questions()(参考 M3.72 safe_exec)对部分 case 仍
# 有 signal(M3.76 dispatch 真推理 0.87 conf on Python 任务)。
#
# 设计:**laya fast-path + 关键词 fallback race**
#   1. laya 真推理(~500ms)先出 head 结果
#   2. conf ≥ 0.7 且**不命中** _OPS_FORCE_OVERRIDE  → 采纳 laya head
#   3. conf < 0.7 OR 命中 _OPS_FORCE_OVERRIDE      → 走旧关键词路径
#   4. 旧路径不动(_LAYA_INTENT_MAP / 阶段 B 各分支完整保留)
#
# 数据生成:data_prep_dispatch_route.py(800 train + 200 eval,8 类均衡)
# 决策依据:见 M3.84 memory(laya head ACC < 60% 触发 hybrid fallback)

# 8 类意图字符串(与 data_prep_dispatch_route.py 对齐)
_INTENT_VIA_HEAD_8 = {
    "chat", "code", "search", "tool_call",
    "roleplay", "plan", "security", "ops",
}

# laya head 翻译表(8 类 → PrisirAI 15 intent,routing.yaml 对齐)
_INTENT_HEAD_TO_PRISIR = {
    "chat":       "default",       # 闲聊 → Explore default
    "code":       "code_implement",
    "search":     "search",
    "tool_call":  "default",       # 工具调用 → 默认动作
    "roleplay":   "content",
    "plan":       "plan",
    "security":   "security",
    "ops":        "process_control",
}

# head conf 阈值(M3.82 laya conf < 0.7 必走 fallback)
_HEAD_CONF_THRESHOLD = 0.7


# M3.87 P1-1:以下函数已弃用 — M3.84 验证 laya head 46% ACC < 50% 不如掷骰子。
# dispatch_impl 不再调用它,保留函数体以便未来 GPU 环境训出 ≥80% ACC head 时启用。
def _laya_route_to_prisir_head(task_text: str, laya_floor: float = 0.60) -> dict:  # noqa: F841
    """M3.84 二次分流换 head:用 laya router 推断 8 类意图 → PrisirAI intent。

    失败/低 conf/无 laya → 返回 head_result=None(由 caller 走关键词 fallback)。

    设计要点:
      - **不动 _LAYA_INTENT_MAP / _OPS_FORCE_OVERRIDE**(M3.73 防误判保留)
      - 用 laya.router_questions() 4 维(domain/difficulty/needs_tools/is_sensitive)
        启发式映射到 8 类:
          * domain=code + has tool_call 信号 → code
          * domain=writing + plan 关键词 → plan
          * domain=data_analysis → ops
          * domain=factual_lookup → search
          * 其他靠 needs_tools + is_sensitive + difficulty 推断
      - 命中 _OPS_FORCE_OVERRIDE 任一关键词 → head_result=None
        (因为 override 是已知误判救援,不让 head 覆盖)
      - conf < laya_floor → head_result=None

    返回 dict schema(与 _laya_route_to_prisir 对齐):
      {
        "intent": "code" | "ops" | None,
        "conf": 0.0-1.0,
        "head_label": "code" | None,
        "domain": "...", "needs_tools": ..., "is_sensitive": ..., "difficulty": ...,
        "available": True/False,
        "matched_via": "head" | None,
        "error": "..." | None,
        "latency_ms": float,
      }
    """
    base = {
        "intent": None, "conf": 0.0, "head_label": None,
        "domain": "", "needs_tools": 0.0, "is_sensitive": 0.0,
        "difficulty": 0.0, "available": False, "matched_via": None,
        "error": None, "latency_ms": 0.0,
    }
    router, qs = _ensure_laya_router()
    if router is None:
        return {**base, "error": "no_laya"}
    text = (task_text or "").strip()
    if not text:
        return {**base, "error": "empty_text"}
    text_lower = text.lower()
    t0 = time.time()
    try:
        state = {"request": text[:800]}
        res = router.predict(state, qs)
        elapsed_ms = (time.time() - t0) * 1000
        answers = res.get("answers") or {}
        if not answers:
            return {**base, "available": True, "error": "empty_answers",
                    "latency_ms": elapsed_ms}
        domain = (answers.get("domain", {}).get("choice") or "").strip()
        domain_probs = answers.get("domain", {}).get("probabilities") or {}
        conf = answers.get("domain", {}).get("answer_confidence", 0.0) or 0.0
        if not conf and domain_probs:
            conf = max(domain_probs.values()) if domain_probs else 0.0
        diff = answers.get("difficulty", {}).get("score", 0.0) or 0.0
        needs_tools = answers.get("needs_tools", {}).get("noul", 0.0) or 0.0
        is_sensitive = answers.get("is_sensitive", {}).get("noul", 0.0) or 0.0

        # ── Phase A:_OPS_FORCE_OVERRIDE 优先(M3.73 防误判)──
        # 任何 override 关键词命中 → head 不参与,直接 fallback
        for intent_name, kws in _OPS_FORCE_OVERRIDE.items():
            if any(kw.lower() in text_lower for kw in kws):
                return {
                    **base,
                    "available": True,
                    "matched_via": "override_skip_head",
                    "domain": domain, "conf": conf,
                    "difficulty": diff, "needs_tools": needs_tools,
                    "is_sensitive": is_sensitive,
                    "latency_ms": elapsed_ms,
                }

        # ── Phase B:laya 4 维 → 8 类意图启发式 ──
        head_label = _map_laya_to_head_label(
            text_lower, domain, diff, needs_tools, is_sensitive
        )
        if head_label is None:
            return {
                **base, "available": True,
                "domain": domain, "conf": conf,
                "difficulty": diff, "needs_tools": needs_tools,
                "is_sensitive": is_sensitive,
                "matched_via": "head_no_decision",
                "latency_ms": elapsed_ms,
            }

        # ── Phase C:conf 阈值 ──
        # 注意:domain_probs max 是 0~1 的概率(M3.76 实测 Python 任务 conf=0.87)
        if conf < laya_floor:
            return {
                **base, "available": True,
                "head_label": head_label, "domain": domain, "conf": conf,
                "difficulty": diff, "needs_tools": needs_tools,
                "is_sensitive": is_sensitive,
                "matched_via": "head_low_conf",
                "latency_ms": elapsed_ms,
            }

        # head 决策 → PrisirAI intent
        prisir_intent = _INTENT_HEAD_TO_PRISIR.get(head_label, "default")
        return {
            **base,
            "intent": prisir_intent,
            "conf": conf,
            "head_label": head_label,
            "domain": domain,
            "difficulty": diff,
            "needs_tools": needs_tools,
            "is_sensitive": is_sensitive,
            "available": True,
            "matched_via": "head",
            "latency_ms": elapsed_ms,
        }

    except Exception as e:  # noqa: BLE001
        return {
            **base, "available": True, "error": str(e)[:200],
            "latency_ms": (time.time() - t0) * 1000,
        }


def _map_laya_to_head_label(
    text_lower: str, domain: str, difficulty: float,
    needs_tools: float, is_sensitive: float,
) -> str | None:
    """laya 4 维信号 + 文本 keyword → 8 类意图 label。

    启发式(参考 M3.71 laya 弱信号 + M3.50 intents 关键词):
      - domain=code + Python/review/bug/debug 类词 → code
      - domain=code + 无 keyword + needs_tools 高 → code
      - domain=writing + plan/checklist/roadmap 类词 → plan
      - domain=writing + needs_tools 高 → roleplay 兜底
      - domain=data_analysis → ops
      - domain=factual_lookup + 学术/paper → search
      - domain=factual_lookup + how/why/recommend → search
      - domain=other + needs_tools 高 → tool_call
      - domain=other + is_sensitive 高 → security
      - domain=other + 闲聊词 → chat
      - 其它 → None(让 fallback 决策)
    """
    # 关键词辅助(laya domain 失真时还能 fallback 到文本)
    has = lambda kws: any(kw.lower() in text_lower for kw in kws)

    # ── ops 优先(M3.73 防 laya 把 "清理 D 盘" 误判 code)──
    if has(_HEAD_OPS_KW):
        return "ops"

    # ── security ──
    if has(_HEAD_SECURITY_KW):
        return "security"

    # ── plan ──
    if has(_HEAD_PLAN_KW):
        return "plan"

    # ── roleplay ──
    if has(_HEAD_ROLEPLAY_KW):
        return "roleplay"

    # ── tool_call ──
    if has(_HEAD_TOOL_CALL_KW):
        return "tool_call"

    # ── chat ──
    if has(_HEAD_CHAT_KW):
        return "chat"

    # ── search / code 走 domain 判断 ──
    if domain == "code":
        # 即使没关键词,只要是 code 域就归 code(M3.76 实测 Python 任务 conf=0.87)
        return "code"
    if domain == "factual_lookup":
        return "search"
    if domain == "writing":
        # 写但非 plan/roleplay 关键词 → 默认 plan
        return "plan"
    if domain == "data_analysis":
        return "ops"

    # ── other / general_knowledge ──
    # 靠 needs_tools + is_sensitive + 难度区分
    if is_sensitive >= 0.5:
        return "security"
    if needs_tools >= 0.5:
        return "tool_call"
    if difficulty >= 2.0:
        return "search"  # 难问题偏 search

    # 实在分不出来 → None,让 caller 走关键词 fallback
    return None


# head 路径精简关键词(与 _OPS_FORCE_OVERRIDE 同源,但 head 用的是
# 8 类精简版,避免重复扫描整个 M3.50/M3.73 词典)
_HEAD_OPS_KW = [
    "进程", "kill ", "kill-", "watchdog", "调度",
    "清理", "磁盘", "垃圾", "卸载", "rm ", "del ",
    "性能", "监控", "cpu", "内存", "端口", "蓝屏", "重启",
    "注册表", "防火墙", "开机", "服务",
]
_HEAD_SECURITY_KW = [
    "漏洞", "注入", "vulnerability", "secret", "auth", "xss", "csrf",
    "密码", "token", "ssl", "审计", "权限", "威胁", "渗透",
    "rbac", "encrypt", "decrypt", "pentest", "ctf",
]
_HEAD_PLAN_KW = [
    "计划", "plan", "路线图", "步骤", "拆解", "阶段",
    "roadmap", "规划", "方案", "sprint", "里程碑", "milestone",
    "checklist", "todo", "排期", "策略",
]
_HEAD_ROLEPLAY_KW = [
    "扮演", "假装", "讲个", "讲故事", "继续讲", "演一段",
    "模仿", "当军师", "角色扮演", "面试官",
]
_HEAD_TOOL_CALL_KW = [
    "帮我打开", "帮我关闭", "帮我重启", "帮我删除",
    "帮我截图", "帮我设置", "帮我调到", "帮我发",
    "重启电脑", "截图当前屏幕", "把音量调到",
    "发邮件", "调亮度", "调音量",
]
_HEAD_CHAT_KW = [
    "你好", "今天", "周末", "心情", "哈哈", "真逗", "陪",
    "聊聊", "想你", "喜欢", "叫啥", "叫什么",
    "想你了", "最近", "过得", "辛苦", "开心", "难过",
    "早安", "晚安", "吃饭", "睡觉",
]


# ── dispatch ─────────────────────────────────────────
def _match_rule(rules: list, task_text: str, laya_floor: float = 0.60,
                no_laya: bool = False, laya_result: dict | None = None) -> dict:
    """regex fast-path + laya 兜底(M3.76)。

    三阶段:
      1. regex 匹配:命中 routing.yaml keywords → matched_via=regex
      2. laya 兜底:regex 未命中 + laya 可用 + conf >= laya_floor → matched_via=laya*
      3. 默认 fallback:yaml default rule 或 Explore 兜底 → matched_via=default

    返回 dict 含 {agent, pool, mode, intent, matched_via, laya_result, task, ...}
    """
    task_lower = (task_text or "").lower()
    default_rule = None
    rules_by_intent: dict[str, dict] = {}

    # 第一遍扫描:收集 default rule + intent→rule 索引
    for rule in rules:
        if "default" in rule:
            default_rule = rule
            continue
        match = rule.get("match", {})
        intent_name = match.get("intent")
        if intent_name and intent_name not in rules_by_intent:
            rules_by_intent[intent_name] = rule

    # 阶段 1:regex 匹配(命中第一条即返回)
    for rule in rules:
        if "default" in rule:
            continue
        match = rule.get("match", {})
        for kw in match.get("keywords", []):
            if kw.lower() in task_lower:
                return {
                    "agent": rule.get("agent"),
                    "pool": rule.get("pool"),
                    "mode": rule.get("mode"),
                    "intent": match.get("intent"),
                    "matched_keyword": kw,
                    "matched_via": "regex",
                    "rule_hit": True,
                    "agent_config": rule.get("agent_config"),
                    "extra": rule.get("extra"),
                    "laya_result": None,
                    "task": task_text,
                }

    # 阶段 2:laya 兜底(regex 没命中,且 laya 可用)
    if not no_laya and laya_result is not None:
        lr_intent = laya_result.get("intent")
        if lr_intent and lr_intent != "default" and lr_intent in rules_by_intent:
            rule = rules_by_intent[lr_intent]
            return {
                "agent": rule.get("agent"),
                "pool": rule.get("pool"),
                "mode": rule.get("mode"),
                "intent": lr_intent,
                "matched_keyword": None,
                "matched_via": laya_result.get("matched_via") or "laya",
                "rule_hit": False,
                "agent_config": rule.get("agent_config"),
                "extra": rule.get("extra"),
                "laya_result": laya_result,
                "task": task_text,
            }

    # 阶段 3:默认 fallback(yaml default rule 或 Explore 兜底)
    if default_rule is not None:
        return {
            "agent": default_rule["default"].get("agent"),
            "pool": default_rule["default"].get("pool"),
            "mode": default_rule["default"].get("mode"),
            "intent": "default",
            "matched_keyword": None,
            "matched_via": "default",
            "rule_hit": False,
            "agent_config": default_rule["default"].get("agent_config"),
            "extra": default_rule["default"].get("extra"),
            "laya_result": laya_result,
            "task": task_text,
        }
    # yaml 完全没有 default rule → 硬兜底 Explore
    return {
        "agent": "Explore",
        "pool": "cheap_lottery",
        "mode": "engineer",
        "intent": "default",
        "matched_keyword": None,
        "matched_via": "default",
        "rule_hit": False,
        "agent_config": None,
        "extra": None,
        "laya_result": laya_result,
        "task": task_text,
    }


def dispatch_impl(task: str, laya_floor: float = 0.60,
                  no_laya: bool = False) -> str:
    """按任务描述查 routing.yaml,返回派单决策(agent / pool / mode)

    M3.76 升级:
      - 新增 laya 兜底:regex 没命中 → laya router 4 维 → PrisirAI intent
      - 新增参数 laya_floor / no_laya 透传给 _match_rule
      - 保留 B1 降级语义(失败 → Explore)

    返回 JSON 字符串,字段:
      ok, agent, pool, mode, intent, matched_via, matched_keyword,
      rule_hit, agent_config, extra, laya_result, task, pool_models,
      routing_version, laya_floor, no_laya, error(可选)
    """
    try:
        if not task or not task.strip():
            raise ValueError("task 不能为空")

        # 探测 laya 可用性(首次,O(1),不阻塞)
        if not no_laya and not _LAYA_ROUTER_LOADED:
            _probe_laya()

        routing = _load_routing()
        rules = routing.get("rules", [])
        pools = routing.get("model_pool", {})

        # 阶段 1.5:laya 推理(在 regex 之前并行无意义,顺序即可)
        # regex 命中概率高 → 先 regex 后 laya,避免不必要推理
        laya_result = None
        if not no_laya:
            # M3.87 P1-1:去掉 M3.84 laya head 二次分流(46% ACC < 50% 不如掷骰子)
            # 直接走旧 _laya_route_to_prisir (regex + laya router 4 维)
            laya_result = _laya_route_to_prisir(task, laya_floor)
            # 留 head_result=None 字段以保持输出 schema 向后兼容
            if isinstance(laya_result, dict):
                laya_result["head_result"] = None

        decision = _match_rule(rules, task, laya_floor=laya_floor,
                               no_laya=no_laya, laya_result=laya_result)
        decision["task"] = task
        decision["pool_models"] = pools.get(decision.get("pool"), [])
        decision["routing_version"] = routing.get("version")
        decision["laya_floor"] = laya_floor
        decision["no_laya"] = no_laya
        if routing.get("_missing"):
            decision["routing_missing"] = True

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
            "matched_via": "default",
            "rule_hit": False,
            "agent_config": None,
            "extra": None,
            "task": task,
            "pool_models": [],
            "routing_version": None,
            "laya_floor": laya_floor,
            "no_laya": no_laya,
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
        # K3 = Moonshot Kimi(K3 是它在 prisiragent 里的命名)
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
                    "max_tokens": 4096,
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
                    if not content:
                        for ch in data.get("choices", [{}]):
                            content = ch.get("message", {}).get("content", "")
                            if content:
                                break
                    return {"ok": True, "result": content, "raw": data}

            elif protocol == "anthropic":
                url = f"{base_url}/messages"
                body = json.dumps({
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 4096,
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
            out["result"] = result.get("result") or ""
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
    """并发跑多个模型,先返回的赢

    空结果剔除 + 次优补位:
      - winner 必须是 ok=True 且 result 非空
      - 每次一批完成时先看这批里有没有合格结果;有则取首个合格为 winner,其余 pending 取消
      - 该批全空则继续等下一批(次优补位),不提前取消
      - 全部跑完仍无合格 → winner=None(附带全部结果供诊断)
    """
    sem = asyncio.Semaphore(concurrency)

    def _valid(r: dict) -> bool:
        return bool(r and r.get("ok") and (r.get("result") or "").strip())

    async def gated(model):
        async with sem:
            return await _race_one(model, prompt, timeout_s)

    tasks = [asyncio.create_task(gated(m)) for m in models]
    pending = set(tasks)
    done_results: list[dict] = []

    winner: dict | None = None
    while pending and winner is None:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            try:
                r = t.result()
            except asyncio.CancelledError:
                r = {"model": "?", "ok": False, "error": "cancelled", "elapsed_ms": 0}
            except Exception as e:  # 防御:task 内部意外抛错不拖垮整个 race
                r = {"model": "?", "ok": False, "error": f"task raised: {e}", "elapsed_ms": 0}
            done_results.append(r)
            if winner is None and _valid(r):
                winner = r
        # 本批无合格 → 继续等 pending(次优补位)

    for t in pending:
        t.cancel()

    return {
        "winner": winner,
        "losers_count": len(done_results) - (1 if winner else 0),
        "total_models": len(models),
        "all_results": done_results,
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
        f"[prisiragent trace] event={event} intent={intent} agent={agent} pool={pool} "
        f"task='{task[:80]}' {extra_s}"
    )


def _ingest_trace_async(record: dict) -> None:
    """后台跑 cognee.remember,失败 fallback

    设计:不抛异常,不阻塞主流程。失败时:
    1. cognee 真实调通(2026-09-25 验证:cognee 1.2.2 在 Python 3.12.9 上 import + run 都 OK)
    2. 同时写 cognee-ready jsonl 作 future batch ingest 备份
    3. 任意一路失败 → stderr 警告(不静默吞),不影响主 trace jsonl
    """
    import threading

    def _do():
        # ── 双轨:既调 cognee,也写 cognee-ready jsonl,任一失败不影响另一个 ──

        # 1. 调 cognee(M3.86 验证可工作,改静默为显式 warn)
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from cognee_tools import cognee_remember_impl
            text = _format_trace_for_cognee(record)
            r = cognee_remember_impl(
                data=text,
                dataset_name="prisiragent_harness_training",
            )
            if '"ok": true' not in r and '"ok":true' not in r:
                print(f"[trace-ingest] cognee ingest 非 ok 返回: {r[:200]}", file=sys.stderr)
        except Exception as e:
            # M3.86 修复:不再静默吞,真实失败要可见
            print(f"[trace-ingest] cognee ingest 异常(已降级到 cognee_ready jsonl): {type(e).__name__}: {e}", file=sys.stderr)

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
            "prisiragent-team-lead 派单:按任务描述查 ~/.claude/mcp_prisiragent_routing.yaml,"
            "返回 {agent, pool, mode, matched_keyword},0-token 关键词匹配,"
            "同时写一条 trace 到 ~/.claude/prisiragent_harness_training/<date>.jsonl"
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
            "手动写一条 trace 到 ~/.claude/prisiragent_harness_training/<date>.jsonl。"
            "供外部 hook / skill 调用,作为 prisiragent 反推 IDE-无关 harness 的训练数据。"
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

    p = argparse.ArgumentParser(description="prisiragent-team-lead CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_d = sub.add_parser("dispatch", help="查 routing.yaml 派单 (M3.76 laya 兜底)")
    p_d.add_argument("task", help="任务描述")
    p_d.add_argument("--no-laya", action="store_true",
                     help="禁用 laya 兜底,纯 regex dispatch")
    p_d.add_argument("--laya-floor", type=float, default=0.60,
                     help="laya domain 置信度阈值(默认 0.60)")

    p_r = sub.add_parser("race", help="Race 模式并发多模型")
    p_r.add_argument("prompt", help="prompt")
    p_r.add_argument("--models", nargs="+", help="模型列表(可选)")
    p_r.add_argument("--timeout", type=int, default=60, help="单模型超时秒")

    p_t = sub.add_parser("trace", help="写 trace")
    p_t.add_argument("event", help="事件名")
    p_t.add_argument("--payload", required=True, help="JSON 字符串")

    args = p.parse_args()

    # M3.76:CLI 启动时探测一次 laya(诊断信息)
    if args.cmd == "dispatch":
        if not args.no_laya:
            _probe_laya()
        print(f"[team_lead] laya available: {_LAYA_OK} "
              f"(routing.yaml missing: {not ROUTING_PATH.exists()})",
              file=sys.stderr)
        print(dispatch_impl(args.task,
                            laya_floor=args.laya_floor,
                            no_laya=args.no_laya))
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
        title = f"Prisiragent 团队协作经验 ({now_str})"
        if intent:
            title = f"Prisiragent {intent} 经验 ({now_str})"

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
            f"domain: Prisiragent/团队协作\n"
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
            "- [[mcp_prisiragent_routing]]",
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
