"""E2E: 签名清单文件传输(身份+出处验证),双进程(agent A 发签名文件,agent B 验证)。

流程:
  A: setup → trust_establish(B) → 造一个测试文件 → send_file_signed → 通知
  B: setup → 收信任根 → 收文件 → receive_file 下载 → verify_received_file(出处+一致性)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simplex_tools as st  # noqa: E402
import simplex_files as sf  # noqa: E402
import simplex_integrity as si  # noqa: E402

SHARE = Path.home() / ".local" / "share" / "aureon" / "simplex" / "_integrity_e2e"
LINK = SHARE / "link.txt"
TESTFILE = SHARE / "payload.bin"
READY = SHARE / "a_ready"


def run_a() -> int:
    print("[A] setup")
    st.call_tool("simplex_setup", {"display_name": "alice", "db_prefix": str(SHARE / "a_db")})
    print("[A] create invitation")
    r = st.call_tool("simplex_create_invitation", {})
    LINK.write_text(r["link"], encoding="utf-8")
    # 造测试文件(已知内容,便于 B 端比对)
    TESTFILE.write_bytes(os.urandom(20000))
    print("[A] wait B connect")
    deadline = time.time() + 120
    contact = None
    while time.time() < deadline:
        lc = st.call_tool("simplex_list_contacts", {})
        act = [c for c in lc.get("output", []) if c.get("active")]
        if act:
            contact = act[0]; break
        time.sleep(2)
    if not contact:
        print("[A] TIMEOUT"); return 1
    cid = contact["contact_id"]
    print(f"[A] connected {contact}, establish trust")
    tr = si.call_tool("simplex_trust_establish", {"contact": str(cid)})
    print("[A] trust:", tr["ok"])
    # 复制信任根到共享目录,模拟"双方 agent 对齐同一根"(真实部署中带外预置)
    import shutil
    shutil.copy(Path.home() / ".local/share/aureon/simplex/integrity_trust.json", SHARE / "trust.json")
    print("[A] send_file_signed")
    sr = si.call_tool("simplex_send_file_signed", {"contact": str(cid), "path": str(TESTFILE)})
    print("[A] send_signed ok:", sr["ok"], sr.get("diagnosable", "")[:120])
    READY.write_text("1")
    time.sleep(20)  # 留时间给 B 下载验证
    return 0 if sr["ok"] else 1


def run_b() -> int:
    print("[B] setup")
    st.call_tool("simplex_setup", {"display_name": "bob", "db_prefix": str(SHARE / "b_db")})
    deadline = time.time() + 90
    while not LINK.exists() and time.time() < deadline:
        time.sleep(1)
    link = LINK.read_text(encoding="utf-8").strip()
    print("[B] accept")
    r = st.call_tool("simplex_accept_invitation", {"link": link, "timeout": 100})
    if not r["ok"]:
        print("[B] accept FAIL", r); return 1
    cid = r["output"]["contact_id"]
    print(f"[B] connected {r['output']}")
    # 对齐信任根(模拟带外预置同一根)
    deadline = time.time() + 60
    trust_src = SHARE / "trust.json"
    while not trust_src.exists() and time.time() < deadline:
        time.sleep(1)
    import shutil, json
    tdata = json.loads(trust_src.read_text(encoding="utf-8"))
    # A 的信任根 contact_id 是 A 侧的;B 侧同一 contact 的 id 不同,需重映射到 B 的 cid
    akey = next(iter(tdata["contacts"].values()))["key"]
    si._set_trust_key(cid, akey, "alice")
    print("[B] trust aligned")
    # 等文件邀请
    print("[B] wait file invitation")
    deadline = time.time() + 90
    fid = None
    while time.time() < deadline:
        lf = sf.call_tool("simplex_list_incoming_files", {})
        items = lf.get("output", [])
        if items:
            fid = items[0].get("file_id") or items[0].get("fileId")
            break
        time.sleep(2)
    if fid is None:
        print("[B] no file invitation"); return 1
    print(f"[B] receive file_id={fid}")
    rr = sf.call_tool("simplex_receive_file", {"file_id": fid, "timeout": 60})
    if not rr["ok"]:
        print("[B] receive FAIL", rr); return 1
    saved = rr["output"].get("saved_path")
    print(f"[B] downloaded -> {saved}")
    # 等签名清单消息
    time.sleep(3)
    print("[B] verify_received_file")
    vr = si.call_tool("simplex_verify_received_file", {"contact": str(cid), "path": saved})
    print("[B] verify:", vr["ok"], vr.get("diagnosable", "")[:160])
    if vr["ok"] and vr["output"].get("verified"):
        print("[B] ✓✓ E2E 签名文件传输验证通过(出处+一致性)")
        return 0
    print("[B] ✗ 验证未通过"); return 1


if __name__ == "__main__":
    SHARE.mkdir(parents=True, exist_ok=True)
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    if role == "a":
        sys.exit(run_a())
    elif role == "b":
        sys.exit(run_b())
    else:
        print("usage: simplex_integrity_e2e.py [a|b]"); sys.exit(2)
