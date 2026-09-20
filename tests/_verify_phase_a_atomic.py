"""P2.5+1 Phase A 6 原子扩展 E2E 验证

直接用 stdio NDJSON 喂子进程,验证每个扩展的命令可调通。
不依赖主进程,纯单测形式。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"C:\Users\Administrator\oi_enhancements")
EXT_HOME = Path(r"C:\Users\Administrator\.prisir\extensions")

CASES = [
    # (ext_id, [ {method, params, expect_keys, expect_ok?} ])
    ("process-scan", [
        ("ps.list", {"limit": 5}, ["processes", "total"], True),
        ("ps.find", {"name": "powershell"}, ["matches"], True),
        ("ps.find", {"name": "nonexistent_xyz_12345"}, ["matches"], True),  # 无匹配也要 ok
    ]),
    ("window-list", [
        ("win.list", {"filter": ""}, ["windows", "total"], True),
        ("win.find", {"title": "Visual Studio"}, ["window"], True),
    ]),
    ("keystroke-emit", [
        # key.press 不真按(测试副作用可控)— 测逻辑路径:sendkeys 到非法 key 应返 error
        ("key.press", {"key": "invalid_xyz"}, [], False),  # 期望 error
        # key.hotkey 测解析:非法 combo 返 error
        ("key.hotkey", {"combo": ""}, [], False),
    ]),
    ("app-launcher", [
        ("app.recent", {}, ["items", "total"], True),
        ("app.open_path", {"path": "C:\\Windows"}, ["ok"], True),
    ]),
    ("scheduled-task", [
        ("sched.list", {}, ["tasks", "total"], True),
        # sched.create 带当时间隔也会失败(只读)— 跳过真实创建,改测 bad input
        ("sched.create", {"name": "e2e_test_bad", "when": "bad_when"}, ["error"], False),
    ]),
    ("http-request", [
        ("http.fetch", {}, [], False),  # 期望 error: url required
        ("http.fetch", {"url": "http://127.0.0.1:1"}, [], False),  # blocked
        ("http.whitelist", {"add": "api.github.com"}, ["allowlist"], True),
        ("http.fetch", {"url": "https://api.github.com/zen"}, ["status"], True),
        ("http.whitelist", {"remove": "api.github.com"}, ["allowlist"], True),
    ]),
]


def call(ext_id: str, method: str, params: dict, timeout: float = 8.0) -> dict:
    """启动 ext 子进程,发 initialize + 一次 request,等 reply。"""
    ext_dir = EXT_HOME / ext_id
    env = os.environ.copy()
    env["PRISIR_EXT_ID"] = ext_id
    env["PRISIR_EXT_VERSION"] = "0.1.0"
    env["PRISIR_EXT_HOME"] = str(ext_dir)
    env["PRISIR_WORKDIR"] = str(ROOT)

    proc = subprocess.Popen(
        ["node", "index.js"],
        cwd=str(ext_dir),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    try:
        # 1. initialize notification(主进程通常先推)
        proc.stdin.write((json.dumps({
            "jsonrpc": "2.0", "method": "initialize", "params": {"session_id": "e2e"}
        }) + "\n").encode("utf-8"))
        proc.stdin.flush()
        time.sleep(0.3)

        # 2. invoke request
        req_id = 1
        proc.stdin.write((json.dumps({
            "jsonrpc": "2.0", "id": req_id, "method": method,
            "params": {"session_id": "e2e", **params}
        }) + "\n").encode("utf-8"))
        proc.stdin.flush()

        # 3. 读 stdout 直到拿到这条 id 的 reply
        deadline = time.time() + timeout
        buf = b""
        while time.time() < deadline:
            try:
                chunk = proc.stdout.read(4096)
            except Exception:
                chunk = b""
            if not chunk:
                # 给 node 一点时间 flush
                time.sleep(0.1)
                if proc.poll() is not None:
                    break
                continue
            buf += chunk
            for line in buf.split(b"\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if msg.get("id") == req_id:
                    if "result" in msg:
                        return msg["result"]
                    if "error" in msg:
                        return {"_rpc_error": msg["error"].get("message", "?")}
        return {"_timeout": True, "_partial": buf.decode("utf-8", "replace")[:200]}
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


def main():
    passed, failed = 0, 0
    for ext_id, cases in CASES:
        print(f"\n=== {ext_id} ===")
        for method, params, expect_keys, expect_ok in cases:
            t0 = time.time()
            r = call(ext_id, method, params)
            dt = (time.time() - t0) * 1000

            if "_timeout" in r:
                tag, note = "✗", "TIMEOUT"
                ok = False
            elif "_rpc_error" in r:
                tag = "✓" if not expect_ok else "✗"
                note = f"rpc_err: {r['_rpc_error'][:60]}"
                ok = (not expect_ok)
            elif "error" in r and expect_ok:
                tag, note = "✗", f"err: {r['error'][:60]}"
                ok = False
            elif "error" in r and not expect_ok:
                tag, note = "✓", f"err: {r['error'][:60]}"
                ok = True
            else:
                missing = [k for k in expect_keys if k not in r]
                if missing:
                    tag, note = "✗", f"missing keys: {missing}"
                    ok = False
                else:
                    tag, note = "✓", "ok"
                    ok = True

            params_short = json.dumps(params, ensure_ascii=False)[:50]
            print(f"  {tag} {method:24s} ({dt:5.0f}ms) {note:50s}  {params_short}")
            if ok:
                passed += 1
            else:
                failed += 1

    print(f"\n=== Total: {passed} passed, {failed} failed ===")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
