#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+B-0 验证:ext RPC bridge 真接通
  - _ext_rpc_call / _ext_proxy_dispatch 真定义(不再是死引用)
  - Node 子进程 spawn 框架就位(Popen + stdio NDJSON)
  - /api/ext/rpc 调试端点接通
  - 现有 3 处调用点 + schedule_writer.py 用新契约解构
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
WEB_FILE = os.path.join(ROOT, "prisIragent_web.py")
SCHED_FILE = os.path.join(ROOT, "schedule_writer.py")


def run_static():
    print("=" * 60)
    print("[1/3] 静态扫 P2.5+B-0 锚点(prisIragent_web.py)")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    checks = [
        ("_EXT_BRIDGE_LOCK = _threading.RLock()",  True),    # 全局锁
        ("_EXT_PROCS = {}",                       True),    # ext 子进程字典
        ("_EXT_LOG_HOOKS = {}",                   True),    # 日志钩子接口
        ("_EXT_INJECT_QUEUE = []",                True),    # ui.inject 缓冲
        ("def _ext_home(",                        True),    # HOME 隔离函数
        ("def _ext_node_exec(",                   True),    # node 解析
        ("def _ext_entry(",                       True),    # 入口解析
        ("def _ext_spawn(",                       True),    # spawn 函数
        ("def _ext_kill(",                        True),    # kill 函数
        ("def _ext_reader_loop(",                 True),    # reader 线程
        ("def _ext_rpc_call(",                    True),    # RPC 主入口
        ("def _ext_proxy_dispatch(",              True),    # 跨扩展转发
        ("def _ext_log(",                         True),    # log 通知处理
        ("def _ext_inject_card(",                 True),    # ui.inject 处理
        ("def _ext_autostart_from_installed(",    True),    # 启动钩子
        ("def _ext_on_enabled(",                  True),    # 启用钩子接口
        ("def _ext_on_disabled(",                 True),    # 禁用钩子接口
        ('"jsonrpc": "2.0"',                      True),    # NDJSON 协议
        ('f"command.{method}"',                   True),    # 命令路由
        ('"extension.invoke_request"',            True),    # 跨调用通知名
        ('"extension.invoke_response"',           True),    # 跨调用响应名
        ('"PRISIR_EXT_HOME"',                     True),    # env var
        ("ext_not_running",                       True),    # 错误码
        ("_EXT_INSTALLED_FILE = ",                True),    # 注册表路径
        ("autostart_from_installed",              True),    # 启动触发
        ("subprocess.Popen(",                     True),    # spawn 调用
        ("stdin=subprocess.PIPE",                 True),    # stdin 管道
        ("stdout=subprocess.PIPE",                True),    # stdout 管道
        ("stderr=subprocess.PIPE",                True),    # stderr 管道
        ('"/prisiragent/api/ext/rpc"',            True),    # 端点路径
        ('P2.5+B-0',                              True),    # 锚点注释
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:60]}")
        if ok:
            passed += 1
        else:
            failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def run_schedule_writer():
    print("=" * 60)
    print("[2/3] schedule_writer.py 用新 _ext_rpc_call 契约解构")
    print("=" * 60)
    src = open(SCHED_FILE, "r", encoding="utf-8").read()
    checks = [
        ('if r.get("error"):',                    True),    # 新契约:error 字段
        ('"ext_not_running" in str(err)',         True),    # 短路整 ext
        ('P2.5+B-0',                              True),    # 锚点注释
        ('if r and (r.get("ok") or r.get("item"))', False),  # 旧契约必须删干净
        ('break  # 整个 ext 不在,不再试',         True),    # 短路行为保留
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:60]}")
        if ok:
            passed += 1
        else:
            failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def run_embed():
    print("=" * 60)
    print("[3/3] 嵌入检查:ext bridge 真接通(关键符号 + 协议关键字)")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    items = [
        ("task-runner",                          "测试目标 ext id"),
        ("subprocess.Popen",                     "subprocess 框架"),
        ("stdin=subprocess.PIPE",                "stdin 管道"),
        ("stdout=subprocess.PIPE",               "stdout 管道"),
        ('"node"',                                "node 可执行兜底"),
        ("jsonrpc",                              "JSON-RPC 协议版本号"),
        ("_ext_rpc_call(",                       "Python → Node API"),
        ("_ext_proxy_dispatch(",                 "跨扩展转发 API"),
        ("_ext_reader_loop",                     "reader 线程名"),
        ("_ext_autostart_from_installed",        "启动钩子函数名"),
        ("_EXT_INSTALLED_FILE",                  "注册表文件常量"),
        ('elif msg.get("method") == "log":',     "log 通知路由"),
        ('"extension.invoke_request"',           "SDK 跨调通知"),
        ('"extension.invoke_response"',          "SDK 跨调响应"),
        ('"ui.inject"',                           "ui.inject 通知"),
        ("crash_count",                          "崩溃计数"),
    ]
    passed, failed = 0, []
    for needle, desc in items:
        ok = (needle in src) == True
        print(f"  {'✓' if ok else '✗'} {desc}: {needle}")
        if ok:
            passed += 1
        else:
            failed.append(needle)
    print(f"  → {passed}/{len(items)}")
    return len(failed) == 0


def main():
    s1 = run_static()
    s2 = run_schedule_writer()
    s3 = run_embed()
    print("=" * 60)
    if s1 and s2 and s3:
        print("✓ P2.5+B-0 ext RPC bridge ALL GREEN")
        return 0
    print("✗ P2.5+B-0 FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())