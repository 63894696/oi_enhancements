// stores/eq.ts — P3.5(2026-10-04)EQ 状态 Pinia 镜像。
//
// 与 PlayerService 单例是「真相 vs 镜像」关系同源(stores/player.ts:13 注释):
//   - 主进程 _eq_state 是真相(落盘 userData/eq-state.json)
//   - renderer 这边 Pinia store 只缓存,改值后 IPC 同步给主进程
//   - 多窗口同步靠主进程广播 shell:eqStateChanged(经 preload onEqStateChanged 订阅)
//
// 设计要点:
//   1. bootstrap() onMounted 调一次 IPC getEqState → 镜像到本地 + EqEngine.apply
//   2. setGain / applyPreset / setEnabled 改本地 + 立即 apply(音频实时响应)+ IPC 同步
//   3. attachBroadcast() 订阅主进程 push → 任意窗改 → 这窗自动 mirror
//   4. reset() = 应用 flat 预置(主开关保留)

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { eqEngine, EQ_PRESETS } from '@/services/eq'
import type { IEqState } from '@/types/music'

export const useEqStore = defineStore('eq', () => {
  // state
  const enabled = ref(false)
  const preset = ref('flat')
  const gains = ref<number[]>([0, 0, 0, 0, 0, 0, 0, 0, 0, 0])

  function snapshot(): IEqState {
    return {
      enabled: enabled.value,
      preset: preset.value,
      gains: gains.value.slice(),
    }
  }

  // 拉主进程初值,首次进入 EQPanel / EqInline 时调用
  async function bootstrap(): Promise<void> {
    const w = window as any
    if (typeof w?.prisIragent?.getEqState !== 'function') return
    try {
      const r = await w.prisIragent.getEqState()
      if (r && r.ok) applyRemote(r)
    } catch (e) {
      console.warn('[eqStore] bootstrap failed:', e)
    }
  }

  // 主进程推送 / bootstrap 时调:写本地 + apply 到 Biquad(不开 IPC 避免回环)
  function applyRemote(s: IEqState): void {
    if (!s || !Array.isArray(s.gains)) return
    enabled.value = !!s.enabled
    preset.value = typeof s.preset === 'string' ? s.preset : 'flat'
    gains.value = s.gains.slice()
    eqEngine.apply(snapshot())
  }

  // 单段 gain 调整(0..9 idx, dB -12..+12)
  async function setGain(i: number, dB: number): Promise<void> {
    const idx = Number(i)
    if (!Number.isInteger(idx) || idx < 0 || idx > 9) return
    const clamped = Math.max(-12, Math.min(12, Number(dB) || 0))
    gains.value[idx] = clamped
    preset.value = 'custom'      // 用户改任意段即脱离预置(落雪范式)
    eqEngine.apply(snapshot())
    const w = window as any
    if (typeof w?.prisIragent?.setEqGain === 'function') {
      void w.prisIragent.setEqGain(idx, clamped)
    }
  }

  // 应用预置(白名单 EQ_PRESETS.keys())
  async function applyPreset(name: string): Promise<void> {
    const p = EQ_PRESETS[name]
    if (!p) return
    gains.value = p.gains.slice()
    preset.value = name
    eqEngine.apply(snapshot())
    const w = window as any
    if (typeof w?.prisIragent?.setEqPreset === 'function') {
      void w.prisIragent.setEqPreset(name)
    }
  }

  // 主开关
  async function setEnabled(b: boolean): Promise<void> {
    enabled.value = !!b
    eqEngine.apply(snapshot())
    const w = window as any
    if (typeof w?.prisIragent?.setEqEnabled === 'function') {
      void w.prisIragent.setEqEnabled(b)
    }
  }

  // 重置为 flat(主开关保留,沿用 enabled 当前值)
  async function reset(): Promise<void> {
    await applyPreset('flat')
  }

  // 订阅主进程 push → attachBroadcast() 调一次,返回 unsubscribe
  function attachBroadcast(): () => void {
    const w = window as any
    if (typeof w?.prisIragent?.onEqStateChanged !== 'function') return () => {}
    return w.prisIragent.onEqStateChanged((payload: any) => {
      if (payload && Array.isArray(payload.gains)) applyRemote(payload)
    })
  }

  return {
    // state
    enabled, preset, gains,
    // actions
    bootstrap, applyRemote, setGain, applyPreset, setEnabled, reset, attachBroadcast,
  }
})