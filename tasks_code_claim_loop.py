# -*- coding: utf-8 -*-
# tasks_code_claim_loop.py — 主会话认领循环(落地七 / 缺口1,2026-08-15)
#
# 定位:补「主会话不在场时 tasks-code 无人认领」的开环。设计要点:
#   本脚本只做【队列机械】(认领/双闸验收/标 done),不做实现本身 ——
#   实现由主会话用 Agent 工具拉起 code-implementer 子代理(持 Read/Edit/Write,
#   能真落盘)完成。文本 LLM 节点改不了文件,606/612 幻影即此;本循环不重复那个错误。
#
# 循环(watcher/cron 报「tasks-code 有待认领」→ 主会话驱动):
#   1. python tasks_code_claim_loop.py claim
#        认领首个 ready 单:内核守卫 → mark_running → 落 claim 上下文(started_ts/声明文件)
#        输出任务全文给主会话。
#   2. 主会话:用 Agent 工具拉起 code-implementer,把任务全文交给它真改代码。
#   3. python tasks_code_claim_loop.py complete <task_id> --result-file <f>
#        双闸验收:落盘校验(mtime >= 认领时刻)+ 宪法闸门扫实现报告。
#        双过 → mark_done + 写 done-feed(watcher 报用户);任一不过 → increment_retry 打回。
#
# 红线:
#   - 永不动内核:声明改动 constitution_compliance.py / prisir-dev-constitution.md 的单
#     直接拒跑(mark_cancelled,等用户/主会话手动)。
#   - 不放任 LLM 自动改代码:实现必须经主会话 Agent 子代理 + 双闸验收。
#   - 凭证走 env;审计只记摘要不落 content 明文。
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CLAIM_STATE = Path.home() / ".local" / "share" / "aureon" / "log" / "tasks_code_claim_state.json"

# 不可动内核:声明要改这些的单,循环拒跑(等用户/主会话手动)
_KERNEL_FILES = ("constitution_compliance.py", "prisir-dev-constitution.md")


def _kernel_guard(declared: list[str]) -> str | None:
    for rel in declared:
        for k in _KERNEL_FILES:
            if k in rel:
                return k
    return None


