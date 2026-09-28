"""prisir_work/agent_multi_turn.py — 多轮对话补 missing(P3j T15-C)。

定位:用户第一轮说 "做个视频",agent 看到 missing=[script],第二轮追问
"文案是?",第三轮用户说文案后填上再发。状态保存在对话线程。

设计:
  · Session dataclass — 一次会话的(intent, args, history, done)
  · start_session(query) → Session(missing_questions=[...])
  · fill_missing(session_id, key, value) → 更新 args + 重算 missing
  · 当 missing 空 → session.ready = True → 可 execute
  · **in-memory 单进程存储**(redis/sqlite 留作 P3j T15.x);session_id 是
    uuid4 字符串,调用方(Web UI / LLM 框架)自己存

用例:
  from prisir_work.agent_multi_turn import SessionStore
  store = SessionStore()
  s = store.start("做个视频,主题 PrisirAI")
  print(s.missing_questions)  # ['文案是?']
  s = store.fill(s.id, "script", "介绍 PrisirAI 的核心能力")
  print(s.ready)  # True
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from . import agent_natural_video as anv

__all__ = ["Session", "SessionStore", "start_session", "fill_missing"]


# ---------------------------------------------------------------------------
# Session 数据类
# ---------------------------------------------------------------------------

@dataclass
class Session:
    """一次多轮对话补 missing 的会话。"""
    id: str
    original_query: str
    capability: str
    args: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    missing_questions: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "original_query": self.original_query,
            "capability": self.capability,
            "args": dict(self.args),
            "missing": list(self.missing),
            "missing_questions": list(self.missing_questions),
            "history": list(self.history),
            "ready": self.ready,
        }


# ---------------------------------------------------------------------------
# 把 missing 翻译成对人话问句(LLM/前端直接展示)
# ---------------------------------------------------------------------------

_MISSING_QUESTIONS: dict[str, str] = {
    "topic": "主题是什么?(例: PrisirAI / AI 改变办公 / 一日三餐)",
    "script": "文案/脚本是什么?(或口播稿)",
    "text": "要转语音的文字内容是?",
    "file": "要转语音的文件路径是?(.txt / .md)",
    "output": "输出文件路径是?(例: C:/out.mp4)",
    "input": "输入视频路径是?",
    "music": "背景音乐路径是?",
    "sub": "字幕文件路径是?(.srt / .vtt / .ass)",
    "path": "视频文件路径是?",
    "video": "要上传的视频路径是?",
    "title": "标题是?",
    "text_or_file": "要转语音的文字内容或文件路径是?",
}


def _missing_to_questions(missing: list[str]) -> list[str]:
    """把 ['topic', 'script'] → ['主题是什么?', '文案是什么?']"""
    out = []
    for m in missing:
        out.append(_MISSING_QUESTIONS.get(m, f"请提供参数: {m}"))
    return out


# ---------------------------------------------------------------------------
# SessionStore(in-memory)
# ---------------------------------------------------------------------------

class SessionStore:
    """多轮对话 session 仓库。单进程,可选 TTL。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def start(self, query: str) -> Session:
        """第一轮:parse_intent → 建 Session。"""
        intent = anv.parse_intent(query)
        if not intent.ok:
            # 解析都没成功 — 也建 Session,但 capability="",前端应回退到 not-recognized
            s = Session(
                id=str(uuid.uuid4()),
                original_query=query,
                capability="",
                args={},
                missing=[],
                missing_questions=[intent.error],
                history=[{"role": "user", "content": query}],
                ready=False,
            )
            self._sessions[s.id] = s
            return s

        s = Session(
            id=str(uuid.uuid4()),
            original_query=query,
            capability=intent.capability,
            args=dict(intent.args),
            missing=list(intent.missing),
            missing_questions=_missing_to_questions(intent.missing),
            history=[{"role": "user", "content": query}],
            ready=not bool(intent.missing),
        )
        self._sessions[s.id] = s
        return s

    def fill(self, session_id: str, key: str, value: Any) -> Optional[Session]:
        """第二轮及之后:用户回答 → 写 args → 重算 missing。"""
        s = self._sessions.get(session_id)
        if not s:
            return None
        if s.ready:
            # 已就绪 — 拒二次 fill
            return s
        if key not in s.missing:
            # 字段不在 missing 里 — 加进 args 但不重算 missing
            s.args[key] = value
        else:
            s.args[key] = value
            s.missing = [m for m in s.missing if m != key]
            s.missing_questions = _missing_to_questions(s.missing)
            s.ready = not bool(s.missing)
        s.history.append({"role": "user", "content": f"{key} = {value}"})
        return s

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def drop(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def all_ids(self) -> list[str]:
        return list(self._sessions.keys())


# ---------------------------------------------------------------------------
# 模块级单例(共享 — Web UI / LLM 框架都拿到同一份)
# ---------------------------------------------------------------------------

_global_store = SessionStore()


def start_session(query: str) -> Session:
    return _global_store.start(query)


def fill_missing(session_id: str, key: str, value: Any) -> Optional[Session]:
    return _global_store.fill(session_id, key, value)


def get_session(session_id: str) -> Optional[Session]:
    return _global_store.get(session_id)