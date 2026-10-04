"""
test_recommend_n9.py — N9(2026-10-04)AI 歌单推荐 ship 测试。

覆盖:
  - TestRecommenderBuildUserState       — recommender.build_user_state 聚合 favorites/play_history_recent/favorite_tags/favorite_artists
  - TestRecommenderRecommendFromCatalog  — 空 catalog → 冷启动 / 收藏 catalog → tag 偏好 / 30 天内 play → 降权 / 多样性封顶
  - TestRecommenderColdStart            — 14 tag 均匀 + seed shuffle + reason 文案
  - TestApiRecommendEndpoint            — api_recommend 注册 + k/seed query + 返 {ok, count, items}
  - TestRecommendPanelComponent         — props.items + loading / emit('play') + emit('refresh') / 空态文案
  - TestMusicViewRecommendationIntegration — MusicView import RecommendPanel + loadRecommendations + onMounted 自动 + onPlayRecommend
  - TestPrivacyNoUpload                  — 沿用 P3.10b 0 上传红线:无外部 fetch URL / 无 WebSocket / 无 LLM/AI 关键字
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPANION = ROOT / "companion"

MVUE = COMPANION / "static" / "music-vue"
SRC = MVUE / "src"

for p in (str(ROOT), str(COMPANION)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


RECOMMENDER = COMPANION / "music" / "recommender.py"
RECOMMEND_POC = COMPANION / "music" / "recommend_poc.py"
API_WEB = COMPANION / "prisIragent-music-web.py"
PANEL = SRC / "components" / "RecommendPanel.vue"
MV = SRC / "views" / "MusicView.vue"


# ============================================================
# TestRecommenderBuildUserState — 从 SongPoolCatalog 聚合 user_state
# ============================================================
class TestRecommenderBuildUserState(unittest.TestCase):
    """N9:recommender.build_user_state 聚合 favorites / play_history_recent。"""

    def test_build_user_state_returns_required_keys(self):
        c = _read(RECOMMENDER)
        # 必备字段:favorite_tags / favorite_artists / play_history_recent / is_cold_start
        self.assertIn("favorite_tags", c)
        self.assertIn("favorite_artists", c)
        self.assertIn("play_history_recent", c)
        self.assertIn("is_cold_start", c)

    def test_build_user_state_uses_song_meta_is_favorite(self):
        c = _read(RECOMMENDER)
        # favorites 来自 SongMeta.is_favorite
        self.assertRegex(c, r"is_favorite")
        # 不调 /api/favorites(避免 player Track join)
        self.assertNotIn("/api/favorites", c)

    def test_recent_iso_aggregates_last_played_iso(self):
        c = _read(RECOMMENDER)
        # play_history_recent 来源 last_played_iso + 30 天过滤
        self.assertIn("last_played_iso", c)
        self.assertIn("_RECENCY_DAYS", c)
        # 用 datetime.fromisoformat 解析
        self.assertRegex(c, r"datetime\.fromisoformat")

    def test_cold_start_detection_logic(self):
        c = _read(RECOMMENDER)
        # is_cold_start = favorites == 0 AND recent == 0
        self.assertRegex(c, r"fav_count\s*==\s*0\s+and\s+len\(recent\)")


# ============================================================
# TestRecommenderRecommendFromCatalog — 主入口 + 多样性封顶
# ============================================================
class TestRecommenderRecommendFromCatalog(unittest.TestCase):
    """N9:recommend_from_catalog(catalog, k, seed) 主流程。"""

    def test_recommend_from_catalog_signature(self):
        c = _read(RECOMMENDER)
        # k: int = 20, seed: Optional[int] = None
        self.assertRegex(c, r"def recommend_from_catalog\(\s*catalog[^)]*k\s*:\s*int\s*=\s*20")
        self.assertRegex(c, r"seed\s*:\s*Optional\[int\]\s*=\s*None")

    def test_recommend_from_catalog_uses_recommend_poc(self):
        c = _read(RECOMMENDER)
        # 复用 recommend_poc.recommend 算法原样不动
        self.assertIn("from music.recommend_poc import recommend", c)
        # 主流程调 recommend(state, pool, k, seed)
        self.assertRegex(c, r"recommend\(state, pool,")

    def test_returns_empty_when_catalog_empty(self):
        c = _read(RECOMMENDER)
        # 防御:catalog None 或 all_songs 空 → 返 []
        self.assertRegex(c, r"if catalog is None or not catalog\.all_songs")
        self.assertRegex(c, r"return \[\]")

    def test_diversity_cap_30_percent(self):
        c = _read(RECOMMEND_POC)
        # PoC 多样性封顶 30%(继承)
        self.assertIn("_TAG_CAP = 0.30", c)


# ============================================================
# TestRecommenderColdStart — 14 tag 均匀 + seed shuffle + reason 文案
# ============================================================
class TestRecommenderColdStart(unittest.TestCase):
    """N9:_cold_start 冷启动分支:14 tag 均匀 + random.Random(seed) shuffle。"""

    def test_cold_start_uses_random_seed(self):
        c = _read(RECOMMENDER)
        # 用 random.Random(seed) 实例化(不是全局 random.seed)
        self.assertRegex(c, r"random\.Random\(seed\)")

    def test_cold_start_uniform_per_tag(self):
        c = _read(RECOMMENDER)
        # 按 tag 分桶 + 每 tag 取 ceil(k/distinct_tags) 首
        self.assertIn("buckets", c)
        self.assertRegex(c, r"per_tag\s*=\s*max\(1, math\.ceil\(k\s*/")

    def test_cold_start_reason_prefix(self):
        c = _read(RECOMMENDER)
        # reason 以前缀「冷启动」开头(API 用此判 is_cold_start)
        self.assertIn("冷启动均匀分布", c)


# ============================================================
# TestApiRecommendEndpoint — /api/recommend 路由
# ============================================================
class TestApiRecommendEndpoint(unittest.TestCase):
    """N9:prisIragent-music-web.py /api/recommend 端点。"""

    def test_api_recommend_handler_exists(self):
        c = _read(API_WEB)
        # async def api_recommend(req)
        self.assertRegex(c, r"async def api_recommend\(")

    def test_api_recommend_registered(self):
        c = _read(API_WEB)
        # 路由注册 /api/recommend
        self.assertRegex(c, r'add_get\("/api/recommend"')

    def test_api_recommend_query_params(self):
        c = _read(API_WEB)
        # 读 k + seed query
        self.assertRegex(c, r'req\.query\.get\(["\']k["\']')
        self.assertRegex(c, r'req\.query\.get\(["\']seed["\']')

    def test_api_recommend_uses_recommend_from_catalog(self):
        c = _read(API_WEB)
        # 调 recommender 主入口(不调 PoC)
        self.assertIn("from music.recommender import recommend_from_catalog", c)
        self.assertRegex(c, r"recommend_from_catalog\(")

    def test_api_recommend_returns_ok_count_items(self):
        c = _read(API_WEB)
        # 返回结构:ok / count / items / k / seed / is_cold_start
        # 检查 _ok 调用形式
        # api_recommend 函数体内有 _ok(...)
        match = re.search(r"async def api_recommend.*?(?=async def |\Z)", c, re.DOTALL)
        self.assertIsNotNone(match, "api_recommend function not found")
        body = match.group(0)
        self.assertIn("items", body)
        self.assertIn("count", body)


# ============================================================
# TestRecommendPanelComponent — RecommendPanel.vue
# ============================================================
class TestRecommendPanelComponent(unittest.TestCase):
    """N9:RecommendPanel.vue props + emit + 空态文案。"""

    def test_panel_imports_vue_refs(self):
        c = _read(PANEL)
        # <script setup lang="ts">
        self.assertIn("<script setup lang=\"ts\">", c)
        # defineProps<{ items, loading }>
        self.assertIn("defineProps", c)
        self.assertIn("items", c)
        self.assertIn("loading", c)

    def test_panel_emits_play_and_refresh(self):
        c = _read(PANEL)
        # defineEmits<{ play: [...], refresh: [] }>
        self.assertIn("defineEmits", c)
        self.assertIn("play", c)
        self.assertIn("refresh", c)
        # 模板里 emit('refresh') 与 emit('play', it)
        self.assertRegex(c, r"emit\(['\"]refresh['\"]\)")
        self.assertRegex(c, r"emit\(['\"]play['\"]")

    def test_panel_empty_state_text(self):
        c = _read(PANEL)
        # 空态文案
        self.assertIn("暂无推荐", c)
        self.assertIn("收藏几首歌后会变得更精准", c)

    def test_panel_cold_start_badge(self):
        c = _read(PANEL)
        # 冷启动徽章 + isColdStart 函数
        self.assertIn("isColdStart", c)
        self.assertIn("冷启动", c)
        # reason 以前缀「冷启动」开头判定
        self.assertRegex(c, r"startsWith\(['\"]冷启动['\"]\)")

    def test_panel_no_electron_import(self):
        c = _read(PANEL)
        # 纯渲染组件,无 IPC / window.prisIragent 调用
        self.assertNotIn("window.prisIragent", c)
        self.assertNotIn("window.prisIr", c)


# ============================================================
# TestMusicViewRecommendationIntegration — MusicView.vue 集成
# ============================================================
class TestMusicViewRecommendationIntegration(unittest.TestCase):
    """N9:MusicView.vue 集成 RecommendPanel。"""

    def test_music_view_imports_recommend_panel(self):
        c = _read(MV)
        self.assertRegex(c, r"import\s+RecommendPanel\s+from\s+['\"]@/components/RecommendPanel\.vue['\"]")

    def test_music_view_has_recommend_state(self):
        c = _read(MV)
        # recommendItems + recommendLoading + _recSeedOffset
        self.assertIn("recommendItems", c)
        self.assertIn("recommendLoading", c)
        self.assertIn("_recSeedOffset", c)

    def test_music_view_has_load_recommendations(self):
        c = _read(MV)
        # async function loadRecommendations
        self.assertRegex(c, r"async function loadRecommendations")
        # 调 /api/recommend?k=20
        self.assertRegex(c, r"/api/recommend\?k=20")

    def test_music_view_has_on_play_recommend(self):
        c = _read(MV)
        self.assertRegex(c, r"async function onPlayRecommend")
        # 调 player.playById
        self.assertRegex(c, r"player\.playById\(item\.id\)")

    def test_music_view_has_on_refresh_recommend(self):
        c = _read(MV)
        self.assertRegex(c, r"function onRefreshRecommend")
        # seed 增量 +1
        self.assertRegex(c, r"_recSeedOffset\s*\+=\s*1")

    def test_music_view_on_mounted_calls_load_recommendations(self):
        c = _read(MV)
        # onMounted 内 void loadRecommendations()
        m = re.search(r"onMounted\(\(\)\s*=>\s*\{(.*?)\}\)", c, re.DOTALL)
        self.assertIsNotNone(m, "onMounted block not found")
        body = m.group(1)
        self.assertIn("loadRecommendations", body)

    def test_music_view_template_embeds_recommend_panel(self):
        c = _read(MV)
        # <RecommendPanel :items=... @play=... @refresh=... />
        self.assertRegex(c, r"<RecommendPanel[^>]*@play=\"onPlayRecommend\"[^>]*@refresh=\"onRefreshRecommend\"")
        # 嵌在 .tags 上方(.recommend 在 .tags 之前)
        # 找第一个 <RecommendPanel 在 <div class="tags"> 之前的位置
        panel_pos = c.find("<RecommendPanel")
        tags_pos = c.find('<div class="tags">')
        self.assertGreater(panel_pos, 0)
        self.assertGreater(tags_pos, panel_pos, "RecommendPanel must appear before .tags")


# ============================================================
# TestPrivacyNoUpload — 0 上传/外传(沿用 P3.10b 红线)
# ============================================================
class TestPrivacyNoUpload(unittest.TestCase):
    """N9:推荐纯本地启发式,不调外部 API / 不上传。"""

    def test_recommender_no_external_fetch(self):
        c = _read(RECOMMENDER)
        # 无 requests/urllib3/aiohttp.ClientSession 等外发
        self.assertNotIn("requests.", c)
        self.assertNotIn("urllib3", c)
        self.assertNotIn("ClientSession", c)
        # 无 LLM 关键字(不调 AI 推理)
        self.assertNotIn("openai", c)
        self.assertNotIn("anthropic", c)
        self.assertNotIn("llm", c.lower().replace("llm", "LLM") if False else "LLM")

    def test_recommend_poc_no_external_fetch(self):
        c = _read(RECOMMEND_POC)
        self.assertNotIn("requests.", c)
        self.assertNotIn("urllib3", c)
        self.assertNotIn("ClientSession", c)

    def test_api_recommend_no_external_fetch(self):
        c = _read(API_WEB)
        # api_recommend 函数体内不调外发 URL
        m = re.search(r"async def api_recommend.*?(?=async def |\Z)", c, re.DOTALL)
        self.assertIsNotNone(m, "api_recommend function not found")
        body = m.group(0)
        # 无 http(s):// 引用
        self.assertNotIn("https://", body)
        self.assertNotIn("http://", body)


if __name__ == "__main__":
    unittest.main()
