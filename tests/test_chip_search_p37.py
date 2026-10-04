"""
test_chip_search_p37.py — P3.7(2026-10-04)N3 chip 多选 + N2 search 框测试。

覆盖:
  - TestSongPoolListVisibleMultiTag — song_pool.list_visible 多 tag OR 合并
  - TestSongPoolSearchQuery — song_pool.list_visible q= 模糊搜索(title+artist)
  - TestApiSongsQueryParams — /api/songs 读 tags 多值 + q,旧 tag= 单值向后兼容
  - TestUiStoreToggleTag — stores/ui.ts tagFilters toggleTag/clearTags
  - TestUiStoreSearchDebounce — stores/ui.ts setSearch 200ms debounce + clearAllFilters
  - TestMusicViewChipSearch — views/MusicView.vue chip 多选 active + search input + clear-all 按钮
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPANION = ROOT / "companion"
MUSIC_WEB = COMPANION / "prisIragent-music-web.py"
SONG_POOL = COMPANION / "music" / "song_pool.py"

MVUE = COMPANION / "static" / "music-vue"
SRC = MVUE / "src"

for p in (str(ROOT), str(COMPANION)):
    if p not in sys.path:
        sys.path.insert(0, p)

from music.song_pool import SongPoolCatalog, SongMeta  # noqa: E402


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _make_pool() -> SongPoolCatalog:
    """手动造一个 6 首 / 3 tag 的 SongPoolCatalog 实例,不读 CSV。"""
    pool = SongPoolCatalog.__new__(SongPoolCatalog)
    pool.all_songs = [
        SongMeta("s1", "晴天", "周杰伦", "流行", 240, False, 0, 0, ""),
        SongMeta("s2", "稻香", "周杰伦", "流行", 220, False, 0, 0, ""),
        SongMeta("s3", "东风破", "周杰伦", "古风", 240, False, 0, 0, ""),
        SongMeta("s4", "Fade", "Alan Walker", "电子", 200, False, 0, 0, ""),
        SongMeta("s5", "小幸运", "田馥甄", "影视 OST", 230, False, 0, 0, ""),
        SongMeta("s6", "海阔天空", "Beyond", "摇滚", 320, False, 0, 0, ""),
    ]
    pool.visible_size = 6
    pool.visible_songs = list(pool.all_songs)
    return pool


# ============================================================
# TestSongPoolListVisibleMultiTag — 多 tag OR 合并
# ============================================================
class TestSongPoolListVisibleMultiTag(unittest.TestCase):
    """P3.7:list_visible(tags=List[str]) — 任一命中即返(OR 合并)。"""

    def test_or_multi_tag_returns_union(self):
        p = _make_pool()
        out = p.list_visible(tags=["流行", "古风"])
        ids = sorted(s.id for s in out)
        # s1,s2 流行 + s3 古风
        self.assertEqual(ids, ["s1", "s2", "s3"])

    def test_single_tag_still_works(self):
        p = _make_pool()
        out = p.list_visible(tags=["流行"])
        ids = sorted(s.id for s in out)
        self.assertEqual(ids, ["s1", "s2"])

    def test_no_tag_returns_all_visible(self):
        p = _make_pool()
        out = p.list_visible(tags=[])
        self.assertEqual(len(out), 6)

    def test_unknown_tag_returns_empty(self):
        p = _make_pool()
        out = p.list_visible(tags=["不存在的标签"])
        self.assertEqual(out, [])

    def test_legacy_tag_kwarg_backward_compat(self):
        """P2.5+24 旧 `tag=` 单值参数仍 work。"""
        p = _make_pool()
        out = p.list_visible(tag="流行")
        ids = sorted(s.id for s in out)
        self.assertEqual(ids, ["s1", "s2"])


# ============================================================
# TestSongPoolSearchQuery — title/artist 模糊搜索
# ============================================================
class TestSongPoolSearchQuery(unittest.TestCase):
    """P3.7:list_visible(q=) — title + artist case-insensitive 简单包含。"""

    def test_title_match(self):
        p = _make_pool()
        out = p.list_visible(q="晴天")
        ids = [s.id for s in out]
        self.assertEqual(ids, ["s1"])

    def test_artist_match(self):
        p = _make_pool()
        out = p.list_visible(q="周")
        ids = sorted(s.id for s in out)
        # s1, s2, s3 都是周杰伦
        self.assertEqual(ids, ["s1", "s2", "s3"])

    def test_case_insensitive(self):
        p = _make_pool()
        # 大写 ALAN,匹配 Alan Walker
        out = p.list_visible(q="ALAN")
        ids = [s.id for s in out]
        self.assertEqual(ids, ["s4"])

    def test_empty_q_returns_all(self):
        p = _make_pool()
        out = p.list_visible(q="")
        self.assertEqual(len(out), 6)

    def test_no_match_returns_empty(self):
        p = _make_pool()
        out = p.list_visible(q="xyz不存在xyz")
        self.assertEqual(out, [])

    def test_tag_and_query_compose(self):
        """P3.7:chip ∩ search(结果累乘)。"""
        p = _make_pool()
        # tag=流行 AND q=稻香 → 只有 s2
        out = p.list_visible(tags=["流行"], q="稻香")
        ids = [s.id for s in out]
        self.assertEqual(ids, ["s2"])
        # tag=流行 AND q=东风破 → 0 首(东风破是古风,不在流行)
        out2 = p.list_visible(tags=["流行"], q="东风破")
        self.assertEqual(out2, [])


# ============================================================
# TestApiSongsQueryParams — /api/songs 读 tags + q
# ============================================================
class TestApiSongsQueryParams(unittest.TestCase):
    """P3.7:api_songs 路由 getall('tag') + 'q' 读取。"""

    def test_api_songs_uses_getall_tag(self):
        c = _read(MUSIC_WEB)
        idx = c.find("async def api_songs")
        self.assertGreater(idx, -1)
        snippet = c[idx: idx + 1000]
        self.assertIn("getall", snippet)
        self.assertIn('"tag"', snippet)
        self.assertIn('"q"', snippet)

    def test_api_songs_returns_sel_tags_and_q(self):
        c = _read(MUSIC_WEB)
        idx = c.find("async def api_songs")
        snippet = c[idx: idx + 1200]
        # 回显 sel_tags / q / tags(全 14 tag)/ songs
        self.assertIn("sel_tags", snippet)
        self.assertIn("q=q", snippet) or self.assertIn("q=q", snippet.replace(" ", ""))
        self.assertIn("songs", snippet)
        self.assertIn("list_tags", snippet)

    def test_api_songs_passes_to_list_visible(self):
        c = _read(MUSIC_WEB)
        idx = c.find("async def api_songs")
        snippet = c[idx: idx + 1200]
        # 调 list_visible(tags=, q=)
        self.assertRegex(snippet, r"list_visible\(\s*tags\s*=\s*sel_tags")
        self.assertIn("q=q", snippet)


# ============================================================
# TestUiStoreToggleTag — stores/ui.ts tagFilters 多选 toggle
# ============================================================
class TestUiStoreToggleTag(unittest.TestCase):
    """P3.7:useUiStore.toggleTag(t) — 已存在则移除,否则 push。"""

    def test_store_exposes_tagfilters(self):
        c = _read(SRC / "stores" / "ui.ts")
        self.assertIn("tagFilters", c)
        self.assertIn("function toggleTag", c)
        self.assertIn("function clearTags", c)

    def test_toggle_tag_push(self):
        """toggleTag 不存在时 push。"""
        c = _read(SRC / "stores" / "ui.ts")
        idx = c.find("function toggleTag")
        snippet = c[idx: idx + 400]
        # indexOf + splice(push)
        self.assertIn("indexOf", snippet)
        self.assertIn("splice", snippet)
        self.assertIn("push", snippet)

    def test_legacy_settag_uses_tagfilters(self):
        """setTag 旧 API 内部归 tagFilters = [tag]。"""
        c = _read(SRC / "stores" / "ui.ts")
        # setTag 函数定义
        self.assertIn("async function setTag", c)
        # setTag 引用 tagFilters
        idx = c.find("async function setTag")
        snippet = c[idx: idx + 250]
        self.assertIn("tagFilters", snippet)

    def test_returns_tagfilters_in_setup(self):
        c = _read(SRC / "stores" / "ui.ts")
        # 末位 return 块暴露(找出最后出现的 "return {" 后的内容)
        idx = c.rfind("return {")
        self.assertGreater(idx, -1)
        return_block = c[idx:]
        self.assertIn("tagFilters", return_block)


# ============================================================
# TestUiStoreSearchDebounce — 200ms debounce
# ============================================================
class TestUiStoreSearchDebounce(unittest.TestCase):
    """P3.7:useUiStore.setSearch(q) — 200ms debounce 后 set searchQuery。"""

    def test_setsearch_clears_old_timer(self):
        c = _read(SRC / "stores" / "ui.ts")
        idx = c.find("function setSearch")
        snippet = c[idx: idx + 500]
        self.assertIn("clearTimeout", snippet)
        self.assertIn("setTimeout", snippet)

    def test_setsearch_has_200ms_constant(self):
        c = _read(SRC / "stores" / "ui.ts")
        # 200ms debounce 常量
        self.assertIn("SEARCH_DEBOUNCE_MS", c)
        self.assertIn("200", c)

    def test_setsearch_updates_query_after_debounce(self):
        c = _read(SRC / "stores" / "ui.ts")
        idx = c.find("function setSearch")
        snippet = c[idx: idx + 600]
        # setTimeout 内 set searchQuery.value = next
        self.assertIn("searchQuery.value", snippet)

    def test_clear_all_filters_clears_both(self):
        c = _read(SRC / "stores" / "ui.ts")
        idx = c.find("function clearAllFilters")
        snippet = c[idx: idx + 400]
        self.assertIn("clearTags", snippet)
        self.assertIn("searchInput", snippet)
        self.assertIn("searchQuery", snippet)

    def test_exposes_searchinput_searchquery(self):
        c = _read(SRC / "stores" / "ui.ts")
        idx = c.rfind("return {")
        return_block = c[idx:]
        self.assertIn("searchInput", return_block)
        self.assertIn("searchQuery", return_block)
        self.assertIn("setSearch", return_block)
        self.assertIn("clearAllFilters", return_block)


# ============================================================
# TestMusicViewChipSearch — MusicView 集成
# ============================================================
class TestMusicViewChipSearch(unittest.TestCase):
    """P3.7:MusicView.vue 顶栏 chip 多选 active + 搜索 input + clear-all 按钮。"""

    def test_musicview_has_search_input(self):
        c = _read(SRC / "views" / "MusicView.vue")
        self.assertIn('type="search"', c)
        self.assertIn('class="search-input"', c)
        # placeholder 搜歌名/歌手
        self.assertIn("搜歌名/歌手", c)

    def test_musicview_has_clear_all_button(self):
        c = _read(SRC / "views" / "MusicView.vue")
        self.assertIn("btn-clear", c)
        # onClearAll handler
        self.assertIn("onClearAll", c)
        # 调 ui.clearAllFilters
        self.assertIn("ui.clearAllFilters", c)

    def test_musicview_chip_uses_tagfilters_includes(self):
        """chip active class 走 ui.tagFilters.includes(t)。"""
        c = _read(SRC / "views" / "MusicView.vue")
        # 旧 ui.tagFilter 单值比较已替换
        self.assertIn("ui.tagFilters.includes", c)
        self.assertNotIn("t === ui.tagFilter", c)

    def test_musicview_multi_chip_display(self):
        """顶栏已选 tag 列表渲染 — v-for over tagFilters。"""
        c = _read(SRC / "views" / "MusicView.vue")
        # 顶栏 .tag-active v-for
        self.assertIn("v-for=\"t in ui.tagFilters\"", c)

    def test_musicview_loadsongs_multi_tag_query(self):
        """loadSongs 拼 ?tag=a&tag=b&q=xxx。"""
        c = _read(SRC / "views" / "MusicView.vue")
        idx = c.find("async function loadSongs")
        snippet = c[idx: idx + 600]
        # 拼 tags=
        self.assertIn("ui.tagFilters", snippet)
        self.assertIn("ui.searchQuery", snippet)
        # encodeURIComponent
        self.assertIn("encodeURIComponent", snippet)

    def test_musicview_searchinput_handler(self):
        """onSearchInput → ui.setSearch(value)。"""
        c = _read(SRC / "views" / "MusicView.vue")
        # onSearchInput 函数 + 调 setSearch
        self.assertIn("function onSearchInput", c)
        self.assertIn("ui.setSearch", c)

    def test_musicview_watches_search_and_tags(self):
        """watch ui.searchQuery + tagFilters 触发 loadSongs。"""
        c = _read(SRC / "views" / "MusicView.vue")
        self.assertIn("watch(", c)
        self.assertIn("ui.searchQuery", c)
        self.assertIn("ui.tagFilters", c)

    def test_musicview_respin_only_when_no_tag(self):
        """btn-respin v-if:ui.tagFilters.length === 0。"""
        c = _read(SRC / "views" / "MusicView.vue")
        # 旧 !ui.tagFilter 单值判断已替换
        self.assertIn("ui.tagFilters.length === 0", c)
        self.assertNotIn('v-if="!ui.tagFilter"', c)

    def test_musicview_left_flex_wrap(self):
        """.left CSS 加 flex-wrap: wrap(顶栏 7+ 元素折行)。"""
        c = _read(SRC / "views" / "MusicView.vue")
        self.assertIn("flex-wrap: wrap", c)

    def test_musicview_has_search_input_css(self):
        """搜索框 CSS 块 .search-input 存在 + focus 态。"""
        c = _read(SRC / "views" / "MusicView.vue")
        self.assertIn(".search-input {", c)
        self.assertIn(".search-input:focus", c)
        self.assertIn(".search-input::placeholder", c)


if __name__ == "__main__":
    unittest.main()
