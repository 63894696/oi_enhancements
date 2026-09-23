# -*- coding: utf-8 -*-
"""页面版本对比 v0.1.0 (P2.5+17b, 2026-09-23)

对 wigolo diff 工具的 Prisir 对应:
  - mode='url': 两 URL 内容对比
  - mode='time': 同一 URL 两时间快照对比(从 cache 拿历史;若 cache 仅一条 → 当场抓一遍)

返回行级 unified diff + 摘要(added/removed/changed_lines)。

任何源失败降级,warnings 透出。
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from typing import Any

log = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _step(name: str, **extra) -> dict:
    out = {"step": name, "ok": True, "duration_ms": 0}
    out.update(extra)
    return out


def _strip_html(html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", "", html or "",
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style\b[^>]*>.*?</style>", "", text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", "\n", text)
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())


def _to_text(content: str, raw: bool = False) -> str:
    """HTML 默认 strip 后比;raw=True 直接比原文。"""
    if raw:
        return content or ""
    return _strip_html(content or "")


def _unified_lines(a_text: str, b_text: str, fromfile: str = "a",
                   tofile: str = "b", n: int = 2) -> str:
    """行级 unified diff。空 → 'unchanged' 标记。

    difflib 坑:输出 header / hunk 行不会自动加换行 → 多行被压成一坨。
    修法:每个 yield 后主动补 \\n(除非已有)。
    """
    a_lines = [ln + "\n" for ln in a_text.splitlines()]
    b_lines = [ln + "\n" for ln in b_text.splitlines()]
    if a_lines == b_lines:
        return ""
    out = []
    for line in difflib.unified_diff(a_lines, b_lines,
                                     fromfile=fromfile, tofile=tofile,
                                     lineterm="", n=n):
        if not line.endswith("\n"):
            line = line + "\n"
        out.append(line)
    return "".join(out)


def _summary(diff_text: str, a_text: str, b_text: str) -> dict:
    """从 unified diff 数 added/removed/changed_lines。"""
    added = 0
    removed = 0
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return {
        "added_lines": added,
        "removed_lines": removed,
        "changed_lines": added + removed,
        "a_chars": len(a_text),
        "b_chars": len(b_text),
    }


def _fetch_content(url: str, timeout: float, no_cache: bool = False) -> tuple[str | None, list[str], dict | None]:
    """从 web_fetch 拿 content。返 (content, warnings, meta)。"""
    warnings: list[str] = []
    try:
        from prisir_work import web_fetch as _wf
        r = _wf.fetch(url, options={"timeout": timeout, "no_cache": no_cache})
        if r.get("ok") and r.get("content"):
            return (r["content"], warnings, r.get("meta"))
        warnings.append("fetch_failed")
        return (None, warnings, r)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"fetch_error:{type(e).__name__}")
        return (None, warnings, None)


def _cache_snapshot(url: str) -> dict | None:
    """从 cache 拿当前快照。"""
    try:
        from prisir_work import cache as _cache
        rec = _cache.cache_get(url)
        if rec and isinstance(rec.get("payload"), dict):
            return rec
        return None
    except Exception:
        return None


def _save_snapshot_to_cache(url: str, content: str) -> None:
    """为了 mode='time' 第二次对比,主动写一份到 cache。"""
    try:
        from prisir_work import cache as _cache
        _cache.cache_put(url, {"content": content, "fetcher": "diff_snapshot"}, ttl_days=1)
    except Exception:
        pass


def diff(url_a: str, url_b: str = "", *, mode: str = "url",
         raw: bool = False, timeout: float = 12.0,
         snapshot_b: bool = True) -> dict:
    """对比两个 URL 或同 URL 两时间快照。

    Args:
        url_a: A 侧 URL(mode='url' 时是与 url_b 对比;mode='time' 时是与"上一次快照"对比)
        url_b: B 侧 URL,仅 mode='url' 用
        mode: 'url' | 'time'
            - 'url': 抓 url_a 和 url_b 比
            - 'time': 抓 url_a 当作 b,cache 里 url_a 历史当作 a;若 cache 没历史且
                     snapshot_b=True → 当前内容先存到 cache 留作下次对比基线
        raw: False(默认)→ 先 strip HTML 再比;True → 比原文
        timeout: 单次 fetch 超时秒数
        snapshot_b: mode='time' 时是否把当前 b 存到 cache(默认 True,用于持续监控)

    Returns:
    {
        'ok': True,
        'mode': str,
        'a_url': str,
        'b_url': str,
        'a_meta': {'fetched_at', 'source': 'live'|'cache'|'empty'},
        'b_meta': {...},
        'diff': str,           # unified diff 文本
        'summary': {added_lines, removed_lines, changed_lines, a_chars, b_chars},
        'unchanged': bool,
        'warnings': list[str],
        'steps': [...]
    }
    """
    warnings: list[str] = []
    steps: list[dict] = []
    if mode not in ("url", "time"):
        return {"ok": False, "mode": mode, "diff": "", "summary": {},
                "warnings": ["invalid_mode"], "steps": []}

    # ── 取 a / b 内容 ──
    a_content = ""
    b_content = ""
    a_meta: dict[str, Any] = {"source": "empty"}
    b_meta: dict[str, Any] = {"source": "empty"}

    if mode == "url":
        if not url_a or not url_b:
            return {"ok": False, "mode": mode, "diff": "", "summary": {},
                    "warnings": ["empty_url"], "steps": []}
        t0 = _now_ms()
        a_content, a_w, _ = _fetch_content(url_a, timeout)
        warnings.extend(a_w)
        a_meta = {"source": "live" if a_content else "empty",
                  "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        steps.append(_step("fetch_a", source=a_meta["source"],
                           duration_ms=_now_ms() - t0))

        t0 = _now_ms()
        b_content, b_w, _ = _fetch_content(url_b, timeout)
        warnings.extend(b_w)
        b_meta = {"source": "live" if b_content else "empty",
                  "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        steps.append(_step("fetch_b", source=b_meta["source"],
                           duration_ms=_now_ms() - t0))

    else:  # mode == 'time'
        if not url_a:
            return {"ok": False, "mode": mode, "diff": "", "summary": {},
                    "warnings": ["empty_url"], "steps": []}
        # a 从 cache 拿历史
        t0 = _now_ms()
        snap = _cache_snapshot(url_a)
        if snap and snap.get("payload", {}).get("content"):
            a_content = snap["payload"]["content"]
            a_meta = {"source": "cache",
                      "fetched_at": snap.get("fetched_at", "")}
        else:
            warnings.append("cache_miss")
            a_meta = {"source": "empty"}
        steps.append(_step("cache_a", source=a_meta["source"],
                           duration_ms=_now_ms() - t0))

        # b 当前 fetch(必须绕 cache,否则会和 a 一样 → 永远 unchanged)
        t0 = _now_ms()
        b_content, b_w, _ = _fetch_content(url_a, timeout, no_cache=True)
        warnings.extend(b_w)
        b_meta = {"source": "live" if b_content else "empty",
                  "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        steps.append(_step("fetch_b", source=b_meta["source"],
                           duration_ms=_now_ms() - t0))

        # 若 b 拿到且没历史,snapshot_b 写入 cache(给下一次对比基线)
        if snapshot_b and b_content and not a_content:
            _save_snapshot_to_cache(url_a, b_content)
            warnings.append("snapshot_saved_for_baseline")

    if not a_content and not b_content:
        return {"ok": True, "mode": mode, "a_url": url_a, "b_url": url_b,
                "a_meta": a_meta, "b_meta": b_meta, "diff": "",
                "summary": {"added_lines": 0, "removed_lines": 0,
                            "changed_lines": 0, "a_chars": 0, "b_chars": 0},
                "unchanged": True, "warnings": warnings + ["both_empty"], "steps": steps}

    # ── 文本化 + diff ──
    t0 = _now_ms()
    a_text = _to_text(a_content, raw=raw)
    b_text = _to_text(b_content, raw=raw)
    diff_text = _unified_lines(a_text, b_text,
                               fromfile=url_a or "a", tofile=url_b or url_a or "b")
    summary = _summary(diff_text, a_text, b_text)
    unchanged = (diff_text == "")
    steps.append(_step("compute_diff", unchanged=unchanged,
                       duration_ms=_now_ms() - t0))

    return {
        "ok": True,
        "mode": mode,
        "a_url": url_a,
        "b_url": url_b if mode == "url" else url_a,
        "a_meta": a_meta,
        "b_meta": b_meta,
        "diff": diff_text,
        "summary": summary,
        "unchanged": unchanged,
        "warnings": warnings,
        "steps": steps,
    }


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) < 3:
        print('usage: python -m prisir_work.diff <url_a> <url_b> [--mode url|time] [--raw]')
        raise SystemExit(2)
    _a, _b = _sys.argv[1], _sys.argv[2]
    _mode = "url"
    _raw = False
    if "--mode" in _sys.argv:
        _mode = _sys.argv[_sys.argv.index("--mode") + 1]
    if "--raw" in _sys.argv:
        _raw = True
    print(json.dumps(diff(_a, _b, mode=_mode, raw=_raw),
                     ensure_ascii=False, indent=2))