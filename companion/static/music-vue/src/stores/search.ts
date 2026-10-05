// stores/search.ts — P2.5+29(2026-10-05)
// 在线搜歌历史 + cache store。SearchModal 挂载时调 /api/search/cache 拉历史,
// 拿到 entries(已搜 songmid 映射)+ queries(最近 50 条 query)。
//
// 0 上传:仅 query 关键词外发,音频不上传(沿用 P3.10b 红线)。
// 不污染 song_pool_v2.csv;只动 userData/_search_cache.json(Python 端写盘)。
import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface SearchEntry {
  songmid: string
  songname: string
  singer: string
  album?: string
  duration?: number
  source: string
  q: string
  ts?: number
}

export interface SearchCacheResponse {
  version: number
  updated_at_iso: string
  entries: Record<string, SearchEntry>
  queries: string[]
}

export const useSearchStore = defineStore('search', () => {
  // 历史 query(最近 50 条,LIFO 排队,最新在前)
  const history = ref<string[]>([])
  // songmid → entry(后端去重 by songmid,前端直接覆盖)
  const entries = ref<Record<string, SearchEntry>>({})
  // 总搜过数(便于顶栏 badge 「已搜 XX 首」)
  const total = ref(0)
  // 最近一次更新 ISO(显示 cache 时效)
  const updatedAt = ref('')
  // 当前临时结果(还没点歌播的) — modal 关闭前在,关闭释放
  const lastResults = ref<Array<{ songmid: string; songname: string; singer: string; album: string; duration: number; source: string }>>([])

  function setCache(c: SearchCacheResponse | null | undefined) {
    if (!c) return
    entries.value = c.entries || {}
    history.value = Array.isArray(c.queries) ? c.queries : []
    total.value = Object.keys(entries.value).length
    updatedAt.value = c.updated_at_iso || ''
  }

  function recordQuery(q: string) {
    const norm = normQ(q)
    if (!norm) return
    // LIFO + 去重
    history.value = [norm, ...history.value.filter((x) => x !== norm)].slice(0, 50)
  }

  function setLastResults(rs: typeof lastResults.value) {
    lastResults.value = Array.isArray(rs) ? rs.slice() : []
  }

  function clear() {
    history.value = []
    entries.value = {}
    total.value = 0
    lastResults.value = []
  }

  function normQ(q: string): string {
    return (q || '').trim()
  }

  return {
    history,
    entries,
    total,
    updatedAt,
    lastResults,
    setCache,
    recordQuery,
    setLastResults,
    clear,
    normQ,
  }
})