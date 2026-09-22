# -*- coding: utf-8 -*-
r"""
prisIrai_config.py — PrisirAI 三端配置加载器 (2026-09-22, P2.5+15)

目的
====
把用户可改的运行参数集中到 `prisIrai_config.yaml`(同级目录或 $INSTDIR)。
三端(Electron 壳 / Tauri 壳 / Python 后端)共用同一份 schema。

优先级
======
CLI / env > YAML > 模块内置默认。
本模块只暴露读取,不改 yaml。

约束
====
- 无 PyYAML 依赖(用内置正则极简解析;YAML 子集足够我们用)。
- 任何读取失败一律降级到内置默认,绝不抛(生产环境零信任加载)。
- 装包后路径候选:① 同目录(__file__ 同级) ② $INSTDIR ③ cwd。

字段
====
- ports.web / companion / music / calendar  (int)
- brand.url / max_per_run / interval_sec / seen_cap
- forum.url / board / hint
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional


# 模块内置默认(本 yaml 没找到任何字段时兜底)
_DEFAULTS = {
    "ports.web": 18802,
    "ports.companion": 18850,
    "ports.music": 0,
    "ports.calendar": 18803,
    "brand.url": "https://www.babelspan.com/updates.json",
    "brand.max_per_run": 3,
    "brand.interval_sec": 86400,
    "brand.seen_cap": 100,
    "forum.url": "https://bbs.babelspan.com/forum.html",
    "forum.board": "browser/shell",
    "forum.hint": "prisirai",
}


# 候选路径:① __file__ 同级 ② $INSTDIR ③ cwd
def _yaml_candidates() -> list:
    here = Path(__file__).resolve().parent
    cands = [here / "prisIrai_config.yaml"]
    inst = os.environ.get("INSTDIR")
    if inst:
        cands.append(Path(inst) / "prisIrai_config.yaml")
    cands.append(Path.cwd() / "prisIrai_config.yaml")
    return cands


# 极简 YAML 子集解析(只支持 key: value / 嵌套缩进 2 空格 / 注释以 # 起)
# 不用 PyYAML = 零外部依赖 + 装包器不必打 PyYAML。
_VAL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.\-]*)\s*:\s*(.+?)\s*(?:#.*)?$")
_INDENT_RE = re.compile(r"^( {2}|\t)?(\S.*)$")


def _parse_yaml(path: Path) -> dict:
    """返回扁平 dict {full.key: raw_str};解析失败 → 空 dict。"""
    out: dict = {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return out
    sections = {}  # 当前缩进段(2 空格一节)
    for line in text.splitlines():
        s = line.rstrip()
        # 注释 / 空行跳过
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        # 段头(无值的 key:):
        m_sec = re.match(r"^([A-Za-z_][A-Za-z0-9_.\-]*)\s*:\s*(?:#.*)?$", s)
        if m_sec and s.endswith(":") is False and ":" in s:
            pass  # fallthrough to value parse
        if re.match(r"^[A-Za-z_][A-Za-z0-9_.\-]*\s*:\s*$", s):
            sec = re.match(r"^([A-Za-z_][A-Za-z0-9_.\-]*)\s*:\s*$", s).group(1)
            sections[sec] = ""
            continue
        # 节内字段(2 空格缩进):
        m = re.match(r"^  ([A-Za-z_][A-Za-z0-9_.\-]*)\s*:\s*(.+?)\s*(?:#.*)?$", s)
        if m:
            sec = list(sections.keys())[-1] if sections else ""
            if sec:
                out[f"{sec}.{m.group(1)}"] = m.group(2)
            continue
    return out


def _coerce(raw: Optional[str], default):
    if raw is None:
        return default
    s = str(raw).strip()
    # 去掉引号
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1]
    # 数字
    if isinstance(default, int):
        try:
            return int(s)
        except (ValueError, TypeError):
            return default
    return s


def _load_flat() -> dict:
    """读 + 解析 + 合并默认。失败全部降级。"""
    flat: dict = {}
    for p in _yaml_candidates():
        if p.exists():
            try:
                parsed = _parse_yaml(p)
                if parsed:
                    flat.update(parsed)
                    break
            except Exception:
                continue
    return flat


_FLAT_CACHE: Optional[dict] = None


def _get_flat() -> dict:
    """带缓存(进程级一次解析,YAML 改了需重启)。"""
    global _FLAT_CACHE
    if _FLAT_CACHE is None:
        _FLAT_CACHE = _load_flat()
    return _FLAT_CACHE


def get(key: str, default=None):
    """读字段。优先 yaml 解析,再内置默认。"""
    flat = _get_flat()
    raw = flat.get(key)
    if raw is None:
        return _DEFAULTS.get(key, default)
    return _coerce(raw, _DEFAULTS.get(key, default))


# ---------- 公共快捷 getter ----------
def web_port_default() -> int:
    return int(get("ports.web", 18802))

def companion_port_default() -> int:
    return int(get("ports.companion", 18850))

def music_port_default() -> int:
    return int(get("ports.music", 0))

def calendar_port_default() -> int:
    return int(get("ports.calendar", 18803))

def brand_url() -> str:
    return str(get("brand.url", "https://www.babelspan.com/updates.json"))

def brand_max_per_run() -> int:
    return int(get("brand.max_per_run", 3))

def brand_interval_sec() -> int:
    return int(get("brand.interval_sec", 86400))

def brand_seen_cap() -> int:
    return int(get("brand.seen_cap", 100))

def forum_url() -> str:
    return str(get("forum.url", "https://bbs.babelspan.com/forum.html"))

def forum_board() -> str:
    return str(get("forum.board", "browser/shell"))

def forum_hint() -> str:
    return str(get("forum.hint", "prisirai"))


def quick_smoke() -> dict:
    return {
        "yaml_found": any(p.exists() for p in _yaml_candidates()),
        "yaml_path": next((str(p) for p in _yaml_candidates() if p.exists()), None),
        "ports.web": web_port_default(),
        "ports.companion": companion_port_default(),
        "ports.music": music_port_default(),
        "ports.calendar": calendar_port_default(),
        "brand.url": brand_url(),
        "brand.max_per_run": brand_max_per_run(),
        "brand.interval_sec": brand_interval_sec(),
        "forum.url": forum_url(),
        "forum.board": forum_board(),
        "forum.hint": forum_hint(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(quick_smoke(), ensure_ascii=False, indent=2))
