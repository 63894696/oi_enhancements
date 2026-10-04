// stores/player.ts — P2.5+24(2026-10-03) → P3.3(2026-10-03)加 listFavorites → P3.6(2026-10-04)加 downloadSong
// Pinia setup store,UI 只读。PlayerService 单例是真相来源,store 只是订阅镜像。
// 借鉴 SmallRuralDog/vue3-electron-music-player 的 setup store 写法。
//
// P3.3(2026-10-03):长按 ♡/♥ 弹 PopupMenu → 选「查看所有收藏」调 listFavorites()
//   调 /api/favorites → 返 {count, favorites:[{fav_id,title,artist,duration}]}
//
// P3.6(2026-10-04):N8 下载完成桌面通知 toast — store 加 downloadSong + 订阅 ws download_done
//   → emit 到 PlayerService 单例('download' 事件),由 useDownloadToast composable 订阅。

import { defineStore } from 'pinia'
import { ref, computed, onScopeDispose } from 'vue'
import { playerService as svc } from '@/services/player'
import { api, wsConnect } from '@/services/api'
import { useUiStore } from '@/stores/ui'
import type { IMusicItem, PlayerStatus, PlayMode, ILyricLineMsg, ILyricLineEvt } from '@/types/music'

export const usePlayerStore = defineStore('player', () => {
  // 状态
  const status = ref<PlayerStatus>('idle')
  const currentTrack = ref<IMusicItem | null>(null)
  const currentTime = ref(0)
  const duration = ref(0)
  const volume = ref(0.8)
  const buffered = ref(0)
  const isFavorite = ref(false)
  const playMode = ref<PlayMode>('sequential')

  // 歌词
  const lyricLines = ref<ILyricLineMsg[]>([])
  const currentLyricIdx = ref(-1)

  // 队列(下次播放的 N 首)
  const playlist = ref<IMusicItem[]>([])
  const cursor = ref(-1)

  // 探测 / health
  const onlineReady = ref(false)
  const seedFallback = ref(false)

  // 计算
  const progress = computed(() => duration.value > 0 ? currentTime.value / duration.value : 0)
  const bufferedPct = computed(() => Math.min(1, buffered.value))

  // ============================================================
  // 订阅 PlayerService 事件
  // ============================================================
  const onSvcStatus = (s: PlayerStatus) => { status.value = s }
  const onSvcTrack = (t: IMusicItem) => {
    currentTrack.value = t
    void refreshFavorite()
  }
  const onSvcTime = (t: number, d: number) => {
    currentTime.value = t
    if (d > 0) duration.value = d
    // 实时算当前歌词行(不依赖 ws 推送)
    computeCurrentLyricIdx(t)
  }
  const onSvcBuffered = (b: number) => { buffered.value = b }
  const onSvcEnded = () => {
    // PlayerService 自己处理 next,UI 不动
  }
  const onSvcError = (msg: string) => {
    console.error('[player.store] svc error:', msg)
  }

  svc.on('status', onSvcStatus)
  svc.on('track', onSvcTrack)
  svc.on('time', onSvcTime)
  svc.on('buffered', onSvcBuffered)
  svc.on('ended', onSvcEnded)
  svc.on('error', onSvcError)

  // 清理订阅(Pinia scope 销毁)
  onScopeDispose(() => {
    svc.off('status', onSvcStatus)
    svc.off('track', onSvcTrack)
    svc.off('time', onSvcTime)
    svc.off('buffered', onSvcBuffered)
    svc.off('ended', onSvcEnded)
    svc.off('error', onSvcError)
  })

  // ============================================================
  // ws state / lyric
  // ============================================================
  let wsState: WebSocket | null = null
  let wsLyric: WebSocket | null = null

  function onWsStateMsg(msg: any) {
    if (msg.type === 'music_state' || msg.type === 'music_progress') {
      if (msg.track) {
        currentTrack.value = msg.track
      }
      if (typeof msg.volume === 'number') {
        volume.value = msg.volume / 100
      }
      if (msg.playback_mode) {
        playMode.value = msg.playback_mode
      }
    }
    // P3.6(2026-10-04)下载完成广播 — web 层 api_cmd 透传 download_done,
    //   → emit 给 PlayerService 单例('download' 事件),useDownloadToast 订阅弹系统通知。
    //   不在此直接弹,保持职责单一(广播→PlayerService→UI)。
    if (msg.type === 'download_done') {
      svc.emit('download', msg)
    }
  }

  function onWsLyricMsg(msg: ILyricLineEvt) {
    if (msg.type === 'lyric_line' && msg.lines) {
      lyricLines.value = msg.lines
      currentLyricIdx.value = msg.current_idx ?? -1
    }
  }

  // ============================================================
  // 歌词二分查找(YesPlayMusic utils/lyric.js 移植)
  // ============================================================
  function computeCurrentLyricIdx(timeSec: number) {
    const ms = Math.floor(timeSec * 1000)
    const lines = lyricLines.value
    if (!lines.length) {
      if (currentLyricIdx.value !== -1) currentLyricIdx.value = -1
      return
    }
    // 二分:找到最后一行 time_ms <= ms 的
    let lo = 0
    let hi = lines.length - 1
    let ans = -1
    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      if (lines[mid].time_ms <= ms) {
        ans = mid
        lo = mid + 1
      } else {
        hi = mid - 1
      }
    }
    if (ans !== currentLyricIdx.value) currentLyricIdx.value = ans
  }

  // ============================================================
  // 命令(UI → PlayerService / API)
  // ============================================================
  async function bootstrap() {
    // 拉 health
    const h = await api('/api/health')
    if (h.ok) {
      onlineReady.value = !!h.online?.initialized
      seedFallback.value = !!h.seed_fallback
    }

    // 拉当前 state
    const s = await api('/api/state')
    if (s.ok && s.state) {
      if (s.state.track) currentTrack.value = s.state.track
      if (s.state.volume != null) volume.value = s.state.volume / 100
      if (s.state.playback_mode) playMode.value = s.state.playback_mode
      // status 用 svc 的(getStatus 同步)
      const svcStatus = svc.getStatus()
      status.value = svcStatus
    }

    // 拉队列
    const q = await api('/api/queue')
    if (q.ok && q.queue) {
      playlist.value = q.queue.filter((t: any) => !t.cursor).map((t: any) => ({
        id: t.id,
        title: t.title,
        artist: t.artist,
        album: t.album || '',
        duration: t.duration_sec ?? t.duration ?? 0,
        source: t.source || 'lx',
      }))
      cursor.value = q.cursor ?? -1
    }

    // 拉歌词(若有当前 track)
    if (currentTrack.value) {
      void refreshLyric(currentTrack.value)
      await refreshFavorite()
    }

    // ws 订阅
    if (!wsState) wsState = wsConnect('/ws/state', onWsStateMsg)
    if (!wsLyric) wsLyric = wsConnect('/ws/lyrics', onWsLyricMsg)
  }

  async function refreshLyric(track: IMusicItem) {
    lyricLines.value = []
    currentLyricIdx.value = -1
    const r = await api(`/api/lyric?title=${encodeURIComponent(track.title)}&artist=${encodeURIComponent(track.artist)}&album=${encodeURIComponent(track.album || '')}`)
    if (r.ok && Array.isArray(r.lines)) {
      lyricLines.value = r.lines
    }
  }

  async function refreshFavorite() {
    if (!currentTrack.value) {
      isFavorite.value = false
      return
    }
    const r = await api('/api/cmd', { action: 'is_favorite', track_id: currentTrack.value.id })
    isFavorite.value = !!(r.ok && r.favorited)
  }

  async function toggleFavorite() {
    if (!currentTrack.value) return
    const r = await api('/api/cmd', { action: 'favorite', track_id: currentTrack.value.id })
    if (r.ok) {
      isFavorite.value = !!r.favorited
      return r
    }
    return null
  }

  // P3.3(2026-10-03):长按 ♡/♥ 菜单 — 「查看所有收藏」
  // 调 /api/favorites GET → 返 {count, favorites:[{fav_id,title,artist,duration}]}
  async function listFavorites(): Promise<{ count: number; list: { fav_id: string; title: string; artist: string; duration: number }[] }> {
    const r = await api('/api/favorites')
    if (r.ok) {
      return { count: r.count || 0, list: r.favorites || [] }
    }
    return { count: 0, list: [] }
  }

  // P3.3(2026-10-03):长按 ♡/♥ 菜单 — 选单条收藏删除(目前不在 UI 暴露,留接口)
  async function removeFavorite(fav_id: string) {
    const r = await api(`/api/favorites/remove?fav_id=${encodeURIComponent(fav_id)}`, {})
    return r
  }

  // P3.6(2026-10-04):N8 下载完成桌面通知 toast — 调 /api/cmd {action:download,track_id}。
  //   返回值是即时 HTTP 响应({ok, err})。
  //   真正的完成广播走 web 层 ws_state download_done → store onWsStateMsg → svc.emit('download')
  //   → useDownloadToast 订阅 → window.prisIragent.showToast(...)。不在此直接 toast,
  //   避免 ws 到达前的「已下载」误报(下载中可能被 UI 误读为「完成」)。
  async function downloadSong(trackId: string) {
    if (!trackId) return { ok: false, err: 'missing track_id' }
    const r = await api('/api/cmd', { action: 'download', track_id: trackId })
    return r
  }

  async function playById(songId: string) {
    // 从 /api/songs 拉详情 → 调 PlayerService
    // P3.9(2026-10-03):后端 SongMeta 加 duration_sec 字段(真歌名池 v2 估算时长),
    //   优先用 duration_sec 兜底(v2 mock.js googleapis mp3 时长不稳),
    //   旧字段 song.duration 仍兼容(v1 没改)。
    // P2.5+28 A 阶段(2026-10-04):删 seed.mp3 兜底,改 playable/last_err 检测。
    //   用户原话:「30 秒静音需要彻底去掉,不能播放就说明原因是什么」。
    //   playable=false 时弹「❌ 此歌暂无法播放:last_err」error toast,
    //   不再假装播 seed.mp3。
    const r = await api(`/api/songs`)
    if (!r.ok) return null
    const song = (r.songs as any[]).find((s) => s.id === songId)
    if (!song) return null
    await svc.playSong({
      id: song.id,
      title: song.title,
      artist: song.artist,
      album: song.album || '',
      duration: song.duration_sec ?? song.duration ?? 0,
      source: 'lx',
    })
    // P2.5+28 A 阶段:不可播检测 — 600ms 后查 /api/state.playable=false 弹 error toast
    setTimeout(async () => {
      try {
        const st = await api('/api/state')
        if (st && st.playable === false && st.last_err) {
          const ui = useUiStore()
          ui.pushToast('error', `❌ 此歌暂无法播放:${st.last_err}`)
        }
      } catch (_) { /* 静默 */ }
    }, 600)
    return song
  }

  async function toggle() {
    await svc.toggle()
  }

  async function playNext() {
    await svc.playNext()
  }

  async function playPrev() {
    await svc.playPrev()
  }

  async function seek(offsetSec: number) {
    await svc.seek(offsetSec)
  }

  function setVolume(v: number) {
    svc.setVolume(v)
    volume.value = v
    void api('/api/cmd', { action: 'volume', volume: Math.round(v * 100) })
  }

  return {
    // state
    status, currentTrack, currentTime, duration, volume, buffered,
    isFavorite, playMode, lyricLines, currentLyricIdx, playlist, cursor,
    onlineReady, seedFallback,
    // computed
    progress, bufferedPct,
    // actions
    bootstrap, refreshFavorite, toggleFavorite, refreshLyric,
    playById, toggle, playNext, playPrev, seek, setVolume,
    // P3.3(2026-10-03):长按收藏菜单 action
    listFavorites, removeFavorite,
    // P3.6(2026-10-04):N8 下载完成桌面通知 toast action
    downloadSong,
  }
})