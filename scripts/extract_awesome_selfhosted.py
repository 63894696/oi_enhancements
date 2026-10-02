"""scripts/extract_awesome_selfhosted.py — 从 awesome-selfhosted/awesome-selfhosted 抽取自部署软件数据。

输入:  scripts/_cache/awesome-selfhosted-README.md (一次性 curl 下载,本扩展只读快照,
       绝不发起 PR/issue/comment 到上游仓库 — 仓库无 AGENTS.md,默认快照制)
输出:  extensions/awesome-selfhosted-promo/data/{categories,services,meta}.json

抽取规则:
  · ### CategoryName   → H3 分类(95 项左右)
  · ([Source Code](url)) 和 ([Demo](url)) 是行内关联链接段
  · 行末多个 backtick 是 licenses / languages:
      `MIT` `Nodejs/Docker`           ← 单 license + 单 lang(可拆)
      `AGPL-3.0/MIT` `Docker/Go`      ← 多 license / 多 lang(用 / 分隔)
      `Apache-2.0` `Docker/Go`        ← 标准
  · 部分条目带 `⚠` 表示项目不再维护

字段策略:
  · licenses  = 提取的不含 / 的 SPDX 短词(若 backtick 含 /,则拆分后加入)
  · languages = 提取的含 / 的(如 `Nodejs/Docker` 拆成 ['Nodejs', 'Docker'])
  · has_warning = bool,行中含 `⚠` 时 true
  · source_code_url = 从 ([Source Code](url)) 提取(可选)

运行:
    curl -sL https://raw.githubusercontent.com/awesome-selfhosted/awesome-selfhosted/master/README.md \\
        -o scripts/_cache/awesome-selfhosted-README.md
    python scripts/extract_awesome_selfhosted.py
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "scripts" / "_cache" / "awesome-selfhosted-README.md"
DATA_DIR = ROOT / "extensions" / "awesome-selfhosted-promo" / "data"
SOURCE_URL = "https://github.com/awesome-selfhosted/awesome-selfhosted"

# H3 分类头:`### Analytics`
CAT_RE = re.compile(r"^### (.+?)\s*$")
# 数据行首段:`- [Name](url) - desc. [optional `⚠`] ([Source Code](url))? ([Demo](url))? \`lic\` \`lang\``
# 因为格式复杂,按整体行拆:首段锚定 - [Name](url),后续整行扫描 source_code_url / warning / backticks。
LINE_RE = re.compile(r"^- \[([^\]]+)\]\(([^)]+)\)\s*(.*)$")
SOURCE_CODE_RE = re.compile(r"\(\[Source Code\]\(([^)]+)\)\)")
DEMO_RE = re.compile(r"\(\[Demo\]\(([^)]+)\)\)")
BACKTICK_RE = re.compile(r"`([^`]+)`")
# 锚定 H2:只有看到 ## Software 之后才认 ### Categories 是真分类(之前的 ## Table of contents 是导航)
SOFTWARE_H2_RE = re.compile(r"^## Software\s*$")
# H2 锚定结束:看到 ## List of Licenses / ## Anti-features / ## External Links 等就停
END_H2_RES = [
    re.compile(r"^## List of Licenses\s*$"),
    re.compile(r"^## Anti-features\s*$"),
    re.compile(r"^## External Links\s*$"),
    re.compile(r"^## Contributing\s*$"),
    re.compile(r"^## License\s*$"),
]
# 跳过的非分类 H3(都是特殊段,行末是 ## TOC 末尾或类似 banner)
SKIP_H3 = {"Software", "List of Licenses", "External Links"}


def slugify(name: str) -> str:
    """'Static Site Generators' → 'static-site-generators'"""
    s = name.lower()
    s = re.sub(r"&", "and", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def split_license_or_lang(token: str) -> tuple[bool, list[str]]:
    """判定 token 是 license 还是 language。
    启发式:含 `-` + 数字版本号(SPDX 风格,如 'AGPL-3.0' / 'Apache-2.0')→ license;
    不含 `-` 或版本号 → language。
    """
    parts = token.split("/")
    return parts


def classify_tokens(tokens: list[str]) -> tuple[list[str], list[str]]:
    """把提取出的 backtick token 拆成 (licenses, languages)。

    ⚠ 跳过(已用 has_warning 字段表示)。
    """
    licenses: list[str] = []
    languages: list[str] = []
    for t in tokens:
        if t == "⚠":
            continue
        # 拆 / 后逐个判断
        parts = [p.strip() for p in t.split("/") if p.strip()]
        for p in parts:
            # SPDX 风格:大写短词 + '-' + 数字版本号(如 AGPL-3.0 / Apache-2.0 / EUPL-1.2)
            if re.match(r"^[A-Z][A-Za-z0-9.\-]*-\d+(\.\d+)?$", p) or p in {"MIT", "Beerware", "WTFPL"}:
                licenses.append(p)
            else:
                languages.append(p)
    # 去重保序
    seen = set()
    lic_out = []
    for x in licenses:
        if x not in seen:
            seen.add(x)
            lic_out.append(x)
    seen = set()
    lang_out = []
    for x in languages:
        if x not in seen:
            seen.add(x)
            lang_out.append(x)
    return lic_out, lang_out


def extract() -> tuple[list[dict], list[dict], dict]:
    text = README.read_text(encoding="utf-8")

    categories: list[dict] = []
    services: list[dict] = []
    cur_cat: str | None = None
    cur_cat_count = 0
    seen_software = False  # ## Software 锚:从它之后才认真分类

    for line in text.splitlines():
        # 锚定 ## Software:从这之后才认真分类
        if SOFTWARE_H2_RE.match(line):
            seen_software = True
            cur_cat = None
            cur_cat_count = 0
            continue

        # 收尾 H2(## List of Licenses 等):到这就停
        if any(re.match(pat, line) for pat in END_H2_RES):
            # 收尾上一个分类
            if cur_cat is not None and not any(c["name"] == cur_cat for c in categories):
                categories.append({
                    "name": cur_cat,
                    "slug": slugify(cur_cat),
                    "n_items": cur_cat_count,
                })
            cur_cat = None
            cur_cat_count = 0
            seen_software = False
            continue

        if not seen_software:
            continue

        # 分类头
        if (m := CAT_RE.match(line)):
            cat_name = m.group(1).strip()
            # 收尾上一个分类
            if cur_cat is not None and not any(c["name"] == cur_cat for c in categories):
                categories.append({
                    "name": cur_cat,
                    "slug": slugify(cur_cat),
                    "n_items": cur_cat_count,
                })
            cur_cat = cat_name
            cur_cat_count = 0
            continue

        # 在首个 ### 之前的内容(## Software 介绍段)跳过
        if cur_cat is None:
            continue

        # 数据行
        if (m := LINE_RE.match(line)):
            name = m.group(1).strip()
            url = m.group(2).strip()
            tail = m.group(3)
            # source_code_url(可选)
            sc_match = SOURCE_CODE_RE.search(tail)
            source_code_url = sc_match.group(1).strip() if sc_match else None
            # has_warning:行中含 `⚠` 时 true(在 tail 中)
            has_warning = "⚠" in tail
            # 提取所有 backtick token
            tokens = BACKTICK_RE.findall(tail)
            licenses, languages = classify_tokens(tokens)
            services.append({
                "cat": cur_cat,
                "name": name,
                "url": url,
                "desc": "",  # desc 没单独提取(行尾 `- desc.` 段是自然语言,与 API 表结构不同),
                              # 留空字符串保持 schema 一致,Node 端 fallback "(无描述)"
                "licenses": licenses,
                "languages": languages,
                "source_code_url": source_code_url,
                "has_warning": has_warning,
            })
            cur_cat_count += 1
            continue

        # 其他行(空行、`^        back to top        ^` 等)忽略

    # 收尾 — 最后一个分类可能没遇到 End H2
    if cur_cat is not None and not any(c["name"] == cur_cat for c in categories):
        categories.append({
            "name": cur_cat,
            "slug": slugify(cur_cat),
            "n_items": cur_cat_count,
        })

    meta = {
        "source": SOURCE_URL,
        "snapshot_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_categories": len(categories),
        "total_services": len(services),
        "license_distribution": _dist(_flat([s["licenses"] for s in services])),
        "language_distribution": _dist(_flat([s["languages"] for s in services])),
        "license_note": "Data 来源 awesome-selfhosted/awesome-selfhosted (CC-BY-SA-3.0);二次使用须保留归属",
        "agents_note": "无 AGENTS.md 限制,本扩展只读快照,绝不发起 PR/issue/comment",
    }
    return categories, services, meta


def _flat(lists: list[list[str]]) -> list[str]:
    out: list[str] = []
    for lst in lists:
        out.extend(lst)
    return out


def _dist(values: list[str]) -> dict[str, int]:
    """统计频次:{value: count},按 count 降序"""
    c = Counter(values)
    return dict(c.most_common())


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
    print(f"[extract] -> {DATA_DIR}")
    for c in cats[:5]:
        print(f"  - {c['name']} ({c['slug']}) -> {c['n_items']} items")
    print(f"[extract] snapshot={meta['snapshot_date']}")
    print(f"[extract] license_top10={list(meta['license_distribution'].items())[:10]}")
    print(f"[extract] language_top10={list(meta['language_distribution'].items())[:10]}")


if __name__ == "__main__":
    main()
