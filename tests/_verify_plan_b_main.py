"""tests/_verify_plan_b_main.py — 主进程 _vcs_call 真实集成测试。

策略:把 stub 写到 installer/_staging/bin/PrisirVcsTool.exe 不现实(那是真的 exe 路径),
我们临时把 _vcs_tool_exe_path() monkey-patch 指向 python + stub 文件,然后调
_detect_git() / _detect_office_renderer() / _git_run() / _git_import_get_blob(),
验证代理层返回正确。

跑法:python tests/_verify_plan_b_main.py
预期:全部断言通过,print "ALL OK"
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


# === 写 stub 到磁盘 ===
STUB = r"""
import json, sys, os, subprocess as sp
sys.stdout.reconfigure(line_buffering=True)
print("[stub-vcs] started", file=sys.stderr, flush=True)
print("[stub-vcs] ready", file=sys.stderr, flush=True)
GIT_EXE = os.environ.get("STUB_GIT_EXE", "")
while True:
    line = sys.stdin.readline()
    if not line:
        break
    line = line.strip()
    if line == "quit":
        break
    try:
        req = json.loads(line)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"bad json: {e}"}), flush=True)
        continue
    method = req.get("method", "")
    args = req.get("args", {})
    if method == "detect_vcs":
        # 跑真 git --version
        if not os.path.isfile(GIT_EXE):
            print(json.dumps({"ok": True, "result": {"detected": False, "version": "", "err": "no git"}}), flush=True)
            continue
        try:
            r = sp.run([GIT_EXE, "--version"], capture_output=True, text=True, timeout=1.5)
            if r.returncode == 0:
                print(json.dumps({"ok": True, "result": {"detected": True, "version": r.stdout.strip(), "err": ""}}), flush=True)
            else:
                print(json.dumps({"ok": True, "result": {"detected": False, "version": "", "err": "non-zero exit"}}), flush=True)
        except Exception as e:
            print(json.dumps({"ok": True, "result": {"detected": False, "version": "", "err": str(e)[:200]}}), flush=True)
    elif method == "office_detect":
        print(json.dumps({"ok": True, "result": {"lo": {"detected": False, "version": "", "path": "", "err": "stub no lo"}, "officecli": {"detected": False, "version": "", "path": "", "err": "stub no officecli"}}}), flush=True)
    elif method == "vcs_run":
        # 真跑 git <args> 用真 git
        if not GIT_EXE:
            print(json.dumps({"ok": True, "result": {"ok": False, "err": "no git"}}), flush=True)
            continue
        try:
            r = sp.run([GIT_EXE] + args.get("args", []), cwd=args.get("cwd", ""), capture_output=True, text=True, timeout=args.get("timeout", 5.0))
            print(json.dumps({"ok": True, "result": {"ok": r.returncode == 0, "returncode": r.returncode, "stdout": r.stdout or "", "stderr": (r.stderr or "")[:500]}}), flush=True)
        except Exception as e:
            print(json.dumps({"ok": True, "result": {"ok": False, "err": str(e)[:200]}}), flush=True)
    elif method == "vcs_get_blob":
        if not GIT_EXE:
            print(json.dumps({"ok": True, "result": {"ok": False, "err": "no git"}}), flush=True)
            continue
        try:
            import base64
            r = sp.run([GIT_EXE, "cat-file", "-p", args.get("blob_sha", "")], cwd=args.get("repo_root", ""), capture_output=True, timeout=5)
            if r.returncode != 0:
                print(json.dumps({"ok": True, "result": {"ok": False, "err": "git cat-file failed"}}), flush=True)
            else:
                print(json.dumps({"ok": True, "result": {"ok": True, "data_b64": base64.b64encode(r.stdout or b"").decode("ascii")}}), flush=True)
        except Exception as e:
            print(json.dumps({"ok": True, "result": {"ok": False, "err": str(e)[:200]}}), flush=True)
    else:
        print(json.dumps({"ok": False, "error": f"unknown method: {method}"}), flush=True)
