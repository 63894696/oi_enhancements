#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# data_prep_tempfile.py — M3.46 临时文件分类器 Step 2 训练集准备(2026-09-23)
#
# 目的:
#   - 扫全盘(默认 C: D: E:,硬排除系统目录)找"可清理临时文件"样本
#   - 自动按 5 类规则打 risk_label(safe/low/medium/high/critical)
#   - 加 _action 字段:"delete" / "review" / "keep",给落地清理工具用
#   - 输出 JSONL,扩 schema 与 Step 1 兼容(text + risk_label + jailbreak_label)
#
# 5 类分级(自动标注):
#   safe     — .tmp/.bak/.swp/.log/~$office 等,直接删
#   low      — __pycache__/、*.pyc、dist/、build/、.pytest_cache/、.mypy_cache/、.ruff_cache/、.tox/
#   medium   — node_modules/、target/、.venv/、.cargo/、.gradle/、.idea/workspace.xml
#   high     — git tracked 源文件、*.py/*.ts/*.js/*.md/*.json 顶层
#   critical — .env、*.pem、*.key、id_rsa*、.aws/、.ssh/、secrets.*、credentials*
#
# 用法:
#   python data_prep_tempfile.py --output data_tempfile.jsonl [--limit 500]
#
# 设计:
#   - 走 pathlib.Path.rglob,IO 密集,多线程并发(8 worker)
#   - 用 stat().st_size + st_mtime + st_atime 算年龄
#   - git 状态:仅对 D:\Agent-First OS\ 这种已知仓库跑 `git status --porcelain`
#   - 风险:扫 C:\Windows\ 会卡,硬排除
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterator

# ------------------------------------------------------------
# 硬排除(全盘扫必加)
# ------------------------------------------------------------
HARD_EXCLUDE_PATTERNS = [
    # Windows 系统目录
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
    "C:\\PerfLogs",
    "C:\\Recovery",
    "C:\\$Recycle.Bin",
    "C:\\System Volume Information",
    # 用户系统目录
    "C:\\Users\\*\\AppData",
    "C:\\Users\\*\\NTUSER.DAT*",
    "C:\\Users\\*\\ntuser.dat*",
    # 通用
    "C:\\hiberfil.sys",
    "C:\\pagefile.sys",
    "C:\\swapfile.sys",
    "C:\\DumpStack.log.tmp",
]

# 路径段级排除(任一段匹配则整路径跳过)
HARD_EXCLUDE_DIR_NAMES = {
    "Windows", "Program Files", "Program Files (x86)", "ProgramData",
    "$Recycle.Bin", "System Volume Information",
    "AppData", "Application Data",
    "Recovery", "PerfLogs",
}

# 额外跳过:扫描时跳过整个子树
SKIP_DIR_NAMES = {
    "$Recycle.Bin",
    "System Volume Information",
    "node_modules",   # 单文件粒度太大,medium 标
    "__pycache__",    # 同上
    ".git",           # git 内部
    ".svn",
    ".hg",
}

# ------------------------------------------------------------
# 5 类分级规则(扩展名 / 路径模式)
# ------------------------------------------------------------

# critical:密钥、凭证、隐私数据
CRITICAL_EXTS = {".pem", ".key", ".pfx", ".p12", ".keystore", ".jks"}
CRITICAL_NAMES = {
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    ".env", ".envrc", "secrets.yml", "secrets.yaml",
    "credentials.json", "credentials.yml", "credentials.yaml",
    "service-account.json",  # GCP
    ".netrc",
}
CRITICAL_DIR_PATTERNS = [
    ".ssh", ".aws", ".gcp", ".azure",
    "credentials", ".kube",
]

# safe:临时文件、可放心删
SAFE_EXTS = {".tmp", ".bak", ".swp", ".swo", ".swn", ".log"}
SAFE_NAME_PREFIXES = ("~$", ".#", "#")  # Office 锁、emacs 备份
SAFE_NAME_SUFFIXES = (".tmp", ".bak", ".old", ".orig", ".rej")

# low:构建产物 / 缓存
LOW_DIR_PATTERNS = [
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    ".eggs",
    "*.egg-info",
    "htmlcov",
    ".coverage",
    "coverage",
    ".nyc_output",
]
LOW_EXTS = {".pyc", ".pyo"}

# medium:依赖缓存 / IDE 临时
MEDIUM_DIR_PATTERNS = [
    "node_modules",
    "target",            # rust/maven
    ".venv", "venv", "env",
    ".cargo",
    ".gradle",
    ".idea",
    ".vscode",
]

# high:源文件 / 配置文件 / git tracked
HIGH_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".hpp",
    ".md", ".rst", ".txt", ".json", ".yaml", ".yml", ".toml",
    ".html", ".css", ".scss",
    ".sh", ".ps1", ".bat", ".cmd",
}


