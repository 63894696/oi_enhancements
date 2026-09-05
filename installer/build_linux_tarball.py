# -*- coding: utf-8 -*-
"""构建 Linux 端用户安装包 (tarball)。

用途:产物 `installer/_dist/PrisirAI-Linux-2.6.0.tar.gz` 直接给用户 wget/curl 下载,
     用户解压后 `bash linux-install.sh` 即可完成端到端部署。

包含(必需):
  installer/linux-install.sh
  installer/linux-uninstall.sh
  installer/LICENSE.txt
  prisiragent-shell/                    (Electron 壳源码 + 预装 node_modules)
  prisiragent_web.py                    (Python 后端入口)
  prisir_findex/                    (Rust findex 源码 + 预编译 .so)
  prisir_fcontent/                  (Python 内容索引 + 预装 OCR models)
  assets/                           (Brand 图标 + 主题 + 山水背景)

排除(运行时生成 / 源码控制):
  prisir_findex/target/release/build/    (增量构建临时目录)
  prisir_findex/target/release/deps/     (依赖 .rmeta 等中间产物)
  prisir_findex/target/release/.fingerprint/
  prisir_findex/target/release/*.d       (Windows-only .d 文件)
  prisir_findex/target/release/prisir_findex.{dll,pdb,exp,lib}  (Win 产物,Linux 用 .so)
  prisir_findex/.findex-state/     (运行时 DB 目录)
  prisir_fcontent/fcontent.db*     (运行时 DB)
  prisir_findex/findex.db*         (运行时 DB)
  prisir_findex/Cargo.lock         (重新生成)
  prisiragent-shell/package-lock.json  (重新生成)
  *.pyc / __pycache__/             (Python 缓存)
  .git/                            (源码控制)

2026-08-28 装好即用改造:
  - 预编译 libprisir_findex.so 已通过 WSL Ubuntu-24.04 在本机构建并打包进 tarball。
  - OCR 模型(ch_PP-OCRv4_det/rec_server_infer.onnx,195 MB)直接从仓库
    prisir_fcontent/models/ 拷入,装包脚本不再现场下。
  - prisiragent-shell/node_modules/(276 MB,electron + 其 deps)直接拷入,不再现场 npm install。
  装包脚本同步去除 cargo build / npm install / 模型下载步骤,「解压即跑」。

2026-08-29 bebian 端到端教训:
  - 之前的打包策略只把 prisiragent_web.py 单文件从仓根拷入;prisiragent_cli.py /
    prisiragent_context.py / lan_pair.py / perm_gate.py / prisiragent_coworker/ 子包 /
    fastlane/ 子包全部漏包。装完 web 端 import 全炸。
  - Win 端 NSIS 不在意 Python import 链(PyInstaller 把 .pyc 打进了 PrisirAI.exe);
    Linux 端直接跑 .py 文件,必须把仓根 Python 部分整块打包。
  - 修法:仓根所有合法 .py(~70 个)打包进 tarball 根;子包 fastlane/、
    prisiragent_coworker/ 走 DIRS_REQUIRED。开发期探测用的 _xxx.py 草稿一律排除
    (EXCLUDE_TOP_UNDERSCORE_DIR,借鉴 build_repo_zip.py 已有规则)。

用法:
  python installer/build_linux_tarball.py
  →  installer/_dist/PrisirAI-Linux-2.6.0.tar.gz
"""
from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "installer" / "_dist"
APP_VERSION = "2.6.0"
# 2026-08-28 加架构后缀(避免未来引入 aarch64 时与本包混淆)
OUT_NAME = f"PrisirAI-Linux-x86_64-{APP_VERSION}.tar.gz"
OUT_PATH = OUT_DIR / OUT_NAME

# 强制包含(子目录整棵,内部还要二次过滤)
# 2026-08-29 bebian 教训:之前用 TOP_LEVEL_REQUIRED 白名单只挑了 prisiragent_web.py,
# 漏了 prisiragent_cli.py / prisiragent_context.py / lan_pair.py / perm_gate.py 等仓根 .py
# 以及 fastlane/、prisiragent_coworker/ 子包。
# 修法:DIRS_REQUIRED 把两个 Python 子包也列上;仓根 .py 改用"扫所有合法 .py"
# (collect_top_level_py),不再白名单枚举,后续 prisiragent_web 加新 import 也不会漏。
DIRS_REQUIRED = [
    "prisiragent-shell",
    "prisir_findex",
    "prisir_fcontent",
    "assets",
    # 2026-08-29 新增:Python 子包
    "fastlane",
    "prisiragent_coworker",
]

# 强制包含(脚本与协议)
INSTALLER_FILES = [
    "installer/linux-install.sh",
    "installer/linux-uninstall.sh",
    "installer/LICENSE.txt",
]

