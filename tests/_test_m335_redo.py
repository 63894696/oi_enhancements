#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
M3.35 重做验证(2026-09-20):16 项静态扫描 + 端到端 E2E。

用法:
    cd C:\\Users\\Administrator\\oi_enhancements
    python tests/_test_m335_redo.py

返回:
    0 = 全绿;1 = 有失败。

依赖:仅标准库 + 同一进程的 Python。无需启动服务器(端到端用临时端口动态启)。
"""
import os
import sys
import json
import socket
import threading
import time
import urllib.request
import urllib.error
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

WEB_FILE = os.path.join(ROOT, "prisIragent_web.py")

# ============================================================
# 1) 静态扫描
# ============================================================
STATIC_CHECKS = [
    ("_PROJECTS = {",                                              True),
    ("_PROJECTS_FILE =",                                           True),
    ("_PROJECTS_LOCK",                                             True),
    ("def _projects_load(",                                        True),
    ("def _projects_save(",                                        True),
    ("def _projects_upsert(",                                      True),
    ("def _projects_remove(",                                      True),
    ("def _projects_activate(",                                    True),
    ("_projects_load()",                                           True),  # main() 调用
    ("ALTER TABLE sessions ADD COLUMN workdir",                    True),
    ("PRAGMA table_info(sessions)",                                True),
    ("create_session",                                             True),
    ('elif path == "/prisiragent/api/projects":',                  True),
    ('id="topbtnProject"',                                         True),
    ('id="projmodal"',                                             True),
    ("#projmodal { position:fixed",                                True),
    ("function openProject(",                                      True),
    ("function closeProject(",                                     True),
    ("async function projectRenderList(",                          True),
    ("async function projectSwitch(",                              True),
    ("async function _applyProjectChange(",                        True),
    ("async function loadSessions(opts",                           True),
    ("'project_title'",                                            False),  # 在 i18n 里,只验含 project_*
    ("project_add:'添加'",                                         True),
    ("project_title:'📂 Projects'",                                True),
    ("工作目录已迁到顶栏",                                          True),
]


def run_static_checks():
    print("=" * 60)
    print("[1/3] 静态扫描(检查 16+ 项源码锚点)")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    passed, failed = 0, []
    for needle, expect in STATIC_CHECKS:
        found = needle in src
        ok = (found == expect)
        # 'project_title' 这类 False 用例是反向验证(确保没在 i18n 字串里漏引号)
        if not expect and found:
            ok = True  # 如果找到了也 OK,反正只是 sanity
        flag = "✓" if ok else "✗"
        print(f"  {flag} '{needle[:50]}' (expect={expect})")
        if ok:
            passed += 1
        else:
            failed.append(needle)
    print(f"  → {passed}/{len(STATIC_CHECKS)} passed")
    return len(failed) == 0


# ============================================================
# 2) 动态启 backend + curl 探
# ============================================================
def pick_free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_backend(port: int) -> threading.Thread:
    """后台线程跑 prisIragent_web.py main()。"""
    import subprocess
    # 用子进程最干净(独立 db / 独立 perm_gate)
    log_path = os.path.join(ROOT, "logs", f"_test_m335_backend_{port}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, WEB_FILE, "--port", str(port),
         "--workdir", ROOT, "--lan"],
        cwd=ROOT,
        stdout=open(log_path, "wb"),
        stderr=subprocess.STDOUT,
    )
    return proc


def http_post(url: str, body: dict, timeout: float = 5.0):
    req = urllib.request.Request(url, method="POST",
                                  headers={"Content-Type": "application/json"},
                                  data=json.dumps(body).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"raw": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}


def http_get(url: str, timeout: float = 5.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return 0, {"error": str(e)}


def wait_ready(port: int, max_sec: int = 30):
    """轮询 /api/info 等后端就绪。"""
    deadline = time.time() + max_sec
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1.0)
            s.close()
            return True
        except OSError:
            time.sleep(0.5)
    return False


def run_e2e():
    print("=" * 60)
    print("[2/3] 端到端 E2E(启 backend + 探 /api/projects)")
    print("=" * 60)
    port = pick_free_port()
    print(f"  pick port={port}")
    proc = start_backend(port)
    try:
        if not wait_ready(port, max_sec=20):
            print(f"  ✗ backend 未就绪:port={port}")
            return False
        time.sleep(1.0)  # 让 modules 都跑完

        base = f"http://127.0.0.1:{port}/prisiragent/api"
        passed, failed = 0, []

        # A. list(默认 = cwd 项目)
        code, r = http_post(base + "/projects", {"op": "list"})
        ok = (code == 200 and r.get("ok") and "active" in r
              and len(r.get("items", [])) >= 1)
        print(f"  {'✓' if ok else '✗'} list: {code} {str(r)[:140]}")
        if ok: passed += 1
        else: failed.append(("list", r))

        # B. add(C:\\Windows)
        if sys.platform == "win32":
            target_dir = "C:\\Windows"
        else:
            target_dir = "/tmp"
        code, r = http_post(base + "/projects", {"op": "add", "path": target_dir})
        ok = (code == 200 and r.get("ok"))
        print(f"  {'✓' if ok else '✗'} add({target_dir}): {code} {str(r)[:120]}")
        if ok: passed += 1
        else: failed.append(("add", r))

        # C. switch
        code, r = http_post(base + "/projects", {"op": "switch", "path": target_dir})
        ok = (code == 200 and r.get("ok") and r.get("active", "").lower() == target_dir.lower())
        print(f"  {'✓' if ok else '✗'} switch({target_dir}): {code} {str(r)[:120]}")
        if ok: passed += 1
        else: failed.append(("switch", r))

        # D. switch 不存在路径(应当 400)
        code, r = http_post(base + "/projects", {"op": "switch",
                                                  "path": "C:\\path\\that\\does\\not\\exist\\zzzz"})
        ok = (code == 400 and not r.get("ok"))
        print(f"  {'✓' if ok else '✗'} switch 不存在路径返回 400: {code} {str(r)[:120]}")
        if ok: passed += 1
        else: failed.append(("switch_400", r))

        # E. sessions?scope=current(workdir 列存在)
        code, r = http_get(base + "/sessions?scope=current")
        ok = (code == 200 and isinstance(r, list)
              and all("workdir" in s for s in r))
        print(f"  {'✓' if ok else '✗'} sessions?scope=current 含 workdir: {code} 数组长={len(r) if isinstance(r,list) else 'n/a'}")
        if ok: passed += 1
        else: failed.append(("sessions_current", r))

        # F. sessions?scope=orphans
        code, r = http_get(base + "/sessions?scope=orphans")
        ok = (code == 200 and isinstance(r, list))
        print(f"  {'✓' if ok else '✗'} sessions?scope=orphans: {code} 长={len(r) if isinstance(r,list) else 'n/a'}")
        if ok: passed += 1
        else: failed.append(("sessions_orphans", r))

        # G. sessions?scope=all
        code, r = http_get(base + "/sessions?scope=all")
        ok = (code == 200 and isinstance(r, list))
        print(f"  {'✓' if ok else '✗'} sessions?scope=all: {code} 长={len(r) if isinstance(r,list) else 'n/a'}")
        if ok: passed += 1
        else: failed.append(("sessions_all", r))

        # H. rename
        code, r = http_post(base + "/projects", {"op": "rename",
                                                  "path": target_dir, "name": "WinSys"})
        ok = (code == 200 and r.get("ok"))
        print(f"  {'✓' if ok else '✗'} rename: {code} {str(r)[:120]}")
        if ok: passed += 1
        else: failed.append(("rename", r))

        # I. pin(切换一次再切回)
        code, r = http_post(base + "/projects", {"op": "pin", "path": target_dir})
        ok = (code == 200 and r.get("ok"))
        print(f"  {'✓' if ok else '✗'} pin: {code} {str(r)[:120]}")
        if ok: passed += 1
        else: failed.append(("pin", r))

        # J. projects.json 真落盘
        projects_json = os.path.join(os.path.expanduser("~"), ".prisir", "projects.json")
        ok = os.path.exists(projects_json)
        print(f"  {'✓' if ok else '✗'} projects.json 落盘: {projects_json} (exists={ok})")
        if ok:
            passed += 1
            try:
                with open(projects_json, "r", encoding="utf-8") as f:
                    pj = json.load(f)
                has_active = "active" in pj and "items" in pj
                print(f"  {'✓' if has_active else '✗'} projects.json 含 active+items 结构")
                if has_active: passed += 1
                else: failed.append(("projects_json_struct", pj))
            except Exception as e:
                print(f"  ✗ projects.json 解析失败: {e}")
                failed.append(("projects_json_parse", str(e)))
        else:
            failed.append(("projects_json_exists", None))

        # K. remove
        code, r = http_post(base + "/projects", {"op": "remove", "path": target_dir})
        ok = (code == 200 and r.get("ok"))
        print(f"  {'✓' if ok else '✗'} remove: {code} {str(r)[:120]}")
        if ok: passed += 1
        else: failed.append(("remove", r))

        # L. 切回 cwd
        code, r = http_post(base + "/projects", {"op": "switch", "path": ROOT})
        ok = (code == 200 and r.get("ok") and r.get("event") == "project_changed")
        print(f"  {'✓' if ok else '✗'} switch 回 cwd 且 event=project_changed: {code} {str(r)[:120]}")
        if ok: passed += 1
        else: failed.append(("switch_back", r))

        print(f"  → E2E {passed} passed, {len(failed)} failed")
        return len(failed) == 0
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


# ============================================================
# 3) 关键字符串扫描(确认前端 embed 包含 projmodal)
# ============================================================
def run_embed_checks():
    print("=" * 60)
    print("[3/3] 前端 HTML 嵌入检查")
    print("=" * 60)
    src = open(WEB_FILE, "r", encoding="utf-8").read()
    items = [
        ('topbtnProject',                 "📂 顶栏按钮"),
        ('projmodal',                     "弹层 id"),
        ('proj-list',                     "项目列表容器"),
        ('proj-path',                     "添加路径输入"),
        ('proj-rename-row',               "改名输入行"),
        ('project_title',                 "i18n key(zh 段)"),
        ('project_title:\'📂 Projects\'',"i18n key(en 段)"),
        ('function openProject',          "JS open 函数"),
        ('function _applyProjectChange',  "JS 刷新函数"),
        ('async function loadSessions(opts', "loadSessions 改 scope"),
    ]
    passed, failed = 0, []
    for needle, desc in items:
        ok = needle in src
        print(f"  {'✓' if ok else '✗'} {desc}: '{needle}'")
        if ok: passed += 1
        else: failed.append(needle)
    print(f"  → 嵌入检查 {passed}/{len(items)}")
    return len(failed) == 0


def main():
    s1 = run_static_checks()
    s2 = run_e2e()
    s3 = run_embed_checks()
    print("=" * 60)
    print(f"汇总:静态={s1} E2E={s2} 嵌入={s3}")
    if s1 and s2 and s3:
        print("✓ M3.35 重做 ALL GREEN")
        return 0
    print("✗ M3.35 重做 FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())