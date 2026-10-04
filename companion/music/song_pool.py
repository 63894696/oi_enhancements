# -*- coding: utf-8 -*-
"""
song_pool.py — P2.5+23(2026-10-03)真歌名池 → P3.9(2026-10-03)v2 接入

从 companion/music/song_pool.csv 读歌名 → 启动时随机洗牌 →
前端 GET /api/songs 拿 60 首可见。

P3.9 设计:
  - 支持 v2 CSV schema (9 列:id, title, artist, tag, duration_sec,
    is_favorite, download_count, play_count, last_played_iso) — 显式
    spXXX id(非 sha1 派生)+ duration_sec 估算时长
  - 保持 v1 schema 向后兼容 (3 列:歌名,歌手,标签) — 用 header 第 1 列
    是否为 'id' 探测; v1 loader 仍用 _safe_id(sha1[:12]) 派生 id
  - utf-8-sig encoding 容 v1 BOM + v2 无 BOM
  - 默认路径 song_pool.csv(主),旧 song_pool_template.csv 改名
    song_pool_v1.csv 备份

v1 vs v2 区别:
  v1: 388 行 / 6 标签 (ACG神曲/熬夜修仙/巴士随身听/古风/华语/欧美) /
      3 列 / id = sha1(title|artist)[:12]
  v2: 841 行 / 14 标签 (含电子/R&B/怀旧金曲 80-00/世界音乐/嘻哈/圣诞/
       儿童/影视 OST/纯音乐 等) / 9 列 / id = sp001..sp841

下游影响:
  - player.py _cmd_favorite 用 track_id 查 catalog title/artist,不依赖 id
    格式(v1 v2 都 OK)
  - 前端 GET /api/songs 返回值加新字段 duration_sec,旧字段保留(向后兼容)
"""
from __future__ import annotations

import csv
import hashlib
import logging
import random
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, List, Optional

log = logging.getLogger("song_pool")

# P3.9(2026-10-03):主 CSV 路径从 song_pool_template.csv 改 song_pool.csv
# (v1 备份到 song_pool_v1.csv,本加载器仍可传 v1 路径走 v1 分支)
_POOL_DEFAULT = Path(__file__).resolve().parent / "song_pool.csv"


@dataclass
class SongMeta:
    id: str            # P3.9:v2 = sp001..sp841 显式;v1 = sha1(title|artist)[:12]
    title: str
    artist: str
    tag: str
    # P3.9 新增(可空,v1 走默认 0/空):
    duration_sec: int = 0
    is_favorite: int = 0
    download_count: int = 0
    play_count: int = 0
    last_played_iso: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _safe_id(title: str, artist: str) -> str:
    """v1 兼容:sha1(title|artist) 前 12 位。v2 不再调此函数。"""
    return hashlib.sha1(f"{title}|{artist}".encode("utf-8")).hexdigest()[:12]


def _safe_int(s, default: int = 0) -> int:
    """容错 int 解析:空 / 字符串 / 浮点 → default。"""
    if s is None:
        return default
    s2 = str(s).strip()
    if not s2:
        return default
    try:
        return int(float(s2))   # 容忍 "269.0" 写法
    except (ValueError, TypeError):
        return default


def _parse_csv_rows(path: Path) -> Iterable[SongMeta]:
    """读 CSV,自动容 v1 (3 列) + v2 (9 列) + BOM。

    Schema 探测:header 第 1 列小写 == "id" → v2;否则 v1。
    v2 用显式 spXXX id + duration_sec;v1 用 _safe_id 派生 + 0 duration。
    """
    if not path.exists():
        return []
    out: List[SongMeta] = []
    with open(path, encoding="utf-8-sig") as f:    # utf-8-sig 容 v1 BOM + v2 无 BOM
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return []
        is_v2 = (header[0] or "").strip().lower() == "id"
        for row in reader:
            if not row:
                continue
            if is_v2:
                # 9 列:id, title, artist, tag, duration_sec,
                #     is_favorite, download_count, play_count, last_played_iso
                if len(row) < 4:
                    continue
                sid = (row[0] or "").strip()
                title = (row[1] or "").strip()
                if not title:
                    continue
                artist = (row[2] or "").strip()
                tag = (row[3] or "").strip()
                dur = _safe_int(row[4] if len(row) > 4 else None, 0)
                fav = _safe_int(row[5] if len(row) > 5 else None, 0)
                dl = _safe_int(row[6] if len(row) > 6 else None, 0)
                play = _safe_int(row[7] if len(row) > 7 else None, 0)
                last_iso = (row[8] or "").strip() if len(row) > 8 else ""
                out.append(SongMeta(
                    id=sid, title=title, artist=artist, tag=tag,
                    duration_sec=dur, is_favorite=fav,
                    download_count=dl, play_count=play,
                    last_played_iso=last_iso,
                ))
            else:
                # v1 兼容:3 列,id 用 _safe_id 派生,duration_sec 默认 0
                if len(row) < 1:
                    continue
                title = (row[0] or "").strip()
                if not title:
                    continue
                artist = (row[1] or "").strip() if len(row) > 1 else ""
                tag = (row[2] or "").strip() if len(row) > 2 else ""
                out.append(SongMeta(
                    id=_safe_id(title, artist),
                    title=title, artist=artist, tag=tag,
                ))
    return out


