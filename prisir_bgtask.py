# -*- coding: utf-8 -*-
# prisir_bgtask.py — 后台任务 + 定时任务(2026-09-05 P5)
#
# 动机(对齐 Claude Code):
#   - 后台任务:长跑命令(编译/下载/训练)不该阻塞对话。run_shell/run_code 加
#     run_in_background,立即返回 task_id,输出落文件,用 task_output 随时查。
#   - 定时任务:用户说「每分钟看下 X」「5 分钟后提醒我 Y」,登记 cron/一次性任务,
#     到点在后台跑一条 shell,结果落文件可查。
#
# 设计红线:
#   - 纯本地、零 LLM;任务=子进程跑 shell,输出落 $PRISIR_DATA_DIR/bgtask/<id>.log。
#   - 后台任务也过权限闸(run_shell 本就 gated,模型调度它时照常弹卡)——
#     这里只提供「跑」的机制,不绕过确认。
#   - 有上限:并发 _MAX_CONCURRENT、输出文件 _MAX_LOG_BYTES 截断、任务表 _MAX_TASKS。
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

_MAX_CONCURRENT = 8
_MAX_TASKS = 200
_MAX_LOG_BYTES = 1_000_000

_LOCK = threading.Lock()
# task_id -> {id, kind(bg/cron), cmd, status(running/done/error/stopped),
#             started, ended, log, cron(可选), next_fire(可选), one_shot}
_TASKS: dict[str, dict] = {}
_SCHED_STARTED = False
_SCHED_LOCK = threading.Lock()


def _dir() -> Path:
    root = os.environ.get("PRISIR_DATA_DIR") or str(Path.home() / ".local" / "share" / "prisir")
    d = Path(root) / "bgtask"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _running_count() -> int:
    return sum(1 for t in _TASKS.values() if t["status"] == "running")


def _trim() -> None:
    """任务表超上限时丢最旧的已完成任务。"""
    if len(_TASKS) <= _MAX_TASKS:
        return
    done = sorted((t for t in _TASKS.values() if t["status"] != "running"),
                  key=lambda t: t.get("ended") or t["started"])
    for t in done[: max(0, len(_TASKS) - _MAX_TASKS)]:
        _TASKS.pop(t["id"], None)


def _run_capture(task: dict) -> None:
    """在子线程里跑命令,输出落 log 文件(截断防爆)。"""
    logp = task["log"]
    try:
        with open(logp, "wb") as lf:
            proc = subprocess.Popen(task["cmd"], shell=True,
                                    stdout=lf, stderr=subprocess.STDOUT,
                                    cwd=task.get("workdir") or None)
            task["_proc"] = proc
            proc.wait()
            task["status"] = "done" if proc.returncode == 0 else "error"
            task["rc"] = proc.returncode
    except Exception as e:  # noqa: BLE001
        task["status"] = "error"
        try:
            with open(logp, "ab") as lf:
                lf.write(f"\n[bgtask error] {type(e).__name__}: {e}".encode())
        except Exception:  # noqa: BLE001
            pass
    finally:
        task["ended"] = time.time()
        task.pop("_proc", None)
        _truncate_log(logp)


