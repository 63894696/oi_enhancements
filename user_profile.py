# -*- coding: utf-8 -*-
"""用户画像沉淀(2026-08-24)

让 agent「越用越懂用户」:每轮对话结束后,用一个轻量 LLM 调用提炼本轮暴露的
**稳定用户特征**(偏好/习惯/角色/忌讳),合并进本地画像文件;下次对话 recall 时注入,
越攒越准。全程本地,不上云、不要账号。

设计取舍(最小可用版):
- 画像存 `~/.local/share/prisir/user_profile.json`(与 chats.db 同目录,纯本地)。
- 提炼走 router.generate()(与 generate_followups 同通道),失败静默不阻塞对话。
- 只沉淀「稳定特征」,不存对话内容/临时上下文(那是 L2/L3 的事)。
- 合并是去重追加 + 每条带累计命中数,同义特征反复出现会加权而非堆重复行。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path

# 画像维度:只提炼这四类稳定特征
_PROFILE_PROMPT = """你在分析一轮用户和 AI 的对话,提炼「用户的稳定特征」,供以后对话参考,让用户觉得 AI 越用越懂他。

只提炼**稳定、可复用**的特征,分四类:
- preference 偏好:语言、详略风格、喜欢格式(表格/分点/只要结论)、要不要代码
- habit 习惯:常用工具链、工作方式、常问的题材
- role 角色/专业:职业/领域、技术深度(决定解释要不要从基础讲起)
- taboo 忌讳:不喜欢被追问、不要啰嗦铺垫、讨厌通用答案

规则:
- 只写这轮**新暴露**或**被证实**的特征;没有就返回空数组。
- 每条是一句简短中文描述(<=20字),不要存对话内容本身。
- 不要编造,不要把一次性上下文当稳定特征。
- 严格返回 JSON 数组,形如:[{{"kind":"preference","fact":"偏好分点作答"}},...]

【本轮对话】
用户:{user}
AI:{answer}

JSON 数组:"""

_CATEGORIES = ("preference", "habit", "role", "taboo")
_KIND_LABEL = {"preference": "偏好", "habit": "习惯", "role": "角色", "taboo": "忌讳"}


def _profile_path() -> Path:
    root = os.environ.get("PRISIR_DATA_DIR") or str(Path.home() / ".local" / "share" / "prisir")
    return Path(root) / "user_profile.json"


def load_profile(include_archived: bool = False) -> list[dict]:
    """读画像文件,返回 [{kind,fact,count,ts},...]。失败返回 []。
    默认跳过 archived(归档非删除,可恢复);include_archived=True 时全量返回。"""
    try:
        p = _profile_path()
        if not p.is_file():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        if include_archived:
            return data
        return [it for it in data if not it.get("archived")]
    except Exception:  # noqa: BLE001
        return []


def archive_fact(fact: str) -> bool:
    """按 fact 文本归档一条画像(置 archived,不删,可恢复)。返回是否命中。"""
    try:
        p = _profile_path()
        if not p.is_file():
            return False
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return False
        hit = False
        for it in data:
            if it.get("fact") == fact:
                it["archived"] = True
                hit = True
        if hit:
            _save_profile(data)
        return hit
    except Exception:  # noqa: BLE001
        return False


def _save_profile(items: list[dict]) -> None:
    try:
        p = _profile_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _merge(old: list[dict], new_facts: list[dict]) -> list[dict]:
    """合并新特征:同 kind+同/近义 fact 则 count+1(加权),否则追加。上限 60 条防膨胀。"""
    by_key = {(it.get("kind", ""), it.get("fact", "")): it for it in old}
    for nf in new_facts:
        kind = nf.get("kind", "")
        fact = (nf.get("fact") or "").strip()[:40]
        if kind not in _CATEGORIES or not fact:
            continue
        key = (kind, fact)
        if key in by_key:
            by_key[key]["count"] = int(by_key[key].get("count", 1)) + 1
            by_key[key]["ts"] = time.time()
        else:
            item = {"kind": kind, "fact": fact, "count": 1, "ts": time.time()}
            by_key[key] = item
            old.append(item)
    # 超上限:保留 count 高 + 最近的
    if len(old) > 60:
        old.sort(key=lambda x: (int(x.get("count", 1)), float(x.get("ts", 0))), reverse=True)
        del old[60:]
    return old


async def distill_profile(router, user_text: str, answer: str) -> None:
    """对话结束后提炼画像。失败静默。供 _run_chat_thread 末尾 asyncio.run 调用。"""
    if not user_text.strip():
        return
    convo = [{"role": "user", "content": _PROFILE_PROMPT.format(
        user=user_text[:1200], answer=(answer or "")[:1500])}]
    try:
        res = await router.generate(convo, strategy="smart", temperature=0.2, max_tokens=300)
        text = (res.get("text") or "").strip()
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return
        arr = json.loads(m.group(0))
        if not isinstance(arr, list) or not arr:
            return
        old = load_profile(include_archived=True)  # 合并要保留已归档条目,不能丢
        _save_profile(_merge(old, arr))
    except Exception:  # noqa: BLE001
        pass


def distill_profile_sync(router, user_text: str, answer: str) -> None:
    """同步包装,供对话线程末尾直接调用。任何异常都吞掉不阻塞对话。"""
    try:
        asyncio.run(distill_profile(router, user_text, answer))
    except Exception:  # noqa: BLE001
        pass


def profile_block(max_items: int = 14) -> str:
    """生成注入系统提示的画像块。无画像返回空串。按 count+recency 排序取前 N。"""
    try:
        items = load_profile()
        if not items:
            return ""
        items = sorted(items, key=lambda x: (int(x.get("count", 1)), float(x.get("ts", 0))),
                       reverse=True)[:max_items]
        lines = ["[用户画像 — 你了解的这位用户,据此调整回答风格/深度,越用越懂他]"]
        for it in items:
            label = _KIND_LABEL.get(it.get("kind", ""), it.get("kind", ""))
            lines.append(f"  - ({label}) {it.get('fact','')}")
        lines.append("[画像结束]")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""
