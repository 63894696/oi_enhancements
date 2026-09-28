# -*- coding: utf-8 -*-
"""prisirmp 引擎:SQLite FTS5 公众号文章索引,单例 + RLock + meta 持久化。

范式抄 prisir_fcontent/engine.py(2026-08-22),关键踩坑已规避:
1. FTS5 用普通表(非 external-content)→ text 列存截断原文, snippet() 才能取片段。
2. RLock + 真扫描放锁外 + _flush 自锁,enable 持锁扫描不死锁。
3. clear 走 DROP+重建 不用 DELETE(并发写不互锁)。
4. CJK 分词用应用层 tokenize(unigram + bigram + ASCII 词),与查询 to_match 同源。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
from typing import Any

from . import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))


def _default_db() -> str:
    """默认库路径。源码运行落模块目录;frozen 后续若加 PyInstaller 再走用户数据目录。"""
    return os.path.join(HERE, "prisirmp.db")


DEFAULT_DB = _default_db()


# Schema:meta(状态持久化)/ accounts(已解密账号)/ articles(公众号文章元数据)/
#         articles_fts(FTS5 虚拟表,普通非 external-content)。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
  account_id TEXT PRIMARY KEY,
  account_dir TEXT NOT NULL,
  decrypted_dir TEXT NOT NULL,
  last_decrypt INTEGER NOT NULL DEFAULT 0,
  message_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY,
  account_id TEXT NOT NULL,
  biz TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,
  publisher TEXT,
  desc TEXT,
  ts INTEGER NOT NULL,
  source INTEGER NOT NULL DEFAULT 0,
  thumburl TEXT,
  text_tok TEXT,
  text TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_ts ON articles(ts DESC);
CREATE INDEX IF NOT EXISTS idx_articles_biz ON articles(biz);
CREATE INDEX IF NOT EXISTS idx_articles_account ON articles(account_id);

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
  url UNINDEXED,
  title, desc, publisher, text_tok, text,
  tokenize='unicode61'
);
"""


def _highlight(snippet: str, query: str) -> str:
    """把 FTS5 snippet 里的查询词用 ** 包起来(前端转 <b>)。

    snippet 来自 text 列(可读原文),但 FTS5 MATCH 是分词串,所以这里按查询串
    直接在原文片段里做大小写不敏感子串高亮(中文整词、英文整词)。
    """
    if not snippet or not query:
        return snippet or ""
    import re
    q = query.strip()
    if not q:
        return snippet
    terms = sorted({t for t in re.split(r"\s+", q) if t}, key=len, reverse=True)
    out = snippet
    for t in terms:
        pat = re.compile(re.escape(t), re.IGNORECASE)
        out = pat.sub(lambda m: "**" + m.group(0) + "**", out)
    return out