class SongPoolCatalog:
    """歌单目录:全量加载 + 随机 60 首可见 + 标签过滤。"""

    DEFAULT_VISIBLE = 60

    def __init__(self, csv_path: Optional[Path] = None, visible_size: int = DEFAULT_VISIBLE):
        # P3.9:默认指向 song_pool.csv(v2 主);传 csv_path 可走 v1 路径
        self.csv_path = Path(csv_path) if csv_path else _POOL_DEFAULT
        self.visible_size = max(1, int(visible_size))
        self.all_songs: List[SongMeta] = []
        self.visible_songs: List[SongMeta] = []

    # -------------------------------------------------------
    # load + shuffle
    # -------------------------------------------------------
    def load(self) -> int:
        """从 CSV 读全量 + 立即 shuffle 选 visible。
        返 loaded 数(去重前原始数,等价的去重后数 = all_songs 长度)。
        """
        rows = list(_parse_csv_rows(self.csv_path))
        # P3.9(2026-10-03):subagent v2 CSV 实测有 1 个真重复(sp138 × 2 首歌)
        # 防御性按 id 去重,保第一行;同时记 warn 日志让用户知道
        seen: set = set()
        dup_ids: set = set()
        deduped: List[SongMeta] = []
        for r in rows:
            if r.id in seen:
                dup_ids.add(r.id)
                continue
            seen.add(r.id)
            deduped.append(r)
        if dup_ids:
            log.warning("[song_pool] deduped %d duplicate ids: %s",
                        len(dup_ids), sorted(dup_ids))
        self.all_songs = deduped
        log.info("[song_pool] loaded %d songs from %s", len(deduped), self.csv_path)
        self.shuffle()
        return len(deduped)

    def shuffle(self) -> None:
        """重新洗牌,取前 visible_size 首作为 visible。"""
        pool = list(self.all_songs)
        random.shuffle(pool)
        self.visible_songs = pool[: self.visible_size]
        log.info("[song_pool] visible %d / %d", len(self.visible_songs), len(self.all_songs))

    # -------------------------------------------------------
    # 查询 API(给 web 路由用)
    # -------------------------------------------------------
    def list_visible(
        self,
        tags: Optional[List[str]] = None,
        q: Optional[str] = None,
        tag: Optional[str] = None,  # 旧单值参数(向后兼容 P2.5+24 + P3.6)
    ) -> List[SongMeta]:
        """P3.7(2026-10-04):支持多 tag OR 合并 + title/artist 模糊搜索。

        - tags: 多 tag,任一命中即返(OR 语义;网易云桌面同款)
        - q: title/artist case-insensitive 简单包含
        - tag: 旧单值参数(向后兼容,内部归并到 tags)
        """
        # 合并 tags + tag 旧单值(向后兼容)
        eff_tags: List[str] = list(tags) if tags else []
        if tag and tag not in eff_tags:
            eff_tags.append(tag)
        qn = (q or "").strip().lower()

        out: List[SongMeta] = []
        for s in self.visible_songs:
            if eff_tags and s.tag not in eff_tags:
                continue
            if qn:
                hay = f"{s.title} {s.artist}".lower()
                if qn not in hay:
                    continue
            out.append(s)
        return out

    def list_tags(self) -> List[str]:
        """返所有出现过的标签(按出现顺序,空标签排最后)。"""
        seen: List[str] = []
        seen_set: set = set()
        for s in self.all_songs:
            if s.tag and s.tag not in seen_set:
                seen.append(s.tag)
                seen_set.add(s.tag)
        return seen

    def get_by_id(self, sid: str) -> Optional[SongMeta]:
        for s in self.all_songs:
            if s.id == sid:
                return s
        return None


if __name__ == "__main__":
    # 独立跑:`python -m music.song_pool` 或 `python companion/music/song_pool.py`
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    cat = SongPoolCatalog()
    n = cat.load()
    print(f"loaded={n} visible={len(cat.visible_songs)} tags={cat.list_tags()}")
    print("first 5 visible:")
    for s in cat.visible_songs[:5]:
        print(f"  - {s.title} | {s.artist} | {s.tag} | {s.id} | dur={s.duration_sec}s")