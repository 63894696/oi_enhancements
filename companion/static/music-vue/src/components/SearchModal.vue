<script setup lang="ts">
// SearchModal.vue — P2.5+29(2026-10-05)
// 顶栏 🔍 按钮触发浮层 modal;输入 + 结果列表 + 点歌播。
// 沿用 LX Music 桌面端 search 范式:输入关键词 → 5 源并行 fallback → 渲染 → 点歌播。
// 不污染 CSV 列表 + P3.7 chip 多选 + N9 AI 推荐区;modal 独立浮层,关闭释放。
//
// 0 上传红线:仅 query 关键词外发,/api/search?q=17 与 LX dispatcher 都只收文本,
//             音频不上传任何服务器(沿用 P3.10b 红线)。
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { api } from '@/services/api'
import { useSearchStore, type SearchEntry } from '@/stores/search'
import { usePlayerStore } from '@/stores/player'
import { useUiStore } from '@/stores/ui'

interface Props {
  visible: boolean
}
const props = defineProps<Props>()
const emit = defineEmits<{
  close: []
}>()

interface SearchResult {
  songmid: string
  songname: string
  singer: string
  album: string
  duration: number
  source: string
}

const query = ref('')
const results = ref<SearchResult[]>([])
const loading = ref(false)
const lastErr = ref('')
const activeIdx = ref(-1)  // 键盘 ↑↓ 高亮

const searchStore = useSearchStore()
const player = usePlayerStore()
const ui = useUiStore()

let debounceTimer: number | null = null
const SEARCH_DEBOUNCE_MS = 300  // 在线请求 + 多源并发,稍宽 debounce
const DEFAULT_LIMIT = 20

function onInput(e: Event) {
  const v = (e.target as HTMLInputElement).value
  query.value = v
  if (debounceTimer != null) {
    window.clearTimeout(debounceTimer)
    debounceTimer = null
  }
  const trimmed = v.trim()
  if (!trimmed) {
    results.value = []
    loading.value = false
    return
  }
  debounceTimer = window.setTimeout(() => {
    void doSearch(trimmed)
  }, SEARCH_DEBOUNCE_MS)
}

async function doSearch(q: string) {
  const trimmed = (q || '').trim()
  if (!trimmed) {
    results.value = []
    return
  }
  loading.value = true
  lastErr.value = ''
  try {
    const r = await api(`/api/search?q=${encodeURIComponent(trimmed)}&limit=${DEFAULT_LIMIT}`)
    if (r && r.ok && Array.isArray(r.results)) {
      results.value = r.results as SearchResult[]
      searchStore.recordQuery(trimmed)
      searchStore.setLastResults(results.value)
      activeIdx.value = results.value.length > 0 ? 0 : -1
    } else {
      results.value = []
      activeIdx.value = -1
      lastErr.value = (r && r.err) || '搜索失败'
      ui.pushToast('warn', lastErr.value || '搜索')
    }
  } catch (e) {
    results.value = []
    activeIdx.value = -1
    lastErr.value = (e as Error).message || '网络异常'
    ui.pushToast('error', `搜索异常:${lastErr.value}`)
  } finally {
    loading.value = false
  }
}

async function onPickSong(s: SearchResult) {
  // P2.5+29(2026-10-05):把 SearchResult 包装成 IMusicItem,直接调 player.playSong。
  //   playSong 内部 /api/cmd play_url 会把 songmid 传到后端,musicUrl 双源 fallback
  //   拿到 mp3 URL → audio.src → 播(沿用 P3.3 ship 的 playSong 流程)。
  // source='lx' 是为了让 PlayerService/playerStore 知道是 LX 在线源出的歌(不走 local library 流程)。
  try {
    await player.playSongItem({
      id: s.songmid,
      title: s.songname,
      artist: s.singer,
      album: s.album || '',
      duration: s.duration || 0,
      source: 'lx',
    })
    ui.pushToast('info', `正在播放:${s.songname} - ${s.singer}`)
    emit('close')
  } catch (e) {
    ui.pushToast('error', `播放失败:${(e as Error).message || '未知'}`)
  }
}

function pickHistory(q: string) {
  query.value = q
  // 立即触发,不走 debounce
  if (debounceTimer != null) {
    window.clearTimeout(debounceTimer)
    debounceTimer = null
  }
  void doSearch(q)
}