class Mprecaller:
    """公众号文章索引引擎。单例,线程安全(RLock)。"""

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def shared(cls, db_path: str | None = None) -> "Mprecaller":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(db_path or DEFAULT_DB)
            return cls._instance

    @classmethod
    def _reset_singleton(cls) -> None:
        """仅供测试用:清掉单例,下一个 shared() 重建。"""
        with cls._lock:
            if cls._instance is not None:
                try:
                    cls._instance.close()
                except Exception:
                    pass
                cls._instance = None

    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        # RLock:enable 持锁做元数据变更时,内部 _flush 自锁可同线程重入。
        self._mu = threading.RLock()
        self._building = False
        self._indexed = 0
        self._last_index = 0
        # SQLite timeout=60:FTS5 大表并发写时不轻易 database is locked。
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=60)
        self.conn.row_factory = sqlite3.Row  # 让 row 支持 [] / keys()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ----- meta 持久化(重启不丢 last_index / wcdb_key) -----
    def _set_meta(self, k: str, v: str) -> None:
        with self._mu:
            self.conn.execute(
                "INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, v))
            self.conn.commit()

    def _get_meta(self, k: str, default: str = "") -> str:
        with self._mu:
            row = self.conn.execute(
                "SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return row["v"] if row else default

    # ----- 状态 -----
    def status(self) -> dict[str, Any]:
        with self._mu:
            cnt = self.conn.execute(
                "SELECT COUNT(*) FROM articles").fetchone()[0]
            account_n = self.conn.execute(
                "SELECT COUNT(*) FROM accounts").fetchone()[0]
            accounts = [dict(r) for r in self.conn.execute(
                "SELECT account_id, account_dir, decrypted_dir, last_decrypt, "
                "message_count FROM accounts ORDER BY last_decrypt DESC").fetchall()]
            last_index = int(self._get_meta("last_index", "0") or "0")
            return {
                "ok": True,
                "enabled": cnt > 0 or self._building,
                "indexed_count": cnt,
                "account_count": account_n,
                "accounts": accounts,
                "building": self._building,
                "scanned": self._indexed,
                "last_index": last_index,
            }

    # ----- 账号管理 -----
    def upsert_account(self, account_id: str, account_dir: str,
                       decrypted_dir: str, message_count: int = 0) -> None:
        with self._mu:
            self.conn.execute(
                "INSERT OR REPLACE INTO accounts"
                "(account_id, account_dir, decrypted_dir, last_decrypt, message_count)"
                " VALUES(?,?,?,?,?)",
                (account_id, account_dir, decrypted_dir,
                 int(time.time()), message_count))
            self.conn.commit()

    def clear_accounts(self) -> None:
        with self._mu:
            self.conn.execute("DELETE FROM accounts")
            self.conn.commit()

    # ----- 文章入库(批量 + 幂等) -----
    def upsert_articles(self, articles: list[dict[str, Any]]) -> int:
        """articles: [{biz,title,url,publisher,desc,ts,source,thumburl,text,account_id}]。

        幂等:url 唯一键,重复 url 走 INSERT OR REPLACE。
        FTS5 全量重灌(规模 10k-100k 条量级够用)。
        返回成功入库条数(去重后)。
        """
        if not articles:
            return 0
        with self._mu:
            n = 0
            for a in articles:
                if not a.get("url") or not a.get("title"):
                    continue
                text = (a.get("text") or "")[:4096]
                try:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO articles"
                        "(account_id, biz, title, url, publisher, desc, ts, "
                        " source, thumburl, text_tok, text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (a.get("account_id", ""), a.get("biz", ""),
                         a["title"], a["url"], a.get("publisher", ""),
                         a.get("desc", ""), int(a.get("ts", 0)),
                         int(a.get("source", 0)), a.get("thumburl", ""),
                         "", text))
                    n += 1
                except sqlite3.IntegrityError:
                    continue
            # FTS 全量重灌(简单正确)
            self.conn.execute("DELETE FROM articles_fts")
            for a in articles:
                if not a.get("url") or not a.get("title"):
                    continue
                text = (a.get("text") or "")[:4096]
                toks = tokenize.tokenize(
                    " ".join([a.get("title", ""), a.get("desc") or "",
                              a.get("publisher") or "", text]))
                self.conn.execute(
                    "INSERT INTO articles_fts(url, title, desc, publisher, text_tok, text)"
                    " VALUES(?,?,?,?,?,?)",
                    (a["url"], a["title"], a.get("desc", ""),
                     a.get("publisher", ""), " ".join(toks), text))
            self.conn.commit()
            self._set_meta("last_index", str(int(time.time())))
            self._indexed = n
            self._last_index = int(time.time())
            return n

    # ----- 搜索 -----
    def search(self, query: str, limit: int = 30, offset: int = 0) -> dict[str, Any]:
        """返 {hits:[{url,title,publisher,ts,desc,source,biz,snippet}], total:N}。"""
        limit = max(1, min(int(limit or 30), 200))
        offset = max(0, int(offset or 0))
        with self._mu:
            match = tokenize.to_match(query)
            if match is None:
                cur = self.conn.execute(
                    "SELECT url, title, publisher, ts, desc, source, biz, '' "
                    "FROM articles ORDER BY ts DESC LIMIT ? OFFSET ?",
                    (limit, offset))
                hits = [self._row_to_hit(r, "") for r in cur.fetchall()]
                total = self.conn.execute(
                    "SELECT COUNT(*) FROM articles").fetchone()[0]
                return {"hits": hits, "total": total}
            # FTS5 列号(url UNINDEXED 是 0):title=1, desc=2, publisher=3,
            # text_tok=4, text=5。snippet 取 text 列(可读原文)。
            sql = (
                "SELECT a.url, a.title, a.publisher, a.ts, a.desc, a.source, "
                "       a.biz, snippet(articles_fts, 5, '', '', '…', 32) AS snip "
                "FROM articles_fts JOIN articles a ON a.url = articles_fts.url "
                "WHERE articles_fts MATCH ? "
                "ORDER BY rank LIMIT ? OFFSET ?")
            try:
                cur = self.conn.execute(sql, (match, limit, offset))
                rows = cur.fetchall()
            except sqlite3.OperationalError:
                rows = []
            hits = []
            for r in rows:
                snip = r["snip"] or ""
                hits.append(self._row_to_hit(r, snip))
            try:
                total = self.conn.execute(
                    "SELECT COUNT(*) FROM articles_fts WHERE articles_fts MATCH ?",
                    (match,)).fetchone()[0]
            except sqlite3.OperationalError:
                total = len(hits)
            return {"hits": hits, "total": total}

    @staticmethod
    def _row_to_hit(row, snippet: str) -> dict[str, Any]:
        """search 返回结果 → dict。row 是 sqlite3.Row(支持 r['col'])。"""
        return {
            "url": row["url"],
            "title": row["title"],
            "publisher": row["publisher"] or "",
            "ts": int(row["ts"] or 0),
            "desc": row["desc"] or "",
            "source": int(row["source"] or 0),
            "biz": row["biz"] or "",
            "snippet": snippet,
        }

    # ----- 清空 -----
    def clear_articles(self) -> None:
        """清空文章索引(保留账号配置 + meta)。DROP+重建 不用 DELETE。"""
        with self._mu:
            self.conn.execute("DROP TABLE IF EXISTS articles_fts")
            self.conn.execute("DELETE FROM articles")
            self.conn.executescript(_SCHEMA)
            self.conn.execute(
                "INSERT OR REPLACE INTO meta(k,v) VALUES('last_index', '0')")
            self.conn.commit()

    # ----- 关闭 -----
    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def __del__(self):
        self.close()