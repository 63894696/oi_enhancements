# -*- coding: utf-8 -*-
"""方案库自学习闭环 + 主题聚类(2026-08-24)

对标 Hermes Agent 的「闭环学习」,最小可用 + 全本地版:
- 主索引 docs/preset-solutions-index.md 是**手工维护**的,永不自动改(保持可控)。
- agent 成功解决任务后,后台线程用轻量 LLM 调用提炼「可复用的本机解法」,写进
  **隔离的 learned 附加区**(`<数据目录>/learned-solutions.md`),与主表分离。
- 命中已有 18 类 → 归到对应类;自评是全新可复用解法 → 进「待归类候选」小节,
  由人工/review 定期归并回主表。同 title 去重加权(count+1),不堆重复。
- 注入:命中类别的 learned 条目 + 待归类计数,随系统提示给 agent,越用越厚。

另含 topic_block:纯本地关键词聚类近 N 条用户消息,提示「你近期常问什么」。

红线:全本地、零云、失败静默不阻塞对话;learn 必须后台线程(同步会卡 409)。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
from pathlib import Path

# 与 prisiragent_web._PRESET_KEYWORDS 的 18 类保持一致(learn prompt 据此归类)。
# 不 import prisiragent_web(避免循环/打包牵连),这里独立维护一份类目名清单。
CATEGORIES: tuple[str, ...] = (
    "输入法问题", "装包问题", "VM 通道问题", "对话链问题", "浏览器问题",
    "文件搜索问题", "协作问题", "权限问题", "密信问题", "翻译问题",
    "视频笔记问题", "VPN 问题", "论坛问题", "网站问题", "微信公众号问题",
    "内容柜问题", "书签分类问题", "移动端问题",
)
NEW_CATEGORY = "new_category"

_LEARN_PROMPT = """你在分析一轮用户和 AI 的对话,判断它是否**成功产出了一个可复用的本机解法**,供以后遇到同类问题直接复用。

只提炼**可复用的解法**(关键路径/命令/坑/步骤),不要存对话内容本身。
一次性问答、纯聊天、失败的尝试,都返回空对象 {{}}。

若确有可复用解法,严格返回 JSON 对象:
{{
  "category": "<从下列已有类选一个,或填 new_category 表示全新类>",
  "title": "<一句话解法名,<=20字>",
  "solution": "<可复用解法要点,含关键路径/命令/坑,<=120字>",
  "paths": ["<相关文件/目录路径,可空数组>"]
}}
已有类目:{categories}

【本轮对话】
用户:{user}
AI:{answer}

