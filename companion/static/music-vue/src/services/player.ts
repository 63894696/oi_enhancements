// services/player.ts — P2.5+24(2026-10-03)
// 借鉴 MusicFree 单例 + Vue-mmPlayer 容错 + LX preload + AbortError 标准规避。
// audio 实例只在 PlayerService 单例里,所有 UI 组件订阅事件不直接动 audio。
//
// 状态机 6 态(MusicFree):
//   idle | loading | buffering | playing | paused | error
//
// race condition 解法:
//   1. token 校验:每次 playSong 改 currentToken,后续 audio event 检查 token 失配就丢弃
//   2. AbortError 仅忽略:.catch(e => if (e.name !== 'AbortError') throw)
//   3. preload 下一首:duration - currentTime < 10s 时调 /api/songs/preload

import { Emitter } from './emitter'
import { api } from './api'
import type { IMusicItem, PlayerStatus } from '@/types/music'

interface PreloadResult {
  ok: boolean
  track_id?: string
  title?: string
  artist?: string
  url?: string | null
  source?: string
  stream_url?: string
  err?: string
}

export class PlayerService extends Emitter {
  private static _instance: PlayerService | null = null
  static getInstance(): PlayerService {
    if (!PlayerService._instance) PlayerService._instance = new PlayerService()
    return PlayerService._instance
  }

  // 单例 audio — UI 组件不持有 audio 实例,只订阅事件
  private audio = new Audio()
  private status: PlayerStatus = 'idle'
  private currentToken: string | null = null
  private currentTrack: IMusicItem | null = null
  private currentTime = 0
  private duration = 0
  private volume = 0.8
  private buffered = 0
  private preloaded: PreloadResult | null = null
  private preloadPromise: Promise<PreloadResult | null> | null = null
  private preloadCheckTimer: number | null = null
  private errorRetryTimer: number | null = null

  private constructor() {
    super()
    this.audio.preload = 'metadata'
    this.audio.volume = this.volume

    // ===== audio 事件 → status 切换(token 校验丢 stale)
    this.audio.addEventListener('canplay', () => this.onCanPlay())
    this.audio.addEventListener('waiting', () => this.onWaiting())
    this.audio.addEventListener('playing', () => this.onPlaying())
    this.audio.addEventListener('pause', () => this.onPause())
    this.audio.addEventListener('ended', () => this.onEnded())
    this.audio.addEventListener('error', () => this.onError())
    this.audio.addEventListener('timeupdate', () => this.onTimeUpdate())
    this.audio.addEventListener('progress', () => this.onProgress())
    this.audio.addEventListener('durationchange', () => this.onDurationChange())
    this.audio.addEventListener('stalled', () => this.onStalled())
  }

  // ============================================================
  // 状态 getter / setter
  // ============================================================
  getStatus(): PlayerStatus { return this.status }
  getCurrentTrack(): IMusicItem | null { return this.currentTrack }
  getCurrentTime(): number { return this.currentTime }
  getDuration(): number { return this.duration }
  getVolume(): number { return this.volume }
  getBuffered(): number { return this.buffered }
  // P3.5(2026-10-04)暴露 audio 元素给 EqEngine.bind 用 — 同一 audio 只能被 createMediaElementSource
  //   链一次,EqEngine 内部 bound 守护避免 InvalidStateError。
  getAudioElement(): HTMLAudioElement { return this.audio }

  private setStatus(s: PlayerStatus) {
    if (this.status === s) return
    this.status = s
    this.emit('status', s)
  }

  // ============================================================
  // 核心:playSong
  // ============================================================
  async playSong(item: IMusicItem, forceReFetch = false): Promise<void> {
    // 新曲目 token
    this.currentToken = item.id
    this.currentTrack = item
    this.setStatus('loading')
    this.emit('track', item)

    // 优先用预取的 stream url
    let streamUrl: string
    let nextUrl: string | null | undefined
    if (!forceReFetch && this.preloaded && this.preloaded.track_id === item.id) {
      streamUrl = this.preloaded.stream_url!
      nextUrl = this.preloaded.url ?? null
      this.preloaded = null
    } else {
      try {
        const r = await api('/api/cmd', {
          action: 'play_url',
          song_info: {
            hash: item.id,
            songmid: item.id,
            songname: item.title,
            singer: item.artist,
          },
          title: item.title,
          artist: item.artist,
        })
        if (!r.ok) throw new Error(r.err || 'play_url failed')
        streamUrl = `/api/stream/${encodeURIComponent(item.id)}`
      } catch (e: any) {
        this.setStatus('error')
        this.emit('error', e.message || String(e))
        return
      }
    }

    if (this.currentToken !== item.id) return  // 用户已切歌

    this.audio.src = streamUrl
    if (nextUrl) this.audio.src = nextUrl  // 远端直链覆盖
    this.audio.load()
    // 不在这里 play() — 等 canplay 事件触发
  }

  // ============================================================
  // audio 事件 → 状态机
  // ============================================================
  private async onCanPlay() {
    if (this.currentToken === null) return
    // P3.5(2026-10-04)首次 canplay 时触发 EqEngine.bind,EqEngine 内部 bound flag 幂等。
    // dynamic import 是为避免循环 import(EqEngine 单例不依赖 Player,但 store 之间可能)。
    import('@/services/eq').then(({ eqEngine }) => {
      eqEngine.bind(this.audio)
      eqEngine.resume()
    }).catch((e) => console.warn('[PlayerService] eq bind failed:', e))
    this.setStatus('buffering')
    try {
      await this.audio.play()
    } catch (e: any) {
      // AbortError 仅忽略(Chrome 50+ race 标准)
      if (e?.name !== 'AbortError') {
        console.error('[PlayerService] play failed:', e)
        this.setStatus('error')
        this.emit('error', e.message || String(e))
      }
    }
  }

