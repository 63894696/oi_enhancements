// stores/ui.ts — P2.5+24(2026-10-03)
// UI 状态:侧栏模式(viewMode) + toast + 当前标签过滤。
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

export const useUiStore = defineStore('ui', () => {
  const viewMode = ref<ViewMode>('playlist')
  const tagFilter = ref<string>('')  // 当前标签过滤(空 = 全部)
  const toasts = ref<ToastMsg[]>([])
  let toastId = 0

  function pushToast(type: ToastMsg['type'], text: string, ttlMs = 2400) {
    const id = ++toastId
    toasts.value.push({ id, type, text })
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

  return {
    viewMode, tagFilter, toasts,
    pushToast, bootstrap, respinPool, setTag,
  }
})