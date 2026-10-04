// stores/ui.ts — P2.5+24(2026-10-03) → P3.3(2026-10-03)加 pushListToast
// UI 状态:侧栏模式(viewMode) + toast + 当前标签过滤。
// P3.3(2026-10-03):pushListToast 支持多行 list 渲染(收藏列表 8s TTL)。
// P3.5(2026-10-04):showEqPanel + toggleEqPanel(MusicView 主窗 EQ 抽屉)。
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/services/api'
import { playerService as svc } from '@/services/player'

export type ViewMode = 'playlist' | 'lyric' | 'queue'

export interface ToastMsg {
  id: number
  type: 'info' | 'warn' | 'error'
  text: string
}

// P3.3(2026-10-03):List 类型 toast(多行列表,8s TTL)
export interface ListToastMsg {
  id: number
  type: 'list'
  title: string
  items: { title: string; subtitle?: string }[]
  ttlMs: number
}

export type AnyToastMsg = ToastMsg | ListToastMsg

export const useUiStore = defineStore('ui', () => {
  const viewMode = ref<ViewMode>('playlist')
  const tagFilter = ref<string>('')  // 当前标签过滤(空 = 全部)
  const toasts = ref<AnyToastMsg[]>([])
  // P3.5(2026-10-04):EQ 抽屉显示状态(独立 EQ 窗与 LyricOnlyView 各自持久化,
  // MusicView 抽屉只是会话级 toggle)
  const showEqPanel = ref(false)
  let toastId = 0

  function pushToast(type: ToastMsg['type'], text: string, ttlMs = 2400) {
    const id = ++toastId
    toasts.value.push({ id, type, text })
    window.setTimeout(() => {
      toasts.value = toasts.value.filter((t) => t.id !== id)
    }, ttlMs)
  }

  // P3.3(2026-10-03):多行 list toast(收藏列表专用,默认 8s)
  function pushListToast(
    title: string,
    items: { title: string; subtitle?: string }[],
    ttlMs = 8000,
  ) {
    const id = ++toastId
    toasts.value.push({ id, type: 'list', title, items, ttlMs })
    window.setTimeout(() => {
      toasts.value = toasts.value.filter((t) => t.id !== id)
    }, ttlMs)
  }

  // 订阅 svc toast 事件
  function bootstrap() {
    svc.on('toast', (msg: { type: string; msg: string }) => {
      const t = (['info', 'warn', 'error'].includes(msg.type) ? msg.type : 'info') as ToastMsg['type']
      pushToast(t, msg.msg)
    })
  }

  async function respinPool() {
    return await api('/api/songs/respin', {})
  }

  async function setTag(tag: string) {
    tagFilter.value = tag
  }

  // P3.5(2026-10-04):EQ 抽屉 toggle
  function toggleEqPanel() {
    showEqPanel.value = !showEqPanel.value
  }

  return {
    viewMode, tagFilter, toasts, showEqPanel,
    pushToast, pushListToast, bootstrap, respinPool, setTag, toggleEqPanel,
  }
})