"""P4-Compaction(2026-10-02) — PrisirAI 上下文压缩(借鉴 jcode-compaction-core)

设计原则:
- 200K token budget(对齐 Claude 真实窗口)
- 80% 触发 summary 软压, 95% 触发 hard 硬压
- 图片平摊 1600 tokens / 图片(避免 base64 长度反复触发连续压缩)
- 工具结果 >4000 字截断, 图片 >1024 字截断
- 同步 summary,不异步(PrisirAI 主对话 latency 敏感)
- 失败 fallback:任何异常 → 返原 messages + None_,不抛(主对话不受影响)

不照搬 jcode:
- BackgroundStarted 异步 → 同步 summary(简化)
- Confirm + Reflect gate → 不引入二次 LLM 调用
- 4 段 SUMMARY_PROMPT 中文化(PrisirAI 中文长文本诉求)

借鉴源:`/tmp/jcode-recon/crates/jcode-compaction-core/src/lib.rs`(1104 行,2026-10 抓取)。
完整设计:C:\\Users\\Administrator\\.claude\\plans\\stateless-launching-locket.md(P4-Compaction 段)
"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

# 让 from memory.oi_memory 可用(测试在 memory/tests/)
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

log = logging.getLogger("memory.compaction")

__all__ = [
    "CompactionAction",
    "CompactionStats",
    "CompactionManager",
    "SUMMARY_PROMPT",
    "DEFAULT_TOKEN_BUDGET",
    "COMPACTION_THRESHOLD",
    "CRITICAL_THRESHOLD",
    "RECENT_TURNS_TO_KEEP",
    "MIN_TURNS_TO_KEEP",
    "EMERGENCY_TOOL_RESULT_MAX_CHARS",
    "EMERGENCY_IMAGE_MAX_CHARS",
    "IMAGE_TOKEN_COST",
    "CHARS_PER_TOKEN",
    "SYSTEM_OVERHEAD_TOKENS",
    "SUMMARY_MAX_CHARS",
]

# ---------------------------------------------------------------------------
# 常量 — 沿用 jcode-compaction-core(注释标借鉴)
# ---------------------------------------------------------------------------

# 借鉴 jcode DEFAULT_TOKEN_BUDGET:对齐 Claude 真实 200K 上下文窗口
DEFAULT_TOKEN_BUDGET: int = 200_000

# 借鉴 jcode COMPACTION_THRESHOLD:80% 触发 summary 软压
COMPACTION_THRESHOLD: float = 0.80

# 借鉴 jcode CRITICAL_THRESHOLD:95% 触发 hard 硬压(避免 API 报 too large)
CRITICAL_THRESHOLD: float = 0.95

# 借鉴 jcode MANUAL_COMPACT_MIN_THRESHOLD:用户手动压缩的最小门槛(本模块未启用)
MANUAL_COMPACT_MIN_THRESHOLD: float = 0.10

# 借鉴 jcode RECENT_TURNS_TO_KEEP:summary 后保留的最近轮次数
RECENT_TURNS_TO_KEEP: int = 10

# 借鉴 jcode MIN_TURNS_TO_KEEP:硬压时绝对最少保留的轮次数
MIN_TURNS_TO_KEEP: int = 2

# 借鉴 jcode EMERGENCY_TOOL_RESULT_MAX_CHARS:工具结果超长截断
EMERGENCY_TOOL_RESULT_MAX_CHARS: int = 4_000

# 借鉴 jcode EMERGENCY_IMAGE_MAX_CHARS:图片 base64 截断(避免 payload 过大)
EMERGENCY_IMAGE_MAX_CHARS: int = 1_024

# 借鉴 jcode PAYLOAD_IMAGE_CHAR_BUDGET:12MB 字节预算,用于 provider 413 兜底
PAYLOAD_IMAGE_CHAR_BUDGET: int = 12 * 1024 * 1024

# 借鉴 jcode CHARS_PER_TOKEN:粗估 4 字符 = 1 token
CHARS_PER_TOKEN: int = 4

# 借鉴 jcode IMAGE_TOKEN_COST:每张图平摊 1600 tokens(避免反复触发连续压缩)
IMAGE_TOKEN_COST: int = 1_600

# 借鉴 jcode SYSTEM_OVERHEAD_TOKENS:system prompt + tool defs 固定开销
SYSTEM_OVERHEAD_TOKENS: int = 18_000

# PrisirAI 定制:摘要输出上限(中文 4 段 ~4000 字)
SUMMARY_MAX_CHARS: int = 4_000

# PrisirAI 定制:system 段视作永远不压缩(system 永远在,只压缩 user/assistant 历史)
SYSTEM_ROLE = "system"

# ---------------------------------------------------------------------------
# 中文 SUMMARY_PROMPT(借鉴 jcode 4 段格式,中文化)
# ---------------------------------------------------------------------------

SUMMARY_PROMPT: str = """请将以下对话压缩成简洁的中文摘要,以便后续轮次继续工作。

