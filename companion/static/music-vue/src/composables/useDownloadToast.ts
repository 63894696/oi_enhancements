// useDownloadToast.ts — P3.6(2026-10-04)N8 下载完成桌面通知 toast 桥接 composable。
//
// 桥 PlayerService 单例 'download' 事件 → window.prisIragent.showToast IPC。
// 后端 publish 下载完成 → web 层 ws_state 广播 download_done → store onWsStateMsg
//   → svc.emit('download', payload) → 本 composable 订阅 → showToast。
//
// 复用 P3.10a(2026-10-04)既有 showToast IPC 基础设施:
//   - main process:showToast({title, body, level})
//   - preload:prisIragent.showToast(...) (Win10+ Action Center / macOS Notification Center / Linux libnotify)
//   - 通知偏好(全部/仅错误/关闭)+ 节流(5s L1 info)+ FIFO 队列(max 3)
//
// 用法(顶层 setup 一次):
//   import { useDownloadToast } from '@/composables/useDownloadToast'
//   useDownloadToast()
//
// 设计要点:
//   - 顶层调用一次:onMounted 订阅、onBeforeUnmount 退订。多个视图同时挂载也只一次
//     toast(单一事实来源 = PlayerService 单例),不重复弹。
//   - 防御:window.prisIragent 不存在时(vite dev / 普通浏览器)→ console.warn 跳过,
//     不抛错,以便 vue-tsc 类型不依赖 preload 类型。
//   - level:ok=True → 'info'(L1 节流),ok=False → 'error'(critical urgency + 不节流)。
//     P3.10a 已规定 level='error' 偏好过滤时仍弹(只有 info 过滤)。

import { onBeforeUnmount, onMounted } from 'vue'
import { playerService as svc } from '@/services/player'

interface DownloadDonePayload {
  type?: 'download_done'
  ok?: boolean
  track_id?: string
  title?: string
  artist?: string
  local_id?: string
  path?: string
  size?: number
  err?: string
  ts?: number
}

function formatSize(bytes?: number): string {
  if (!bytes || bytes <= 0) return ''
  const KB = 1024
  const MB = KB * 1024
  if (bytes >= MB) return `${(bytes / MB).toFixed(1)}MB`
  if (bytes >= KB) return `${(bytes / KB).toFixed(0)}KB`
  return `${bytes}B`
}

export function useDownloadToast() {
  let unsub: (() => void) | null = null

  function onDownload(payload: DownloadDonePayload) {
    const w = window as any
    if (!w?.prisIragent?.showToast) {
      // vite dev / 普通浏览器 / 单元测试:静默跳过,但 console.warn 留痕
      console.warn('[useDownloadToast] window.prisIragent.showToast missing, skip toast')
      return
    }
    const ok = !!payload?.ok
    const title = payload?.title || payload?.track_id || '歌曲'
    if (ok) {
      // P3.10a level='info' 5s 节流;连续下载不 spam
      const size = formatSize(payload?.size)
      const body = size ? `已下载到本地缓存(${size})` : '已下载到本地缓存'
      void w.prisIragent.showToast({
        title: `PrisirAI · 已下载`,
        body: `${title}${payload?.artist ? ` — ${payload.artist}` : ''}\n${body}`,
        level: 'info',
      })
    } else {
      // 失败:level='error' critical urgency + 不节流
      const err = payload?.err || '未知错误'
      void w.prisIragent.showToast({
        title: `PrisirAI · 下载失败`,
        body: `${title}\n${err}`,
        level: 'error',
      })
    }
  }

  onMounted(() => {
    svc.on('download', onDownload)
    unsub = () => svc.off('download', onDownload)
  })

  onBeforeUnmount(() => {
    if (unsub) {
      unsub()
      unsub = null
    }
  })
}