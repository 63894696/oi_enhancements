"""scripts/extract_free_for_dev.py — 从 ripienaar/free-for-dev README 抽取免费资源数据。

输入:  scripts/_cache/free-for-dev-README.md (一次性 curl 下载,AGENTS.md 禁止 AI 改原仓库,
       所以我们不 git clone、不发 PR、不评论,纯本地只读快照)
输出:  extensions/free-for-dev-promo/data/{categories,services,meta}.json

抽取规则:
  · ## 标题          → 一级分类(categories.json)
  · * [Name](url) - desc   (2 空格缩进)  → 顶级服务条目
  · * Name - desc           (4 空格缩进)  → 嵌套条目(AWS > CloudFront 这种)
  · 嵌套条目 name 写成 "ParentName > ChildName" 便于 UI 折叠显示
  · "Full, detailed list - url" 这种结尾引用,作为顶级条目的补充链接(进 meta.extra_links)

运行:
    curl -sL https://raw.githubusercontent.com/ripienaar/free-for-dev/master/README.md \\
        -o scripts/_cache/free-for-dev-README.md
    python scripts/extract_free_for_dev.py
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "scripts" / "_cache" / "free-for-dev-README.md"
DATA_DIR = ROOT / "extensions" / "free-for-dev-promo" / "data"
SOURCE_URL = "https://github.com/ripienaar/free-for-dev"

CAT_RE    = re.compile(r"^## (.+?)\s*$")
PARENT_RE = re.compile(r"^  \* \[([^\]]+)\]\(([^)]+)\)\s*$")  # 无 desc,只挂子条目
TOP_RE    = re.compile(r"^  \* \[([^\]]+)\]\(([^)]+)\)\s*-\s*(.+)$")
CHILD_RE  = re.compile(r"^    \* (.+?)\s*-\s*(.+)$")
EXTRA_RE  = re.compile(r"^    \* (Full[^*]*?)\s*-\s*(.+)$")  # "Full, detailed list - https://..."


def slugify(name: str) -> str:
    """'Major Cloud Providers' → 'major-cloud-providers'(跟 TOC anchor 匹配)"""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def extract() -> tuple[list[dict], list[dict], dict]:
    text = README.read_text(encoding="utf-8")

    categories: list[dict] = []
    services: list[dict] = []
    cur_cat: str | None = None
    cur_top_name: str | None = None

    for line in text.splitlines():
        if (m := CAT_RE.match(line)):
            cur_cat = m.group(1).strip()
            cur_top_name = None
            continue

        if cur_cat is None:
            continue  # 在 TOC 之前的所有内容跳过

        if (m := PARENT_RE.match(line)):
            name, url = m.group(1).strip(), m.group(2).strip()
            cur_top_name = name
            cur_top_url = url
            services.append({
                "cat":   cur_cat,
                "name":  name,
                "url":   url,
                "desc":  "",   # parent-only,desc 由子条目填充
                "level": 1,
            })
            continue

        if (m := TOP_RE.match(line)):
            name, url, desc = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
            cur_top_name = name
            cur_top_url = url
            services.append({
                "cat":   cur_cat,
                "name":  name,
                "url":   url,
                "desc":  desc,
                "level": 1,
            })
            continue

        if (m := CHILD_RE.match(line)):
            name, desc = m.group(1).strip(), m.group(2).strip()
            # GCP/AWS 子条目 4 空格缩进,有 [name](url) 或无 link(无 link 时继承父 link)
            link_m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", name)
            if link_m:
                child_name = link_m.group(1).strip()
                child_url = link_m.group(2).strip()
                rest = name[link_m.end():].strip()
            else:
                child_name = name
                child_url = ""
                rest = ""
            full_name = f"{cur_top_name} > {child_name}" if cur_top_name else child_name
            services.append({
                "cat":    cur_cat,
                "name":   full_name,
                "url":    child_url,
                "desc":   desc,
                "level":  2,
                "parent": cur_top_name or "",
                "parent_url": cur_top_url if not child_url else "",
                "rest":   rest,  # 偶尔有 "- also X" 后缀
            })
            continue

        # 分类结束(下个 ## 或空行 + Back to Top)→ 写 category
        if line.startswith("**[⬆️ Back to Top]"):
            n = sum(1 for s in services if s["cat"] == cur_cat)
            categories.append({
                "name":     cur_cat,
                "slug":     slugify(cur_cat),
                "n_items":  n,
            })

    # 收尾 — 最后分类可能没 Back to Top
    if cur_cat and not any(c["name"] == cur_cat for c in categories):
        n = sum(1 for s in services if s["cat"] == cur_cat)
        categories.append({
            "name":    cur_cat,
            "slug":    slugify(cur_cat),
            "n_items": n,
        })

    meta = {
        "source":             SOURCE_URL,
        "snapshot_date":      datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_categories":   len(categories),
        "total_services":     len(services),
        "level1_count":       sum(1 for s in services if s["level"] == 1),
        "level2_count":       sum(1 for s in services if s["level"] == 2),
        "license_note":       "Data 来源 ripienaar/free-for-dev 公共协作;二次使用属各服务方各自条款",
        "agents_note":        "原仓库 AGENTS.md 禁止 AI 贡献,本扩展只读快照,绝不发起 PR/issue/comment",
    }
    return categories, services, meta


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cats, svcs, meta = extract()

    (DATA_DIR / "categories.json").write_text(
        json.dumps(cats, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA_DIR / "services.json").write_text(
        json.dumps(svcs, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[extract] categories={len(cats)} services={len(svcs)} "
          f"(L1={meta['level1_count']} L2={meta['level2_count']})")
    print(f"[extract] → {DATA_DIR}")
    # sanity print 前 3 个分类头
    for c in cats[:3]:
        print(f"  - {c['name']} ({c['slug']}) → {c['n_items']} items")
    print(f"[extract] snapshot={meta['snapshot_date']}")


if __name__ == "__main__":
    main()
