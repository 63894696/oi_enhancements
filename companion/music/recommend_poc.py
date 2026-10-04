# -*- coding: utf-8 -*-
"""
recommend_poc.py — P2.5+25 PoC(2026-10-03)

AI 歌单推荐 PoC:启发式加权打分 + 多样性封顶 + 已播放过滤。
不依赖 ML,验证「规则 + 计数」是否够替代 999 硬补。

输入:user_state dict(收藏/下载/最近播放 30 天的 song_id 列表)
输出:List[Dict] song_id + score + reason
"""
from __future__ import annotations

import csv
import hashlib
import random
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

_POOL: Path | None = None  # N9(2026-10-04):不再直接读 CSV,改走 recommender.recommend_from_catalog(catalog)
_TAG_W = 0.5
_ARTIST_W = 0.3
_POP_W = 0.1
_NOVEL_W = 0.1
_TAG_CAP = 0.30   # 同 tag 上限 30%
_RECENCY_DAYS = 30


def _song_id(title: str, artist: str) -> str:
    return hashlib.sha1(f"{title}|{artist}".encode("utf-8")).hexdigest()[:12]


def load_pool() -> List[Dict[str, Any]]:
    """读 CSV → List[{id, title, artist, tag}],去重。N9 起:改走 recommender.recommend_from_catalog(catalog)。"""
    if _POOL is None:
        raise RuntimeError(
            "[recommend_poc] _POOL 已禁用;请用 recommender.recommend_from_catalog(SongPoolCatalog)"
        )
    out: List[Dict[str, Any]] = []
    seen = set()
    with open(_POOL, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if not row:
                continue
            t = (row[0] or "").strip()
            if not t:
                continue
            a = (row[1] or "").strip() if len(row) > 1 else ""
            tag = (row[2] or "").strip() if len(row) > 2 else ""
            k = (t.lower(), a.lower())
            if k in seen:
                continue
            seen.add(k)
            out.append({"id": _song_id(t, a), "title": t, "artist": a, "tag": tag})
    return out


def _reason_for(song: Dict[str, Any], user_state: Dict[str, Any]) -> str:
    fav_tags: Counter = user_state.get("favorite_tags", Counter())
    fav_artists: Counter = user_state.get("favorite_artists", Counter())
    if song["tag"] and fav_tags.get(song["tag"], 0) >= 3:
        return f"你收藏过 {fav_tags[song['tag']]} 首{song['tag']}"
    if song["artist"] and fav_artists.get(song["artist"], 0) >= 2:
        return f"你喜欢 {song['artist']} 的 {fav_artists[song['artist']]} 首歌"
    if song["tag"] and fav_tags.get(song["tag"], 0) > 0:
        return f"命中你偏好的 {song['tag']}"
    if song["artist"] and fav_artists.get(song["artist"], 0) > 0:
        return f"你听过 {song['artist']} 的歌"
    if song["tag"]:
        return f"探索新风格 {song['tag']}"
    return "随机推荐"


def recommend(user_state: Dict[str, Any], pool: List[Dict[str, Any]],
              k: int = 20, seed: int | None = None) -> List[Dict[str, Any]]:
    if seed is not None:
        random.seed(seed)
    fav_tags: Counter = user_state.get("favorite_tags", Counter())
    fav_artists: Counter = user_state.get("favorite_artists", Counter())
    recent: set = set(user_state.get("play_history_recent", []))
    pop_weight = user_state.get("play_count_weight", _POP_W)
    novelty_weight = user_state.get("novelty_weight", _NOVEL_W)

    fav_tag_total = sum(fav_tags.values()) or 1
    fav_artist_total = sum(fav_artists.values()) or 1

    scored: List[Dict[str, Any]] = []
    max_play = max((s.get("play_count", 0) for s in pool), default=1) or 1
    for s in pool:
        sid = s["id"]
        # 已播放 30 天内 → 大幅降分(除非虚拟 popularity 高)
        if sid in recent and s.get("play_count", 0) <= 5:
            continue
        tag_score = fav_tags.get(s["tag"], 0) / fav_tag_total if s["tag"] else 0
        artist_score = fav_artists.get(s["artist"], 0) / fav_artist_total if s["artist"] else 0
        pop_score = s.get("play_count", 0) / max_play
        novelty_score = 0.0 if sid in recent else 1.0

        score = (_TAG_W * tag_score
                 + _ARTIST_W * artist_score
                 + pop_weight * pop_score
                 + novelty_weight * novelty_score)
        scored.append({
            "id": sid,
            "title": s["title"],
            "artist": s["artist"],
            "tag": s["tag"],
            "score": round(score, 3),
            "reason": _reason_for(s, user_state),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 多样性封顶 30%:同 tag 不超过 k*0.30
    cap = max(1, int(k * _TAG_CAP))
    selected: List[Dict[str, Any]] = []
    tag_count: Counter = Counter()
    for item in scored:
        if len(selected) >= k:
            break
        t = item["tag"] or "_"
        if tag_count[t] >= cap:
            continue
        selected.append(item)
        tag_count[t] += 1
    # 不够 k 时从被 cap 掉的补足
    if len(selected) < k:
        for item in scored:
            if item in selected:
                continue
            selected.append(item)
            if len(selected) >= k:
                break
    return selected


def _print(title: str, items: List[Dict[str, Any]], limit: int = 8) -> None:
    print(f"== {title} ==")
    for i, it in enumerate(items[:limit], 1):
        print(f"{i}. {it['title']} - {it['artist']} "
              f"(tag={it['tag'] or '?'}, score={it['score']}, "
              f"reason:{it['reason']})")
    print()


def main() -> None:
    pool = load_pool()
    print(f"[pool] loaded {len(pool)} unique songs")
    tags = Counter(s["tag"] for s in pool)
    print(f"[pool] tags: {dict(tags.most_common())}\n")

    # 场景 1:用户偏古风+纯音乐
    fav1 = Counter({"古风": 8, "纯音乐": 5, "流行": 2})
    fav_artists1 = Counter({"周杰伦": 4, "银临": 3})
    state1 = {
        "favorite_tags": fav1,
        "favorite_artists": fav_artists1,
        "play_history_recent": [],
    }
    rec1 = recommend(state1, pool, k=20, seed=42)
    _print("用户偏古风+纯音乐", rec1)

    # 场景 2:用户收藏全在粤语
    fav2 = Counter({"粤语": 12})
    fav_artists2 = Counter({"陈奕迅": 6, "张学友": 4})
    state2 = {
        "favorite_tags": fav2,
        "favorite_artists": fav_artists2,
        "play_history_recent": [],
    }
    rec2 = recommend(state2, pool, k=20, seed=43)
    _print("用户收藏全在粤语", rec2)

    # 场景 3:全空(新用户冷启动)
    state3 = {"favorite_tags": Counter(), "favorite_artists": Counter(),
              "play_history_recent": []}
    rec3 = recommend(state3, pool, k=20, seed=44)
    _print("全空 user_state(新用户)", rec3)

    # 多样性验证:tag 分布
    for label, rec in [("古风偏好", rec1), ("粤语偏好", rec2), ("新用户", rec3)]:
        dist = Counter(rec[i]["tag"] for i in range(len(rec)))
        top = ", ".join(f"{t}={n}" for t, n in dist.most_common(5))
        print(f"  [{label}] tag 分布 top5: {top}")


if __name__ == "__main__":
    main()