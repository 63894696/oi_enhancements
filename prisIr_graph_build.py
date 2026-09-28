"""prisIr_graph_build.py — 扫 Obsidian vault,解析 frontmatter/heading/wikilink 入图

设计:
- 纯函数 parse_md_file(content) → (title, frontmatter, headings, edges)
- 单文件解析无 IO,易于单元测试
- build_vault(vault_dir, db_path, rebuild=False, debug=False) 负责 IO + 增量
- 不抽 LLM(零成本);只抽 [[wikilink]] + frontmatter + heading

可独立测试:
    python prisIr_graph_build.py     # 跑内置 smoke test(tmp vault)
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import prisIr_graph_store as store

# ── 正则 ──────────────────────────────────────────────────────────
# 只匹配 [[target]] 或 [[target|alias]] 或 [[target#heading]],过滤含 [[ / ]] 的破损
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\]]+?)(?:#[^\[\]|]+?)?(?:\|[^\[\]]+?)?\]\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(raw: str) -> dict | None:
    """解析 YAML frontmatter。

    优先 yaml.safe_load(PyYAML);不可用降级到 'key: value' 行解析。
    失败返回 None(不抛,便于 vault 中部分坏文件不阻断整批)。
    """
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return None
    fm_text = m.group(1)
    # PyYAML 优先
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(fm_text)
        if isinstance(data, dict):
            return data
        return {"_raw": data}
    except ImportError:
        pass
    except Exception:
        pass
    # Fallback: 简单 'key: value' 行解析(只支持字符串 / list 标签行)
    out: dict[str, Any] = {}
    for line in fm_text.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        # tags: [a, b, c]
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            out[k] = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
        else:
            # 去引号
            out[k] = v.strip("'\"")
    return out or None


def parse_headings(content: str) -> list[str]:
    """收集所有 heading 行(去前导 #,保留文本)。"""
    out = []
    for line in content.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            out.append(f"{'#' * level} {m.group(2)}")
    return out


def parse_wikilinks(content: str) -> list[dict]:
    """提取 [[wikilink]] → {dst_title, line_no, section}。

    - line_no:1-indexed 行号
    - section:所属 heading 文本(无 heading 则 None)

    破损匹配(包含未配对的 [[ ]])直接跳过。
    """
    edges: list[dict] = []
    current_section: str | None = None
    for i, line in enumerate(content.splitlines(), start=1):
        # 维护当前 heading 上下文(只跟踪 ## ##  ## ###,不区分级别)
        h = _HEADING_RE.match(line)
        if h:
            current_section = f"{'#' * len(h.group(1))} {h.group(2)}"
        for m in _WIKILINK_RE.finditer(line):
            target = m.group(1).strip()
            if not target:
                continue
            edges.append({
                "dst_title": target,
                "line_no": i,
                "section": current_section,
            })
    return edges


def parse_md_file(path: Path) -> dict:
    """单文件解析,返回 {title, frontmatter, headings, edges, error?}。

    失败(读不了/解码错)返回带 error 字段的 dict,不抛。
    """
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = path.read_text(encoding="gbk")
        except Exception as e:
            return {"title": path.stem, "frontmatter": None, "headings": [],
                    "edges": [], "error": f"decode error: {e}"}
    except Exception as e:
        return {"title": path.stem, "frontmatter": None, "headings": [],
                "edges": [], "error": f"read error: {e}"}

    title = path.stem  # 文件名去 .md
    fm = parse_frontmatter(content)
    # 解析时不包含 frontmatter 行(避免把 frontmatter 里的 : 当 heading)
    body_start = 0
    fm_match = _FRONTMATTER_RE.match(content)
    if fm_match:
        body_start = fm_match.end()
    body = content[body_start:]
    headings = parse_headings(body)
    edges = parse_wikilinks(body)
    return {"title": title, "frontmatter": fm, "headings": headings, "edges": edges}


# ── 全 vault 构建 ─────────────────────────────────────────────────
def build_vault(
    vault_dir: Path,
    db_path: Path = store.DB_PATH,
    rebuild: bool = False,
    debug: bool = False,
) -> dict:
    """扫 vault → 解析 → 入库(增量)。返回本次 {scanned, added, updated, removed, errors, elapsed}。"""
    if not vault_dir.exists():
        return {"ok": False, "error": f"vault 不存在: {vault_dir}"}

    store.init_schema(db_path)
    t0 = time.time()
    scanned = 0
    added = 0
    updated = 0
    errors: list[str] = []

    # 第一遍:扫所有 md,记录 path → mtime
    md_files: list[tuple[Path, str, float]] = []  # (abs_path, rel_posix, mtime)
    for p in vault_dir.rglob("*.md"):
        rel = p.relative_to(vault_dir).as_posix()
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        md_files.append((p, rel, mtime))

    # 增量:如果 DB 中已存在且 mtime 未变,跳过解析
    for abs_path, rel, mtime in md_files:
        scanned += 1
        existing = None
        if not rebuild:
            existing = store.get_node_by_path(rel, db_path)
            if existing and existing["mtime"] >= mtime - 0.5:  # 0.5s 容差
                # mtime 视为未变,跳过
                continue
        try:
            parsed = parse_md_file(abs_path)
        except Exception as e:
            errors.append(f"{rel}: {e}")
            continue
        if "error" in parsed:
            errors.append(f"{rel}: {parsed['error']}")
            # 即使解析报错,仍 upsert 一个空 node 保证 path 在 DB 里被追踪
            try:
                nid = store.upsert_node(
                    rel, parsed["title"], None, [], mtime, db_path
                )
                store.replace_edges_for_node(nid, [], db_path)
            except Exception:
                pass
            continue
        try:
            nid = store.upsert_node(
                rel,
                parsed["title"],
                parsed["frontmatter"],
                parsed["headings"],
                mtime,
                db_path,
            )
            store.replace_edges_for_node(nid, parsed["edges"], db_path)
            if existing:
                updated += 1
            else:
                added += 1
        except Exception as e:
            errors.append(f"{rel} (write): {e}")
            continue

    # 第二遍:解析孤立链接 → 填 dst_id(新增节点可能成为老孤立边的目标)
    filled = store.resolve_orphans(db_path)

    # 第三遍:对账删除 vault 里已不存在的节点
    removed = store.delete_node_orphans({rel for _, rel, _ in md_files}, db_path)

    elapsed = round(time.time() - t0, 2)
    return {
        "ok": True,
        "vault": str(vault_dir),
        "scanned": scanned,
        "added": added,
        "updated": updated,
        "removed": removed,
        "orphan_resolved": filled,
        "errors": len(errors),
        "error_samples": errors[:5] if debug else [],
        "elapsed_sec": elapsed,
    }


# ── Smoke Test ────────────────────────────────────────────────────
def _smoke():
    import tempfile

    with tempfile.TemporaryDirectory(prefix="prisIr_graph_smoke_") as tmp:
        tmp_vault = Path(tmp)
        # 造 5 个 md:含 wikilink 跨链 + frontmatter + heading + 孤立链接
        (tmp_vault / "a.md").write_text(
            "---\ntags: [foo, alpha]\nauthor: me\n---\n"
            "# A\n"
            "## Section 1\n"
            "see [[b]] for next step\n"
            "and [[c#H1]] and [[ghost-file]]\n",
            encoding="utf-8",
        )
        (tmp_vault / "b.md").write_text(
            "# B\n"
            "back to [[a]] and [[d|alias]]\n",
            encoding="utf-8",
        )
        (tmp_vault / "c.md").write_text(
            "# C\n"
            "## H1\n"
            "links [[a]] here\n",
            encoding="utf-8",
        )
        (tmp_vault / "d.md").write_text(
            "# D\n"
            "no wikilink\n",
            encoding="utf-8",
        )
        (tmp_vault / "nested").mkdir()
        (tmp_vault / "nested" / "e.md").write_text(
            "# E\n"
            "link [[a]] and [[non-existent]]\n",
            encoding="utf-8",
        )

        tmp_db = Path(tmp) / "smoke.db"
        result = build_vault(tmp_vault, tmp_db, debug=True)
        print("[smoke] build result:", json.dumps(result, ensure_ascii=False, indent=2))
        assert result["ok"], "build failed"
        assert result["scanned"] == 5, f"scanned={result['scanned']}"

        s = store.stats(tmp_db)
        print("[smoke] stats:", json.dumps(s, ensure_ascii=False, indent=2))
        assert s["nodes"] == 5
        assert s["edges"] >= 6
        # 孤立链接至少有 ghost-file 和 non-existent
        assert s["orphaned_edges"] >= 1

        # 验证 a 节点已入库(具体边的检查留给 query 层)
        a_node = store.get_node_by_path("a.md", tmp_db)
        assert a_node and a_node["title"] == "a"
        assert a_node["frontmatter"]["tags"] == ["foo", "alpha"]

        # 增量再跑,应全部 no-op
        result2 = build_vault(tmp_vault, tmp_db)
        print("[smoke] incremental:", result2["added"], result2["updated"])
        assert result2["added"] == 0 and result2["updated"] == 0

        # 删一个文件,再 build,应 removed=1
        (tmp_vault / "d.md").unlink()
        result3 = build_vault(tmp_vault, tmp_db)
        print("[smoke] after delete:", result3["removed"])
        assert result3["removed"] == 1

        print("[smoke] PASS")


if __name__ == "__main__":
    _smoke()
    sys.exit(0)