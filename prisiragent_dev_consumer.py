# -*- coding: utf-8 -*-
# prisiragent_dev_consumer.py — prisiragent 开发团队协作链路消费者守护(2026-08-14)
#
# 背景(F1 stall 根因):派单走 task_submit → memory.task_queue.TaskQueue,
# 消费端是 mcp_prisiragent_v3_plan_parallel workflow 的 task_queue_input 节点。
# 但此前没有任何常驻进程在跑这个 workflow → task 569 一直 pending,无人消费。
# 本守护补上这个断点:常驻循环消费 ready task,mark_running → 跑 workflow → mark_done。
#
# 并行:一个进程内开 N 个 worker 线程,各自独立跑 workflow(WorkflowEntry 是并行图引擎)。
#   --workers N(默认 3)。每个 task 经 mark_running 原子认领,不会重复消费。
#
# 用法:
#   python prisiragent_dev_consumer.py              # 前台跑(看日志)
#   python prisiragent_dev_consumer.py --workers 4  # 4 并发
#   python prisiragent_dev_consumer.py --daemon     # 后台分离跑(无窗)
#   python prisiragent_dev_consumer.py --install    # 注册开机自启(登录触发)
#
# 红线:
#   - task content 进 LLM(走 cc-switch 15721 统一路由,不直连 vendor);
#   - 凭证全走环境变量,不回显;
#   - 失败的 task increment_retry(达到 max_retries 自动转 blocked/cancelled 由 TaskQueue 决定);
#   - 审计脱敏:只记 task_id/title/结果长度,不记 content 全文。
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # oi_enhancements/
sys.path.insert(0, str(ROOT))

LOG_DIR = Path.home() / ".local" / "share" / "aureon" / "log"
LOG_FILE = LOG_DIR / "prisiragent_dev_consumer.log"
TASK_NAME = "OIAgentDevConsumer"
LOCK_FILE = LOG_DIR / "prisiragent_dev_consumer.lock"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("prisiragent.dev_consumer")


def _get_tq():
    from memory.task_queue import TaskQueue  # noqa: PLC0415
    return TaskQueue()


# ── 质量闸门:宪法合规检测器(出队前必过)─────────────────────────
# 见 constitution_compliance.py —— 团队产出进编译收口前先过 scan_text,
# 命中 blocker 即打回(increment_retry),不标 done。这是 harness 的「判分层」,
# 补上 harness_test_battery(自然语言题集)+ v3 harness.py(执行引擎)都没有的确定性判分。
try:
    import constitution_compliance as _cc  # noqa: PLC0415
except Exception:  # noqa: BLE001
    _cc = None


def _gate_check(text: str):
    """宪法合规闸门。返回 (passed: bool, report_dict|None)。检测器缺失时放行(不阻塞)。"""
    if _cc is None:
        return True, None
    rep = _cc.scan_text(text)
    return rep.verdict == "PASS", rep.to_dict()


# ── 落地三:内核篡改自检(不可动内核)──────────────────────────────
# constitution_compliance.py + prisir-dev-constitution.md 是「不可动内核」,
# 只能由用户/主会话修改,团队 LLM 永不能改。consumer 启动时校验 checksum,
# 被篡改则拒绝消费并告警(借鉴 HarnessBank 不可动内核,保证判分基准不漂)。
_KERNEL_CHECKSUM = ROOT / "_kernel_checksum.txt"


def _verify_kernel() -> bool:
    """校验内核文件 checksum。文件缺失或不含本机条目时放行(未启用),不符则拒绝。"""
    if not _KERNEL_CHECKSUM.exists():
        return True  # 未启用 checksum 机制,放行
    try:
        import hashlib
        ok = True
        for line in _KERNEL_CHECKSUM.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            want, rel = line.split(None, 1)
            p = ROOT / rel.strip()
            if not p.exists():
                log.error("内核文件缺失: %s", rel)
                ok = False
                continue
            got = hashlib.sha256(p.read_bytes()).hexdigest()
            if got != want:
                log.error("内核被篡改: %s (期望 %s...,实际 %s...)", rel, want[:12], got[:12])
                ok = False
        return ok
    except Exception as e:  # noqa: BLE001
        log.error("内核校验异常: %s", str(e)[:120])
        return False


