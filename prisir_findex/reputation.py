"""文件信誉查询(协助查毒):本地哈希 + 云端信誉,只传哈希、默认不传本体。

红线(与本仓库安全纪律一致):
  - 默认本地,任何云端调用由调用方在「用户显式开」后触发;本模块不自行联网,全靠显式调用。
  - 哈希查询优先:只把 SHA256/MD5 发云端,文件本体不出本机。
  - 上传文件本体(VT)是单独方法 upload_virustotal,必须调用方先拿到用户当场显式同意
    (前端确认卡 / A2H 人审门),本模块只负责执行,不做授权决策。
  - VirusTotal API key 由调用方经 keyring(PrisirKeyStore)取,本模块不持久化、不回显、
    不落日志/审计明文。
  - 哈希计算只读;>HASH_MAX_BYTES 的文件默认不算(可 override,防 IO 拉爆)。

数据源(均需免费 Auth-Key,只传哈希;key 由调用方经 keyring 取,本模块不落):
  - MalwareBazaar (abuse.ch):Auth-Key 按哈希查已知恶意。https://mb-api.abuse.ch/api/v1/
  - VirusTotal v3:哈希查询 /api/v3/files/{sha256};上传 /api/v3/files。
注:abuse.ch 2024 起把 Auth-Key 从可选改为必需,无真正「免 key」的云端哈希查询了,
故做成「双 key 都可选、配一个用一个」,都不配时给诚实提示。
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request

MB_API = "https://mb-api.abuse.ch/api/v1/"
VT_API = "https://www.virustotal.com/api/v3"

# 哈希计算护栏:超过此大小默认跳过(可显式 override)。防对超大文件 IO 拉爆。
HASH_MAX_BYTES = 512 * 1024 * 1024  # 512MB
# VT 免费公开 API 上传上限(普通端点 32MB;更大要换 upload_url 端点,仍受账号配额限)。
VT_UPLOAD_MAX_BYTES = 32 * 1024 * 1024

_UA = {"User-Agent": "prisir-findex-reputation/1.0"}


def hash_file(path: str, override_max: bool = False) -> dict:
    """本地算 SHA256 + MD5(只读)。返回 {ok, sha256, md5, size, error?}。
    >HASH_MAX_BYTES 且未 override 时跳过并说明。"""
    if not path:
        return {"ok": False, "error": "empty path"}
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        return {"ok": False, "error": "不是文件或不存在"}
    size = os.path.getsize(p)
    if size > HASH_MAX_BYTES and not override_max:
        return {"ok": False, "error": f"文件 {size} B 超过 {HASH_MAX_BYTES} B 哈希护栏", "size": size}
    try:
        sha = hashlib.sha256()
        md5 = hashlib.md5()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(chunk)
                md5.update(chunk)
        return {"ok": True, "sha256": sha.hexdigest(), "md5": md5.hexdigest(), "size": size}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"读文件失败: {e}", "size": size}


def _post(url: str, data: dict, headers: dict | None = None, timeout: int = 20) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _get(url: str, headers: dict | None = None, timeout: int = 20) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return e.code, {}


def query_malwarebazaar(sha256: str = "", md5: str = "", api_key: str = "", timeout: int = 20) -> dict:
    """MalwareBazaar 按哈希查询(需免费 Auth-Key)。命中即已知恶意。
    返回 {ok, found, verdict, signature?, first_seen?, error?}。无 key 返回 no_malwarebazaar_key。"""
    h = sha256 or md5
    if not h:
        return {"ok": False, "found": False, "error": "no hash"}
    if not api_key:
        return {"ok": False, "found": False, "error": "no_malwarebazaar_key"}
    try:
        d = _post(MB_API, {"query": "get_info", "hash": h},
                  headers={**_UA, "Auth-Key": api_key}, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "found": False, "error": f"MalwareBazaar 请求失败: {e}"}
    status = d.get("query_status")
    if status == "ok" and d.get("data"):
        rec = d["data"][0]
        return {
            "ok": True, "found": True, "verdict": "malicious",
            "signature": rec.get("signature") or rec.get("file_type") or "已知恶意",
            "first_seen": rec.get("first_seen"),
            "source": "MalwareBazaar",
        }
    if status == "hash_not_found":
        return {"ok": True, "found": False, "verdict": "unknown", "source": "MalwareBazaar"}
    return {"ok": False, "found": False, "error": f"MalwareBazaar 返回: {status}"}


def query_virustotal_hash(sha256: str, api_key: str, timeout: int = 20) -> dict:
    """VT v3 按 SHA256 查文件报告。返回 {ok, found, malicious, total, engines?, error?}。
    found=False 表示 VT 无此文件记录(可考虑上传)。key 由调用方取,本函数不落。"""
    if not api_key:
        return {"ok": False, "found": False, "error": "no_virustotal_key"}
    if not sha256:
        return {"ok": False, "found": False, "error": "no sha256"}
    code, d = _get(f"{VT_API}/files/{sha256}", headers={**_UA, "x-apikey": api_key}, timeout=timeout)
    if code == 404:
        return {"ok": True, "found": False, "verdict": "unknown", "source": "VirusTotal"}
    if code != 200:
        return {"ok": False, "found": False, "error": f"VirusTotal HTTP {code}"}
    attr = (d.get("data") or {}).get("attributes") or {}
    stats = attr.get("last_analysis_stats") or {}
    mal = int(stats.get("malicious", 0) or 0)
    susp = int(stats.get("suspicious", 0) or 0)
    total = sum(int(v or 0) for v in stats.values()) or 0
    verdict = "malicious" if mal > 0 else ("suspicious" if susp > 0 else "clean")
    return {
        "ok": True, "found": True, "verdict": verdict,
        "malicious": mal, "suspicious": susp, "total": total,
        "meaningful_name": attr.get("meaningful_name"),
        "source": "VirusTotal",
    }


def upload_virustotal(path: str, api_key: str, timeout: int = 120) -> dict:
    """上传文件本体到 VT 分析。**必须**调用方先取得用户当场显式同意(确认卡/A2H 门)。
    仅 <VT_UPLOAD_MAX_BYTES。返回 {ok, analysis_id?, error?}。文件本体离开本机,慎用。"""
    if not api_key:
        return {"ok": False, "error": "no_virustotal_key"}
    p = os.path.abspath(path or "")
    if not os.path.isfile(p):
        return {"ok": False, "error": "不是文件或不存在"}
    size = os.path.getsize(p)
    if size > VT_UPLOAD_MAX_BYTES:
        return {"ok": False, "error": f"文件 {size} B 超过 VT 免费上传上限 {VT_UPLOAD_MAX_BYTES} B", "size": size}
    boundary = "----prisirfindex" + hashlib.md5(os.urandom(8)).hexdigest()
    fname = os.path.basename(p)
    try:
        with open(p, "rb") as f:
            data = f.read()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"读文件失败: {e}"}
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{VT_API}/files", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("x-apikey", api_key)
    req.add_header("User-Agent", _UA["User-Agent"])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"VirusTotal 上传 HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"上传失败: {e}"}
    aid = ((d.get("data") or {}).get("id")) or ""
    return {"ok": True, "analysis_id": aid, "size": size, "source": "VirusTotal",
            "hint": "已提交分析,稍后按 SHA256 查询出报告"}
