"""tests/_verify_plan_b_proxy.py — 验证思路 B stdio JSON-RPC 协议联通性。

不依赖 PyInstaller,直接用 python 子进程模拟 PrisirVcsTool.exe,
验证:
  1. stdio JSON-RPC 协议能跑通 detect_vcs / office_detect / vcs_run / vcs_get_blob / crash
  2. 真 prisIragent_vcs.py 在子进程模式也能正常 serve

跑法:python tests/_verify_plan_b_proxy.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# === stub:模拟 PrisirVcsTool.exe ===
STUB = r"""
import json, sys, base64
sys.stdout.reconfigure(line_buffering=True)
print("[stub] started", file=sys.stderr, flush=True)
print("[stub] ready", file=sys.stderr, flush=True)
while True:
    line = sys.stdin.readline()
    if not line:
        print("[stub] eof", file=sys.stderr, flush=True)
        break
    line = line.strip()
    if line == "quit":
        break
    try:
        req = json.loads(line)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"bad json: {e}"}))
        sys.stdout.flush()
        continue
    method = req.get("method", "")
    args = req.get("args", {})
    if method == "detect_vcs":
        print(json.dumps({"ok": True, "result": {"detected": True, "version": "2.51.0", "err": ""}}))
    elif method == "office_detect":
        print(json.dumps({"ok": True, "result": {"lo": {"detected": False, "version": "", "path": "", "err": "no lo"}, "officecli": {"detected": True, "version": "1.0", "path": "C:/x/officecli.exe", "err": ""}}}))
    elif method == "vcs_run":
        print(json.dumps({"ok": True, "result": {"ok": True, "returncode": 0, "stdout": "abc", "stderr": ""}}))
    elif method == "vcs_get_blob":
        body = b"hello world"
        print(json.dumps({"ok": True, "result": {"ok": True, "data_b64": base64.b64encode(body).decode("ascii")}}))
    elif method == "crash_after":
        print(json.dumps({"ok": True, "result": {"ok": True}}))
        sys.stdout.flush()
        sys.exit(99)
    else:
        print(json.dumps({"ok": False, "error": f"unknown method: {method}"}))
    sys.stdout.flush()
"""


def call_rpc(p, method, args, timeout=5.0):
    """写一行 JSON,读一行 JSON。"""
    req = json.dumps({"method": method, "args": args or {}}, ensure_ascii=False)
    p.stdin.write(req + "\n")
    p.stdin.flush()
    line = p.stdout.readline()
    if not line:
        return {"ok": False, "err": "eof"}
    return json.loads(line.strip())


def test_stub():
    print("=== test 1: stub 走 stdio JSON-RPC ===")
    stub_path = os.path.join(tempfile.gettempdir(), "_plan_b_stub.py")
    with open(stub_path, "w", encoding="utf-8") as f:
        f.write(STUB)
    p = subprocess.Popen(
        [sys.executable, stub_path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    r = call_rpc(p, "detect_vcs", {"force": True})
    assert r.get("ok"), f"detect_vcs fail: {r}"
    assert r["result"]["detected"] is True
    assert r["result"]["version"] == "2.51.0"
    print(f"  [1] detect_vcs: {r['result']}")
    r = call_rpc(p, "vcs_get_blob", {})
    assert r.get("ok")
    import base64
    data = base64.b64decode(r["result"]["data_b64"])
    assert data == b"hello world", f"got {data!r}"
    print(f"  [2] vcs_get_blob: {data!r}")
    r = call_rpc(p, "vcs_run", {"args": ["ls"], "cwd": "/tmp"})
    assert r.get("ok")
    assert r["result"]["stdout"] == "abc"
    print(f"  [3] vcs_run: {r['result']}")
    # 让子进程崩溃
    r = call_rpc(p, "crash_after", {})
    print(f"  [4] crash_after last resp: {r}")
    p.stdin.close()
    rc = p.wait(timeout=3)
    assert rc == 99, f"want rc=99, got {rc}"
    print(f"  [5] crash_after exit code: {rc}")
    os.remove(stub_path)


def test_real_vcs_script():
    print("=== test 2: 真实 prisIragent_vcs.py ===")
    vcs_py = os.path.join(ROOT, "prisIragent_vcs.py")
    p = subprocess.Popen(
        [sys.executable, vcs_py],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    # 子进程启动时会 print 到 stderr,我们不读 stderr(避免阻塞),直接发 method
    r = call_rpc(p, "detect_vcs", {"force": True})
    print(f"  [1] real vcs detect_vcs: {r}")
    assert r.get("ok"), f"detect_vcs via real vcs script fail: {r}"
    r2 = call_rpc(p, "office_detect", {"force": True})
    print(f"  [2] real vcs office_detect: {r2}")
    assert r2.get("ok"), f"office_detect via real vcs script fail: {r2}"
    # 优雅退出
    p.stdin.write("quit\n")
    p.stdin.flush()
    p.stdin.close()
    rc = p.wait(timeout=3)
    assert rc == 0, f"want rc=0, got {rc}"
    print(f"  [3] real vcs exit: {rc}")


if __name__ == "__main__":
    test_stub()
    test_real_vcs_script()
    print("ALL OK")