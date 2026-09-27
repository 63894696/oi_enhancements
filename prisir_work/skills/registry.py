"""
prisIr_work/skills/registry.py — Skills 工作台索引生成器(Phase 1, 2026-09-27)。

定位:把 capability._REGISTRY 自动转成 JSON 结构化 skill 索引,
     跟 capability.py 是镜像而非重复。

核心 API:
  describe_registry()    → 返 {"schema_version", "skills": [...]} — 极简索引 JSON
  describe_skill(slug)   → 返 SkillDescribe — 完整 schema(Lazy 加载)
  list_skills()          → 返所有 SkillIndex 列表
  search_skills(query)   → 按 name/id/tags 搜
  count_skills()         → 数字

实现要点:
  · **零复制** — 不维护第二份能力表,所有数据从 capability._REGISTRY 读
  · **emoji 派生** — 不在 capability.py 存 emoji;按风险等级 / 命名空间启发式派生
  · **fail-soft** — 任何异常返 None 或 [];绝不抛栈
  · **可缓存** — describe_registry() 走 LRU 缓存,companion 启动一次 OK
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .schema import (
    SCHEMA_VERSION,
    SkillArg,
    SkillDescribe,
    SkillIndex,
)

log = logging.getLogger("prisir_work.skills.registry")

# ---------------------------------------------------------------------------
# emoji 派生规则(不存 emoji 字段,按命名空间启发式)
# ---------------------------------------------------------------------------

_NS_EMOJI: dict[str, str] = {
    "poster": "🎨",
    "image-gen": "🖼️",
    "video": "🎬",
    "youtube": "📺",
    "publish": "📤",
    "web.search": "🔍",
    "web.fetch": "🌐",
    "web.research": "📚",
    "web.extract": "🧬",
    "web.find_similar": "🔗",
    "web.feedparser": "📡",
    "web.ytdlp": "🎞️",
    "web.tune": "⚙️",
    "web.reach": "🌍",
    "web.jina": "📖",
    "web.gh": "🐙",
    "web.exa": "✨",
    "web.hn": "🟠",
    "web.playwright": "🎭",
    "web.agent-browser": "🦾",
    "web.screenshot": "📸",
    "free": "🆓",
    "agency": "👤",
}


def _emoji_for(skill_id: str, risk: str) -> str:
    """按命名空间 + 风险等级启发式取 emoji。fallback 看 risk。"""
    if "." in skill_id:
        ns = ".".join(skill_id.split(".")[:2])
        if ns in _NS_EMOJI:
            return _NS_EMOJI[ns]
        # 二级 namespace
        head = skill_id.split(".")[0]
        if head in _NS_EMOJI:
            return _NS_EMOJI[head]
    risk_emoji = {"L0": "🟢", "L1": "🟡", "L2": "🟠", "L3": "🔴"}
    return risk_emoji.get(risk, "⚪")


# ---------------------------------------------------------------------------
# tags 派生 — keywords 直接复用,补 domain tag
# ---------------------------------------------------------------------------

def _tags_for(cap_id: str, keywords: tuple[str, ...]) -> list[str]:
    """从 capability keywords 抽 tags,去重 + 长度过滤(<= 6 个)。"""
    seen: set[str] = set()
    out: list[str] = []
    # 第一段 id 也算 tag(e.g. "video" / "web" / "free")
    domain = cap_id.split(".")[0]
    if domain and domain not in seen:
        seen.add(domain)
        out.append(domain)
    for kw in keywords:
        kw_clean = kw.strip().lower()
        if not kw_clean or len(kw_clean) > 16 or kw_clean in seen:
            continue
        seen.add(kw_clean)
        out.append(kw_clean)
        if len(out) >= 6:
            break
    return out


# ---------------------------------------------------------------------------
# index 缓存
# ---------------------------------------------------------------------------

_INDEX_CACHE: list[SkillIndex] | None = None


def _build_index() -> list[SkillIndex]:
    """从 capability._REGISTRY 构建 SkillIndex 列表(全量)。

    任何坏 entry 都跳过 + log warning,绝不抛栈。
    """
    global _INDEX_CACHE
    if _INDEX_CACHE is not None:
        return _INDEX_CACHE
    try:
        from .. import capability as _cap  # 顶层相对导入
    except Exception as exc:  # noqa: BLE001
        log.warning("skills.registry: capability import failed: %s", exc)
        return []

    out: list[SkillIndex] = []
    for entry in _cap.list_capabilities():
        try:
            cap_id = entry.get("id")
            if not cap_id:
                continue
            name = entry.get("title", cap_id)
            # title 太长就截(索引要小)
            if len(name) > 80:
                name = name[:77] + "..."
            risk = entry.get("risk", "L0")
            keywords = tuple(entry.get("keywords", []) or [])
            out.append(SkillIndex(
                id=cap_id,
                name=name,
                emoji=_emoji_for(cap_id, risk),
                risk=risk,
                tags=_tags_for(cap_id, keywords),
                backend="builtin",
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("skills.registry: skip bad entry %s: %s",
                        entry.get("id"), exc)
    out.sort(key=lambda x: x.id)
    _INDEX_CACHE = out
    return out


def invalidate_cache() -> None:
    """清索引缓存(capability 动态注册后调)。"""
    global _INDEX_CACHE
    _INDEX_CACHE = None


# ---------------------------------------------------------------------------
# 顶层 API
# ---------------------------------------------------------------------------

def count_skills() -> int:
    return len(_build_index())


def list_skills() -> list[SkillIndex]:
    return list(_build_index())


def search_skills(query: str) -> list[SkillIndex]:
    """按 id / name / tags 子串搜(大小写不敏感)。空 query 返全量。"""
    q = (query or "").strip().lower()
    out: list[SkillIndex] = []
    for idx in _build_index():
        if not q:
            hit = True
        else:
            hay = " ".join([idx.id, idx.name, *idx.tags]).lower()
            hit = q in hay
        if hit:
            out.append(idx)
    return out


def describe_registry() -> dict[str, Any]:
    """返 JSON 结构化索引(直接给 system prompt 用)。

    包含 schema_version / 总数 / skills list。失败/空 → 仅 schema_version。
    """
    skills = _build_index()
    return {
        "schema_version": SCHEMA_VERSION,
        "total": len(skills),
        "skills": [s.to_dict() for s in skills],
    }


def describe_registry_compact() -> str:
    """把索引序列化成单行紧凑 JSON 字符串(给 system prompt 直接拼)。

    这是用户拍板的「JSON 结构化,可被 LLM 程序化解析」形式。
    """
    return json.dumps(describe_registry(), ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# describe_skill — lazy 加载完整 schema
# ---------------------------------------------------------------------------

# 简易 args 推导:从 capability keywords + endpoint 路径启发
# 不读 endpoint handler body(避免引入 endpoint 依赖)
_KV_PARAM_RE = re.compile(r"\{([a-z_]+)\}")


def _args_for(cap_id: str, keywords: tuple[str, ...]) -> list[SkillArg]:
    """启发式推 args schema。

    主要 skill 类别启发:
      - video.* / image-gen.* / poster.* / publish.* / agency.* — 业务参数
      - web.* — 通常有 query/url/limit
    """
    out: list[SkillArg] = []
    # 通用:大多数 skill 接受 query 类参数
    head = cap_id.split(".")[0]

    if head == "web":
        # 大部分 web.* 接受 query 或 url
        if "search" in cap_id or "research" in cap_id or "extract" in cap_id or "tune" in cap_id:
            out.append(SkillArg(
                name="query", type="string", required=True,
                description="搜索/研究/抽取关键词",
            ))
        if "fetch" in cap_id or "jina" in cap_id:
            out.append(SkillArg(
                name="url", type="string", required=True,
                description="要抓取的 URL",
            ))
        if "feedparser" in cap_id or "ytdlp" in cap_id:
            out.append(SkillArg(
                name="url", type="string", required=True,
                description="feed / 视频 URL",
            ))
        if "screenshot" in cap_id and cap_id.endswith("capture"):
            out.append(SkillArg(name="mode", type="string", required=False,
                                default="fullscreen",
                                enum=["fullscreen", "window", "area"],
                                description="截图模式"))
        if "playwright" in cap_id or "agent-browser" in cap_id:
            if "click" in cap_id or "type" in cap_id or "fill" in cap_id:
                out.append(SkillArg(name="ref", type="string", required=True,
                                    description="a11y 树 @ref 或 selector"))
            if cap_id.endswith("type") or cap_id.endswith("fill"):
                out.append(SkillArg(name="text", type="string", required=True,
                                    description="要输入的文本"))
            if cap_id.endswith("navigate") or cap_id.endswith("open"):
                out.append(SkillArg(name="url", type="string", required=True,
                                    description="目标 URL"))
    elif head == "video":
        if cap_id.endswith(".info") or cap_id.endswith(".analyze"):
            out.append(SkillArg(name="path", type="string", required=False,
                                description="本地视频路径或 URL"))
        if cap_id.endswith(".tts"):
            out.append(SkillArg(name="text", type="string", required=True,
                                description="要转语音的文字"))
            out.append(SkillArg(name="voice", type="string", required=False,
                                description="声音 ID(默认 zh-CN-XiaoxiaoNeural)"))
        if cap_id.endswith(".asr") or cap_id.endswith(".burn"):
            out.append(SkillArg(name="path", type="string", required=True,
                                description="视频/音频/字幕文件路径"))
        if cap_id.endswith(".cut"):
            out.append(SkillArg(name="path", type="string", required=True,
                                description="源视频路径"))
            out.append(SkillArg(name="start", type="string", required=True,
                                description="起始时间 HH:MM:SS 或秒"))
            out.append(SkillArg(name="end", type="string", required=True,
                                description="结束时间 HH:MM:SS 或秒"))
        if cap_id.endswith(".bgm"):
            out.append(SkillArg(name="path", type="string", required=True,
                                description="视频路径"))
            out.append(SkillArg(name="music", type="string", required=True,
                                description="背景音乐路径"))
        if cap_id.endswith(".orchestrate") or cap_id.endswith(".create"):
            out.append(SkillArg(name="topic", type="string", required=True,
                                description="视频主题"))
            out.append(SkillArg(name="script", type="string", required=False,
                                description="视频文案(可选)"))
    elif head == "youtube":
        if cap_id.endswith(".upload"):
            out.append(SkillArg(name="path", type="string", required=True,
                                description="本地视频路径"))
            out.append(SkillArg(name="title", type="string", required=True,
                                description="视频标题"))
            out.append(SkillArg(name="exec_real", type="string", required=False,
                                default="false",
                                description="true = 真上传,false = 校验"))
    elif head == "publish":
        if cap_id.endswith(".html"):
            out.append(SkillArg(name="title", type="string", required=True,
                                description="文章标题"))
            out.append(SkillArg(name="html", type="string", required=True,
                                description="HTML 草稿"))
            out.append(SkillArg(name="platform", type="string", required=False,
                                default="wechat-oa",
                                description="目标平台 ID"))
    elif head in ("poster", "image-gen", "free", "agency"):
        # 通用 query / slug 形式
        if "search" in cap_id or "find" in cap_id:
            out.append(SkillArg(name="query", type="string", required=True,
                                description="搜索关键词"))
        if "get" in cap_id or "detail" in cap_id or "apply" in cap_id:
            out.append(SkillArg(name="slug", type="string", required=True,
                                description="条目 slug"))
        if "list" in cap_id or "list_categories" in cap_id:
            out.append(SkillArg(name="div", type="string", required=False,
                                description="agency 角色分类(可选)"))
        if cap_id.endswith(".random"):
            out.append(SkillArg(name="category", type="string", required=False,
                                description="分类过滤(可选)"))

    return out


def describe_skill(skill_id: str) -> SkillDescribe | None:
    """读 capability _REGISTRY → 转 SkillDescribe(fail-soft)。"""
    if not skill_id:
        return None
    try:
        from .. import capability as _cap
        entry = _cap.get(skill_id)
        if not entry:
            return None
        # 找 index
        idx = next((s for s in _build_index() if s.id == skill_id), None)
        if idx is None:
            return None
        # examples 从 keywords 选前 2 个做 hint
        kws = tuple(entry.get("keywords", []) or [])
        examples: list[str] = []
        for kw in kws[:2]:
            examples.append(f"用法提示:{kw}")

        return SkillDescribe(
            index=idx,
            title=entry.get("title", skill_id),
            description=entry.get("title", skill_id),  # 暂无更长 desc;后续可加
            args=_args_for(skill_id, kws),
            confirm=entry.get("confirm", ""),
            returns='{"ok": bool, ...}',  # 占位
            examples=examples,
            endpoint=entry.get("endpoint"),
            method=entry.get("method", "POST"),
            ext_id=None,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("describe_skill(%s) failed: %s", skill_id, exc)
        return None


__all__ = [
    "describe_registry",
    "describe_registry_compact",
    "describe_skill",
    "list_skills",
    "search_skills",
    "count_skills",
    "invalidate_cache",
]