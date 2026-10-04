// composables/useSpectrum.ts — P3.8(2026-10-04)16 段 spectrum bar composable。
//
// 原理:
//   - onMounted 启动 requestAnimationFrame loop(浏览器原生 60fps)
//   - 每帧 eqEngine.getFrequencyData(buf) 拉 1024 bin 频谱(0-255 dB 强度)
//   - 16 段对数映射(覆盖 0-22kHz):32/64/125/250/500/1k/2k/4k/6k/8k/10k/12k/14k/16k/18k/20k Hz
//   - 每段取 [lo, hi) bin range 内的 peak(而非 avg,落雪同款)→ 归一 0-1
//   - audio.paused 时强制 0 输出(Web Audio 暂停后流断,getByteFrequencyData 仍返残留)
//   - onBeforeUnmount cancelAnimationFrame + 解绑 loop
//
// 借鉴:
//   - 落雪 lx-music-desktop:对数映射 + peak 而非 avg + analyser 在 masterGain 之后
//   - Spotify 桌面:16 段 logarithmic,masterGain → analyser → destination
//   - 网易云桌面:对数分布覆盖人耳敏感频段(20Hz-20kHz)
//
// 约束:
//   - 仅在主 music 子窗调用(MusicView);LyricOnlyView / EqWindowView 拿不到 PlayerService.audio
//   - 频谱数据 100% 本地,无 IPC / 无 WS / 无 audio 上传(P3.10b 红线)
import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'
import { eqEngine } from '@/services/eq'

// P3.8 用户拍板:16 段对数频点 Hz(覆盖 0-22kHz 人耳范围)
export const SPECTRUM_BANDS = [
  32, 64, 125, 250, 500, 1000, 2000, 4000,
  6000, 8000, 10000, 12000, 14000, 16000, 18000, 20000,
] as const

export const SPECTRUM_BAR_COUNT = 16

// P3.8:player.ts 提供 audio 引用;useSpectrum 在 pause 时强制 0 输出
let _audioRef: HTMLAudioElement | null = null
export function registerSpectrumAudio(audio: HTMLAudioElement | null) {
  _audioRef = audio
}

/**
 * 16 段 spectrum bar 实时数据(每段 0-1 归一)。
 * - onMounted 自动启 rAF loop
 * - onBeforeUnmount 自动 cancel
 */
export function useSpectrum(): Ref<number[]> {
  const spectrum = ref<number[]>(new Array(SPECTRUM_BAR_COUNT).fill(0))

  let rafId: number | null = null
  let stopped = false

  // bin 频率映射(fftSize=2048 @ 44.1kHz → bin 宽 ~21.5Hz; index = freq / binWidth)
  const SAMPLE_RATE_HZ = 44100
  const FFT_SIZE = 2048
  const BIN_COUNT = FFT_SIZE / 2

  function loop() {
    if (stopped) return
    rafId = window.requestAnimationFrame(loop)

    // pause / 静音 / 未绑 → 0 输出
    if (!_audioRef || _audioRef.paused || !eqEngine.isBound()) {
      const allZero = new Array(SPECTRUM_BAR_COUNT).fill(0)
      spectrum.value = allZero
      return
    }

    const buf = eqEngine.getFrequencyData()
    const binWidth = SAMPLE_RATE_HZ / FFT_SIZE  // ~21.5 Hz / bin
    const out = new Array<number>(SPECTRUM_BAR_COUNT)
    for (let i = 0; i < SPECTRUM_BAR_COUNT; i++) {
      // 每段 = 中心 Hz ± 半段宽(末段取到 Nyquist 22050)
      const centerHz = SPECTRUM_BANDS[i]
      const halfHz = i === SPECTRUM_BAR_COUNT - 1
        ? (22050 - centerHz)
        : (SPECTRUM_BANDS[i + 1] - centerHz) / 2
      const lo = Math.max(0, Math.floor((centerHz - halfHz) / binWidth))
      const hi = Math.min(BIN_COUNT - 1, Math.ceil((centerHz + halfHz) / binWidth))
      let peak = 0
      for (let b = lo; b <= hi; b++) {
        const v = buf[b]
        if (v > peak) peak = v
      }
      // 归一 0-1(255 max)
      out[i] = peak / 255
    }
    spectrum.value = out
  }

  onMounted(() => {
    stopped = false
    rafId = window.requestAnimationFrame(loop)
  })

  onBeforeUnmount(() => {
    stopped = true
    if (rafId != null) {
      window.cancelAnimationFrame(rafId)
      rafId = null
    }
  })

  return spectrum
}
