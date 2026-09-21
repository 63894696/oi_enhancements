#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+B-4.E(2026-09-21)跨机器 workflow bundle 共享 — 静态锚点扫 + cli 单元 smoke。

覆盖:
  S1. task-runner ext 2 命令注册(task.files.bundle_export / bundle_import)
  S2. task-runner ext _toMsysPath helper(MSYS 路径转换)
  S3. task-runner ext 用 execSync + tar | gzip + 临时 staging
  S4. task-runner ext bundle_export:扫 workflows/ + 按 names[] 过滤 + README.md 生成
  S5. task-runner ext bundle_import:逐个 validateDag + 自调 _handlerTaskFilesWrite + upsert
  S6. cli workflow_bundle tool def + _t_workflow_bundle 函数体
  S7. cli workflow_bundle_import tool def + _t_workflow_bundle_import 函数体
  S8. cli 子代工具集剔除 workflow_bundle(防递归)
  S9. cli handler dispatch if name == "workflow_bundle" / "workflow_bundle_import"
  S10. web wf-side .wf-bundle-bar 3 按钮 HTML 锚点
  S11. web .wf-task-check checkbox 列 + onchange 钩子
  S12. web #wf-bundle-modal file input (.tar.gz/.tgz)
  S13. web JS wfBundleSelectAll / wfBundleUpdateCount / wfBundleExport / wfBundleOpenImport / wfBundleImportApply / wfBundleImportCancel
  S14. web i18n wf_bundle_* 中英(11 keys)
  S15. py_compile cli/web OK + node --check OK
