"""scripts/extract_public_apis.py — 从 public-apis/public-apis README 抽取 API 数据。

输入:  scripts/_cache/public-apis-README.md (一次性 curl 下载,本扩展只读快照,
       绝不发起 PR/issue/comment 到上游仓库)
输出:  extensions/public-apis-promo/data/{categories,services,meta}.json

抽取规则:
  · ### Category      → H3 分类(51 项)
  · API | Description | Auth | HTTPS | CORS    → 列头行(跳过)
  · |:---|:---|:---|:---|:---|                  → 分隔行(跳过)
  · | [Name](url) | desc | auth | https | cors | → 数据行
  · "**[⬆ Back to Index](#index)**"             → 分类结束标记
  · 部分条目 6-8 列(多了 GoBase / Trial / Custom fields),只取前 5 列
  · APILayer 横幅 / MCP servers / Learn more 等 H2 段不在 ### 范围内,自动跳过

运行:
    curl -sL https://raw.githubusercontent.com/public-apis/public-apis/master/README.md \\
        -o scripts/_cache/public-apis-README.md
    python scripts/extract_public_apis.py
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "scripts" / "_cache" / "public-apis-README.md"
DATA_DIR = ROOT / "extensions" / "public-apis-promo" / "data"
SOURCE_URL = "https://github.com/public-apis/public-apis"

# H3 分类头:`### Animals`
CAT_RE = re.compile(r"^### (.+?)\s*$")
# 数据行:`| [Name](url) | desc | auth | https | cors |`  至少 5 列
# 取首列 [Name](url),次列 desc,3-5 列 auth/https/cors(可能带反引号如 `apiKey`)
ROW_RE = re.compile(
    r"^\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|\s*"   # | [name](url) |
    r"([^|]+?)\s*\|\s*"                          # desc
    r"([^|]+?)\s*\|\s*"                          # auth (含反引号原文)
    r"([^|]+?)\s*\|\s*"                          # https
    r"([^|]+?)\s*\|"                             # cors
)
# H2 锚定:只有看到 ## Index 之后才认 ### Categories 是真分类(之前的 APILayer/MCP 是 H2 banner)
INDEX_H2_RE = re.compile(r"^## Index\s*$")
# 列头/分隔行识别(确认跳过)
HEADER_RE = re.compile(r"^API\s*\|\s*Description\s*\|\s*Auth\s*\|\s*HTTPS\s*\|\s*CORS\s*$", re.I)
SEPARATOR_RE = re.compile(r"^\|:?---+")
BACK_RE = re.compile(r"\*\*\[⬆ Back to Index")
# 引号/空白清洗
QUOTE_RE = re.compile(r"^`|`$")


def slugify(name: str) -> str:
    """'Art & Design' → 'art-design';'Continuous Integration' → 'continuous-integration'"""
    s = name.lower()
    s = re.sub(r"&", "and", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def clean_cell(cell: str) -> str:
    """去前导/尾随反引号 + 空白;保留 'Yes'/'No'/'Unknown'/'apiKey'/'OAuth'/'X-Mashape-Key'"""
    return QUOTE_RE.sub("", cell).strip()


def extract() -> tuple[list[dict], list[dict], dict]:
    text = README.read_text(encoding="utf-8")

    categories: list[dict] = []
    services: list[dict] = []
    cur_cat: str | None = None
    cur_cat_count = 0
    seen_index = False  # ## Index 锚:在它之前的 H3 一律当作 banner(MCP/APILayer)

    for line in text.splitlines():
        # 锚定 ## Index:从这之后才认真分类
        if INDEX_H2_RE.match(line):
            seen_index = True
            cur_cat = None
            cur_cat_count = 0
            continue

        # 分类头
        if (m := CAT_RE.match(line)):
            if not seen_index:
                continue  # banner H3,跳过
            # 收尾上一个分类
            if cur_cat is not None and not any(c["name"] == cur_cat for c in categories):
                categories.append({
                    "name": cur_cat,
                    "slug": slugify(cur_cat),
                    "n_items": cur_cat_count,
                })
            cur_cat = m.group(1).strip()
            cur_cat_count = 0
            continue

        # 在首个 ### 之前的内容(APILayer banner / MCP / Learn more)全部跳过
        if cur_cat is None:
            continue

        # 跳分类结束标记
        if BACK_RE.search(line):
            categories.append({
                "name": cur_cat,
                "slug": slugify(cur_cat),
                "n_items": cur_cat_count,
            })
            cur_cat = None
            cur_cat_count = 0
            continue

        # 跳列头 + 分隔
        if HEADER_RE.match(line) or SEPARATOR_RE.match(line.strip()):
            continue

        # 数据行
        if (m := ROW_RE.match(line)):
            name = m.group(1).strip()
            url  = m.group(2).strip()
            desc = m.group(3).strip()
            auth = clean_cell(m.group(4))
            https = clean_cell(m.group(5))
            cors = clean_cell(m.group(6))
            services.append({
                "cat":   cur_cat,
                "name":  name,
                "url":   url,
                "desc":  desc,
                "auth":  auth,
                "https": https,
                "cors":  cors,
                "level": 1,
            })
            cur_cat_count += 1
            continue

        # 其他行(空行、`### ` 之间的 br 标签等)忽略

    # 收尾 — 最后一个分类可能没 Back to Index
    if cur_cat is not None and not any(c["name"] == cur_cat for c in categories):
        categories.append({
            "name": cur_cat,
            "slug": slugify(cur_cat),
            "n_items": cur_cat_count,
        })

    meta = {
        "source":             SOURCE_URL,
        "snapshot_date":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_categories":   len(categories),
        "total_services":     len(services),
        "auth_distribution":  _dist([s["auth"] for s in services]),
        "https_distribution": _dist([s["https"] for s in services]),
        "cors_distribution":  _dist([s["cors"] for s in services]),
        "license_note":       "Data 来源 public-apis/public-apis(MIT);二次使用属各服务方各自条款",
        "agents_note":        "无 AGENTS.md 限制,本扩展只读快照,绝不发起 PR/issue/comment",
    }
    return categories, services, meta


def _dist(values: list[str]) -> dict[str, int]:
    """统计频次:{value: count}"""
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cats, svcs, meta = extract()

    (DATA_DIR / "categories.json").write_text(
        json.dumps(cats, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA_DIR / "services.json").write_text(
        json.dumps(svcs, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[extract] categories={len(cats)} services={len(svcs)}")
    print(f"[extract] → {DATA_DIR}")
    for c in cats[:5]:
        print(f"  - {c['name']} ({c['slug']}) → {c['n_items']} items")
    print(f"[extract] snapshot={meta['snapshot_date']}")
    print(f"[extract] auth_top5={list(meta['auth_distribution'].items())[:5]}")
    print(f"[extract] https_dist={meta['https_distribution']}")
    print(f"[extract] cors_dist={meta['cors_distribution']}")


if __name__ == "__main__":
    main()
