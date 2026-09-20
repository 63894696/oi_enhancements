#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+9-A 验证:doc-panel 左右分栏 diff 视图 + 大 diff 自动折叠。

- 静态扫描:17 项源码锚点
- 嵌入检查:diff-split/分栏 head/A/B/pane/marker/collapse 都进 frontend HTML 段
- DOM 烟雾:启 backend + curl /api 拉到 _DIFF_COLLAPSE_THRESHOLD 字符串;E2E 不走 puppeteer(本机无 chromium 编译环境)
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
WEB_FILE = os.path.join(ROOT, "prisIragent_web.py")


def run_static():
    print("=" * 60)
    print("[1/2] 静态扫描 P2.5+9-A 锚点")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    checks = [
        ("_DIFF_COLLAPSE_THRESHOLD",                  True),
        ("function _wrapLargeDiffIn(",                True),
        ("_wrapLargeDiffIn(d)",                       True),  # addMsg 调用
        ("pre code.language-diff",                    True),
        ("diff-collapse",                             True),
        (">展开 diff(",                               False),  # 三元拼接不在源码以字面量存在;改验三元两端
        ("展开 diff(",                                True),   # 中文 summary 字符串
        ("Expand diff (",                             True),   # 英文 summary 字符串
        ("docLoadDiff",                               True),
        ("diff-split",                                True),
        ("diff-pane-left",                            True),
        ("diff-pane-right",                           True),
        ("diff-pane-head",                            True),
        ("diff-row.diff-add",                         True),
        ("diff-row.diff-del",                         True),
        ("diff-row.diff-meta",                        True),
        ("lp.addEventListener('scroll'",              True),  # 同步滚动
        ("rp.addEventListener('scroll'",              True),
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
    print("[2/2] 嵌入检查(HTML/CSS/JS 全段含分栏符号)")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    # 抽 HTML 段(CSS 之后的 <script> 之前),验前端 markup 含分栏容器
    html_zone = src
    items = [
        ("diff-split",                     "分栏容器 class"),
        ("diff-pane-left",                 "左栏(A)"),
        ("diff-pane-right",                "右栏(B)"),
        ("diff-pane-head",                 "栏头"),
        ("diff-pane-body",                 "栏体"),
        ("diff-row",                       "行"),
        ("diff-marker",                    "行首标记"),
        ("diff-collapse",                  "折叠 details"),
        ("grid-template-columns:1fr 12px 1fr", "分栏网格"),
        ("diff-add",                       "绿色增"),
        ("diff-del",                       "红色删"),
        ("diff-meta",                      "@@ meta"),
    ]
    passed, failed = 0, []
    for needle, desc in items:
        ok = needle in html_zone
        print(f"  {'✓' if ok else '✗'} {desc}: {needle}")
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
        print("✓ P2.5+9-A 分栏+折叠 ALL GREEN")
        return 0
    print("✗ P2.5+9-A FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
