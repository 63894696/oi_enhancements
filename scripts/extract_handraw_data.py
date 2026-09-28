"""
extract_handraw_data.py
从 yang0/handraw-style 的 markdown 抽取 278 风格 / 36 颜色 / 120 版式为 JSON。
数据基于 styles_200_reorganized.md + COLORS.md + LAYOUTS.md,字段尽量精简。

输出 3 个 JSON:
  styles.json     — 278 条,字段: n, ref_author, name_zh, traits_zh
  colors.json     — 36 条,  字段: n, name_zh, name_en, vibe_zh, prompt_zh
  layouts.json    — 120 条, 字段: n, cat, name_zh, prompt_zh

为什么这样抽:
- 不嵌图片路径(原始仓库图几百 MB,PrisirAI 自己挑参考)
- 核心字段就够扩展拼接中英 prompt;eng 字段从英文文件 parse 时补
- 用 1 个文件路径而不是网络拉,断网可跑;CI 缓存也行
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ── paths ────────────────────────────────────────────────────────────
SRC = Path(r"C:\Users\Administrator\oi_enhancements\ext-src\handraw")
OUT = Path(r"C:\Users\Administrator\oi_enhancements\extensions\handraw-style-prompter\data")
OUT.mkdir(parents=True, exist_ok=True)


# ── 278 风格:styles_200_reorganized.md ────────────────────────────────
# 表格行格式:| 001 · Gemma Correll | Playful Deadpan Doodle | 特征... |
STYLE_ROW_RE = re.compile(
    r"^\|\s*(?P<n>\d{3})\s*·\s*(?P<ref>[^|]+?)\s*\|\s*"
    r"(?P<name>[^|]+?)\s*\|\s*"
    r"(?P<traits>[^|]*?)\s*\|\s*$"
)


def parse_styles() -> list[dict]:
    src = (SRC / "styles_200_reorganized.md").read_text(encoding="utf-8")
    rows: list[dict] = []
    for line in src.splitlines():
        m = STYLE_ROW_RE.match(line.strip())
        if not m:
            continue
        traits = m.group("traits").strip()
        # 055 行 traits 为空 → 仍记录,字段留空便于 Phase B 兜底
        rows.append({
            "n": int(m.group("n")),
            "ref_author": m.group("ref").strip(),
            "name_zh": m.group("name").strip(),
            "traits_zh": traits,
        })
    return rows


# ── 36 颜色:COLORS.md ────────────────────────────────────────────────
# 单条结构(在 HTML img 内 + <small> + <details>):
#   <img ... alt='C-01 克莱因蓝'><br>**C-01** · 克莱因蓝<br><small>纯粹之蓝 通向无限</small>
#   <br><details><summary>查看色彩提示词</summary><br>`主题色：克莱因蓝（Klein Blue）。`</details>
COLOR_IMG_RE = re.compile(
    r"<img[^>]*alt='(?P<n>C-\d{2})\s+(?P<zh>[^']+?)'[^>]*>"
    r"<br>\*\*(?P=n)\*\*\s*·\s*(?P<zh2>[^<]+?)<br>"
    r"<small>(?P<vibe>[^<]+)</small>"
    r"<br><details>"
)
COLOR_DETAILS_RE = re.compile(
    r"<summary>查看色彩提示词</summary><br>`(?P<prompt>[^`]+)`</details>"
)


def parse_colors() -> list[dict]:
    src = (SRC / "COLORS.md").read_text(encoding="utf-8")
    rows: list[dict] = []
    # 先按 IMG 块定位,再在它之后取最近的 details
    for m in COLOR_IMG_RE.finditer(src):
        n = m.group("n")
        zh = m.group("zh").strip() or m.group("zh2").strip()
        vibe = m.group("vibe").strip()
        # 在 m.end() 之后找下一个 details
        rest = src[m.end():m.end() + 600]
        dm = COLOR_DETAILS_RE.search(rest)
        prompt_zh = dm.group("prompt").strip() if dm else ""
        # 提取英文名:从 prompt_zh 里抠括号内,例如 "克莱因蓝（Klein Blue）"
        en_m = re.search(r"[（(]([^）)]+)[）)]", prompt_zh)
        name_en = en_m.group(1).strip() if en_m else ""
        rows.append({
            "n": n,
            "name_zh": zh,
            "name_en": name_en,
            "vibe_zh": vibe,
            "prompt_zh": prompt_zh,
        })
    return rows


# ── 120 版式:LAYOUTS.md ──────────────────────────────────────────────
# 单条结构类似 colors,但 prompt 在 <details> 里更长,且带"漫画分镜。排版:..." 开头
LAYOUT_IMG_RE = re.compile(
    r"<img[^>]*alt='(?P<n>(?:SC|IG|SB)-\d{3})\s+(?P<zh>[^']+?)'[^>]*>"
)
LAYOUT_DETAILS_RE = re.compile(
    r"<summary>查看排版提示词</summary><br>(?P<prompt>[^<]+?)</details>",
    re.DOTALL,
)


def parse_layouts() -> list[dict]:
    src = (SRC / "LAYOUTS.md").read_text(encoding="utf-8")
    rows: list[dict] = []
    for m in LAYOUT_IMG_RE.finditer(src):
        n = m.group("n")
        cat = n.split("-")[0]  # SC / IG / SB
        zh = m.group("zh").strip()
        rest = src[m.end():m.end() + 800]
        dm = LAYOUT_DETAILS_RE.search(rest)
        prompt_zh = dm.group("prompt").strip() if dm else ""
        rows.append({
            "n": n,
            "cat": cat,
            "name_zh": zh,
            "prompt_zh": prompt_zh,
        })
    return rows


def main() -> int:
    styles = parse_styles()
    colors = parse_colors()
    layouts = parse_layouts()
    (OUT / "styles.json").write_text(
        json.dumps(styles, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "colors.json").write_text(
        json.dumps(colors, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "layouts.json").write_text(
        json.dumps(layouts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"styles={len(styles)} colors={len(colors)} layouts={len(layouts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())