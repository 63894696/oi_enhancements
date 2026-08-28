# -*- coding: utf-8 -*-
"""构建 Linux 端用户安装包 (tarball)。

用途:产物 `installer/_dist/PrisirAI-Linux-2.6.0.tar.gz` 直接给用户 wget/curl 下载,
     用户解压后 `bash linux-install.sh` 即可完成端到端部署。

包含(必需):
  installer/linux-install.sh
  installer/linux-uninstall.sh
  installer/LICENSE.txt
  oiagent-shell/        (Electron 壳源码 + package.json + main.js;
                         npm install 现场重装 node_modules)
  oiagent_web.py        (Python 后端入口)
  prisir_findex/        (Rust findex 引擎;现场 cargo build)
  prisir_fcontent/      (Python 内容索引;含 models/ OCR 模型可选,
                         不在 tarball,装包脚本按需下)
  assets/               (Brand 图标 + 主题 + 山水背景)

排除(在目标机上现场生成 / 太大):
  oiagent-shell/node_modules/      (276 MB, npm install 现场重装)
  prisir_findex/target/            (131 MB, cargo build 现场编译)
  prisir_findex/.findex-state/     (运行时 DB 目录)
  prisir_fcontent/fcontent.db*     (运行时 DB)
  prisir_fcontent/models/          (OCR 模型权重,运行时按需下载)
  prisir_findex/Cargo.lock         (重新生成)
  oiagent-shell/package-lock.json  (重新生成)
  *.pyc / __pycache__/             (Python 缓存)
  .git/                            (源码控制)

用法:
  python installer/build_linux_tarball.py
  →  installer/_dist/PrisirAI-Linux-2.6.0.tar.gz
"""
from __future__ import annotations

import os
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "installer" / "_dist"
APP_VERSION = "2.6.0"
# 2026-08-28 加架构后缀(避免未来引入 aarch64 时与本包混淆)
OUT_NAME = f"PrisirAI-Linux-x86_64-{APP_VERSION}.tar.gz"
OUT_PATH = OUT_DIR / OUT_NAME

# 强制包含(顶层文件)
TOP_LEVEL_REQUIRED = [
    "oiagent_web.py",
]

# 强制包含(子目录整棵,内部还要二次过滤)
DIRS_REQUIRED = [
    "oiagent-shell",
    "prisir_findex",
    "prisir_fcontent",
    "assets",
]

# 强制包含(脚本与协议)
INSTALLER_FILES = [
    "installer/linux-install.sh",
    "installer/linux-uninstall.sh",
    "installer/LICENSE.txt",
]

# 排除(目录/文件单层名)
EXCLUDE_NAMES = frozenset({
    "node_modules",      # Electron 依赖(现场 npm install)
    "target",            # Rust build artifacts(现场 cargo build)
    "__pycache__",       # Python 缓存
    ".findex-state",     # findex 运行时状态
    "models",            # OCR 模型权重(运行时按需下载,不入 tarball)
    ".git",
    "_staging",
    "_dev_assets",
    "dist",              # Electron 打包产物
    "screenshots",       # fcontent 截图样本
})

# 排除(精确文件名 — 含 SQLite WAL/SHM 后缀)
EXCLUDE_FILENAMES = frozenset({
    "Cargo.lock",        # 现场 cargo 生成
    "package-lock.json", # 现场 npm 生成
})


def should_exclude(rel_path: str) -> bool:
    """是否排除该相对路径。

    rel_path 是相对于 src 子目录根的相对路径,如:
      findex.db
      target/release/foo
      models/PP-OCRv4/ch.onnx
    """
    rel = rel_path.replace("\\", "/")
    parts = rel.split("/")
    last = parts[-1]

    # 1) 单层目录/文件名命中(精确)
    if any(part in EXCLUDE_NAMES for part in parts):
        return True
    if last in EXCLUDE_FILENAMES:
        return True

    # 2) SQLite 运行时:findex.db / findex.db-wal / findex.db-shm
    if last.startswith("findex.db") or last.startswith("fcontent.db"):
        return True

    # 3) Python 缓存
    if last.endswith(".pyc"):
        return True

    return False


def add_file(tar: tarfile.TarFile, src: Path, arcname: str) -> None:
    """添加文件到 tar,保留可执行位。"""
    ti = tarfile.TarInfo(name=arcname)
    ti.size = src.stat().st_size
    ti.mtime = int(src.stat().st_mtime)
    ti.mode = 0o644
    if os.access(src, os.X_OK):
        ti.mode = 0o755
    with src.open("rb") as f:
        tar.addfile(ti, f)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 临时 staging 目录(整理后再 tar)
    staging = OUT_DIR / "_staging_linux"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()

    # 1. installer/ 文件
    installer_stage = staging / "installer"
    installer_stage.mkdir()
    for rel in INSTALLER_FILES:
        src = ROOT / rel
        if not src.exists():
            raise SystemExit(f"missing required: {rel}")
        dst = installer_stage / Path(rel).name
        shutil.copy2(src, dst)
        print(f"  + {rel}")

    # 2. 顶层 Python 入口
    for rel in TOP_LEVEL_REQUIRED:
        src = ROOT / rel
        if not src.exists():
            raise SystemExit(f"missing required: {rel}")
        shutil.copy2(src, staging / rel)
        print(f"  + {rel}")

    # 3. 子目录整棵
    for d in DIRS_REQUIRED:
        src = ROOT / d
        if not src.exists():
            print(f"  WARN: skip missing {d}")
            continue
        dst = staging / d
        # 用 shutil.copytree 配合 ignore,过滤排除项
        def _ignore(_dir, names):
            ignored = []
            for n in names:
                full = os.path.join(_dir, n).replace("\\", "/")
                rel_full = os.path.relpath(full, src).replace("\\", "/")
                if should_exclude(rel_full):
                    ignored.append(n)
            return ignored

        shutil.copytree(src, dst, ignore=_ignore)
        kept_size = sum(p.stat().st_size for p in dst.rglob("*") if p.is_file())
        print(f"  + {d}/ (filtered to {kept_size/1024/1024:.1f} MB)")

    # 4. 打 tar.gz
    print(f"\nBuilding {OUT_PATH} ...")
    if OUT_PATH.exists():
        OUT_PATH.unlink()
    with tarfile.open(OUT_PATH, "w:gz") as tar:
        # 用 PrisirAI-Linux-x86_64-VERSION 作根目录名(用户解压后是一个文件夹)
        arcroot = OUT_PATH.name.replace(".tar.gz", "")
        for p in sorted(staging.rglob("*")):
            if p.is_file():
                rel = p.relative_to(staging)
                # arcname 形如 PrisirAI-Linux-2.6.0/installer/linux-install.sh
                arcname = f"{arcroot}/{rel.as_posix()}"
                add_file(tar, p, arcname)

    size_mb = OUT_PATH.stat().st_size / 1024 / 1024
    print(f"\n✓ {OUT_PATH}  ({size_mb:.1f} MB)")

    # 5. 清理 staging
    shutil.rmtree(staging)
    print(f"  staging cleaned")


if __name__ == "__main__":
    main()