# -*- coding: utf-8 -*-
# prisir_workflow.py — 多 agent 编排引擎(2026-09-05)
#
# 迁入 team_lead_tools 的编排模式(race 竞速 / dispatch 路由 / 多步 fan-out),
# 但不搬其 964 行(那套绑 cognee trace + routing.yaml + quota 适配,重)。
# 这里落地为「一个 run_workflow 工具 + 三个编排原语」,复用 prisiragent_cli 的
# spawn_subagent 基座(子代独立工具循环 + 权限闸透传 + 进度事件回父级)。
#
# 编排原语(对齐 team_lead 语义,简化落地):
#   race     同一 prompt 并发派给多个子代(可不同模型),先出有效结果的赢,其余取消。
#            —— team_lead.race_impl 的「先返回的进 trace」思想,但子代是 agent 不是裸 LLM。
#   dispatch 按任务文本从路由规则挑模型/角色,派单给一个子代。
#            —— team_lead.dispatch_impl 的「规则命中派单」思想,规则改用简表。
#   pipeline 声明 steps,顺序执行;每步可标记 parallel 做 fan-out,步间把上步结果
#            喂给下步。—— 多步编排主干。
#
# 红线:
#   - 全程同步(prisiragent 主架构同步),并发用线程不用 asyncio(避开 MCP 桥那套
#     loop 亲和性坑);子代本就独立 run_conversation,线程安全靠 spawn 的 _SPAWN_LOCK。
#   - 子代不能再 spawn(防递归),编排只在主 agent 这一层。
#   - 每步超时 + 总步数上限,防失控 fan-out。
from __future__ import annotations

import concurrent.futures as _fut
import re
import threading
import time

# 编排上限(防失控)
_MAX_PARALLEL = 6        # 单次 fan-out / race 最多并发几个子代
_MAX_STEPS = 20          # 一个 pipeline 最多几步
_STEP_TIMEOUT = 600      # 单步超时(秒)


# ─────────────────────────────────────────────────
# 模型解析:race/dispatch 指定「平台名」时经 web 层 key_store 解析成 litellm 串。
# 与 _resolve_subagent_model 同路,但编排器要能「列出可用平台」供 race 默认池。
# ─────────────────────────────────────────────────
def _available_platforms() -> list[str]:
    """用户 key_store 里已配置的平台名列表(race 默认池)。web 层不在返回 []。"""
    try:
        import prisiragent_web as _w  # noqa: PLC0415
        plats = _w._key_store.list_platforms()
        out = []
        for p in plats:
            name = p.get("platform") if isinstance(p, dict) else str(p)
            if name:
                out.append(name)
        return out
    except Exception:  # noqa: BLE001
        return []


def _resolve_model(spec: str, parent_model: str) -> str:
    """平台名 → litellm model 串;空/解析失败回退父级模型。"""
    spec = (spec or "").strip()
    if not spec:
        return parent_model
    try:
        import prisiragent_cli as _c  # noqa: PLC0415
        return _c._resolve_subagent_model(spec, parent_model)
    except Exception:  # noqa: BLE001
        return parent_model


# ─────────────────────────────────────────────────
# 子代执行单元:包一层 _run_subagent_once,返回统一结构。
# 不直接调 _t_spawn_subagent(它面向工具调用、返回拼接字符串);
# 编排器要结构化结果(ok/out/model/elapsed)做 race 判定与步间传递。
# ─────────────────────────────────────────────────
def _run_step(task: str, model: str, workdir: str, goal: bool,
              on_event, on_confirm, tag: str = "") -> dict:
    """跑一个子代,返回 {ok, out, model, elapsed_ms, tag}。异常收敛为 ok=False。"""
    import prisiragent_cli as _c  # noqa: PLC0415
    t0 = time.time()

    def _ev(ev):
        if on_event:
            try:
                ev2 = dict(ev)
                ev2["agent"] = "sub"
                if tag:
                    ev2["wf_tag"] = tag
                on_event(ev2)
            except Exception:  # noqa: BLE001
                pass

    try:
        tools = _c._subagent_tools(None)  # 全量(除 spawn 自身)
        res = _c._run_subagent_once(task, model, workdir, tools, goal,
                                    _ev, on_confirm, None)
        out = res.get("out", "")
        rc = res.get("rc", 0)
        return {
            "ok": rc == 0 and bool(out.strip()),
            "out": out,
            "model": model,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "tag": tag,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "out": "",
            "error": f"{type(e).__name__}: {e}",
            "model": model,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "tag": tag,
        }


