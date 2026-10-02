"""scripts/extract_awesome_hub.py — 从 sindresorhus/awesome README 抽取分类 + 主题入口。

输入:  scripts/_cache/sindresorhus-awesome-README.md (只读快照,不发 PR/issue/comment)
输出:  extensions/awesome-hub-promo/data/{categories,topics,meta}.json

抽取规则:
  · ## Category       → H2 分类(实际 27 项,SKIP_H2:Contents/Contributing 跳过)
  · - [Name](url) - desc / - Name - desc → L1 主题
  · \t- [Name](url) - desc / \t- Name   → L2 嵌套主题(部分上游用 4 空格缩进)
  · 不递归解析子仓库 README(只抽本 hub 列表入口链接,避免无止境递归)

运行:
    curl -sL https://raw.githubusercontent.com/sindresorhus/awesome/main/readme.md \\
        -o scripts/_cache/sindresorhus-awesome-README.md
    python scripts/extract_awesome_hub.py
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "scripts" / "_cache" / "sindresorhus-awesome-README.md"
DATA_DIR = ROOT / "extensions" / "awesome-hub-promo" / "data"
SOURCE_URL = "https://github.com/sindresorhus/awesome"
RAW_URL = "https://raw.githubusercontent.com/sindresorhus/awesome/main/readme.md"

SKIP_H2 = {"Contents", "Contributing"}
H2_RE = re.compile(r"^## (.+?)\s*$")
ENTRY_RE = re.compile(r"^(?P<indent>[\t ]*)- (?P<rest>.+?)$")
LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)(?:\s*-\s*(.*))?$")
PLAIN_RE = re.compile(r"^([^\[\]]+?)(?:\s*-\s*(.*))?$")


def slugify(name: str) -> str:
    """'Art & Design' → 'art-design';'Continuous Integration' → 'continuous-integration'"""
    s = re.sub(r"&", " and ", name.lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def parse_entry(indent: str, rest: str) -> dict | None:
    """单条列表行 → {name, url, desc, level, has_link}"""
    level = 2 if indent else 1  # 缩进非空即嵌套(L2)
    if m := LINK_RE.match(rest):
        return {
            "name": m.group(1).strip(),
            "url": m.group(2).strip(),
            "desc": (m.group(3) or "").strip(),
            "level": level,
            "has_link": True,
        }
    if m := PLAIN_RE.match(rest):
        if not m.group(1).strip():
            return None
        return {
            "name": m.group(1).strip(),
            "url": "",
            "desc": (m.group(2) or "").strip(),
            "level": level,
            "has_link": False,
        }
    return None


def extract() -> tuple[list[dict], list[dict], dict]:
    text = README.read_text(encoding="utf-8")
    categories: list[dict] = []
    topics: list[dict] = []
    cur_cat: str | None = None
    cur_count = 0
    cur_with_link = 0

    for line in text.splitlines():
        if m := H2_RE.match(line):
            name = m.group(1).strip()
            if name in SKIP_H2:
                cur_cat = None
                continue
            cur_cat = name
            cur_count = cur_with_link = 0
            categories.append({"name": name, "slug": slugify(name), "n_topics": 0, "n_with_link": 0})
            continue

        if cur_cat is None:
            continue
        if not (em := ENTRY_RE.match(line)):
            continue
        parsed = parse_entry(em.group("indent"), em.group("rest"))
        if parsed is None:
            continue
        parsed["cat"] = cur_cat
        topics.append(parsed)
        cur_count += 1
        if parsed["has_link"]:
            cur_with_link += 1
        categories[-1]["n_topics"] = cur_count
        categories[-1]["n_with_link"] = cur_with_link

    meta = {
        "source": SOURCE_URL,
        "source_raw_url": RAW_URL,
        "snapshot_date": "2026-10-02",
        "snapshot_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_categories": len(categories),
        "total_topics": len(topics),
        "total_with_link": sum(1 for t in topics if t["has_link"]),
        "level_breakdown": {
            "L1": sum(1 for t in topics if t["level"] == 1),
            "L2": sum(1 for t in topics if t["level"] == 2),
        },
        "license_note": "CC0-1.0",
        "agents_note": (
            "sindresorhus/awesome 无 AGENTS.md,默认快照制;"
            "本扩展只读 README,不联网回写、不发 PR/issue/comment。"
        ),
    }
    return categories, topics, meta


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cats, topics, meta = extract()
    (DATA_DIR / "categories.json").write_text(
        json.dumps(cats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA_DIR / "topics.json").write_text(
        json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"categories: {len(cats)}")
    print(f"topics: {len(topics)} (with_link={meta['total_with_link']})")
    print(f"level: L1={meta['level_breakdown']['L1']} L2={meta['level_breakdown']['L2']}")
    print("top 5 by n_topics:")
    for c in sorted(cats, key=lambda c: c["n_topics"], reverse=True)[:5]:
        print(f"  {c['name']:30s} {c['n_topics']:3d} (link={c['n_with_link']})")


if __name__ == "__main__":
    main()
