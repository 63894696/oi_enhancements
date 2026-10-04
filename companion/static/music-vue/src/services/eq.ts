// services/eq.ts — P3.5(2026-10-04)Web Audio API 10 段 EQ 引擎单例。
//                     P3.8(2026-10-04)同 ctx 串接 AnalyserNode(masterGain → analyser → destination)
//                     暴露 getFrequencyData() 给 P3.8 useSpectrum composable 拉 16 段 spectrum bar。
//
// 原理:
//   - 浏览器端用 Web Audio API,BiquadFilterNode type='peaking' 实现 10 段 parametric EQ
//   - 频段 31.25 / 62.5 / 125 / 250 / 500 / 1k / 2k / 4k / 8k / 16k Hz(对数均布,主流 10 段同款)
//   - 每段 ±12 dB,Q=1.0(默认窄 Q,落雪 / YesPlayMusic 同款)
//   - 链: source → bq0 → bq1 → ... → bq9 → masterGain → analyser → destination
//   - AnalyserNode 在 masterGain 之后(EQ shaping + 总体音量 = 用户实际听到的样子;落雪 / Spotify 同款)
//   - fftSize=2048 → frequencyBinCount=1024;smoothingTimeConstant=0.8(落雪同款平滑)
//
// 关键约束:
//   - 同一 HTMLAudioElement 只能被 createMediaElementSource() 调一次(InvalidStateError)
//   - 因此 EqEngine 是单例,bind() 加 bound flag 守护,二次 bind 静默 noop
//   - masterGain 防削顶:enabled=true 时 0.5(-6 dB),enabled=false 时 1.0 直通
//
// 借鉴:
//   - 落雪 lx-music-desktop eqCore.ts(10 段 biquad 串行)
//   - YesPlayMusic useEqualizer.ts(preset 数组 + masterGain 防削顶)
//   - Spotify 桌面(eq-core.js 6 段;本项目 10 段覆盖更广)
//
// 与 PlayerService 关系:
//   - services/player.ts 在 audio 第一次 canplay 触发 eqEngine.bind(this.audio)
//     (通过 PlayerService.getAudioElement() getter 暴露 audio 实例)
//   - bind 之后,EqEngine 独立持有 source/biquads/masterGain,PlayerService 不感知

import type { IEqState } from '@/types/music'

export const EQ_BANDS = [31.25, 62.5, 125, 250, 500, 1000, 2000, 4000, 8000, 16000] as const