# ─────────────────────────────────────────────────
# 原语① race:同 prompt 并发多子代,先出有效结果的赢。
# ─────────────────────────────────────────────────
def race(prompt: str, models: list[str] | None, workdir: str,
         parent_model: str, goal: bool = True,
         on_event=None, on_confirm=None, max_parallel: int = _MAX_PARALLEL) -> dict:
    """竞速:同一任务并发派给多个子代(各自模型),先返回有效结果者胜。

    models=None 时用 key_store 全部已配平台。返回 {winner, contestants}。
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"error": "race: empty prompt"}
    specs = models or _available_platforms() or [""]
    specs = [s for s in specs if s is not None][:max_parallel]
    if not specs:
        specs = [""]
    resolved = [(s, _resolve_model(s, parent_model)) for s in specs]

    results: list[dict] = []
    winner: dict | None = None
    # FIRST_COMPLETED 语义:谁先出 ok 结果谁赢,其余等超时/取消。
    with _fut.ThreadPoolExecutor(max_workers=len(resolved)) as ex:
        futures = {
            ex.submit(_run_step, prompt, mdl, workdir, goal,
                      on_event, on_confirm, f"race:{spec or 'inherit'}"): (spec, mdl)
            for spec, mdl in resolved
        }
        try:
            for fut in _fut.as_completed(futures, timeout=_STEP_TIMEOUT):
                r = fut.result()
                results.append(r)
                if r.get("ok") and winner is None:
                    winner = r
                    # 不等其余:取消未完成的(尽力;线程不可强杀,标记即可)
                    for other in futures:
                        if other is not fut and not other.done():
                            other.cancel()
                    break
        except _fut.TimeoutError:
            pass
    return {
        "winner": winner,
        "contestants": results,
        "total": len(resolved),
        "ok": winner is not None,
    }


# ─────────────────────────────────────────────────
# 原语② dispatch:按任务文本从路由规则挑模型/角色,派单。
# 规则是简表:[{match(正则), model, note}],第一命中生效;都不中走 default。
# ─────────────────────────────────────────────────
def _match_route(rules: list[dict], task_text: str) -> dict:
    for rule in rules:
        pat = rule.get("match", "")
        if not pat:
            continue
        try:
            if re.search(pat, task_text, re.IGNORECASE):
                return rule
        except re.error:
            if pat.lower() in task_text.lower():
                return rule
    return {}


def dispatch(task: str, rules: list[dict] | None, workdir: str,
             parent_model: str, default_model: str = "",
             goal: bool = True, on_event=None, on_confirm=None) -> dict:
    """路由派单:按任务文本命中规则挑模型,派给一个子代执行。

    rules 缺省空 → 用 default_model(空=继承父级)。返回 {route, result}。
    """
    task = (task or "").strip()
    if not task:
        return {"error": "dispatch: empty task"}
    rule = _match_route(rules or [], task)
    model_spec = rule.get("model") or default_model
    mdl = _resolve_model(model_spec, parent_model)
    note = rule.get("note", "")
    r = _run_step(task, mdl, workdir, goal, on_event, on_confirm,
                  f"dispatch:{model_spec or 'inherit'}")
    return {"route": {"model": mdl, "spec": model_spec, "note": note},
            "result": r, "ok": r.get("ok", False)}


# ─────────────────────────────────────────────────
# 原语③ pipeline:声明 steps 顺序执行,步可 parallel fan-out,步间传结果。
#   steps = [
#     {"task": "...", "model": "...", "parallel": ["子任务A","子任务B"]},  # fan-out 步
#     {"task": "用上一步结果做 ...", "use_prev": true},                     # 顺序步
#   ]
# ─────────────────────────────────────────────────
def pipeline(steps: list[dict], workdir: str, parent_model: str,
             goal: bool = True, on_event=None, on_confirm=None) -> dict:
    """多步编排:顺序跑 steps,支持单步 fan-out 与步间结果传递。返回 {steps, ok}。"""
    if not isinstance(steps, list) or not steps:
        return {"error": "pipeline: steps must be a non-empty list"}
    steps = steps[:_MAX_STEPS]
    ran: list[dict] = []
    prev_out = ""
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            ran.append({"index": i, "ok": False, "error": "step not a dict"})
            continue
        task = (step.get("task") or "").strip()
        if step.get("use_prev") and prev_out:
            task = f"{task}\n\n【上一步结果】\n{prev_out[:3000]}"
        mdl = _resolve_model(step.get("model", ""), parent_model)

        # fan-out 步:parallel 列表里每个子任务并发一个子代
        par = step.get("parallel")
        if isinstance(par, list) and par:
            par = [str(p) for p in par if str(p).strip()][:_MAX_PARALLEL]
            with _fut.ThreadPoolExecutor(max_workers=len(par)) as ex:
                futs = [ex.submit(_run_step, p, mdl, workdir, goal,
                                  on_event, on_confirm, f"step{i}-par{j}")
                        for j, p in enumerate(par)]
                subs = []
                for f in futs:
                    try:
                        subs.append(f.result(timeout=_STEP_TIMEOUT))
                    except Exception as e:  # noqa: BLE001
                        subs.append({"ok": False, "error": f"{type(e).__name__}: {e}"})
            ok_n = sum(1 for s in subs if s.get("ok"))
            prev_out = "\n\n".join(
                f"--- 子任务 {j+1} ---\n{s.get('out','')}" for j, s in enumerate(subs))
            ran.append({"index": i, "type": "parallel", "count": len(par),
                        "ok_count": ok_n, "ok": ok_n > 0, "subs": subs})
            continue

        # 顺序步
        if not task:
            ran.append({"index": i, "ok": False, "error": "empty task"})
            continue
        r = _run_step(task, mdl, workdir, goal, on_event, on_confirm, f"step{i}")
        prev_out = r.get("out", "")
        ran.append({"index": i, "type": "seq", "model": mdl, **r})
    return {"steps": ran, "ok": any(s.get("ok") for s in ran)}


# ─────────────────────────────────────────────────
# run_workflow 工具入口:模型用自然语言声明编排,这里解析执行。
# ─────────────────────────────────────────────────
def run_workflow(spec: dict, workdir: str, parent_model: str,
                 on_event=None, on_confirm=None) -> str:
    """执行一个 workflow 声明,返回给模型看的结构化文本结果。

    spec = {
      "mode": "race" | "dispatch" | "pipeline",
      "prompt": "...",            # race
      "models": [...],            # race(可选,默认全部已配平台)
      "task": "...",              # dispatch
      "rules": [...],             # dispatch(可选)
      "steps": [...],             # pipeline
      "goal": true                # 可选,默认 true(子代自主跑)
    }
    """
    if not isinstance(spec, dict):
        return "[run_workflow error] spec must be a JSON object"
    mode = (spec.get("mode") or "").strip()
    goal = bool(spec.get("goal", True))
    t0 = time.time()

    def _fmt(res: dict) -> str:
        return _format_result(mode, res, int((time.time() - t0) * 1000))

    if mode == "race":
        return _fmt(race(spec.get("prompt", ""), spec.get("models"),
                         workdir, parent_model, goal, on_event, on_confirm))
    if mode == "dispatch":
        return _fmt(dispatch(spec.get("task", ""), spec.get("rules"),
                             workdir, parent_model, spec.get("default_model", ""),
                             goal, on_event, on_confirm))
    if mode == "pipeline":
        return _fmt(pipeline(spec.get("steps"), workdir, parent_model,
                             goal, on_event, on_confirm))
    return f"[run_workflow error] unknown mode '{mode}'(支持 race/dispatch/pipeline)"


def _format_result(mode: str, res: dict, ms: int) -> str:
    """把编排结果格式化成模型可读的文本(进对话历史,供模型汇总)。"""
    if res.get("error"):
        return f"[run_workflow {mode} error] {res['error']}"
    lines = [f"[run_workflow {mode} 完成, {ms}ms]"]
    if mode == "race":
        w = res.get("winner")
        if w:
            lines.append(f"胜出: {w.get('tag','')} (模型 {w.get('model','')}, "
                         f"{w.get('elapsed_ms',0)}ms)")
            lines.append("胜出输出:\n" + (w.get("out", "")[:4000]))
        else:
            lines.append(f"无子代产出有效结果(共 {res.get('total',0)} 个参赛)")
    elif mode == "dispatch":
        route = res.get("route", {})
        lines.append(f"派单到模型 {route.get('model','')} ({route.get('note','')})")
        r = res.get("result", {})
        lines.append(f"执行 {'成功' if r.get('ok') else '失败'} "
                     f"({r.get('elapsed_ms',0)}ms):\n" + (r.get("out", "")[:4000]))
    elif mode == "pipeline":
        for s in res.get("steps", []):
            if s.get("type") == "parallel":
                lines.append(f"步骤 {s.get('index')}: fan-out {s.get('ok_count',0)}/"
                             f"{s.get('count',0)} 成功")
            else:
                lines.append(f"步骤 {s.get('index')}: {'成功' if s.get('ok') else '失败'} "
                             f"({s.get('elapsed_ms',0)}ms)")
        # 最后一步的输出作为整体结果
        last = next((s for s in reversed(res.get("steps", [])) if s.get("out")), None)
        if last:
            lines.append("最终输出:\n" + last.get("out", "")[:4000])
    return "\n".join(lines)


# 全局编排锁:与 spawn 的 _SPAWN_LOCK 同源——编排内部子代经 _run_subagent_once
# 会拿 _SPAWN_LOCK 串行化 TOOLS 全局替换,这里不再加第二层锁(避免死锁)。
_WORKFLOW_LOCK = threading.Lock()
