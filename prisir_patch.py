"""通用增量补丁核心(PrisirAI / 灵犀拼音等复用)。

设计理念(2026-09-08,任务 #102):
  全量安装包(PrisirAI 467MB / 灵犀 NSIS)对小改动太重。增量补丁只发「改动的文件」,
  应用器在本机备份→校验→落盘→登记,可随时回滚。一套核心多产品复用:目标产品只是
  「补丁根目录 + 文件映射规则」不同,应用/校验/回滚逻辑完全共用。

补丁包格式(zip):
  patch.json      —— 清单(见 build_manifest)
  files/...       —— 要覆盖的文件,按 patch.json 里每条的 "relpath" 落位
  (补丁包由打包侧 prisir_patch_build.py 生成;本模块只负责应用/校验/回滚)

安全红线:
  - 只写补丁根目录(patches/)与显式声明的壳静态目录,绝不写任意路径(防 zip 跳出)。
  - 每个文件落盘前算 sha256 与清单比对,不符即拒;应用失败整体回滚。
  - 回滚=把应用时备份的原文件复原 + 删登记;无原文件(新增)则直接删。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

SCHEMA = 1  # patch.json 结构版本,日后改结构递增并做兼容


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------------------
# 补丁根目录:可写用户数据区。frozen(PyInstaller)与源码运行共用同一套。
# ---------------------------------------------------------------------------
def default_patch_root(product: str = "prisir") -> Path:
    """补丁根目录。product 区分产品('prisir' / 'lingxi'),共用同一数据根下的子目录。

    读 PRISIR_DATA 环境变量(与 keys.db/chat.db 同一数据根),缺省 ~/.local/share/<product>。
    Windows 下 Path.home()=用户目录,~/.local/share 是项目既有约定(见 web 层 _DB_DIR)。
    """
    base = os.environ.get("PRISIR_DATA") or str(Path.home() / ".local" / "share" / product)
    return Path(base) / "patches"


def state_path(patch_root: Path) -> Path:
    return patch_root / "_applied.json"


def load_state(patch_root: Path) -> dict:
    """已应用补丁登记: {patch_id: {"applied_ts":..., "entries":[{relpath, sha256, backup}], "target":...}}"""
    sp = state_path(patch_root)
    if not sp.exists():
        return {}
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(patch_root: Path, state: dict) -> None:
    patch_root.mkdir(parents=True, exist_ok=True)
    state_path(patch_root).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 应用
# ---------------------------------------------------------------------------
def _safe_relpath(relpath: str) -> Path:
    """把清单里的 relpath 规整成安全的相对路径;含 .. 或绝对盘符即拒(防 zip 跳出)。"""
    rel = Path(relpath)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"非法补丁路径(跳出补丁根): {relpath!r}")
    return rel


def apply_patch(zip_path: str | Path, patch_root: Path,
                shell_dir: Path | None = None) -> dict:
    """应用一个补丁包。返回 {"ok":bool, "patch_id":..., "applied":[relpath...], "error":...}

    patch_root: Py 层补丁落点(运行时 sys.path 最前,覆盖 frozen PYZ 同名模块)。
    shell_dir : 壳静态文件根(assets/prisiragent-shell 的安装目录);补丁里 relpath 以
                "shell/" 前缀的条目落到 shell_dir 下,其余落 patch_root。None=不装壳文件。
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        return {"ok": False, "error": f"补丁包不存在: {zip_path}"}
    try:
        zf = zipfile.ZipFile(zip_path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"补丁包不是有效 zip: {e}"}

    with zf:
        # 1) 读清单
        try:
            manifest = json.loads(zf.read("patch.json").decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"缺/坏 patch.json: {e}"}
        if manifest.get("schema") != SCHEMA:
            return {"ok": False, "error": f"补丁结构版本不符(schema={manifest.get('schema')},需 {SCHEMA})"}
        patch_id = manifest.get("patch_id") or zip_path.stem
        entries = manifest.get("files") or []
        if not entries:
            return {"ok": False, "error": "补丁清单为空"}

        state = load_state(patch_root)
        if patch_id in state:
            return {"ok": False, "error": f"补丁 {patch_id} 已应用过(先回滚再重打)",
                    "patch_id": patch_id}

        # 2) 逐条校验 sha256(读 zip 内字节)再落盘;任一失败整体回滚已落的
        applied: list[str] = []
        backups: list[dict] = []
        try:
            for ent in entries:
                relpath = ent["relpath"]
                want_sha = ent.get("sha256", "")
                try:
                    data = zf.read("files/" + relpath)
                except KeyError:
                    raise ValueError(f"补丁包缺文件 files/{relpath}")
                if want_sha and _sha256_bytes(data) != want_sha:
                    raise ValueError(f"{relpath} sha256 不符(包损坏或被改)")

                # 落点分流:shell/ 前缀 → 壳目录;其余 → patch_root
                if relpath.startswith("shell/"):
                    if shell_dir is None:
                        raise ValueError(f"{relpath} 是壳文件但本补丁器未配壳目录")
                    dest = shell_dir / _safe_relpath(relpath[len("shell/"):])
                else:
                    dest = patch_root / _safe_relpath(relpath)
                dest.parent.mkdir(parents=True, exist_ok=True)

                # 备份原文件(用于回滚);新增文件无原文件,记 backup=None
                if dest.exists():
                    bkp = dest.with_name(dest.name + f".bak_{int(time.time())}")
                    shutil.copy2(dest, bkp)
                    backups.append({"dest": str(dest), "backup": str(bkp)})
                else:
                    backups.append({"dest": str(dest), "backup": None})

                dest.write_bytes(data)
                applied.append(relpath)
        except Exception as e:  # noqa: BLE001
            # 回滚本次已落的:有备份复原,无备份(新增)删除
            for b in backups:
                d = Path(b["dest"])
                try:
                    if b["backup"]:
                        shutil.copy2(b["backup"], d)
                        os.remove(b["backup"])
                    elif d.exists():
                        d.unlink()
                except Exception:  # noqa: BLE001
                    pass
            return {"ok": False, "error": f"应用失败已回滚: {e}", "patch_id": patch_id}

        # 3) 登记
        state[patch_id] = {
            "applied_ts": int(time.time()),
            "product": manifest.get("product", ""),
            "base_version": manifest.get("base_version", ""),
            "entries": [{"relpath": e["relpath"], "sha256": e.get("sha256", "")} for e in entries],
            "backups": backups,
        }
        save_state(patch_root, state)
        return {"ok": True, "patch_id": patch_id, "applied": applied,
                "note": "重启应用后生效(Py 层补丁在下次启动时优先加载)"}