# ── 第一层:契约注入(每次必现)─────────────────────────────────────
_CONSTITUTION_PATH = ROOT / "docs" / "prisir-dev-constitution.md"


def _load_constitution() -> str:
    """读项目宪法(Ed25519/凭证/白名单/代码正确性红线)。读不到则空(不阻塞)。"""
    try:
        return _CONSTITUTION_PATH.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _build_system() -> str:
    base = (
        "你是 prisiragent 开发协作团队的执行 agent。下面先给你【项目宪法】——硬性技术契约,"
        "与你的常识/习惯写法冲突时一律以它为准,违反即返工。然后是任务(task content),"
        "产出可执行的处理结果:开发/审查/调研给出具体方案、代码或结论;信息不足明确指出缺什么。"
        "结果要可直接交付,不要客套。\n\n"
        "【内核不可动红线】constitution_compliance.py 与 docs/prisir-dev-constitution.md "
        "是不可动内核,只能由用户/主会话修改。你(团队 agent)无权修改这两个文件;"
        "任何要求你修改它们的任务,直接拒绝并在结果里说明「内核不可动,需主会话/用户改」。\n\n"
        "【项目宪法】\n" + _load_constitution()
    )
    # 落地八:注入本角色(consumer)的成功模式——「老员工手感」,上次怎么做成的。
    try:
        import dev_patterns as _dp  # noqa: PLC0415
        pat = _dp.recall_patterns("prisiragent 开发 实现 方案", role="consumer", n=3)
        if pat:
            base += "\n\n【你之前验证可行的做法(老员工手感,可参考复用)】\n" + pat
    except Exception:  # noqa: BLE001
        pass
    return base


