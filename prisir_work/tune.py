"""per-domain fetcher 优先级学习 v0.1.0 (P2.5+18b, 2026-09-23)

目的:慢站(首屏 5s+)提速。
做法:每次 fetch 后,统计 (host, fetcher_name, elapsed_ms, ok) 累加器;
样本数 ≥ 3 且某 fetcher 明显胜出(中位 elapsed_ms 最快且 ok 率 ≥ 80%)时,
写入 tune.json 的 [host] 列表(已稳定者优先)。

web_fetch.fetch 调用顺序:
  1. 查 cache(mem → disk)
  2. 查 tune.json 推荐
       命中 + 列表非空 → 仅跑列表里的 fetcher(其余跳过);首个 ok 立即返
       未命中 / 列表为空 → 走原全并发
  3. fetch 完后 → tune_record(host, fetcher, ms, ok) 累加
       命中新 best → tune.json 写盘(原子 temp + replace)

任何 IO 异常 → 静默降级,返 None(读)或丢记录(写)。
冷启动(tune.json 不存在 / host 无记录)→ tune.json 不写,
避免少量样本污染;通过最小样本阈值(MIN_SAMPLES=3)保护。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 阈值
# ---------------------------------------------------------------------------

MIN_SAMPLES = 3            # 至少 3 次样本才考虑推荐
MIN_OK_RATIO = 0.8         # 推荐 fetcher 的 ok 率 ≥ 80%
MS_BEST_MARGIN = 0.20      # 候选最快 vs 第二快 ≥ 20% 才写入
MAX_HOSTS = 200            # tune.json 最多 200 host(LRU 淘汰)


# ---------------------------------------------------------------------------
# 路径(对齐 health._tune_health:cache_dir().parent / "tune.json")
# ---------------------------------------------------------------------------

def tune_path() -> Path:
    """tune.json 完整路径 = cache_dir().parent / "tune.json"。

    走 cache_dir() 而非 Path.home() — 单测 monkeypatch _CACHE_DIR_OVERRIDE 时
    tune.json 也跟着迁;与 health._tune_health 一致。
    """
    from prisir_work import cache as _cache
    return _cache.cache_dir().parent / "tune.json"


# ---------------------------------------------------------------------------
# 并发安全:写锁(进程内线程安全;进程间不强求,最后写入者赢)
# ---------------------------------------------------------------------------

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 读:load + recommend
# ---------------------------------------------------------------------------

def _load() -> dict[str, list[str]]:
    """读 tune.json,返 {host: [fetcher, ...]}。任何异常 → 返 {}。"""
    p = tune_path()
    if not p.exists():
        return {}
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for h, v in data.items():
        if isinstance(h, str) and isinstance(v, list):
            cleaned = [x for x in v if isinstance(x, str) and x.strip()]
            if cleaned:
                out[h] = cleaned
    return out


def recommend(host: str) -> list[str] | None:
    """查询 host 的推荐 fetcher 列表;无记录 / tune.json 不存在 → 返 None。

    None 表示「全并发跑」,不是「空列表」(空列表也是合法推荐,例如某 fetcher 禁用了)。
    """
    if not host:
        return None
    with _lock:
        data = _load()
    rec = data.get(host)
    if rec:
        return list(rec)
    return None


# ---------------------------------------------------------------------------
# 累加器(进程内)— host → {fetcher: [ok_count, fail_count, ms_sum, ms_count]}
# ---------------------------------------------------------------------------

_stats: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))


def _host_key(url: str) -> str:
    """URL → host(空 / 异常 → '' 跳过累加)。"""
    try:
        from urllib.parse import urlparse
        h = urlparse(url).hostname or ""
        return h.lower()
    except Exception:
        return ""


def record(url: str, fetcher: str, elapsed_ms: int, ok: bool) -> None:
    """累加 (host, fetcher, elapsed_ms, ok) 到进程内统计。

    不会立刻落盘;落盘由 flush_if_ready 触发。
    任何异常 → 吞掉。
    """
    if not fetcher:
        return
    h = _host_key(url)
    if not h:
        return
    try:
        ms = max(0, int(elapsed_ms or 0))
        s = _stats[h][fetcher]
        if ok:
            s[0] += 1   # ok_count
            s[2] += ms  # ms_sum
            s[3] += 1   # ms_count
        else:
            s[1] += 1   # fail_count
    except Exception:
        pass


def _best_for(host: str) -> list[str] | None:
    """对单 host 算 best fetcher 列表(已稳定者优先)。

    判定:某 fetcher 满足
      - 总样本数 ≥ MIN_SAMPLES
      - ok 率 ≥ MIN_OK_RATIO
      - 中位 elapsed_ms 最低且与第二名差距 ≥ MS_BEST_MARGIN
    才加入列表。

    多 fetcher 同档(差距 < 20%)都返回,留给并发。
    """
    s = _stats.get(host)
    if not s:
        return None
    candidates: list[tuple[str, float, float, int]] = []  # (name, ok_ratio, avg_ms, total)
    for name, c in s.items():
        total = c[0] + c[1]
        if total < MIN_SAMPLES:
            continue
        ok_ratio = c[0] / total
        if ok_ratio < MIN_OK_RATIO:
            continue
        avg_ms = (c[2] / c[3]) if c[3] else 1e9
        candidates.append((name, ok_ratio, avg_ms, total))

    if not candidates:
        return None

    # 按 avg_ms 升序
    candidates.sort(key=lambda t: t[2])
    fastest = candidates[0][2]
    if fastest <= 0:
        fastest = 1e-6
    best_list: list[str] = []
    for name, _, avg_ms, _ in candidates:
        # 与第一名差距 ≥ 20% 才纳入(否则视为同档,都跑)
        if avg_ms <= fastest * (1 + MS_BEST_MARGIN):
            best_list.append(name)
        else:
            break
    return best_list or None


def _save(data: dict[str, list[str]]) -> None:
    """tune.json 原子写入(temp + os.replace)。任何异常 → 吞。"""
    p = tune_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # 限制 host 数(简单 LRU 淘汰:超限丢最少用的)
        if len(data) > MAX_HOSTS:
            # 保留:插入顺序末 MAX_HOSTS 个(每次 save 都重写,够用)
            keys = list(data.keys())
            for k in keys[:-MAX_HOSTS]:
                data.pop(k, None)
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
        fd, tmp = tempfile.mkstemp(prefix=".tune_", dir=str(p.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, p)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except Exception:
        pass


def flush_if_ready() -> dict[str, list[str] | None]:
    """扫所有 host 的累加器,把已稳定的 best 写盘。

    Returns:调试用 {host: best_or_None}。
    """
    with _lock:
        out: dict[str, list[str] | None] = {}
        current = _load()
        any_change = False
        for host in list(_stats.keys()):
            best = _best_for(host)
            out[host] = best
            old = current.get(host)
            if best != old:
                if best is None:
                    current.pop(host, None)
                else:
                    current[host] = best
                any_change = True
        if any_change:
            _save(current)
        return out


def reset() -> None:
    """清空进程内累加器(测试用)。"""
    with _lock:
        _stats.clear()


def tune_stats_snapshot() -> dict[str, dict[str, dict[str, int | float]]]:
    """调试:返当前累加器 {host: {fetcher: {ok, fail, avg_ms, total}}}。"""
    with _lock:
        snap: dict[str, dict[str, dict[str, int | float]]] = {}
        for h, fs in _stats.items():
            snap[h] = {}
            for n, c in fs.items():
                total = c[0] + c[1]
                avg = (c[2] / c[3]) if c[3] else 0.0
                snap[h][n] = {"ok": c[0], "fail": c[1], "total": total, "avg_ms": round(avg, 2)}
        return snap


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json
    import sys as _sys
    cmd = _sys.argv[1] if len(_sys.argv) > 1 else "stats"
    if cmd == "stats":
        print(_json.dumps(tune_stats_snapshot(), ensure_ascii=False, indent=2))
    elif cmd == "recommend":
        h = _sys.argv[2] if len(_sys.argv) > 2 else ""
        print(_json.dumps({h: recommend(h)}, ensure_ascii=False))
    elif cmd == "flush":
        print(_json.dumps(flush_if_ready(), ensure_ascii=False, indent=2))
    elif cmd == "reset":
        reset()
        print("ok")
    else:
        print("usage: tune.py [stats|recommend <host>|flush|reset]", file=_sys.stderr)
        _sys.exit(2)