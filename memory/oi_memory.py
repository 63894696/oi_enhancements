"""OI agent 持久记忆层 —— SQLite + 关键词 recall

设计原则:
- 零外部依赖(只依赖 Python 标准库 + OI 自带的 litellm)
- 4 层记忆(L0 identity / L1 essential / L2 on-demand / L3 deep-search),沿用 Peekaboo-W 思路
- 不引外部 embedding 库,关键词 token overlap 算相似度(够用即可)
- SQLite WAL 模式,跨进程安全
- 每个 OI chat() 自动 store 一次,下次自动 recall 注入 system prompt

v0.24 新增:
- namespace 字段(backward compatible ALTER TABLE)
- auto_promote():防 L0 永不退位 + L2/L3 高频条目自动升 L1
- _decay_score():recall 排序时乘时间衰减(半衰期 7 天)

v0.26 新增:
- quality_score 字段: 记忆质量权重(0-1)，参与 decay_score 计算
- decay_score 改为: quality_score * access_count * exp(-0.1 * age_days)
- status / depends_on_json / priority 3 列(backward compatible ALTER)
- store/recall/list_by_layer 加 status 过滤
- get_by_id / update_status / append_to_content 方法
- stats() 加 by_status 字段

API:
    from oi_memory import OIMemory
    mem = OIMemory()
    mem.store(layer='L2', title='...', content='...', tags=['oi-task'])
    hits = mem.recall('how to fix panel bug', n=5)
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable

# ============================================================
# 配置
# ============================================================
OI_HOME = Path(os.environ.get("OI_HOME", Path.home() / ".oi"))
DB_PATH = OI_HOME / "memory.db"

LAYERS = ("L0", "L1", "L2", "L3")
LAYER_DESC = {
    "L0": "identity — 谁是 OI、谁是用户、长期偏好",
    "L1": "essential — 关键事实(用户名、项目根、常用路径)",
    "L2": "on-demand — 短期工作记忆(任务上下文、最近工具调用)",
    "L3": "deep-search — 历史对话快照(全文 recall 检索)",
}

# v0.25:task queue 状态机
TASK_STATUSES = ("pending", "running", "done", "blocked", "cancelled")
TASK_DEFAULT_STATUS = "pending"
TASK_NAMESPACE_PREFIX = "tasks"  # task 记忆的 namespace
TASK_TITLE_PREFIX = "task:"  # 跟现有 task:panel-bug 约定一致

# 中文 + 英文 token 切分
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]+")


def tokenize(text: str) -> set[str]:
    """极简 tokenizer:英文按词 / 中文按字 + bigrams"""
    if not text:
        return set()
    tokens = set()
    for m in _TOKEN_RE.findall(text):
        tokens.add(m.lower())
        # 中文:额外加相邻 bigram
        if re.fullmatch(r"[一-鿿]+", m):
            for i in range(len(m) - 1):
                tokens.add(m[i : i + 2])
    return tokens


@dataclass
class Memory:
    id: int
    layer: str
    title: str
    content: str
    tags: list[str]
    created_at: float
    access_count: int
    namespace: str = ""  # v0.24:namespace 字段,backward compatible(默认空字符串)
    # v0.25:task queue 支撑字段
    status: str = ""  # 空字符串=非 task;否则 = pending/running/done/blocked/cancelled
    depends_on: list[int] = field(default_factory=list)  # 依赖的 task_id 列表
    priority: int = 0  # task 优先级,数字越大越优先(默认 0)
    quality_score: float = 1.0  # v0.26: 记忆质量权重(0-1)，参与 decay_score
    owner_agent: str = ""  # v0.44 P1-5: 拥有者 agent id(空=全局共享)

    def to_dict(self):
        return {
            **asdict(self),
            "tags_json": json.dumps(self.tags, ensure_ascii=False),
            "depends_on_json": json.dumps(self.depends_on),
        }


class OIMemory:
    """OI agent 持久记忆层 — SQLite + keyword recall"""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ---------- DB ----------
    def _init_db(self):
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    layer TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    last_access REAL,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    token_freq TEXT NOT NULL DEFAULT '',
                    namespace TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    depends_on_json TEXT NOT NULL DEFAULT '[]',
                    priority INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # v0.24 + v0.25:backward compatible ALTER(老 DB 没新列时补)——必须先于对应索引
            for alter_sql in [
                "ALTER TABLE memories ADD COLUMN namespace TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE memories ADD COLUMN depends_on_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE memories ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE memories ADD COLUMN quality_score REAL NOT NULL DEFAULT 1.0",
                # v0.44 P1-5:per-agent 记忆隔离 — 拥有者 agent id(空=全局共享)
                "ALTER TABLE memories ADD COLUMN owner_agent TEXT NOT NULL DEFAULT ''",
            ]:
                try:
                    c.execute(alter_sql)
                except sqlite3.OperationalError:
                    pass  # 列已存在
            c.execute("CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_memories_namespace ON memories(namespace)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_memories_namespace_status ON memories(namespace, status)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_memories_owner ON memories(owner_agent)")
            c.commit()

    @contextmanager
    def _conn(self):
        # check_same_thread=False + 自管 lock
        conn = sqlite3.connect(str(self.db_path), timeout=10, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ---------- 写入 ----------
    def store(
        self,
        layer: str = "L2",
        title: str = "",
        content: str = "",
        tags: list[str] | None = None,
        dedupe_title: bool = True,
        namespace: str = "",  # v0.24:optional namespace 字段
        status: str = "",  # v0.25:task 状态(空字符串=非 task)
        depends_on: list[int] | None = None,  # v0.25:task 依赖的 task_id 列表
        priority: int = 0,  # v0.25:task 优先级
        quality_score: float = 1.0,  # v0.26:记忆质量权重
        owner_agent: str = "",  # v0.44 P1-5:拥有者 agent id(空=全局共享,所有 agent 可见)
    ) -> int:
        if layer not in LAYERS:
            raise ValueError(f"layer 必须是 {LAYERS} 之一,got {layer!r}")
        if status and status not in TASK_STATUSES:
            raise ValueError(f"status 必须是 {TASK_STATUSES} 之一,got {status!r}")
        tags = tags or []
        depends_on_json = json.dumps(depends_on or [])
        tf = self._token_freq(content + " " + title)
        with self._lock, self._conn() as c:
            # 去重:同一 layer + title + namespace 存在则更新 content + task 字段
            if dedupe_title:
                row = c.execute(
                    "SELECT id FROM memories WHERE layer=? AND title=? AND namespace=?",
                    (layer, title, namespace),
                ).fetchone()
                if row:
                    c.execute(
                        """UPDATE memories SET content=?, tags_json=?, token_freq=?, created_at=?,
                              status=?, depends_on_json=?, priority=?, quality_score=?, owner_agent=? WHERE id=?""",
                        (content, json.dumps(tags, ensure_ascii=False), tf, time.time(),
                         status, depends_on_json, priority, quality_score, owner_agent, row["id"]),
                    )
                    c.commit()
                    return int(row["id"])
            cur = c.execute(
                """INSERT INTO memories(layer, title, content, tags_json, created_at, token_freq,
                       namespace, status, depends_on_json, priority, quality_score, owner_agent)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (layer, title, content, json.dumps(tags, ensure_ascii=False), time.time(), tf,
                 namespace, status, depends_on_json, priority, quality_score, owner_agent),
            )
            c.commit()
            return int(cur.lastrowid)

    # ---------- 检索 ----------
    def recall(
        self,
        query: str,
        n: int = 5,
        layers: Iterable[str] = ("L0", "L1", "L2", "L3"),
        min_score: float = 0.05,
        namespace: str | None = None,  # v0.24:None = 全部, str = 过滤
        status: str | None = None,  # v0.25:None = 不过滤, str = 只返该 status 的条目
        visible_to: str | None = None,  # v0.44 P1-5:调用方 agent id;None=不过滤(向后兼容)
    ) -> list[Memory]:
        """按 query 关键词 overlap 算分,返回 top-N memory

        v0.24:排序时乘 decay_score(半衰期 7 天)防老条目霸榜
        v0.25:加 status 过滤(task queue 用)
        v0.44 P1-5:visible_to 访问控制 — 传入 agent id 时,
                   只能看到 全局共享(owner_agent='') + 自己拥有(owner_agent=visible_to) 的记忆,
                   防 Federation 多 agent 互相偷看(B 报告确认的真实泄露面)。
        """
        if not query.strip():
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        layers = tuple(layers)
        where_clauses = [f"layer IN ({','.join('?' * len(layers))})"]
        params: list = list(layers)
        if namespace is not None:
            where_clauses.append("namespace=?")
            params.append(namespace)
        if status is not None:
            where_clauses.append("status=?")
            params.append(status)
        if visible_to is not None:
            where_clauses.append("(owner_agent='' OR owner_agent=?)")
            params.append(visible_to)
        with self._lock, self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM memories WHERE {' AND '.join(where_clauses)}",
                params,
            ).fetchall()

        scored: list[tuple[float, Memory]] = []
        for row in rows:
            tf = json.loads(row["token_freq"]) if row["token_freq"] else {}
            mem_tokens = set(tf.keys())
            if not mem_tokens:
                continue
            overlap = q_tokens & mem_tokens
            base_score = len(overlap) / ((len(q_tokens) ** 0.5) * (len(mem_tokens) ** 0.5))
            qs = row["quality_score"] if row["quality_score"] is not None else 1.0
            final_score = base_score * self._decay_score(row["access_count"], row["last_access"], qs)
            if base_score >= min_score:
                scored.append((final_score, self._row_to_memory(row)))
        scored.sort(key=lambda x: (-x[0], -x[1].created_at))
        hits = [m for _, m in scored[:n]]
        # 更新 access_count(异步,不阻塞返回)
        if hits:
            ids = [m.id for m in hits]
            with self._conn() as c:
                c.execute(
                    f"UPDATE memories SET access_count = access_count + 1, last_access = ? WHERE id IN ({','.join('?' * len(ids))})",
                    [time.time()] + ids,
                )
                c.commit()
        return hits

    def list_by_layer(
        self,
        layer: str,
        limit: int = 20,
        namespace: str | None = None,
        status: str | None = None,  # v0.25:task 过滤
        visible_to: str | None = None,  # v0.44 P1-5:调用方 agent id;None=不过滤
    ) -> list[Memory]:
        """列出某 layer 的记忆(默认按 created_at DESC)

        v0.24:可选 namespace 过滤
        v0.25:可选 status 过滤(task queue 用)
        v0.44 P1-5:可选 visible_to 访问控制(全局共享 + 自己拥有)
        """
        where_clauses = ["layer=?"]
        params: list = [layer]
        if namespace is not None:
            where_clauses.append("namespace=?")
            params.append(namespace)
        if status is not None:
            where_clauses.append("status=?")
            params.append(status)
        if visible_to is not None:
            where_clauses.append("(owner_agent='' OR owner_agent=?)")
            params.append(visible_to)
        params.append(limit)
        with self._lock, self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM memories WHERE {' AND '.join(where_clauses)} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    # ---------- v0.25:task queue 辅助方法 ----------
    def _row_to_memory(self, row) -> Memory:
        """从 sqlite3.Row 转 Memory dataclass,共享给 recall/list_by_layer/get_by_id"""
        return Memory(
            id=row["id"], layer=row["layer"], title=row["title"], content=row["content"],
            tags=json.loads(row["tags_json"] or "[]"),
            created_at=row["created_at"], access_count=row["access_count"],
            namespace=row["namespace"] or "",
            status=row["status"] or "",
            depends_on=json.loads(row["depends_on_json"] or "[]"),
            priority=row["priority"] or 0,
            quality_score=row["quality_score"] if row["quality_score"] is not None else 1.0,
            owner_agent=row["owner_agent"] if "owner_agent" in row.keys() else "",
        )

    def get_by_id(self, memory_id: int) -> Memory | None:
        """按 id 取记忆(task_queue 检查 depends_on 用)"""
        with self._lock, self._conn() as c:
            row = c.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not row:
            return None
        return self._row_to_memory(row)

    def update_status(self, memory_id: int, status: str):
        """更新 status(task_queue 状态机用)"""
        if status not in TASK_STATUSES:
            raise ValueError(f"status 必须是 {TASK_STATUSES} 之一,got {status!r}")
        with self._lock, self._conn() as c:
            c.execute("UPDATE memories SET status=? WHERE id=?", (status, memory_id))
            c.commit()

    def append_to_content(self, memory_id: int, suffix: str):
        """追加内容到 content(mark_done 时记录 result 用)"""
        with self._lock, self._conn() as c:
            row = c.execute("SELECT content FROM memories WHERE id=?", (memory_id,)).fetchone()
            if not row:
                return False
            new_content = (row["content"] + "\n\n" + suffix) if row["content"] else suffix
            c.execute(
                "UPDATE memories SET content=?, last_access=? WHERE id=?",
                (new_content, time.time(), memory_id),
            )
            c.commit()
            return True

    # ---------- v0.24 tier 自动迁移 + decay ----------
    def _decay_score(
        self,
        access_count: int,
        last_access: float | None,
        quality_score: float = 1.0,
    ) -> float:
        """访问次数 × 质量 × 时间衰减因子(防老条目霸榜)

        公式:score = quality_score * access_count * exp(-0.1 * age_days)
        半衰期 ≈ 7 天(0.1 = ln(2) / 7)
        v0.26: 加入 quality_score 权重，低质量条目衰减更快
        """
        if access_count <= 0:
            return 0.0
        if not last_access:
            return quality_score * float(access_count)  # 从未被访问的不衰减
        age_days = (time.time() - last_access) / 86400.0
        decay = math.exp(-0.1 * age_days)
        return quality_score * access_count * decay

    def auto_promote(
        self,
        threshold_access_count: int = 50,
        max_age_hours: float = 168.0,  # 7 天
        dry_run: bool = False,
    ) -> dict:
        """v0.24:自动 tier 迁移,防 L0 永不退位

        规则:
        - L2/L3 → L1:access_count >= threshold 且 last_access 在 max_age_hours 内(高频 + 最近活跃)
        - L0 → L1:last_access 超 max_age_hours(冷记忆自动退位)或 access_count 远高于阈值(霸榜自动降级)
        - L1 → L2:last_access 超 max_age_hours × 2(essential 也可能 stale)

        Returns:
            {"promoted_to_l1": N, "demoted_from_l0": M, "demoted_from_l1": K, "dry_run": bool}
        """
        cutoff = time.time() - max_age_hours * 3600.0
        stale_cutoff = time.time() - (max_age_hours * 2) * 3600.0
        result = {"promoted_to_l1": 0, "demoted_from_l0": 0, "demoted_from_l1": 0, "dry_run": dry_run}

        with self._lock, self._conn() as c:
            # L2/L3 → L1(高频 + 最近)
            cur = c.execute(
                """
                UPDATE memories SET layer = 'L1'
                WHERE layer IN ('L2', 'L3')
                  AND access_count >= ?
                  AND last_access >= ?
                """,
                (threshold_access_count, cutoff),
            )
            result["promoted_to_l1"] = cur.rowcount

            # L0 → L1(冷记忆退位 + 霸榜降级)
            cur = c.execute(
                """
                UPDATE memories SET layer = 'L1'
                WHERE layer = 'L0'
                  AND (last_access < ? OR (last_access IS NOT NULL AND access_count >= ? * 3))
                """,
                (cutoff, threshold_access_count),
            )
            result["demoted_from_l0"] = cur.rowcount

            # L1 → L2(essential 长期未访问)
            cur = c.execute(
                """
                UPDATE memories SET layer = 'L2'
                WHERE layer = 'L1'
                  AND last_access IS NOT NULL
                  AND last_access < ?
                """,
                (stale_cutoff,),
            )
            result["demoted_from_l1"] = cur.rowcount

            if not dry_run:
                c.commit()
            else:
                # dry_run:rollback(但 SQLite 没有跨语句 savepoint,直接不开 commit)
                pass

        return result

    def stats(self) -> dict:
        with self._lock, self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            by_layer = {
                layer: c.execute("SELECT COUNT(*) FROM memories WHERE layer=?", (layer,)).fetchone()[0]
                for layer in LAYERS
            }
            # v0.25:by_status(task queue 状态分布)
            by_status = {
                s: c.execute("SELECT COUNT(*) FROM memories WHERE status=?", (s,)).fetchone()[0]
                for s in TASK_STATUSES
            }
            top_accessed = c.execute(
                "SELECT title, layer, access_count FROM memories ORDER BY access_count DESC LIMIT 5"
            ).fetchall()
        return {
            "total": total,
            "by_layer": by_layer,
            "by_status": by_status,
            "top_accessed": [dict(r) for r in top_accessed],
            "db_path": str(self.db_path),
        }

    def forget(self, memory_id: int) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            c.commit()
            return cur.rowcount > 0

    # ---------- 工具 ----------
    @staticmethod
    def _token_freq(text: str) -> str:
        """存 token→freq 的 JSON,recall 时用"""
        tf = {}
        for t in tokenize(text):
            tf[t] = tf.get(t, 0) + 1
        # 截断到高频 top 200,避免 sqlite 列过大
        top = sorted(tf.items(), key=lambda x: -x[1])[:200]
        return json.dumps(dict(top), ensure_ascii=False)


if __name__ == "__main__":
    # Smoke
    import sys
    db = OIMemory()
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(db.stats(), indent=2, ensure_ascii=False))
    else:
        print("Usage: python oi_memory.py stats")