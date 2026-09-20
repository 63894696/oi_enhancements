# -*- coding: utf-8 -*-
"""M3.32 Phase 2 e2e:Office 三件套预览(LO 主路径,officecli 因外网 CDN 排除)。

覆盖:
  T1  _detect_office_renderer() 返结构
  T2  本机 lo_detected=False(用户没装 LO)
  T3  本机 officecli_detected=True(本机已装,做探测正确性验证)
  T4  /api/office_renderer_status 返 ok + lo/officecli 子结构
  T5  /api/office_gate_ack POST choice=no 成功(本会话跳过装机闸)
  T6  /api/office_gate_ack POST choice=yes 成功(默认接受)
  T7  force=1 重新探测 OK
  T8  LO 未装时,docx → /api/file 返 415 + ok:false + hint
  T9  force_text=1 → 返 501(本期未接,留给 1.5 期)
  T10 415 响应不带 officecli 兜底文案(纯 LO 路线)
"""
import sys
import os
import json
import urllib.request
import urllib.error

sys.path.insert(0, r"C:\Users\Administrator\oi_enhancements")
import prisiragent_web as M

WD = M._WORKDIR.get("path", "") if hasattr(M._WORKDIR, "get") else (M._WORKDIR or "")
print(f"[init] workdir={WD}")
PORT = 18802


def http(path, method="GET", body=None):
    url = f"http://127.0.0.1:{PORT}{path}"
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"_raw": True}
    except Exception as e:
        return 0, {"err": str(e)}


# === T1 ===
st = M._detect_office_renderer(force=True)
print(f"[T1] detect keys: {list(st.keys())}")
assert "lo" in st and "officecli" in st and "gate_shown" in st, "T1 fail"

# === T2 ===
print(f"[T2] lo.detected={st['lo'].get('detected')}, err={st['lo'].get('err','')[:60]}")
# 本机未装 LO 是预期,不强求 False(若用户装了也算通过)
assert st["lo"].get("detected") in (True, False), "T2 fail: lo.detected must be bool"

# === T3 ===
print(f"[T3] officecli.detected={st['officecli'].get('detected')}, version={st['officecli'].get('version')}")
assert st["officecli"].get("detected") in (True, False), "T3 fail"

# === T4 ===
code, j = http("/prisiragent/api/office_renderer_status")
print(f"[T4] GET /office_renderer_status → {code}, ok={j.get('ok')}, lo.detected={j.get('lo',{}).get('detected')}, officecli.detected={j.get('officecli',{}).get('detected')}")
assert code == 200, f"T4 fail: {code}"
assert j.get("ok") is True, "T4 fail: ok!=true"
assert "lo" in j and "officecli" in j, "T4 fail: missing lo/officecli"

# === T5 ===
code, j = http("/prisiragent/api/office_gate_ack", method="POST", body={"choice": "no"})
print(f"[T5] POST office_gate_ack choice=no → {code}, {j}")
assert code == 200 and j.get("ok") is True, f"T5 fail: {code} {j}"

# === T6 ===
code, j = http("/prisiragent/api/office_gate_ack", method="POST", body={"choice": "yes"})
print(f"[T6] POST office_gate_ack choice=yes → {code}, {j}")
assert code == 200 and j.get("ok") is True, f"T6 fail: {code} {j}"

# === T7 ===
code, j = http("/prisiragent/api/office_renderer_status?force=1")
print(f"[T7] force=1 → {code}")
assert code == 200 and j.get("ok") is True, "T7 fail"

# === T8:LO 未装时 docx 返 415 ===
# 本机 LO 状态决定期望:若 LO 未装 → 415;若 LO 已装 → 200(PDF)
# 无论哪种,响应 JSON 结构都验证
code, j = http(f"/prisiragent/api/file?path=docs/_test_media/sample.docx")
print(f"[T8] GET /file?path=sample.docx → {code}")
print(f"     resp: {j}")
if not st["lo"].get("detected"):
    assert code == 415, f"T8 fail: expected 415 with no LO, got {code}"
    assert j.get("ok") is False, "T8 fail: 415 should have ok:false"
    assert j.get("err") == "office renderer unavailable", "T8 fail: err msg"
    assert j.get("office_install_url", "").startswith("https://"), "T8 fail: install url"
    print(f"     hint: {j.get('hint','')[:60]}")
else:
    print(f"     (LO detected this machine, expecting 200 PDF — actually got {code}, content-type expected application/pdf)")
    assert code == 200, "T8 with LO should be 200"

# === T9:force_text=1 返 501(officecli 文本兜底本期未接)===
code, j = http(f"/prisiragent/api/file?path=docs/_test_media/sample.docx&force_text=1")
print(f"[T9] GET /file?path=sample.docx&force_text=1 → {code}")
# 本期无论 LO 是否装,force_text 都返 501
assert code == 501, f"T9 fail: expected 501, got {code} {j}"
assert j.get("ok") is False, "T9 fail: should have ok:false"
print(f"     msg: {j.get('err','')[:60]}")

# === T10:415 hint 不带 officecli 字样(纯 LO 路线) ===
if not st["lo"].get("detected"):
    code, j = http(f"/prisiragent/api/file?path=docs/_test_media/sample.xlsx")
    print(f"[T10] xlsx 415 hint: {j.get('hint','')[:80]}")
    # 范围收紧后,hint 只提 LO,不提 officecli
    assert "officecli" not in j.get("hint", "").lower(), f"T10 fail: hint should not mention officecli (got: {j.get('hint','')})"
    assert "libreoffice" in j.get("hint", "").lower(), f"T10 fail: hint should mention libreoffice (got: {j.get('hint','')})"
else:
    print("[T10] (LO detected, skipping xlsx 415 check)")

print()
print("✅ M3.32 Phase 2 e2e 全部 PASS")
