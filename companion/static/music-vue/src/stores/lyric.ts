// stores/lyric.ts — P2.5+25(2026-10-03)桌面歌词独立 Pinia store。
//
// 与 player store 完全独立:
//   - 不依赖 audio 单例(桌面歌词窗不播歌,只显示)
//   - 不依赖 player store(主 music 子窗关闭歌词窗仍可用)
//   - 只接 ws /ws/lyrics 推送 + 拉 /api/state + /api/agent/cfg/list
//
// 借鉴 yesplaymusic 二分查找 + 借鉴旧 lyrics.js 的 ws 协议

import { defineStore } from 'pinia'
import { ref, computed, onScopeDispose } from 'vue'
import { api, wsConnect } from '@/services/api'
import type { IMusicItem, ILyricLineMsg } from '@/types/music'

interface LyricCfg {
  color: string
  font_size: number
  mode: string      // 'line' | 'word' — 当前实现只用 line
  delay_ms: number
  opacity: number
}

export const useLyricStore = defineStore('lyric', () => {
  // ============================================================
  // state
  // ============================================================
  const lines = ref<ILyricLineMsg[]>([])
  const currentIdx = ref(-1)
  const track = ref<IMusicItem | null>(null)
  const progress = ref(0)   // 秒
  const duration = ref(0)   // 秒
  const connected = ref(false)
  const cfg = ref<LyricCfg>({
    color: '#f6f1e7',
    font_size: 32,
    mode: 'line',
    delay_ms: 0,
    opacity: 0.85,
  })

  // computed
  const progressPct = computed(() => {
    if (duration.value <= 0) return 0
    return Math.max(0, Math.min(100, (progress.value / duration.value) * 100))
  })
  const currentText = computed(() => {
    const i = currentIdx.value
    if (i < 0 || i >= lines.value.length) return ''
    return lines.value[i].text || ''
  })

  // ============================================================
  // ws /ws/lyrics 订阅
  // ============================================================
  let ws: WebSocket | null = null

  function onLyricMsg(msg: any) {
    // ws 协议上有 lyric_cfg / lyric_line / music_state / music_progress / heartbeat 5 种,
    // 后端 ws_lyrics 同时推 lyric_cfg + lyric_line(后端 _publish_lyric)。
    // music_state / music_progress 来自 ws_state,但 ws_lyrics 也可能收到(后端 _on_player_state_change
    // 不走 lyric 通道,所以此处容错处理)。
    if (msg.type === 'lyric_cfg') {
      if (typeof msg.color === 'string') cfg.value.color = msg.color
      if (typeof msg.font_size === 'number') cfg.value.font_size = msg.font_size
      if (typeof msg.mode === 'string') cfg.value.mode = msg.mode
      if (typeof msg.delay_ms === 'number') cfg.value.delay_ms = msg.delay_ms
      if (typeof msg.opacity === 'number') cfg.value.opacity = msg.opacity
      applyCfgToCss()
    } else if (msg.type === 'lyric_line') {
      if (Array.isArray(msg.lines)) lines.value = msg.lines
      if (typeof msg.current_idx === 'number') currentIdx.value = msg.current_idx
    } else if (msg.type === 'music_state' || msg.type === 'music_progress') {
      if (msg.track) track.value = msg.track as IMusicItem
      if (typeof msg.progress === 'number') progress.value = msg.progress
      if (typeof msg.duration === 'number' && msg.duration > 0) duration.value = msg.duration
    } else if (msg.type === 'heartbeat') {
      connected.value = true
    }
  }

  function applyCfgToCss() {
    const root = document.documentElement
    root.style.setProperty('--lyrics-color', cfg.value.color)
    root.style.setProperty('--lyrics-font-size', cfg.value.font_size + 'px')
    root.style.setProperty('--lyrics-opacity', String(cfg.value.opacity))
  }

  // ============================================================
  // 二分查找兜底(YesPlayMusic utils/lyric.js 移植)
  // ws 推送 current_idx 滞后时,前端用 progress 实时算
  // ============================================================
  function computeCurrentIdxByTime(timeSec: number) {
    const ms = Math.floor(timeSec * 1000) + cfg.value.delay_ms
    const ls = lines.value
    if (!ls.length) {
      if (currentIdx.value !== -1) currentIdx.value = -1
      return
    }
    let lo = 0
    let hi = ls.length - 1
    let ans = -1
    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      if (ls[mid].time_ms <= ms) {
        ans = mid
        lo = mid + 1
      } else {
        hi = mid - 1
      }
    }
    if (ans !== currentIdx.value) currentIdx.value = ans
  }

  // 监听 progress 变化主动重算(不依赖 ws 推送)
  function onProgressChange() {
    if (duration.value > 0 && progress.value > 0) {
      computeCurrentIdxByTime(progress.value)
      // 同步到 CSS var
      const root = document.documentElement
      root.style.setProperty('--progress', progressPct.value.toFixed(2) + '%')
    }
  }

  // ============================================================
  // bootstrap
  // ============================================================
  async function bootstrap() {
    // 拉 cfg
    const c = await api('/api/agent/cfg/list')
    if (c.ok && Array.isArray(c.items)) {
      const m: Record<string, any> = {}
      c.items.forEach((it: any) => { m[it.path] = it.value })
      if (m['lyrics.color']) cfg.value.color = String(m['lyrics.color'])
      if (m['lyrics.font_size']) cfg.value.font_size = Number(m['lyrics.font_size'])
      if (m['lyrics.mode']) cfg.value.mode = String(m['lyrics.mode'])
      if (m['lyrics.delay_ms'] != null) cfg.value.delay_ms = Number(m['lyrics.delay_ms'])
      if (m['lyrics.opacity'] != null) cfg.value.opacity = Number(m['lyrics.opacity'])
      applyCfgToCss()
    }

    // 拉当前 state
    const s = await api('/api/state')
    if (s.ok && s.state) {
      if (s.state.track) track.value = s.state.track as IMusicItem
      if (s.state.progress != null) progress.value = s.state.progress
      if (s.state.duration) duration.value = s.state.duration
    }

    // 拉歌词(若有当前 track)
    if (track.value) {
      const r = await api(
        `/api/lyric?title=${encodeURIComponent(track.value.title)}` +
        `&artist=${encodeURIComponent(track.value.artist)}` +
        `&album=${encodeURIComponent(track.value.album || '')}`
      )
      if (r.ok && Array.isArray(r.lines)) {
        lines.value = r.lines as ILyricLineMsg[]
      }
    }

    // ws 订阅
    if (!ws) ws = wsConnect('/ws/lyrics', onLyricMsg)
  }

  onScopeDispose(() => {
    if (ws) {
      try { ws.close() } catch (_) {}
      ws = null
    }
  })

  return {
    // state
    lines, currentIdx, track, progress, duration, connected, cfg,
    // computed
    progressPct, currentText,
    // actions
    bootstrap, onProgressChange, computeCurrentIdxByTime,
  }
})