"""


def main():
    stub_path = os.path.join(tempfile.gettempdir(), "_plan_b_stub_vcs.py")
    with open(stub_path, "w", encoding="utf-8") as f:
        f.write(STUB)
    # 找真 git
    git_exe = os.path.join(ROOT, "installer", "_staging", "bin", "git", "mingw64", "bin", "git.exe")
    if not os.path.isfile(git_exe):
        # 退而求其次:系统 git
        for p in [r"C:\Program Files\Git\mingw64\bin\git.exe", "/usr/bin/git"]:
            if os.path.isfile(p):
                git_exe = p
                break
    os.environ["STUB_GIT_EXE"] = git_exe
    print(f"[test] git_exe = {git_exe}")
    print(f"[test] stub_path = {stub_path}")

    # 启动 stub 子进程,监听 stdout 转给主进程 _vcs_call
    # 我们直接替换 _vcs_tool_exe_path 让他指向 python + stub_path
    import importlib.util
    spec = importlib.util.spec_from_file_location("prisIragent_web", os.path.join(ROOT, "prisIragent_web.py"))
    pw = importlib.util.module_from_spec(spec)
    sys.modules["prisIragent_web"] = pw
    spec.loader.exec_module(pw)

    real_path = pw._vcs_tool_exe_path
    pw._vcs_tool_exe_path = lambda: f"{sys.executable}"
    real_spawn = pw._vcs_spawn
    def fake_spawn():
        import subprocess as _sp
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = getattr(_sp, "CREATE_NO_WINDOW", 0)
        return _sp.Popen(
            [sys.executable, stub_path], stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE,
            text=True, encoding="utf-8", errors="replace", **kw,
        )
    pw._vcs_spawn = fake_spawn

    try:
        # ===== _detect_git =====
        pw._GIT_STATE.update({"detected": None, "version": "", "last_check_ts": 0.0})
        d = pw._detect_git(force=True)
        print(f"[1] _detect_git: {d}")
        assert d["detected"], f"want True, got {d}"
        assert "2.51" in (d["version"] or "") or "git version" in (d["version"] or "")
        # ===== _detect_office_renderer =====
        d = pw._detect_office_renderer(force=True)
        print(f"[2] _detect_office_renderer: {d}")
        assert "lo" in d and "officecli" in d
        # ===== _git_run =====
        r = pw._git_run(["--version"], cwd=ROOT, timeout=3.0)
        print(f"[3] _git_run: {r}")
        assert r["ok"], f"want ok, got {r}"
        assert "git version" in (r.get("stdout") or "")
        # ===== _git_import_get_blob(真测,先临时建个 git 仓)=====
        tmp_repo = os.path.join(tempfile.gettempdir(), "_plan_b_repo_test")
        if os.path.isdir(tmp_repo):
            import shutil
            shutil.rmtree(tmp_repo, ignore_errors=True)
        os.makedirs(tmp_repo, exist_ok=True)
        # git init
        r = pw._git_run(["init", "-q"], cwd=tmp_repo, timeout=3.0)
        assert r["ok"], f"git init: {r}"
        # git config user.email/user.name
        for k, v in [("user.email", "test@x"), ("user.name", "test")]:
            r = pw._git_run(["config", "user." + k.split(".")[1], v], cwd=tmp_repo, timeout=3.0)
            assert r["ok"], f"git config {k}: {r}"
        # 写一个文件 → add → commit
        test_file = os.path.join(tmp_repo, "hello.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("plan_b_proxy_smoke_ok\n")
        r = pw._git_run(["add", "hello.txt"], cwd=tmp_repo, timeout=3.0)
        assert r["ok"], f"git add: {r}"
        r = pw._git_run(["commit", "-q", "-m", "init"], cwd=tmp_repo, timeout=3.0)
        assert r["ok"], f"git commit: {r}"
        # 拿 HEAD blob sha
        r = pw._git_run(["ls-tree", "HEAD", "hello.txt"], cwd=tmp_repo, timeout=3.0)
        assert r["ok"], f"git ls-tree: {r}"
        sha = (r["stdout"] or "").split()[2]
        print(f"[4] blob sha: {sha}")
        # 调 _git_import_get_blob
        content = pw._git_import_get_blob(tmp_repo, sha)
        print(f"[5] _git_import_get_blob: {content!r}")
        assert content is not None, "want content"
        assert "plan_b_proxy_smoke_ok" in content, f"want plan_b_proxy_smoke_ok, got {content!r}"
        # ===== 子进程死掉后自动重启 =====
        # 杀掉当前子进程,再调一次 _detect_git
        with pw._VCS_LOCK:
            if pw._VCS_PROC is not None:
                try:
                    pw._VCS_PROC.kill()
                except Exception:
                    pass
            pw._VCS_PROC = None
        d = pw._detect_git(force=True)
        print(f"[6] _detect_git after kill: {d}")
        assert d["detected"]
        print("ALL OK")
    finally:
        pw._vcs_tool_exe_path = real_path
        pw._vcs_spawn = real_spawn
        # 清 stub
        try:
            os.remove(stub_path)
        except Exception:
            pass
        # 关掉子进程
        with pw._VCS_LOCK:
            if pw._VCS_PROC is not None:
                try:
                    pw._VCS_PROC.kill()
                except Exception:
                    pass
                pw._VCS_PROC = None


if __name__ == "__main__":
    main()