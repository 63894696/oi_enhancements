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
# tier 启发式 — Phase 8(2026-09-28)
#
# 用户决策:"长尾低频但关键的技能不能被频次去重干掉"(如季度报告 / 年度报税 /
# 平台 publish 等)。tier 字段只做**分层标记**,不做删除;未来真有压缩需求
# 时可按 tier 分层注入(hot 始终在, warm 默认在, cold 按需注入)。
#
# 启发规则(只覆盖命名空间 + 风险模式,具体 capability 可在 capability.py
# override `_tier` 字段):
#   · **hot** — 日常高频(查询类、信息类、生成类)
#   · **warm** — 常规(月/周频次的写操作、平台 publish、文件操作)
#   · **cold** — 低频但关键(年度/按需、审计、合规、报告)
# ---------------------------------------------------------------------------

# 命名空间启发 → tier(短前缀匹配)
_NS_TIER: dict[str, str] = {
    "video.info": "hot",       # 视频元数据查询
    "video.asr": "warm",       # 字幕提取
    "video.bgm": "warm",       # 加背景乐
    "video.tts": "warm",       # 语音合成
    "video.cut": "warm",       # 剪辑
    "video.burn": "warm",      # 烧字幕
    "video.orchestrate": "warm",  # 编排(中等频次)
    "video.create": "warm",    # 创作(中等频次)
    "video.analyze": "warm",   # 分析

    "web.search": "hot",       # 搜索
    "web.fetch": "hot",        # 抓 URL
    "web.research": "warm",    # 多步研究
    "web.extract": "warm",     # 抽取
    "web.screenshot": "warm",  # 截图
    "web.playwright": "warm",  # 浏览器交互
    "web.agent-browser": "warm",  # 浏览器交互
    "web.feedparser": "warm",  # RSS
    "web.ytdlp": "cold",       # 视频元数据(批量场景用,日常少)
    "web.tune": "cold",        # 调参(开发用)
    "web.reach": "cold",       # 14 平台(用户主用某几个,其余冷)

    "poster.gen": "warm",      # 海报 prompt
    "poster.random": "warm",
    "image-gen.from_poster_prompt": "warm",
    "image-gen.from_text": "warm",

    "agency.list_divisions": "warm",  # 列表
    "agency.search": "warm",         # 搜索
    "agency.detail": "cold",         # 详细(低频)

    "free_for_dev.search": "warm",   # 搜索
    "free_for_dev.detail": "cold",   # 详情

    "publish.wechat": "warm",       # 公众号
    "publish.xiaohongshu": "warm",  # 小红书
    "publish.bilibili": "warm",     # B 站
    "publish.youtube": "warm",      # YouTube

    "calendar.create": "warm",      # 日历
    "calendar.list": "hot",         # 列事件(查)
    "calendar.update": "warm",
    "calendar.delete": "cold",      # 删事件(低频)
    "calendar.search": "warm",

    "youtube.upload": "warm",       # 上传(常规)
    "youtube.analytics": "cold",    # 数据(按需)

    "music.compose": "warm",        # 作曲
    "music.lyrics": "warm",

    "git.commit": "warm",
    "git.push": "warm",
    "git.pr_create": "cold",        # PR(按需)
    "git.tag": "cold",              # tag(按需)

    "audit.report": "cold",         # 审计报告(用户原话场景:季度/年度)
    "audit.compliance": "cold",     # 合规检查(用户原话场景:年度)
    "tax.file": "cold",             # 报税(用户原话场景:年度)
    "yearly.summary": "cold",       # 年终总结(用户原话场景:年度)
    "quarterly.report": "cold",     # 季度报告(用户原话场景:季度)
}