  private onWaiting() {
    if (this.currentToken === null) return
    this.setStatus('buffering')
  }

  private onPlaying() {
    if (this.currentToken === null) return
    this.setStatus('playing')
    this.startPreloadWatcher()
  }

  private onPause() {
    if (this.currentToken === null) return
    if (this.status === 'loading' || this.status === 'buffering') {
      // canplay 前 pause 是初始态,保持
      return
    }
    this.setStatus('paused')
  }

  private onEnded() {
    if (this.currentToken === null) return
    this.emit('ended')
    // 自动衔接下一首
    void this.playNext()
  }

  private onError() {
    if (this.currentToken === null) return
    console.warn('[PlayerService] audio error:', this.audio.error)
    this.setStatus('error')
    // 3 秒后自动 next(Vue-mmPlayer 容错)
    if (this.errorRetryTimer) window.clearTimeout(this.errorRetryTimer)
    this.errorRetryTimer = window.setTimeout(() => {
      if (this.currentToken !== null) void this.playNext()
    }, 3000)
  }

  private onStalled() {
    // Vue-mmPlayer:stalled 时主动 load() 重连
    if (this.currentToken === null) return
    console.info('[PlayerService] audio stalled, reload')
    this.audio.load()
  }

  private onTimeUpdate() {
    this.currentTime = this.audio.currentTime
    this.emit('time', this.currentTime, this.duration)
  }

  private onProgress() {
    if (this.audio.buffered.length > 0 && this.audio.duration > 0) {
      this.buffered = this.audio.buffered.end(this.audio.buffered.length - 1) / this.audio.duration
      this.emit('buffered', this.buffered)
    }
  }

  private onDurationChange() {
    this.duration = this.audio.duration || 0
    this.emit('time', this.currentTime, this.duration)
  }

  // ============================================================
  // 控制命令
  // ============================================================
  async toggle(): Promise<void> {
    if (this.status === 'playing') return this.pause()
    return this.resume()
  }

  async pause(): Promise<void> {
    this.audio.pause()
    this.setStatus('paused')
  }

  async resume(): Promise<void> {
    if (!this.currentTrack) return
    if (this.audio.src) {
      try {
        await this.audio.play()
      } catch (e: any) {
        if (e?.name !== 'AbortError') {
          console.error('[PlayerService] resume failed:', e)
        }
      }
    }
  }

  async stop(): Promise<void> {
    this.audio.pause()
    this.audio.removeAttribute('src')
    this.audio.load()
    this.currentToken = null
    this.currentTrack = null
    this.setStatus('idle')
  }

  async seek(offsetSec: number): Promise<void> {
    if (!this.currentTrack) return
    const dur = this.duration || 0
    const target = Math.max(0, Math.min(offsetSec, dur))
    this.audio.currentTime = target
    this.currentTime = target
    this.emit('time', this.currentTime, this.duration)
    // 上报后端
    void api('/api/cmd', { action: 'progress', position: target, duration: dur })
  }

  setVolume(v: number): void {
    const clamped = Math.max(0, Math.min(1, v))
    this.volume = clamped
    this.audio.volume = clamped
    this.emit('volume', clamped)
  }

  async playNext(): Promise<void> {
    try {
      const r = await api('/api/cmd', { action: 'next' })
      if (!r.ok) {
        this.emit('toast', { type: 'warn', msg: `下一首: ${r.err || '无'}` })
        return
      }
      const track = r.state?.track
      if (track) {
        await this.playSong({
          id: track.id,
          title: track.title,
          artist: track.artist,
          album: track.album || '',
          duration: track.duration || 0,
          source: track.source || 'lx',
        })
      }
    } catch (e: any) {
      this.emit('toast', { type: 'error', msg: `下一首失败: ${e.message}` })
    }
  }

  async playPrev(): Promise<void> {
    try {
      const r = await api('/api/cmd', { action: 'prev' })
      if (!r.ok) {
        this.emit('toast', { type: 'warn', msg: `上一首: ${r.err || '无'}` })
        return
      }
      const track = r.state?.track
      if (track) {
        await this.playSong({
          id: track.id,
          title: track.title,
          artist: track.artist,
          album: track.album || '',
          duration: track.duration || 0,
          source: track.source || 'lx',
        })
      }
    } catch (e: any) {
      this.emit('toast', { type: 'error', msg: `上一首失败: ${e.message}` })
    }
  }

  // ============================================================
  // preload 下一首 URL — 借鉴 LX usePreloadNextMusic
  // ============================================================
  private startPreloadWatcher() {
    if (this.preloadCheckTimer !== null) return
    this.preloadCheckTimer = window.setInterval(() => {
      // 还剩 ≤ 10s 时预取
      if (this.duration > 0 && this.duration - this.currentTime <= 10 && !this.preloadPromise) {
        void this.preloadNext()
      }
    }, 1000)
  }

  private stopPreloadWatcher() {
    if (this.preloadCheckTimer !== null) {
      window.clearInterval(this.preloadCheckTimer)
      this.preloadCheckTimer = null
    }
  }

  async preloadNext(): Promise<PreloadResult | null> {
    if (this.preloadPromise) return this.preloadPromise
    this.preloadPromise = api.post<PreloadResult>('/api/songs/preload', {})
      .then((r) => {
        if (r.ok && r.stream_url) {
          this.preloaded = r
        }
        return r.ok ? r : null
      })
      .catch((e) => {
        console.warn('[PlayerService] preload failed:', e)
        return null
      })
      .finally(() => {
        this.preloadPromise = null
      })
    return this.preloadPromise
  }
}

export const playerService = PlayerService.getInstance()