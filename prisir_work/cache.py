"""本地 7d 磁盘 JSON 缓存 — web_fetch 的命中/落盘后端。

设计:
  · 路径:~/AppData/Local/PrisirAI/cache/web/  (Windows)
         ~/.local/share/PrisirAI/cache/web/   (Linux)
         ~/Library/Caches/PrisirAI/cache/web/ (macOS)
  · 文件名:sha1(url) + ".json"
  · 存储格式:{"url", "fetched_at", "expires_at", "payload"}
  · 写入:tempfile + os.replace(并发安全,避免半写)
  · 一切 IO 异常吞掉 — 用户拍板「先有东西再处理」原则

任何 cache_* 函数都不 raise — 调用方写 `cached = cache_get(url) or {}` 即可。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 路径:跨平台 cache_dir(Windows / Linux / macOS) + 测试可 monkeypatch
# ---------------------------------------------------------------------------

_CACHE_DIR_OVERRIDE: Path | None = None


def cache_dir() -> Path:
    """缓存根目录。Windows = ~/AppData/Local/PrisirAI/cache/web。

    测试可通过 monkeypatch 模块属性 `_CACHE_DIR_OVERRIDE` 改写路径。
    任何 IO 异常 → 静默退化为 Path.home()/.prisIrai_cache/web(尽力能写)。
    """
    if _CACHE_DIR_OVERRIDE is not None:
        d = _CACHE_DIR_OVERRIDE
    else:
        try:
            if sys.platform.startswith("win"):
                base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
                d = base / "PrisirAI" / "cache" / "web"
            elif sys.platform == "darwin":
                d = Path.home() / "Library" / "Caches" / "PrisirAI" / "cache" / "web"
            else:  # linux / other unix
                base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
                d = base / "PrisirAI" / "cache" / "web"
        except Exception:
            # 极兜底:任何路径推导失败 → 写到 home 下隐藏目录
            d = Path.home() / ".prisIrai_cache" / "web"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # 写不进就算了,get/put 都会吞掉
    return d


def _key_path(url: str) -> Path:
    """url → 文件路径。sha1 避免特殊字符/路径穿越/长度问题。"""
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return cache_dir() / f"{h}.json"


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """UTC ISO 8601。带 Z 后缀便于人读。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        # 兼容 "...Z" 形式
        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
        else:
            s2 = s
        return datetime.fromisoformat(s2)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def cache_get(url: str) -> dict[str, Any] | None:
    """命中且未过期 → 返 {url, fetched_at, expires_at, payload};否则 None。

    任何 IO/解析异常 → 返 None(吞)。不删过期条目(给并发读兜底)。
    """
    try:
        p = _key_path(url)
        if not p.exists():
            return None
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        # 必须字段
        if data.get("url") != url:
            return None  # hash 撞了(几乎不可能)但保险
        exp = _parse_iso(str(data.get("expires_at", "")))
        if exp is None:
            return None
        now = datetime.now(timezone.utc)
        if exp <= now:
            return None  # 过期视作 miss,不删(避免并发竞态)
        return {
            "url": data.get("url", url),
            "fetched_at": data.get("fetched_at", ""),
            "expires_at": data.get("expires_at", ""),
            "payload": data.get("payload", {}),
        }
    except Exception:
        return None


def cache_put(url: str, payload: dict, ttl_days: int = 7) -> None:
    """落盘到 cache_dir/<sha1>.json。tempfile + os.replace 避免半写。

    异常全部吞掉(用户拍板:降级 > 崩)。
    """
    try:
        d = cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=int(ttl_days))
        record = {
            "url": url,
            "fetched_at": _now_iso(),
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "payload": payload if isinstance(payload, dict) else {"value": payload},
        }
        target = _key_path(url)
        # tempfile + os.replace:Windows / POSIX 都并发安全
        fd, tmp_path = tempfile.mkstemp(prefix=".cache_", suffix=".tmp", dir=str(d))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False)
            os.replace(tmp_path, target)
        except Exception:
            # 半成品文件清理
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        pass  # 任何异常静默


def cache_clear() -> None:
    """调试用:删整个 web/ 缓存目录。异常吞。

    注意:只会删当前 cache_dir() 解析出来的根(支持 monkeypatch 测试隔离)。
    """
    try:
        d = cache_dir()
        if not d.exists():
            return
        for child in d.iterdir():
            try:
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    # 子目录也清(理论不该有,但保险)
                    for sub in child.iterdir():
                        try:
                            sub.unlink()
                        except OSError:
                            pass
                    try:
                        child.rmdir()
                    except OSError:
                        pass
            except OSError:
                pass
    except Exception:
        pass