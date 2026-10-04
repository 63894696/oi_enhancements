"""
test_spectrum_p38.py — P3.8(2026-10-04)N10 实时频谱 16 段测试。

覆盖:
  - TestEqEngineAnalyser — services/eq.ts AnalyserNode 在 masterGain 之后 / fftSize=2048 / smoothing=0.8 / bound 守护
  - TestSpectrumBinMapping — composables/useSpectrum.ts 1024 bin → 16 段对数映射 / peak 而非 avg / 0 输入 → 全 0
  - TestUseSpectrumComposable — useSpectrum 生命周期 + registerSpectrumAudio
  - TestSpectrumBarsComponent — components/SpectrumBars.vue props + CSS gradient
  - TestMusicViewSpectrumIntegration — MusicView 集成(import + grid + viewMode 控制)
  - TestPrivacyNoUpload — 0 上传/外传(沿用 P3.10b 红线)
"""
from __future__ import annotations

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


EQ = SRC / "services" / "eq.ts"
USE_SPECTRUM = SRC / "composables" / "useSpectrum.ts"
SPECTRUM_BARS = SRC / "components" / "SpectrumBars.vue"
MV = SRC / "views" / "MusicView.vue"


# ============================================================
# TestEqEngineAnalyser — services/eq.ts AnalyserNode 接入点
# ============================================================
class TestEqEngineAnalyser(unittest.TestCase):
    """P3.8:EqEngine.bind() 末尾串接 AnalyserNode(masterGain 之后)。"""

    def test_analyser_node_attached(self):
        c = _read(EQ)
        # createAnalyser 存在
        self.assertIn("createAnalyser", c)
        # analyser 属性
        self.assertIn("private analyser", c)

    def test_analyser_after_master_gain(self):
        """AnalyserNode 串接在 masterGain → destination 之间(EQ shaping 后)。"""
        c = _read(EQ)
        idx = c.find("bind(audioEl:")
        snippet = c[idx: idx + 2500]
        # masterGain.connect(analyser)
        self.assertIn("masterGain.connect(this.analyser)", snippet)
        # analyser.connect(ctx.destination)
        self.assertIn("analyser.connect(this.ctx.destination)", snippet)

    def test_analyser_fft_size_2048(self):
        c = _read(EQ)
        self.assertIn("fftSize = 2048", c)

    def test_analyser_smoothing_time_constant(self):
        """smoothingTimeConstant=0.8(落雪同款平滑)。"""
        c = _read(EQ)
        self.assertIn("smoothingTimeConstant = 0.8", c)

    def test_freq_buf_initialized(self):
        """freqBuf 在 bind 时初始化为 frequencyBinCount 长 Uint8Array。"""
        c = _read(EQ)
        idx = c.find("bind(audioEl:")
        snippet = c[idx: idx + 2500]
        self.assertIn("frequencyBinCount", snippet)
        self.assertIn("new Uint8Array", snippet)

    def test_get_frequency_data_method(self):
        """getFrequencyData(buf?) 暴露频谱数据。"""
        c = _read(EQ)
        self.assertIn("getFrequencyData", c)
        self.assertIn("getByteFrequencyData", c)

    def test_get_frequency_data_returns_zero_when_not_bound(self):
        """未绑时返全 0(避免 caller 拿到 undefined)。"""
        c = _read(EQ)
        idx = c.find("getFrequencyData(buf")
        snippet = c[idx: idx + 500]
        self.assertIn("fill(0)", snippet)