# 排除(顶级目录名) — 2026-08-28 装好即用改造:
#   node_modules / target / models 现都已在仓库内构建完毕,直接拷入 tarball;
#   不再现场 npm install / cargo build / 下模型。
# 注意:这里只列「顶级」目录名,深层同名目录(如 node_modules/*/dist/)不被砍。
EXCLUDE_NAMES = frozenset({
    "__pycache__",       # Python 缓存
    ".findex-state",     # findex 运行时状态
    ".git",
    "_staging",
    "_dev_assets",
    "dist",              # 顶级 dist/(Electron 打包 .exe 产物,Linux 不要)
    "screenshots",       # fcontent 截图样本
})

# 排除(精确文件名 — Win 端 cargo 产物,在 Linux tarball 不需要)
EXCLUDE_FILENAMES = frozenset({
    "Cargo.lock",        # 现场 cargo 生成
    "package-lock.json", # 现场 npm 生成
    "prisir_findex.dll",       # Win .dll
    "prisir_findex.dll.exp",   # MSVC link export
    "prisir_findex.dll.lib",   # MSVC import lib
    "prisir_findex.pdb",       # MSVC debug symbols
    "prisir_findex.d",         # MinGW/MSVC dep stub
    "libprisir_findex.d",      # MinGW/MSVC dep stub
    "libprisir_findex.rlib",   # Rust 静态 lib(cargo 用的中间产物,运行时不要)
    ".cargo-artifact-lock",
    ".cargo-build-lock",
    ".cargo-lock",
    ".rustc_info.json",
    "CACHEDIR.TAG",
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

    # 1) 单层目录/文件名命中(精确)。
    # 关键:「dist」「models」这种通用名,在 node_modules/*/dist/ 下是 npm 包源码
    # 的预编译产物,electron 装上后跑需要它们;不能粗暴全砍。
    # 这里用「顶级目录 in EXCLUDE_NAMES」白名单:只砍位于第一层的 dist/、
    # models/,深层的 dist/ 视为合法包内容。
    if parts and parts[0] in EXCLUDE_NAMES:
        return True

    # 2) 第二层及更深的目录中只砍「构建期中间产物」名(deps/build/.fingerprint
    #    这些和 target/release 子树绑定;原属第一层的 __pycache__ 已在 #1 砍)
    DEEP_EXCLUDE = {"deps", "build", ".fingerprint"}
    if any(part in DEEP_EXCLUDE for part in parts):
        return True

    if last in EXCLUDE_FILENAMES:
        return True

    # 3) SQLite 运行时:findex.db / findex.db-wal / findex.db-shm
    if last.startswith("findex.db") or last.startswith("fcontent.db"):
        return True

    # 4) Python 缓存
    if last.endswith(".pyc"):
        return True

    return False


def collect_top_level_py(root: Path) -> list[str]:
    """扫仓库根(.py 顶层文件)所有合法 Python 文件。

    2026-08-29 bebian 教训:之前用白名单列 TOP_LEVEL_REQUIRED 总有遗漏。
    现在改成"扫仓根所有 .py,排除 _xxx.py 草稿",与 Win 端 NSIS 装
    PrisirAI.exe 内嵌的字节码覆盖范围一致。

    排除规则(借鉴 build_repo_zip.py 的 EXCLUDE_TOP_UNDERSCORE_DIR,
    再加上 .tmp_ 前缀的开发期探针):
    - 以 `_` 开头的 .py(_ak_probe.py / _test_xxx.py 等开发期探针/草稿)
    - 以 `.tmp_` 开头的 .py(本机开发时的临时探针脚本,被有意保留在工作区)
    - __pycache__/ 下的 .py

    返回:相对仓库根的文件名(如 "prisiragent_web.py"),sorted。
    """
    out: list[str] = []
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        if not p.suffix == ".py":
            continue
        if p.name.startswith("_"):
            # _xxx.py 是开发期探针/草稿,不打包
            continue
        if p.name.startswith(".tmp_"):
            # .tmp_xxx.py 同上,开发期临时探针
            continue
        out.append(p.name)
    return out


def add_file(tar: tarfile.TarFile, src: Path, arcname: str) -> None:
    """添加文件到 tar,保留可执行位 + setuid/setgid/sticky 位。

    关键坑(2026-08-28):在 Windows 上构建 tarball 时,os.stat() 对从 Linux 拷过来的
    ELF 二进制返回 mode=0o666(Windows NTFS 不懂 POSIX x 位)。如果按 stat 直接
    写,所有 Linux 二进制都会被打成 0644,在 Linux 上 chmod 不到 +x(电子二进制
    跑不起来)。修法:用 arcname 后缀路径识别已知二进制,赋强制 mode。
    """
    ti = tarfile.TarInfo(name=arcname)
    ti.size = src.stat().st_size
    ti.mtime = int(src.stat().st_mtime)

    # 默认:普通文件
    ti.mode = 0o644

    # .so / Linux ELF 二进制:0755
    arcname_l = arcname.replace("\\", "/")
    last = arcname_l.split("/")[-1]
    is_linux_elf = last.endswith(".so") or last.endswith(".so.1")

    # electron 强制 chmod 列表(arcname 末段匹配)
    FORCE_EXEC = {"electron", "chrome_crashpad_handler"}
    FORCE_SETUID = {"chrome-sandbox"}

    if last in FORCE_SETUID:
        ti.mode = 0o4755
    elif last in FORCE_EXEC or is_linux_elf:
        ti.mode = 0o755
    elif os.access(src, os.X_OK):
        # Win 端原生 .exe / .bat / .cmd 等
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

    # 2. 顶层 Python 入口(扫所有合法 .py,不再白名单)
    # 2026-08-29 bebian 教训:之前白名单只挑 prisiragent_web.py,漏了 cli/context/
    # lan_pair/perm_gate 等,导致装包后 web 端 ModuleNotFoundError。
    # 现在扫仓根所有 .py,排除 _xxx.py 草稿(开发期探针不打包)。
    top_level_py = collect_top_level_py(ROOT)
    if not top_level_py:
        raise SystemExit("no top-level .py found in repo root!")
    for rel in top_level_py:
        src = ROOT / rel
        shutil.copy2(src, staging / rel)
    print(f"  + 顶层 .py {len(top_level_py)} 个: {', '.join(top_level_py[:5])}{'...' if len(top_level_py) > 5 else ''}")

    # 3. 子目录整棵
    # 2026-08-28 改造:为绕开 Windows MAX_PATH(260)对深层 node_modules 的截断,
    # 把含深层目录(prisiragent-shell/node_modules、prisir_findex/target/)的子树
    # 先拷到短路径 %TEMP%\pls_xxx,再从短路径加入 tar;arcname 仍按
    # PrisirAI-Linux-x86_64-2.6.0/<dir>/... 计算。
    # 不含深层的(assets、prisir_fcontent/models 二级浅目录不进深层)用普通
    # shutil.copytree 写进 staging 即可。
    short_temp_root = Path(tempfile.gettempdir()) / f"pls_{uuid.uuid4().hex[:8]}"
    short_temp_root.mkdir(parents=True, exist_ok=False)
    # dir_name -> 源完整路径,等 tar 阶段从 short_temp_root / dir_name 取
    short_dirs = {}

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

        # 策略:先 short_path 拷,再从 short_path 整个走目录打包到 staging(若短)
        # 或者直接挂到 short_dirs 在 tar 阶段独立处理。
        short_dst = short_temp_root / d
        shutil.copytree(src, short_dst, ignore=_ignore, dirs_exist_ok=True)
        kept_size = sum(p.stat().st_size for p in short_dst.rglob("*") if p.is_file())
        print(f"  + {d}/ (filtered to {kept_size/1024/1024:.1f} MB)")
        short_dirs[d] = short_dst

    # 4. 打 tar.gz
    print(f"\nBuilding {OUT_PATH} ...")
    if OUT_PATH.exists():
        OUT_PATH.unlink()
    with tarfile.open(OUT_PATH, "w:gz") as tar:
        # 用 PrisirAI-Linux-x86_64-VERSION 作根目录名(用户解压后是一个文件夹)
        arcroot = OUT_PATH.name.replace(".tar.gz", "")

        # 4a. 从 staging(installer/、prisiragent_web.py、assets/ 等浅目录)写
        for p in sorted(staging.rglob("*")):
            if p.is_file():
                rel = p.relative_to(staging)
                arcname = f"{arcroot}/{rel.as_posix()}"
                add_file(tar, p, arcname)

        # 4b. 从 short_temp_root 写(深路径的 prisiragent-shell、prisir_findex/)
        for d, short_dst in short_dirs.items():
            for p in sorted(short_dst.rglob("*")):
                if p.is_file():
                    rel = p.relative_to(short_dst)
                    arcname = f"{arcroot}/{d}/{rel.as_posix()}"
                    add_file(tar, p, arcname)

    size_mb = OUT_PATH.stat().st_size / 1024 / 1024
    print(f"\n✓ {OUT_PATH}  ({size_mb:.1f} MB)")

    # 5. 清理 staging + 临时短路径
    shutil.rmtree(staging)
    shutil.rmtree(short_temp_root)
    print(f"  staging + temp cleaned")


if __name__ == "__main__":
    main()