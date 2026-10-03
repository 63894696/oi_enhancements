// types/music.ts — P2.5+24(2026-10-03)
// 借鉴 MusicFree `IMusicItem` 统一数据结构。
// 收藏 / 下载 / 本地 / 远端 共用同一结构,UI 不需要分支。

export type PlayerStatus =
  | 'idle'         // 无曲目
  | 'loading'      // 点歌 / 解析 url 中
  | 'buffering'    // audio 缓冲
  | 'playing'      // 实际播放中
  | 'paused'       // 暂停
  | 'error'        // 出错(3 秒后自动 next)

export type SourceKind =
  | 'local'              // 本地文件
  | 'lx:mock.js'        // lx 多源(细)
  | 'lx:juhe.js'
  | 'song_pool_fav'    // 收藏虚拟
  | 'seed'             // seed.mp3 兜底

export type PlayMode = 'sequential' | 'shuffle' | 'repeat_one'

export interface IMusicItem {
  id: string              // sha1(title|artist|songid)[:16]
  title: string
  artist: string
  album: string
  duration: number        // 秒
  source: SourceKind | string
  // 运行时字段(从后端 state 来)
  path?: string           // 远程 url / 本地文件路径
  isFavorite?: boolean
  artwork?: string        // 暂未实装(用首字占位)
}

// ws 推过来的 music_state 完整 payload(后端 to_dict)
export interface IPlayerStateMsg {
  type: 'music_state' | 'music_progress' | 'heartbeat'
  status: 'idle' | 'playing' | 'paused' | 'stopped'
  track: IMusicItem | null
  progress: number
  volume: number
  muted: boolean
  playback_mode: PlayMode
  updated_at: number
  ts: number
}

// ws lyric payload
export interface ILyricLineMsg {
  time_ms: number
  text: string
  words?: Array<{ time_ms: number; text: string }>
}

export interface ILyricLineEvt {
  type: 'lyric_line' | 'lyric_cfg' | 'heartbeat'
  lines?: ILyricLineMsg[]
  current_idx?: number
  current_text?: string
  source?: string
  color?: string
  font_size?: number
  mode?: string
  delay_ms?: number
  opacity?: number
  ts: number
}