# ============================================================
# TestSpectrumBinMapping — 1024 bin → 16 段对数映射
# ============================================================
class TestSpectrumBinMapping(unittest.TestCase):
    """P3.8:useSpectrum 把 1024 bin FFT 数据聚合成 16 段对数频点。"""

    def test_spectrum_bands_defined(self):
        c = _read(USE_SPECTRUM)
        self.assertIn("SPECTRUM_BANDS", c)
        # 16 个频点
        bands_str = c[c.find("SPECTRUM_BANDS = ["): c.find("SPECTRUM_BANDS = [") + 400]
        self.assertIn("32", bands_str)
        self.assertIn("20000", bands_str)

    def test_spectrum_bar_count_16(self):
        c = _read(USE_SPECTRUM)
        self.assertIn("SPECTRUM_BAR_COUNT", c)
        self.assertIn("16", c)

    def test_uses_peak_not_avg(self):
        """16 段映射取 peak(而非 avg,落雪同款)。"""
        c = _read(USE_SPECTRUM)
        idx = c.find("let peak = 0")
        self.assertGreater(idx, -1)
        # peak 写法
        snippet = c[idx: idx + 200]
        self.assertIn("if (v > peak)", snippet)

    def test_pause_returns_zero(self):
        """audio.paused 时强制 0 输出(避免残留频谱)。"""
        c = _read(USE_SPECTRUM)
        idx = c.find("if (!_audioRef || _audioRef.paused")
        self.assertGreater(idx, -1)
        snippet = c[idx: idx + 400]
        self.assertIn("new Array(SPECTRUM_BAR_COUNT).fill(0)", snippet)
        self.assertIn("return", snippet)

    def test_normalize_255(self):
        """频谱归一 0-1(255 max)。"""
        c = _read(USE_SPECTRUM)
        idx = c.find("out[i] = peak / 255")
        self.assertGreater(idx, -1)


# ============================================================
# TestUseSpectrumComposable — 生命周期
# ============================================================
class TestUseSpectrumComposable(unittest.TestCase):
    """P3.8:useSpectrum composable onMounted 启 rAF + onBeforeUnmount cancel。"""

    def test_uses_request_animation_frame(self):
        c = _read(USE_SPECTRUM)
        self.assertIn("requestAnimationFrame(loop)", c)
        self.assertIn("cancelAnimationFrame", c)

    def test_on_mounted_starts_loop(self):
        c = _read(USE_SPECTRUM)
        idx = c.find("onMounted(")
        self.assertGreater(idx, -1)
        snippet = c[idx: idx + 300]
        self.assertIn("requestAnimationFrame(loop)", snippet)

    def test_on_before_unmount_cancels(self):
        c = _read(USE_SPECTRUM)
        idx = c.find("onBeforeUnmount(")
        self.assertGreater(idx, -1)
        snippet = c[idx: idx + 300]
        self.assertIn("cancelAnimationFrame", snippet)
        self.assertIn("stopped = true", snippet)

    def test_register_spectrum_audio(self):
        """registerSpectrumAudio(audio) 暴露给 composable pause 判定。"""
        c = _read(USE_SPECTRUM)
        self.assertIn("function registerSpectrumAudio", c)
        self.assertIn("_audioRef", c)

    def test_uses_eq_engine_is_bound_check(self):
        c = _read(USE_SPECTRUM)
        self.assertIn("eqEngine.isBound()", c)


# ============================================================
# TestSpectrumBarsComponent — components/SpectrumBars.vue
# ============================================================
class TestSpectrumBarsComponent(unittest.TestCase):
    """P3.8:SpectrumBars.vue 渲染 16 段 vertical bar + gold/red 主题色。"""

    def test_accepts_data_prop(self):
        c = _read(SPECTRUM_BARS)
        self.assertIn("defineProps<", c)
        self.assertIn("data: number[]", c)

    def test_renders_16_bars(self):
        c = _read(SPECTRUM_BARS)
        # v-for over data
        self.assertIn('v-for="(v, i) in data"', c)
        # 渲染 .bar div
        self.assertIn("class=\"bar\"", c)

    def test_uses_gold_red_gradient(self):
        """CSS linear-gradient gh-gold → gh-red(主题色)。"""
        c = _read(SPECTRUM_BARS)
        self.assertIn("linear-gradient(to top, var(--gh-gold", c)
        self.assertIn("var(--gh-red", c)

    def test_bar_height_animates(self):
        """bar height 随 data 变化 + transition 平滑。"""
        c = _read(SPECTRUM_BARS)
        self.assertIn("transition: height 60ms linear", c)
        # barHeight 函数(2-100%)
        self.assertIn("function barHeight", c)
        self.assertIn("Math.max(2, Math.min(100", c)

    def test_silence_min_height(self):
        """静音(全 0)时 height 2px,避免完全不可见。"""
        c = _read(SPECTRUM_BARS)
        idx = c.find("function barHeight")
        snippet = c[idx: idx + 200]
        self.assertIn("Math.max(2", snippet)


