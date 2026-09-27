"""
scripts/extract_agency_agents.py — agency-agents Phase A 数据抽取(2026-09-27)。

输入:`scripts/_cache/agency-agents/`(git clone --depth 1 一次性产物)
输出:`extensions/agency-roles/data/{divisions,roles,meta}.json`

设计要点:
  · 只读源仓库(原 MIT 允许,本仓库不 fork,只快照)。
  · 18 division 严格按 divisions.json 名单;strategy/examples/integrations/scripts 跳过。
  · frontmatter 用纯正则切(避免引 python-frontmatter 依赖)。
  · persona_md = frontmatter 之后的整段 body;估算 tokens ≈ chars/4(英文启发)。
  · 中文 agent(persona_md 首 100 字符含 CJK)标记 lang="zh" 以便主对话路由。

用法:
    python scripts/extract_agency_agents.py
    # 或指定路径
    python scripts/extract_agency_agents.py --src scripts/_cache/agency-agents --out extensions/agency-roles/data
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent

# divisions.json _note 明示 NON_DIVISION_DIRS: examples / scripts / integrations
# strategy/ 也是 no-frontmatter(README + nexus-strategy playbook),不进抽取
NON_DIVISION_DIRS = {"examples", "scripts", "integrations", "strategy"}

# 简单 YAML key 正则(只支持纯 scalar 值,够 agency-agents 用)
_FM_KEY_RE = re.compile(r"^([a-z_]+)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_FM_BODY_SEP_RE = re.compile(r"^---\s*$", re.MULTILINE)


def parse_frontmatter(md_text: str) -> tuple[dict[str, str], str]:
    """切 frontmatter + body。无 frontmatter 时返 ({}, 全文)。"""
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, md_text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, md_text

    fm: dict[str, str] = {}
    for ln in lines[1:end]:
        ln = ln.rstrip()
        if not ln or ln.startswith("#"):
            continue
        m = _FM_KEY_RE.match(ln)
        if m:
            key, val = m.group(1).lower(), m.group(2).strip()
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            fm[key] = val
    body = "\n".join(lines[end + 1 :]).strip()
    return fm, body


def estimate_tokens(body: str) -> int:
    """粗估 token 数:英文 chars/4,CJK chars/1.5(经验值)。"""
    cjk = sum(1 for c in body if "一" <= c <= "鿿")
    other = len(body) - cjk
    return max(1, int(cjk / 1.5 + other / 4))


def detect_lang(body: str) -> str:
    head = body[:600]
    cjk = sum(1 for c in head if "一" <= c <= "鿿")
    return "zh" if cjk > 30 else "en"


def derive_tags(fm: dict[str, str], body: str, max_tags: int = 6) -> list[str]:
    """从前 200 字描述 + vibe 抽 5-6 个英文 tag(去 stop word)。"""
    stop = {
        "a", "an", "and", "or", "the", "of", "in", "to", "for", "with", "is",
        "are", "be", "you", "your", "we", "our", "this", "that", "as", "on",
        "by", "from", "it", "at", "all", "any", "but", "not",
    }
    raw = (fm.get("description", "") + " " + fm.get("vibe", "")).lower()
    raw = re.sub(r"[^a-z0-9\s-]", " ", raw)
    tokens: list[str] = []
    seen: set[str] = set()
    for tok in raw.split():
        tok = tok.strip("-")
        if len(tok) < 3 or tok in stop or tok in seen:
            continue
        seen.add(tok)
        tokens.append(tok)
        if len(tokens) >= max_tags:
            break
    return tokens


def iter_agent_files(repo: Path) -> Iterable[tuple[Path, str]]:
    """(file_path, division_name) 迭代器。"""
    for div_dir in sorted(repo.iterdir()):
        if not div_dir.is_dir() or div_dir.name in NON_DIVISION_DIRS:
            continue
        if div_dir.name.startswith("."):
            continue
        for md in sorted(div_dir.glob("*.md")):
            yield md, div_dir.name


def extract_roles(repo: Path) -> list[dict]:
    roles: list[dict] = []
    skipped: list[str] = []
    for md, div in iter_agent_files(repo):
        try:
            text = md.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{md}: {exc}")
            continue
        fm, body = parse_frontmatter(text)
        if not fm or len(body) < 100:
            skipped.append(f"{md}: empty frontmatter or body<100 chars")
            continue
        slug = md.stem
        lang = detect_lang(body)
        tokens = estimate_tokens(body)
        tags = derive_tags(fm, body)
        roles.append({
            "div": div,
            "slug": slug,
            "name": fm.get("name", slug),
            "emoji": fm.get("emoji", ""),
            "color": fm.get("color", ""),
            "description": fm.get("description", ""),
            "vibe": fm.get("vibe", ""),
            "lang": lang,
            "tags": tags,
            "persona_md": body,
            "persona_tokens_est": tokens,
            "persona_chars": len(body),
            "source_path": f"{div}/{md.name}",
        })
    if skipped:
        print(f"⚠ skipped {len(skipped)} files:")
        for s in skipped[:10]:
            print(f"  - {s}")
        if len(skipped) > 10:
            print(f"  ... and {len(skipped) - 10} more")
    return roles


def load_divisions(repo: Path) -> list[dict]:
    src = json.loads((repo / "divisions.json").read_text(encoding="utf-8"))
    out: list[dict] = []
    for name, info in src["divisions"].items():
        if name in NON_DIVISION_DIRS:
            continue
        out.append({
            "name": name,
            "label": info["label"],
            "icon": info["icon"],
            "color": info["color"],
        })
    return out


def write_outputs(repo: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    divisions = load_divisions(repo)
    roles = extract_roles(repo)
    by_div: dict[str, int] = {}
    for r in roles:
        by_div[r["div"]] = by_div.get(r["div"], 0) + 1
    for d in divisions:
        d["n_roles"] = by_div.get(d["name"], 0)

    zh = sum(1 for r in roles if r["lang"] == "zh")
    en = len(roles) - zh

    meta = {
        "source": "https://github.com/msitarzewski/agency-agents",
        "snapshot_date": _dt.date.today().isoformat(),
        "snapshot_iso": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "total_divisions": len(divisions),
        "total_roles": len(roles),
        "roles_by_division": by_div,
        "lang_split": {"en": en, "zh": zh},
        "snapshot_method": "git clone --depth 1",
        "license": "MIT",
        "extension_fork": False,
        "sync_command": "git -C scripts/_cache/agency-agents pull --ff-only",
        "non_division_dirs": sorted(NON_DIVISION_DIRS),
        "extraction_script": "scripts/extract_agency_agents.py",
    }

    (out_dir / "divisions.json").write_text(
        json.dumps({"divisions": divisions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "roles.json").write_text(
        json.dumps({"roles": roles}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta


def sanity_check(meta: dict) -> list[str]:
    issues: list[str] = []
    if meta["total_divisions"] != 18:
        issues.append(f"divisions={meta['total_divisions']}, expected 18")
    if meta["total_roles"] < 200:
        issues.append(f"roles={meta['total_roles']}, expected >= 200")
    for d, n in meta["roles_by_division"].items():
        if n < 1:
            issues.append(f"division {d} has 0 roles")
    return issues


def main() -> int:
    ap = argparse.ArgumentParser(description="agency-agents → JSON 抽取")
    ap.add_argument("--src", type=Path,
                    default=ROOT / "scripts" / "_cache" / "agency-agents",
                    help="git clone 出来的源目录")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "extensions" / "agency-roles" / "data",
                    help="输出目录")
    args = ap.parse_args()

    if not args.src.exists():
        print(f"✗ source not found: {args.src}", file=sys.stderr)
        print(f"  请先: git clone --depth 1 https://github.com/msitarzewski/agency-agents.git {args.src}",
              file=sys.stderr)
        return 1

    print(f"→ 抽取 {args.src} → {args.out}")
    meta = write_outputs(args.src, args.out)
    issues = sanity_check(meta)
    print(f"✓ divisions={meta['total_divisions']}  roles={meta['total_roles']}  "
          f"en={meta['lang_split']['en']} zh={meta['lang_split']['zh']}")
    if issues:
        print("⚠ sanity issues:")
        for i in issues:
            print(f"  - {i}")
        return 2
    print(f"✓ sanity check 通过")
    print(f"✓ 写入: divisions.json / roles.json / meta.json → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())