def _truncate_log(logp: str) -> None:
    try:
        if os.path.getsize(logp) > _MAX_LOG_BYTES:
            with open(logp, "rb") as f:
                f.seek(-_MAX_LOG_BYTES // 2, os.SEEK_END)
                tail = f.read()
            with open(logp, "wb") as f:
                f.write(b"[...truncated...]\n" + tail)
    except Exception:  # noqa: BLE001
        pass


def start_background(cmd: str, workdir: str = "") -> str:
    """登记并启动一个后台 shell 任务。立即返回 task_id 描述串。"""
    cmd = (cmd or "").strip()
    if not cmd:
        return "[background error] empty command"
    with _LOCK:
        if _running_count() >= _MAX_CONCURRENT:
            return f"[background error] 并发上限 {_MAX_CONCURRENT} 已满,先 task_list 看哪些能等"
        tid = uuid.uuid4().hex[:8]
        task = {"id": tid, "kind": "bg", "cmd": cmd, "workdir": workdir,
                "status": "running", "started": time.time(),
                "log": str(_dir() / f"{tid}.log")}
        _TASKS[tid] = task
        _trim()
        threading.Thread(target=_run_capture, args=(task,), daemon=True,
                         name=f"bgtask-{tid}").start()
    return (f"[background started] task_id={tid}\n"
            f"命令已在后台运行。用 task_output(task_id=\"{tid}\") 查看进度/结果。")


def task_output(task_id: str, tail: int = 4000) -> str:
    """读某任务的当前输出 + 状态。"""
    t = _TASKS.get(task_id)
    if not t:
        return f"[task_output error] 未知 task_id: {task_id}"
    out = ""
    try:
        with open(t["log"], "rb") as f:
            data = f.read().decode("utf-8", "replace")
        out = data[-tail:] if len(data) > tail else data
    except Exception as e:  # noqa: BLE001
        out = f"(读输出失败: {e})"
    status = t["status"]
    head = f"[task {task_id} · {status}]"
    if status != "running":
        head += f" rc={t.get('rc', '?')} 用时 {int((t.get('ended') or time.time()) - t['started'])}s"
    return f"{head}\n{out}"


def task_list() -> str:
    """列出全部后台/定时任务。"""
    if not _TASKS:
        return "[task_list] 暂无后台/定时任务"
    lines = []
    for t in sorted(_TASKS.values(), key=lambda x: x["started"], reverse=True)[:30]:
        line = f"- {t['id']} [{t['kind']}] {t['status']}: {t['cmd'][:60]}"
        if t["kind"] == "cron" and t.get("cron"):
            line += f"  (cron '{t['cron']}',下次 {time.strftime('%H:%M:%S', time.localtime(t.get('next_fire', 0)))})"
        lines.append(line)
    return "[task_list]\n" + "\n".join(lines)


def stop_task(task_id: str) -> str:
    """终止一个运行中的任务。"""
    t = _TASKS.get(task_id)
    if not t:
        return f"[stop error] 未知 task_id: {task_id}"
    if t["status"] != "running":
        return f"[stop] 任务已是 {t['status']}"
    proc = t.get("_proc")
    try:
        if proc:
            proc.kill()
        t["status"] = "stopped"
        t["ended"] = time.time()
        return f"[stopped] {task_id}"
    except Exception as e:  # noqa: BLE001
        return f"[stop error] {type(e).__name__}: {e}"


# ─────────────────────────────────────────────────
# cron:5 字段(分 时 日 月 周),本地时间;或 delay 分钟一次性。
# ─────────────────────────────────────────────────
def _parse_cron_field(field: str, lo: int, hi: int) -> set[int] | None:
    """解析单个 cron 字段为取值集合;'*' 返回 None(任意)。支持 * 、 */n 、 a 、 a,b 、 a-b。"""
    field = field.strip()
    if field == "*":
        return None
    vals: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if part.startswith("*/"):
            step = int(part[2:])
            vals.update(range(lo, hi + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            vals.update(range(int(a), int(b) + 1))
        else:
            vals.add(int(part))
    return {v for v in vals if lo <= v <= hi}


def _cron_matches(expr: str, tm: time.struct_time) -> bool:
    """5 字段 cron 是否命中当前本地时间。"""
    parts = expr.split()
    if len(parts) != 5:
        return False
    try:
        minute = _parse_cron_field(parts[0], 0, 59)
        hour = _parse_cron_field(parts[1], 0, 23)
        dom = _parse_cron_field(parts[2], 1, 31)
        month = _parse_cron_field(parts[3], 1, 12)
        dow = _parse_cron_field(parts[4], 0, 6)  # 0=周日
    except Exception:  # noqa: BLE001
        return False
    # time 的 tm_wday:周一=0;cron:周日=0 → 转换
    cron_dow = (tm.tm_wday + 1) % 7
    return ((minute is None or tm.tm_min in minute)
            and (hour is None or tm.tm_hour in hour)
            and (dom is None or tm.tm_mday in dom)
            and (month is None or tm.tm_mon in month)
            and (dow is None or cron_dow in dow))


def schedule_cron(cmd: str, cron: str = "", delay_minutes: float = 0,
                  workdir: str = "") -> str:
    """登记定时任务。cron 5 字段 或 delay_minutes 一次性。返回 task_id。"""
    cmd = (cmd or "").strip()
    if not cmd:
        return "[cron error] empty command"
    cron = (cron or "").strip()
    if not cron and not delay_minutes:
        return "[cron error] 需给 cron 表达式或 delay_minutes"
    if cron and len(cron.split()) != 5:
        return "[cron error] cron 需 5 字段(分 时 日 月 周)"
    tid = uuid.uuid4().hex[:8]
    task = {"id": tid, "kind": "cron", "cmd": cmd, "workdir": workdir,
            "status": "running", "started": time.time(),
            "log": str(_dir() / f"{tid}.log"),
            "cron": cron, "one_shot": bool(delay_minutes and not cron),
            "next_fire": time.time() + delay_minutes * 60 if delay_minutes else 0}
    with _LOCK:
        _TASKS[tid] = task
        _trim()
    _ensure_scheduler()
    desc = f"一次性 {delay_minutes} 分钟后" if task["one_shot"] else f"cron '{cron}'"
    return f"[cron registered] task_id={tid}({desc})\n到点后台执行,用 task_output 查结果。"


def _ensure_scheduler() -> None:
    """惰性启动调度线程(每分钟扫一次 cron 表 + 检查一次性任务)。"""
    global _SCHED_STARTED
    with _SCHED_LOCK:
        if _SCHED_STARTED:
            return
        _SCHED_STARTED = True
        threading.Thread(target=_scheduler_loop, daemon=True,
                         name="bgtask-scheduler").start()


def _scheduler_loop() -> None:
    while True:
        time.sleep(30)  # 30s 粒度扫(cron 分钟级足够,且省 CPU)
        now = time.time()
        tm = time.localtime(now)
        for t in list(_TASKS.values()):
            if t["kind"] != "cron" or t["status"] != "running":
                continue
            try:
                if t["one_shot"]:
                    if now >= t.get("next_fire", 0):
                        _fire_cron(t)
                        t["status"] = "done"  # 一次性跑完即完成
                        t["ended"] = now
                elif t.get("cron"):
                    nf = t.get("next_fire", 0)
                    if now >= nf and _cron_matches(t["cron"], tm):
                        _fire_cron(t)
                        t["next_fire"] = now + 60  # 下一分钟再判
                    elif nf == 0:
                        t["next_fire"] = now + 60
            except Exception:  # noqa: BLE001
                pass


def _fire_cron(t: dict) -> None:
    """到点执行 cron 命令(追加写同一 log)。"""
    try:
        with open(t["log"], "ab") as lf:
            lf.write(f"\n=== fire {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode())
            subprocess.run(t["cmd"], shell=True, stdout=lf,
                           stderr=subprocess.STDOUT, cwd=t.get("workdir") or None,
                           timeout=600)
        _truncate_log(t["log"])
    except Exception as e:  # noqa: BLE001
        try:
            with open(t["log"], "ab") as lf:
                lf.write(f"[cron fire error] {e}\n".encode())
        except Exception:  # noqa: BLE001
            pass