def _is_critical(path: Path) -> bool:
    if path.suffix.lower() in CRITICAL_EXTS:
        return True
    if path.name in CRITICAL_NAMES:
        return True
    pstr = str(path).replace("/", "\\").lower()
    parts_lower = path.parts
    return any(d.lower() in parts_lower for d in CRITICAL_DIR_PATTERNS)


def _is_safe(path: Path) -> bool:
    name = path.name
    if name.startswith(SAFE_NAME_PREFIXES):
        return True
    if any(name.endswith(suf) for suf in SAFE_NAME_SUFFIXES):
        return True
    if path.suffix.lower() in SAFE_EXTS:
        return True
    return False


def _in_low_dir(path: Path) -> bool:
    parts_lower = {p.lower() for p in path.parts}
    for pat in LOW_DIR_PATTERNS:
        if pat.lower() in parts_lower:
            return True
    return False


def _in_medium_dir(path: Path) -> bool:
    parts_lower = {p.lower() for p in path.parts}
    for pat in MEDIUM_DIR_PATTERNS:
        if pat.lower() in parts_lower:
            return True
    return False


def classify(path: Path) -> tuple[str, str]:
    """返 (risk_label, action)。"""
    # critical 最优先
    if _is_critical(path):
        return "critical", "keep"
    # safe 次之(顶层 .tmp/.bak/.log 等)
    if _is_safe(path):
        return "safe", "delete"
    # 目录类
    if _in_low_dir(path):
        return "low", "delete"
    if _in_medium_dir(path):
        return "medium", "review"
    # 扩展名
    if path.suffix.lower() in LOW_EXTS:
        return "low", "delete"
    if path.suffix.lower() in HIGH_EXTS:
        return "high", "keep"
    # 兜底:未知扩展 → medium
    return "medium", "review"


# ------------------------------------------------------------
# Git 状态检测(对已知仓库)
# ------------------------------------------------------------
KNOWN_REPO_ROOTS = [
    Path("C:/Users/Administrator/oi_enhancements"),
    Path("C:/Users/Administrator/oi_enhancements/companion"),
    Path("D:/Agent-First OS"),      # 工作目录(若某天 init 过 .git 也识别)
    Path("D:/prisir-train-assets"),
]


def _git_status_map(repo_root: Path) -> dict[str, str]:
    """返 {rel_path: status_code}。失败返 {}。"""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=20,
        )
        if out.returncode != 0:
            return {}
        result: dict[str, str] = {}
        for line in out.stdout.splitlines():
            if len(line) < 3:
                continue
            code = line[:2]
            rel = line[3:].strip()
            # git porcelain 对中文/空格路径会加引号
            if rel.startswith('"') and rel.endswith('"'):
                rel = rel[1:-1]
            result[rel.replace("/", "\\")] = code
        return result
    except Exception:
        return {}


# ------------------------------------------------------------
# 主扫描
# ------------------------------------------------------------
DEFAULT_SCAN_ROOTS = [Path("C:/"), Path("D:/"), Path("E:/")]
MAX_DEPTH = 12   # 防 path.rglob 失控

# 可选白名单子目录(精确路径,即使父目录在排除名单也允许扫)
# 适用于 Windows 系统目录里"已知安全的临时文件区"
WINDOWS_SAFE_SUBDIRS = [
    "C:\\Windows\\Temp",
    "C:\\Windows\\Logs",
    "C:\\Windows\\SoftwareDistribution\\Download",
    "C:\\Windows\\ServiceProfiles\\LocalService\\AppData\\Local\\Temp",
    "C:\\Windows\\System32\\LogFiles",
    "C:\\Windows\\Prefetch",
    "C:\\Windows\\debug",
    "C:\\Windows\\Installer\\$PatchCache$",
    "C:\\Windows\\.old",            # 升级留下的旧 Windows
    "C:\\Windows\\WinSxS\\Backup",  # 已废组件备份
    # 用户目录下的安全临时区(父目录 AppData 在排除名单,精确子目录放过)
    "C:\\Users\\Administrator\\AppData\\Local\\Temp",
    "C:\\Users\\Administrator\\AppData\\Local\\Microsoft\\Windows\\INetCache",
    "C:\\Users\\Administrator\\AppData\\Local\\Microsoft\\Windows\\Explorer",
    "C:\\Users\\Administrator\\AppData\\Local\\CrashDumps",
    "C:\\Users\\Administrator\\AppData\\Roaming\\Microsoft\\Windows\\Recent",
]