function onKeyDown(e: KeyboardEvent) {
  if (!props.visible) return
  // Escape 关闭
  if (e.key === 'Escape') {
    e.preventDefault()
    emit('close')
    return
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    if (results.value.length === 0) return
    activeIdx.value = (activeIdx.value + 1) % results.value.length
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    if (results.value.length === 0) return
    activeIdx.value = activeIdx.value <= 0 ? results.value.length - 1 : activeIdx.value - 1
  } else if (e.key === 'Enter') {
    e.preventDefault()
    if (activeIdx.value >= 0 && activeIdx.value < results.value.length) {
      void onPickSong(results.value[activeIdx.value])
    }
  }
}

async function loadCache() {
  try {
    const r = await api('/api/search/cache')
    if (r && r.ok && r.cache) {
      searchStore.setCache(r.cache)
    }
  } catch {
    // 静默;cache 加载失败不影响 modal 使用
  }
}

watch(
  () => props.visible,
  (v) => {
    if (v) {
      // 打开时:拉历史 + 自动 focus input(下一帧)
      void loadCache()
      // 自动 focus 输入框
      window.setTimeout(() => {
        const el = document.getElementById('search-modal-input') as HTMLInputElement | null
        if (el) el.focus()
      }, 50)
    } else {
      // 关闭时:清 query + results(避免下次打开残留)
      query.value = ''
      results.value = []
      loading.value = false
      activeIdx.value = -1
      lastErr.value = ''
    }
  },
)

onMounted(() => {
  window.addEventListener('keydown', onKeyDown)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown)
  if (debounceTimer != null) {
    window.clearTimeout(debounceTimer)
    debounceTimer = null
  }
})
</script>

<template>
  <transition name="modal-fade">
    <div v-if="visible" class="modal-mask" @click.self="emit('close')">
      <div class="modal-panel">
        <div class="modal-header">
          <input
            id="search-modal-input"
            type="search"
            class="search-input-modal"
            :value="query"
            @input="onInput"
            placeholder="搜歌名/歌手(5 源并行:网易云/QQ/酷我/酷狗/咪咕)"
            autocomplete="off"
            spellcheck="false"
          />
          <button class="btn-close" @click="emit('close')" title="关闭(Esc)">✕</button>
        </div>

        <div class="modal-body">
          <!-- 历史 query 短列表(query 空时显示) -->
          <div
            v-if="!query.trim() && searchStore.history.length > 0"
            class="history-block"
          >
            <div class="block-title">最近搜过</div>
            <div class="history-list">
              <button
                v-for="h in searchStore.history.slice(0, 10)"
                :key="h"
                class="history-chip"
                @click="pickHistory(h)"
                :title="h"
              >
                {{ h }}
              </button>
            </div>
          </div>

          <!-- 空 query 又无历史:占位 -->
          <div
            v-else-if="!query.trim()"
            class="status status-empty"
          >
            <div class="hint-title">输入歌名或歌手,5 源并行搜</div>
            <div class="hint-examples">
              <span class="ex">周杰伦</span>
              <span class="ex">孤勇者 - 陈奕迅</span>
              <span class="ex">晴天 - 周杰伦</span>
              <span class="ex">起风了 - 买辣椒也用券</span>
            </div>
            <div class="hint-tip" v-if="searchStore.total">
              已搜过 {{ searchStore.total }} 首,缓存到本地
              <code>_search_cache.json</code>
            </div>
          </div>

          <!-- loading -->
          <div v-else-if="loading" class="status">搜索中…</div>

          <!-- 异常 -->
          <div v-else-if="lastErr && !results.length" class="status status-err">
            {{ lastErr }}
          </div>

          <!-- 无结果 -->
          <div v-else-if="!results.length" class="status">没有匹配歌曲</div>

          <!-- 结果列表 -->
          <div v-else class="result-list">
            <div
              v-for="(s, idx) in results"
              :key="s.songmid"
              class="result-row"
              :class="{ active: idx === activeIdx }"
              @click="onPickSong(s)"
              @mouseenter="activeIdx = idx"
            >
              <div class="result-main">
                <span class="result-title">{{ s.songname }}</span>
                <span class="result-singer">{{ s.singer }}</span>
              </div>
              <div class="result-meta">
                <span class="result-src" :class="`src-${s.source}`">{{ s.source }}</span>
                <span class="result-album" v-if="s.album">{{ s.album }}</span>
                <span class="result-duration" v-if="s.duration">{{ formatDuration(s.duration) }}</span>
              </div>
            </div>
          </div>
        </div>

        <div class="modal-footer">
          <span class="tip">
            <kbd>↑</kbd><kbd>↓</kbd> 选择 · <kbd>Enter</kbd> 播放 · <kbd>Esc</kbd> 关闭
          </span>
          <span class="tip" v-if="searchStore.entries && searchStore.total">
            已搜过 {{ searchStore.total }} 首(本地缓存)
          </span>
        </div>
      </div>
    </div>
  </transition>
