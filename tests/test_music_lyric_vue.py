"""
test_music_lyric_vue.py — P2.5+25(2026-10-03)桌面歌词独立窗测试。

覆盖:
  - Vue lyric 入口结构(lyric.html / src/lyric.ts / LyricOnlyView.vue / lyric store / lyric.css)
  - 独立 Pinia store 模式(bootstrap + 二分查找 + ws 订阅 + dblclick handler)
  - Vite 多入口构建配置
  - Electron shell `_CHILD_SPEC.lyric` + transparent/frame:false/alwaysOnTop 字段
  - IPC `shell:openLyric` + `shell:closeLyric` 白名单
  - preload.js 暴露 prisIragent.openLyric/closeLyric
  - MiniBar 🎤 按钮 + onOpenLyric handler
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPANION = ROOT / "companion"
MUSIC_VUE = COMPANION / "static" / "music-vue"
SHELL = ROOT / "prisiragent-shell"

for p in (str(ROOT), str(COMPANION)):
    if p not in sys.path:
        sys.path.insert(0, p)


# ============================================================
# Vue lyric 独立窗文件结构
# ============================================================
class TestLyricVueStructure(unittest.TestCase):
    """P2.5+25(2026-10-03):桌面歌词独立窗 5 个新文件齐全。"""

    def test_lyric_html(self):
        p = MUSIC_VUE / "lyric.html"
        self.assertTrue(p.exists(), f"missing: {p}")
        content = p.read_text(encoding="utf-8")
        self.assertIn('id="app"', content, "missing #app mount point")
        self.assertIn("/src/lyric.ts", content, "missing lyric.ts script")
        self.assertIn("桌面歌词", content, "missing 桌面歌词 title")

    def test_src_lyric_ts(self):
        p = MUSIC_VUE / "src" / "lyric.ts"
        self.assertTrue(p.exists(), f"missing: {p}")
        content = p.read_text(encoding="utf-8")
        self.assertIn("createApp", content, "must createApp")
        self.assertIn("createPinia", content, "must createPinia for store")
        self.assertIn("LyricOnlyView", content, "must mount LyricOnlyView")
        self.assertIn("lyric.css", content, "must import lyric.css")

    def test_views_lyric_only(self):
        p = MUSIC_VUE / "src" / "views" / "LyricOnlyView.vue"
        self.assertTrue(p.exists(), f"missing: {p}")
        content = p.read_text(encoding="utf-8")
        # 关键设计:顶部 drag-bar + 主歌词区 + 双击关闭 + conn tag
        self.assertIn('id="drag-bar"', content, "missing drag-bar")
        self.assertIn('id="lyrics-stage"', content, "missing lyrics-stage")
        self.assertIn('@dblclick="onDblClick"', content, "missing dblclick close handler")
        self.assertIn("closeLyric", content, "must call closeLyric IPC")
        self.assertIn('id="conn-tag"', content, "missing connection tag")
        self.assertIn("useLyricStore", content, "must use lyric store")

    def test_stores_lyric_ts(self):
        p = MUSIC_VUE / "src" / "stores" / "lyric.ts"
        self.assertTrue(p.exists(), f"missing: {p}")
        content = p.read_text(encoding="utf-8")
        # 必须独立 defineStore('lyric', ...) 不依赖 player store
        self.assertIn("defineStore('lyric'", content, "missing lyric store id")
        self.assertIn("useLyricStore", content, "missing export")
        # ws /ws/lyrics 订阅
        self.assertIn("wsConnect", content, "must use wsConnect helper")
        self.assertIn("/ws/lyrics", content, "must subscribe /ws/lyrics")
        # 二分查找兜底
        self.assertIn("computeCurrentIdxByTime", content, "must implement binary search")
        self.assertIn("while (lo <= hi)", content, "must use binary search loop")
        # bootstrap 拉 cfg + state + lyric
        self.assertIn("bootstrap", content, "must implement bootstrap")
        self.assertIn("/api/state", content, "must fetch /api/state")
        self.assertIn("/api/agent/cfg/list", content, "must fetch cfg list")
        self.assertIn("/api/lyric", content, "must fetch /api/lyric")
        # cfg 字段全在
        for f in ("color", "font_size", "mode", "delay_ms", "opacity"):
            self.assertIn(f, content, f"missing cfg field {f}")
        # lyric_cfg 推送处理
        self.assertIn("lyric_cfg", content, "must handle lyric_cfg event")
        self.assertIn("lyric_line", content, "must handle lyric_line event")
        self.assertIn("music_state", content, "must handle music_state event")
        self.assertIn("applyCfgToCss", content, "must apply cfg to CSS vars")

    def test_styles_lyric_css(self):
        p = MUSIC_VUE / "src" / "styles" / "lyric.css"
        self.assertTrue(p.exists(), f"missing: {p}")
        content = p.read_text(encoding="utf-8")
        # 透明背景关键 — Electron transparent 窗才透桌面
        self.assertIn("background: transparent", content, "body must be transparent")
        # 拖动条 -webkit-app-region: drag
        self.assertIn("-webkit-app-region: drag", content, "missing drag region CSS")
        self.assertIn("#drag-bar", content, "missing drag-bar selector")
        # 主歌词区 + active 行渐变高亮
        self.assertIn("#lyrics-stage", content, "missing lyrics-stage selector")
        self.assertIn(".line.active", content, "missing line.active selector")
        self.assertIn("scale(1.06)", content, "missing active line scale")
        # 进度条
        self.assertIn("--progress", content, "missing progress CSS var")
        # 国画主题色
        self.assertIn("#c14d3a", content, "missing 国画红 #c14d3a")
        # CSS var 全部声明
        for v in ("--lyrics-color", "--lyrics-font-size", "--lyrics-opacity"):
            self.assertIn(v, content, f"missing CSS var {v}")


# ============================================================
# Lyric 关键模式(代码模式 grep)
# ============================================================
class TestLyricPatterns(unittest.TestCase):
    """代码模式必须存在:openLyric IPC / 双击关闭 / 托盘菜单 / preload 暴露 / MiniBar 按钮。"""

    def test_lyric_view_dblclick(self):
        """LyricOnlyView 双击 → closeLyric"""
        p = MUSIC_VUE / "src" / "views" / "LyricOnlyView.vue"
        content = p.read_text(encoding="utf-8")
        self.assertIn("window.prisIragent.closeLyric", content)
        self.assertIn("typeof", content, "must check typeof for dev-mode fallback")

    def test_minibar_lyric_button(self):
        """MiniBar 加 🎤 按钮 + onOpenLyric handler"""
        p = MUSIC_VUE / "src" / "components" / "MiniBar.vue"
        content = p.read_text(encoding="utf-8")
        self.assertIn("🎤", content, "missing 🎤 emoji")
        self.assertIn('class="ctrl lyric"', content, "missing .ctrl.lyric class")
        self.assertIn("onOpenLyric", content, "missing onOpenLyric handler")
        self.assertIn("prisIragent.openLyric", content, "must call openLyric IPC")
        # 优雅降级 — dev 模式无 IPC 时 toast 提示
        self.assertIn("ui.pushToast", content, "must toast fallback")
        self.assertIn("未就绪", content, "fallback message must mention IPC 未就绪")

    def test_minibar_position(self):
        """🎤 按钮必须在 ♥ 收藏之前(用户期望从左到右视觉顺序)"""
        p = MUSIC_VUE / "src" / "components" / "MiniBar.vue"
        content = p.read_text(encoding="utf-8")
        lyric_pos = content.find("ctrl lyric")
        fav_pos = content.find("ctrl fav")
        self.assertGreater(fav_pos, 0, "missing fav btn")
        self.assertGreater(lyric_pos, 0, "missing lyric btn")
        self.assertLess(lyric_pos, fav_pos,
                        "🎤 必须在 ♥ 收藏之前(更显眼位置)")


# ============================================================
# Vite 多入口构建
# ============================================================
class TestViteMultiEntry(unittest.TestCase):
    """P2.5+25(2026-10-03):vite 多入口 — main + lyric。"""

    def test_rollup_input(self):
        p = MUSIC_VUE / "vite.config.ts"
        content = p.read_text(encoding="utf-8")
        self.assertIn("rollupOptions", content)
        self.assertIn("input:", content)
        # 必须含 lyric 入口
        self.assertIn("lyric:", content)
        # main 入口需保留
        self.assertIn("main:", content)
        # 路径必须指向 lyric.html(不是旧 /lyrics Tauri 专属)
        self.assertIn("lyric.html", content)

    def test_build_outputs_both(self):
        """npm run build 后 dist 同时含 index.html + lyric.html。"""
        dist = MUSIC_VUE / "dist"
        if not dist.exists():
            self.skipTest(f"dist not built, run: cd {MUSIC_VUE} && npm run build")
        self.assertTrue((dist / "index.html").exists(), "missing dist/index.html")
        self.assertTrue((dist / "lyric.html").exists(), "missing dist/lyric.html")


# ============================================================
# Electron shell 扩展
# ============================================================
class TestElectronLyricSpec(unittest.TestCase):
    """P2.5+25(2026-10-03):_createChildWindow + _CHILD_SPEC.lyric + 托盘 + IPC。"""

    def _read_main(self):
        return (SHELL / "main.js").read_text(encoding="utf-8")

    def test_create_child_window_accepts_transparent(self):
        content = self._read_main()
        # _createChildWindow 必须接受 transparent/frame/alwaysOnTop/resizable/skipTaskbar 字段
        # 找 _createChildWindow 函数体
        self.assertIn("_createChildWindow", content)
        for f in ("spec.transparent", "spec.frame", "spec.alwaysOnTop",
                  "spec.resizable", "spec.skipTaskbar"):
            self.assertIn(f, content, f"_createChildWindow 缺少 {f} 字段")

    def test_child_spec_lyric(self):
        content = self._read_main()
        self.assertIn("_CHILD_SPEC", content)
        self.assertIn("lyric:", content, "missing _CHILD_SPEC.lyric")
        # 尺寸
        self.assertIn("transparent: true", content, "lyric 必须 transparent:true")
        self.assertIn("frame: false", content, "lyric 必须 frame:false")
        self.assertIn("alwaysOnTop: true", content, "lyric 必须 alwaysOnTop:true")
        self.assertIn("skipTaskbar: true", content, "lyric 必须 skipTaskbar:true")
        self.assertIn("桌面歌词", content, "lyric 窗标题")

    def test_open_lyric_window_helper(self):
        content = self._read_main()
        # 必须有 openLyricWindow helper(镜像 openMusicWindow 端口轮询)
        self.assertIn("function openLyricWindow", content, "missing openLyricWindow helper")
        self.assertIn("/music-vue/lyric.html", content, "must serve /music-vue/lyric.html")
        self.assertIn("lyric.html", content, "must point to lyric.html")
        # 必须 startMusic() + waitForPort + openInShell
        self.assertIn("openLyricWindow", content)
        # openInShell 第二参数 label="lyric"
        self.assertIn('"lyric"', content, "openInShell must use label='lyric'")

    def test_tray_menu_lyric_item(self):
        content = self._read_main()
        # 托盘菜单 🎤 桌面歌词
        self.assertIn("🎤 桌面歌词", content, "missing tray menu 🎤 桌面歌词")
        self.assertIn("openLyricWindow", content, "tray must click openLyricWindow")

    def test_ipc_open_lyric(self):
        content = self._read_main()
        self.assertIn('"shell:openLyric"', content, "missing shell:openLyric IPC handler")
        self.assertIn("openLyricWindow()", content, "shell:openLyric must call openLyricWindow")

    def test_ipc_close_lyric(self):
        content = self._read_main()
        self.assertIn('"shell:closeLyric"', content, "missing shell:closeLyric IPC handler")
        self.assertIn("childWindows.get(\"lyric\")", content,
                      "shell:closeLyric must find lyric child window")
        self.assertIn("w.close()", content, "shell:closeLyric must close the window")

    def test_no_open_lyric_no_label_collision(self):
        """openLyricWindow 不能误开主窗或主 music 子窗。"""
        content = self._read_main()
        # openLyricWindow 必须用 label="lyric"(主窗 label 是 "main"/空)
        self.assertIn('openInShell(', content)
        # 不许出现 openInShell(..., "main") 在 openLyricWindow 函数体内
        # (兜底:openLyricWindow 失败才会 fallback "main",但不是主路径)
        # 这里只验主路径 label 是 "lyric"
        idx = content.find("function openLyricWindow")
        body = content[idx: idx + 1200]
        self.assertIn('"lyric"', body, "openLyricWindow 主路径必须 label='lyric'")


# ============================================================
# preload.js 暴露
# ============================================================
class TestPreloadLyricExpose(unittest.TestCase):
    """P2.5+25(2026-10-03):prisIragent 前缀暴露 openLyric/closeLyric。"""

    def test_prisiragent_namespace(self):
        p = SHELL / "preload.js"
        content = p.read_text(encoding="utf-8")
        self.assertIn("prisIragent", content, "missing prisIragent namespace")
        self.assertIn("exposeInMainWorld", content)

    def test_open_lyric_exposed(self):
        p = SHELL / "preload.js"
        content = p.read_text(encoding="utf-8")
        self.assertIn("openLyric", content, "missing openLyric exposure")
        self.assertIn("shell:openLyric", content, "must invoke shell:openLyric IPC")

    def test_close_lyric_exposed(self):
        p = SHELL / "preload.js"
        content = p.read_text(encoding="utf-8")
        self.assertIn("closeLyric", content, "missing closeLyric exposure")
        self.assertIn("shell:closeLyric", content, "must invoke shell:closeLyric IPC")


if __name__ == "__main__":
    unittest.main()