def _is_excluded(path: Path) -> bool:
    pstr = str(path).replace("/", "\\")
    # 白名单优先:精确子目录命中即不排除
    for safe in WINDOWS_SAFE_SUBDIRS:
        if pstr.startswith(safe):
            return False
    for pat in HARD_EXCLUDE_PATTERNS:
        # pat 用反斜杠,简单 startswith
        if "*" in pat:
            # 含通配:用 fnmatch 风格
            import fnmatch
            if fnmatch.fnmatch(pstr, pat):
                return True
        else:
            if pstr.startswith(pat):
                return True
    # 额外:路径上任一段是 HARD_EXCLUDE_DIR_NAMES 直接排除
    parts_lower = {p.lower() for p in path.parts}
    for d in HARD_EXCLUDE_DIR_NAMES:
        if d.lower() in parts_lower:
            return True
    return False


def _scan_one(root: Path) -> Iterator[dict]:
    """单根扫描。手 walk + 排除目录不下钻 + MAX_DEPTH 兜底。"""
    if not root.exists():
        return

    # 队列 BFS,避免深递归
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        path, depth = stack.pop()
        if depth > MAX_DEPTH:
            continue
        if _is_excluded(path):
            continue
        try:
            entries = list(path.iterdir())
        except (PermissionError, OSError, NotADirectoryError):
            continue
        for child in entries:
            try:
                # 早剪:目录被排除 → 不下钻
                if child.is_dir():
                    if child.name in SKIP_DIR_NAMES:
                        continue
                    if _is_excluded(child):
                        continue
                    stack.append((child, depth + 1))
                    continue
                if not child.is_file():
                    continue
                stat = child.stat()
                risk, action = classify(child)
                now = time.time()
                age_days = (now - stat.st_mtime) / 86400
                yield {
                    "_path": str(child),
                    "_ext": child.suffix.lower(),
                    "_size_bytes": stat.st_size,
                    "_age_days": round(age_days, 1),
                    "risk_label": risk,
                    "_action": action,
                    "_source": f"scan:{root}",
                }
            except (PermissionError, OSError):
                continue


def _resolve_git_status(samples: list[dict]) -> None:
    """对已知仓库样本补 _git_status。"""
    for repo_root in KNOWN_REPO_ROOTS:
        if not (repo_root / ".git").exists():
            continue
        status_map = _git_status_map(repo_root)
        if not status_map:
            continue
        repo_str = str(repo_root).replace("/", "\\")
        for s in samples:
            pstr = s["_path"]
            if not pstr.startswith(repo_str):
                continue
            rel = pstr[len(repo_str):].lstrip("\\")
            s["_git_status"] = status_map.get(rel, "tracked")


# ------------------------------------------------------------
# 多样性采样(避免一类塞满)
# ------------------------------------------------------------
# 目标分布:每类至少 N 条,不足时全留;超额时按 rdir 多样性截
RISK_MIN_TARGETS = {
    "safe": 30,
    "low": 80,
    "medium": 80,
    "high": 80,
    "critical": 20,
}


def _balance_diversity(samples: list[dict], limit: int) -> list[dict]:
    """两层采样:
    1) 按 risk_label 分大桶,每桶达到 RISK_MIN_TARGETS 后按 rdir 多样性截
    2) 不足 RISK_MIN_TARGETS 的类全保留
    3) 最终 shuffle 后截到 limit
    """
    rng = random.Random(42)
    by_risk: dict[str, list[dict]] = {}
    for s in samples:
        by_risk.setdefault(s["risk_label"], []).append(s)

    picked: list[dict] = []
    for risk, items in by_risk.items():
        target = RISK_MIN_TARGETS.get(risk, 20)
        if len(items) <= target:
            # 全部保留(类样本本身不够)
            picked.extend(items)
            continue
        # 超额:按 rdir 多样性抽,但每 rdir 不强 cap(按比例分)
        sub_buckets: dict[str, list[dict]] = {}
        for it in items:
            parts = Path(it["_path"]).parts
            rdir = parts[2].lower() if len(parts) >= 4 else (
                parts[1].lower() if len(parts) >= 3 else "_root")
            sub_buckets.setdefault(rdir, []).append(it)
        # 每 rdir 按它占总样本的比例分 target,但每桶至少 10 条保多样性
        total = sum(len(v) for v in sub_buckets.values())
        kept: list[dict] = []
        for rdir, sub_items in sub_buckets.items():
            share = max(int(target * len(sub_items) / total), 10)
            rng.shuffle(sub_items)
            kept.extend(sub_items[:share])
        # 截到 target(可能略多)
        picked.extend(kept[:target])

    rng.shuffle(picked)
    return picked[:limit]


