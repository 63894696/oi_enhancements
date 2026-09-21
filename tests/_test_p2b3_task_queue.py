#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+B-3 验证:Python 后端任务队列升级(优雅取消 + 进度流 + 持久化)
  - task-runner:AbortController / activeRuns Map / node_runs 表
  - task-runner:task.run.cancel / task.run.active / task.run.progress 通知
  - SDK:ext.notify 暴露
  - Python:_WF_PROGRESS_QUEUE / _ext_run_progress / reader_loop 路由
  - Python:/api/workflow/run_progress 端点
  - 前端:wf-progress HTML / progress bar CSS / 节点色 / cancel 按钮
  - 前端 JS:wfStartProgressPoll / wfPollProgress / wfOnProgress / wfCancelRun
  - 前端 i18n:wf_cancel / wf_canceled / wf_progress / wf_active
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
WEB_FILE = os.path.join(ROOT, "prisIragent_web.py")
TASK_FILE = os.path.join(ROOT, "extensions", "task-runner", "index.js")
SDK_FILE = os.path.join(ROOT, "extensions", "sdk", "index.js")


def run_task_runner():
    print("=" * 60)
    print("[1/5] task-runner ext:AbortController + node_runs + cancel/progress")
    print("=" * 60)
    src = open(TASK_FILE, "r", encoding="utf-8").read()
    checks = [
        ("CREATE TABLE IF NOT EXISTS node_runs",       True),  # 新表
        ("node_runs_run_idx",                          True),  # 索引
        ("run_id,node_id,status,attempts,ms",          True),  # 字段
        ("function _notifyProgress(",                  True),  # 推送 helper
        ("ext.notify('task.run.progress'",             True),  # 通知方法
        ("_activeRuns.get(runId)?.abort",              True),  # 取 Ac
        ("AbortController",                            True),  # Ac 构造
        ("new AbortController()",                      True),  # new Ac
        ("const _activeRuns = new Map()",              True),  # activeRuns Map
        ("_activeRuns.set(runId",                      True),  # 注册
        ("_activeRuns.delete(runId",                   True),  # 清
        ("_activeRuns.size",                           True),  # scheduler 统计
        ("task.run.cancel",                            True),  # 取消命令
        ("task.run.active",                            True),  # 活动列表命令
        ("INSERT OR REPLACE INTO node_runs",           True),  # 持久化
        ("status: 'running'",                          True),  # 进度起点
        ("status: 'canceled'",                         True),  # 取消终点
        ("ac?.signal.aborted",                          True),  # 取消检测
        ("if (lastErr === 'aborted' || attempts",      True),  # 取消不重试
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:55]}")
        if ok: passed += 1
        else: failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def run_sdk():
    print("=" * 60)
    print("[2/5] SDK:ext.notify 通道暴露 + B-0 历史前缀剥除")
    print("=" * 60)
    src = open(SDK_FILE, "r", encoding="utf-8").read()
    checks = [
        ("notify(method, params) {",                  True),  # 公开方法
        ("this._notify(method, params);",             True),  # 调用内部
        ("notify(method, params)",                    True),  # 锚点
        # B-3 烟雾发现的历史 bug:B-0 ship 时 Python 给 method 加 'command.' 前缀,
        # SDK 一直没剥,所有真实命令返 'unknown method'。修法见 _handleRequest。
        ("startsWith('command.')",                    True),  # 前缀检测
        ("rawMethod.slice('command.'.length)",        True),  # 剥前缀
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:55]}")
        if ok: passed += 1
        else: failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def run_python_backend():
    print("=" * 60)
    print("[3/5] Python 后端:_WF_PROGRESS_QUEUE + reader_loop + run_progress 端点")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    checks = [
        ("_WF_PROGRESS_QUEUE",                         True),  # 队列常量
        ("_WF_PROGRESS_LOCK",                          True),  # 锁
        ('msg.get("method") == "task.run.progress"',  True),  # 路由分支
        ("_ext_run_progress(ext_id",                   True),  # 函数
        ("_ext_run_progress(ext_id: str, params: dict)", True),  # 签名
        ("def _ext_run_progress(",                    True),  # 函数存在
        ("run_progress",                               True),  # 端点
        ("\"run_id\": run_id",                         True),  # 入参解析
        ("missing",                                    True),  # 返 missing 标记
        ("_WF_PROGRESS_QUEUE",                         True),  # 锚点
        ("_WF_PROGRESS_LOCK",                          True),  # 锚点
        ("P2.5+B-3",                                   True),  # 注释锚点
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:55]}")
        if ok: passed += 1
        else: failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def run_html_css():
    print("=" * 60)
    print("[4/5] 前端 HTML / CSS:进度条 + 取消按钮 + 节点色")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    checks = [
        ('id="wf-progress"',                           True),  # 进度条容器
        ('id="wf-progress-bar"',                       True),  # 进度槽
        ('id="wf-progress-bar-fill"',                  True),  # 进度填充
        ('id="wf-progress-text"',                      True),  # 文本
        ('id="wf-cancel-btn"',                         True),  # 取消按钮
        ('onclick="wfCancelRun()"',                    True),  # 取消 handler
        ('data-i18n="wf_cancel"',                      True),  # i18n
        ('#wf-progress {',                             True),  # 进度 CSS
        ('#wf-progress-bar {',                         True),  # 槽 CSS
        ('#wf-progress-bar-fill {',                    True),  # 填充 CSS
        ('#wf-progress-bar-fill.running',              True),  # amber
        ('#wf-progress-bar-fill.canceled',             True),  # red
        ('#wf-cancel-btn {',                           True),  # 取消按钮 CSS
        ('#wf-cancel-btn:hover',                       True),  # hover
        ('.wf-node.canceled',                          True),  # 节点 canceled 色
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:55]}")
        if ok: passed += 1
        else: failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def run_js_i18n():
    print("=" * 60)
    print("[5/5] 前端 JS / i18n:进度轮询 + 取消函数 + B-3 i18n 键")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    # JS 函数 / 全局
    js_checks = [
        ("function wfStartProgressPoll(",              True),
        ("function wfStopProgressPoll(",               True),
        ("async function wfPollProgress(",             True),
        ("async function wfMaybeFinishRun(",           True),
        ("function wfOnProgress(",                     True),
        ("async function wfCancelRun(",                True),
        ("function wfOnRunDone(",                      True),
        ("_wfCurrentRunId",                            True),
        ("_wfPollTimer",                               True),
        ("_wfProgressLastSeq",                         True),
        ("_wfProgressTotal",                           True),
        ("wfRunById(_wfCurrentTask.id, false)",        True),  # wfRun 接 fire-and-forget
        ("wfStopProgressPoll()",                       True),  # 关 modal 清
    ]
    # i18n zh + en
    i18n_checks = [
        ("wf_cancel:'⏹ 取消当前运行'",                 True),
        ("wf_canceled:'已取消'",                       True),
        ("wf_progress:'进度'",                         True),
        ("wf_active:'当前运行'",                       True),
        ("wf_cancel:'⏹ Cancel current run'",           True),
        ("wf_canceled:'Canceled'",                     True),
        ("wf_progress:'Progress'",                     True),
        ("wf_active:'Active run'",                     True),
    ]
    all_checks = js_checks + i18n_checks
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
    s2 = run_sdk()
    s3 = run_python_backend()
    s4 = run_html_css()
    s5 = run_js_i18n()
    print("=" * 60)
    if s1 and s2 and s3 and s4 and s5:
        print("✓ P2.5+B-3 任务队列升级 ALL GREEN")
        return 0
    print("✗ P2.5+B-3 FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())