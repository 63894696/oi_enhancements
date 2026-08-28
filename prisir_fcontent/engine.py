# -*- coding: utf-8 -*-
"""Fcontent 引擎:SQLite FTS5 内容索引,独立可选模块(探囊外挂层)。

与 prisir_findex 解耦:独立库 fcontent.db、独立 enable/disable、逐目录授权。
红线:默认关、roots 必填(逐目录授权)、只存分词结果不存原文、不上云。
"""
import json, os, sqlite3, sys, threading, time

from . import extract, tokenize

HERE = os.path.dirname(os.path.abspath(__file__))


def _default_db() -> str:
    """默认库路径。frozen(PyInstaller)下 HERE=_MEIPASS 只读,改落用户数据目录
    (%USERPROFILE%/.local/share/prisir),与 keys.db 同处;源码运行仍落模块目录。"""
    if getattr(sys, "frozen", False):
        base = os.path.join(os.path.expanduser("~"), ".local", "share", "prisir")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "fcontent.db")
    return os.path.join(HERE, "fcontent.db")


DEFAULT_DB = _default_db()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  mtime INTEGER NOT NULL,
  size INTEGER NOT NULL DEFAULT 0,
  first_seen INTEGER NOT NULL DEFAULT 0,
  is_ocr INTEGER NOT NULL DEFAULT 0
);
-- 普通 FTS5 表(非 external-content):content_tok 存分词串(匹配用),text 存截断原文
-- (仅用于 snippet 取片段,不出本机、截断 512KB)。external-content 表(content='')的
-- snippet() 恒返 NULL(2026-08-22 实测),不能用。
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
  path UNINDEXED,
  content_tok,
  text,
  tokenize='unicode61'
);
"""

# 与 findex 保持一致的默认排除(系统/缓存目录)
_DEFAULT_EXCLUDE = {
    "windows", "program files", "program files (x86)", "programdata",
    "node_modules", ".git", "appdata", "$recycle.bin", "__pycache__",
    ".venv", "venv", "dist", "build",
}


def _highlight(snippet: str, query: str) -> str:
    """把 FTS5 snippet(截断原文)里的查询词用 ** 包起来(前端转 <b>)。

    snippet 来自 text 列(可读原文);但查询匹配的是分词串,故这里按查询串
    直接在原文片段里做大小写不敏感子串高亮(中文整词、英文整词)。"""
    if not snippet or not query:
        return snippet or ""
    import re
    q = query.strip()
    if not q:
        return snippet
    # 按长度降序,先长后短(避免短词抢匹配);转义正则特殊字符
    terms = sorted({t for t in re.split(r"\s+", q) if t}, key=len, reverse=True)
    out = snippet
    for t in terms:
        pat = re.compile(re.escape(t), re.IGNORECASE)
        out = pat.sub(lambda m: "**" + m.group(0) + "**", out)
    return out


class Fcontent:
    """内容索引引擎。线程安全(内部锁)。"""
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def shared(cls, db_path=None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(db_path or DEFAULT_DB)
            return cls._instance

    def __init__(self, db_path=DEFAULT_DB):
        self.db_path = db_path
        # RLock:enable 持锁做元数据操作时 _flush 可同线程重入(真扫描在锁外)。
        self._mu = threading.RLock()
        self._building = False
        self._scanned = 0
        self._last_scan = 0
        self._roots = []
        # timeout 大写:FTS5 大表操作/并发写时让锁等待更久(默认 5s 在高并发下易爆 locked)。
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=60)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self._migrate()  # 旧库补列(is_ocr 等)
        self.conn.commit()
        self._ocr = False  # 本轮 enable 是否开 OCR(扫描时透传 extract)
        self._load_state()  # 重启后从库读回 roots/ocr_on/last_scan(否则失忆但索引还在)

    def _load_state(self):
        """重启恢复:roots/ocr_on/last_scan 持久化在 meta 表(库文件),进程重启不丢。"""
        try:
            self.conn.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
            rows = dict(self.conn.execute("SELECT k,v FROM meta").fetchall())
            import json as _j
            if rows.get("roots"):
                self._roots = _j.loads(rows["roots"])
            if rows.get("ocr_on"):
                self._ocr = rows["ocr_on"] == "1"
            if rows.get("last_scan"):
                self._last_scan = int(rows["last_scan"])
        except Exception:  # noqa: BLE001
            pass

    def _save_state(self):
        """持久化 roots/ocr_on/last_scan 到 meta 表(调用方须持锁)。"""
        import json as _j
        try:
            self.conn.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
            self.conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('roots',?)", (_j.dumps(self._roots),))
            self.conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('ocr_on',?)", ("1" if self._ocr else "0",))
            self.conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('last_scan',?)", (str(self._last_scan),))
            self.conn.commit()
        except Exception:  # noqa: BLE001
            pass

    def _migrate(self):
        """轻量迁移:files 表缺列则 ALTER 补上(旧库升级不重建)。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(files)").fetchall()}
        if "is_ocr" not in cols:
            self.conn.execute("ALTER TABLE files ADD COLUMN is_ocr INTEGER NOT NULL DEFAULT 0")

    # ---- 状态 ----
    def status(self):
        with self._mu:
            cnt = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            ocr_ok, ocr_reason = extract.ocr_available()
            return {
                "ok": True,
                "enabled": cnt > 0 or self._building,
                "indexed_count": cnt,
                "building": self._building,
                "scanned": self._scanned,
                "last_scan": self._last_scan,
                "roots": list(self._roots),
                "ocr_on": self._ocr,  # 本轮索引是否开了 OCR
                # OCR 能力真探测:装了 rapidocr 则可用,否则诚实给安装指引
                "ocr": {"available": ocr_ok, "reason": ocr_reason,
                        "hint": None if ocr_ok else "pip install rapidocr_onnxruntime 后可用"},
            }

    # ---- 首扫 / 重建 ----
    def enable(self, roots, exclude=None, ocr=False):
        """同步首扫(阻塞)。roots=显式授权目录列表(必填,空则拒扫)。

        ocr=True 且能力可用才把图片(.png/.jpg/...)纳入索引;默认关。
        未装 rapidocr 而 ocr=True:不报错,图片跳过、status 里诚实上报。"""
        roots = [os.path.abspath(r) for r in (roots or []) if os.path.isdir(r)]
        if not roots:
            return {"ok": False, "error": "roots_required",
                    "hint": "内容索引需逐目录显式授权:请给 roots 目录列表(不做全盘)"}
        ocr_on = bool(ocr) and extract.ocr_available()[0]
        with self._mu:
            if self._building:
                return {"ok": False, "error": "already_building"}
            self._building = True
            self._scanned = 0
            self._ocr = ocr_on
            self._clear()  # 重建式:enable 前清空旧索引(与 findex 一致;增量是后续优化项)
        excl = {e.lower() for e in (exclude or [])} | _DEFAULT_EXCLUDE
        t0 = time.time()
        try:
            # 真扫描放锁外:_flush 内部自锁批量提交,扫描/解析不持锁,避免锁粒度过大。
            self._scan(roots, excl)
            self._roots = roots
            self._last_scan = int(time.time())
            with self._mu:
                self._save_state()  # 持久化 roots/ocr_on/last_scan(重启不丢)
            return {"ok": True, "scanned": self._scanned, "ocr": ocr_on,
                    "elapsed_s": round(time.time() - t0, 1)}
        finally:
            with self._mu:
                self._building = False

    def enable_async(self, roots, exclude=None, on_done=None, ocr=False):
        """后台线程首扫,立即返回。进度用 status() 轮询。"""
        if self._building:
            return {"ok": False, "error": "already_building"}

        def _run():
            r = self.enable(roots, exclude, ocr=ocr)
            if on_done:
                try:
                    on_done(r)
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "started": True}

    def _scan(self, roots, excl):
        ocr_on = self._ocr
        batch = []
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d.lower() not in excl]
                for fn in filenames:
                    fp = os.path.join(dirpath, fn)
                    ext = os.path.splitext(fn)[1].lower()
                    if not extract.supported(ext, ocr=ocr_on):
                        continue
                    text = extract.extract_text(fp, ocr=ocr_on)
                    if not text:
                        continue
                    try:
                        st = os.stat(fp)
                        mtime, size = int(st.st_mtime), st.st_size
                    except OSError:
                        continue
                    toks = tokenize.tokenize(text)
                    is_ocr = 1 if ext in extract._IMG_EXTS else 0
                    batch.append((fp, mtime, size, " ".join(toks), text, is_ocr))
                    self._scanned += 1
                    if len(batch) >= 200:
                        self._flush(batch)
                        batch.clear()
        if batch:
            self._flush(batch)

    def _flush(self, batch):
        with self._mu:
            for path, mtime, size, tokstr, text, is_ocr in batch:
                # 保 first_seen:INSERT OR IGNORE 补元数据,已存在只 UPDATE 可变字段
                self.conn.execute(
                    "INSERT OR IGNORE INTO files(path, mtime, size, first_seen, is_ocr) VALUES(?,?,?,?,?)",
                    (path, mtime, size, int(time.time()), is_ocr))
                self.conn.execute(
                    "UPDATE files SET mtime=?, size=?, is_ocr=? WHERE path=?", (mtime, size, is_ocr, path))
                # FTS(普通表,按 path 去重):先删旧再插新
                self.conn.execute("DELETE FROM files_fts WHERE path=?", (path,))
                self.conn.execute(
                    "INSERT INTO files_fts(path, content_tok, text) VALUES(?,?,?)",
                    (path, tokstr, text))
            self.conn.commit()

    # ---- 搜索 ----
    def search(self, query, limit=50, offset=0):
        """内容子串搜索,带匹配片段。回 {"hits":[{path,mtime,size,snippet}], "total":N}。"""
        match = tokenize.to_match(query)
        with self._mu:
            if match is None:
                # 空查询:按 mtime 倒序列已索引文件(无片段)
                cur = self.conn.execute(
                    "SELECT path, mtime, size, is_ocr FROM files ORDER BY mtime DESC LIMIT ? OFFSET ?",
                    (int(limit), int(offset)))
                hits = [{"path": r[0], "mtime": r[1], "size": r[2], "snippet": "",
                         "is_ocr": bool(r[3])} for r in cur.fetchall()]
                total = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                return {"hits": hits, "total": total}
            # FTS5 snippet 取匹配片段:text 是第 2 列(截断原文,可读);** 包裹命中词。
            # content_tok 是分词串(unigram+bigram),snippet 取出来人读不顺,故 snippet 用 text 列。
            sql = (
                "SELECT f.path, f.mtime, f.size, f.is_ocr, "
                "snippet(files_fts, 2, '', '', '…', 40) AS snip "
                "FROM files_fts JOIN files f ON f.path = files_fts.path "
                "WHERE files_fts MATCH ? "
                "ORDER BY rank LIMIT ? OFFSET ?")
            try:
                cur = self.conn.execute(sql, (match, int(limit), int(offset)))
                rows = cur.fetchall()
            except sqlite3.OperationalError:
                rows = []
            hits = [{"path": r[0], "mtime": r[1], "size": r[2], "is_ocr": bool(r[3]),
                     "snippet": _highlight(r[4] or "", query)} for r in rows]
            try:
                total = self.conn.execute(
                    "SELECT COUNT(*) FROM files_fts WHERE files_fts MATCH ?", (match,)
                ).fetchone()[0]
            except sqlite3.OperationalError:
                total = len(hits)
            return {"hits": hits, "total": total}

    # ---- 关闭 / 清空 ----
    def _clear(self):
        """清空两表(调用方须持锁或在不竞争处调)。

        用 DROP+重建 而非 DELETE:FTS5 大表 DELETE 很重且与并发写互锁
        (2026-08-22 实测 enable 的 _clear 卡 database is locked);DROP 毫秒级绕锁。"""
        self.conn.execute("DROP TABLE IF EXISTS files_fts")
        self.conn.execute("DROP TABLE IF EXISTS files")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def disable(self):
        """清空内容索引(用户关闭功能)。"""
        with self._mu:
            self._clear()
            self._roots = []
            self._last_scan = 0
            self._scanned = 0
            self._ocr = False
            self._save_state()  # 持久化清空状态
            return {"ok": True}

    def close(self):
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    def __del__(self):
        self.close()