JSON 对象:"""

_MAX_ITEMS = 80  # learned 区条目上限,防膨胀


def _data_dir() -> Path:
    root = os.environ.get("PRISIR_DATA_DIR") or str(Path.home() / ".local" / "share" / "prisir")
    return Path(root)


def _learned_path() -> Path:
    # 运行时写,不能放 _REPO_ROOT/docs(frozen 下 _MEIPASS 只读),走用户数据目录。
    return _data_dir() / "learned-solutions.md"


def _chats_db() -> Path:
    return _data_dir() / "chats.db"


# ---------------------------------------------------------------------------
# learned 文件读写(行格式,机器可解析):
#   - [category] title :: solution :: path1,path2 :: count :: ts :: archived(0/1)
# 新类候选单列一节。
# ---------------------------------------------------------------------------
def _serialize(item: dict) -> str:
    paths = ",".join(item.get("paths") or [])
    return (f"- [{item.get('category','')}] {item.get('title','')} :: "
            f"{item.get('solution','')} :: {paths} :: "
            f"{int(item.get('count',1))} :: {int(item.get('ts',0))} :: "
            f"{1 if item.get('archived') else 0}")


def _parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("- ["):
        return None
    try:
        m = re.match(r"^- \[(.*?)\] (.*?) :: (.*?) :: (.*?) :: (\d+) :: (\d+) :: (\d+)\s*$", line)
        if not m:
            return None
        cat, title, sol, paths, count, ts, arch = m.groups()
        return {
            "category": cat, "title": title, "solution": sol,
            "paths": [p for p in paths.split(",") if p],
            "count": int(count), "ts": int(ts), "archived": arch == "1",
        }
    except Exception:  # noqa: BLE001
        return None


def load_learned() -> list[dict]:
    """读 learned 区全部条目(含 archived)。失败返回 []。"""
    try:
        p = _learned_path()
        if not p.is_file():
            return []
        items = []
        for line in p.read_text(encoding="utf-8").splitlines():
            it = _parse_line(line)
            if it:
                items.append(it)
        return items
    except Exception:  # noqa: BLE001
        return []


def _save_learned(items: list[dict]) -> None:
    try:
        p = _learned_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        classified = [i for i in items if i.get("category") != NEW_CATEGORY]
        candidates = [i for i in items if i.get("category") == NEW_CATEGORY]
        lines = [
            "# Prisir 方案库 — 自动学习附加区(agent 学习写入,勿手改主表)",
            "# 行格式: - [类目] 标题 :: 解法 :: 路径,路径 :: count :: ts :: archived",
            "",
            "## 已分类",
        ]
        lines += [_serialize(i) for i in classified] or ["(空)"]
        lines += ["", "## 待归类候选(新类,待人工/review 归并回主索引)"]
        lines += [_serialize(i) for i in candidates] or ["(空)"]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def archive_learned(title: str) -> bool:
    """按 title 归档一条 learned 解法(不删,可恢复)。返回是否命中。"""
    try:
        items = load_learned()
        hit = False
        for it in items:
            if it.get("title") == title:
                it["archived"] = True
                hit = True
        if hit:
            _save_learned(items)
        return hit
    except Exception:  # noqa: BLE001
        return False


def _merge_learned(old: list[dict], new: dict) -> list[dict]:
    """同 title 去重加权(count+1 + 刷新 ts + 合并更长的 solution),否则追加。上限截断。"""
    title = (new.get("title") or "").strip()[:30]
    if not title:
        return old
    for it in old:
        if it.get("title") == title:
            it["count"] = int(it.get("count", 1)) + 1
            it["ts"] = time.time()
            if len(new.get("solution", "")) > len(it.get("solution", "")):
                it["solution"] = new["solution"]
            np = [p for p in (new.get("paths") or []) if p not in it.get("paths", [])]
            it.setdefault("paths", []).extend(np)
            return old
    old.append({
        "category": new.get("category") if new.get("category") in CATEGORIES else NEW_CATEGORY,
        "title": title,
        "solution": (new.get("solution") or "")[:160],
        "paths": list(new.get("paths") or [])[:6],
        "count": 1, "ts": time.time(), "archived": False,
    })
    # 超上限:按 count+recency 保前 N
    if len(old) > _MAX_ITEMS:
        old.sort(key=lambda x: (int(x.get("count", 1)), float(x.get("ts", 0))), reverse=True)
        del old[_MAX_ITEMS:]
    return old


async def learn_from_chat(router, user_text: str, answer: str) -> None:
    """对话成功后提炼可复用解法进 learned 区。失败静默。供后台线程 asyncio.run 调。"""
    if not user_text.strip() or not (answer or "").strip():
        return
    prompt = _LEARN_PROMPT.replace("{categories}", "、".join(CATEGORIES)).format(
        user=user_text[:1200], answer=answer[:1500])
    try:
        res = await router.generate([{"role": "user", "content": prompt}],
                                    strategy="smart", temperature=0.2, max_tokens=300)
        text = (res.get("text") or "").strip()
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return
        obj = json.loads(m.group(0))
        if not isinstance(obj, dict) or not obj.get("title"):
            return
        cat = obj.get("category") or NEW_CATEGORY
        if cat not in CATEGORIES:
            cat = NEW_CATEGORY
        obj["category"] = cat
        items = load_learned()
        _save_learned(_merge_learned(items, obj))
    except Exception:  # noqa: BLE001
        pass


def learn_from_chat_sync(router, user_text: str, answer: str) -> None:
    """同步包装,供对话线程末尾后台线程调用。任何异常吞掉不阻塞对话。"""
    try:
        asyncio.run(learn_from_chat(router, user_text, answer))
    except Exception:  # noqa: BLE001
        pass


def learned_block(hit_categories: list[str] | None = None, max_items: int = 8) -> str:
    """渲染注入块:命中类别的 learned 解法(非 archived)+ 待归类候选计数。"""
    try:
        items = [i for i in load_learned() if not i.get("archived")]
        if not items:
            return ""
        hits = []
        if hit_categories:
            hits = [i for i in items if i.get("category") in hit_categories]
        # 没命中类别时不硬塞全部(防噪音),只给待归类计数
        candidates = [i for i in items if i.get("category") == NEW_CATEGORY]
        hits = sorted(hits, key=lambda x: (int(x.get("count", 1)), float(x.get("ts", 0))),
                      reverse=True)[:max_items]
        if not hits and not candidates:
            return ""
        lines = ["[自动学习的解法 — agent 从成功任务里沉淀,优先参考]"]
        for i in hits:
            lines.append(f"  - [{i.get('category')}] {i.get('title')}(x{i.get('count',1)}): "
                         f"{i.get('solution')}")
        if candidates:
            lines.append(f"  (另有 {len(candidates)} 条待归类新解法)")
        lines.append("[自动学习解法结束]")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# 主题聚类提示(纯本地关键词匹配,不调 LLM,60s 缓存)
# ---------------------------------------------------------------------------
# 复用与 _PRESET_KEYWORDS 相同的关键词表(独立维护一份,避免 import prisiragent_web)。
_TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("输入法", ("输入法", "候选词", "拼音", "五笔", "语音输入", "打字", "灵犀")),
    ("装包", ("装包", "安装包", "NSIS", "PyInstaller", "打包", "Setup", "卸载")),
    ("浏览器", ("浏览器", "Chromium", "MV3", "扩展", "CDP", "下载")),
    ("翻译", ("翻译", "漫画翻译", "字幕翻译", "OCR", "translate")),
    ("视频", ("视频", "B站", "Bilibili", "YouTube", "字幕", "ffmpeg", "gif")),
    ("搜索", ("搜索", "findex", "fcontent", "找文件", "全盘")),
    ("协作/派单", ("派单", "协作", "tasks-code", "consumer", "开发团队")),
    ("移动端", ("移动端", "Android", "安卓", "APK", "Capacitor", "MuMu", "手机")),
    ("微信/论坛", ("微信", "公众号", "论坛", "bbs")),
    ("权限/安全", ("权限", "安全检查", "弹卡", "病毒", "监控")),
)
_TOPIC_CACHE: dict = {"ts": 0.0, "text": ""}
_TOPIC_TTL = 60.0


def topic_block(max_topics: int = 3, lookback: int = 60) -> str:
    """聚类近 lookback 条用户消息,渲染「你近期常问」一行。无命中返回空串。"""
    now = time.time()
    if now - _TOPIC_CACHE["ts"] < _TOPIC_TTL and _TOPIC_CACHE["text"]:
        return _TOPIC_CACHE["text"]
    try:
        db = _chats_db()
        if not db.is_file():
            return ""
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = [r[0] for r in con.execute(
                "SELECT content FROM messages WHERE role='user' ORDER BY rowid DESC LIMIT ?",
                (lookback,))]
        finally:
            con.close()
        counts: dict[str, int] = {}
        for content in rows:
            for topic, kws in _TOPIC_KEYWORDS:
                if any(k in content for k in kws):
                    counts[topic] = counts.get(topic, 0) + 1
        if not counts:
            return ""
        top = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:max_topics]
        text = "[你近期常问] " + "、".join(f"{t}({n})" for t, n in top)
        _TOPIC_CACHE["ts"] = now
        _TOPIC_CACHE["text"] = text
        return text
    except Exception:  # noqa: BLE001
        return ""