"""
import os
import re
import sys
import subprocess
import json as _json
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CLI = os.path.join(ROOT, "prisIragent_cli.py")
WEB = os.path.join(ROOT, "prisIragent_web.py")
EXT = os.path.join(ROOT, "extensions", "task-runner", "index.js")


def _read(path):
    return open(path, encoding="utf-8").read()


def section(title):
    print("=" * 60)
    print(f"[{title}]")
    print("=" * 60)


# === S1 task-runner ext 2 命令注册 ===
def s1_ext_commands():
    section("S1. task-runner ext task.files.bundle_export / bundle_import 注册")
    src = _read(EXT)
    checks = [
        ("task.files.bundle_export 注册",
         "task.files.bundle_export'" in src or "task.files.bundle_export\"" in src),
        ("task.files.bundle_import 注册",
         "task.files.bundle_import'" in src or "task.files.bundle_import\"" in src),
        ("README 头部注释列出 bundle_*",
         "task.files.bundle_export" in src and "task.files.bundle_import" in src
         and "P2.5+B-4.E" in src),
        ("require child_process execSync",
         "require('child_process')" in src or 'require("child_process")' in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S2 _toMsysPath helper ===
def s2_msys_path():
    section("S2. _toMsysPath helper(MSYS 路径转换)")
    src = _read(EXT)
    checks = [
        ("function _toMsysPath def", "function _toMsysPath" in src),
        ("检测 [A-Za-z]:[\\\\\\/] 盘符", "A-Za-z" in src and ":" in src and "[\\\\\\/" in src),
        ("lowercase 盘符 + 替换反斜杠", ".toLowerCase()" in src and ".replace(/\\\\/g, '/')" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S3 execSync + tar | gzip + 临时 staging ===
def s3_tar_exec():
    section("S3. execSync tar -czf / tar -xzf + 临时 staging")
    src = _read(EXT)
    checks = [
        ("execSync 调 tar -czf", "execSync" in src and "tar -czf" in src),
        ("execSync 调 tar -xzf", "tar -xzf" in src),
        ("shell '/usr/bin/bash'", "shell: '/usr/bin/bash'" in src),
        ("_tmpBundlePath helper", "function _tmpBundlePath" in src),
        ("_cleanupBundle helper", "function _cleanupBundle" in src),
        ("tmp 目录 .prisir/_tmp", ".prisir" in src and "_tmp" in src),
        ("stage 目录 mkdir recursive", "mkdirSync" in src and "recursive: true" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S4 bundle_export 流程 ===
def s4_bundle_export_flow():
    section("S4. bundle_export:扫 workflows/ + 按 names[] 过滤 + README.md + base64 返")
    src = _read(EXT)
    checks = [
        ("args.names array 校验", "Array.isArray(args.names)" in src),
        ("扫 workflows/*.json", "fs.readdirSync(WORKFLOWS_DIR())" in src),
        ("按 names[] 过滤(selected)", "args.names.includes(name)" in src),
        ("空数组 = ALL 分支", "args.names.length === 0" in src),
        ("README.md 生成", "README.md" in src and "Prisir Workflows Bundle" in src),
        ("README 含导出时间戳", "Exported at" in src),
        ("README 含 workflow 节点数", "node_count" in src and "nodes:" in src),
        ("copyFileSync 到 staging", "fs.copyFileSync" in src),
        ("writeFileSync README.md", "fs.writeFileSync" in src),
        ("返 base64", ".toString('base64')" in src),
        ("返 count / size_bytes / name", "count:" in src and "size_bytes" in src
         and "name: `workflows-bundle-" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S5 bundle_import 流程 ===
def s5_bundle_import_flow():
    section("S5. bundle_import:base64 → 解 tar.gz → 逐个 validateDag + 写 + upsert")
    src = _read(EXT)
    # 找 bundle_import 注册块
    m = re.search(r"registerCommand\('task\.files\.bundle_import'.*?(?=registerCommand\(|// ─── |// ═══)", src, re.S)
    body = m.group(0) if m else src
    checks = [
        ("base64 → Buffer.from", "Buffer.from(args.base64" in body),
        ("写临时 tar.gz", "fs.writeFileSync(tarGzPath" in body),
        ("tar -xzf 解压", "tar -xzf" in body),
        ("要求 workflows/ 子目录存在", "fs.existsSync(bundleWorkflowsDir)" in body),
        ("缺 workflows/ → error", "bundle missing workflows/" in body),
        ("逐个 .json 校验", "validateDag(obj.dag)" in body),
        ("缺 name/dag → skipped", "missing name or dag" in body),
        ("自调 _handlerTaskFilesWrite", "_handlerTaskFilesWrite({" in body),
        ("自调 _handlerTaskUpsert", "_handlerTaskUpsert({" in body),
        ("返 imported[] + skipped[] + total", "imported" in body and "skipped" in body and "total" in body),
        ("_cleanupBundle 收尾", "_cleanupBundle" in body),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S6 cli workflow_bundle tool def + handler ===
def s6_cli_bundle_def():
    section("S6. cli workflow_bundle tool def + _t_workflow_bundle 函数")
    src = _read(CLI)
    m = re.search(r'\{"type":\s*"function"\s*,\s*"function":\s*\{\s*"name":\s*"workflow_bundle".*?"required"\s*:\s*\[\s*\]\s*\}\s*\}\s*\}', src, re.S)
    if not m:
        print("  ✗ workflow_bundle tool def NOT FOUND")
        return False
    blob = m.group(0)
    checks = [
        ('name: "workflow_bundle"', '"name": "workflow_bundle"' in blob),
        ("description 提到 .tar.gz", ".tar.gz" in blob),
        ("description 提到 names array", "names" in blob and "array" in blob.lower()),
        ("description 提到 base64 返", "base64" in blob),
        ("description 提到 Windows 10+ tar", "Windows 10+" in blob or "Windows 10" in blob),
        ("names 参数 type=array", '"names"' in blob and '"array"' in blob),
        ("names.items.type=string", '"string"' in blob),
        ('default=[]', '"default": []' in blob),
        ('required []', re.search(r'"required"\s*:\s*\[\s*\]\s*\}\s*\}\s*\}', blob) is not None),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


def s6b_cli_bundle_fn():
    src = _read(CLI)
    m = re.search(r'def _t_workflow_bundle\(args: dict.*?(?=\ndef |\nclass |\Z)', src, re.S)
    if not m:
        print("  ✗ _t_workflow_bundle def NOT FOUND")
        return False
    body = m.group(0)
    checks = [
        ("def _t_workflow_bundle", "def _t_workflow_bundle" in body),
        ("抽 names list", "isinstance(args.get(\"names\"), list)" in body
                          or "isinstance(args.get('names'), list)" in body),
        ("clean_names sanitize", "clean_names" in body),
        ("调 task.files.bundle_export", "task.files.bundle_export" in body),
        ("_task_runner_rpc(顶层 helper)", "_task_runner_rpc(" in body),
        ("timeout=30(bundle 包大文件耗时长)", "timeout=30" in body),
        ("失败透传 error", "bundle_export failed" in body),
        ("返 name/count/size_bytes/base64", '"name"' in body and '"count"' in body
                                          and '"size_bytes"' in body and '"base64"' in body),
        ("note 字段说明", "note" in body),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S7 cli workflow_bundle_import tool def + handler ===
def s7_cli_bundle_import_def():
    section("S7. cli workflow_bundle_import tool def + _t_workflow_bundle_import 函数")
    src = _read(CLI)
    m = re.search(r'\{"type":\s*"function"\s*,\s*"function":\s*\{\s*"name":\s*"workflow_bundle_import".*?"required"\s*:\s*\[\s*\]\s*\}\s*\}\s*\}', src, re.S)
    if not m:
        print("  ✗ workflow_bundle_import tool def NOT FOUND")
        return False
    blob = m.group(0)
    checks = [
        ('name: "workflow_bundle_import"', '"name": "workflow_bundle_import"' in blob),
        ("description 提到解压 + 入库", "解压" in blob or "unpack" in blob.lower() or "import" in blob.lower()),
        ("description 提到 base64", "base64" in blob),
        ('base64 参数', '"base64"' in blob),
        ('required []', re.search(r'"required"\s*:\s*\[\s*\]\s*\}\s*\}\s*\}', blob) is not None),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


def s7b_cli_bundle_import_fn():
    src = _read(CLI)
    m = re.search(r'def _t_workflow_bundle_import\(args: dict.*?(?=\ndef |\nclass |\Z)', src, re.S)
    if not m:
        print("  ✗ _t_workflow_bundle_import def NOT FOUND")
        return False
    body = m.group(0)
    checks = [
        ("def _t_workflow_bundle_import", "def _t_workflow_bundle_import" in body),
        ("抽 base64 字段", "args.get(\"base64\")" in body or "args.get('base64')" in body),
        ("空 base64 error + hint", "workflow_bundle_import 需要 base64" in body),
        ("调 task.files.bundle_import", "task.files.bundle_import" in body),
        ("timeout=30", "timeout=30" in body),
        ("失败透传 error", "bundle_import failed" in body),
        ("返 imported/skipped/total", '"imported"' in body and '"skipped"' in body
                                     and '"total"' in body),
        ("note 含导入计数", "已导入" in body),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S8 子代工具集剔除 workflow_bundle ===
def s8_subagent_exclude():
    section("S8. cli 子代工具集剔除 workflow_bundle(防递归)")
    src = _read(CLI)
    checks = [
        ("子代剔除 pool 行含 workflow_bundle",
         "spawn_subagent" in src and "workflow_bundle" in src
         and "new_workflow" in src and "not in (" in src),
        ("docstring 提到防递归", "防递归" in src and "workflow_bundle" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S9 cli handler dispatch ===
def s9_handler_dispatch():
    section("S9. cli handler dispatch workflow_bundle / workflow_bundle_import")
    src = _read(CLI)
    checks = [
        ('if name == "workflow_bundle":', 'if name == "workflow_bundle":' in src),
        ('if name == "workflow_bundle_import":', 'if name == "workflow_bundle_import":' in src),
        ("调 _t_workflow_bundle", "_t_workflow_bundle(" in src),
        ("调 _t_workflow_bundle_import", "_t_workflow_bundle_import(" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S10 web wf-side .wf-bundle-bar ===
def s10_web_bundle_bar():
    section("S10. web wf-side .wf-bundle-bar 3 按钮 HTML")
    src = _read(WEB)
    checks = [
        (".wf-bundle-bar div", 'class="wf-bundle-bar"' in src),
        ('#wf-bundle-all-btn', 'id="wf-bundle-all-btn"' in src),
        ('wfBundleSelectAll onclick', "onclick=\"wfBundleSelectAll()\"" in src),
        ('#wf-bundle-export-btn', 'id="wf-bundle-export-btn"' in src),
        ('wfBundleExport onclick', "onclick=\"wfBundleExport()\"" in src),
        ('#wf-bundle-import-btn', 'id="wf-bundle-import-btn"' in src),
        ('wfBundleOpenImport onclick', "onclick=\"wfBundleOpenImport()\"" in src),
        ('export btn 初始 disabled', '<button class="topbtn small primary" id="wf-bundle-export-btn" onclick="wfBundleExport()"'
                                     in src and "disabled" in src.split('id="wf-bundle-export-btn"')[1][:300]),
        ('data-i18n wf_bundle_all', 'data-i18n="wf_bundle_all"' in src),
        ('data-i18n wf_bundle_export', 'data-i18n="wf_bundle_export"' in src),
        ('data-i18n wf_bundle_import', 'data-i18n="wf_bundle_import"' in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S11 web checkbox 列 + onchange ===
def s11_web_checkbox():
    section("S11. web .wf-task-check checkbox 列 + onchange wfBundleUpdateCount")
    src = _read(WEB)
    checks = [
        (".wf-task-check input", 'class="wf-task-check"' in src),
        ("checkbox type=checkbox", 'type="checkbox"' in src),
        ("data-task-name 属性", "data-task-name=" in src),
        ("data-task-id 属性", "data-task-id=" in src),
        ("onchange wfBundleUpdateCount", "onchange=\"wfBundleUpdateCount()\"" in src),
        ("wfRenderTaskList 末尾调 wfBundleUpdateCount",
         "wfBundleUpdateCount" in src and src.count("wfBundleUpdateCount") >= 2),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S12 web #wf-bundle-modal ===
def s12_web_bundle_modal():
    section("S12. web #wf-bundle-modal file input (.tar.gz/.tgz)")
    src = _read(WEB)
    checks = [
        ('#wf-bundle-modal', 'id="wf-bundle-modal"' in src),
        ('#wf-bundle-file', 'id="wf-bundle-file"' in src),
        ('accept=".tar.gz,.tgz"', "accept=\".tar.gz,.tgz\"" in src),
        ('wfBundleImportCancel onclick', "onclick=\"wfBundleImportCancel()\"" in src),
        ('wfBundleImportApply onclick', "onclick=\"wfBundleImportApply()\"" in src),
        ('data-i18n wf_bundle_import_title', 'data-i18n="wf_bundle_import_title"' in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S13 web JS bundle functions ===
def s13_web_bundle_js():
    section("S13. web JS wfBundle* 6 函数")
    src = _read(WEB)
    checks = [
        ("function wfBundleSelectAll def", "function wfBundleSelectAll" in src),
        ("function wfBundleUpdateCount def", "function wfBundleUpdateCount" in src),
        ("async function wfBundleExport def", "async function wfBundleExport" in src),
        ("function wfBundleOpenImport def", "function wfBundleOpenImport" in src),
        ("function wfBundleImportCancel def", "function wfBundleImportCancel" in src),
        ("async function wfBundleImportApply def", "async function wfBundleImportApply" in src),
        ("wfBundleExport 调 task.files.bundle_export",
         "method: 'task.files.bundle_export'" in src
         or "method: \"task.files.bundle_export\"" in src),
        ("wfBundleExport atob + Uint8Array + Blob",
         "atob" in src and "Uint8Array" in src and "new Blob" in src),
        ("wfBundleExport a.click() 触发下载", "a.click()" in src and "URL.createObjectURL" in src),
        ("wfBundleImportApply 调 task.files.bundle_import",
         "method: 'task.files.bundle_import'" in src
         or "method: \"task.files.bundle_import\"" in src),
        ("wfBundleImportApply btoa 编码 base64",
         "btoa(bin)" in src or "btoa(" in src),
        ("wfBundleImportApply 后调 wfRenderTaskList",
         "wfRenderTaskList" in src and "wfRenderTaskList()" in src.split("wfBundleImportApply")[1]),
        (".tar.gz 后缀校验",
         ".endsWith('.tar.gz')" in src and ".endsWith('.tgz')" in src),
        ("CHUNK 分块编码防爆栈", "CHUNK" in src and "0x8000" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S14 web i18n 中英 ===
def s14_i18n():
    section("S14. web i18n wf_bundle_* 中英(11 keys)")
    src = _read(WEB)
    keys_zh = ["wf_bundle_all:", "wf_bundle_export:", "wf_bundle_import:",
               "wf_bundle_select_first:", "wf_bundle_invalid_ext:",
               "wf_bundle_export_fail:", "wf_bundle_import_fail:",
               "wf_bundle_imported:", "wf_bundle_exported:",
               "wf_bundle_import_title:", "wf_bundle_import_hint:",
               "wf_bundle_import_file:"]
    keys_en_quoted = ["wf_bundle_all:'", "wf_bundle_export:'", "wf_bundle_import:'",
                      "wf_bundle_select_first:'", "wf_bundle_invalid_ext:'",
                      "wf_bundle_export_fail:'", "wf_bundle_import_fail:'",
                      "wf_bundle_imported:'", "wf_bundle_exported:'",
                      "wf_bundle_import_title:'", "wf_bundle_import_hint:'",
                      "wf_bundle_import_file:'"]
    checks = []
    for k in keys_zh:
        checks.append((f"中文 {k}", k in src))
    for k in keys_en_quoted:
        checks.append((f"英文 {k}", k in src))
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S15 py_compile + node --check ===
def s15_py_compile():
    section("S15. py_compile cli/web + node --check")
    rc1 = subprocess.run([sys.executable, "-m", "py_compile", CLI], capture_output=True, text=True).returncode
    rc2 = subprocess.run([sys.executable, "-m", "py_compile", WEB], capture_output=True, text=True).returncode
    rc3 = subprocess.run(["node", "--check", EXT], capture_output=True, text=True).returncode
    ok1, ok2, ok3 = rc1 == 0, rc2 == 0, rc3 == 0
    print(f"  {'✓' if ok1 else '✗'} cli py_compile rc={rc1}")
    print(f"  {'✓' if ok2 else '✗'} web py_compile rc={rc2}")
    print(f"  {'✓' if ok3 else '✗'} node --check rc={rc3}")
    if rc1 != 0: print(f"     cli: {rc1.stderr[:200]}")
    if rc2 != 0: print(f"     web: {rc2.stderr[:200]}")
    if rc3 != 0: print(f"     node: {rc3.stderr[:200]}")
    return ok1 and ok2 and ok3


def main():
    sections = [
        s1_ext_commands(),
        s2_msys_path(),
        s3_tar_exec(),
        s4_bundle_export_flow(),
        s5_bundle_import_flow(),
        s6_cli_bundle_def(),
        s6b_cli_bundle_fn(),
        s7_cli_bundle_import_def(),
        s7b_cli_bundle_import_fn(),
        s8_subagent_exclude(),
        s9_handler_dispatch(),
        s10_web_bundle_bar(),
        s11_web_checkbox(),
        s12_web_bundle_modal(),
        s13_web_bundle_js(),
        s14_i18n(),
        s15_py_compile(),
    ]
    print("=" * 60)
    passed = sum(1 for s in sections if s)
    total = len(sections)
    if passed == total:
        print(f"✓ P2.5+B-4.E workflow_bundle ALL GREEN ({passed}/{total} sections)")
        return 0
    print(f"✗ P2.5+B-4.E workflow_bundle FAILED ({passed}/{total} sections)")
    return 1


if __name__ == "__main__":
    sys.exit(main())