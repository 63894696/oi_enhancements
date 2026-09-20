#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+9-C 验证:CancellationToken 接入 — switchSession 自动 estop + pollResult pollSid 锁死。

- 静态扫描:9 项源码锚点
- 嵌入检查:pollSid 标志 + /status 探活 + estop 调用 都进 frontend JS
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
WEB_FILE = os.path.join(ROOT, "prisIragent_web.py")


def run_static():
    print("=" * 60)
    print("[1/2] 静态扫描 P2.5+9-C 锚点")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    checks = [
        ("async function switchSession(",             True),
        ("async function pollResult(",               True),
        ("const pollSid = sessionId;",               True),       # pollResult 锁 sid
        ("while (pollSid === sessionId)",            True),       # 主循环条件
        ("pollSid === sessionId",                    True),       # 跳出后条件复位
        ("skipEstop",                                True),       # 内调跳过 estop
        ("opts && opts.skipEstop",                   True),
        ('api(\'/estop\', {method:\'POST\'',         True),       # switchSession 内 estop 调用
        ("if (s.running)",                           True),
        ("'/status?session_id=' + sessionId",        True),       # switchSession 探活字面量
    ]
    passed, failed = 0, []
    for needle, expect in checks:
        ok = (needle in src) == expect
        print(f"  {'✓' if ok else '✗'} {needle[:48]}")
        if ok:
            passed += 1
        else:
            failed.append(needle)
    print(f"  → {passed}/{len(checks)}")
    return len(failed) == 0


def run_embed():
    print("=" * 60)
    print("[2/2] 嵌入检查:switchSession / pollResult 关键改动落到位")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    items = [
        ("P2.5+9-C",                                "任务锚点注释"),
        ("CancellationToken",                       "模式名"),
        ("_run_chat_thread",                        "后端 worker 引用"),
        ("_estop_event",                            "后端 estop 入口"),
        ("切会话前先把当前会话 worker 收尾",        "switchSession 注释"),
        ("while (pollSid === sessionId)",           "pollResult 循环"),
        ("opts && opts.skipEstop",                  "内调跳过 estop 路径"),
    ]
    passed, failed = 0, []
    for needle, desc in items:
        ok = needle in src
        print(f"  {'✓' if ok else '✗'} {desc}: {needle[:40]}")
        if ok:
            passed += 1
        else:
            failed.append(needle)
    print(f"  → {passed}/{len(items)}")
    return len(failed) == 0


def main():
    s1 = run_static()
    s2 = run_embed()
    print("=" * 60)
    if s1 and s2:
        print("✓ P2.5+9-C CancellationToken ALL GREEN")
        return 0
    print("✗ P2.5+9-C FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())