# ------------------------------------------------------------
# 文本字段构造(给模型看的 prompt)
# ------------------------------------------------------------
def _build_text(s: dict) -> str:
    """给模型看的「路径 + 上下文」描述。"""
    p = Path(s["_path"])
    parts = p.parts
    # 简化:取盘符后第一/二级目录 + 文件名 + 扩展
    if len(parts) >= 5:
        ctx = "\\".join(parts[1:4])  # e.g. Users\Administrator\Documents
        rel = "\\".join(parts[4:])
        loc = f"{ctx}\\{rel}"
    else:
        loc = str(p)
    age = s["_age_days"]
    size_kb = s["_size_bytes"] / 1024
    size_str = (f"{size_kb:.0f}KB" if size_kb < 1024
                else f"{size_kb/1024:.1f}MB")
    git = s.get("_git_status", "")
    git_str = f" git={git}" if git else ""
    return (f"文件路径: {loc}\n"
            f"扩展名: {s['_ext'] or '(无)'}\n"
            f"大小: {size_str}\n"
            f"年龄: {age:.0f} 天{git_str}\n"
            f"问: 这个文件是否可以安全清理?")


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 2 临时文件训练集准备")
    ap.add_argument("--output", default="data_tempfile.jsonl",
                    help="输出 JSONL 路径")
    ap.add_argument("--limit", type=int, default=500,
                    help="最终样本上限")
    ap.add_argument("--max-raw", type=int, default=200000,
                    help="原始扫描上限,防磁盘爆)")
    ap.add_argument("--roots", nargs="+", default=None,
                    help="扫描根目录列表,默认 C:/ D:/ E:/")
    args = ap.parse_args()

    roots = [Path(r) for r in (args.roots or
                                [str(x) for x in DEFAULT_SCAN_ROOTS])]

    print(f"[1/4] 扫描根目录: {[str(r) for r in roots]}")
    print(f"      硬排除: {len(HARD_EXCLUDE_PATTERNS)} 条")

    raw: list[dict] = []
    t0 = time.time()
    stop_flag = [False]   # 闭包共享,达到 max-raw 时置 True

    def _scan_with_progress(root: Path) -> list[dict]:
        """单根扫描,每 N 个文件 print 一次进度。"""
        items: list[dict] = []
        last_print = time.time()
        for entry in _scan_one(root):
            if stop_flag[0]:
                break
            items.append(entry)
            if len(items) % 500 == 0 or time.time() - last_print > 3.0:
                elapsed = time.time() - t0
                print(f"    [scan] {root} → {len(items)} 条 "
                      f"(总 {len(raw)+len(items)}, {elapsed:.0f}s)",
                      flush=True)
                last_print = time.time()
        return items

    # 顺序扫(单线程,加 progress,避免 Windows NTFS race)
    for root in roots:
        if not root.exists():
            print(f"  - {root} 不存在,跳过")
            continue
        print(f"  > 开始扫 {root} ...", flush=True)
        try:
            items = _scan_with_progress(root)
            raw.extend(items)
            print(f"  + {root}: {len(items)} 条 (累计 {len(raw)})",
                  flush=True)
            if len(raw) >= args.max_raw:
                print(f"  ⚠ 达到 --max-raw={args.max_raw},停止扫描")
                stop_flag[0] = True
                break
        except Exception as e:
            print(f"  ✗ {root} 失败: {type(e).__name__}: {e}")

    print(f"\n  共扫描 {len(raw)} 条文件,耗时 {time.time()-t0:.1f}s")

    if not raw:
        print("ERROR: 没扫到任何文件,检查路径或权限")
        return 1

    # 2) git 状态(只对已知仓库,异步可加)
    print(f"[2/4] 解析 git 状态...")
    _resolve_git_status(raw)
    git_count = sum(1 for s in raw if s.get("_git_status"))
    print(f"  + {git_count} 条带 git 状态")

    # 3) 多样性平衡
    print(f"[3/4] 多样性平衡(每桶 cap,总 ≤ {args.limit})")
    balanced = _balance_diversity(raw, args.limit)
    print(f"  + {len(balanced)} 条入训练集")

    # 4) 写盘
    print(f"[4/4] 写盘: {args.output}")
    out = Path(args.output)
    by_risk: dict[str, int] = {}
    by_action: dict[str, int] = {}
    with out.open("w", encoding="utf-8") as f:
        for s in balanced:
            row = {
                "text": _build_text(s),
                "risk_label": s["risk_label"],
                "jailbreak_label": False,  # 临时文件分类不涉及 jailbreak
                "_action": s["_action"],
                "_path": s["_path"],
                "_ext": s["_ext"],
                "_age_days": s["_age_days"],
                "_git_status": s.get("_git_status", ""),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            by_risk[s["risk_label"]] = by_risk.get(s["risk_label"], 0) + 1
            by_action[s["_action"]] = by_action.get(s["_action"], 0) + 1

    print(f"\n  汇总:")
    print(f"    by_risk:  {by_risk}")
    print(f"    by_action: {by_action}")
    print(f"  ✅ 写盘: {out} ({out.stat().st_size/1024:.1f} KB, "
          f"{len(balanced)} 条)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())