// stores/ui.ts — P2.5+24(2026-10-03) → P3.3(2026-10-03)加 pushListToast
// UI 状态:侧栏模式(viewMode) + toast + 当前标签过滤。
// P3.3(2026-10-03):pushListToast 支持多行 list 渲染(收藏列表 8s TTL)。
// P3.5(2026-10-04):showEqPanel + toggleEqPanel(MusicView 主窗 EQ 抽屉)。
// P3.7(2026-10-04):tagFilter → tagFilters: string[] (chip 多选 OR 合并) +
//   searchInput + searchQuery (200ms debounce 实时搜索 title/artist)。
//   tagFilter/setTag 保留为单值便捷别名(其它调用方无破坏)。
// P2.5+29(2026-10-05):searchModalVisible + openSearch/closeSearch(SearchModal 浮层)。
//                   0 上传:仅 query 关键词外发,音频不上传(沿用 P3.10b 红线)。
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
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
  // P3.7(2026-10-04):chip 多选 — 空数组 = 全部;OR 合并后端 list_visible
  const tagFilters = ref<string[]>([])
  // P3.7:实时搜索 — searchInput 立即绑 v-model,200ms debounce 后 set searchQuery
  //   (searchQuery 才是真用于请求后端的字符串)
  const searchInput = ref<string>('')
  const searchQuery = ref<string>('')
  // P3.7:debounce timer(模块内单例,组件间共用)
  let _searchTimer: number | null = null
  const SEARCH_DEBOUNCE_MS = 200
  const toasts = ref<AnyToastMsg[]>([])
  // P3.5(2026-10-04):EQ 抽屉显示状态(独立 EQ 窗与 LyricOnlyView 各自持久化,
  // MusicView 抽屉只是会话级 toggle)
  const showEqPanel = ref(false)
  // P2.5+29(2026-10-05):SearchModal 浮层显示状态(只挂 MusicView 主窗,
  //   LyricOnlyView / EqWindowView 不挂 modal,避免跨 BrowserWindow 状态串扰)。
  const searchModalVisible = ref(false)
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

  // P3.7(2026-10-04):chip 多选 toggle — 已存在则移除,否则 push。
  // 沿用 P2.5+24 「再点同 chip 取消」的 toggle 范式,只不过换成多值。
  function toggleTag(tag: string) {
    if (!tag) return
    const idx = tagFilters.value.indexOf(tag)
    if (idx >= 0) tagFilters.value.splice(idx, 1)
    else tagFilters.value.push(tag)
  }

  function clearTags() {
    tagFilters.value = []
  }

  // P3.7:search 输入 — 清旧 timer,新 timer 200ms 后才 set searchQuery(防 spam)。
  // MusicView 调 loadSongs() 看 searchQuery(tagFilters 也是),所以 searchQuery
  //   变了就触发请求。searchInput 立即响应 v-model,不参与请求。
  function setSearch(q: string) {
    searchInput.value = q
    if (_searchTimer != null) {
      window.clearTimeout(_searchTimer)
      _searchTimer = null
    }
    const next = (q || '').trim()
    _searchTimer = window.setTimeout(() => {
      searchQuery.value = next
      _searchTimer = null
    }, SEARCH_DEBOUNCE_MS)
  }

  // P3.7:一键清空 chip + search(顶栏「× 清空」按钮)
  function clearAllFilters() {
    clearTags()
    if (_searchTimer != null) {
      window.clearTimeout(_searchTimer)
      _searchTimer = null
    }
    searchInput.value = ''
    searchQuery.value = ''
  }

  // P3.7:tagFilter 单值便捷别名 — 取第一个 chip(其它 4 个旧调用方未升级 P3.7,免破)。
  const tagFilter = computed<string>(() => tagFilters.value[0] || '')
  async function setTag(tag: string) {
    // 旧 API:把 tag 设为单选 = tagFilters = [tag](空字符串 = 清空)
    if (!tag) clearTags()
    else tagFilters.value = [tag]
  }

  // P3.5(2026-10-04):EQ 抽屉 toggle
  function toggleEqPanel() {
    showEqPanel.value = !showEqPanel.value
  }

  // P2.5+29(2026-10-05):SearchModal 开关 — 顶栏 🔍 按钮触发 openSearch,modal 关闭调 closeSearch。
  function openSearch() {
    searchModalVisible.value = true
  }
  function closeSearch() {
    searchModalVisible.value = false
  }

  return {
    viewMode, tagFilter, tagFilters, searchInput, searchQuery, toasts, showEqPanel,
    searchModalVisible,
    pushToast, pushListToast, bootstrap, respinPool,
    setTag, toggleTag, clearTags, setSearch, clearAllFilters,
    toggleEqPanel, openSearch, closeSearch,
  }
})