"""memory_migration.py — v0.26 MEMORY.md 瘦身迁移工具

把 MEMORY.md 链接对应的 .md 文件批量迁到 Prisiragent namespace。

策略:
- 双存:原 .md 不删(其他工具可能引用),MEMORY.md 还指原路径
- 按文件名 namespace 分类(自动)
- 每条用 task_submit 写到 Prisiragent(可走 v0.25 task queue)
- 沙箱验证:recall 测试召回质量

用法:
    # 1. 扫 MEMORY.md,列出所有待迁条目
    python memory_migration.py scan

    # 2. 批量迁到 Prisiragent(走 namespace)
    python memory_migration.py migrate

    # 3. 验证召回质量
    python memory_migration.py verify
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.oi_memory import OIMemory  # noqa
from memory.task_queue import TaskQueue  # noqa

MEMORY_DIR = Path("C:/Users/Administrator/.claude/projects/C--Users-Administrator/memory")
MEMORY_MD = MEMORY_DIR / "MEMORY.md"

# v0.26 决策:MEMORY.md 留的高频核心(用文件名前缀匹配,避免标题差异)
# 规则:工程原则 + 环境硬约束 + 当前活跃项目 + 常驻 daemon + 用户决策类
MEMORY_KEEP_FILE_PATTERNS = {
    # 工程原则
    "no-functional-duplication-principle",  # 分工不重复工程原则
    "user-engineering-preferences",  # user 工程偏好
    "vibe-coding-two-prompts",  # Vibe Coding 两个 prompt
    "exploration-blocked-vs-todo",  # 前沿探索类项目工程原则
    "2026-07-03-direction2-decision",  # 走方向 2 决策
    # 环境硬约束
    "bailian-env-vars",  # Bailian env vars
    "env-var-api-keys-autonomous-usage",  # env var API key 策略
    "obsidian-vault-as-canonical",  # Obsidian vault 规范
    "browser-cdp-uitars-glm52-route",  # 浏览器 CDP 端口
    "settings-json-serena-deferred",  # settings.json serena deferred
    "living-architecture-diagram-practice",  # Living architecture
    "bailian-embed-quota-exhausted",  # 百炼嵌入配额
    # 活跃项目核心状态
    "v024-session-memory-injector-shipped",  # v0.24 ship
    "v024-oi-memory-tier-migration",  # v0.24 tier 迁移副作用
    "v024-namespace-naming-design",  # v0.24 命名设计
    "v025-task-queue-shipped",  # v0.25 ship
    "v0251-shipped",  # v0.25.1 ship
    "v022-federation-shipped",  # v0.22 Federation + WireGuard
    # 常驻 daemon / 工具
    "aureon-v0195b-4-providers-active",  # v0.19.5b 4 provider
    "mcp-prisiragent-v061-everything-shipped",  # mcp_oiagent v0.6.1
    "everything-gui-registry-fix",  # Everything GUI fix
    "win10-shell-association-fix",  # Win10 shell fix
    # 决策类
    "fable5-actually-unavailable",  # Fable 5 不可用
    "new-key-verified",  # 新 Cursor key 验证
    "cursor-key-and-local-proxy",  # Cursor key + 15721 代理
    "proton-vpn-active-for-web",  # ProtonVPN 已开
}


def _is_keep(file: str) -> bool:
    """MEMORY.md 链接对应的 .md 文件是否在 KEEP 列表"""
    base = file.replace(".md", "").lower()
    return any(pattern in base for pattern in MEMORY_KEEP_FILE_PATTERNS)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("v026.migration")


def _filename_to_namespace(fname: str) -> str:
    """把 .md 文件名映射到 Prisiragent namespace"""
    f = fname.replace(".md", "")
    # aureon 系列
    if re.search(r"v?0?(18|19|20|21|22|23|24|25)", f) and "aureon" in f.lower():
        # 提取版本号
        m = re.search(r"v?0?(\d+)", f)
        if m:
            return f"aureon-v0.{m.group(1)[-1]}" if len(m.group(1)) == 2 else f"aureon-v0.{m.group(1)[0]}"
        return "aureon-other"
    if "aureon" in f.lower() or "ai-first-os" in f.lower() or "android-emulator" in f.lower():
        return "aureon-other"
    # 平台
    if "bailian" in f or "百炼" in f or "agi" in f.lower():
        return "platform-bailian"
    if "fable" in f.lower() or "cursor" in f.lower() or "composer" in f.lower():
        return "platform-cursor"
    if "glim" in f or "glm" in f.lower() or "minimax" in f.lower():
        return "platform-other-llm"
    if "stepfun" in f.lower():
        return "platform-stepfun"
    if "trustedari" in f.lower() or "trusted_ari" in f.lower():
        return "platform-trustedari"
    # voice / video
    if "voice" in f or "shandian" in f or "wispr" in f:
        return "voice-input"
    if "bili" in f or "video" in f or "audiobook" in f or "yt-note" in f or "douyin" in f:
        return "video-tools"
    # content / 学术
    if "wechat" in f or "xhs" in f or "pdf" in f or "note" in f or "fact-check" in f or "translate" in f:
        return "content-tools"
    if "scholaread" in f or "academic" in f or "paper" in f:
        return "academic-pipeline"
    # 工具集成
    if "everything" in f.lower() or "shell" in f.lower():
        return "tool-integration"
    if "mumu" in f.lower() or "avd" in f.lower():
        return "android-emulator"
    if "prisiragent" in f or "oi-" in f or "mcp" in f.lower() or "oi_memory" in f:
        return "prisiragent-integration"
    # 决策 / 原则
    if "原则" in f or "feedback" in f or "偏好" in f:
        return "principles"
    # default
    return "other"


def scan_memory_md() -> Iterator[dict]:
    """扫 MEMORY.md,产出所有 (title, file_path, namespace)"""
    if not MEMORY_MD.exists():
        log.error(f"MEMORY.md 不存在: {MEMORY_MD}")
        return
    content = MEMORY_MD.read_text(encoding="utf-8")
    for line_no, line in enumerate(content.splitlines(), start=1):
        m = re.match(r"^- \[(.+?)\]\(([^)]+\.md)\)", line)
        if m:
            title = m.group(1).strip()
            file = m.group(2)
            yield {
                "title": title,
                "file": file,
                "line": line_no,
                "abs_path": MEMORY_DIR / file,
                "namespace": _filename_to_namespace(file),
                "keep_in_memory_md": _is_keep(file),
            }


def cmd_scan():
    """扫 + 统计"""
    entries = list(scan_memory_md())
    print(f"\n=== 扫描结果: 共 {len(entries)} 条 ===\n")
    keep = [e for e in entries if e["keep_in_memory_md"]]
    migrate = [e for e in entries if not e["keep_in_memory_md"]]
    print(f"  MEMORY.md 保留: {len(keep)} 条")
    print(f"  迁 Prisiragent: {len(migrate)} 条")
    # 按 namespace 分组
    ns_count: dict = {}
    for e in migrate:
        ns_count[e["namespace"]] = ns_count.get(e["namespace"], 0) + 1
    print(f"\n=== 迁 Prisiragent 的 namespace 分布 ===")
    for ns, n in sorted(ns_count.items(), key=lambda x: -x[1]):
        print(f"  {ns}: {n} 条")
    print(f"\n=== 待保留条目(MEMORY_KEEP_FILE_PATTERNS 匹配)===")
    keep_entries = [e for e in entries if e["keep_in_memory_md"]]
    for e in sorted(keep_entries, key=lambda x: x["line"]):
        print(f"  ✅ L{e['line']}: {e['title'][:60]}  ({e['file']})")


def cmd_migrate(dry_run: bool = True):
    """批量迁:每条写到 Prisiragent namespace"""
    entries = [e for e in scan_memory_md() if not e["keep_in_memory_md"]]
    mem = OIMemory()
    print(f"\n=== {'DRY RUN' if dry_run else '真跑'}迁 {len(entries)} 条 ===\n")
    success = 0
    failed = 0
    for e in entries:
        abs_path = e["abs_path"]
        if not abs_path.exists():
            log.warning(f"  跳过(文件不存在): {e['file']}")
            failed += 1
            continue
        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception as ex:
            log.warning(f"  读失败: {e['file']}: {ex}")
            failed += 1
            continue
        # 去 frontmatter 后的内容(已经是 markdown 格式,直接存)
        # title 用 MEMORY.md 的 title(短),content 是 .md 全文
        if dry_run:
            print(f"  [DRY] ns={e['namespace']:25s} | {e['title'][:50]:50s} | {len(content)} chars")
        else:
            try:
                # v0.26 优化:加 alias tags 让 recall 不只靠 namespace
                fname_lower = e["file"].lower().replace(".md", "")
                alias_tags = []
                # 提取版本号
                import re as _re
                ver_match = _re.search(r"v0\.(\d{1,2})", fname_lower)
                if ver_match:
                    ver = ver_match.group(1).zfill(2)
                    alias_tags.append(f"ver:v0.{ver}")
                ver_short = _re.search(r"v0?(\d{1,2})", fname_lower)
                if ver_short:
                    alias_tags.append(f"ver:short-v{ver_short.group(1)}")
                # 提取关键词作为 tag
                for kw in ["aureon", "federation", "wireguard", "bailian", "cursor", "voice", "video", "obsidian", "pdf", "everything", "proton", "scholaread", "fable", "claude", "mcp", "android", "muji", "scholaread"]:
                    if kw in fname_lower:
                        alias_tags.append(f"kw:{kw}")
                # 主题 namespace 也作为 tag
                alias_tags.append(f"ns:{e['namespace']}")
                tags = ["memory_migration", "v0.26"] + alias_tags

                # 关键修复:OIMemory token_freq 只算 title + content 前 200 token
                # 必须在 content **头部** 注入 alias 关键词(否则 token_freq 不含)
                alias_header = " ".join(alias_tags).replace(":", "_") + "\n\n"
                enriched_content = alias_header + content

                mem.store(
                    layer="L3",
                    title=f"[memory_migration] {e['title']}",
                    content=enriched_content,
                    tags=tags,
                    namespace=e["namespace"],
                )
                success += 1
                log.info(f"  ✅ ns={e['namespace']}: {e['title'][:50]}")
            except Exception as ex:
                failed += 1
                log.error(f"  ❌ ns={e['namespace']}: {e['title'][:50]}: {ex}")
    print(f"\n=== 结果 ===")
    print(f"  成功: {success}")
    print(f"  失败: {failed}")


def cmd_verify(sample_size: int = 5):
    """验证召回质量:从迁的 namespace 抽几条,recall 验证能召回"""
    mem = OIMemory()
    namespaces = set()
    for e in scan_memory_md():
        if not e["keep_in_memory_md"]:
            namespaces.add(e["namespace"])

    print(f"\n=== 验证 {len(namespaces)} 个 namespace 的召回 ===\n")
    for ns in sorted(namespaces):
        # 在这个 namespace 里有几条
        items = mem.list_by_layer("L3", limit=200, namespace=ns)
        if not items:
            print(f"  ⚠️  {ns}: 0 条")
            continue
        # 取第一条的关键词做 recall
        sample = items[0]
        kw = re.findall(r"[\w一-鿿]+", sample.title)[1:3]  # 跳过 [memory_migration] 前缀
        query = " ".join(kw)
        hits = mem.recall(query, n=3, namespace=ns)
        ok = len(hits) >= 1 and hits[0].id == sample.id
        print(f"  {'✅' if ok else '❌'} {ns:25s} | {len(items):3d} 条 | query='{query}' | top hit id={hits[0].id if hits else 'NONE'}")


def main():
    import argparse
    p = argparse.ArgumentParser(description="v0.26 MEMORY.md 迁移工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="扫 MEMORY.md,显示保留/迁分组")

    p_mig = sub.add_parser("migrate", help="批量迁到 Prisiragent")
    p_mig.add_argument("--dry-run", action="store_true", default=True)
    p_mig.add_argument("--real", action="store_true", help="真跑(默认 dry_run)")

    p_v = sub.add_parser("verify", help="验证召回质量")
    p_v.add_argument("--sample-size", type=int, default=5)

    args = p.parse_args()
    if args.cmd == "scan":
        cmd_scan()
    elif args.cmd == "migrate":
        cmd_migrate(dry_run=not args.real)
    elif args.cmd == "verify":
        cmd_verify(args.sample_size)


if __name__ == "__main__":
    main()