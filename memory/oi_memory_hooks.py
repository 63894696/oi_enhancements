"""OI agent 持久记忆层 hooks — pre_chat 自动 recall,post_chat 自动 store

用法:
    from interpreter import interpreter
    from oi_memory_hooks import install
    install(interpreter, agent_name='oi-coder')

之后每次 interpreter.chat(task) 会自动:
  1. pre_chat:按 task 内容 recall 相关历史 → 注入 system message
  2. post_chat:把对话存到 L3(短期工作记忆)

可以手动 store L0(身份)/ L1(关键事实):
    from oi_memory_hooks import store
    store(layer='L0', title='user', content='用户偏好中文 + 写笔记习惯')
    store(layer='L1', title='projects.team-web', content='C:/Users/Administrator/demos/team-web')
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# 让 oi_memory 可 import(同目录)
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from oi_memory import OIMemory, LAYERS  # noqa: E402

_DEFAULT_MEM: OIMemory | None = None


def get_memory() -> OIMemory:
    """单例,所有 OI 进程共享一个 SQLite"""
    global _DEFAULT_MEM
    if _DEFAULT_MEM is None:
        _DEFAULT_MEM = OIMemory()
    return _DEFAULT_MEM


def store(layer: str, title: str, content: str, tags: list[str] | None = None) -> int:
    """手动 store,主要给 L0/L1 用(身份 + 关键事实)"""
    return get_memory().store(layer=layer, title=title, content=content, tags=tags)


def recall(query: str, n: int = 5) -> list[dict]:
    """手动 recall,返回 dict 列表方便序列化"""
    return [
        {
            "id": m.id, "layer": m.layer, "title": m.title,
            "content": m.content[:300], "tags": m.tags,
            "created_at": m.created_at,
        }
        for m in get_memory().recall(query, n=n)
    ]


def _format_hits_for_prompt(hits: list) -> str:
    """把 recall 结果格式化成可注入 system prompt 的文本
    支持 Memory dataclass 和 dict 两种输入(hooks 层用 dict)
    """
    if not hits:
        return ""
    lines = ["[OI memory recall — related past context]"]
    for i, h in enumerate(hits, 1):
        layer = h.layer if hasattr(h, "layer") else h.get("layer", "?")
        title = h.title if hasattr(h, "title") else h.get("title", "?")
        content = h.content if hasattr(h, "content") else h.get("content", "")
        snippet = (content or "")[:200].replace("\n", " ")
        more = "..." if len(content or "") > 200 else ""
        lines.append(f"  {i}. [{layer}] {title}: {snippet}{more}")
    lines.append("[End recall]")
    return "\n".join(lines)


def install(interpreter, agent_name: str = "oi", recall_n: int = 5, max_recall_chars: int = 1500) -> None:
    """把 pre_chat / post_chat hooks 装到 OI interpreter 上

    装完之后:
      - 每次 chat() 开始前,自动按 task 内容 recall → 注入 system message
      - 每次 chat() 结束后,自动把 user/task 和 assistant 回复存到 L3
    """
    mem = get_memory()

    # 记录 agent 身份到 L0
    mem.store(
        layer="L0",
        title=f"agent:{agent_name}",
        content=f"OI agent instance {agent_name} pid={os.getpid()} installed at {time.time()}",
        tags=["oi-agent"],
        dedupe_title=True,
    )

    # ---------- pre_chat hook ----------
    _orig_chat = interpreter.chat

    def chat_with_memory(*args, **kwargs):
        # 抓原始 task 描述(必须在 recall 注入前保存,否则 post_chat 会污染 L3)
        original_task = args[0] if args else kwargs.get("message") or kwargs.get("task") or ""
        task = original_task
        if isinstance(task, str) and task.strip():
            hits = mem.recall(task, n=recall_n)
            if hits:
                ctx = _format_hits_for_prompt(hits)
                if len(ctx) > max_recall_chars:
                    ctx = ctx[:max_recall_chars] + "\n[recall truncated]"
                # 把 recall 注入到 task 开头(只发给 LLM,不存到 L3)
                if args:
                    args = (f"{ctx}\n\n{original_task}",) + args[1:]
                else:
                    kwargs["message"] = f"{ctx}\n\n{original_task}"

        # 跑原 chat(消费 generator)
        chunks = []
        for chunk in _orig_chat(*args, **kwargs):
            chunks.append(chunk)
            yield chunk

        # post_chat:存对话快照到 L3,用 original_task 避免 recall context 污染
        try:
            response_text = _extract_response_text(chunks)
            if original_task and response_text:
                title = original_task[:80].replace("\n", " ")
                body = f"USER TASK:\n{original_task[:1500]}\n\nASSISTANT:\n{response_text[:3000]}"
                mem.store(
                    layer="L3",
                    title=f"{agent_name}:{title}",
                    content=body,
                    tags=["oi-chat", agent_name],
                )
        except Exception:
            pass  # post_chat 失败不能阻塞主流程

    interpreter.chat = chat_with_memory


def _extract_response_text(chunks: list) -> str:
    """从 OI chat 的 chunk 流里抽 assistant 的纯文本回复"""
    parts: list[str] = []
    for c in chunks:
        if isinstance(c, dict):
            t = c.get("type")
            if t == "message":
                content = c.get("content")
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    for seg in content:
                        if isinstance(seg, dict) and "text" in seg:
                            parts.append(seg["text"])
        elif isinstance(c, str):
            parts.append(c)
    return "\n".join(parts)


if __name__ == "__main__":
    # smoke
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(get_memory().stats(), indent=2, ensure_ascii=False))
    elif len(sys.argv) > 1 and sys.argv[1] == "recall":
        q = sys.argv[2] if len(sys.argv) > 2 else "panel bug fix"
        print(json.dumps(recall(q), indent=2, ensure_ascii=False))
    else:
        print("Usage:")
        print("  python oi_memory_hooks.py stats")
        print("  python oi_memory_hooks.py recall <query>")