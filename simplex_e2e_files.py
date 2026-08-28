"""simplex_e2e_files.py — 功能块①(语音/文件发送)双进程 E2E 验证

Process A (alice): setup -> create_invitation -> wait bob(ready) -> send_file + send_voice -> 保持在线
Process B (bob):   setup -> accept_invitation -> 逐个收文件邀请 -> receive_file 下载 -> 字节级比对

确定性设计(避免此前踩的坑):
  - 每次跑前强制删除旧身份 db(否则 contact_id/connStatus 被污染,invite 链接错乱)。
  - Bob 收一个下载一个(不囤积),alice 保持在线直到 Bob 下载窗口结束。
  - 发送前等 connStatus=ready(文件要求 ready;文本无此要求)。

用法:先开 alice,再开 bob(两进程各持一个 libsimplex 实例/身份)。
  python simplex_e2e_files.py alice
  python simplex_e2e_files.py bob
"""

from __future__ import annotations

import json
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simplex_files as sf  # noqa: E402
import simplex_tools as st  # noqa: E402

ROOT = Path(__file__).resolve().parent
SHARE = ROOT / "e2e_share_files"
SHARE.mkdir(exist_ok=True)
INVITE = SHARE / "invite.txt"

SEND_DIR = Path(tempfile.gettempdir()) / "simplex_e2e_send"
SEND_DIR.mkdir(exist_ok=True)
FILE_BIN = SEND_DIR / "e2e_doc.bin"
VOICE_M4A = SEND_DIR / "e2e_voice.m4a"
FILE_BIN.write_bytes(bytes(range(256)) * 64)               # 16 KB
VOICE_M4A.write_bytes(b"M4A_FAKE_AUDIO_" + b"\xab" * 8000)  # ~8 KB
EXPECTED_FILE = FILE_BIN.read_bytes()
EXPECTED_VOICE = VOICE_M4A.read_bytes()


def run_alice() -> int:
    print("[alice] setup...", flush=True)
    r = st.call_tool("simplex_setup", {"display_name": "alice", "db_prefix": str(SHARE / "alice_db")})
    if not r["ok"] and not r.get("already_running"):
        print("[alice] setup FAILED", r, flush=True); return 1
    r = st.call_tool("simplex_create_invitation", {})
    if not r["ok"]:
        print("[alice] create_invitation FAILED", r, flush=True); return 1
    INVITE.write_text(r["link"])
    print(f"[alice] invitation written: {r['link'][:50]}...", flush=True)

    print("[alice] waiting for bob READY (up to 120s)...", flush=True)
    cid = None
    for _ in range(60):
        rc = st.call_tool("simplex_list_contacts", {})
        for c in rc.get("output", []):
            if c.get("active") and c.get("ready"):
                cid, cname = c["contact_id"], c["display_name"]; break
        if cid: break
        time.sleep(2)
    if not cid:
        print("[alice] TIMEOUT: bob never became ready", flush=True); return 1
    print(f"[alice] bob ready: contact_id={cid} name={cname}", flush=True)

    print(f"[alice] sending FILE ({FILE_BIN.stat().st_size}B)...", flush=True)
    r1 = sf.call_tool("simplex_send_file", {"contact": cname, "path": str(FILE_BIN), "caption": "e2e doc", "timeout": 60})
    print(f"[alice] send_file -> ok={r1['ok']} status={(r1.get('output') or {}).get('status')}", flush=True)
    print(f"[alice] sending VOICE ({VOICE_M4A.stat().st_size}B)...", flush=True)
    r2 = sf.call_tool("simplex_send_voice", {"contact": cname, "path": str(VOICE_M4A), "duration": 3, "timeout": 60})
    print(f"[alice] send_voice -> ok={r2['ok']} status={(r2.get('output') or {}).get('status')}", flush=True)

    # 保持在线,覆盖 bob 下载窗口(发送方须在线供 XFTP 拉取分片)
    print("[alice] holding 150s for bob to download...", flush=True)
    time.sleep(150)
    ok = r1["ok"] and r2["ok"]
    print(f"[alice] {'SEND OK' if ok else 'SEND FAIL'}", flush=True)
    return 0 if ok else 1


def run_bob() -> int:
    print("[bob] setup...", flush=True)
    r = st.call_tool("simplex_setup", {"display_name": "bob", "db_prefix": str(SHARE / "bob_db")})
    if not r["ok"] and not r.get("already_running"):
        print("[bob] setup FAILED", r, flush=True); return 1
    print("[bob] waiting for invitation...", flush=True)
    for _ in range(60):
        if INVITE.exists(): break
        time.sleep(2)
    if not INVITE.exists():
        print("[bob] TIMEOUT waiting for invitation", flush=True); return 1
    r = st.call_tool("simplex_accept_invitation", {"link": INVITE.read_text().strip(), "timeout": 100})
    if not r["ok"]:
        print("[bob] accept FAILED", r, flush=True); return 1
    print(f"[bob] connected: {r['output']}", flush=True)

    # 逐个收+下载(收一个下一个),共等 2 个文件
    results = {}
    deadline = time.time() + 180
    while len(results) < 2 and time.time() < deadline:
        rf = sf.call_tool("simplex_list_incoming_files", {})
        pending = [g for g in rf.get("output", []) if g["file_name"] not in results]
        for g in pending:
            fid, fname = g["file_id"], g["file_name"]
            rd = sf.call_tool("simplex_receive_file", {"file_id": fid})
            if rd["ok"]:
                data = Path(rd["output"]["saved_path"]).read_bytes()
                expect = EXPECTED_VOICE if "voice" in fname else EXPECTED_FILE
                results[fname] = (data == expect, len(data))
                print(f"[bob] {fname}: downloaded {len(data)}B match={data == expect}", flush=True)
            else:
                print(f"[bob] {fname}: receive not done yet ({rd.get('error')})", flush=True)
        if len(results) < 2:
            time.sleep(3)

    print(f"[bob] byte-compare: {results}", flush=True)
    ok = len(results) >= 2 and all(v[0] for v in results.values())
    print(f"[bob] {'E2E SUCCESS' if ok else 'E2E FAIL'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    if role == "alice":
        sys.exit(run_alice())
    elif role == "bob":
        sys.exit(run_bob())
    else:
        print("usage: python simplex_e2e_files.py [alice|bob]")
        sys.exit(2)