// P3.5 用户拍板 6 预置 — 与主进程 main.js EQ_PRESETS 字典一一对应。
// 改一处需同步另一处(测试 test_eq_p35.py 会断言 keys 一致)。
export const EQ_PRESETS: Record<string, { name: string; gains: number[] }> = {
  flat:       { name: '平直',     gains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
  vocal:      { name: '人声增强', gains: [0, -2, 0, 1, 2, 3, 2, 0, 0, 0] },
  bass:       { name: '低音增强', gains: [4, 5, 3, 1, 0, 0, 0, 0, 0, 0] },
  treble:     { name: '高音增强', gains: [0, 0, 0, 0, 0, 0, 1, 3, 4, 3] },
  rock:       { name: '摇滚',     gains: [3, 2, 1, -1, -3, 1, 2, 3, 4, 3] },
  electronic: { name: '电子',     gains: [3, 2, 0, -2, -1, 1, 0, 1, 3, 4] },
}

export const PRESET_KEYS = Object.keys(EQ_PRESETS)

export class EqEngine {
  private static _instance: EqEngine | null = null
  static getInstance(): EqEngine {
    if (!EqEngine._instance) EqEngine._instance = new EqEngine()
    return EqEngine._instance
  }

  private ctx: AudioContext | null = null
  private biquads: BiquadFilterNode[] = []
  private masterGain: GainNode | null = null
  private source: MediaElementAudioSourceNode | null = null
  // P3.8:AnalyserNode 串接在 masterGain → destination 之间,频谱反映 EQ shaping 后输出。
  private analyser: AnalyserNode | null = null
  // P3.8:freqBuf 复用 Uint8Array(fftSize/2 = 1024 bin);避免每帧 alloc。
  private freqBuf: Uint8Array | null = null
  private bound = false

  // 把 audio 元素链入 Web Audio Graph。重复调用安全(bound flag 守护)。
  bind(audioEl: HTMLAudioElement): boolean {
    if (this.bound) return true
    if (typeof window === 'undefined') return false
    try {
      const AC = window.AudioContext || (window as any).webkitAudioContext
      if (!AC) {
        console.warn('[EqEngine] AudioContext not supported')
        return false
      }
      this.ctx = new AC()
      this.source = this.ctx.createMediaElementSource(audioEl)
      this.biquads = EQ_BANDS.map((f) => {
        const n = this.ctx!.createBiquadFilter()
        n.type = 'peaking'
        n.frequency.value = f
        n.Q.value = 1.0
        n.gain.value = 0
        return n
      })
      this.masterGain = this.ctx!.createGain()
      this.masterGain.gain.value = 1.0    // 初始直通;enabled=true 后改为 0.5
      // P3.8:AnalyserNode 串接在 masterGain 之后(EQ shaping 后,反映用户实际听到的样子)
      this.analyser = this.ctx!.createAnalyser()
      this.analyser.fftSize = 2048         // 1024 bin,bin 宽 ~21-23Hz @ 44.1/48kHz
      this.analyser.smoothingTimeConstant = 0.8  // 落雪同款平滑
      this.freqBuf = new Uint8Array(this.analyser.frequencyBinCount)
      // 链:source → bq0 → bq1 → ... → bq9 → masterGain → analyser → destination
      let prev: AudioNode = this.source
      for (const bq of this.biquads) {
        prev.connect(bq)
        prev = bq
      }
      prev.connect(this.masterGain)
      this.masterGain.connect(this.analyser)
      this.analyser.connect(this.ctx.destination)
      this.bound = true
      return true
    } catch (e) {
      console.warn('[EqEngine] bind failed:', e)
      return false
    }
  }

  // 应用 EQ 状态:enabled=false 时 masterGain=1 直通,每段 gain=0;
  // enabled=true 时 masterGain=0.5 防削顶,每段 gain 用 _eq_state.gains[i]。
  apply(state: IEqState): void {
    if (!this.bound || !this.masterGain) return
    if (!state || !Array.isArray(state.gains)) return
    try {
      this.masterGain.gain.value = state.enabled ? 0.5 : 1.0
      for (let i = 0; i < 10; i++) {
        const bq = this.biquads[i]
        if (!bq) continue
        const g = Number(state.gains[i])
        bq.gain.value = state.enabled && Number.isFinite(g) ? g : 0
      }
    } catch (e) {
      console.warn('[EqEngine] apply failed:', e)
    }
  }

  // AudioContext 需在 user gesture 后 resume;PlayerService onCanPlay 触发。
  resume(): void {
    if (this.ctx && this.ctx.state === 'suspended') {
      void this.ctx.resume().catch((e) => console.warn('[EqEngine] resume failed:', e))
    }
  }

  // 测试 / 调试用
  isBound(): boolean { return this.bound }
  getBiquad(i: number): BiquadFilterNode | null {
    return this.biquads[i] || null
  }

  // P3.8:暴露 AnalyserNode 给 useSpectrum composable 拉频谱。
  // getFrequencyData(buf?) — 不传 buf 时复用内部 freqBuf(避免每帧 alloc)。
  // 返回的 buf 是 Uint8Array(0-255 dB 频谱强度,index 0..frequencyBinCount-1)。
  getAnalyser(): AnalyserNode | null { return this.analyser }
  getFrequencyData(buf?: Uint8Array): Uint8Array {
    if (!this.analyser) {
      // 未绑时返全 0,避免 caller 拿到 undefined
      if (!this.freqBuf) this.freqBuf = new Uint8Array(1024)
      this.freqBuf.fill(0)
      return this.freqBuf
    }
    const target = buf || this.freqBuf!
    this.analyser.getByteFrequencyData(target)
    return target
  }
}

export const eqEngine = EqEngine.getInstance()