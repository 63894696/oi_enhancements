#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+B-4(2026-09-21)AI agent 任务执行派单 — 静态锚点扫。
覆盖:
  - cli 端 TOOLS 里 run_task 工具定义(name / description / dag.additionalProperties / wait / required)
  - cli 端 handler `if name == "run_task"` + 子代工具集剔除 run_task
  - cli 端 _t_run_task:urllib.request / task.upsert / task.run / PRISIR_WEB_PORT / dag 浅校验
  - web 端 _TASK_RUN_TO_SID / _TASK_RUN_TO_TASK / _TASK_RUN_LOCK 模块级 + RLock
  - web 端 _task_run_register / _task_run_push_progress / _task_run_poll_done 三函数
  - web 端 _ext_run_progress 末尾调 _task_run_push_progress
  - web 端 _run_chat_thread 末尾识别 `nm == "run_task"` + 正则 "run_id"
  - 关键字面量:`🔀 task-runner` / `_threading.Thread` / `daemon=True`
  - py_compile 双文件 OK
"""
import os
import re
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CLI = os.path.join(ROOT, "prisIragent_cli.py")
WEB = os.path.join(ROOT, "prisIragent_web.py")


def _read(path):
    return open(path, encoding="utf-8").read()


def section(title):
    print("=" * 60)
    print(f"[{title}]")
    print("=" * 60)


def cli_tools_run_task():
    """cli TOOLS 里 run_task 工具定义。"""
    section("1. cli TOOLS run_task 定义")
    src = _read(CLI)
    # 找 "name": "run_task" 那段 TOOL 定义(必须存在)
    m = re.search(r'\{"type":\s*"function"\s*,\s*"function":\s*\{\s*"name":\s*"run_task".*?\}\s*\}\s*,?\s*\}', src, re.S)
    if not m:
        print("  ✗ run_task tool def NOT FOUND in TOOLS")
        return False
    blob = m.group(0)
    checks = [
        ("name:run_task", '"name": "run_task"' in blob),
        ("description 提到 task-runner / DAG", "task-runner" in blob or "DAG" in blob or "workflow" in blob),
        ("dag 含 additionalProperties", "additionalProperties" in blob),
        ("dag 节点含 ext / method", '"ext"' in blob and '"method"' in blob),
        ("dag 节点 required [ext, method]", '"required"' in blob and '"ext"' in blob and '"method"' in blob),
        ("wait 参数默认 false", '"wait"' in blob and "false" in blob.lower()),
        ("required name + dag", '"required"' in blob and '"name"' in blob and '"dag"' in blob),
        ("提到 ext 列表", "todo" in blob and "calendar" in blob),
        ("提到 retry", "retry" in blob),
    ]
    ok = 0
    for name, hit in checks:
        marker = "✓" if hit else "✗"
        print(f"  {marker} {name}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


def cli_handler_run_task():
    """cli handler 段 if name == "run_task"。"""
    section("2. cli handler run_task + 子代防递归")
    src = _read(CLI)
    checks = [
        ("handler 分支 if name == \"run_task\"", 'if name == "run_task":' in src),
        ("handler 调 _t_run_task", "_t_run_task(" in src),
        ("子代工具集剔除 run_task",
         '"spawn_subagent"' in src and '"run_workflow"' in src and '"run_task"' in src
         and ("not in (" in src or "not in(" in src)),
    ]
    ok = 0
    for name, hit in checks:
        marker = "✓" if hit else "✗"
        print(f"  {marker} {name}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


def cli_run_task_func():
    """cli _t_run_task 函数体:urllib / task.upsert / task.run / PRISIR_WEB_PORT / dag 浅校验。"""
    section("3. cli _t_run_task 函数体")
    src = _read(CLI)
    # 找 def _t_run_task 到下一个 def
    m = re.search(r'def _t_run_task\(args.*?(?=\ndef |\nclass |\Z)', src, re.S)
    if not m:
        print("  ✗ _t_run_task def NOT FOUND")
        return False
    body = m.group(0)
    checks = [
        ("urllib.request 引入", "urllib.request" in body),
        ("urllib.request.Request 调 /api/ext/rpc", "Request(" in body and "/api/ext/rpc" in body),
        ("PRISIR_WEB_PORT 环境变量", "PRISIR_WEB_PORT" in body),
        ("回退 PRISIRAGENT_PORT", "PRISIRAGENT_PORT" in body),
        ("回退默认 18800", "18800" in body),
        ("dag 非空校验", "dag" in body and ("non-empty" in body or "not dag" in body or "dag must" in body or "must be non-empty" in body)),
        ("name required 校验", "name required" in body or "name 不能为空" in body or "name is empty" in body),
        ("每个 node 含 ext + method 校验", "missing ext or method" in body or "missing ext" in body),
        ("调 task.upsert", '"task.upsert"' in body or "'task.upsert'" in body),
        ("调 task.run", '"task.run"' in body or "'task.run'" in body),
        ("wait 参数", "wait" in body),
        ("fire-and-forget 默认", "queued" in body or "fire-and-forget" in body),
        ("返 task_id + run_id + status", "task_id" in body and "run_id" in body and "status" in body),
        ("失败带 error", "error" in body),
        ("JSON 返回 _json.dumps", "_json.dumps" in body or "json.dumps" in body),
    ]
    ok = 0
    for name, hit in checks:
        marker = "✓" if hit else "✗"
        print(f"  {marker} {name}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


def web_globals():
    """web 模块级 _TASK_RUN_TO_SID / _TASK_RUN_TO_TASK / _TASK_RUN_LOCK + RLock。"""
    section("4. web 模块级 _TASK_RUN_* + RLock")
    src = _read(WEB)
    checks = [
        ("_TASK_RUN_TO_SID 模块级 dict", "_TASK_RUN_TO_SID: dict" in src or "_TASK_RUN_TO_SID = {}" in src or "_TASK_RUN_TO_SID: dict =" in src),
        ("_TASK_RUN_TO_TASK 模块级 dict", "_TASK_RUN_TO_TASK" in src),
        ("_TASK_RUN_LOCK = _threading.RLock", "_TASK_RUN_LOCK" in src and "RLock" in src),
    ]
    ok = 0
    for name, hit in checks:
        marker = "✓" if hit else "✗"
        print(f"  {marker} {name}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


def web_functions():
    """web 端三个函数:_task_run_register / _task_run_push_progress / _task_run_poll_done。"""
    section("5. web 三大函数 _task_run_*")
    src = _read(WEB)
    checks = [
        ("_task_run_register def", "def _task_run_register(" in src),
        ("_task_run_register 用 RLock with", "_TASK_RUN_LOCK" in src and "with _TASK_RUN_LOCK" in src),
        ("_task_run_register 启 daemon Thread", "threading.Thread" in src and "_task_run_poll_done" in src and "daemon=True" in src),
        ("_task_run_push_progress def", "def _task_run_push_progress(" in src),
        ("_task_run_push_progress 调 add_message", "add_message(sid, \"tool\"" in src or "add_message(sid,'tool'" in src or 'add_message(sid, "tool"' in src),
        ("_task_run_poll_done def", "def _task_run_poll_done(" in src),
        ("_task_run_poll_done 长轮询", "for _ in range" in src and "_t.sleep(2)" in src or "time.sleep(2)" in src),
        ("_task_run_poll_done 终态 ok/failed/canceled",
         '"ok"' in src and '"failed"' in src and '"canceled"' in src),
        ("_task_run_poll_done 落完结 add_message", '"[🔀 task-runner] run ' in src or "[🔀 task-runner] run " in src),
        ("_task_run_poll_done finally 清理映射", "finally" in src and "_TASK_RUN_TO_SID.pop" in src and "_TASK_RUN_TO_TASK.pop" in src),
    ]
    ok = 0
    for name, hit in checks:
        marker = "✓" if hit else "✗"
        print(f"  {marker} {name}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


def web_bridge():
    """_ext_run_progress 末尾调 _task_run_push_progress。

    注:wfmodal 进度走 _WF_PROGRESS_QUEUE(长轮询 /api/workflow/run_progress),不在这
    条路径上,所以本检查只看 chat 桥。
    """
    section("6. _ext_run_progress 桥接到 _task_run_push_progress")
    src = _read(WEB)
    # 找 _ext_run_progress 函数末尾
    m = re.search(r'def _ext_run_progress\([^)]*\)[^\n]*:\s*\n(.*?)(?=\ndef |\Z)', src, re.S)
    if not m:
        print("  ✗ _ext_run_progress NOT FOUND")
        return False
    body = m.group(1)
    checks = [
        ("函数体内调 _task_run_push_progress", "_task_run_push_progress(" in body),
        ("rid 抽取(run_id)", "rid =" in body or "rid=" in body),
        ("函数体不含 wfmodal 残留(B-4 只做 chat 桥)",
         not re.search(r'_WF_RUN_PROGRESS_HOOKS\s*\[', body)),
        ("reader_loop 调 _ext_run_progress", "_ext_run_progress(ext_id, msg" in src),
    ]
    ok = 0
    for name, hit in checks:
        marker = "✓" if hit else "✗"
        print(f"  {marker} {name}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


def chat_thread_register():
    """_run_chat_thread 末尾识别 run_task + 正则抓 run_id 注册。"""
    section("7. _run_chat_thread tool trace 注册")
    src = _read(WEB)
    checks = [
        ("识别 nm == \"run_task\"", 'nm == "run_task"' in src or "nm=='run_task'" in src),
        ("正则抓 run_id", '"run_id"\\s*:\\s*"([^"]+)"' in src or '"run_id"\\s*:' in src),
        ("正则抓 task_id", '"task_id"' in src),
        ("调 _task_run_register", "_task_run_register(" in src),
        ("加 try/except 防护", "try:" in src and "except Exception" in src),
        ("add_message 落工具轨迹",
         'add_message(sid, "tool"' in src or "add_message(sid,'tool'" in src),
    ]
    ok = 0
    for name, hit in checks:
        marker = "✓" if hit else "✗"
        print(f"  {marker} {name}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


def literals():
    """关键可视化字面量 + daemon Thread 用法。"""
    section("8. 关键字面量")
    src_web = _read(WEB)
    src_cli = _read(CLI)
    checks = [
        ("web 出现 🔀 task-runner", "🔀 task-runner" in src_web),
        ("cli 出现 🔀 task-runner", "🔀 task-runner" in src_cli),
        ("web daemon Thread", "daemon=True" in src_web),
        ("web run_id 字面量", "run_id" in src_web),
        ("cli PRISIR_WEB_PORT 读 env", "PRISIR_WEB_PORT" in src_cli),
        ("web PRISIRAGENT_PORT 读 env (子代 → 父代端口)", "PRISIRAGENT_PORT" in src_web),
    ]
    ok = 0
    for name, hit in checks:
        marker = "✓" if hit else "✗"
        print(f"  {marker} {name}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


def py_compile_both():
    """py_compile OK。"""
    section("9. py_compile 双文件")
    rc1 = subprocess.run([sys.executable, "-m", "py_compile", CLI], capture_output=True, text=True).returncode
    rc2 = subprocess.run([sys.executable, "-m", "py_compile", WEB], capture_output=True, text=True).returncode
    ok1 = rc1 == 0
    ok2 = rc2 == 0
    print(f"  {'✓' if ok1 else '✗'} cli py_compile rc={rc1}")
    print(f"  {'✓' if ok2 else '✗'} web py_compile rc={rc2}")
    return ok1 and ok2


def main():
    sections = [
        cli_tools_run_task(),
        cli_handler_run_task(),
        cli_run_task_func(),
        web_globals(),
        web_functions(),
        web_bridge(),
        chat_thread_register(),
        literals(),
        py_compile_both(),
    ]
    print("=" * 60)
    passed = sum(1 for s in sections if s)
    total = len(sections)
    if passed == total:
        print(f"✓ P2.5+B-4 agent dispatch ALL GREEN ({passed}/{total} sections)")
        return 0
    print(f"✗ P2.5+B-4 agent dispatch FAILED ({passed}/{total} sections)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
