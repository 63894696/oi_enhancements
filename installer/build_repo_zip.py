# -*- coding: utf-8 -*-
"""构建开发者模式 repo.zip —— 仓库源码快照,只收源码/文档,筛掉构建产物+纯内部材料+数据。

用途:installer/prisirai.nsi 的「开发者模式」可选组件,给开发者改代码用。
定位:给开发者的**源码包**,不是内部档案也不是构建产物包。

三重过滤:
1. **扩展名白名单**:只收代码/文档/配置/小图标(.py/.rs/.js/.md/.toml/.json/.html/.css/.ico/.png 等)。
   一切大二进制/构建产物(.dll/.exe/.bin/.zip/.node/.asar...)天然被挡在门外。
2. **目录黑名单**:.git/node_modules/target/dist/build/__pycache__/.venv/logs 等中间产物与依赖。
3. **纯内部材料**:docs/ 里暴露内部流程/基础设施/外宣策略的文档 + 根目录 `_` 开头草稿 +
   本机运行数据/密钥(双保险,白名单本就挡了 .db,这里再兜底)。

用法: python installer/build_repo_zip.py   →  installer/_dev_assets/repo.zip
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "installer" / "_dev_assets" / "repo.zip"

# ── 1) 扩展名白名单:只收「源码/文档/配置/小资源」──
# 这是主过滤器:大二进制与构建产物因扩展名不在列而被排除,无需枚举。
ALLOW_EXT = {
    # 代码
    ".py", ".rs", ".js", ".ts", ".jsx", ".tsx", ".mojo", ".go", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".mjs", ".sh", ".ps1", ".bat", ".cmd",
    # 文档/文本
    ".md", ".txt", ".rst",
    # 配置/数据描述
    ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg", ".lock", ".nsh", ".nsi",
    ".gitignore", ".gitattributes", ".editorconfig",
    # 网页/样式/小资源
    ".html", ".htm", ".css", ".ico", ".png", ".svg",
}

# ── 2) 目录黑名单(任何层级命中即整目录跳过)──
EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build",
    "logs", ".cache", "out", "target", ".idea", ".vscode", ".mypy_cache",
    ".pytest_cache", "screenshots", "_staging", "_dev_assets",
    # 浏览器 profile / 探测数据(缓存扩展/安全列表/截图,非源码)
    "Default", "Extensions", "ActorSafetyLists", "adblock",
}

# 顶层 `_` 开头的目录一律视为内部探测/缓存/临时数据(_comet_probe/_secbrowser_profile 等)
EXCLUDE_TOP_UNDERSCORE_DIR = True

# 单文件大小上限:源码/文档都该 < 1MB;词库/缓存 json/大 html 等超出即排除
MAX_FILE_BYTES = 1_000_000

# ── 3a) docs/ 纯内部文档(暴露内部流程/基建/外宣,对改代码无用)──
EXCLUDE_DOCS = {
    # 内部团队分工/issues/代行 —— 内部协作流程
    "prisiragent-team-division-2026-08-13.md",
    "prisiragent-team-issues-2026-08-13.md",
    "prisiragent-action-delegate-plan-2026-08-12.md",
    # 外宣语料策略 —— 内部运营
    "prisir-outreach-corpus-plan-2026-08-13.md",
    # 含云实例 id 的编译工单/交接 —— 暴露内部基础设施
    "prisir-m3-compile-workorder-2026-08-22.md",
    "handoff-m3-compile-2026-08-22.md",
    # 威胁模型:含真实服务器地址 + 对外口径策略 —— 内部安全/运营
    "securedm-threat-model-2026-08-14.md",
}

# ── 3b) 根目录 `_` / `.tmp_` 开头的内部草稿/一次性调试脚本 ──
EXCLUDE_ROOT_UNDERSCORE = True
# `.tmp_*` 是探查/E2E/调试用一次性脚手架(截图/png/草稿 py),不属于源码包
EXCLUDE_ROOT_TMP = True

# ── 3c) 文件名黑名单(数据/密钥,白名单外的双保险)──
EXCLUDE_FILE_NAMES = {
    "memory.db", "chats.db", "keys.db", "user_profile.json",
    "learned-solutions.md", "fcontent.db", ".env", "id_rsa", "id_ed25519",
}


def _rel(dirpath: str, name: str) -> str:
    return (Path(dirpath) / name).resolve().relative_to(ROOT).as_posix()


def _skip_dir(relpath: str) -> bool:
    parts = relpath.split("/")
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    # 顶层 `_` 开头目录(_comet_probe/_secbrowser_profile/_vc2_profile 等探测/缓存数据)
    if EXCLUDE_TOP_UNDERSCORE_DIR and len(parts) >= 1 and parts[0].startswith("_"):
        return True
    # 顶层 `.tmp_` 开头目录(一次性调试脚手架的截图/输出)
    if EXCLUDE_ROOT_TMP and len(parts) >= 1 and parts[0].startswith(".tmp"):
        return True
    return False


def _keep_file(relpath: str) -> bool:
    parts = relpath.split("/")
    name = parts[-1]
    # 目录黑名单
    if _skip_dir(relpath):
        return False
    # 数据/密钥兜底
    if name in EXCLUDE_FILE_NAMES:
        return False
    # docs 纯内部文档
    if len(parts) >= 2 and parts[0] == "docs" and name in EXCLUDE_DOCS:
        return False
    # 根目录 `_` / `.tmp_` 草稿
    if len(parts) == 1 and EXCLUDE_ROOT_UNDERSCORE and name.startswith("_"):
        return False
    if len(parts) == 1 and EXCLUDE_ROOT_TMP and name.startswith(".tmp"):
        return False
    # 扩展名白名单(无扩展名但命中 .gitignore 这类也算)
    ext = os.path.splitext(name)[1].lower()
    if ext in ALLOW_EXT or name in ALLOW_EXT:
        return True
    return False


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    n_in, n_skip = 0, 0
    skipped_docs, skipped_unders = [], []
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            # 自顶向下剪枝:命中黑名单目录直接不下钻(避免爬 864M 的 target)
            dirnames[:] = [d for d in dirnames if not _skip_dir(_rel(dirpath, d))]
            for fn in filenames:
                rel = _rel(dirpath, fn)
                full = Path(dirpath) / fn
                oversized = False
                try:
                    oversized = full.stat().st_size > MAX_FILE_BYTES
                except OSError:
                    pass
                if not oversized and _keep_file(rel):
                    z.write(full, rel)
                    n_in += 1
                else:
                    n_skip += 1
                    if rel.startswith("docs/") and fn in EXCLUDE_DOCS:
                        skipped_docs.append(rel)
                    elif len(rel.split("/")) == 1 and fn.startswith("_"):
                        skipped_unders.append(rel)
    print(f"[repo.zip] 打入 {n_in} 文件,排除 {n_skip}")
    print(f"[repo.zip] 筛掉的纯内部 docs ({len(skipped_docs)}):")
    for d in sorted(skipped_docs):
        print(f"   - {d}")
    print(f"[repo.zip] 筛掉的根目录 `_` 草稿 ({len(skipped_unders)}): {len(skipped_unders)} 个")
    print(f"[repo.zip] 输出: {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
