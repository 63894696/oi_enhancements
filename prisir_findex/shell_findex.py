# -*- coding: utf-8 -*-
"""Prisir Findex — 本机文件搜索引擎的 Python ctypes 壳。

自建全盘文件名/路径索引(类 Everything),不依赖外部 es.exe。
红线:只存元数据(路径/名/目录/扩展名/大小/修改时间),不读文件内容;
默认不扫盘,显式 enable/build 才扫。

用法:
  from shell_findex import Findex
  fx = Findex()                      # 开/建库(不扫盘)
  fx.enable([r"C:\\Users"])          # 首扫建索引(同步,调用方放线程)
  fx.search("报告")                   # 子串搜索,按 mtime 倒序
  fx.status()                        # {enabled, indexed_count, ...}
  fx.disable()                       # 清空索引
"""
import ctypes, json, os, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
DLL = os.path.join(HERE, "target", "release", "prisir_findex.dll")
DEFAULT_DB = os.path.join(HERE, "findex.db")


class Findex:
    """Rust findex 引擎封装。线程安全(引擎内部 Mutex)。"""
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def shared(cls, db_path=None):
        """进程内单例(oiagent_web 多路由共用)。"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(db_path or DEFAULT_DB)
            return cls._instance

    def __init__(self, db_path=DEFAULT_DB):
        if not os.path.exists(DLL):
            raise RuntimeError(f"未编译 prisir_findex.dll: {DLL}")
        lib = ctypes.CDLL(DLL)
        lib.findex_open.restype = ctypes.c_void_p
        lib.findex_open.argtypes = [ctypes.c_char_p]
        lib.findex_build.restype = ctypes.c_void_p
        lib.findex_build.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.findex_query.restype = ctypes.c_void_p
        lib.findex_query.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
        lib.findex_status.restype = ctypes.c_void_p
        lib.findex_status.argtypes = [ctypes.c_void_p]
        lib.findex_clear.restype = ctypes.c_void_p
        lib.findex_clear.argtypes = [ctypes.c_void_p]
        lib.findex_free.argtypes = [ctypes.c_void_p]
        lib.findex_free_string.argtypes = [ctypes.c_void_p]
        self.lib = lib
        self.db_path = db_path
        self.h = lib.findex_open(db_path.encode())
        if not self.h:
            raise RuntimeError(f"findex_open 失败: {db_path}")
        self._build_thread = None

    # ---- 内部:释放返回的 C 字符串并解析 JSON ----
    def _json(self, ptr):
        if not ptr:
            return {"ok": False, "error": "null_response"}
        s = ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8")
        self.lib.findex_free_string(ptr)
        try:
            return json.loads(s)
        except Exception:
            return {"ok": False, "error": "bad_json", "raw": s}

    # ---- 首扫 / 重建索引 ----
    def enable(self, roots, exclude=None):
        """同步首扫(会阻塞)。roots=根目录列表。回 {ok, scanned} 或 {ok:False,error}。
        调用方若要不阻塞,请用 enable_async。"""
        args = json.dumps({"roots": list(roots), "exclude": list(exclude or [])})
        return self._json(self.lib.findex_build(self.h, args.encode()))

    def enable_async(self, roots, exclude=None, on_done=None):
        """后台线程首扫,立即返回。on_done(result_dict) 扫完回调。
        进度用 status() 轮询(building/scanned)。"""
        if self._build_thread and self._build_thread.is_alive():
            return {"ok": False, "error": "already_building"}

        def _run():
            r = self.enable(roots, exclude)
            if on_done:
                try:
                    on_done(r)
                except Exception:
                    pass

        self._build_thread = threading.Thread(target=_run, daemon=True)
        self._build_thread.start()
        return {"ok": True, "started": True}

    # ---- 搜索 ----
    def search(self, query, limit=50):
        """子串搜索(name/path),按 mtime 倒序。回 hits 列表。"""
        r = self._json(self.lib.findex_query(self.h, (query or "").encode(), int(limit)))
        return r.get("hits", []) if r.get("ok") else []

    # ---- 状态 ----
    def status(self):
        """{enabled, indexed_count, last_scan, building, scanned}。"""
        return self._json(self.lib.findex_status(self.h))

    # ---- 关闭 / 清空 ----
    def disable(self):
        """清空索引(用户关闭功能)。"""
        return self._json(self.lib.findex_clear(self.h))

    def close(self):
        if self.h:
            self.lib.findex_free(self.h)
            self.h = None

    # ---- 预估首扫时长(供前端显示「约需 X 分钟」)----
    @staticmethod
    def estimate_seconds(file_count):
        """按经验速率 ~5000 文件/秒 估算;返回秒数下限 1。"""
        return max(1, int(file_count / 5000))

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
