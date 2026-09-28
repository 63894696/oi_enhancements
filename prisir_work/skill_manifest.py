"""prisir_work/skill_manifest.py — 移植自 Easel OpenClaw skill 范式。

OpenClaw skill 结构(2026-09-24 抽自 zju_easel/skills/openclaw/skill-wechat-publisher/):
  SKILL.md        — YAML frontmatter (name/description/layer) + markdown body
  EASEL-META.md   — 元数据表(来源仓库 / star / 文件数 / 备注)
  brief.md.example — 用户填的 brief 模板(主题 / 风格 / 账号)
  scripts/        — 真可跑脚本入口
  references/     — 领域知识(瘦身后的 SKILL.md 用 [n] 引用)
  assets/         — 静态资源(主题 / 图片风格 / 排版卡片)
  tests/          — 单元/集成测试

PrisirAI 自家用法(降级版):
  · skill_manifest 包一个轻 SKILL.md frontmatter 解析,够用就好。
  · 不复制整个 OpenClaw gateway(那是 Easel 用的,PrisirAI 有 prisir_work 门面)
  · 第一个落地 skill 是 wechat-publisher(在 prisir_work/publisher.py + easel_bridge.py)。
  · 后续接新 skill(Easel 的 card-design / image-editing / batch-process 等)按同范式扩。

设计:不做完整 OpenClaw runtime clone,只把 manifest 标准化,让 agent/扩展/CLI 用
同一字段发现/描述/触发 skill。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "SkillManifest",
    "parse_skill_md",
    "scan_skills_dir",
    "register_skill",
    "find_skill",
    "list_skills",
]


@dataclass
class SkillManifest:
    """从 SKILL.md frontmatter + body 抽出的标准化字段。"""
    name: str
    description: str = ""
    layer: str = ""          # discover / plan / create / publish / review ...
    triggers: list[str] = field(default_factory=list)  # description 里"触发场景"那行解析
    body_path: str = ""      # SKILL.md 完整路径
    extra: dict[str, Any] = field(default_factory=dict)  # 其它 frontmatter 字段

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "layer": self.layer,
            "triggers": self.triggers,
            "body_path": self.body_path,
            "extra": self.extra,
        }


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)$",
    re.DOTALL,
)


def parse_skill_md(md_path: str | os.PathLike) -> SkillManifest | None:
    """读 SKILL.md 抽 YAML frontmatter + 触发场景。YAML 解析用最简 key:value。

    为不引入 PyYAML 依赖,用极简解析:
      · frontmatter 是 `key: value` 行,value 多行用 | 块
      · 不支持嵌套 / 列表缩写(超过我们 SKILL.md 实际需要)
    """
    p = Path(md_path)
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    fm = m.group("fm")
    body = m.group("body")
    fm_dict: dict[str, str] = {}
    cur_key: str | None = None
    cur_lines: list[str] = []
    for line in fm.splitlines():
        if line.startswith("  ") and cur_key:
            cur_lines.append(line.strip())
            continue
        if cur_key:
            fm_dict[cur_key] = "\n".join(cur_lines).strip() if cur_lines else ""
            cur_key = None
            cur_lines = []
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            cur_key = k.strip()
            cur_lines = [v.strip()] if v.strip() else []
    if cur_key:
        fm_dict[cur_key] = "\n".join(cur_lines).strip()
    name = fm_dict.get("name", p.parent.name)
    description = fm_dict.get("description", "").replace("\n", " ").strip()
    layer = fm_dict.get("layer", "")
    # 触发场景:从 description 里"触发场景"后面抽(逗号/顿号分)
    triggers: list[str] = []
    if "触发场景" in description:
        tail = description.split("触发场景", 1)[-1]
        # 去掉括号 / 引号
        tail = re.sub(r"[()「」:：]", " ", tail)
        for part in re.split(r"[,，、/]", tail):
            part = part.strip().strip("\"'")
            if part and len(part) <= 30:
                triggers.append(part)
    return SkillManifest(
        name=name,
        description=description,
        layer=layer,
        triggers=triggers,
        body_path=str(p.resolve()),
        extra={k: v for k, v in fm_dict.items()
               if k not in ("name", "description", "layer")},
    )


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, SkillManifest] = {}


def register_skill(m: SkillManifest) -> None:
    _REGISTRY[m.name] = m


def find_skill(name: str) -> SkillManifest | None:
    return _REGISTRY.get(name)


def list_skills() -> list[dict[str, Any]]:
    return [m.to_dict() for m in _REGISTRY.values()]


def scan_skills_dir(root: str | os.PathLike, *, pattern: str = "SKILL.md") -> int:
    """扫 root 下所有 SKILL.md,注册进表。返新增条数。"""
    root = Path(root)
    if not root.is_dir():
        return 0
    n = 0
    for md in root.rglob(pattern):
        m = parse_skill_md(md)
        if m:
            register_skill(m)
            n += 1
    return n


# ---------------------------------------------------------------------------
# 默认:扫描 Easel 仓库的 skills/(只读不复制)
# ---------------------------------------------------------------------------

def _register_defaults() -> None:
    """默认扫一遍 ~/work/zju_easel/skills + oi_enhancements/companion + 自家扩展。"""
    candidates = [
        Path("C:/Users/Administrator/work/zju_easel/skills"),
        Path("C:/work/zju_easel/skills"),
        Path.home() / "work" / "zju_easel" / "skills",
        Path.home() / "Easel" / "skills",
        # PrisirAI 自家 skill 目录(若有)
        Path(__file__).resolve().parent.parent / "skills",
    ]
    total = 0
    for c in candidates:
        total += scan_skills_dir(c)
    # 内置一个自家 skill(避免空仓库时啥也没)
    register_skill(SkillManifest(
        name="wechat-oa-publisher",
        description="PrisirAI 自家:发布 HTML 草稿到微信公众号(走 Easel 桥接)。触发场景:发布, 发文, 公众号, 推文, 发到, wechat, mp, 草稿。",
        layer="publish",
        triggers=["发布", "发文", "公众号", "推文", "发到", "wechat",
                  "mp", "草稿", "发草稿", "发到公众号", "微信文章"],
        extra={"version": "0.1.0", "backend": "easel_bridge"},
    ))


_register_defaults()


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="prisirmp-skill-manifest",
                                description="Skill manifest 扫描/查询")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="列出所有已注册 skill")
    p_get = sub.add_parser("get", help="按名查 skill")
    p_get.add_argument("name")
    p_scan = sub.add_parser("scan", help="扫指定目录")
    p_scan.add_argument("root")

    args = p.parse_args(argv)
    if args.cmd == "list":
        for s in list_skills():
            print(f"  - {s['name']:30} layer={s['layer']:10} "
                  f"triggers={len(s['triggers'])}")
    elif args.cmd == "get":
        m = find_skill(args.name)
        if not m:
            print(f"[error] 未找到 skill: {args.name}")
            return 2
        print(json.dumps(m.to_dict(), ensure_ascii=False, indent=2))
    elif args.cmd == "scan":
        n = scan_skills_dir(args.root)
        print(f"扫 {args.root} → 新增 {n} 个 skill")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))