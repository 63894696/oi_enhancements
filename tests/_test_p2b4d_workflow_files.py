#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+B-4.D(2026-09-21)AI agent 写 workflow 文件 + import/export — 静态锚点扫 + cli/web 单元 smoke。

覆盖:
  S1. task-runner ext 6 命令注册(task.files.list/read/write/delete/import/export)
  S2. task-runner ext 路径安全 _resolveSafePath + WORKFLOWS_DIR
  S3. task-runner ext _workflowFileToObj + 文件 schema 校验
  S4. task-runner ext task.upsert/task.get 重构到模块顶层 _handlerTaskUpsert/_handlerTaskGet
  S5. cli new_workflow tool def 含 name/dag/path/content/trigger/schedule + required []
  S6. cli _t_new_workflow 函数体 + handler 分支
  S7. cli 子代工具集剔除 new_workflow
  S8. web _shell_system_prompt 末尾 workflow_files 简表注入
  S9. web wfmodal 📥/📤 按钮 HTML 锚点 + 导入 modal
  S10. web JS wfExportCurrent / wfOpenImport / wfImportApply / wfImportCancel
  S11. web i18n wf_export / wf_import / wf_import_title / wf_import_hint / wf_import_file / wf_import_paste / wf_imported 中英
  S12. py_compile 双文件 OK
  S13. cli 单元 smoke:_t_new_workflow 5 cases(write / import / paste / empty / write-fail透传)
  S14. web 单元 smoke:_shell_system_prompt workflow_files 简表注入 3 cases(0 file / 3 file / RPC 异常)
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


