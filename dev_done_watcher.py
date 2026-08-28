# -*- coding: utf-8 -*-
# dev_done_watcher.py — 消费 oiagent_dev_consumer 写的 done-feed(协作链「完成了没人知道」的反馈通道)。
#
# consumer 每完成一个 task,往 ~/.local/share/aureon/log/oiagent_dev_done_feed.jsonl
# 追加一行 {task_id,title,result_len,gate_verdict,gate_warn,ts,iso}。
# 本脚本由主会话的 cron 定期调用:读取 feed 中尚未消费的条目(游标在 cursor 文件),
# 对每个新完成的 task 做宪法闸门复检,输出一段可贴给用户的中文简报。
#
# 用法:  python dev_done_watcher.py            # 输出新完成条目简报(无新条目则打印 NONE)
#        python dev_done_watcher.py --all      # 忽略游标,全量简报(调试用)
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
import sys as _s
_s.path.insert(0, str(ROOT))

LOG_DIR = Path.home() / ".local" / "share" / "aureon" / "log"
FEED = LOG_DIR / "oiagent_dev_done_feed.jsonl"
CURSOR = LOG_DIR / "oiagent_dev_done_feed.cursor"


def _read_feed() -> list[dict]:
    if not FEED.exists():
        return []
    out = []
    for line in FEED.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
    return out


def main() -> None:
    show_all = "--all" in sys.argv
    feed = _read_feed()
    seen: set[str] = set()
    if not show_all and CURSOR.exists():
        try:
            seen = set(json.loads(CURSOR.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            seen = set()

    new = [r for r in feed if str(r.get("task_id")) not in seen]

    # 落地六旁路:tasks-code 有待主会话认领的改代码单(含幻影升级来的),也报——
    # 否则主会话不在场时升级单会躺着没人知道。与 done-feed 新完成合并成一条简报。
    pending_code = []
    try:
        import dev_dispatch as _dd  # noqa: PLC0415
        pending_code = _dd.list_code_tasks()
    except Exception:  # noqa: BLE001
        pass

    # 落地八旁路:有待师傅挑的成功模式候选,也报——否则候选躺着没人入库。
    pending_patterns = []
    try:
        import dev_patterns as _dp  # noqa: PLC0415
        pending_patterns = _dp.pending()
    except Exception:  # noqa: BLE001
        pass

    if not new and not pending_code and not pending_patterns:
        print("NONE")
        return

    # 对新完成条目做闸门复检(结果全文在 task_queue,feed 只有摘要)
    try:
        import constitution_compliance as cc  # noqa: PLC0415
        from memory.task_queue import TaskQueue  # noqa: PLC0415
        tq = TaskQueue()
    except Exception:  # noqa: BLE001
        cc, tq = None, None

    lines = []
    if new:
        lines.append(f"开发链新完成 {len(new)} 个任务(consumer 完成信号,免轮询):")
    for r in new:
        tid = r.get("task_id")
        gate_info = f"出队闸={r.get('gate_verdict')}"
        if r.get("gate_warn"):
            gate_info += f"(warn:{r['gate_warn']})"
        recheck = ""
        if cc is not None and tq is not None:
            try:
                res = tq.get(int(tid)).result or ""
                rep = cc.scan_text(res)
                warns = [f.rule_id for f in rep.findings if f.severity == "warn"]
                recheck = f" | 复检={rep.verdict}" + (f" warn={warns}" if warns else "")
            except Exception:  # noqa: BLE001
                recheck = " | 复检ERR"
        lines.append(
            f"  • #{tid} {r.get('title','')[:40]} — {r.get('result_len',0)}字 | "
            f"{gate_info}{recheck} | {r.get('iso','')}"
        )
    if pending_code:
        lines.append(f"⚠️ tasks-code 有 {len(pending_code)} 个改代码单待主会话认领(含幻影升级):")
        for t in pending_code:
            lines.append(f"  • #{t.id} [p{t.priority}] {t.title[:50]}")
        # 落地七接续:把首个单的全文带出来,主会话看到即可直接 claim + 拉 code-implementer,
        # 不用再手动 dev_dispatch.py list。落地七循环只做队列机械,实现由主会话 Agent 子代理做。
        first = pending_code[0]
        decl = _dd.parse_declared_files(getattr(first, "content", "") or "")
        lines.append(f"  → 主会话认领循环(落地七): python tasks_code_claim_loop.py claim")
        lines.append(f"     首个单 #{first.id} 声明改动: {decl}")
        lines.append(f"     任务全文: python -c \"from memory.task_queue import TaskQueue; print(TaskQueue().get({first.id}).content)\"")
    if pending_patterns:
        lines.append(f"🧠 成功模式候选 {len(pending_patterns)} 条待师傅挑(落地八·员工记忆):")
        for i, r in enumerate(pending_patterns[:5]):
            lines.append(f"  [{i}] role={r['role']} | {r['title'][:40]}")
        lines.append("  → 师傅挑选: python dev_patterns.py pending 查看, approve <i> 入库 / reject <i> 丢弃")
    print("\n".join(lines))

    seen |= {str(r.get("task_id")) for r in feed}
    CURSOR.write_text(json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
