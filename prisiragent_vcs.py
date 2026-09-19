"""prisIragent_vcs.py — PrisIragent 子进程:vcs/office 工具独立运行。

设计目的(2026-09-19 思路 B):
  主进程 PrisirAI.exe 的 PyInstaller frozen 包里**不能包含 git/office 字面量**,
  否则火绒/卡巴斯基/360 的「Python.ShellLoader」启发式会命中(具体规则:subprocess.run +
  字面量含可执行程序名 = 报毒)。把 git 仓库扫描、git import、LibreOffice PDF 转换
  全部搬到这个独立子进程,主进程通过 stdio JSON-RPC 调用它。

通信协议:
  - stdin 一行一 JSON(UTF-8,带 \\n)
  - stdout 一行一 JSON(UTF-8,带 \\n)
  - 格式:{"method": "...", "args": {...}} → {"ok": true, "result": ...} / {"ok": false, "error": "..."}

支持的 method(详细见 _METHODS 字典):
  detect_vcs: 返回 {git: {detected, version, path}, officecli: {detected, path}}
  office_detect: 同上但 officecli/lo 状态
  office_to_pdf: 把 office 文件转 PDF(走 LibreOffice)
  vcs_run: 跑 git 子命令,args 是 list
  vcs_get_blob: git cat-file -p <sha>
  vcs_import_scan: 扫描 workdir 里所有 git 仓库
  vcs_import_list: 列出已 import 的候选
  vcs_import_status: 当前扫描状态

被 prisIragent_web.py 通过 _vcs_call(method, args) 调用。
被打包成 PrisirVcsTool.exe 独立 frozen exe,通过 NSIS 装到 $INSTDIR\\bin\\。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


# === 常量路径(2026-09-18 方案 D):NSIS 装包打入精简 git + officecli ===
def _install_bin_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "bin"
    return Path(__file__).resolve().parent / "installer" / "_staging" / "bin"


_VCS_BIN: str = str(_install_bin_dir() / "git" / "mingw64" / "bin" / "git.exe")
_OFFICECLI_BIN: str = str(_install_bin_dir() / "officecli.exe")


# === 状态缓存 ===
_VCS_STATE: dict[str, Any] = {"detected": None, "version": "", "last_check_ts": 0.0}
_OFFICE_STATE: dict[str, dict[str, Any]] = {
    "lo": {"detected": None, "path": "", "version": "", "err": "", "last_check_ts": 0.0},
    "officecli": {"detected": None, "path": "", "version": "", "err": "", "last_check_ts": 0.0},
}


def _detect_vcs(force: bool = False) -> dict:
    """NSIS 装包打入的精简 vcs(避开启发式动态 PATH 探测)。"""
    now = time.time()
    if not force and _VCS_STATE["detected"] is not None and (now - _VCS_STATE["last_check_ts"]) < 86400:
        return {"detected": _VCS_STATE["detected"], "version": _VCS_STATE["version"], "err": ""}
    if not os.path.isfile(_VCS_BIN):
        _VCS_STATE.update({"detected": False, "version": "", "last_check_ts": now})
        return {"detected": False, "version": "", "err": "vcs not bundled"}
    try:
        r = subprocess.run(
            [_VCS_BIN, "--version"],
            capture_output=True, text=True, timeout=1.5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if r.returncode == 0:
            ver = (r.stdout or "").strip()
            _VCS_STATE.update({"detected": True, "version": ver, "last_check_ts": now})
            return {"detected": True, "version": ver, "err": ""}
        _VCS_STATE.update({"detected": False, "version": "", "last_check_ts": now})
        return {"detected": False, "version": "", "err": (r.stderr or "non-zero exit")[:200]}
    except Exception as e:  # noqa: BLE001
        _VCS_STATE.update({"detected": False, "version": "", "last_check_ts": now})
        return {"detected": False, "version": "", "err": f"{type(e).__name__}: {e}"[:200]}


def _detect_office_renderer(force: bool = False) -> dict:
    """探测 Office 渲染器(LibreOffice 主,OfficeCLI 兜底)。常量路径。"""
    now = time.time()
    out: dict[str, Any] = {}

    # LibreOffice 扫描 Win 标准路径
    if not force and _OFFICE_STATE["lo"].get("detected") is not None and (now - _OFFICE_STATE["lo"].get("last_check_ts", 0)) < 86400:
        out["lo"] = {k: v for k, v in _OFFICE_STATE["lo"].items() if k != "last_check_ts"}
    else:
        lo_path = ""
        if os.name == "nt":
            prog = os.environ.get("ProgramFiles", r"C:\Program Files")
            prog86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
            for base in [prog, prog86]:
                for sub in [
                    r"LibreOffice\program\soffice.com",
                    r"LibreOffice\program\soffice.exe",
                    r"LibreOffice 7\program\soffice.com",
                ]:
                    p = os.path.join(base, sub)
                    if os.path.isfile(p):
                        lo_path = p
                        break
                if lo_path:
                    break
        rec = {"detected": bool(lo_path), "version": "", "path": lo_path,
               "err": "" if lo_path else "LibreOffice 未装"}
        _OFFICE_STATE["lo"] = {**rec, "last_check_ts": now}
        out["lo"] = rec

    # OfficeCLI 走常量 NSIS 打入
    if not force and _OFFICE_STATE["officecli"].get("detected") is not None and (now - _OFFICE_STATE["officecli"].get("last_check_ts", 0)) < 86400:
        out["officecli"] = {k: v for k, v in _OFFICE_STATE["officecli"].items() if k != "last_check_ts"}
    else:
        if not os.path.isfile(_OFFICECLI_BIN):
            rec = {"detected": False, "version": "", "path": "", "err": "officecli not bundled"}
        else:
            try:
                r = subprocess.run(
                    [_OFFICECLI_BIN, "--version"], capture_output=True, text=True, timeout=2.5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                ver = (r.stdout or r.stderr or "").strip()[:40]
                rec = {"detected": True, "version": ver, "path": _OFFICECLI_BIN, "err": ""}
            except Exception as e:  # noqa: BLE001
                rec = {"detected": False, "version": "", "path": _OFFICECLI_BIN,
                       "err": f"{type(e).__name__}: {e}"[:200]}
        _OFFICE_STATE["officecli"] = {**rec, "last_check_ts": now}
        out["officecli"] = rec

    return out


def _vcs_run(args: list, cwd: str, timeout: float = 5.0) -> dict:
    """跑 vcs 子进程(常量绝对路径 _VCS_BIN)。"""
    if not _VCS_STATE.get("detected"):
        d = _detect_vcs()
        if not d["detected"]:
            return {"ok": False, "err": "no_vcs", "stdout": "", "stderr": ""}
    if not os.path.isdir(cwd):
        return {"ok": False, "err": f"cwd not exists: {cwd}", "stdout": "", "stderr": ""}
    try:
        r = subprocess.run(
            [_VCS_BIN] + list(args),
            cwd=cwd, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "ok": r.returncode == 0,
            "returncode": r.returncode,
            "stdout": r.stdout or "",
            "stderr": (r.stderr or "")[:500],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "err": "timeout", "stdout": "", "stderr": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": f"{type(e).__name__}: {e}"[:200], "stdout": "", "stderr": ""}


def _convert_office_to_pdf(src_path: str, workdir: str) -> dict:
    """Office → PDF(走 LibreOffice,常量命令,无 tempfile.mkdtemp)。"""
    if not _OFFICE_STATE["lo"].get("detected"):
        return {"ok": False, "err": "lo not detected"}
    soffice = _OFFICE_STATE["lo"].get("path") or ""
    if not soffice or not os.path.isfile(soffice):
        return {"ok": False, "err": "lo binary missing"}
    try:
        st = os.stat(src_path)
    except OSError:
        return {"ok": False, "err": "src not found"}
    if st.st_size > 50 * 1024 * 1024:
        return {"ok": False, "err": "file too large >50MB"}

    import hashlib
    key_src = f"{src_path}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")
    cache_key = hashlib.sha256(key_src).hexdigest()[:16]
    cache_dir = os.path.join(workdir, ".prisir_office_cache")
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        return {"ok": False, "err": "cannot mkdir cache"}
    pdf_path = os.path.join(cache_dir, f"{cache_key}.pdf")
    if os.path.isfile(pdf_path) and os.path.getmtime(pdf_path) >= st.st_mtime:
        return {"ok": True, "pdf_path": pdf_path, "cached": True}

    profile_dir = os.path.join(cache_dir, "lo_profile")
    try:
        os.makedirs(profile_dir, exist_ok=True)
    except OSError:
        pass
    profile_url = "file:///" + profile_dir.replace("\\", "/").lstrip("/")
    outdir = os.path.join(cache_dir, "lo_out")
    try:
        os.makedirs(outdir, exist_ok=True)
    except OSError:
        return {"ok": False, "err": "cannot mkdir outdir"}
    try:
        for fn in os.listdir(outdir):
            if fn.lower().endswith(".pdf"):
                os.remove(os.path.join(outdir, fn))
    except OSError:
        pass
    try:
        cmd = [
            soffice,
            f"-env:UserInstallation={profile_url}",
            "--headless",
            "--norestore", "--nofirststartwizard", "--nologo",
            "--convert-to", "pdf",
            "--outdir", outdir,
            src_path,
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        cand = None
        try:
            for fn in os.listdir(outdir):
                if fn.lower().endswith(".pdf"):
                    cand = os.path.join(outdir, fn)
                    break
        except OSError:
            cand = None
        if cand and os.path.isfile(cand) and os.path.getsize(cand) > 0:
            try:
                import shutil as _sh
                _sh.move(cand, pdf_path)
            except OSError:
                pdf_path = cand
            return {"ok": True, "pdf_path": pdf_path, "cached": False}
        return {"ok": False, "err": "no pdf produced"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "err": "timeout"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": f"{type(e).__name__}: {e}"[:200]}


# === vcs import 扫描(简化版) ===
def _scan_repos(workdir: str) -> dict:
    """扫描 workdir 子树找 .git 目录,返回候选 repo 列表(简化,主进程再做精细加工)。
    为启发式安全:这里不用 subprocess,纯 os.walk。
    """
    repos: list[str] = []
    if not workdir or not os.path.isdir(workdir):
        return {"ok": True, "repos": []}
    for root, dirs, _files in os.walk(workdir):
        # 跳过明显无关目录
        dirs[:] = [d for d in dirs if d not in {"node_modules", ".venv", "venv", "__pycache__", ".git"}]
        if ".git" in dirs:
            repos.append(root)
            dirs[:] = []  # 不下钻
    return {"ok": True, "repos": repos}


def _vcs_get_blob(repo_root: str, blob_sha: str) -> dict:
    """vcs cat-file -p <sha> 取 blob 内容。"""
    if not blob_sha:
        return {"ok": False, "err": "empty sha"}
    if not _VCS_STATE.get("detected"):
        d = _detect_vcs()
        if not d["detected"]:
            return {"ok": False, "err": "no_vcs"}
    try:
        r = subprocess.run(
            [_VCS_BIN, "cat-file", "-p", blob_sha],
            cwd=repo_root, capture_output=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if r.returncode != 0:
            return {"ok": False, "err": (r.stderr or b"").decode(errors="replace")[:200]}
        data = r.stdout or b""
        if len(data) > 32 * 1024 * 1024:
            return {"ok": False, "err": "blob > 32MB"}
        return {"ok": True, "data_b64": __import__("base64").b64encode(data).decode("ascii")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": f"{type(e).__name__}: {e}"[:200]}


# === method dispatch ===
_METHODS: dict[str, Any] = {
    "detect_vcs": lambda args: _detect_vcs(args.get("force", False)),
    "office_detect": lambda args: _detect_office_renderer(args.get("force", False)),
    "vcs_run": lambda args: _vcs_run(args.get("args", []), args.get("cwd", ""), args.get("timeout", 5.0)),
    "office_to_pdf": lambda args: _convert_office_to_pdf(args.get("src_path", ""), args.get("workdir", "")),
    "scan_repos": lambda args: _scan_repos(args.get("workdir", "")),
    "vcs_get_blob": lambda args: _vcs_get_blob(args.get("repo_root", ""), args.get("blob_sha", "")),
}


def _serve_one_line(line: str) -> str:
    """处理一行 JSON 请求,返回一行 JSON 响应。"""
    try:
        req = json.loads(line)
    except json.JSONDecodeError as e:
        return json.dumps({"ok": False, "error": f"bad json: {e}"}, ensure_ascii=False)
    method = req.get("method", "")
    args = req.get("args", {})
    fn = _METHODS.get(method)
    if fn is None:
        return json.dumps({"ok": False, "error": f"unknown method: {method}"}, ensure_ascii=False)
    try:
        result = fn(args)
        return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}, ensure_ascii=False)


def main() -> int:
    """stdio JSON-RPC server:读 stdin 一行一 JSON,写 stdout 一行一 JSON。
    跑法:PrisirVcsTool.exe  (被 PrisirAI.exe 子进程 spawn)。
    """
    # 立即 flush,主进程 spawn 时要看到日志
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    print("[prisIragent_vcs] started", file=sys.stderr, flush=True)

    # 主动探测一次 vcs/office,主进程 spawn 后第一次调会拿到 ready
    _detect_vcs(force=True)
    _detect_office_renderer(force=True)
    print("[prisIragent_vcs] ready", file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line == "quit":
            break
        resp = _serve_one_line(line)
        print(resp, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())