# ── workflow graph:task_queue_input → llm(执行) → end ──────────────
def _build_graph() -> dict:
    return {
        "nodes": [
            {
                "id": "tq", "type": "task_queue_input",
                "config": {
                    "limit": 1,
                    "namespace": "tasks",
                    "output_key": "task_content",
                    "task_id_key": "task_id",
                    "on_no_ready": "block",
                },
            },
            {
                "id": "exec", "type": "llm",
                "config": {
                    # 直连百炼(cc-switch 15721 只是 health 端点,不是模型代理 — 见
                    # team_lead_tools.ENDPOINT_MAP 注释)。代码任务按路由表走 qwen3-coder 系。
                    # base_url 取 BAILIAN_BASE_URL(节点已兼容带不带 /v1)。
                    "model": os.environ.get("PRISIRAGENT_DEV_MODEL", os.environ.get("OIAGENT_DEV_MODEL", "qwen3-coder-flash")),
                    "base_url": os.environ.get(
                        "BAILIAN_BASE_URL",
                        "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                    "api_key_env": "BAILIAN_API_KEY",
                    "system": _build_system(),
                    "prompt_selector": "task_content",
                    "output_key": "result",
                    "max_tokens": 4096,
                    "mock_fallback": False,  # 真跑不假 PASS;LLM 不可达则报错→走 retry
                    "stream": True,
                    # 第二层:纠错回流(可选 recall 开关,见 llm.py)
                    "memory_recall": True,
                    "memory_namespace": "dev_lessons",
                    "memory_recall_n": 4,
                },
            },
            {
                "id": "end", "type": "end",
                "config": {"outputs": ["exec.result"]},
            },
        ],
        "edges": [
            {"source": "tq", "target": "exec"},
            {"source": "exec", "target": "end"},
        ],
    }


# ── 落地五:自动沉淀 lesson(打回 → dev_lessons 分格)─────────────
# 自进化里我们能安全拿的那块:把「沉淀」自动化(变异/选择权仍在主会话)。
# 只沉淀【打回】——打回原因是确凿教训;放行不单独立 lesson(避免污染库)。
# clause/pathology 直接取闸门 finding 的 clause/rule_id,天然进对格;幻影打回单列。
# 经 OIMemory.store + dedupe_title 幂等:同 clause+pathology 只留一条(最新覆盖)。
def _record_lesson(title_suffix: str, clause: str, pathology: str, content: str) -> None:
    """把一条打回教训写进 dev_lessons 分格。任何失败静默(记录是增强,不阻塞消费)。"""
    try:
        from memory.oi_memory import OIMemory  # noqa: PLC0415
        tags = ["dev-lesson", "prisir", "auto-sink"]
        if clause:
            tags.append(f"clause:{clause}")
        if pathology:
            tags.append(f"pathology:{pathology}")
        # 当前 consumer 模型(产生该教训时的模型),供跨模型标注
        model = os.environ.get("PRISIRAGENT_DEV_MODEL", os.environ.get("OIAGENT_DEV_MODEL", "qwen3-coder-flash"))
        tags.append(f"model:{model}")
        OIMemory().store(
            layer="L2",
            title=f"自动沉淀·{title_suffix}",
            content=content,
            tags=tags,
            namespace="dev_lessons",
        )
    except Exception as e:  # noqa: BLE001
        log.debug("自动沉淀 lesson 跳过(%s)", e)


def _escalate_to_code(tq, task_id, title: str, content: str, missing: list[str]) -> int | None:
    """落地六:幻影打回 → 升级转 tasks-code 给主会话(缺口2 打回→升级路由)。

    幻影交付(声称改代码但没落盘)是 consumer 做不了的——原地 retry 同一份 prompt 还会幻影
    (606/612 实证)。所以不 retry,改为:取消原单 + 在 tasks-code 重开新单(带原 content +
    幻影教训 + 原 task_id 溯源),由主会话认领真做。返回新 task_id,失败返回 None。
    """
    try:
        import dev_dispatch as _dd  # noqa: PLC0415
        orig = tq.get(int(task_id))
        orig_content = getattr(orig, "content", content) or content
        note = (
            f"[升级自 tasks#{task_id}·幻影打回] consumer 两次落盘校验未过"
            f"(未落实: {', '.join(missing)})。这是改代码任务,consumer 做不了,"
            f"转主会话认领真做。教训已沉淀 pathology:phantom_delivery。\n\n"
        )
        new_id = _dd.submit_code_task(
            title=f"[转]{title}"[:200],
            content=note + orig_content,
            files=_dd.parse_declared_files(orig_content),
            priority=6,  # 升级单略高于普通,优先被主会话看到
        )
        tq.mark_cancelled(int(task_id), reason=f"幻影打回,升级转 tasks-code#{new_id}")
        log.info("task %s 幻影打回 → 升级转 tasks-code#%s(主会话认领)", task_id, new_id)
        return new_id
    except Exception as e:  # noqa: BLE001
        log.error("升级转 tasks-code 失败(%s),退回原地 retry", str(e)[:120])
        return None


def run_one(worker_idx: int) -> bool:
    """消费一个 ready task。返回 True=有处理到 task,False=无 ready(该睡)。"""
    from mcp_prisiragent_v3_plan_parallel.workflow import (  # noqa: PLC0415
        LimitsConfig, WorkflowEntry,
    )
    from mcp_prisiragent_v3_plan_parallel.workflow.events import (  # noqa: PLC0415
        GraphRunFinishedEvent, NodeRunStartedEvent,
    )

    tq = _get_tq()
    started_ts = time.time()  # 兜底校验用:此刻之后声明文件的 mtime 才算「这次真改了」
    entry = WorkflowEntry(
        _build_graph(), query="",
        limits_config=LimitsConfig(max_steps=10, max_wallclock_s=300.0),
    )
    result = entry.run_and_collect()
    # 无 ready task:task_queue_input 节点 raise → GraphRunFailed(不抛),error 含「无 ready」
    task_id = entry.pool.get("task_id")
    if task_id is None:
        return False  # 没拉到 task(无 ready / mark 失败),睡了再试
    title = entry.pool.get("task_id_title", "")

    # 成功标志:有 GraphRunFinished 且 exec 节点产出非空 result
    finished = any(isinstance(e, GraphRunFinishedEvent) for e in result["events"])
    end_out = result.get("end_outputs", {})
    reply = ""
    for k, v in end_out.items():
        if v is not None and str(v).strip():
            reply = str(v)
            break

    if finished and reply:
        # 质量闸门:宪法合规检测。命中 blocker → 打回(increment_retry),不标 done。
        passed, gate = _gate_check(reply)
        if not passed:
            blockers = [f for f in gate["findings"] if f["severity"] == "blocker"]
            reasons = "; ".join(f"{f['rule_id']}({f['clause']})" for f in blockers)
            tq.increment_retry(
                task_id,
                error_msg=f"宪法合规闸门打回: {reasons}",
            )
            log.warning("[w%d] ✗ task %s 宪法闸门打回: %s", worker_idx, task_id, reasons)
            # 落地五:自动沉淀——每条 blocker 的 clause/rule_id 即 clause/pathology,进对格。
            for f in blockers:
                _record_lesson(
                    f"宪法{f['clause']}·{f['rule_id']}",
                    clause=f["clause"], pathology=f["rule_id"],
                    content=(f"task{task_id} 因宪法闸门打回。{f['why']} "
                             f"(任务: {str(title)[:60]})"),
                )
            return True
        # 兜底(落地「2」):若任务 content 声明了「改动文件:...」,真查这些文件是否落盘,
        # 没真改 → 幻影交付,打回。这抓 606/612 那类「报告贴代码、文件没动」。
        try:
            import dev_dispatch as _dd  # noqa: PLC0415
            content = str(entry.pool.get("task_content") or "")
            declared = _dd.parse_declared_files(content)
            if declared:
                ok, missing = _dd.verify_files_touched(declared, started_ts)
                if not ok:
                    log.warning("[w%d] ✗ task %s 幻影交付打回,未落盘: %s",
                                worker_idx, task_id, ", ".join(missing))
                    # 落地五:幻影交付是重要病理(606/612 连发),单列 pathology 沉淀。
                    _record_lesson(
                        "幻影交付·文件未落盘",
                        clause="§5", pathology="phantom_delivery",
                        content=(f"task{task_id} 报告声称改了文件但实际未落盘,"
                                 f"被落盘校验打回。未落实: {', '.join(missing)}。"
                                 f"教训:声称改代码必须真改文件,不是贴代码在报告里。"
                                 f"(任务: {str(title)[:60]})"),
                    )
                    # 落地六:幻影是 consumer 做不了的,原地 retry 同 prompt 还会幻影——
                    # 升级转 tasks-code 给主会话认领。失败才退回原地 retry。
                    new_id = _escalate_to_code(tq, task_id, str(title), content, missing)
                    if new_id is None:
                        tq.increment_retry(
                            task_id,
                            error_msg="幻影交付打回(升级转 tasks-code 失败,原地 retry): " + ", ".join(missing),
                        )
                    return True
        except Exception as _e:  # noqa: BLE001
            log.debug("落盘校验跳过(%s)", _e)  # 校验本身失败不阻塞(增强,非必需)
        tq.mark_done(task_id, result=reply[:20000])
        # 落地八:双闸通过 → 提炼成功模式候选(半自动,师傅挑才入库),补「老员工手感」。
        # 与落地五(打回记错题)对偶:这个记成功做法,但只挂候选不直接入库(防噪声灌库)。
        try:
            import dev_patterns as _dp  # noqa: PLC0415
            _dp.propose_success("consumer", f"tasks#{task_id}", str(title), reply)
        except Exception:  # noqa: BLE001
            pass
        warn_note = ""
        if gate and gate.get("warn_count"):
            warn_note = f" (warn:{gate['warn_count']})"
        log.info("[w%d] ✓ task %s done (%s) 结果 %d 字%s",
                 worker_idx, task_id, str(title)[:30], len(reply), warn_note)
        # 完成信号:写 done-feed,让主会话/管家的 watcher 能「事件驱动」地发现完成,
        # 不用手动轮询 task_queue(对应「协作链健康」缺口:完成了没人知道)。
        _emit_done(task_id, str(title), len(reply), gate)
    else:
        tq.increment_retry(task_id, error_msg="workflow 未产出 result")
        log.warning("[w%d] ✗ task %s 未完成,已记 retry", worker_idx, task_id)
    return True


def _emit_done(task_id, title: str, result_len: int, gate) -> None:
    """任务完成信号:追加一行 JSON 到 done-feed.jsonl(append-only)。

    watcher(主会话 cron / 管家协作链健康插件)消费这个 feed,发现新条目即做
    闸门复检 + 上报,不用轮询 task_queue。只记摘要(task_id/title/len/gate 结论),
    不记 content 全文(审计脱敏红线)。任何失败静默(完成信号是增强,不阻塞消费)。
    """
    try:
        import json as _json
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        feed = LOG_DIR / "prisiragent_dev_done_feed.jsonl"
        rec = {
            "task_id": task_id,
            "title": title[:60],
            "result_len": result_len,
            "gate_verdict": (gate or {}).get("verdict"),
            "gate_warn": (gate or {}).get("warn_count", 0),
            "ts": time.time(),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with open(feed, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _worker_loop(worker_idx: int, idle_sleep: float) -> None:
    log.info("[w%d] worker 启动", worker_idx)
    while not _STOP.is_set():
        try:
            did = run_one(worker_idx)
        except Exception as e:  # noqa: BLE001
            log.error("[w%d] worker 循环异常: %s", worker_idx, str(e)[:200])
            did = False
        if not did:
            _STOP.wait(idle_sleep)  # 无 ready task,睡了再试


_STOP = threading.Event()


def _pid_alive(pid: int) -> bool:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue) -ne $null"],
            capture_output=True, text=True, timeout=10)
        return "True" in r.stdout
    except Exception:  # noqa: BLE001
        return False


def _acquire_singleton() -> bool:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            old = int(LOCK_FILE.read_text().strip())
            if old != os.getpid() and _pid_alive(old):
                return False
        except Exception:  # noqa: BLE001
            pass
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def run_forever(workers: int, idle_sleep: float) -> None:
    if not _acquire_singleton():
        print("已有 dev-consumer 实例在跑(单实例锁),退出", flush=True)
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)
    # 落地三:内核篡改自检 —— 内核被改则拒绝消费(判分基准不可由被测对象改动)
    if not _verify_kernel():
        log.error("内核校验失败(constitution_compliance/constitution 被篡改),拒绝启动消费")
        print("内核校验失败,拒绝启动。详见日志。", flush=True)
        return
    log.info("prisiragent dev-consumer 启动 workers=%d idle_sleep=%.0fs (内核校验通过)", workers, idle_sleep)
    threads = [
        threading.Thread(target=_worker_loop, args=(i, idle_sleep), daemon=True)
        for i in range(workers)
    ]
    for t in threads:
        t.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("收到中断,停止")
        _STOP.set()


