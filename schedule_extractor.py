# -*- coding: utf-8 -*-
"""日程/任务/番茄钟 AI 主动编排 v0.1.0 (Phase P2.5+8, 2026-09-20)

设计:对话提到时间+事件→LLM 提炼 JSON→后台线程写日历/todo/pomodoro。
不阻塞对话、不向用户要确认(首次明细已批准)。

复用模板: solutions_learner.learn_from_chat_sync / user_profile.distill_profile_sync
跑在 _run_chat_thread 末尾的后台 daemon thread 里。

红线:
- 全本地 LLM,失败静默不阻塞对话
- 触发关键词白名单过滤,无时间词不调 LLM
- 写入失败不影响对话
- pomodoro 只建议不主动 start(用户必须自己在场开始)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

log = logging.getLogger("schedule_extractor")

# ── 触发关键词(对 user_text 简单 OR 匹配,有则跑 LLM)────────────────
_TRIGGERS = re.compile(
    r"明天|今天|后天|下周|周[一二三四五六日]|"
    r"上午|下午|晚上|早上|中午|凌晨|"
    r"\d+月\d+日|\d+:\d+|\d+点|几点|"
    r"会议|开会|约|赴|赴约|面试|"
    r"截止|deadline|due|提醒|记得|备忘|"
    r"任务|待办|todo|办完|搞定|完成|"
    r"番茄|pomodoro|专注|深潜|"
    r"分钟|小时",
    re.IGNORECASE,
)

_PROMPT = """你是一个日程/任务提炼助手。判断下面这轮对话是否提到了**可执行的时间安排**。

输出严格 JSON(无 markdown 包裹):
{
  "events": [{"summary":"<事件标题>","dtstart":"<ISO8601 UTC,如 2026-09-21T06:00:00Z>","dtend":"<ISO8601 UTC>","timezone":"Asia/Shanghai","description":"<可选>"}],
  "todos": [{"title":"<任务标题>","due":"<ISO8601,可选>","priority":"high|mid|low","tags":["<可选>"]}],
  "pomo": {"suggest":[{"start_at":"<ISO8601,可选>","duration_min":25,"task_title":"<任务名>"}],"reason":"<为什么建议>"}
}

无明确时间安排 → 返回 {"events":[],"todos":[],"pomo":{"suggest":[]}}。
不要对日常寒暄/技术问答/纯聊天编造事件。

对话:
[用户] {user_text}
[助手] {answer}

只输出 JSON,不要解释。"""


async def distill_schedule(router, user_text: str, answer: str) -> dict:
    """异步调 LLM 提炼。返 {events, todos, pomo} dict(失败/无安排 → 空 dict)。"""
    if not router or not user_text:
        return {}
    convo = [
        {"role": "system", "content": _PROMPT.format(
            user_text=user_text[:800], answer=answer[:1500])},
        {"role": "user", "content": "请提炼 JSON。"},
    ]
    try:
        res = await router.generate(convo, strategy="fast",
                                    temperature=0.1, max_tokens=400)
        text = (res.get("text") or "").strip()
        # 抽 JSON 块(可能混了 markdown ```json ... ```)
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {}
        data = json.loads(m.group(0))
        if not isinstance(data, dict):
            return {}
        data.setdefault("events", [])
        data.setdefault("todos", [])
        if not isinstance(data.get("pomo"), dict):
            data["pomo"] = {"suggest": []}
        # 类型兜底
        if not isinstance(data["events"], list):
            data["events"] = []
        if not isinstance(data["todos"], list):
            data["todos"] = []
        if not isinstance(data["pomo"].get("suggest"), list):
            data["pomo"]["suggest"] = []
        return data
    except Exception as e:  # noqa: BLE001
        log.warning("distill_schedule llm err: %s", e)
        return {}


def distill_schedule_sync(router, user_text: str, answer: str) -> dict:
    """同步包装。返回提炼结果(异常返空 dict),供主线程末尾调。"""
    try:
        return asyncio.run(distill_schedule(router, user_text, answer))
    except Exception:  # noqa: BLE001
        return {}


def should_trigger(user_text: str) -> bool:
    """是否触发 LLM 提炼。粗筛:含时间/事件关键词才跑。"""
    if not user_text:
        return False
    return bool(_TRIGGERS.search(user_text))


def validate_iso(s: Any) -> str | None:
    """校验 ISO8601 时间字符串。无效返 None。"""
    if not isinstance(s, str) or not s.strip():
        return None
    s = s.strip()
    try:
        from datetime import datetime
        # 支持 "2026-09-21T14:00:00+08:00" / "...Z" / "...+0800"
        s2 = s.replace("Z", "+00:00") if s.endswith("Z") else s
        datetime.fromisoformat(s2)
        return s
    except Exception:
        return None