def _save_claim(task_id: int, started_ts: float, declared: list[str], created_at: float | None = None) -> None:
    CLAIM_STATE.parent.mkdir(parents=True, exist_ok=True)
    state = {}
    if CLAIM_STATE.exists():
        try:
            state = json.loads(CLAIM_STATE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            state = {}
    state[str(task_id)] = {"started_ts": started_ts, "declared": declared}
    if created_at is not None:
        state[str(task_id)]["created_at"] = created_at
    CLAIM_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _load_claim(task_id: int) -> dict | None:
    try:
        return json.loads(CLAIM_STATE.read_text(encoding="utf-8")).get(str(task_id))
    except Exception:  # noqa: BLE001
        return None


def cmd_claim() -> int:
    """认领首个 tasks-code ready 单。输出任务全文(主会话拿去交给 code-implementer)。"""
    import dev_dispatch as dd
    from memory.task_queue import TaskQueue
    tq = TaskQueue()

    ready = dd.list_code_tasks()
    if not ready:
        print("NONE: tasks-code 无待认领单")
        return 1
    task = ready[0]
    tid, title, content = task.id, task.title, task.content or ""
    declared = dd.parse_declared_files(content)

    k = _kernel_guard(declared)
    if k:
        tq.mark_cancelled(tid, reason=f"声明改动不可动内核 {k},认领循环拒跑,等用户/主会话手动")
        print(f"REJECT-KERNEL: #{tid} 声明改动内核 {k},已标 cancelled,需用户/主会话手动处理")
        return 2

    mr = tq.mark_running(tid)
    if not (isinstance(mr, dict) and mr.get("ok")):
        print(f"ERROR: #{tid} mark_running 失败: {mr}")
        return 3

    started = time.time()
    # 查任务的 created_at,作为兜底 verify 时间锚点(防止 retry 时 started_ts 过新导致误判)
    created_at = None
    try:
        t_meta = tq.get(tid) if hasattr(tq, "get") else None
        if t_meta is not None and hasattr(t_meta, "created_at"):
            created_at = float(t_meta.created_at)
    except Exception:  # noqa: BLE001
        pass
    _save_claim(tid, started, declared, created_at=created_at)
    print(f"CLAIMED #{tid} (started_ts={started:.0f}{', created_at=' + str(int(created_at)) if created_at else ''})")
    print(f"声明改动文件: {declared}")
    print("=" * 60)
    print(f"标题: {title}\n")
    print(content)
    print("=" * 60)
    print("下一步: 主会话用 Agent 工具拉起 code-implementer 执行,完成后跑:")
    print(f"  python tasks_code_claim_loop.py complete {tid} --result-file <实现报告路径>")
    return 0


def cmd_complete(task_id: int, result_file: str) -> int:
    """双闸验收:落盘校验 + 宪法闸门。双过标 done,任一不过打回。"""
    import dev_dispatch as dd
    import constitution_compliance as cc
    from memory.task_queue import TaskQueue
    tq = TaskQueue()

    claim = _load_claim(task_id)
    if not claim:
        print(f"ERROR: 无 #{task_id} 的认领上下文(未先跑 claim?)")
        return 3
    started_ts = claim["started_ts"]
    declared = claim["declared"]
    # 兜底时间锚:优先 created_at(retry 时不会变),fallback started_ts
    verify_ts = float(claim.get("created_at") or started_ts)

    try:
        reply = Path(result_file).read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: 读实现报告失败 {result_file}: {e}")
        return 3

    # 闸一:落盘校验(文件级幻影)
    ok_files, missing = dd.verify_files_touched(declared, verify_ts)
    # 闸二:宪法闸门(语义违宪/幻影声明)
    rep = cc.scan_text(reply)
    blockers = [f for f in rep.findings if f.severity == "blocker"]

    if not ok_files:
        tq.increment_retry(task_id, error_msg="认领循环:幻影(文件未落盘) " + ", ".join(missing))
        print(f"REJECT-PHANTOM: #{task_id} 文件未落盘: {missing} → 已打回 retry")
        return 4
    if blockers:
        reasons = "; ".join(f"{f.rule_id}({f.clause})" for f in blockers)
        tq.increment_retry(task_id, error_msg=f"认领循环:宪法闸门打回 {reasons}")
        print(f"REJECT-CONSTITUTION: #{task_id} {reasons} → 已打回 retry")
        return 5

    tq.mark_done(task_id, result=reply[:20000])
    # 落地八:双闸通过 → 提炼成功模式候选(半自动,师傅挑才入库),补「老员工手感」。
    try:
        import dev_patterns as dp
        if dp.propose_success("code-implementer", f"tasks-code#{task_id}", f"tasks-code#{task_id}", reply):
            print(f"  (已挂 1 条成功模式候选,主会话可 python dev_patterns.py pending 挑选入库)")
    except Exception:  # noqa: BLE001
        pass
    try:
        import oiagent_dev_consumer as cons
        cons._emit_done(task_id, f"[tasks-code]#{task_id}", len(reply), rep.to_dict())
    except Exception:  # noqa: BLE001
        pass
    print(f"DONE: #{task_id} 双闸通过(落盘 ✓ + 宪法 ✓),已标 done + 写 done-feed")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("claim", help="认领首个 tasks-code ready 单")
    cp = sub.add_parser("complete", help="双闸验收 + 标 done/打回")
    cp.add_argument("task_id", type=int)
    cp.add_argument("--result-file", required=True, help="实现报告文本路径")
    args = ap.parse_args()

    if args.cmd == "claim":
        sys.exit(cmd_claim())
    else:
        sys.exit(cmd_complete(args.task_id, args.result_file))


if __name__ == "__main__":
    main()
