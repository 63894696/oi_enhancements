# -*- coding: utf-8 -*-
"""
song_pool.py — P2.5+23(2026-10-03)真歌名池

从 companion/music/song_pool_template.csv 读歌名 → 启动时随机洗牌 →
前端 GET /api/songs 拿 60 首可见。

设计:
  - 不依赖 lx_runtime 协议层(mock.js 仍负责 musicUrl 返 googleapis mp3)。
  - 用户填好 CSV,启动时 Python 端直接持有。
  - 每次启动 shuffle = 60 首不同顺序,符合用户「不每次打开都相同」诉求。

CSV 格式:`歌名,歌手,标签`(UTF-8)
  - 行 17 / 行 137 有 `""Big Hero 6""` 双引号嵌套 → 用标准 csv module 自动容错。
  - 标题 / 歌手 / 标签都可能为空(允许空列)。
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

_POOL_TEMPLATE = Path(__file__).resolve().parent / "song_pool_template.csv"


@dataclass
class SongMeta:
    id: str            # sha1(title|artist) 前 12 位
    title: str
    artist: str
    tag: str

    def to_dict(self) -> dict:
        return asdict(self)


def _safe_id(title: str, artist: str) -> str:
    return hashlib.sha1(f"{title}|{artist}".encode("utf-8")).hexdigest()[:12]


def _parse_csv_rows(path: Path) -> Iterable[SongMeta]:
    """读 CSV,容错双引号嵌套 / 空列 / 空标题。

    标准 csv module:`"Immortals(From ""Big Hero 6""/Soundtrack)"` 会被
    自动解为 `Immortals(From "Big Hero 6"/Soundtrack)`(单引号)。
    """
    if not path.exists():
        return []
    out: List[SongMeta] = []
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return []
        for row in reader:
            if not row:
                continue
            title = (row[0] or "").strip() if len(row) > 0 else ""
            if not title:
                continue
            artist = (row[1] or "").strip() if len(row) > 1 else ""
            tag = (row[2] or "").strip() if len(row) > 2 else ""
            out.append(SongMeta(id=_safe_id(title, artist),
                                title=title, artist=artist, tag=tag))
    return out


class SongPoolCatalog:
    """歌单目录:全量加载 + 随机 60 首可见 + 标签过滤。"""

    DEFAULT_VISIBLE = 60

    def __init__(self, csv_path: Optional[Path] = None, visible_size: int = DEFAULT_VISIBLE):
        self.csv_path = Path(csv_path) if csv_path else _POOL_TEMPLATE
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
        # 同 (title.lower, artist.lower) 去重
        seen = set()
        deduped: List[SongMeta] = []
        for r in rows:
            key = (r.title.lower(), r.artist.lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)
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
    def list_visible(self, tag: Optional[str] = None) -> List[SongMeta]:
        if tag:
            return [s for s in self.visible_songs if s.tag == tag]
        return list(self.visible_songs)

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
        print(f"  - {s.title} | {s.artist} | {s.tag} | {s.id}")