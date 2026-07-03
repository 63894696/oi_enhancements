"""OI agent 持久记忆层 —— SQLite + 关键词 recall

设计原则:
- 零外部依赖(只依赖 Python 标准库 + OI 自带的 litellm)
- 4 层记忆(L0 identity / L1 essential / L2 on-demand / L3 deep-search),沿用 Peekaboo-W 思路
- 不引外部 embedding 库,关键词 token overlap 算相似度(够用即可)
- SQLite WAL 模式,跨进程安全
- 每个 OI chat() 自动 store 一次,下次自动 recall 注入 system prompt

API:
    from oi_memory import OIMemory
    mem = OIMemory()
    mem.store(layer='L2', title='...', content='...', tags=['oi-task'])
    hits = mem.recall('how to fix panel bug', n=5)
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
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

    def to_dict(self):
        return {**asdict(self), "tags_json": json.dumps(self.tags, ensure_ascii=False)}


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
                    token_freq TEXT NOT NULL DEFAULT ''
                )
                """
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)")
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
    ) -> int:
        if layer not in LAYERS:
            raise ValueError(f"layer 必须是 {LAYERS} 之一,got {layer!r}")
        tags = tags or []
        tf = self._token_freq(content + " " + title)
        with self._lock, self._conn() as c:
            # 去重:同一 layer + title 存在则更新 content
            if dedupe_title:
                row = c.execute(
                    "SELECT id FROM memories WHERE layer=? AND title=?",
                    (layer, title),
                ).fetchone()
                if row:
                    c.execute(
                        "UPDATE memories SET content=?, tags_json=?, token_freq=?, created_at=? WHERE id=?",
                        (content, json.dumps(tags, ensure_ascii=False), tf, time.time(), row["id"]),
                    )
                    c.commit()
                    return int(row["id"])
            cur = c.execute(
                "INSERT INTO memories(layer, title, content, tags_json, created_at, token_freq) VALUES (?,?,?,?,?,?)",
                (layer, title, content, json.dumps(tags, ensure_ascii=False), time.time(), tf),
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
    ) -> list[Memory]:
        """按 query 关键词 overlap 算分,返回 top-N memory"""
        if not query.strip():
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        layers = tuple(layers)
        with self._lock, self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM memories WHERE layer IN ({','.join('?' * len(layers))})",
                layers,
            ).fetchall()

        scored: list[tuple[float, Memory]] = []
        for row in rows:
            tf = json.loads(row["token_freq"]) if row["token_freq"] else {}
            mem_tokens = set(tf.keys())
            if not mem_tokens:
                continue
            overlap = q_tokens & mem_tokens
            # score = |overlap| / sqrt(|q| * |mem|),即 cosine-like 但无嵌入
            score = len(overlap) / ((len(q_tokens) ** 0.5) * (len(mem_tokens) ** 0.5))
            if score >= min_score:
                scored.append((score, Memory(
                    id=row["id"], layer=row["layer"], title=row["title"],
                    content=row["content"],
                    tags=json.loads(row["tags_json"] or "[]"),
                    created_at=row["created_at"],
                    access_count=row["access_count"],
                )))
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

    def list_by_layer(self, layer: str, limit: int = 20) -> list[Memory]:
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM memories WHERE layer=? ORDER BY created_at DESC LIMIT ?",
                (layer, limit),
            ).fetchall()
        return [Memory(
            id=r["id"], layer=r["layer"], title=r["title"], content=r["content"],
            tags=json.loads(r["tags_json"] or "[]"),
            created_at=r["created_at"], access_count=r["access_count"],
        ) for r in rows]

    def stats(self) -> dict:
        with self._lock, self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            by_layer = {
                layer: c.execute("SELECT COUNT(*) FROM memories WHERE layer=?", (layer,)).fetchone()[0]
                for layer in LAYERS
            }
            top_accessed = c.execute(
                "SELECT title, layer, access_count FROM memories ORDER BY access_count DESC LIMIT 5"
            ).fetchall()
        return {
            "total": total,
            "by_layer": by_layer,
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