def install(workers: int) -> None:
    script = os.path.abspath(__file__)
    pyw = str(Path(sys.executable).with_name("pythonw.exe"))
    cmd = (f'schtasks /Create /TN "{TASK_NAME}" /F '
           f'/TR "\\"{pyw}\\" \\"{script}\\" --workers {workers}" '
           f'/SC ONLOGON /RL LIMITED')
    r = subprocess.run(cmd, shell=True, capture_output=True)
    out = (r.stdout or r.stderr).decode("gbk", errors="replace")
    print(out)
    if r.returncode == 0:
        print(f"已注册开机自启 '{TASK_NAME}'。手动触发: schtasks /Run /TN {TASK_NAME}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=int(os.environ.get("PRISIRAGENT_DEV_WORKERS", os.environ.get("OIAGENT_DEV_WORKERS", "3"))))
    ap.add_argument("--idle-sleep", type=float, default=20.0)
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--install", action="store_true")
    args = ap.parse_args()

    if args.install:
        install(args.workers)
    elif args.daemon:
        DETACHED = 0x00000008 | 0x00000200
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        dlog = open(LOG_DIR / "dev_consumer_daemon.out.log", "ab")
        pyw = str(Path(sys.executable).with_name("pythonw.exe"))
        subprocess.Popen([pyw, os.path.abspath(__file__), "--workers", str(args.workers)],
                         creationflags=DETACHED, stdout=dlog, stderr=subprocess.STDOUT)
        print(f"dev-consumer 已后台启动(workers={args.workers})")
    else:
        run_forever(args.workers, args.idle_sleep)


if __name__ == "__main__":
    main()