# ============================================================
# TestMusicViewSpectrumIntegration — MusicView 集成
# ============================================================
class TestMusicViewSpectrumIntegration(unittest.TestCase):
    """P3.8:MusicView 顶层挂 useSpectrum + <SpectrumBars> + grid 3 列。"""

    def test_musicview_imports_use_spectrum(self):
        c = _read(MV)
        self.assertIn("useSpectrum", c)
        self.assertIn("registerSpectrumAudio", c)

    def test_musicview_imports_spectrum_bars(self):
        c = _read(MV)
        self.assertIn("SpectrumBars", c)
        # import
        self.assertIn("import SpectrumBars", c)

    def test_musicview_uses_spectrum_in_template(self):
        c = _read(MV)
        # <SpectrumBars :data="spectrum" />
        self.assertIn("<SpectrumBars", c)
        self.assertIn(':data="spectrum"', c)

    def test_musicview_spectrum_only_in_non_queue_mode(self):
        """viewMode === 'queue' 时不显示频谱(队列模式占满)。"""
        c = _read(MV)
        # 找 template 里 <SpectrumBars 后跟 v-show= 的位置(comment 里也含 <SpectrumBars>,需跳过注释)
        # 用正则找 SpectrumBars 后 v-show 控制
        import re
        # 找 <SpectrumBars ... v-show="ui.viewMode === 'lyric' ...
        m = re.search(r'<SpectrumBars[^>]*v-show="ui\.viewMode', c)
        self.assertIsNotNone(m, "SpectrumBars 缺少 viewMode 控制 v-show")
        # 检查包含 queue 排除(只 lyric/playlist)
        idx = m.start()
        snippet = c[idx: idx + 250]
        self.assertIn("'lyric'", snippet)
        self.assertIn("'playlist'", snippet)

    def test_musicview_bottom_grid_3_columns(self):
        """.bottom grid 改 240px 1fr 160px(3 列)。"""
        c = _read(MV)
        idx = c.find(".bottom {")
        snippet = c[idx: idx + 200]
        self.assertIn("240px 1fr 160px", snippet)

    def test_musicview_registers_audio_in_on_mounted(self):
        """onMounted 调 registerSpectrumAudio(player.getAudioElement())。"""
        c = _read(MV)
        idx = c.find("function onMounted(")
        if idx < 0:
            # 旧版 onMounted(loadSongs) 已改 → 找 onMounted(() =>
            idx = c.find("onMounted(() =>")
        snippet = c[idx: idx + 400]
        self.assertIn("registerSpectrumAudio", snippet)
        self.assertIn("getAudioElement", snippet)


# ============================================================
# TestPrivacyNoUpload — 0 上传/外传(沿用 P3.10b 红线)
# ============================================================
class TestPrivacyNoUpload(unittest.TestCase):
    """P3.8:频谱数据纯本地渲染,无 IPC / 无 WS / 无 audio 上传。"""

    def test_no_ws_publish_in_spectrum(self):
        """useSpectrum 无 ws.publish / sendMessage 调用。"""
        c = _read(USE_SPECTRUM)
        self.assertNotIn("ws.send", c)
        self.assertNotIn("ws.publish", c)
        self.assertNotIn("fetch(", c)
        self.assertNotIn("XMLHttpRequest", c)

    def test_no_ipc_handler_in_spectrum_bars(self):
        """SpectrumBars.vue 不引入 ipcRenderer / window.prisIragent.send。"""
        c = _read(SPECTRUM_BARS)
        self.assertNotIn("ipcRenderer", c)
        self.assertNotIn("window.prisIragent", c)
        self.assertNotIn("fetch(", c)
        self.assertNotIn("XMLHttpRequest", c)

    def test_no_audio_upload_in_musicview_spectrum(self):
        """MusicView 频谱接入不引入 fetch / XMLHttpRequest / ws。"""
        c = _read(MV)
        idx = c.find("useSpectrum")
        snippet = c[idx: idx + 600]
        self.assertNotIn("fetch(", snippet.replace("fetch( ", ""))  # 容错 fetch (
        self.assertNotIn("ws.publish", snippet)
        self.assertNotIn("XMLHttpRequest", snippet)


if __name__ == "__main__":
    unittest.main()
