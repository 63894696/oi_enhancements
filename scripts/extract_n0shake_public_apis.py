"""scripts/extract_n0shake_public_apis.py — 从 n0shake/Public-APIs README 抽取 API 数据(Sprint 2 Phase A)。

输入:  scripts/_cache/n0shake-public-apis-README.md(curl 下载,只读快照,不联网回写)
输出:  extensions/n0shake-public-apis-promo/data/{categories,services,meta}.json

抽取规则:
  · ## APIs              → H2 锚(锚后 ### 才认分类)
  · ### CategoryName     → H3 分类(59 项,尾部 Credits/More Resources/Contributions 无表格,跳过)
  · #### Open Licenses   → H4 子分类(rows 归属父 H3,如 Legal)
  · `| [**Name**](url) | desc | open_trial |` → 数据行(3 列)
  · open_trial 列常见值:**N/A**,**Unknown**,💸,![Open Source](...)(=Open Source)
  · `[⬆ Back to Table of Contents](#...)` → 分类结束标记

运行:
    python scripts/extract_n0shake_public_apis.py
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "scripts" / "_cache" / "n0shake-public-apis-README.md"
DATA_DIR = ROOT / "extensions" / "n0shake-public-apis-promo" / "data"
SOURCE_URL = "https://github.com/n0shake/Public-APIs"
README_URL = "https://raw.githubusercontent.com/n0shake/Public-APIs/master/README.md"

# 锚定 ## APIs:从这之后才认真分类
APIS_H2_RE = re.compile(r"^## APIs\s*$")
H3_RE = re.compile(r"^### (.+?)\s*$")
H4_RE = re.compile(r"^#### (.+?)\s*$")
# 数据行:首列 name+url(可能含多个链接),次列 desc,三列 open_trial
ROW_RE = re.compile(r"^\|\s*(.*?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")
DATA_HEADER_RE = re.compile(r"^\|\s*API\s*\|\s*Description\s*\|\s*Open/Trial\s*\|?$", re.I)
SEPARATOR_RE = re.compile(r"^\|\s*:?---+")
BACK_RE = re.compile(r"\[⬆ Back to Table of Contents")
SKIP_CATS = {"Credits", "More Resources", "Contributions"}
IMG_OPEN_RE = re.compile(r"!\[Open Source\]\([^)]+\)(?:\s*\"Open Source\")?")
BOLD_RE = re.compile(r"^\*\*(.+?)\*\*$")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"&", "and", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or re.sub(r"\s+", "-", name).strip("-")


def parse_first_cell(raw: str) -> tuple[str, str]:
    """首列 `[**Name](u) ([Alt](u2))` → name='Name (Alt)' url=u"""
    links = LINK_RE.findall(raw)
    if not links:
        m = BOLD_RE.match(raw.strip())
        return (m.group(1) if m else raw.strip(), "")
    name = BOLD_RE.match(links[0][0])
    primary_name = name.group(1) if name else links[0][0]
    if len(links) > 1:
        extras = " / ".join((BOLD_RE.match(n) or type("", (), {"group": lambda *_: n})()).group(1)
                            for n, _ in links[1:])
        primary_name = f"{primary_name} ({extras})"
    return primary_name.strip(), links[0][1].strip()


def clean_open_trial(raw: str) -> str:
    s = raw.strip()
    if IMG_OPEN_RE.search(s): return "Open Source"
    m = BOLD_RE.match(s)
    if m: return m.group(1).strip()
    return s.strip("`").strip()


def clean_desc(raw: str) -> str:
    return raw.strip().rstrip("|").strip()


def extract() -> tuple[list[dict], list[dict], dict]:
    text = README.read_text(encoding="utf-8")
    categories: list[dict] = []
    services: list[dict] = []
    cur_cat: str | None = None
    cur_cat_count = 0
    seen_apis_h2 = False

    def flush_cat():
        """收尾当前 cat(非 SKIP_CATS、未入 categories)"""
        if cur_cat is None or cur_cat in SKIP_CATS:
            return
        if any(c["name"] == cur_cat for c in categories):
            return
        categories.append({"name": cur_cat, "slug": slugify(cur_cat), "n_items": cur_cat_count})

    for line in text.splitlines():
        if APIS_H2_RE.match(line):
            seen_apis_h2 = True
            cur_cat = None
            cur_cat_count = 0
            continue
        if not seen_apis_h2:
            continue
        if H4_RE.match(line):
            continue  # 子段,rows 仍记父 H3 cat
        if (m := H3_RE.match(line)):
            flush_cat()
            cur_cat = m.group(1).strip()
            cur_cat_count = 0
            if cur_cat in SKIP_CATS:
                cur_cat = None
            continue
        if cur_cat is None:
            continue
        if BACK_RE.search(line):
            flush_cat()
            cur_cat = None
            cur_cat_count = 0
            continue
        if DATA_HEADER_RE.match(line) or SEPARATOR_RE.match(line.strip()):
            continue
        if (m := ROW_RE.match(line)):
            name, url = parse_first_cell(m.group(1).strip())
            services.append({
                "cat": cur_cat, "name": name, "url": url,
                "desc": clean_desc(m.group(2)),
                "open_trial": clean_open_trial(m.group(3)),
                "level": 1,
            })
            cur_cat_count += 1
            continue

    flush_cat()  # 最后一个分类可能没 Back to Table of Contents

    # open_trial 分布
    open_trial_dist: dict[str, int] = {}
    for s in services:
        k = s["open_trial"] or "(空)"
        open_trial_dist[k] = open_trial_dist.get(k, 0) + 1
    open_trial_dist = dict(sorted(open_trial_dist.items(), key=lambda kv: -kv[1]))

    meta = {
        "source": SOURCE_URL,
        "snapshot_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_categories": len(categories),
        "total_services": len(services),
        "open_trial_distribution": open_trial_dist,
        "license_note": "Data 来源 n0shake/Public-APIs(CC BY-NC-SA 4.0,见 README 末尾);二次使用属各服务方各自条款",
        "agents_note": "无 AGENTS.md 限制;上游无 AGENTS.md 也不收 PR 邀请,本扩展只读快照,绝不发起 PR/issue/comment",
    }
    return categories, services, meta


def main() -> None:
    if not README.exists():
        import urllib.request
        README.parent.mkdir(parents=True, exist_ok=True)
        print(f"[extract] downloading {README_URL} → {README}")
        urllib.request.urlretrieve(README_URL, README)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cats, svcs, meta = extract()
    (DATA_DIR / "categories.json").write_text(json.dumps(cats, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA_DIR / "services.json").write_text(json.dumps(svcs, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[extract] categories={len(cats)} services={len(svcs)}")
    print(f"[extract] → {DATA_DIR}")
    for c in cats[:5]:
        print(f"  - {c['name']} ({c['slug']}) → {c['n_items']} items")
    print(f"[extract] snapshot={meta['snapshot_date']}")
    print(f"[extract] open_trial_dist={meta['open_trial_distribution']}")


if __name__ == "__main__":
    main()