def _tier_for(skill_id: str, risk: str) -> str:
    """启发式取 tier。规则:
      1. 命名空间 + 完整 id 命中 → 该 tier
      2. 命名空间段(前 2 段)命中 → 该 tier
      3. 都没命中 + L0 → 'warm'
      4. 都没命中 + L1+ → 'cold'(写操作默认冷,日常少用但关键)
    """
    if skill_id in _NS_TIER:
        return _NS_TIER[skill_id]
    if "." in skill_id:
        ns2 = ".".join(skill_id.split(".")[:2])
        if ns2 in _NS_TIER:
            return _NS_TIER[ns2]
    # 兜底:命名空间段(第一段)
    head = skill_id.split(".")[0]
    if head in _NS_TIER:
        return _NS_TIER[head]
    # L0 默认 warm,L1+ 默认 cold(写操作日常少)
    return "cold" if risk in ("L1", "L2", "L3") else "warm"


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
            # Phase 8:优先读 capability 显式 `_tier`,没设走启发式
            tier = entry.get("_tier") or _tier_for(cap_id, risk)
            out.append(SkillIndex(
                id=cap_id,
                name=name,
                emoji=_emoji_for(cap_id, risk),
                risk=risk,
                tags=_tags_for(cap_id, keywords),
                backend="builtin",
                tier=tier,
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


# ---------------------------------------------------------------------------
# Phase 8 — tier 分组 API(只分层,不删东西)
# ---------------------------------------------------------------------------

def list_skills_by_tier(tier: str) -> list[SkillIndex]:
    """返指定 tier 的 skill 列表。tier ∈ {"hot", "warm", "cold", "archive"}。
    空字符串或未知 tier → 返空列表。
    """
    if not tier:
        return []
    return [s for s in _build_index() if s.tier == tier]


def count_by_tier() -> dict[str, int]:
    """返 tier → 数量映射,例如 {"hot": 8, "warm": 45, "cold": 16, "archive": 0}。
    永远包含 hot/warm/cold/archive 4 个 key(0 也输出),便于 UI 稳定渲染。
    """
    out = {"hot": 0, "warm": 0, "cold": 0, "archive": 0}
    for s in _build_index():
        if s.tier in out:
            out[s.tier] += 1
    return out


def tiers_summary() -> str:
    """返 1 行人话摘要,给 debug / 日志用。"""
    c = count_by_tier()
    total = sum(c.values())
    return (
        f"tiers: hot={c['hot']} warm={c['warm']} cold={c['cold']} "
        f"archive={c['archive']} (total={total})"
    )


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


def _skill_to_compact(s: SkillIndex, *, max_name: int = 24, max_tags: int = 4) -> dict:
    """单 skill 紧凑化:去 emoji / name 截断 / tags 上限。

    Phase 7(2026-09-28):用户决策"全部 skill 给 LLM 看得到,接受成本"。
    紧凑化的目标不是去重(69 项 endpoint 都不重复,无法合并),
    而是去掉 LLM 不需要看的装饰信息,保留关键 id/risk/tags/name 4 字段。
    节省 ~27% token 但 LLM 看到的能力 100% 等价。

    Phase 8(2026-09-28):保留 tier 字段(hot/warm/cold/archive),用于未来按 tier
    分层注入,本阶段 system prompt 不变(全量 69 仍输出)。
    """
    out = {
        "id": s.id,
        "name": (s.name[:max_name - 3] + "...") if len(s.name) > max_name else s.name,
        "risk": s.risk,
        "tags": list(s.tags[:max_tags]),
        "tier": s.tier,
    }
    # backend=builtin 是默认且唯一值,不输出省字符;
    # extension/tool_use 输出便于 LLM 识别
    if s.backend and s.backend != "builtin":
        out["backend"] = s.backend
    return out


def describe_registry_compact(*, ultra: bool = False) -> str:
    """把索引序列化成单行紧凑 JSON 字符串(给 system prompt 直接拼)。

    这是用户拍板的「JSON 结构化,可被 LLM 程序化解析」形式。

    参数:
        ultra: 字段名短化(i/n/r/t/b/t)+ 全去 backend,0 节省 ~38%,但牺牲可读性。
               默认 False(标准紧凑,去 emoji + name 截断 + tags 上限,-27%)。

    Phase 8:tier 字段保留在每项里;system prompt 不变(全量 69 + tier 字段)。
    后续若启用按 tier 分层注入,可改 `skills_index_block(only_tiers=...)` 参数。
    """
    skills = _build_index()
    if ultra:
        # 字段名短化版(LLM 看短名无歧义:schema_version→v, total→n, skills→s,
        # id→i, name→n, risk→r, tags→t, backend→b, tier→tir)
        compact = []
        for sk in skills:
            d = _skill_to_compact(sk)
            compact.append({
                "i": d["id"], "n": d["name"], "r": d["risk"],
                "t": d["tags"], "tir": d["tier"],
            })
        return json.dumps({"v": SCHEMA_VERSION, "n": len(compact), "s": compact},
                          ensure_ascii=False, separators=(",", ":"))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "total": len(skills),
        "skills": [_skill_to_compact(s) for s in skills],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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
    "list_skills_by_tier",
    "count_by_tier",
    "tiers_summary",
    "invalidate_cache",
]