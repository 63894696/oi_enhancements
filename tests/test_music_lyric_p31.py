"""
test_music_lyric_p31.py — P3.1(2026-10-03)歌词窗进度条拖动跳转测试。

覆盖:
  - TestLyricStoreSeek — lyric store 加 seek/seekPct + 走 /api/cmd
  - TestLyricProgressBar — 新组件 5 关键字段(props/emit/三层/dragging/no-drag)
  - TestLyricOnlyViewProgressBar — 视图集成 LyricProgressBar + #progress-zone
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MUSIC_VUE = ROOT / "companion" / "static" / "music-vue"


# ============================================================
# lyric store seek action
# ============================================================
class TestLyricStoreSeek(unittest.TestCase):
    """P3.1(2026-10-03):lyric store 加 seek/seekPct,走 /api/cmd {action:'seek', offset}。"""

    def _read(self):
        return (MUSIC_VUE / "src" / "stores" / "lyric.ts").read_text(encoding="utf-8")

    def test_seek_function_defined(self):
        content = self._read()
        self.assertIn("async function seek", content, "must define seek()")

    def test_seek_calls_api_cmd(self):
        content = self._read()
        # seek 函数体内必调 /api/cmd seek
        idx = content.find("async function seek")
        self.assertGreater(idx, 0)
        snippet = content[idx: idx + 800]
        self.assertIn("action: 'seek'", snippet,
                      "seek must call /api/cmd with action='seek'")
        self.assertIn("offset:", snippet,
                      "seek must pass offset param")

    def test_seek_optimistic_update(self):
        """seek 必须乐观更新 progress.value + CSS var,不阻塞 UI。"""
        content = self._read()
        idx = content.find("async function seek")
        snippet = content[idx: idx + 800]
        self.assertIn("progress.value = target", snippet,
                      "must optimistically update progress before await")
        self.assertIn("--progress", snippet,
                      "must update CSS var --progress")

    def test_seek_clamps_to_duration(self):
        """seek 输入必须 clamp 到 [0, duration] 防越界。"""
        content = self._read()
        idx = content.find("async function seek")
        snippet = content[idx: idx + 800]
        self.assertIn("Math.max(0", snippet, "must clamp lower bound")
        self.assertIn("Math.min", snippet, "must clamp upper bound")

    def test_seek_inflight_throttle(self):
        """高频调用必须有节流(seekInFlight guard)。"""
        content = self._read()
        self.assertIn("seekInFlight", content,
                      "must have seekInFlight throttle flag")
        self.assertIn("if (seekInFlight) return", content,
                      "must early-return on in-flight")

    def test_seek_pct_function(self):
        content = self._read()
        self.assertIn("async function seekPct", content,
                      "must have seekPct convenience wrapper")
        idx = content.find("async function seekPct")
        snippet = content[idx: idx + 400]
        self.assertIn("duration.value", snippet, "seekPct must multiply by duration")
        self.assertIn("await seek", snippet, "seekPct must call seek")

    def test_seek_exported_in_store_return(self):
        content = self._read()
        self.assertIn("seek, seekPct", content,
                      "seek/seekPct must be in store return")
        # 必须有 P3.1 注释
        self.assertIn("P3.1", content, "must mark P3.1 ship")


# ============================================================
# LyricProgressBar 组件
# ============================================================
class TestLyricProgressBar(unittest.TestCase):
    """P3.1(2026-10-03):LyricProgressBar.vue 组件 — 不绑 player store,接受 props/emit。"""

    def _read(self):
        return (MUSIC_VUE / "src" / "components" / "LyricProgressBar.vue").read_text(encoding="utf-8")

    def test_file_exists(self):
        p = MUSIC_VUE / "src" / "components" / "LyricProgressBar.vue"
        self.assertTrue(p.exists(), f"missing: {p}")

    def test_define_props(self):
        content = self._read()
        self.assertIn("defineProps", content, "must use defineProps")
        self.assertIn("current:", content, "must accept current prop")
        self.assertIn("duration:", content, "must accept duration prop")

    def test_define_emits_seek(self):
        content = self._read()
        self.assertIn("defineEmits", content, "must use defineEmits")
        self.assertIn("emit('seek'", content, "must emit seek event")
        # 必须传 offset 数值
        self.assertRegex(content, r"emit\(['\"]seek['\"],\s*[a-zA-Z_]+\s*\*\s*[a-zA-Z_.]+",
                         "must emit seek with offset = pct * duration")

    def test_three_layers(self):
        """三层进度条:bg + active + thumb(借鉴 Vue-mmPlayer)。"""
        content = self._read()
        for cls in ("lp-bg", "lp-active", "lp-thumb"):
            self.assertIn(cls, content, f"missing layer {cls}")

    def test_dragging_state(self):
        """拖动态 must 控制 thumb 显示 + 临时 pct。"""
        content = self._read()
        self.assertIn("dragging", content, "must have dragging ref")
        self.assertIn("onDown", content, "must have onDown handler")
        self.assertIn("onMove", content, "must have onMove handler")
        self.assertIn("onUp", content, "must have onUp handler")

    def test_no_drag_app_region(self):
        """进度条组件必 -webkit-app-region: no-drag,不抢顶部拖动条。"""
        content = self._read()
        self.assertIn("-webkit-app-region: no-drag", content,
                      "must have no-drag to avoid stealing drag-bar")

    def test_no_player_store_import(self):
        """LyricProgressBar 必须不绑 player store(独立组件,lyric store 自控制)。"""
        content = self._read()
        self.assertNotIn("usePlayerStore", content,
                         "must NOT bind player store (independent component)")
        self.assertNotIn("from '@/stores/player'", content,
                         "must NOT import player store")


# ============================================================
# LyricOnlyView 集成
# ============================================================
class TestLyricOnlyViewProgressBar(unittest.TestCase):
    """P3.1(2026-10-03):LyricOnlyView 加 LyricProgressBar + #progress-zone + onSeek。"""

    def _read(self):
        return (MUSIC_VUE / "src" / "views" / "LyricOnlyView.vue").read_text(encoding="utf-8")

    def test_imports_lyric_progress_bar(self):
        content = self._read()
        self.assertIn("import LyricProgressBar", content,
                      "must import LyricProgressBar component")

    def test_on_seek_handler(self):
        content = self._read()
        self.assertIn("function onSeek", content,
                      "must have onSeek handler")
        self.assertIn("lyric.seek", content,
                      "onSeek must call lyric.seek (NOT player.seek)")

    def test_progress_zone_in_template(self):
        content = self._read()
        self.assertIn("id=\"progress-zone\"", content,
                      "must have #progress-zone container")
        self.assertIn("<LyricProgressBar", content,
                      "must render LyricProgressBar component")
        self.assertIn("@seek=\"onSeek\"", content,
                      "must bind onSeek to @seek emit")

    def test_progress_zone_no_drag(self):
        """#progress-zone 必 -webkit-app-region: no-drag 防抢拖动。"""
        content = self._read()
        idx = content.find("#progress-zone")
        self.assertGreater(idx, 0)
        snippet = content[idx: idx + 600]
        self.assertIn("-webkit-app-region: no-drag", snippet,
                      "#progress-zone must be no-drag")

    def test_progress_zone_position(self):
        """#progress-zone 必 fixed + bottom 22px(在 #meta 之上)。"""
        content = self._read()
        idx = content.find("#progress-zone")
        snippet = content[idx: idx + 600]
        self.assertIn("position: fixed", snippet)
        self.assertIn("bottom: 22px", snippet,
                      "must position above #meta (22px above bottom)")


if __name__ == "__main__":
    unittest.main()