</template>

<script lang="ts">
function formatDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return ''
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
</script>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(2px);
}
.modal-panel {
  background: #1e1f24;
  color: #e6e6e8;
  border-radius: 10px;
  width: 560px;
  max-width: 92vw;
  height: 600px;
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.6);
  border: 1px solid #2f3038;
}
.modal-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid #2f3038;
}
.search-input-modal {
  flex: 1;
  background: #15171b;
  color: #f0f0f3;
  border: 1px solid #3a3c45;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 14px;
  outline: none;
}
.search-input-modal:focus {
  border-color: #6c8cff;
}
.btn-close {
  background: transparent;
  border: 1px solid #3a3c45;
  color: #cfd0d6;
  border-radius: 4px;
  width: 32px;
  height: 32px;
  cursor: pointer;
  font-size: 14px;
}
.btn-close:hover {
  background: #2a2c33;
}
.modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}
.status {
  padding: 24px;
  text-align: center;
  color: #8e8f97;
  font-size: 13px;
}
.status-err {
  color: #ff8a8a;
}
.status-empty .hint-title {
  font-size: 14px;
  margin-bottom: 12px;
  color: #cfd0d6;
}
.status-empty .hint-examples {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  margin-bottom: 16px;
}
.status-empty .ex {
  background: #2a2c33;
  border-radius: 4px;
  padding: 4px 10px;
  color: #b8c2ff;
  font-size: 13px;
}
.status-empty .hint-tip {
  font-size: 11px;
  color: #6f6f78;
}
.status-empty code {
  background: #2a2c33;
  padding: 1px 4px;
  border-radius: 3px;
}
.history-block {
  padding: 8px 14px;
}
.block-title {
  font-size: 11px;
  color: #6f6f78;
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.history-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.history-chip {
  background: #2a2c33;
  border: 1px solid #3a3c45;
  color: #cfd0d6;
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.history-chip:hover {
  background: #353841;
  border-color: #6c8cff;
}
.result-list {
  display: flex;
  flex-direction: column;
}
.result-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 14px;
  cursor: pointer;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}
.result-row:hover,
.result-row.active {
  background: #2c303a;
}
.result-row.active {
  border-left: 2px solid #6c8cff;
  padding-left: 12px;
}
.result-main {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex: 1;
  min-width: 0;
}
.result-title {
  font-size: 13px;
  color: #f0f0f3;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 200px;
}
.result-singer {
  font-size: 12px;
  color: #8e8f97;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
}
.result-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #6f6f78;
}
.result-src {
  font-family: monospace;
  font-size: 10px;
  background: #2a2c33;
  border-radius: 3px;
  padding: 1px 5px;
  color: #b8c2ff;
}
.src-wy {
  color: #c73b73;
}
.src-tx {
  color: #6cb73b;
}
.src-kw {
  color: #b78a3b;
}
.src-kg {
  color: #3b8ac7;
}
.src-mg {
  color: #b73b6c;
}
.result-album {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.modal-footer {
  display: flex;
  justify-content: space-between;
  padding: 8px 14px;
  border-top: 1px solid #2f3038;
  font-size: 11px;
  color: #6f6f78;
}
.modal-footer .tip {
  display: flex;
  align-items: center;
  gap: 4px;
}
kbd {
  background: #2a2c33;
  border: 1px solid #3a3c45;
  border-radius: 3px;
  padding: 0 4px;
  font-family: monospace;
  font-size: 10px;
  color: #cfd0d6;
}
.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 0.15s ease;
}
.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}
</style>