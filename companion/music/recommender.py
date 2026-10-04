# -*- coding: utf-8 -*-
"""
recommender.py — N9(2026-10-04)AI 歌单推荐 state builder + Web 入口

复用 recommend_poc.recommend()(2026-10-03 P2.5+25 PoC,启发式加权打分 + 多样性封顶)
和 SongPoolCatalog(P3.9 v2 CSV 已有 is_favorite / play_count / last_played_iso 字段),
新增 state builder 把 catalog 实例喂进 PoC,无需 PoC 直接读 CSV。

设计要点:
  - 纯本地启发式,无 AI/ML/上传(沿用 P3.10b「0 上传/外传」红线)
  - 冷启动:无 favorites + 无 play_history → 14 tag 均匀分布 + random.shuffle
  - favorites/play_history 直接读 SongPoolCatalog 静态字段(无文件 IO)
  - 推荐结果每次请求实时计算(无 cache / 无落盘)
  - reason 文案扩展 PoC,加冷启动 reason

下游:
  - prisIragent-music-web.py api_recommend 调 recommend_from_catalog(APP.song_pool, k, seed)
  - 前端 RecommendPanel.vue 渲染 list,click → player.playById(id)
"""
from __future__ import annotations

import logging
import math
import random
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from music.song_pool import SongMeta, SongPoolCatalog

# N9(2026-10-04):复用 PoC 的 recommend + _reason_for,算法权重不动
from music.recommend_poc import recommend, _reason_for

log = logging.getLogger("recommender")

# N9 冷启动均匀分布的「tag 数」— 拍板 14 tag(P3.9 v2 CSV 14 tag)
# 真实 tag 列表从 catalog.all_songs 聚合(动态,不硬编码)
_RECENCY_DAYS = 30


def _recent_iso_to_set(catalog: SongPoolCatalog, days: int = _RECENCY_DAYS) -> set:
    """从 catalog.all_songs.last_played_iso 聚合 N 天内的 song_id set。

    容错:last_played_iso 空 / 非法格式 → 跳过;无 future date(只取 <= now)。
    Returns:set[str] song_id 集合。
    """
    out: set = set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
    for s in catalog.all_songs:
        iso = (s.last_played_iso or "").strip()
        if not iso:
            continue
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                out.add(s.id)
        except (ValueError, TypeError):
            continue
    return out


def build_user_state(catalog: SongPoolCatalog, days: int = _RECENCY_DAYS) -> Dict[str, Any]:
    """N9:从 SongPoolCatalog 聚合 user_state — favorites / play_history / tag / artist Counter。

    Returns:
        {
          "favorite_tags": Counter,         # tag → 收藏数
          "favorite_artists": Counter,      # artist → 收藏数
          "play_history_recent": set[str],  # 30 天内播放过的 song_id 集合
          "is_cold_start": bool,            # True 表示无收藏 + 无 30 天内播放 → 走冷启动分支
          "favorite_count": int,
          "recent_play_count": int,
        }
    """
    favorite_tags: Counter = Counter()
    favorite_artists: Counter = Counter()
    fav_count = 0
    for s in catalog.all_songs:
        if s.is_favorite and s.is_favorite > 0:
            fav_count += 1
            if s.tag:
                favorite_tags[s.tag] += 1
            if s.artist:
                favorite_artists[s.artist] += 1

    recent = _recent_iso_to_set(catalog, days=days)
    is_cold = (fav_count == 0 and len(recent) == 0)
    return {
        "favorite_tags": favorite_tags,
        "favorite_artists": favorite_artists,
        "play_history_recent": recent,
        "is_cold_start": is_cold,
        "favorite_count": fav_count,
        "recent_play_count": len(recent),
    }


def _catalog_to_pool(catalog: SongPoolCatalog) -> List[Dict[str, Any]]:
    """SongPoolCatalog → recommend_poc.recommend 期望的 List[Dict] 格式。

    PoC 字段:id / title / artist / tag / play_count
    """
    return [
        {
            "id": s.id,
            "title": s.title,
            "artist": s.artist,
            "tag": s.tag,
            "play_count": s.play_count or 0,
        }
        for s in catalog.all_songs
    ]


def _cold_start(catalog: SongPoolCatalog, k: int, seed: Optional[int]) -> List[Dict[str, Any]]:
    """N9 冷启动(无收藏 + 无 30 天内播放):14 tag 均匀分布 + random.shuffle。

    每 tag 取 ceil(k / distinct_tags) 首,不足则循环;最后 random.shuffle(seed) 让每次刷新都不同。

    Returns:List[{id, title, artist, tag, score, reason}] 长度 = k
    """
    if k <= 0:
        return []
    if seed is not None:
        rnd = random.Random(seed)
    else:
        rnd = random.Random()

    # 1) 按 tag 分桶
    buckets: Dict[str, List[SongMeta]] = {}
    for s in catalog.all_songs:
        if not s.tag:
            continue
        buckets.setdefault(s.tag, []).append(s)
    if not buckets:
        # catalog 无 tag(异常)→ 兜底随机
        all_songs = list(catalog.all_songs)
        rnd.shuffle(all_songs)
        return [
            {"id": s.id, "title": s.title, "artist": s.artist, "tag": s.tag,
             "score": 0.0, "reason": f"冷启动兜底随机 {s.tag or '?'}"}
            for s in all_songs[:k]
        ]

    distinct_tags = list(buckets.keys())
    per_tag = max(1, math.ceil(k / max(1, len(distinct_tags))))

    # 2) 每 tag 取 per_tag 首(循环取模)
    selected: List[SongMeta] = []
    for t in distinct_tags:
        bucket = list(buckets[t])
        rnd.shuffle(bucket)
        for i in range(per_tag):
            if not bucket:
                break
            selected.append(bucket[i % len(bucket)])
    # 3) shuffle 整列表 + 截前 k
    rnd.shuffle(selected)
    selected = selected[:k]

    # 4) 输出 reason:冷启动均匀分布 ${tag}
    out: List[Dict[str, Any]] = []
    for s in selected:
        out.append({
            "id": s.id,
            "title": s.title,
            "artist": s.artist,
            "tag": s.tag,
            "score": 0.0,
            "reason": f"冷启动均匀分布 {s.tag}",
        })
    return out


def recommend_from_catalog(
    catalog: SongPoolCatalog,
    k: int = 20,
    seed: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """N9 主入口 — 从 SongPoolCatalog 实例推荐 k 首歌。

    流程:
      1) build_user_state(catalog) 聚合 favorites/play_history
      2) 冷启动(is_cold_start=True)→ _cold_start(catalog, k, seed)
      3) 否则 → recommend(user_state, pool, k=k, seed=seed)

    Returns:List[{id, title, artist, tag, score, reason}] 长度 ≤ k
    """
    if catalog is None or not catalog.all_songs:
        log.warning("[recommender] catalog empty or None")
        return []

    state = build_user_state(catalog)
    if state["is_cold_start"]:
        return _cold_start(catalog, k=k, seed=seed)

    pool = _catalog_to_pool(catalog)
    return recommend(state, pool, k=k, seed=seed)