def rollback_patch(patch_id: str, patch_root: Path) -> dict:
    """回滚一个已应用补丁:复原备份/删除新增,清登记。"""
    state = load_state(patch_root)
    rec = state.get(patch_id)
    if not rec:
        return {"ok": False, "error": f"未应用过补丁 {patch_id}"}
    restored, removed = [], []
    for b in rec.get("backups", []):
        d = Path(b["dest"])
        try:
            if b["backup"] and Path(b["backup"]).exists():
                shutil.copy2(b["backup"], d)
                os.remove(b["backup"])
                restored.append(str(d))
            elif d.exists():
                d.unlink()
                removed.append(str(d))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"回滚到 {d} 失败: {e}"}
    del state[patch_id]
    save_state(patch_root, state)
    return {"ok": True, "patch_id": patch_id, "restored": restored, "removed": removed}


def list_patches(patch_root: Path) -> dict:
    """列出已应用补丁(供 GUI 展示/回滚选择)。"""
    state = load_state(patch_root)
    return {"ok": True, "patch_root": str(patch_root),
            "patches": [{"patch_id": k, "applied_ts": v.get("applied_ts"),
                         "base_version": v.get("base_version"),
                         "files": [e["relpath"] for e in v.get("entries", [])]}
                        for k, v in state.items()]}


# ---------------------------------------------------------------------------
# 运行时覆盖:在 import 任何项目模块前调用,把补丁目录插到 sys.path 最前,
# 使 patches/<mod>.py 优先于 frozen PYZ / 源码目录里的同名模块被 import。
# 这是「改一个 .py 只发几 KB 补丁、不重打 348MB exe」的关键。
# ---------------------------------------------------------------------------
def activate_patches(product: str = "prisir") -> str:
    """把补丁根目录插到 sys.path[0]。返回补丁根(供诊断/日志)。无补丁也安全(空目录)。"""
    root = default_patch_root(product)
    try:
        root.mkdir(parents=True, exist_ok=True)
        sp = str(root)
        if sp not in sys.path:
            sys.path.insert(0, sp)
    except Exception:  # noqa: BLE001
        pass
    return str(root)
