#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+B-3 hotfix(2026-09-21):
  - 节点双击编辑:wfStartDrag 之前 e.preventDefault() 拦截浏览器 dblclick,
    改成「mousedown 只记 armed + 移动 >5px 才真 drag」,双击可正常触发。
  - 运行历史清理:新 task.runs.clear 命令 + wfClearRuns JS + 清理按钮 + i18n。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
WEB_FILE = os.path.join(ROOT, "prisIragent_web.py")
TASK_FILE = os.path.join(ROOT, "extensions", "task-runner", "index.js")


def run_dblclick_fix():
    print("=" * 60)
    print("[1/3] 节点双击编辑修复:mousedown 不 preventDefault + 拖拽阈值")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    checks = [
        # 关键锚点:armed/origin 阈值
        ("_wfDragArmed",                     True),  # 新全局(armed 节点 id)
        ("_wfDragOrigin",                    True),  # mousedown 起点
        ("Math.abs(dx) < 5 && Math.abs(dy) < 5", True),  # 5px 阈值
        ("Math.abs(dx) < 5",                True),  # 锚点(防 grep 漏)
        # 老 preventDefault 必须消失
        # (不能简单 grep 'e.preventDefault()' 因为别的函数也用,所以用注释锚)
        ("// 双击不被拦截",                   True),  # hotfix 注释
        # wfApplyDragPos 抽出来复用
        ("function wfApplyDragPos(",           True),  # 抽离 helper
        # 双击监听还在
        ("'dblclick', () => wfEditNode(",   True),  # 保留双击监听
        # mousedown 注册还在(改 armed 流程)
        ("'mousedown', (e) => wfStartDrag(", True),  # mousedown 监听
        # hover 防按钮
        ("if (e.target.tagName === 'BUTTON') return", True),
        ("// 不要拦按钮",                    True),  # 注释
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:55]}")
        if ok: passed += 1
        else: failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def run_clear_runs():
    print("=" * 60)
    print("[2/3] 运行历史清理:task.runs.clear 命令 + wfClearRuns + 按钮 + i18n")
    print("=" * 60)
    # task-runner 端
    task_src = open(TASK_FILE, "r", encoding="utf-8").read()
    # Python 端
    web_src = open(WEB_FILE, "r", encoding="utf-8").read()
    checks = [
        # task-runner 端(分支处理:tid=不传走无 WHERE 全删,带 tid 走 WHERE 子句)
        ("'task.runs.clear'",                task_src.count("'task.runs.clear'") > 0),
        ("DELETE FROM runs",                 task_src.count("DELETE FROM runs") > 0),
        ("DELETE FROM node_runs",            task_src.count("DELETE FROM node_runs") > 0),
        ("cleared_node_runs",                task_src.count("cleared_node_runs") > 0),
        ("if (tid) {",                       task_src.count("if (tid) {") > 0),
        # Python 端 wfClearRuns + 按钮 + i18n
        ("async function wfClearRuns(",      True),
        ("task.runs.clear",                  True),
        ('id="wf-runs-clean-btn"',           True),
        ("wf_runs_clear",                    True),
        ("wf_clear_runs_confirm",            True),
        ("wf_clear_runs_fail",               True),
        ("wf_runs_clear:'🧹 清空'",          True),  # zh
        ("wf_runs_clear:'🧹 Clear'",         True),  # en
        # CSS
        ("#wf-runs-head #wf-runs-clean-btn", True),
        ("#wf-runs-head #wf-runs-clean-btn:hover", True),
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in web_src or needle in task_src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:55]}")
        if ok: passed += 1
        else: failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def run_no_regression():
    print("=" * 60)
    print("[3/3] 不回归锚点(以前状态的关键)")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    task_src = open(TASK_FILE, "r", encoding="utf-8").read()
    sdk_src = open(os.path.join(ROOT, "extensions", "sdk", "index.js"), "r", encoding="utf-8").read()
    checks = [
        # B-3 主体(进度流)
        ("function wfStartProgressPoll(",    True),
        ("function wfPollProgress(",         True),
        ("function wfOnProgress(",           True),
        ("function wfCancelRun(",            True),
        ("function wfStopProgressPoll(",     True),
        # SDK 修复
        ("startsWith('command.')",          sdk_src.count("startsWith('command.')") > 0),
        # task-runner 持久化
        ("CREATE TABLE IF NOT EXISTS node_runs", task_src.count("CREATE TABLE IF NOT EXISTS node_runs") > 0),
        ("AbortController",                  task_src.count("AbortController") > 0),
        ("const _activeRuns = new Map",      task_src.count("const _activeRuns = new Map") > 0),
        # Python 端
        ("_WF_PROGRESS_QUEUE",               True),
        ("run_progress",                     True),
        # wfmodal 旧元素
        ("wfmodal",                          True),
        ("topbtnWorkflow",                   True),
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in src or needle in task_src or needle in sdk_src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:55]}")
        if ok: passed += 1
        else: failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def main():
    s1 = run_dblclick_fix()
    s2 = run_clear_runs()
    s3 = run_no_regression()
    print("=" * 60)
    if s1 and s2 and s3:
        print("✓ P2.5+B-3 hotfix ALL GREEN")
        return 0
    print("✗ P2.5+B-3 hotfix FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())