# === S1 task-runner ext 6 命令注册 ===
def s1_ext_commands():
    section("S1. task-runner ext 6 命令注册 (task.files.*)")
    src = _read(EXT)
    checks = [
        ("task.files.list 注册", "registerCommand('task.files.list'" in src or "registerCommand(\"task.files.list\"" in src),
        ("task.files.read 注册", "task.files.read'" in src or "task.files.read\"" in src),
        ("task.files.write 注册", "task.files.write'" in src or "task.files.write\"" in src),
        ("task.files.delete 注册", "task.files.delete'" in src or "task.files.delete\"" in src),
        ("task.files.import 注册", "task.files.import'" in src or "task.files.import\"" in src),
        ("task.files.export 注册", "task.files.export'" in src or "task.files.export\"" in src),
        ("README 头部注释列出 task.files.*", "P2.5+B-4.D" in src and "task.files.list" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S2 路径安全 ===
def s2_path_safety():
    section("S2. _resolveSafePath + WORKFLOWS_DIR")
    src = _read(EXT)
    checks = [
        ("WORKFLOWS_DIR 函数", "WORKFLOWS_DIR" in src and "path.join(HOME()" in src and "'workflows'" in src),
        ("_resolveSafePath 函数", "_resolveSafePath" in src),
        ("防 ../ 逃逸 startsWith 校验", "startsWith" in src and "base" in src),
        ("只允许 .json 后缀", ".endsWith('.json')" in src),
        ("绝对路径走 path.resolve", "path.isAbsolute" in src),
        ("相对路径走 PRISIR_EXT_HOME(HOME)", "path.resolve(HOME()" in src),
        ("_ensureWorkflowsDir mkdir recursive", "_ensureWorkflowsDir" in src and "mkdirSync" in src and "recursive: true" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S3 _workflowFileToObj + schema 校验 ===
def s3_file_obj():
    section("S3. _workflowFileToObj + 文件 schema 校验")
    src = _read(EXT)
    checks = [
        ("_workflowFileToObj 函数", "_workflowFileToObj" in src),
        ("读 fs.readFileSync", "fs.readFileSync" in src and "_workflowFileToObj" in src),
        ("JSON.parse + try/catch", "JSON.parse" in src and "invalid JSON" in src),
        ("校验 name 必须", "missing name" in src),
        ("校验 dag 必须", "missing dag" in src),
        ("返回 name/path/trigger/schedule/dag/updated_at/node_count",
         "node_count" in src and "updated_at" in src and "trigger" in src and "schedule" in src),
        ("validateDag 在 write/import 路径都调", src.count("validateDag(") >= 3),
        ("文件名 sanitize [a-z0-9_-]", "replace(/[^\\w\\-]/g" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S4 task.upsert / task.get 提到模块顶层 ===
def s4_handler_refactor():
    section("S4. _handlerTaskUpsert / _handlerTaskGet 模块顶层(供 import 自调)")
    src = _read(EXT)
    checks = [
        ("_handlerTaskUpsert 函数 def", "async function _handlerTaskUpsert" in src),
        ("_handlerTaskGet 函数 def", "async function _handlerTaskGet" in src),
        ("_handlerTaskFilesWrite 函数 def", "async function _handlerTaskFilesWrite" in src),
        ("task.upsert 改调 _handlerTaskUpsert", "_handlerTaskUpsert(args)" in src),
        ("task.get 改调 _handlerTaskGet", "_handlerTaskGet(args)" in src),
        ("task.files.write 改调 _handlerTaskFilesWrite", "_handlerTaskFilesWrite(args)" in src),
        ("task.files.import 自调 _handlerTaskFilesWrite", "_handlerTaskFilesWrite({" in src),
        ("task.files.import 自调 _handlerTaskUpsert", "_handlerTaskUpsert({" in src),
        ("task.files.export 自调 _handlerTaskGet", "_handlerTaskGet({" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S5 cli new_workflow tool def ===
def s5_cli_tool_def():
    section("S5. cli new_workflow tool def")
    src = _read(CLI)
    m = re.search(r'\{"type":\s*"function"\s*,\s*"function":\s*\{\s*"name":\s*"new_workflow".*?"required"\s*:\s*\[\s*\]\s*\}\s*\}\s*\}', src, re.S)
    if not m:
        print("  ✗ new_workflow tool def NOT FOUND")
        return False
    blob = m.group(0)
    checks = [
        ("name: new_workflow", '"name": "new_workflow"' in blob),
        ("description 提到 workflows/<name>.json", "workflows/<name>.json" in blob),
        ("description 提到 write mode / import mode / paste mode",
         "write mode" in blob and "import mode" in blob and "paste mode" in blob),
        ("description 提到写文件 ≠ 自动入库+跑", "Does NOT auto-upsert" in blob or "写完不入库" in blob or "Do NOT auto-upsert" in blob),
        ("name 参数", '"name"' in blob),
        ("dag 参数 + additionalProperties", '"dag"' in blob and "additionalProperties" in blob),
        ("dag.node.required ext+method", '"required"' in blob and '"ext"' in blob and '"method"' in blob),
        ("path 参数", '"path"' in blob and "import mode" in blob.lower()),
        ("content 参数(粘贴)", '"content"' in blob),
        ("trigger 参数 + default manual", '"trigger"' in blob and '"manual"' in blob),
        ("schedule 参数", '"schedule"' in blob),
        ("required 改 []", re.search(r'"required"\s*:\s*\[\s*\]\s*\}\s*\}\s*\}', blob) is not None),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S6 cli _t_new_workflow 函数 + handler ===
def s6_cli_function():
    section("S6. cli _t_new_workflow 函数 + handler 分支")
    src = _read(CLI)
    m = re.search(r'def _t_new_workflow\(args.*?(?=\ndef |\nclass |\Z)', src, re.S)
    if not m:
        print("  ✗ _t_new_workflow def NOT FOUND")
        return False
    body = m.group(0)
    checks = [
        ("def _t_new_workflow", "def _t_new_workflow" in body),
        ("name + dag + path + content 字段抽取",
         '"name"' in body and '"dag"' in body and '"path"' in body and '"content"' in body),
        ("trigger + schedule 抽取", '"trigger"' in body and '"schedule"' in body),
        ("content 优先分支(if content:)", "if content:" in body),
        ("elif path:", "elif path:" in body or "elif path:" in body.replace(" ", "")),
        ("elif name and isinstance(dag, dict) and dag",
         "name and isinstance(dag, dict) and dag" in body),
        ("啥都没传 error + hint",
         "name+dag" in body and "path import" in body and "content paste" in body),
        ("paste/import 走 task.files.import", "task.files.import" in body),
        ("write 走 task.files.write", "task.files.write" in body),
        ("调 _task_runner_rpc(顶层 helper)", "_task_runner_rpc(" in body),
        ("timeout=10", "timeout=10" in body),
        ("失败透传 error", "method + ' failed" in body or " failed" in body),
        ("import 模式 note: 可立即 run_task", "已写入文件 + 入库 SQLite" in body or "可立即 run_task" in body),
        ("write 模式 note: 需显式 run_task", "已写入 workflows/" in body and "显式调 run_task" in body),
        ("handler 分支 if name == \"new_workflow\"",
         'if name == "new_workflow":' in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S7 子代工具集剔除 new_workflow ===
def s7_subagent_exclude():
    section("S7. cli 子代工具集剔除 new_workflow(防递归)")
    src = _read(CLI)
    checks = [
        ("子代剔除 pool 行", "spawn_subagent" in src and "run_workflow" in src and "run_task" in src and "new_workflow" in src
         and "not in (" in src),
        ("docstring 提到 new_workflow 防递归", "new_workflow" in src and "防递归" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S8 web _shell_system_prompt workflow_files 简表注入 ===
def s8_web_inject():
    section("S8. web _shell_system_prompt 末尾 workflow_files 简表注入")
    src = _read(WEB)
    m = re.search(r'def _shell_system_prompt\(user_text.*?(?=\ndef |\nclass |\Z)', src, re.S)
    if not m:
        print("  ✗ _shell_system_prompt def NOT FOUND")
        return False
    body = m.group(0)
    checks = [
        ("_ext_rpc_call 调 task-runner task.files.list",
         '_ext_rpc_call("task-runner", "task.files.list"' in body),
        ("timeout=2.0", "timeout=2.0" in body),
        ("markdown 表头『【已落盘 workflow 文件】』",
         "【已落盘 workflow 文件" in body),
        ("markdown 列头 | name | path | nodes | trigger |",
         "| name | path | nodes | trigger |" in body),
        ("提到 new_workflow 写入", "new_workflow" in body),
        ("提到 task.files.read 读", "task.files.read" in body),
        ("提到写完不入库不跑", "写完不入库不跑" in body or "写完不入库" in body),
        ("_ffiles 取 .result.files", "_ffiles" in body and ".get(\"files\")" in body),
        (">50 截断提示", "len(_ffiles) > 50" in body),
        ("try/except 静默", "except Exception" in body and "pass" in body),
        ("parts.append 拼入 system prompt", "parts.append" in body),
        ("marker 注释 P2.5+B-4.D", "P2.5+B-4.D" in body),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S9 web wfmodal 📥/📤 按钮 + 导入 modal ===
def s9_wfmodal_html():
    section("S9. web wfmodal 📥/📤 按钮 + 导入 modal HTML")
    src = _read(WEB)
    checks = [
        ("wf-tools 段包含 📤 导出按钮 + wfExportCurrent",
         "wfExportCurrent" in src and "📤 导出" in src),
        ("wf-tools 段包含 📥 导入按钮 + wfOpenImport",
         "wfOpenImport" in src and "📥 导入" in src),
        ("data-i18n wf_export", 'data-i18n="wf_export"' in src),
        ("data-i18n wf_import", 'data-i18n="wf_import"' in src),
        ("导入 modal #wf-import-modal", 'id="wf-import-modal"' in src),
        ("导入 modal 文件 input #wf-import-file",
         'id="wf-import-file"' in src and 'accept=".json"' in src),
        ("导入 modal 粘贴 textarea #wf-import-paste",
         'id="wf-import-paste"' in src and "<textarea" in src),
        ("导入 modal 取消按钮 wfImportCancel", "wfImportCancel" in src),
        ("导入 modal 应用按钮 wfImportApply", "wfImportApply" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S10 web JS 函数 ===
def s10_wfmodal_js():
    section("S10. web JS wfExportCurrent / wfOpenImport / wfImportApply / wfImportCancel")
    src = _read(WEB)
    checks = [
        ("wfExportCurrent async def", "async function wfExportCurrent" in src),
        ("wfExportCurrent 调 task.files.export",
         "method: 'task.files.export'" in src or "method: \"task.files.export\"" in src),
        ("wfExportCurrent 用 Blob + a.click() 下载",
         "new Blob(" in src and "URL.createObjectURL" in src and "a.click()" in src),
        ("wfExportCurrent 校验 _wfCurrentTask.id",
         "!_wfCurrentTask" in src and "!_wfCurrentTask.id" in src),
        ("wfOpenImport def", "function wfOpenImport" in src),
        ("wfOpenImport 清空 file input + paste textarea",
         "wf-import-file" in src and "wf-import-paste" in src and ".value = ''" in src),
        ("wfImportCancel def", "function wfImportCancel" in src),
        ("wfImportApply async def", "async function wfImportApply" in src),
        ("wfImportApply 调 task.files.import(content)",
         "method: 'task.files.import'" in src and "content" in src),
        ("wfImportApply 失败 alert", "alert('导入失败" in src or "alert(\"导入失败" in src),
        ("wfImportApply 成功 wfRenderTaskList + wfLoadTask",
         "wfRenderTaskList" in src and "wfLoadTask(r.result.task_id)" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S11 i18n 中英 ===
def s11_i18n():
    section("S11. web i18n wf_export/wf_import/wf_import_title/wf_import_hint/wf_import_file/wf_import_paste/wf_imported 中英")
    src = _read(WEB)
    zh_keys = ["wf_export:", "wf_import:", "wf_import_title:", "wf_import_hint:",
               "wf_import_file:", "wf_import_paste:", "wf_imported:"]
    checks = []
    for k in zh_keys:
        # 中文在 zh dict(早期),英文在 en dict(后期)
        checks.append((f"中文 i18n {k}", k in src))
    # 英文 key 验证
    for k in ["wf_export:'", "wf_import:'", "wf_import_title:'", "wf_import_hint:'",
              "wf_import_file:'", "wf_import_paste:'", "wf_imported:'"]:
        checks.append((f"英文 i18n {k}", k in src))
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S12 py_compile 双文件 ===
def s12_py_compile():
    section("S12. py_compile 双文件 + node --check")
    rc1 = subprocess.run([sys.executable, "-m", "py_compile", CLI], capture_output=True, text=True).returncode
    rc2 = subprocess.run([sys.executable, "-m", "py_compile", WEB], capture_output=True, text=True).returncode
    rc3 = subprocess.run(["node", "--check", EXT], capture_output=True, text=True).returncode
    ok1, ok2, ok3 = rc1 == 0, rc2 == 0, rc3 == 0
    print(f"  {'✓' if ok1 else '✗'} cli py_compile rc={rc1}")
    print(f"  {'✓' if ok2 else '✗'} web py_compile rc={rc2}")
    print(f"  {'✓' if ok3 else '✗'} node --check rc={rc3}")
    return ok1 and ok2 and ok3


# === S13 cli 单元 smoke ===
def _load_cli_module():
    spec = importlib.util.spec_from_file_location("_cli_under_test", CLI)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        print(f"  (cli 加载异常: {type(e).__name__}: {e})")
        return None


def s13_cli_smoke():
    section("S13. cli _t_new_workflow 单元 smoke (5 cases)")
    cli = _load_cli_module()
    if cli is None:
        return False
    # mock _task_runner_rpc — 用全局 _CUR_RPC_FIXTURE 切 fixture
    def fake_rpc(method, params, timeout=8):
        global _CUR_RPC_FIXTURE
        return dict(_CUR_RPC_FIXTURE)
    cli._task_runner_rpc = fake_rpc

    cases = []

    # case 1: write mode (name+dag) → task.files.write
    global _CUR_RPC_FIXTURE
    _CUR_RPC_FIXTURE = {"ok": True, "result": {"path": "/home/workflows/daily_summary.json",
                                              "name": "daily_summary", "file": {}}}
    r = cli._t_new_workflow({"name": "daily_summary",
                             "dag": {"n1": {"ext": "todo", "method": "todo.add",
                                            "params": {"title": "x"}}}}, "/tmp")
    cases.append(("write mode",
                  _json.loads(r),
                  lambda d: d.get("ok") is True and d.get("method") == "task.files.write"
                            and d.get("name") == "daily_summary" and "task_id" not in d))

    # case 2: import mode (path) → task.files.import
    _CUR_RPC_FIXTURE = {"ok": True, "result": {"path": "/abs/foo.json", "name": "foo",
                                              "task_id": "t_xyz"}}
    r = cli._t_new_workflow({"path": "/abs/foo.json"}, "/tmp")
    cases.append(("import mode (path)",
                  _json.loads(r),
                  lambda d: d.get("ok") is True and d.get("method") == "task.files.import"
                            and d.get("task_id") == "t_xyz"))

    # case 3: paste mode (content) → task.files.import
    _CUR_RPC_FIXTURE = {"ok": True, "result": {"path": "/home/workflows/x.json", "name": "x",
                                              "task_id": "t_paste"}}
    r = cli._t_new_workflow({"content": '{"name":"x","dag":{}}'}, "/tmp")
    cases.append(("paste mode (content)",
                  _json.loads(r),
                  lambda d: d.get("ok") is True and d.get("method") == "task.files.import"
                            and d.get("task_id") == "t_paste"))

    # case 4: empty args → error
    r = cli._t_new_workflow({}, "/tmp")
    cases.append(("empty args → error",
                  _json.loads(r),
                  lambda d: "error" in d and "name+dag" in d.get("error", "")
                            and "path import" in d.get("error", "")
                            and "content paste" in d.get("error", "")))

    # case 5: write RPC failed → 透传 error
    _CUR_RPC_FIXTURE = {"ok": False, "error": "invalid dag: missing ext"}
    r = cli._t_new_workflow({"name": "bad", "dag": {"n1": {}}}, "/tmp")
    cases.append(("write RPC failed → 透传 error",
                  _json.loads(r),
                  lambda d: "error" in d and "task.files.write failed" in d.get("error", "")
                            and "invalid dag" in d.get("error", "")))

    ok = 0
    for label, data, validator in cases:
        passed = validator(data)
        print(f"  {'✓' if passed else '✗'} [{label}] → {str(data)[:140]}")
        if passed: ok += 1
    print(f"  → {ok}/{len(cases)}")
    return ok == len(cases)


_CUR_RPC_FIXTURE = {}


# === S14 web 单元 smoke ===
def s14_web_smoke():
    section("S14. web _shell_system_prompt workflow_files 简表注入 smoke (3 cases)")
    src = _read(WEB)
    marker = "# P2.5+B-4.D(2026-09-21):已落盘 workflow 文件清单注入"
    marker_pos = src.find(marker)
    if marker_pos < 0:
        print(f"  ✗ marker '{marker}' NOT FOUND")
        return False
    snippet = src[marker_pos:]
    # 截到下一个 return 之前(整个 _shell_system_prompt 函数结尾)
    ret_pos = snippet.find("\n    return ")
    if ret_pos < 0:
        print("  ✗ return not found after marker")
        return False
    inj_full = snippet[:ret_pos].rstrip()
    fn_src = "def _inject(user_text, sid=''):\n" + "\n".join("    " + line for line in inj_full.split("\n")) + "\n"

    def make_fake_rpc(files):
        def _fake_rpc(ext_id, method, params=None, timeout=5.0):
            return {"ok": True, "result": {"files": files}}
        return _fake_rpc

    cases = []

    # case 1: 0 file → 不注入
    try:
        ns = {"parts": ["PRE_BLOCK"], "_ext_rpc_call": make_fake_rpc([])}
        exec(fn_src, ns)
        ns["_inject"]("hello")
        out = ns["parts"]
        cases.append(("0 file → 不注入", out,
                      lambda p: len(p) == 1 and "PRE_BLOCK" in p
                                and "已落盘 workflow 文件" not in "\n".join(p)))
    except Exception as e:
        cases.append(("0 file → 不注入", str(e), lambda d: False))

    # case 2: 3 file → 注入 markdown 简表
    files3 = [
        {"name": "daily_summary", "path": "/home/workflows/daily_summary.json",
         "node_count": 2, "trigger": "manual"},
        {"name": "review_code", "path": "/home/workflows/review_code.json",
         "node_count": 3, "trigger": "manual"},
        {"name": "ship_v3", "path": "/home/workflows/ship_v3.json",
         "node_count": 5, "trigger": "schedule"},
    ]
    try:
        ns = {"parts": ["PRE_BLOCK"], "_ext_rpc_call": make_fake_rpc(files3)}
        exec(fn_src, ns)
        ns["_inject"]("hello")
        out = "\n".join(ns["parts"])
        cases.append(("3 file → 注入简表", out,
                      lambda s: "【已落盘 workflow 文件" in s
                                and "| name | path | nodes | trigger |" in s
                                and "daily_summary" in s
                                and "/home/workflows/daily_summary.json" in s
                                and "ship_v3" in s))
    except Exception as e:
        cases.append(("3 file → 注入简表", str(e), lambda d: False))

    # case 3: RPC 异常 → 静默
    def boom_rpc(ext_id, method, params=None, timeout=5.0):
        raise RuntimeError("ext_not_running")
    try:
        ns = {"parts": ["PRE_BLOCK"], "_ext_rpc_call": boom_rpc}
        exec(fn_src, ns)
        ns["_inject"]("hello")
        out = ns["parts"]
        cases.append(("RPC 异常 → 静默", out,
                      lambda p: len(p) == 1 and "已落盘 workflow 文件" not in "\n".join(p)))
    except Exception as e:
        cases.append(("RPC 异常 → 静默", str(e), lambda d: False))

    ok = 0
    for label, data, validator in cases:
        passed = validator(data)
        if not isinstance(data, str):
            data = str(data)
        print(f"  {'✓' if passed else '✗'} [{label}] → {data[:120]!r}")
        if passed: ok += 1
    print(f"  → {ok}/{len(cases)}")
    return ok == len(cases)


def main():
    sections = [
        s1_ext_commands(),
        s2_path_safety(),
        s3_file_obj(),
        s4_handler_refactor(),
        s5_cli_tool_def(),
        s6_cli_function(),
        s7_subagent_exclude(),
        s8_web_inject(),
        s9_wfmodal_html(),
        s10_wfmodal_js(),
        s11_i18n(),
        s12_py_compile(),
        s13_cli_smoke(),
        s14_web_smoke(),
    ]
    print("=" * 60)
    passed = sum(1 for s in sections if s)
    total = len(sections)
    if passed == total:
        print(f"✓ P2.5+B-4.D workflow_files ALL GREEN ({passed}/{total} sections)")
        return 0
    print(f"✗ P2.5+B-4.D workflow_files FAILED ({passed}/{total} sections)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
