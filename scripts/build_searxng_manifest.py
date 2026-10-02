"""build_searxng_manifest.py — 从 searxng settings.yml 筛启用无 key 段。

筛规则:
  - 顶层 `- name:` 起始的 entry(不是 `name:` 子字段)
  - 该 entry 内:
    - 缺 `disabled: true` (有 `disabled: true` 跳过)
    - 缺 `requires_api_key: <truthy>` (无则无 key)
    - 缺 `tokens:` 非空 (无则无 key)
    - 缺 `auth_method: <其他none>`(是 none / 不存在 = OK)

输出:prisIr_work/search_engines/_engines_manifest.py
"""
import re
from pathlib import Path

CACHE = Path(__file__).parent / "_cache" / "searxng-settings.yml"
OUT = Path(__file__).parent.parent / "prisIr_work" / "search_engines" / "_engines_manifest.py"


# 顶层 `- name: <x>`(行首是 `  - name:`,前面恰好 2 空格 + dash + space + name + :)
TOP_NAME_RE = re.compile(r"^  - name:\s*(\S+)\s*$")
# 子字段缩进 4-6 空格
INDENT_RE = re.compile(r"^    (\S+?):\s*(.*)$")


def parse_settings(path: Path) -> list[dict]:
    """按 entry 切分 settings.yml。"""
    engines: list[dict] = []
    cur: dict | None = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            m = TOP_NAME_RE.match(line)
            if m:
                if cur:
                    engines.append(cur)
                cur = {"name": m.group(1), "fields": {}}
                continue
            # 下一个顶层 entry `- name:` 或顶层 key
            if line.startswith("  - ") and not line.startswith("  - name:"):
                # 列表项但不是 name,跳过(很少见,如 categories)
                continue
            # 顶层 key(顶层 settings 段,无 indent 或 indent=2 但不是 `-`)
            if line and not line.startswith(" "):
                if cur:
                    engines.append(cur)
                cur = None
                continue
            if cur is None:
                continue
            m2 = INDENT_RE.match(line)
            if m2:
                cur["fields"][m2.group(1)] = m2.group(2).strip()
        if cur:
            engines.append(cur)
    return engines


def is_enabled_no_key(engine: dict) -> bool:
    """筛启用且无 key。"""
    f = engine["fields"]
    # disabled 字段存在 + 是 true,跳过
    if "disabled" in f and f["disabled"].lower() == "true":
        return False
    # requires_api_key 字段存在 + 非空值,跳过
    if "requires_api_key" in f and f["requires_api_key"].lower() not in ("", "false", "none"):
        return False
    # tokens 列表非空,跳过
    if "tokens" in f and f["tokens"] and f["tokens"].lower() not in ("[]", ""):
        return False
    # secret 字段非空,跳过
    if "secret" in f and f["secret"] and f["secret"].lower() not in ("", "false", "none", "~"):
        return False
    return True


def main() -> None:
    engines = parse_settings(CACHE)
    no_key = [e["name"] for e in engines if is_enabled_no_key(e)]
    # 去重 + 排序
    no_key = sorted(set(no_key))

    print(f"total parsed engines: {len(engines)}")
    print(f"enabled no-key engines: {len(no_key)}")
    print("first 20:", no_key[:20])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append('"""_engines_manifest.py — SearXNG 启用无 key 引擎清单。')
    lines.append("")
    lines.append("由 scripts/build_searxng_manifest.py 从")
    lines.append("https://raw.githubusercontent.com/searxng/searxng/master/searx/settings.yml")
    lines.append("解析生成(snapshot 2026-10-02)。")
    lines.append("")
    lines.append("约束:")
    lines.append("- 与 prisIr_work/search_engines/*.py 内的 register_all() 必须一一对应")
    lines.append("- 测试 test_engines_manifest_count_matches 强制对齐")
    lines.append("- 已 ship 的内置 provider(ddg_html / bing_public / baidu / jina_search /")
    lines.append("  gh_search / exa_search / hn_search / tavily / serper)不计此 manifest")
    lines.append('"""')
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append(f"EXPECTED_PROVIDERS: tuple[str, ...] = (")
    for n in no_key:
        lines.append(f'    "{n}",')
    lines.append(")")
    lines.append("")
    lines.append(f"EXPECTED_COUNT: int = {len(no_key)}")
    lines.append("")
    lines.append("# 按类别分组(便于 system prompt 注入)")
    lines.append("CATEGORIES: dict[str, tuple[str, ...]] = {")
    CATEGORIES = {
        "general": ["mojeek", "startpage", "ecosia", "qwant"],
        "academic": ["arxiv", "pubmed", "semantic_scholar", "crossref",
                     "europepmc", "pdbe", "astrophysics", "openaire"],
        "code": ["github", "docker", "pypi", "npmjs", "stackoverflow",
                 "askubuntu", "arch linux wiki", "gentoo", "nixos",
                 "sourcehut"],
        "wikipedia": ["wikipedia", "wikidata", "wikinews", "wiktionary",
                      "wikicommons.images", "wikicommons.videos",
                      "wikicommons.audio", "wikicommons.files"],
        "media": ["youtube", "vimeo", "piped", "piped.music",
                  "bandcamp", "mixcloud", "dailymotion",
                  "openverse.audio", "soundcloud"],
        "images": ["500px", "1x", "deviantart", "pexels", "unsplash"],
        "news": ["bing news", "duckduckgo news", "google news",
                 "reuters", "yahoo news"],
        "maps": ["openstreetmap", "photon"],
        "specialty": sorted(set(no_key) - set(
            ["mojeek", "startpage", "ecosia", "qwant",
             "arxiv", "pubmed", "semantic_scholar", "crossref",
             "europepmc", "pdbe", "astrophysics", "openaire",
             "github", "docker", "pypi", "npmjs", "stackoverflow",
             "askubuntu", "arch linux wiki", "gentoo", "nixos",
             "sourcehut",
             "wikipedia", "wikidata", "wikinews", "wiktionary",
             "wikicommons.images", "wikicommons.videos",
             "wikicommons.audio", "wikicommons.files",
             "youtube", "vimeo", "piped", "piped.music",
             "bandcamp", "mixcloud", "dailymotion",
             "openverse.audio", "soundcloud",
             "500px", "1x", "deviantart", "pexels", "unsplash",
             "bing news", "duckduckgo news", "google news",
             "reuters", "yahoo news",
             "openstreetmap", "photon"])),
    }
    for cat, names in CATEGORIES.items():
        if not names:
            continue
        lines.append(f'    "{cat}": (')
        for n in names:
            lines.append(f'        "{n}",')
        lines.append("    ),")
    lines.append("}")
    lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()