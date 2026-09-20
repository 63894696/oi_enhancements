#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+B-2 验证:工作流编排 UI + 后端闭环
  - task-runner 重试/backoff/timeout 落地
  - task.template.list 命令注册
  - /api/workflow/templates 端点 + _WF_FALLBACK_TEMPLATES 兜底
  - 前端 wfmodal HTML/CSS/JS/i18n 关键符号锚点
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
WEB_FILE = os.path.join(ROOT, "prisIragent_web.py")
TASK_FILE = os.path.join(ROOT, "extensions", "task-runner", "index.js")


def run_task_runner():
    print("=" * 60)
    print("[1/4] task-runner ext:重试/backoff/模板/cancel")
    print("=" * 60)
    src = open(TASK_FILE, "r", encoding="utf-8").read()
    checks = [
        ("function _sleep(",                      True),
        ("function _backoffDelay(",               True),
        ("async function executeNodeWithRetry(",  True),
        ("async function executeDag(",            True),
        ("executeNodeWithRetry(nid, dag[nid]",   True),  # 调用点替换
        ("ext.registerCommand('task.run.cancel'", True),
        ("ext.registerCommand('task.template.list'", True),
        ("'task.run.cancel'",                    True),  # 锚点
        ("simple_echo",                          True),  # 模板 1
        ("daily_summary",                        True),  # 模板 2
        ("three_step_demo",                      True),  # 模板 3
        ("max_retries",                          True),  # retry 字段
        ("timeout_sec",                          True),  # retry 字段
        ("'exponential'",                        True),  # backoff 默认
        ("Promise.race([",                        True),  # timeout 用法
        ("Promise.allSettled",                   True),  # DAG 分层
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:55]}")
        if ok: passed += 1
        else: failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def run_endpoint():
    print("=" * 60)
    print("[2/4] Python 后端:/api/workflow/templates + 兜底常量")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    checks = [
        ("/prisiragent/api/workflow/templates",   True),  # 端点路径
        ("task.template.list",                    True),  # 调用命令名
        ("_WF_FALLBACK_TEMPLATES",                True),  # 兜底常量
        ('"templates": _WF_FALLBACK_TEMPLATES',   True),  # 兜底分支片段
        ('_ext_rpc_call("task-runner"',           True),  # RPC 调用
        ("P2.5+B-2",                              True),  # 锚点注释
        ("_ext_rpc_call(",                        True),  # 桥调用
        ("import random",                          True),  # B-0 漏修的 NameError 防护
        ("random.randrange",                      True),  # B-0 真用了
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:55]}")
        if ok: passed += 1
        else: failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def run_html():
    print("=" * 60)
    print("[3/4] 前端 HTML:wfmodal / 节点弹层 / 模板弹层 / 顶栏按钮")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    checks = [
        ('id="wfmodal"',                          True),
        ('id="wf-header"',                        True),
        ('id="wf-body"',                          True),
        ('id="wf-canvas"',                        True),
        ('id="wf-svg"',                           True),
        ('id="wf-nodes"',                         True),
        ('id="wf-edges"',                         True),
        ('id="wf-runs"',                          True),
        ('id="wf-task-list"',                     True),
        ('id="wf-sched-btn"',                     True),
        ('id="wf-node-modal"',                    True),
        ('id="wf-tpl-modal"',                     True),
        ('marker id="wf-arrow"',                  True),  # SVG 箭头
        ('onclick="openWorkflow()"',              True),  # 顶栏按钮
        ('id="topbtnWorkflow"',                   True),  # 顶栏按钮 ID
        ('onclick="wfSave()"',                    True),
        ('onclick="wfRun()"',                     True),
        ('onclick="wfTemplates()"',               True),
        ('onclick="wfValidate()"',                True),
        ('onclick="wfToggleSchedule()"',          True),
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:55]}")
        if ok: passed += 1
        else: failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def run_js_css_i18n():
    print("=" * 60)
    print("[4/4] 前端 JS / CSS / i18n 锚点")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    # JS 关键函数 / 状态
    js_checks = [
        ("async function openWorkflow(",           True),
        ("function closeWorkflow(",               True),
        ("async function wfRenderTaskList(",      True),
        ("async function wfLoadTask(",            True),
        ("function wfNew(",                       True),
        ("async function wfSave(",                True),
        ("async function wfRun(",                 True),
        ("async function wfRunById(",             True),
        ("function wfRenderNodes(",               True),
        ("function wfRenderEdges(",               True),
        ("function wfStartDrag(",                 True),
        ("function wfOnDragMove(",                True),
        ("function wfEditNode(",                  True),
        ("function wfNodeSave(",                  True),
        ("function wfDeleteNode(",                True),
        ("async function wfValidate(",            True),
        ("async function wfDeleteTask(",          True),
        ("async function wfRefreshRuns(",         True),
        ("async function wfRefreshScheduleStatus(", True),
        ("async function wfToggleSchedule(",      True),
        ("async function wfTemplates(",           True),
        ("function wfApplyTpl(",                  True),
        ("_wfCurrentTask",                        True),
        ("_wfNodes",                              True),
        ("_wfDraggingNode",                       True),
        ("createElementNS('http://www.w3.org/2000/svg'", True),  # SVG 创建
        ("marker-end",                            True),  # SVG 箭头属性
    ]
    # CSS 锚点
    css_checks = [
        ("#wfmodal {",                            True),
        ("#wfmodal.open {",                       True),
        ("#wf-canvas {",                          True),
        (".wf-node {",                            True),
        (".wf-node.ok",                           True),
        (".wf-node.failed",                       True),
        (".wf-node.running",                      True),
        ("#wf-runs-body .wf-run",                 True),
        ("#wf-sched-btn.on",                      True),
        (".wf-nm-card",                           True),
    ]
    # i18n keys(中英)
    i18n_checks = [
        ("workflow:'🔀 工作流'",                  True),
        ("wf_new:'+ 新建'",                       True),
        ("wf_templates:'📋 模板'",                True),
        ("wf_validate:'✓ 校验'",                  True),
        ("wf_run:'▶ 运行'",                       True),
        ("wf_node_retry:'重试'",                  True),
        ("workflow:'🔀 Workflow'",                True),  # en
        ("wf_new:'+ New'",                        True),
        ("wf_run:'▶ Run'",                        True),
        ("wf_node_retry:'Retry'",                 True),
    ]
    all_checks = js_checks + css_checks + i18n_checks
    passed, failed = 0, []
    for needle, expect in all_checks:
        ok = (needle in src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:55]}")
        if ok: passed += 1
        else: failed.append(needle)
    print(f"  → {passed}/{len(all_checks)}")
    return len(failed) == 0


def main():
    s1 = run_task_runner()
    s2 = run_endpoint()
    s3 = run_html()
    s4 = run_js_css_i18n()
    print("=" * 60)
    if s1 and s2 and s3 and s4:
        print("✓ P2.5+B-2 工作流编排 ALL GREEN")
        return 0
    print("✗ P2.5+B-2 FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
