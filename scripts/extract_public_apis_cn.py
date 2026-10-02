"""scripts/extract_public_apis_cn.py — 从 llf007/public-apis-cn README 抽取国内 API 数据。

输入:  scripts/_cache/public-apis-cn-README.md (一次性 curl 下载,纯本地只读快照)
输出:  extensions/public-apis-cn-promo/data/{categories,services,meta}.json

抽取规则(public-apis-cn 是 markdown 表格,与 free-for-dev 的 `* [Name](url)` 列表不同):
  · ### 中文名         → 一级分类(categories.json)
  · 表格分隔行 `|:---|:---|...`     跳过
  · 表格 header 行 `| API | 描述 | 认证方式 | 支持HTTPS |`   跳过(注意有的有 5 列带「跨域CORS」)
  · 数据行 `| [Name](url) | desc | auth | https | [cors] |`     进 services.json
  · 「**[⬆ 返回目录]**」/「**[⬆ Back to ...]**」行作为分类结束符
  · 分类前的反引号段 `\\`\\`描述\\`\\`` 仅 metadata 记 cat_intro,不进 desc

字段映射(中文 desc + emoji 整段保留):
  - 是 / 否 / apiKey / OAuth / 无 / User-Agent / Unknown ... 都保留原值
  - level 统一 1(public-apis-cn 没有 nested 条目)

运行:
    curl -sL https://raw.githubusercontent.com/llf007/public-apis-cn/master/README.md \\
        -o scripts/_cache/public-apis-cn-README.md
    python scripts/extract_public_apis_cn.py
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "scripts" / "_cache" / "public-apis-cn-README.md"
DATA_DIR = ROOT / "extensions" / "public-apis-cn-promo" / "data"
SOURCE_URL = "https://github.com/llf007/public-apis-cn"

CAT_RE      = re.compile(r"^### (.+?)\s*$")
BACK_RE     = re.compile(r"^\*\*\[⬆")
HEADER_RE   = re.compile(r"^\|\s*(API|名称|Name)\s*\|", re.IGNORECASE)
SEPARATOR_RE = re.compile(r"^\|[:\s|?-]+\|?\s*$")
DATA_RE     = re.compile(
    r"^\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|"   # | [Name](url) |
    r"\s*(.*?)\s*\|"                         # desc |
    r"\s*(.*?)\s*\|"                         # auth |
    r"\s*(.*?)\s*(?:\|\s*(.*?)\s*)?\|?\s*$"  # https | [cors] (optional 5th)
)
INTRO_RE    = re.compile(r"^`([^`]+)`\s*$")


def slugify(name: str) -> str:
    """中文名直接当 slug(UI 用),不做 pinyin 转写。

    例: 'AI模型' → 'AI模型'
        '测试数据' → '测试数据'
        'URL 缩短服务' → 'URL 缩短服务'
    UI 上中文分类名直接显示,无副作用。
    """
    return name.strip()


def _clean_cell(cell: str) -> str:
    """strip 前后空格 + 反引号 + 句号"""
    s = (cell or "").strip()
    s = s.strip("`").strip()
    return s


def extract() -> tuple[list[dict], list[dict], dict]:
    text = README.read_text(encoding="utf-8")

    categories: list[dict] = []
    services:   list[dict] = []
    cat_intros: dict[str, str] = {}

    cur_cat: str | None = None
    cur_intro_buf: list[str] = []

    def flush_cat() -> None:
        """分类结束(遇到 ### 新分类 / Back 链接 / 文件末)— 把累计的 services 计入 cat 元数据"""
        nonlocal cur_cat, cur_intro_buf
        if cur_cat is None:
            return
        n = sum(1 for s in services if s["cat"] == cur_cat)
        categories.append({
            "name":    cur_cat,
            "slug":    slugify(cur_cat),
            "n_items": n,
        })
        if cur_intro_buf:
            cat_intros[cur_cat] = " ".join(cur_intro_buf).strip()
        cur_intro_buf = []

    for line in text.splitlines():
        # ── H3 分类标题 ─────────────────────────────────────
        if (m := CAT_RE.match(line)):
            # 结束上一个分类
            flush_cat()
            cur_cat = m.group(1).strip()
            continue

        # ── 在第一个分类之前(TOC 之前)整段跳过 ─────────────
        if cur_cat is None:
            continue

        # ── Back 链接 / H3 后续 → 分类结束符 ───────────────
        if BACK_RE.match(line):
            flush_cat()
            cur_cat = None  # 之后的行不入数据
            continue

        # ── 表格 header / 分隔 ───────────────────────────────
        if HEADER_RE.match(line) or SEPARATOR_RE.match(line):
            continue

        # ── 表格数据行 ─────────────────────────────────────
        if (m := DATA_RE.match(line)):
            name, url, desc, auth, https = (
                m.group(1).strip(),
                m.group(2).strip(),
                _clean_cell(m.group(3)),
                _clean_cell(m.group(4)),
                _clean_cell(m.group(5) or "未知"),
            )
            services.append({
                "cat":    cur_cat,
                "name":   name,
                "url":    url,
                "desc":   desc,
                "auth":   auth or "未知",
                "https":  https or "未知",
                "level":  1,
            })
            continue

        # ── 反引号分类简介 — 累积到 cat_intros(不进 desc) ────
        if (m := INTRO_RE.match(line)):
            cur_intro_buf.append(m.group(1).strip())
            continue

    # 文件末 flush(最后一个分类可能没 Back 链接)
    flush_cat()

    meta = {
        "source":         SOURCE_URL,
        "snapshot_date":  datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_categories": len(categories),
        "total_services":   len(services),
        "license_note":     "Data 来源 llf007/public-apis-cn 公共协作(基于 public-apis 翻译 + 国内补充),MIT-style 公共贡献",
        "agents_note":      "纯本地只读快照,绝不发起 PR/issue/comment;AGENTS.md 未声明禁止但仍按只读保守处理",
        "schema_note":      "每条 service 含 cat/name/url/desc/auth/https/level;中文 desc 整段保留(emoji + 中文标点)",
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

    print(f"[extract] categories={len(cats)} services={len(svcs)}")
    print(f"[extract] → {DATA_DIR}")
    for c in cats[:3]:
        print(f"  - {c['name']} (slug={c['slug']}) → {c['n_items']} items")
    print(f"[extract] snapshot={meta['snapshot_date']}")


if __name__ == "__main__":
    main()
