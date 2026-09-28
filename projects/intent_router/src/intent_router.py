#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# src/intent_router.py — M3.69 意图分发器核心(2026-09-24)
#
# 目的:
#   - 把 classify_intents + classify_task_local 联合,做意图+任务双 spec 路由
#   - 输出:route_spec / spec_action / 综合置信度 / 执行命令建议
#   - **零侵入**:只 import companion 下两个已有 wrapper,不修改任何 companion/ 代码
#   - **离线可用**:无网络,纯本地 0.6B 双 adapter 推理(延迟 ~5s/句)
#
# 设计:
#   - 路由映射表(7 条核心映射,见 _ROUTE_MAP):
#       tool_call + general  → disk_cleanup / tempfile / log / email(用户补 spec)
#       tool_call + fast     → perf (perf_monitor)
#       chat     + general   → companion_jev.chat
#       code     + code_call → IDE 工具(spec print, 不真执行)
#       code     + code_qa   → RAG / 搜索
#       search   + general  → web 搜索
#       search   + fast     → fast-path 命令查询
#   - 双 spec 各自输出 dist,intents_dist/task_dist 是按 conf 平展的 5/6 维向量
#   - 综合置信度 = intent_conf * task_conf(独立概率相乘)
#   - **不重训 LoRA**,复用现成 intents_conf + task_conf 两个 adapter
#
# 用法:
#   from intent_router import classify_and_route, route_map
#   out = classify_and_route("清理 D 盘垃圾")
#   out['route']         # {'intent': 'tool_call', 'task': 'general',
#                        #  'spec': 'disk_cleanup/tempfile/log/email',
#                        #  'action': '...', 'confidence': 0.764}
#   out['intents_dist']  # 5 类意图 softmax dist
#   out['task_dist']     # 6 类任务 softmax dist
"""
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# 关键:把 companion/ 加入 sys.path,不修改 companion 任何代码
_COMPANION_DIR = Path(
    "C:/Users/Administrator/oi_enhancements/companion").resolve()
if str(_COMPANION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPANION_DIR))

# 关键:把 laya_guard/ 加入 sys.path,复用 laya Router(M3.73 新增)
_LAYA_GUARD_DIR = Path(
    "C:/Users/Administrator/oi_enhancements/projects/laya_guard/src").resolve()
if str(_LAYA_GUARD_DIR) not in sys.path:
    sys.path.insert(0, str(_LAYA_GUARD_DIR))

# 复用现成 wrapper(零侵入)
from classify_intents import classify_intents, VALID_INTENTS  # noqa: E402
from classify_task_local import classify_task_local, VALID_TASKS  # noqa: E402

# laya router preset(M3.73)— lazy import(失败静默 fallback)
try:
    from laya import Router as _LayaRouter  # noqa: E402
    from laya import router_questions as _laya_router_questions  # noqa: E402
    _LAYA_OK = True
except ImportError as e:  # noqa: BLE001
    _LAYA_OK = False
    _LAYA_ERR = repr(e)

# laya router preset 单例 cache(M3.73)— 第一次调用时实例化,后续复用
_LAYA_ROUTER = None
_LAYA_ROUTER_QS = None
_LAYA_ROUTER_TRIED = False

# 路由映射表:7 条核心(intents_category, task_category) → recommended_spec
# 顺序敏感:更具体的在前
_ROUTE_MAP: list[tuple[tuple[str, str], dict]] = [
    # tool_call 系列
    (("tool_call", "general"),
     {"spec": "disk_cleanup/tempfile/log/email",
      "spec_short": "tool_call_general",
      "action": "python classify_disk_cleanup.py --path <path>",
      "executor": "companion_tool_call",
      "needs_user_spec": True,
      "description": "用户补 spec(dis/temp/log/email)后执行"}),
    (("tool_call", "fast"),
     {"spec": "perf",
      "spec_short": "tool_call_fast",
      "action": "python companion/bench_perf_local.py --realtime",
      "executor": "perf_guard",
      "needs_user_spec": False,
      "description": "触发本地 perf 守护(perf_monitor)"}),
    (("tool_call", "long"),
     {"spec": "perf_long",
      "spec_short": "tool_call_long",
      "action": "python companion/bench_perf_local.py --window 24h",
      "executor": "perf_guard_long",
      "needs_user_spec": False,
      "description": "长窗口 perf 趋势分析"}),
    # chat 系列
    (("chat", "general"),
     {"spec": "jev_chat",
      "spec_short": "chat_general",
      "action": "async companion_jev.ask_intent(user_text)",
      "executor": "companion_jev",
      "needs_user_spec": False,
      "description": "触发 companion_jev.chat 走在线 Jev(可降级本地)"}),
    (("chat", "fast"),
     {"spec": "jev_chat_fast",
      "spec_short": "chat_fast",
      "action": "async companion_jev.ask_intent(user_text, fastlane=True)",
      "executor": "companion_jev",
      "needs_user_spec": False,
      "description": "Jev fast-path(短问候/单轮,无需深推理)"}),
    (("chat", "creative"),
     {"spec": "jev_chat_creative",
      "spec_short": "chat_creative",
      "action": "async companion_jev.ask_intent(user_text, creative=True)",
      "executor": "companion_jev",
      "needs_user_spec": False,
      "description": "Jev creative 模式(写诗/故事/角色)"}),
    # code 系列
    (("code", "code_call"),
     {"spec": "ide_exec",
      "spec_short": "code_call",
      "action": "IDE 工具调用(本期只 print spec,不真执行)",
      "executor": "ide_tool",
      "needs_user_spec": False,
      "description": "触发 IDE 工具/执行代码片段"}),
    (("code", "code_qa"),
     {"spec": "rag_search",
      "spec_short": "code_qa",
      "action": "python rag_search.py --query <q>",
      "executor": "rag",
      "needs_user_spec": False,
      "description": "触发 RAG / 知识库搜索"}),
    (("code", "fast"),
     {"spec": "code_fast",
      "spec_short": "code_fast",
      "action": "code_helper --quick <q>",
      "executor": "code_helper",
      "needs_user_spec": False,
      "description": "代码快查(语法/API 速答)"}),
    # search 系列
    (("search", "general"),
     {"spec": "web_search",
      "spec_short": "search_general",
      "action": "web_search --query <q>",
      "executor": "web_search",
      "needs_user_spec": False,
      "description": "触发通用 web 搜索"}),
    (("search", "fast"),
     {"spec": "fast_path",
      "spec_short": "search_fast",
      "action": "fastlane_lookup <q>",
      "executor": "fastlane",
      "needs_user_spec": False,
      "description": "触发 fast-path 命令查询(本地缓存优先)"}),
    (("search", "code_qa"),
     {"spec": "rag_search",
      "spec_short": "search_code_qa",
      "action": "python rag_search.py --query <q>",
      "executor": "rag",
      "needs_user_spec": False,
      "description": "搜索类代码问答(RAG 优先)"}),
    (("search", "code_call"),
     {"spec": "ide_exec",
      "spec_short": "search_code_call",
      "action": "ide_tool --query <q>",
      "executor": "ide_tool",
      "needs_user_spec": False,
      "description": "搜索 + 触发现有 IDE 工具"}),
    # roleplay 系列
    (("roleplay", "general"),
     {"spec": "jev_roleplay",
      "spec_short": "roleplay",
      "action": "async companion_jev.ask_intent(user_text, roleplay=True)",
      "executor": "companion_jev",
      "needs_user_spec": False,
      "description": "Jev roleplay 模式"}),
    (("roleplay", "fast"),
     {"spec": "jev_roleplay_fast",
      "spec_short": "roleplay_fast",
      "action": "async companion_jev.ask_intent(user_text, roleplay=True, fastlane=True)",
      "executor": "companion_jev",
      "needs_user_spec": False,
      "description": "Jev roleplay fast-path"}),
    (("roleplay", "creative"),
     {"spec": "jev_roleplay_creative",
      "spec_short": "roleplay_creative",
      "action": "async companion_jev.ask_intent(user_text, roleplay=True, creative=True)",
      "executor": "companion_jev",
      "needs_user_spec": False,
      "description": "Jev roleplay creative 模式"}),
    # fallback: 任意 (x, general) → general 兜底
    (("__any__", "general"),
     {"spec": "general_fallback",
      "spec_short": "general",
      "action": "default_chat(user_text)",
      "executor": "default",
      "needs_user_spec": False,
      "description": "未匹配专属路由,默认聊天兜底"}),
]


def route_map() -> list[dict]:
    """返回完整路由表(调试/导出用)。

    Returns:
        list of {intent, task, spec, action, executor, needs_user_spec, description}
    """
    out = []
    for (intent, task), meta in _ROUTE_MAP:
        out.append({
            "intent": intent,
            "task": task,
            "spec": meta["spec"],
            "spec_short": meta["spec_short"],
            "action": meta["action"],
            "executor": meta["executor"],
            "needs_user_spec": meta["needs_user_spec"],
            "description": meta["description"],
        })
    return out


# ============================================================
# laya router preset fast-path(M3.73 新增)
# ============================================================
def _ensure_laya_router():
    """懒加载 laya Router + router_questions preset。失败返回 None。"""
    global _LAYA_ROUTER, _LAYA_ROUTER_QS, _LAYA_ROUTER_TRIED
    if not _LAYA_OK:
        return None, None
    if _LAYA_ROUTER_TRIED:
        return _LAYA_ROUTER, _LAYA_ROUTER_QS
    try:
        _LAYA_ROUTER = _LayaRouter(default="multilingual", preload=True)
        _LAYA_ROUTER_QS = _laya_router_questions()
    except Exception as e:  # noqa: BLE001
        print(f"[intent_router] laya Router 加载失败: {e}", file=sys.stderr)
        _LAYA_ROUTER = None
        _LAYA_ROUTER_QS = None
    _LAYA_ROUTER_TRIED = True
    return _LAYA_ROUTER, _LAYA_ROUTER_QS


def _laya_route_to_5x6(answers: dict) -> tuple[str, str, float]:
    """把 laya router 4 维答案 → (intent, task_type, laya_confidence)。

    laya router schema:
      - difficulty(0-3):trivial/easy/moderate/hard
      - domain(choice):code / math_or_logic / writing / factual_lookup / data_analysis / general_knowledge / other
      - needs_tools(noul):0-1,需外部工具
      - is_sensitive(noul):0-1,涉及金钱/法律/医疗/安全

    映射策略(经验,见 M3.71 laya bench + M3.73 修复):
      - difficulty ≥ 2.5        → task=long
      - difficulty 1.5-2.5      → task=general
      - difficulty 0.5-1.5      → task=fast
      - difficulty < 0.5        → task=fast
      - domain=code             → intent=code
      - domain=writing          → intent=chat(创意写作)
      - domain=factual_lookup   → intent=search
      - domain=data_analysis    → intent=tool_call
      - 其它                    → intent=chat
      - needs_tools ≥ 0.6       → 强制 intent=tool_call(若上一步是 chat)
      - is_sensitive ≥ 0.7      → 强制 task=general(避免 fast-path 跳过审核)

    注:`original_text` 仅用于"domain=code 但 needs_tools 高 → tool_call"的判定,
    用来识别 disk_cleanup / 危险命令 / perf 监控 等场景(LoRA 训练集把它们归 tool_call)。
    M3.73 修复:这些场景 laya 都判 domain=code + needs_tools≥0.3,但 LoRA 训练集标 tool_call;
    关键词匹配后升级,避免 fast-path 误归 code。

    Returns:
      (intent, task, laya_conf)
      laya_conf 是 difficulty 的 0-1 归一化 + 各维度平均 confidence
    """
    diff = answers.get("difficulty", {}).get("score", 0.0) or 0.0
    domain = answers.get("domain", {}).get("choice", "general_knowledge")
    needs_tools = answers.get("needs_tools", {}).get("noul", 0.0) or 0.0
    is_sensitive = answers.get("is_sensitive", {}).get("noul", 0.0) or 0.0
    original_text = answers.get("__original_text__", "") or ""

    # task
    if diff >= 2.5:
        task = "long"
    elif diff >= 1.5:
        task = "general"
    elif diff >= 0.5:
        task = "fast"
    else:
        task = "fast"

    # intent
    if domain == "code":
        intent = "code"
    elif domain == "writing":
        intent = "chat"
    elif domain == "factual_lookup":
        intent = "search"
    elif domain == "data_analysis":
        intent = "tool_call"
    else:
        intent = "chat"

    # needs_tools 强覆盖 chat → tool_call
    if needs_tools >= 0.6 and intent == "chat":
        intent = "tool_call"

    # M3.73 修复:domain=code + 文本含"运维/系统"关键词 → tool_call
    # laya 把"清理 D 盘"/"rm -rf"/"性能监控"都判 code(0.987+),但 LoRA 训练集这些归 tool_call
    # 关键词精简到运维类,真正的代码编程问题(无这些词)不受影响
    _OPS_HINTS = (
        "盘", "清理", "垃圾", "rm ", "rm-", "del ", "del-",
        "删除", "卸载", "安装", "重启", "关机",
        "性能", "监控", "cpu", "内存", "进程", "端口", "服务",
        "registry", "注册表", "防火墙", "网络", "磁盘", "备份", "还原",
        "系统", "开机", "蓝屏",
    )
    if intent == "code" and any(
        kw in original_text.lower() for kw in _OPS_HINTS
    ):
        intent = "tool_call"

    # is_sensitive 强制 task=general(慢路径,需审核)
    if is_sensitive >= 0.7:
        task = "general"

    # laya_conf:laya 4 维平均 confidence(0-1)
    domain_probs = answers.get("domain", {}).get("probabilities", {})
    domain_top_prob = max(domain_probs.values()) if domain_probs else 0.5
    laya_conf = round(
        (min(diff / 3.0, 1.0) * 0.4
         + domain_top_prob * 0.4
         + needs_tools * 0.1
         + (1.0 - is_sensitive) * 0.1),
        4,
    )
    return intent, task, laya_conf


def _laya_classify_route(text: str) -> Optional[dict]:
    """laya router_questions() 4 维 → (intent, task) + confidence。

    Returns:
      None — laya 未加载 / 推理失败
      dict — {intent, task, laya_conf, raw, latency_ms, backend='laya_router'}
    """
    router, qs = _ensure_laya_router()
    if router is None or qs is None:
        return None

    import time
    t0 = time.time()
    try:
        res = router.predict({"request": text}, qs)
        elapsed_ms = (time.time() - t0) * 1000
        answers = res.get("answers", {})
        # M3.73:注入原文用于"运维/系统"关键词匹配(domain=code → tool_call 升级)
        answers["__original_text__"] = text
        if not answers:
            return {
                "intent": "chat", "task": "general",
                "laya_conf": 0.0,
                "raw": res, "latency_ms": elapsed_ms,
                "backend": "laya_router",
                "parse_fail": True,
            }
        intent, task, laya_conf = _laya_route_to_5x6(answers)
        return {
            "intent": intent,
            "task": task,
            "laya_conf": laya_conf,
            "raw": answers,
            "latency_ms": elapsed_ms,
            "backend": "laya_router",
            "parse_fail": False,
        }
    except Exception as e:  # noqa: BLE001
        elapsed_ms = (time.time() - t0) * 1000
        return {
            "intent": "chat", "task": "general",
            "laya_conf": 0.0,
            "raw": None, "latency_ms": elapsed_ms,
            "backend": "laya_router",
            "parse_fail": True,
            "error": f"{type(e).__name__}: {e}",
        }


def _build_dist(adapter_name: str, valid_labels: set[str],
                primary: str, primary_conf: float,
                raw: str = "") -> dict[str, float]:
    """从 adapter 输出构造均匀分布,主类用 primary_conf,其余平摊剩余概率。

    Note:conf 版 adapter 输出格式是 `<label>:0.95`,我们只有 1 个 conf。
    其余类的概率无法直接拿(0.6B 不返回 softmax dist),
    所以用 **(1 - primary_conf) / (n-1)** 近似"其余类均匀分布"。
    这是**已知限制**:与真 softmax dist 相比有偏差,主要看主类的绝对置信度。
    """
    n = len(valid_labels)
    dist: dict[str, float] = {}
    remaining = max(0.0, 1.0 - primary_conf)
    other = remaining / max(1, n - 1) if n > 1 else 0.0
    for label in valid_labels:
        if label == primary:
            dist[label] = round(primary_conf, 4)
        else:
            dist[label] = round(other, 4)
    return dist


def _pick_route(intent: str, task: str) -> dict:
    """根据 (intent, task) 查路由表,fallback 走 __any__。"""
    for (key_intent, key_task), meta in _ROUTE_MAP:
        if key_intent == "__any__":
            continue  # fallback 最后才看
        if key_intent == intent and key_task == task:
            return {
                "intent": intent,
                "task": task,
                **meta,
            }
    # fallback
    for (key_intent, key_task), meta in _ROUTE_MAP:
        if key_intent == "__any__":
            return {
                "intent": intent,
                "task": task,
                **meta,
            }
    # 兜底兜底(理论上不会到这里)
    return {
        "intent": intent,
        "task": task,
        "spec": "unknown",
        "spec_short": "unknown",
        "action": "(no route)",
        "executor": "none",
        "needs_user_spec": False,
        "description": "no matching route",
    }


def classify_and_route(text: str, history: str = "",
                       use_conf: bool = True,
                       no_run: bool = False,
                       laya_floor: float = 0.60,
                       no_laya: bool = False) -> dict:
    """单条输入 → laya fast-path + 双 spec LoRA fallback + 路由。

    M3.73 升级:
      - laya.router_questions() 4 维先跑(本地 CPU p50 ~500ms)
      - laya_conf ≥ laya_floor → 直接用 laya 结果,跳过 LoRA 双 spec(节省 ~5s)
      - laya_conf < laya_floor → 走 LoRA intents_conf + task_conf 兜底
      - no_laya=True → 跳过 laya(强制 LoRA 路径)

    Args:
        text: 用户输入
        history: 上下文(可空)
        use_conf: True 用 conf adapter(推荐),False 用 base
        no_run: True 只返回路由不执行
        laya_floor: laya 置信度阈值,≥ 此值用 laya 否则回退 LoRA
        no_laya: True 禁用 laya fast-path(强制 LoRA)

    Returns:
        dict 包含:
          - input: 原文
          - intents_dist: 5 类意图分布
          - task_dist: 6 类任务分布
          - intent / intent_conf: 主意图 + 置信度
          - task_type / task_conf: 主任务 + 置信度
          - laya_result: laya fast-path 输出(若有)
          - backend: "laya_router" 或 "lora_fallback" 或 "lora_only"
          - route: {intent, task, spec, action, executor, ...}
          - confidence: 综合置信度 = intent_conf * task_conf
          - latency_sec: 双 spec 总耗时
          - tokens: 总 token 数
          - raws: {intents_raw, task_raw, laya_raw}
    """
    import time
    t0 = time.time()

    laya_res = None
    backend = "lora_only"

    # 0. laya fast-path(若启用)
    if not no_laya:
        laya_res = _laya_classify_route(text)
        if (laya_res is not None
                and not laya_res.get("parse_fail")
                and laya_res.get("laya_conf", 0.0) >= laya_floor):
            # laya 高置信度,直接用 laya 路由
            intent = laya_res["intent"]
            task = laya_res["task"]
            intent_conf = laya_res["laya_conf"]
            task_conf = laya_res["laya_conf"]
            backend = "laya_router"

            # 构造分布(单点 + 0.1 平摊给其它)
            intents_dist = _build_dist("laya", VALID_INTENTS,
                                       intent, intent_conf)
            task_dist = _build_dist("laya", VALID_TASKS,
                                    task, task_conf)

            route = _pick_route(intent, task)
            confidence = round(intent_conf * task_conf, 4)
            elapsed = round(time.time() - t0, 2)

            return {
                "input": text,
                "history": history,
                "intents_dist": intents_dist,
                "task_dist": task_dist,
                "intent": intent,
                "intent_conf": intent_conf,
                "task_type": task,
                "task_conf": task_conf,
                "laya_result": laya_res,
                "backend": backend,
                "risk_label": "unknown",  # laya 不判 risk
                "jailbreak_label": False,
                "route": route,
                "confidence": confidence,
                "latency_sec": elapsed,
                "tokens": 0,
                "raws": {
                    "intents_raw": "",
                    "task_raw": "",
                    "laya_raw": laya_res.get("raw"),
                },
            }

    # 1. LoRA 双 spec(原 M3.69 主路径)
    intents_res = classify_intents(text, history=history, use_conf=use_conf)
    task_res = classify_task_local(text, history=history, use_conf=use_conf)

    # 如果 laya 跑了但低置信度,在 backend 标记
    if laya_res is not None and not laya_res.get("parse_fail"):
        backend = "lora_fallback"  # laya 试了但 < floor,降级 LoRA
    elif laya_res is not None and laya_res.get("parse_fail"):
        backend = "lora_fallback"  # laya parse 失败,降级 LoRA

    elapsed = round(time.time() - t0, 2)

    # 提取主类 + 置信度
    intent = intents_res["intent"]
    intent_conf = intents_res.get("action_conf", 0.0)
    task = task_res["task_type"]
    task_conf = task_res.get("action_conf", 0.0)

    # 构造分布(已知限制:conf adapter 只输出 1 个 conf,其余类按 (1-conf)/(n-1) 平摊)
    intents_dist = _build_dist("intents_conf", VALID_INTENTS,
                               intent, intent_conf, intents_res.get("raw", ""))
    task_dist = _build_dist("task_conf", VALID_TASKS,
                            task, task_conf, task_res.get("raw", ""))

    # 路由
    route = _pick_route(intent, task)

    # 综合置信度 = intent_conf × task_conf(独立概率相乘)
    confidence = round(intent_conf * task_conf, 4)

    return {
        "input": text,
        "history": history,
        "intents_dist": intents_dist,
        "task_dist": task_dist,
        "intent": intent,
        "intent_conf": intent_conf,
        "task_type": task,
        "task_conf": task_conf,
        "laya_result": laya_res,  # 可能为 None 或低置信度
        "backend": backend,
        "risk_label": intents_res.get("risk_label", "unknown"),
        "jailbreak_label": intents_res.get("jailbreak_label", False),
        "route": route,
        "confidence": confidence,
        "latency_sec": elapsed,
        "tokens": intents_res.get("tokens", 0) + task_res.get("tokens", 0),
        "raws": {
            "intents_raw": intents_res.get("raw", ""),
            "task_raw": task_res.get("raw", ""),
            "laya_raw": (laya_res or {}).get("raw") if laya_res else None,
        },
    }


def multi_candidates(text: str, top_k: int = 5) -> list[dict]:
    """多候选排序:对所有 (intent, task) 组合按 conf 排序。

    实际数据来源:取 intents_dist × task_dist 的笛卡尔积 top_k。
    已知限制:由于 conf adapter 只输出 1 个 conf,笛卡尔积分布是近似的。

    Returns:
        list of {intent, task, joint_prob, route, confidence} 按 joint_prob 倒序
    """
    # 先跑一次主分类拿主类 conf
    main = classify_and_route(text)
    intents_dist = main["intents_dist"]
    task_dist = main["task_dist"]

    candidates = []
    for intent, iprob in intents_dist.items():
        for task, tprob in task_dist.items():
            joint = round(iprob * tprob, 4)
            route = _pick_route(intent, task)
            candidates.append({
                "intent": intent,
                "task": task,
                "intent_prob": iprob,
                "task_prob": tprob,
                "joint_prob": joint,
                "spec": route["spec"],
                "spec_short": route["spec_short"],
                "executor": route["executor"],
            })

    candidates.sort(key=lambda x: x["joint_prob"], reverse=True)
    return candidates[:top_k]


# ------------------------------------------------------------
# 三种格式化输出
# ------------------------------------------------------------
def format_text(result: dict) -> str:
    """人类可读文本格式(默认 CLI 输出)。"""
    lines = []
    lines.append(f"输入: {result['input']!r}")
    lines.append("")

    lines.append(f"[backend] {result.get('backend', 'lora_only')}")
    if result.get("laya_result"):
        lr = result["laya_result"]
        lines.append(f"  laya_conf: {lr.get('laya_conf', 0):.3f}  "
                     f"lat: {lr.get('latency_ms', 0):.0f}ms  "
                     f"parse_fail: {lr.get('parse_fail', False)}")
    lines.append("")

    lines.append("[intents_conf] 5 类分布:")
    for label, prob in sorted(result["intents_dist"].items(),
                              key=lambda x: -x[1]):
        marker = "  ← 选" if label == result["intent"] else ""
        lines.append(f"  {label:<12} {prob:.2f}{marker}")
    lines.append("")

    lines.append("[task_conf] 6 类分布:")
    for label, prob in sorted(result["task_dist"].items(),
                              key=lambda x: -x[1]):
        marker = "  ← 选" if label == result["task_type"] else ""
        lines.append(f"  {label:<12} {prob:.2f}{marker}")
    lines.append("")

    route = result["route"]
    lines.append(f"→ 路由: {route['intent']} + {route['task']}")
    lines.append(f"→ spec: {route['spec']}")
    lines.append(
        f"→ 置信度: {result['intent_conf']:.2f} × "
        f"{result['task_conf']:.2f} = {result['confidence']:.3f}")
    lines.append(f"→ 执行: {route['action']}")
    lines.append(f"→ 描述: {route['description']}")
    lines.append("")
    lines.append(f"(延迟 {result['latency_sec']}s, "
                 f"{result['tokens']} tokens)")
    return "\n".join(lines)


def format_md(result: dict) -> str:
    """Markdown 格式(适合贴文档)。"""
    lines = [f"# Intent Router 报告\n",
             f"**输入**: `{result['input']}`\n"]

    lines.append("## 意图分布(5 类)\n")
    lines.append("| intent | prob |")
    lines.append("|---|---|")
    for label, prob in sorted(result["intents_dist"].items(),
                              key=lambda x: -x[1]):
        marker = " ← **选**" if label == result["intent"] else ""
        lines.append(f"| {label} | {prob:.2f}{marker} |")
    lines.append("")

    lines.append("## 任务分布(6 类)\n")
    lines.append("| task | prob |")
    lines.append("|---|---|")
    for label, prob in sorted(result["task_dist"].items(),
                              key=lambda x: -x[1]):
        marker = " ← **选**" if label == result["task_type"] else ""
        lines.append(f"| {label} | {prob:.2f}{marker} |")
    lines.append("")

    route = result["route"]
    lines.append("## 路由建议\n")
    lines.append(f"- **意图**: `{route['intent']}` "
                 f"(conf {result['intent_conf']:.2f})")
    lines.append(f"- **任务**: `{route['task']}` "
                 f"(conf {result['task_conf']:.2f})")
    lines.append(f"- **spec**: `{route['spec']}`")
    lines.append(f"- **executor**: `{route['executor']}`")
    lines.append(f"- **综合置信度**: `{result['confidence']:.3f}`")
    lines.append(f"- **执行命令**: `{route['action']}`")
    lines.append(f"- **说明**: {route['description']}")
    return "\n".join(lines)


def format_json(result: dict) -> str:
    """JSON 格式(机器可读)。"""
    import json as _json
    return _json.dumps(result, ensure_ascii=False, indent=2)
