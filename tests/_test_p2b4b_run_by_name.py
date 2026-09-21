#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+B-4.B(2026-09-21)AI agent 触发已存 workflow(task_name 模糊匹配)— 静态锚点扫 + cli/web 单元 smoke。

覆盖:
  S1. cli run_task tool def 含 task_name 字段 + required 改 [] + 描述两模式叙述
  S2. cli handler _t_run_task 加 task_name 分支 + 啥都没传校验
  S3. cli 新 _t_run_task_by_name 函数体(精确/子串匹配 + 多命中 candidates + 零命中 hint)
  S4. cli _task_runner_rpc 模块顶层 helper
  S5. web _shell_system_prompt 末尾 task 简表 markdown 注入
  S6. py_compile 双文件 OK
  S7. cli 单元 smoke:exact/substr/empty/ambiguous/no-match/legacy-dag/error-empty
  S8. web 单元 smoke:_shell_system_prompt 简表注入 / 0 task 不注入 / RPC 异常静默
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


def _read(path):
    return open(path, encoding="utf-8").read()


def section(title):
    print("=" * 60)
    print(f"[{title}]")
    print("=" * 60)


# === S1 cli tool def ===
def s1_cli_tool_def():
    section("S1. cli run_task tool def 加 task_name + required 改 []")
    src = _read(CLI)
    m = re.search(r'\{"type":\s*"function"\s*,\s*"function":\s*\{\s*"name":\s*"run_task".*?\}\s*\}\s*,?\s*\}', src, re.S)
    if not m:
        print("  ✗ run_task tool def NOT FOUND")
        return False
    blob = m.group(0)
    checks = [
        ("description 含 'Re-run existing' / 'task_name'", "Re-run existing" in blob and "task_name" in blob),
        ("description 含 'Inline dag'", "Inline dag" in blob),
        ("description 含 'task-runner'", "task-runner" in blob),
        ("description 含 progress streams", "Progress streams" in blob or "progress streams" in blob or "tool trace" in blob),
        ("description 含 DAG 节点字典描述", "DAG is" in blob or "dag is" in blob),
        ("task_name 参数存在", '"task_name"' in blob),
        ("task_name 描述 子串 / 模糊 / 二选一",
         "substring" in blob.lower() and ("二选一" in blob or "case-insensitive" in blob.lower())),
        ("name 参数仍在", '"name"' in blob),
        ("dag 参数仍在", '"dag"' in blob),
        ("wait 参数仍在", '"wait"' in blob and "False" in blob),
        ("required 改 []", re.search(r'"required"\s*:\s*\[\s*\]\s*\}\s*\}\s*\}', blob) is not None),
        ("dag.additionalProperties 保留", "additionalProperties" in blob),
        ("node required ext+method 保留", '"ext"' in blob and '"method"' in blob),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S2 cli _t_run_task handler ===
def s2_cli_handler():
    section("S2. cli _t_run_task 加 task_name 分支 + 啥都没传校验")
    src = _read(CLI)
    m = re.search(r'def _t_run_task\(args.*?(?=\ndef |\nclass |\Z)', src, re.S)
    if not m:
        print("  ✗ _t_run_task def NOT FOUND")
        return False
    body = m.group(0)
    checks = [
        ("task_name 字段抽取", 'task_name = str(args.get("task_name") or "").strip()' in body),
        ("啥都没传校验", 'name and not dag and not task_name' in body),
        ("啥都没传 error 含 hint",
         "name + dag" in body and "task_name" in body and "hint" in body),
        ("task_name 分支优先", 'if task_name:' in body and '_t_run_task_by_name' in body),
        ("调 _t_run_task_by_name", '_t_run_task_by_name(task_name, wait)' in body),
        ("dag 浅校验保留", 'each nid 都要有 ext + method' in body or 'missing ext or method' in body),
        ("name 仍要非空校验", "name required" in body),
        ("task.upsert 调法保留", '"task.upsert"' in body),
        ("task.run 调法保留", '"task.run"' in body),
        ("fire-and-forget 保留", '"queued": True' in body or "queued: True" in body),
        ("RPC helper 改顶层", '_task_runner_rpc(' in body),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S3 cli _t_run_task_by_name ===
def s3_cli_helper():
    section("S3. cli _t_run_task_by_name 函数体")
    src = _read(CLI)
    m = re.search(r'def _t_run_task_by_name\(task_name.*?(?=\ndef |\nclass |\Z)', src, re.S)
    if not m:
        print("  ✗ _t_run_task_by_name def NOT FOUND")
        return False
    body = m.group(0)
    checks = [
        ("def 含 task_name + wait", "def _t_run_task_by_name" in body and "wait" in body),
        ("调 _task_runner_rpc", "_task_runner_rpc(" in body),
        ("task.list", '"task.list"' in body),
        ("limit=100", '"limit": 100' in body or "limit=100" in body),
        ("lower() 大小写不敏感", ".lower()" in body),
        ("exact 优先列表", "exact" in body and "=" in body),
        ("substr 候选", "substr" in body),
        ("candidates 合并", "candidates = exact + substr" in body or "exact + substr" in body),
        ("零命中 error + hint", '"no workflow matching' in body and "可用的已存 workflow" in body),
        ("多命中无精确 → ambiguous", "ambiguous" in body and "candidates" in body),
        ("hint ask user", "ask the user" in body or "disambiguate" in body),
        ("target = candidates[0]", "target = candidates[0]" in body),
        ("matched_by 字段", "matched_by" in body),
        ("task.run 调法", '"task.run"' in body and '"id"' in body),
        ("error 兜底", "task.run failed" in body or "task.run failed" in body),
        ("rpc 失败走 _task_runner_rpc", "_task_runner_rpc(" in body),
        ("JSON 返回", "_json.dumps" in body),
        ("返 task_name 给 LLM", '"task_name"' in body),
        ("返 run_id 给 LLM", '"run_id"' in body),
        ("返 status", '"status"' in body),
        ("0 task hint 引导用 inline", "no stored workflows" in body),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S4 _task_runner_rpc 模块顶层 ===
def s4_cli_rpc_helper():
    section("S4. cli _task_runner_rpc 模块顶层 helper")
    src = _read(CLI)
    checks = [
        ("def _task_runner_rpc 模块级(不在函数内)", "def _task_runner_rpc(" in src),
        ("接收 method + params + timeout", "def _task_runner_rpc(method: str, params: dict, timeout: int = 8)" in src),
        ("读 PRISIR_WEB_PORT", "PRISIR_WEB_PORT" in src),
        ("回退 PRISIRAGENT_PORT", "PRISIRAGENT_PORT" in src),
        ("回退默认 18800", "18800" in src),
        ("/api/ext/rpc 端点", "/api/ext/rpc" in src),
        ("ext_id task-runner 硬编码", '"task-runner"' in src),
        ("try/except 返 error", "except Exception" in src and '"error"' in src),
        ("urlopen 用 _ur", "_ur.urlopen" in src),
        ("JSON 解码", "_json.loads" in src),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S5 web _shell_system_prompt ===
def s5_web_inject():
    section("S5. web _shell_system_prompt 末尾 task 简表注入")
    src = _read(WEB)
    m = re.search(r'def _shell_system_prompt\(user_text.*?(?=\ndef |\nclass |\Z)', src, re.S)
    if not m:
        print("  ✗ _shell_system_prompt def NOT FOUND")
        return False
    body = m.group(0)
    checks = [
        ("_ext_rpc_call 调 task-runner task.list",
         '_ext_rpc_call("task-runner", "task.list"' in body),
        ("limit 100", '"limit": 100' in body),
        ("timeout=2.0", "timeout=2.0" in body),
        ("markdown 表头 加粗中文", "【已存 workflow(可跑)】" in body),
        ("markdown 列头", "| name | id | 节点数 | trigger |" in body),
        ("子串说明", "task_name" in body and "重跑" in body),
        ("_ttasks 取 .result.tasks", "_ttasks" in body and ".get(\"tasks\")" in body),
        ("逐 task 拼 _tlines", "_tlines.append" in body),
        (">50 截断提示", "len(_ttasks) > 50" in body),
        ("try/except 静默", "except Exception" in body and "pass" in body),
        ("parts.append 拼入 system prompt", "parts.append" in body),
    ]
    ok = 0
    for n, hit in checks:
        print(f"  {'✓' if hit else '✗'} {n}")
        if hit: ok += 1
    print(f"  → {ok}/{len(checks)}")
    return ok == len(checks)


# === S6 py_compile ===
def s6_py_compile():
    section("S6. py_compile 双文件")
    rc1 = subprocess.run([sys.executable, "-m", "py_compile", CLI], capture_output=True, text=True).returncode
    rc2 = subprocess.run([sys.executable, "-m", "py_compile", WEB], capture_output=True, text=True).returncode
    ok1 = rc1 == 0
    ok2 = rc2 == 0
    print(f"  {'✓' if ok1 else '✗'} cli py_compile rc={rc1}")
    print(f"  {'✓' if ok2 else '✗'} web py_compile rc={rc2}")
    return ok1 and ok2


# === S7 cli 单元 smoke ===
def _load_cli_module():
    """importlib 加载 cli(避开 NTFS case fold 问题,主会话不要真启动 web)。"""
    spec = importlib.util.spec_from_file_location("_cli_under_test", CLI)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        print(f"  (cli 加载异常: {type(e).__name__}: {e})")
        return None


def s7_cli_smoke():
    section("S7. cli 单元 smoke (7 cases)")
    cli = _load_cli_module()
    if cli is None:
        return False
    # mock _task_runner_rpc — 全局变量 _CUR_FIXTURE 切 fixture
    def fake_rpc(method, params, timeout=8):
        if method == "task.list":
            return {"ok": True, "result": {"tasks": list(_CUR_FIXTURE)}}
        if method in ("task.upsert", "task.run"):
            return {"ok": True, "result": {"id": "t_inline", "run_id": "r_fake", "status": "queued"}}
        return {"ok": False, "error": "unknown method " + method}
    cli._task_runner_rpc = fake_rpc

    cases = []

    def set_fixture(items):
        global _CUR_FIXTURE
        _CUR_FIXTURE = list(items)

    # case 1: exact match
    set_fixture([{"id": "t_a", "name": "daily_summary"}, {"id": "t_b", "name": "review_code"}])
    r = cli._t_run_task_by_name("daily_summary", False)
    cases.append(("exact", "daily_summary", r, lambda d: d.get("matched_by") == "exact" and d.get("task_id") == "t_a"))
    # case 2: substring match
    r = cli._t_run_task_by_name("review", False)
    cases.append(("substring", "review", r, lambda d: d.get("matched_by") == "substring" and d.get("task_id") == "t_b"))
    # case 3: empty task list
    set_fixture([])
    r = cli._t_run_task_by_name("anything", False)
    cases.append(("empty list", "anything", r, lambda d: "no stored workflows" in d.get("error", "")))
    # case 4: ambiguous
    set_fixture([{"id": "t_1", "name": "review code v1"},
                 {"id": "t_2", "name": "review code v2"},
                 {"id": "t_3", "name": "ship v3"}])
    r = cli._t_run_task_by_name("review code", False)
    cases.append(("ambiguous", "review code", r, lambda d: "ambiguous" in d.get("error", "") and len(d.get("candidates", [])) == 2))
    # case 5: no match
    set_fixture([{"id": "t_1", "name": "daily_summary"}, {"id": "t_2", "name": "ship"}])
    r = cli._t_run_task_by_name("zzz_not_exist", False)
    cases.append(("no match", "zzz_not_exist", r, lambda d: "no workflow matching" in d.get("error", "") and "可用的已存 workflow" in d.get("hint", "")))
    # case 6: legacy inline dag still works
    set_fixture([])
    r = cli._t_run_task({"name": "inline_test", "dag": {"n1": {"ext": "todo", "method": "todo.add", "params": {}}}}, "/tmp")
    cases.append(("inline dag", "inline_test", r, lambda d: d.get("run_id") == "r_fake" and d.get("queued") is True))
    # case 7: empty args rejected
    r = cli._t_run_task({}, "/tmp")
    cases.append(("empty args", "{}", r, lambda d: "need either" in d.get("error", "") and "task_name" in d.get("hint", "")))

    ok = 0
    for label, q, raw, validator in cases:
        try:
            data = _json.loads(raw) if isinstance(raw, str) and raw.strip().startswith("{") else {"raw": raw}
        except Exception:
            data = {"raw": raw}
        passed = validator(data)
        print(f"  {'✓' if passed else '✗'} [{label}] {q!r:30s} → {str(data)[:140]}")
        if passed: ok += 1
    print(f"  → {ok}/{len(cases)}")
    return ok == len(cases)


_CUR_FIXTURE = []


# === S8 web 单元 smoke ===
def s8_web_smoke():
    """web 端 _shell_system_prompt 末尾 task 简表注入的真跑测试。

    web 模块 import 会触发很多全局初始化(Tornado app / aiomgr / sqlite),
    直接 exec_module 会卡死;改用源码片段提取 + sandbox exec 试注入。
    """
    section("S8. web _shell_system_prompt 简表注入 smoke (3 cases)")
    src = _read(WEB)
    # 定位 B-4.B 注入段的 try/except 块(以注释 marker 开头)
    marker = "# P2.5+B-4.B(2026-09-21):已存 workflow 简表注入"
    marker_pos = src.find(marker)
    if marker_pos < 0:
        print(f"  ✗ marker '{marker}' NOT FOUND")
        return False
    # 取从 marker 到下一个 return 之前的 try/except 块(扩展缩进到函数体级别)
    snippet = src[marker_pos:]
    # 截到下一个 return 之前
    ret_pos = snippet.find("\n    return ")
    if ret_pos < 0:
        print("  ✗ return not found after marker")
        return False
    # 取 try 块(注释 + try + except)
    inj_full = snippet[:ret_pos].rstrip()
    # 改成可调函数:sandbox exec
    fn_src = "def _inject(user_text, sid=''):\n" + "\n".join("    " + line for line in inj_full.split("\n")) + "\n"

    def make_fake_rpc(tasks):
        def _fake_rpc(ext_id, method, params=None, timeout=5.0):
            return {"ok": True, "result": {"tasks": tasks}}
        return _fake_rpc

    cases = []

    # case 1: 0 task → 不注入(简表段不出现)
    try:
        ns = {"parts": ["PRE_BLOCK"], "_ext_rpc_call": make_fake_rpc([])}
        exec(fn_src, ns)
        ns["_inject"]("hello")
        out = ns["parts"]
        cases.append(("0 task → 不注入", out,
                      lambda p: len(p) == 1 and "PRE_BLOCK" in p and "已存 workflow" not in "\n".join(p)))
    except Exception as e:
        cases.append(("0 task → 不注入", str(e), lambda d: False))

    # case 2: 3 task → 注入简表 markdown
    tasks3 = [
        {"id": "t1", "name": "daily_summary", "dag": {"a": {}, "b": {}}, "trigger": "schedule"},
        {"id": "t2", "name": "review_code", "dag": {"x": {}}},
        {"id": "t3", "name": "ship_v3", "dag": {"p": {}, "q": {}, "r": {}}, "trigger": "manual"},
    ]
    try:
        ns = {"parts": ["PRE_BLOCK"], "_ext_rpc_call": make_fake_rpc(tasks3)}
        exec(fn_src, ns)
        ns["_inject"]("hello")
        out = "\n".join(ns["parts"])
        cases.append(("3 task → 注入简表 markdown", out,
                      lambda s: "【已存 workflow(可跑)】" in s and "| name | id | 节点数 | trigger |" in s
                                and "daily_summary" in s and "t1" in s and "schedule" in s))
    except Exception as e:
        cases.append(("3 task → 注入简表", str(e), lambda d: False))

    # case 3: RPC 抛异常 → 不注入,函数不抛
    def boom_rpc(ext_id, method, params=None, timeout=5.0):
        raise RuntimeError("ext_not_running")
    try:
        ns = {"parts": ["PRE_BLOCK"], "_ext_rpc_call": boom_rpc}
        exec(fn_src, ns)
        ns["_inject"]("hello")
        out = ns["parts"]
        cases.append(("RPC 异常 → 静默", out,
                      lambda p: len(p) == 1 and "已存 workflow" not in "\n".join(p)))
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
        s1_cli_tool_def(),
        s2_cli_handler(),
        s3_cli_helper(),
        s4_cli_rpc_helper(),
        s5_web_inject(),
        s6_py_compile(),
        s7_cli_smoke(),
        s8_web_smoke(),
    ]
    print("=" * 60)
    passed = sum(1 for s in sections if s)
    total = len(sections)
    if passed == total:
        print(f"✓ P2.5+B-4.B run_by_name ALL GREEN ({passed}/{total} sections)")
        return 0
    print(f"✗ P2.5+B-4.B run_by_name FAILED ({passed}/{total} sections)")
    return 1


if __name__ == "__main__":
    sys.exit(main())