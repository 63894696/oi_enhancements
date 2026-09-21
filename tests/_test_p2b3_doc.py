#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+B-3 hotfix(2026-09-21): 文档锚点扫
- docs/workflow-usage-demo-2026-09-21.md 必须存在
- 含 9 个用户痛点修复小节 + 5 分钟上手 + 端到端演示
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOC = os.path.join(ROOT, "docs", "workflow-usage-demo-2026-09-21.md")


def run_doc_exists():
    print("=" * 60)
    print("[1/3] 文档存在 + 篇幅")
    print("=" * 60)
    if not os.path.exists(DOC):
        print(f"  ✗ NOT FOUND: {DOC}")
        return False
    size = os.path.getsize(DOC)
    src = open(DOC, encoding="utf-8").read()
    lines = src.count("\n") + 1
    print(f"  ✓ size={size} bytes lines={lines}")
    print(f"  ✓ path={DOC}")
    return size > 5000 and lines > 100


def run_doc_content():
    print("=" * 60)
    print("[2/3] 文档内容覆盖(用户三大反馈)")
    print("=" * 60)
    src = open(DOC, encoding="utf-8").read()
    # 用户三个反馈:双击编辑失效 / 运行历史清理 / 使用演示文档
    required_sections = [
        ("双击编辑(本 hotfix 修)", "3.1 双击编辑"),  # 双击 fix
        ("🧹 清空(本 hotfix 修)", "5.3"),            # 清空 fix
        ("task.runs.clear", "5.3"),                    # 命令
        ("task.runs.clear", "9 已知坑"),               # 修
        ("column index out of range", "9 已知坑"),      # SQL bug 修
        ("五分钟上手", "1 五分钟上手"),                 # 演示
        ("简单 todo 计数", "7 端到端最小演示"),         # e2e demo
        ("测试取消", "7.4"),                           # cancel demo
        ("测试清空", "7.5"),                           # clear demo
        ("task.run.cancel", "4.4 ⏹ 取消"),            # cancel 文档化
        ("进度条", "4.1"),                            # 进度条
        ("AbortController", "9 已知坑"),                # abort 文档化
        ("任务列表", "1.1"),                            # 任务列表锚点
        ("daily_summary", "1.3"),                       # 模板演示
    ]
    passed_count = 0
    for needle, where in required_sections:
        ok = needle in src
        marker = "✓" if ok else "✗"
        print(f"  {marker} {needle[:50]}  ({where})")
        if ok: passed_count += 1
    print(f"  → {passed_count}/{len(required_sections)}")
    return passed_count == len(required_sections)


def run_doc_pitfalls():
    print("=" * 60)
    print("[3/3] 坑表覆盖(用户能自查)")
    print("=" * 60)
    src = open(DOC, encoding="utf-8").read()
    # 9 已知坑 表里至少 6 行
    pitfall_section = src.split("## 9. 已知坑")[1] if "## 9. 已知坑" in src else ""
    rows = pitfall_section.count("| ")  # markdown table 行
    print(f"  ✓ 已知坑表行数 = {rows}")
    # 必须有修法列
    has_fix_col = "修法 / 绕过" in pitfall_section
    print(f"  ✓ 有'修法 / 绕过'列 = {has_fix_col}")
    return rows >= 6 and has_fix_col


def main():
    s1 = run_doc_exists()
    s2 = run_doc_content()
    s3 = run_doc_pitfalls()
    print("=" * 60)
    if s1 and s2 and s3:
        print("✓ P2.5+B-3 doc coverage ALL GREEN")
        return 0
    print("✗ P2.5+B-3 doc coverage FAILED")
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())