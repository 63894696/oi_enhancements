"""P1-Instincts(2026-10-02)— JSONL 置信度召回层(借鉴 ECC continuous-learning-v2)。

设计原则:
- 起点低(0.3),被满意累加,被拒绝衰减
- 阈值 0.5 才自动注入 build_messages
- 召回:token overlap 关键词相似度(沿用 OIMemory.tokenize)+ confidence 权重
- 存储:JSONL 便于追加/审计/跨 harness 共享,不引 embedding 库
- 失败 fallback:任何 IO / 解析异常 → 静默返空 list / 默认值,不抛

存储路径:`~/.prisIrAI/instincts.jsonl`(Windows = C:\\Users\\Administrator\\.prisIrAI\\instincts.jsonl)。

完整设计:C:\\Users\\Administrator\\.claude\\plans\\stateless-launching-locket.md(P1-Instincts 段)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

# 让 from memory.oi_memory import tokenize 可用(测试在 memory/tests/)
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from oi_memory import tokenize  # noqa: E402

__all__ = [
    "Instinct",
    "InstinctStore",
    "record_instinct",
    "DEFAULT_INSTINCTS_ENABLED",
    "DEFAULT_INSTINCTS_CONFIDENCE_THRESHOLD",
    "DEFAULT_INSTINCTS_MAX_INJECT",
    "DEFAULT_INSTINCTS_MAX_CHARS",
]

log = logging.getLogger("memory.instincts")

# ---------------------------------------------------------------------------
# 常量 / 配置项
# ---------------------------------------------------------------------------

DEFAULT_INSTINCTS_ENABLED: bool = True
DEFAULT_INSTINCTS_CONFIDENCE_THRESHOLD: float = 0.5
DEFAULT_INSTINCTS_MAX_INJECT: int = 6
DEFAULT_INSTINCTS_MAX_CHARS: int = 3000
DEFAULT_INSTINCT_CONFIDENCE_START: float = 0.3
INSTINCT_CONFIDENCE_STEP_UP: float = 0.1     # success=True 加
INSTINCT_CONFIDENCE_STEP_DOWN: float = 0.05  # success=False 减
INSTINCT_CONFIDENCE_MAX: float = 0.95
INSTINCT_CONFIDENCE_MIN_DELETE: float = 0.1  # 跌破此阈值自动删

INSTINCT_CATEGORIES: tuple[str, ...] = (
    "user_pref",
    "code_style",
    "tool_usage",
    "self_learning",
)


# ---------------------------------------------------------------------------
# 极简 yaml scalar 读取(不引 PyYAML,不复用 rules.py)
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
            except ValueError:
                return default
        return v.strip()
    return default


def _instincts_enabled() -> bool:
    val = _read_yaml_scalar("instincts_enabled", DEFAULT_INSTINCTS_ENABLED)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    return DEFAULT_INSTINCTS_ENABLED


def _instincts_confidence_threshold() -> float:
    val = _read_yaml_scalar("instincts_confidence_threshold",
                            DEFAULT_INSTINCTS_CONFIDENCE_THRESHOLD)
    try:
        n = float(val)  # type: ignore[arg-type]
        if 0.0 <= n <= 1.0:
            return n
    except (TypeError, ValueError):
        pass
    return DEFAULT_INSTINCTS_CONFIDENCE_THRESHOLD


def _instincts_max_inject() -> int:
    val = _read_yaml_scalar("instincts_max_inject", DEFAULT_INSTINCTS_MAX_INJECT)
    try:
        n = int(val)  # type: ignore[arg-type]
        if n >= 1:
            return n
    except (TypeError, ValueError):
        pass
    return DEFAULT_INSTINCTS_MAX_INJECT


def _instincts_relevance_ranking() -> bool:
    val = _read_yaml_scalar("instincts_relevance_ranking", True)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    return True


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class Instinct:
    """单条 Instinct(置信度召回层的基本单元)。"""
    id: str                              # UUID4 hex
    pattern: str                         # 一句话模式(中文/英文均可)
    category: str = "self_learning"      # user_pref / code_style / tool_usage / self_learning
    confidence: float = DEFAULT_INSTINCT_CONFIDENCE_START
    applied_count: int = 0
    success_count: int = 0
    last_used_at: float = 0.0
    created_at: float = 0.0
    source_session: str = ""
    tags: list[str] = field(default_factory=list)
    pinned: bool = False                 # 钉死 → reinforce 不衰减(高级)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> "Instinct | None":
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(d, dict):
            return None
        try:
            return cls(
                id=str(d.get("id") or ""),
                pattern=str(d.get("pattern") or ""),
                category=str(d.get("category") or "self_learning"),
                confidence=float(d.get("confidence",
                                       DEFAULT_INSTINCT_CONFIDENCE_START)),
                applied_count=int(d.get("applied_count") or 0),
                success_count=int(d.get("success_count") or 0),
                last_used_at=float(d.get("last_used_at") or 0.0),
                created_at=float(d.get("created_at") or 0.0),
                source_session=str(d.get("source_session") or ""),
                tags=list(d.get("tags") or []),
                pinned=bool(d.get("pinned") or False),
            )
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# JSONL 存储
# ---------------------------------------------------------------------------


class InstinctStore:
    """JSONL 存储:每行一条 Instinct(append-only)。

    内部维护 path 文件的读写:
    - `add` 追加单条(append-only, 同 id 去重)
    - `reinforce / forget / pin` 整体重写(read-modify-write)
    - `recall` 全文件读 → tokenize → 排序
    """
    DEFAULT_PATH: Path = Path.home() / ".prisIrAI" / "instincts.jsonl"

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            self.path: Path = self.DEFAULT_PATH
        else:
            self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            log.warning("[instincts] mkdir 失败: %s", self.path.parent)

    # ---------- 工具 ----------
    def _read_all(self) -> list[Instinct]:
        """读全文件,跳过损坏行。IO 异常 → 空 list。"""
        try:
            if not self.path.is_file():
                return []
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:  # noqa: BLE001
            log.warning("[instincts] read %s 失败: %s", self.path, e)
            return []
        out: list[Instinct] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            ins = Instinct.from_json(line)
            if ins is None:
                continue  # 损坏行静默跳过(不抛)
            out.append(ins)
        return out

    def _rewrite_all(self, items: list[Instinct]) -> bool:
        """整体重写。IO 异常 → False。"""
        try:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for ins in items:
                    f.write(ins.to_json() + "\n")
            os.replace(tmp, self.path)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("[instincts] rewrite %s 失败: %s", self.path, e)
            return False

    # ---------- API ----------
    def is_enabled(self) -> bool:
        """读 yaml 的 instincts_enabled(默认 True)。"""
        try:
            return _instincts_enabled()
        except Exception:  # noqa: BLE001
            return DEFAULT_INSTINCTS_ENABLED

    def add(self, instinct: Instinct) -> bool:
        """追加一条。已存在同 id → 覆盖追加(去重)。

        IO 异常 → False,不抛。"""
        try:
            # 先读全量,过滤掉同 id
            existing = [x for x in self._read_all() if x.id != instinct.id]
            existing.append(instinct)
            return self._rewrite_all(existing)
        except Exception as e:  # noqa: BLE001
            log.warning("[instincts] add 失败: %s", e)
            return False

    def get(self, inst_id: str) -> Instinct | None:
        """按 id 取一条,不存在 → None。"""
        try:
            for x in self._read_all():
                if x.id == inst_id:
                    return x
            return None
        except Exception:  # noqa: BLE001
            return None

    def reinforce(self, inst_id: str, success: bool) -> bool:
        """反馈累积。

        success=True  → confidence += 0.1(封顶 0.95)
        success=False → confidence -= 0.05(下限 0.05,pinned 不衰减)
        confidence < 0.1 → 自动从库中删除

        IO 异常 / 不存在 → False。"""
        try:
            items = self._read_all()
            keep: list[Instinct] = []
            target: Instinct | None = None
            for ins in items:
                if ins.id != inst_id:
                    keep.append(ins)
                    continue
                target = ins
                # pinned:保护不被衰减
                if success:
                    ins.confidence = min(INSTINCT_CONFIDENCE_MAX,
                                         ins.confidence + INSTINCT_CONFIDENCE_STEP_UP)
                    ins.applied_count += 1
                    ins.success_count += 1
                else:
                    if ins.pinned:
                        # pinned 只更新计数,不改 confidence
                        ins.applied_count += 1
                    else:
                        ins.confidence = max(0.0,
                                             ins.confidence - INSTINCT_CONFIDENCE_STEP_DOWN)
                        ins.applied_count += 1
                ins.last_used_at = time.time()
                if ins.confidence < INSTINCT_CONFIDENCE_MIN_DELETE and not ins.pinned:
                    # 自动删除:不放进 keep
                    continue
                keep.append(ins)
            if target is None:
                return False
            return self._rewrite_all(keep)
        except Exception as e:  # noqa: BLE001
            log.warning("[instincts] reinforce 失败: %s", e)
            return False

    def forget(self, inst_id: str) -> bool:
        """人工删除。id 不存在 → False。"""
        try:
            items = self._read_all()
            keep = [x for x in items if x.id != inst_id]
            if len(keep) == len(items):
                return False
            return self._rewrite_all(keep)
        except Exception as e:  # noqa: BLE001
            log.warning("[instincts] forget 失败: %s", e)
            return False

    def pin(self, inst_id: str) -> bool:
        """钉死某条,pinned=True 后 reinforce 不衰减。"""
        try:
            items = self._read_all()
            changed = False
            for ins in items:
                if ins.id == inst_id:
                    if not ins.pinned:
                        ins.pinned = True
                        changed = True
                    break
            if not changed:
                return False
            return self._rewrite_all(items)
        except Exception as e:  # noqa: BLE001
            log.warning("[instincts] pin 失败: %s", e)
            return False

    def unpin(self, inst_id: str) -> bool:
        """解除钉死。"""
        try:
            items = self._read_all()
            changed = False
            for ins in items:
                if ins.id == inst_id:
                    if ins.pinned:
                        ins.pinned = False
                        changed = True
                    break
            if not changed:
                return False
            return self._rewrite_all(items)
        except Exception as e:  # noqa: BLE001
            log.warning("[instincts] unpin 失败: %s", e)
            return False

    def recall(self, query: str, top_n: int | None = None) -> list[Instinct]:
        """按 query 关键词 overlap 排序,confidence >= 门槛过滤,返 top_n 条。

        排序: token overlap 数(按 q_tokens 与 pattern+tags 的 tokenize 算)
              × confidence 权重。
        配置项 instincts_relevance_ranking=False → 仅按 confidence DESC。
        """
        try:
            threshold = _instincts_confidence_threshold()
            if top_n is None:
                top_n = _instincts_max_inject()
            top_n = max(1, int(top_n))
            relevance_on = _instincts_relevance_ranking()

            items = self._read_all()
            # 门槛过滤
            eligible = [x for x in items if x.confidence >= threshold]
            if not eligible:
                return []
            if not query.strip():
                # 无 query → 仅按 confidence DESC + applied_count DESC
                eligible.sort(key=lambda x: (-x.confidence, -x.applied_count,
                                             -x.last_used_at))
                return eligible[:top_n]
            q_tokens = tokenize(query)
            if not q_tokens:
                eligible.sort(key=lambda x: (-x.confidence, -x.applied_count,
                                             -x.last_used_at))
                return eligible[:top_n]

            scored: list[tuple[float, Instinct]] = []
            for ins in eligible:
                pat_tokens = tokenize(ins.pattern)
                tag_tokens: set[str] = set()
                for t in ins.tags:
                    tag_tokens |= tokenize(t)
                mem_tokens = pat_tokens | tag_tokens
                if not mem_tokens:
                    continue
                overlap = q_tokens & mem_tokens
                overlap_score = len(overlap)
                if relevance_on:
                    # cosine-like + confidence 加权
                    base = overlap_score / (
                        (len(q_tokens) ** 0.5) * (len(mem_tokens) ** 0.5)
                    )
                    score = base * ins.confidence
                else:
                    score = float(overlap_score) * ins.confidence
                scored.append((score, ins))
            scored.sort(key=lambda x: (-x[0], -x[1].confidence, -x[1].last_used_at))
            return [ins for _, ins in scored[:top_n]]
        except Exception as e:  # noqa: BLE001
            log.warning("[instincts] recall 失败: %s", e)
            return []

    def stats(self) -> dict:
        """全库统计:总数 / 平均置信度 / 按 category 分组 / pinned 数。"""
        empty = {
            "total": 0,
            "avg_confidence": 0.0,
            "by_category": {c: 0 for c in INSTINCT_CATEGORIES},
            "pinned": 0,
            "above_threshold": 0,
            "path": str(self.path),
        }
        try:
            items = self._read_all()
        except Exception:  # noqa: BLE001
            return empty
        if not items:
            return {**empty, "path": str(self.path)}
        total = len(items)
        avg_conf = sum(x.confidence for x in items) / total
        by_cat: dict[str, int] = {c: 0 for c in INSTINCT_CATEGORIES}
        for x in items:
            cat = x.category if x.category in INSTINCT_CATEGORIES else "self_learning"
            by_cat[cat] = by_cat.get(cat, 0) + 1
        threshold = _instincts_confidence_threshold()
        return {
            "total": total,
            "avg_confidence": round(avg_conf, 4),
            "by_category": by_cat,
            "pinned": sum(1 for x in items if x.pinned),
            "above_threshold": sum(1 for x in items if x.confidence >= threshold),
            "path": str(self.path),
        }

    def list_all(self) -> list[Instinct]:
        """列出全部(给 CLI / 调试用)。"""
        try:
            return self._read_all()
        except Exception:  # noqa: BLE001
            return []

    # ---------- 格式化 ----------
    def format_for_prompt(self, hits: Iterable[Instinct]) -> str:
        """把 recall 命中序列化成 user prompt 段。

        格式:
          [Instincts — 置信度召回]
          1. [0.65] 回答必须中文
          2. [0.55] 写注释要写中文
          [End instincts]

        max_chars 超 DEFAULT_INSTINCTS_MAX_CHARS → 截断 + 追加 [truncated]。
        """
        items = list(hits)
        if not items:
            return ""
        lines: list[str] = ["[Instincts — 置信度召回]"]
        for i, ins in enumerate(items, 1):
            conf = f"{ins.confidence:.2f}"
            lines.append(f"  {i}. [{conf}] {ins.pattern}")
        lines.append("[End instincts]")
        text = "\n".join(lines)
        if len(text) > DEFAULT_INSTINCTS_MAX_CHARS:
            text = text[:DEFAULT_INSTINCTS_MAX_CHARS].rstrip() + "\n[truncated]"
        return text


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------


def record_instinct(
    pattern: str,
    category: str = "self_learning",
    source_session: str = "",
    tags: tuple[str, ...] | list[str] = (),
    inst_id: str | None = None,
    path: Path | str | None = None,
) -> str:
    """便捷入口:会话结束时调用,confidence=0.3 起步。

    Returns:
        新建/覆盖的 instinct id(UUID4 hex)。
    """
    new_id = inst_id or uuid.uuid4().hex
    ins = Instinct(
        id=new_id,
        pattern=pattern,
        category=category if category in INSTINCT_CATEGORIES else "self_learning",
        confidence=DEFAULT_INSTINCT_CONFIDENCE_START,
        created_at=time.time(),
        source_session=source_session,
        tags=list(tags),
    )
    store = InstinctStore(path=path)
    store.add(ins)
    return new_id


# ---------------------------------------------------------------------------
# CLI 入口:`python -m memory.instincts list|forget <id>|pin <id>|stats`
# ---------------------------------------------------------------------------


def _cli(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python -m memory.instincts <list|forget <id>|pin <id>|unpin <id>|stats|recall <query>>")
        return 1
    cmd = argv[1]
    store = InstinctStore()
    if cmd == "list":
        items = store.list_all()
        for ins in items:
            pin_tag = " [PINNED]" if ins.pinned else ""
            print(f"  [{ins.confidence:.2f}] {ins.id}{pin_tag} ({ins.category}) {ins.pattern}")
        print(f"-- {len(items)} total --")
        return 0
    if cmd == "stats":
        import json as _json
        print(_json.dumps(store.stats(), indent=2, ensure_ascii=False))
        return 0
    if cmd == "forget" and len(argv) >= 3:
        ok = store.forget(argv[2])
        print("forget ok" if ok else "forget: id not found")
        return 0 if ok else 2
    if cmd == "pin" and len(argv) >= 3:
        ok = store.pin(argv[2])
        print("pin ok" if ok else "pin: id not found")
        return 0 if ok else 2
    if cmd == "unpin" and len(argv) >= 3:
        ok = store.unpin(argv[2])
        print("unpin ok" if ok else "unpin: id not found")
        return 0 if ok else 2
    if cmd == "recall" and len(argv) >= 3:
        hits = store.recall(" ".join(argv[2:]))
        print(store.format_for_prompt(hits))
        return 0
    print(f"Unknown command: {cmd}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli(sys.argv))