请按以下 4 段格式输出:
- **上下文(Context)**:当前任务和目标(1-2 句)
- **已做(What we did)**:关键动作、改动的文件、解决的问题
- **当前状态(Current state)**:已完成、还在做、阻塞中、下一步
- **用户偏好(User preferences)**:用户在对话中已拍板的决策

保持简洁但保留关键细节(如错误信息、决策、文件路径)。"""

# ---------------------------------------------------------------------------
# 极简 yaml scalar 读取(沿用 instincts.py / rules.py 范式,不引 PyYAML)
# ---------------------------------------------------------------------------

def _read_yaml_scalar(key: str, default):
    """极简 yaml scalar 读取:仅识别 `key: value` 顶层行。

    候选:prisIrai_config.yaml / prisirmp.config.yaml,先 cwd 再 oi_enhancements 根。
    不同 default 类型用不同正则(bool/int/float/str)。
    """
    candidates: list[Path] = []
    try:
        cwd = Path.cwd()
        candidates.append(cwd / "prisIrai_config.yaml")
        candidates.append(cwd / "prisirmp.config.yaml")
    except Exception:  # noqa: BLE001
        pass
    try:
        here = Path(__file__).resolve().parent
        proj_root = here.parent  # oi_enhancements/
        candidates.append(proj_root / "prisIrai_config.yaml")
        candidates.append(proj_root / "prisirmp.config.yaml")
    except Exception:  # noqa: BLE001
        pass

    if isinstance(default, bool):
        pat = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(true|false)\s*$",
                         re.IGNORECASE | re.MULTILINE)
    elif isinstance(default, int):
        pat = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(\d+)\s*$",
                         re.MULTILINE)
    elif isinstance(default, float):
        pat = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(-?\d+(?:\.\d+)?)\s*$",
                         re.MULTILINE)
    else:
        pat = re.compile(rf"^\s*{re.escape(key)}\s*:\s*(.+?)\s*$",
                         re.MULTILINE)

    seen: set[Path] = set()
    for c in candidates:
        try:
            cr = c.resolve()
        except Exception:  # noqa: BLE001
            continue
        if cr in seen:
            continue
        seen.add(cr)
        if not cr.is_file():
            continue
        try:
            text = cr.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        m = pat.search(text)
        if not m:
            continue
        v = m.group(1)
        if isinstance(default, bool):
            return v.strip().lower() == "true"
        if isinstance(default, int):
            try:
                return int(v.strip())
            except ValueError:
                return default
        if isinstance(default, float):
            try:
                return float(v.strip())
            except (TypeError, ValueError):
                return default
        return v.strip()
    return default


# ---------------------------------------------------------------------------
# CompactionAction / CompactionStats
# ---------------------------------------------------------------------------

class CompactionAction(Enum):
    """压缩动作类型。借鉴 jcode CompactionAction,去掉 BackgroundStarted 异步分支。"""

    None_ = "none"                   # 不需要压缩
    Summarized = "summarized"        # 80% 软压(summary LLM 调用)
    HardCompacted = "hard_compacted"  # 95% 硬压(直接丢旧消息,无 LLM)


@dataclass
class CompactionStats:
    """压缩状态统计(借鉴 jcode CompactionStats,精简掉异步 / semantic 字段)。"""

    total_turns: int          # 输入 messages 总条数
    active_messages: int      # 过滤 system 后的活跃消息数
    has_summary: bool         # 是否已含 summary system 段
    token_estimate: int       # 当前 token 估算
    context_usage: float      # token_estimate / budget(0-1)


# ---------------------------------------------------------------------------
# CompactionManager
# ---------------------------------------------------------------------------

class CompactionManager:
    """上下文压缩管理器。

    用法:
        mgr = CompactionManager()
        if mgr.is_enabled():
            msgs, action = mgr.compact(msgs, llm_call=None)
            # llm_call=None → 只走 hard 硬压(不调 LLM)
            # llm_call=callable(prompt) -> str → 80%+ 触发 summary
    """

    def __init__(
        self,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        threshold: float = COMPACTION_THRESHOLD,
        critical: float = CRITICAL_THRESHOLD,
        recent_turns: int = RECENT_TURNS_TO_KEEP,
        min_turns: int = MIN_TURNS_TO_KEEP,
    ) -> None:
        self.token_budget: int = token_budget
        self.threshold: float = threshold
        self.critical: float = critical
        self.recent_turns: int = recent_turns
        self.min_turns: int = min_turns

    # ---------- 配置 ----------
    def is_enabled(self) -> bool:
        """读 compaction_enabled 配置(默认 True)。"""
        val = _read_yaml_scalar("compaction_enabled", True)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("true", "1", "yes", "on")
        return True

    def _configured_threshold(self) -> float:
        """读 compaction_threshold(默认 0.80)。"""
        val = _read_yaml_scalar("compaction_threshold", self.threshold)
        try:
            n = float(val)  # type: ignore[arg-type]
            if 0.0 < n <= 1.0:
                return n
        except (TypeError, ValueError):
            pass
        return self.threshold

    def _configured_critical(self) -> float:
        """读 compaction_critical(默认 0.95)。"""
        val = _read_yaml_scalar("compaction_critical", self.critical)
        try:
            n = float(val)  # type: ignore[arg-type]
            if 0.0 < n <= 1.0:
                return n
        except (TypeError, ValueError):
            pass
        return self.critical

    def _configured_recent_turns(self) -> int:
        """读 compaction_recent_turns(默认 10)。"""
        val = _read_yaml_scalar("compaction_recent_turns", self.recent_turns)
        try:
            n = int(val)  # type: ignore[arg-type]
            if n >= self.min_turns:
                return n
        except (TypeError, ValueError):
            pass
        return self.recent_turns

    # ---------- 估算 ----------
    def estimate_tokens(self, messages: list[dict]) -> int:
        """粗估 messages 总 token 数。

        算法(借鉴 jcode):
          - text chars / CHARS_PER_TOKEN(4)
          - 每张 inline image 收 IMAGE_TOKEN_COST(1600,平摊,不按 base64 长度)
          - 加 SYSTEM_OVERHEAD_TOKENS(18K)固定开销

        messages 形如 [{'role': ..., 'content': ...}, ...]
        content 可能是:
          - str
          - list[dict] (含 {'type': 'image', ...} / {'type': 'text', 'text': '...'})
        """
        if not messages:
            return SYSTEM_OVERHEAD_TOKENS

        total_chars = 0
        image_count = 0

        for m in messages:
            if not isinstance(m, dict):
                continue
            content = m.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype in ("image", "image_url"):
                        image_count += 1
                    elif btype == "text":
                        text = block.get("text", "")
                        if isinstance(text, str):
                            total_chars += len(text)
                    else:
                        # 其他 block(tool_use / tool_result)按 JSON 长度粗估
                        try:
                            import json as _json
                            total_chars += len(_json.dumps(block, ensure_ascii=False))
                        except Exception:  # noqa: BLE001
                            total_chars += 100  # 兜底
            else:
                total_chars += len(str(content))

        text_tokens = (total_chars + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
        image_tokens = image_count * IMAGE_TOKEN_COST
        return text_tokens + image_tokens + SYSTEM_OVERHEAD_TOKENS

    def needs_compaction(self, messages: list[dict]) -> tuple[bool, CompactionAction]:
        """判断是否需要压缩 + 哪种动作。

        返回 (是否需要, CompactionAction):
          - (False, None_)        < threshold %
          - (True, Summarized)    threshold <= usage < 临界
          - (True, HardCompacted) usage >= 临界
        """
        try:
            tokens = self.estimate_tokens(messages)
            usage = tokens / self.token_budget if self.token_budget > 0 else 0.0
            thr = self._configured_threshold()
            crit = self._configured_critical()
            if usage >= crit:
                return True, CompactionAction.HardCompacted
            if usage >= thr:
                return True, CompactionAction.Summarized
            return False, CompactionAction.None_
        except Exception:  # noqa: BLE001
            log.exception("[compaction] needs_compaction 失败,fail-open")
            return False, CompactionAction.None_

    def stats(self, messages: list[dict]) -> CompactionStats:
        """取当前压缩状态(借鉴 jcode CompactionStats 精简版)。"""
        try:
            active = [m for m in messages if isinstance(m, dict) and m.get("role") != SYSTEM_ROLE]
            has_summary = any(
                isinstance(m, dict) and isinstance(m.get("content"), str)
                and "Previous Conversation Summary" in m.get("content", "")
                for m in messages
            )
            token_estimate = self.estimate_tokens(messages)
            usage = (token_estimate / self.token_budget) if self.token_budget > 0 else 0.0
            return CompactionStats(
                total_turns=len(messages),
                active_messages=len(active),
                has_summary=has_summary,
                token_estimate=token_estimate,
                context_usage=usage,
            )
        except Exception:  # noqa: BLE001
            log.exception("[compaction] stats 失败")
            return CompactionStats(
                total_turns=len(messages) if messages else 0,
                active_messages=0,
                has_summary=False,
                token_estimate=0,
                context_usage=0.0,
            )

    # ---------- 主入口 ----------
    def compact(
        self,
        messages: list[dict],
        llm_call: Optional[Callable[[str], str]] = None,
    ) -> tuple[list[dict], CompactionAction]:
        """主入口:有需要就压缩。

        参数:
          messages:  当前 LLM 输入 messages
          llm_call:  可选同步 LLM 调用入口,sig = (prompt: str) -> str。
                     - 传 None:只走 hard 硬压(不调 LLM,summary 段留 placeholder)
                     - 传 callable:80%+ 时尝试调它做 summary,失败 fallback 到 hard 硬压

        返回:(新 messages, 实际采取的动作)
          - 无需压缩 → (原 messages, None_)
          - 80%+:     调 llm_call 摘要旧 messages + 保留 recent_turns → Summarized
          - 95%:      紧急硬压 + 保留 min_turns → HardCompacted
          - 任何异常 → (原 messages, None_)  fail-open,不抛
        """
        try:
            need, action = self.needs_compaction(messages)
            if not need:
                return messages, CompactionAction.None_

            if action == CompactionAction.Summarized:
                if llm_call is None:
                    # 无 LLM 通道 → 升级到 hard 硬压兜底
                    log.info("[compaction] Summarized 触发但 llm_call=None,降级 HardCompacted")
                    return self._hard_compact(messages), CompactionAction.HardCompacted
                return self._summarize(messages, llm_call)

            # HardCompacted
            return self._hard_compact(messages), CompactionAction.HardCompacted
        except Exception:  # noqa: BLE001
            log.exception("[compaction] compact 失败,fail-open 返原 messages")
            return messages, CompactionAction.None_

    # ---------- soft 压缩(80%+) ----------
    def _summarize(
        self,
        messages: list[dict],
        llm_call: Callable[[str], str],
    ) -> tuple[list[dict], CompactionAction]:
        """软压:用 LLM 摘要旧 messages,保留 recent_turns。

        算法(借鉴 jcode):
          1. 分离 system 段(永保留)+ recent_turns user/assistant(永保留)
          2. 中间的"待压消息"拼成 transcript
          3. 拼 SUMMARY_PROMPT 调 llm_call
          4. 在 system 段后插入一条 summary system(以 '## Previous Conversation Summary' 标识)
          5. 返回 [system..., summary, recent...]
        """
        recent_n = self._configured_recent_turns()
        # 取最近 N 轮(以 user/assistant 为粒度)
        active = [m for m in messages if isinstance(m, dict) and m.get("role") != SYSTEM_ROLE]
        if len(active) <= recent_n:
            # 不到压缩线,fallback 到硬压兜底
            return self._hard_compact(messages), CompactionAction.HardCompacted

        # 分离 system / 待压 / 保留
        system_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == SYSTEM_ROLE]
        recent_msgs = active[-recent_n:]
        to_compact = active[:-recent_n]

        # 拼 transcript
        transcript = self._build_transcript(to_compact)
        prompt = f"{transcript}\n\n---\n\n{SUMMARY_PROMPT}"

        try:
            summary_text = llm_call(prompt) or ""
        except Exception:  # noqa: BLE001
            log.exception("[compaction] llm_call 失败,降级 HardCompacted")
            return self._hard_compact(messages), CompactionAction.HardCompacted

        summary_text = (summary_text or "").strip()
        if not summary_text:
            # 空摘要等于没压 → 降级硬压
            log.warning("[compaction] llm_call 返空摘要,降级 HardCompacted")
            return self._hard_compact(messages), CompactionAction.HardCompacted

        # 截断到 SUMMARY_MAX_CHARS
        if len(summary_text) > SUMMARY_MAX_CHARS:
            summary_text = summary_text[:SUMMARY_MAX_CHARS] + "\n[summary truncated]"

        summary_block = (
            "## Previous Conversation Summary\n\n"
            f"{summary_text}\n\n"
            "---\n\n"
        )
        new_msgs: list[dict] = []
        new_msgs.extend(system_msgs)
        new_msgs.append({"role": SYSTEM_ROLE, "content": summary_block})
        new_msgs.extend(recent_msgs)
        log.info(
            "[compaction] Summarized: %d → %d msgs (recent_kept=%d, summary_chars=%d)",
            len(messages), len(new_msgs), recent_n, len(summary_text),
        )
        return new_msgs, CompactionAction.Summarized

    def _build_transcript(self, msgs: list[dict]) -> str:
        """把 messages 拼成可读 transcript(给 LLM 看)。"""
        parts: list[str] = []
        for m in msgs:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, str):
                parts.append(f"**{role.capitalize()}:**\n{content}\n")
            elif isinstance(content, list):
                # 多模态:只取文本 block + 图片标记
                segs: list[str] = []
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        segs.append(b.get("text", ""))
                    elif b.get("type") in ("image", "image_url"):
                        segs.append("[image]")
                    else:
                        segs.append(f"[{b.get('type', 'block')}]")
                parts.append(f"**{role.capitalize()}:**\n" + "\n".join(segs) + "\n")
            else:
                parts.append(f"**{role.capitalize()}:**\n{str(content)}\n")
        return "\n".join(parts)

    # ---------- hard 压缩(95%+) ----------
    def _hard_compact(self, messages: list[dict]) -> list[dict]:
        """硬压:直接丢旧消息,只保留 min_turns 轮 + 所有 system 段。

        不调 LLM,只截掉中段。
        """
        system_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == SYSTEM_ROLE]
        active = [m for m in messages if isinstance(m, dict) and m.get("role") != SYSTEM_ROLE]
        keep = active[-self.min_turns:] if len(active) > self.min_turns else active

        new_msgs: list[dict] = []
        new_msgs.extend(system_msgs)
        if len(active) > self.min_turns:
            # 插入一条标记:之前的内容被硬压丢了
            drop_count = len(active) - self.min_turns
            new_msgs.append({
                "role": SYSTEM_ROLE,
                "content": (
                    f"[compaction] {drop_count} earlier turns dropped "
                    "(context emergency, hard compact)"
                ),
            })
        new_msgs.extend(keep)
        log.info(
            "[compaction] HardCompacted: %d → %d msgs (dropped %d turns)",
            len(messages), len(new_msgs), max(0, len(active) - self.min_turns),
        )
        return new_msgs

    # ---------- 紧急截断(单条内容) ----------
    def emergency_truncate_tool_result(self, content: str) -> str:
        """> EMERGENCY_TOOL_RESULT_MAX_CHARS → 截断 + 加 [truncated]。

        用法:在 tool result 入 messages 前调(防止单个 tool result 把 context 撑爆)。
        """
        if not isinstance(content, str):
            return content  # type: ignore[return-value]
        if len(content) <= EMERGENCY_TOOL_RESULT_MAX_CHARS:
            return content
        keep = EMERGENCY_TOOL_RESULT_MAX_CHARS
        return content[:keep] + "\n[truncated]"

    def emergency_truncate_image(self, image_payload: dict) -> dict:
        """> EMERGENCY_IMAGE_MAX_CHARS → 截断 base64 data 字段。

        image_payload 形如 {'type': 'image', 'source': {'type': 'base64', 'data': '...'}}
        或 {'type': 'image_url', 'image_url': {'url': 'data:image/...;base64,...'}}
        """
        if not isinstance(image_payload, dict):
            return image_payload  # type: ignore[return-value]
        try:
            new_payload = dict(image_payload)  # 浅拷贝
            src = new_payload.get("source")
            if isinstance(src, dict):
                src2 = dict(src)
                data = src2.get("data", "")
                if isinstance(data, str) and len(data) > EMERGENCY_IMAGE_MAX_CHARS:
                    src2["data"] = data[:EMERGENCY_IMAGE_MAX_CHARS] + "...[truncated]"
                new_payload["source"] = src2
            else:
                # image_url 形式
                url_field = new_payload.get("image_url")
                if isinstance(url_field, dict):
                    url2 = dict(url_field)
                    url = url_field.get("url", "")
                    if isinstance(url, str) and len(url) > EMERGENCY_IMAGE_MAX_CHARS:
                        url2["url"] = url[:EMERGENCY_IMAGE_MAX_CHARS] + "...[truncated]"
                    new_payload["image_url"] = url2
            return new_payload
        except Exception:  # noqa: BLE001
            log.exception("[compaction] emergency_truncate_image 失败,返原")
            return image_payload