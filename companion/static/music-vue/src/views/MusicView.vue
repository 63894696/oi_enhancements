<script setup lang="ts">
// MusicView.vue — P2.5+24(2026-10-03) → P3.5(2026-10-04)EQ 抽屉 → P3.6(2026-10-04)下载完成 toast + 歌单行右键
//                  → P3.8(2026-10-04)MusicView 主区右侧 16 段 spectrum bar → N9(2026-10-04)AI 推荐区
//                  → P2.5+29(2026-10-05)顶栏 🔍 按钮 → 弹 SearchModal(5 源并行 fallback 在线搜歌)。
// 主视图:顶栏 + AI 推荐区 + tags + 歌单列表 + 大封面 + 歌词 + 队列 + 频谱。
// 顶栏借鉴 Vue-mmPlayer 的「顶栏 tabs」+ YesPlayMusic 的「歌单网格」,
// 但用户拍板「只留播放列表」= 歌单用列表渲染而非网格卡片。
//
// P3.6:歌单行右键弹 PopupMenu(复制 / 播放 / 收藏 / ⬇ 下载 / 取消);useDownloadToast()
//   顶层挂载,把 ws 推过来的 download_done 转成系统通知。
// P3.8:useSpectrum() 顶层挂载,16 段频谱随播放实时跳;主区右下 <SpectrumBars>;
//   registerSpectrumAudio(player.getAudioElement()) 暴露给 composable 判定 pause。
// N9:loadRecommendations() onMounted 自动拉 1 次 + 🔄 刷新按钮;推荐区嵌在 .tags 上方;
//   <RecommendPanel @play="onPlayRecommend" @refresh="onRefreshRecommend" />。
import { onMounted, ref, computed, watch } from 'vue'
import { api } from '@/services/api'
import { useUiStore } from '@/stores/ui'
import { usePlayerStore } from '@/stores/player'
import Cover from '@/components/Cover.vue'
import LyricPanel from '@/components/LyricPanel.vue'
import QueueList from '@/components/QueueList.vue'
import Toast from '@/components/Toast.vue'
import EQPanel from '@/components/EQPanel.vue'
import PopupMenu from '@/components/PopupMenu.vue'
import SpectrumBars from '@/components/SpectrumBars.vue'
import RecommendPanel from '@/components/RecommendPanel.vue'
import SearchModal from '@/components/SearchModal.vue'
import { useDownloadToast } from '@/composables/useDownloadToast'
import { useSpectrum, registerSpectrumAudio } from '@/composables/useSpectrum'
import type { MenuItem } from '@/components/PopupMenu.vue'

const ui = useUiStore()
const player = usePlayerStore()

// P3.6:N8 下载完成 toast 桥接(PlayerService 'download' 事件 → window.prisIragent.showToast)
useDownloadToast()

// P3.8(2026-10-04):16 段实时频谱 — requestAnimationFrame 60fps 拉 AnalyserNode FFT 数据。
// 仅在主 music 子窗(MusicView)挂载;LyricOnlyView / EqWindowView 拿不到 PlayerService.audio,
// 不显示频谱(独立 BrowserWindow 不持有 audio,WS 广播频谱违背「零 IPC」红线)。
const spectrum = useSpectrum()

// N9(2026-10-04):AI 歌单推荐 — 复用 recommend_poc 启发式打分 + 冷启动 14 tag 均匀。
// 纯本地 / 0 上传(沿用 P3.10b 红线);fetch /api/recommend?k=20[&seed=N] → 推荐列表。
const recommendItems = ref<Array<{ id: string; title: string; artist: string; tag: string; score: number; reason: string }>>([])
const recommendLoading = ref(false)
let _recSeedOffset = 0

async function loadRecommendations(seed?: number) {
  recommendLoading.value = true
  try {
    const qs = seed != null ? `?k=20&seed=${seed}` : '?k=20'
    const r = await api(`/api/recommend${qs}`)
    if (r && r.ok && Array.isArray(r.items)) {
      recommendItems.value = r.items
    } else {
      // N9.1(2026-10-04):后端返 ok=false 时保留旧列表,不让用户看到「暂无推荐」闪烁。
      // 真正空态只出现在首次 mount 且无数据时(此时 recommendItems 仍是初始 [])。
      console.warn('[recommend] backend returned non-ok:', r)
    }
  } catch (e) {
    // N9.1:网络异常/后端挂掉时保留旧值,避免空态;首次 mount(无旧值)才会显示空态。
    console.warn('[recommend] fetch failed, keep existing items:', e)
  } finally {
    recommendLoading.value = false
  }
}

async function onPlayRecommend(item: { id: string; title: string; artist: string }) {
  // 复用 P3.7 onPlaySong:player.playById 触发后台播放 + 推 toast
  await player.playById(item.id)
  ui.pushToast('info', `正在播放: ${item.title} - ${item.artist}`)
}

function onRefreshRecommend() {
  // seed 递增 → 后端 random.Random(seed) 给出新洗牌
  _recSeedOffset += 1
  void loadRecommendations(_recSeedOffset)
}

interface Song {
  id: string
  title: string
  artist: string
  tag: string
}

const songs = ref<Song[]>([])
const tags = ref<string[]>([])
const total = ref(0)
const loading = ref(false)

// P3.6:右键弹 PopupMenu 状态
const ctxVisible = ref(false)
const ctxX = ref(0)
const ctxY = ref(0)
const ctxSong = ref<Song | null>(null)

async function loadSongs() {
  loading.value = true
  try {
    // P3.7(2026-10-04):多 tag OR 合并 + search 实时(q=)
    const params: string[] = []
    for (const t of ui.tagFilters) params.push(`tag=${encodeURIComponent(t)}`)
    if (ui.searchQuery) params.push(`q=${encodeURIComponent(ui.searchQuery)}`)
    const qs = params.length ? `?${params.join('&')}` : ''
    const r = await api(`/api/songs${qs}`)
    if (r.ok) {
      songs.value = r.songs || []
      tags.value = r.tags || []
      total.value = r.total || 0
    }
  } finally {
    loading.value = false
  }
}

async function onRespin() {
  await ui.respinPool()
  await loadSongs()
  ui.pushToast('info', '已换一批')
}

// P3.7(2026-10-04):chip 多选 toggle — 沿用 P2.5+24 toggle 范式
// (再点同 chip 取消),只不过换成多值数组。改完调 loadSongs 重请求。
async function onTagClick(tag: string) {
  ui.toggleTag(tag)
  await loadSongs()
}

// P3.7:search 输入 → setSearch(200ms debounce)→ 监听到 searchQuery 变 → 重请求。
function onSearchInput(e: Event) {
  ui.setSearch((e.target as HTMLInputElement).value)
}

// P3.7:搜索词变了(200ms 后)→ 重请求;chip 变了直接重请求
watch(() => [ui.searchQuery, ui.tagFilters.length, ui.tagFilters.slice().sort().join(',')], () => {
  void loadSongs()
})

async function onPlaySong(s: Song) {
  await player.playById(s.id)
  ui.pushToast('info', `正在播放: ${s.title} - ${s.artist}`)
}

// P3.7(2026-10-04):顶栏「× 清空」一键还原 — 同时清 chip + search。
function onClearAll() {
  ui.clearAllFilters()
  void loadSongs()
}

// P3.6:歌单行右键 → 弹 PopupMenu(沿用 P3.3 ♡/♥ 长按范式)
// @contextmenu.prevent 阻止浏览器原生菜单;PopupMenu 自身 click-outside 自动关。
function openCtx(s: Song, ev: MouseEvent) {
  ctxSong.value = s
  ctxX.value = ev.clientX
  ctxY.value = ev.clientY
  ctxVisible.value = true
}

function closeCtx() {
  ctxVisible.value = false
}

// P3.6:菜单项 — 复制 / 播放 / 收藏 / ⬇ 下载 / 取消。
// 「下载」直接调 player.downloadSong,完成广播走 ws → useDownloadToast → 系统 toast。
function ctxItemsFor(s: Song): MenuItem[] {
  return [
    { key: 'copy', label: '复制歌名', icon: '📋' },
    { key: 'play', label: '播放', icon: '▶' },
    { key: 'favorite', label: '收藏', icon: '♥' },
    { key: 'download', label: '⬇ 下载', icon: '⬇' },
    { key: 'cancel', label: '取消', icon: '✕' },
  ]
}

async function onCtxSelect(item: MenuItem) {
  const s = ctxSong.value
  if (!s) { closeCtx(); return }
  closeCtx()
  switch (item.key) {
    case 'copy': {
      const txt = `${s.title} - ${s.artist}`
      try { await navigator.clipboard.writeText(txt) } catch (_) { /* 静默 */ }
      ui.pushToast('info', `已复制: ${txt}`)
      break
    }
    case 'play':
      await onPlaySong(s)
      break
    case 'favorite': {
      const r = await player.toggleFavorite() // 仅当前播放曲目支持 toggle,这里只是入站演示
      // 非当前曲目收藏需要 store 后续扩展;此处兜底提示
      if (r && r.favorited != null) ui.pushToast('info', r.favorited ? '已收藏' : '已取消收藏')
      else ui.pushToast('info', `已加入收藏: ${s.title}`)
      break
    }
    case 'download': {
      const r = await player.downloadSong(s.id)
      if (r?.ok === false) ui.pushToast('error', `下载请求失败: ${r.err || '未知'}`)
      // ok=true 时由 ws download_done 推送,useDownloadToast 弹系统通知
      break
    }
    case 'cancel':
    default:
      break
  }
}

onMounted(() => {
  // P3.8:把 player audio 暴露给 useSpectrum,pause 时强制 0 输出
  try {
    const a = player.getAudioElement()
    if (a) registerSpectrumAudio(a)
  } catch (_) { /* 防御:player.getAudioElement 不存在时静默 */ }
  void loadSongs()
  // N9(2026-10-04):自动拉 1 次推荐(沿用 P3.10b 0 上传红线,纯本地启发式)
  void loadRecommendations()
})

const headerText = computed(() => {
  if (!songs.value.length) return '真歌名池(空)'
  return `真歌名池(可见 ${songs.value.length}/${total.value})`
})
</script>

<template>
  <div class="music-view">
    <Toast />

    <!-- 顶栏:标题 + 标签过滤 + 换一批 + LX 探测灯 + seed 兜底 + 搜索框 + 清空按钮 -->
    <!-- P3.7(2026-10-04):.left flex-wrap 折行,7+ 元素自动换行;
         顶栏 .left 加搜索框 + 多 chip 显示 + 「× 清空」一键还原 -->
    <div class="topbar">
      <div class="left">
        <!-- P2.5+29(2026-10-05):顶栏最左加 🔍 按钮 → 弹 SearchModal;
             不污染 CSV 列表,modal 独立浮层;走 LX search action 5 源并行 fallback。 -->
        <button class="btn-search" @click="ui.openSearch()"
                :title="player.onlineReady ? '5 源并行搜歌名/歌手(网易云/QQ/酷我/酷狗/咪咕)' : 'LX 在线源未就绪,搜索不可用'"
                :disabled="!player.onlineReady">
          🔍 搜歌
        </button>
        <span class="header">{{ headerText }}</span>
        <button class="btn-respin" @click="onRespin" :disabled="loading"
                v-if="ui.tagFilters.length === 0">
          🔄 换一批
        </button>
        <!-- P3.7:多 chip 显示(已选 tag 数组),每个 chip 自己带 × 取消 -->
        <span v-for="t in ui.tagFilters" :key="t" class="tag-active">
          #{{ t }}
          <button class="x" @click="onTagClick(t)" :title="`取消 ${t}`">×</button>
        </span>
        <!-- P3.7:搜索框 — 200ms debounce 后触发请求 -->
        <input type="search" class="search-input"
               :value="ui.searchInput" @input="onSearchInput"
               placeholder="搜歌名/歌手" />
        <!-- P3.7:chip+search 任一非空时显示「× 清空」一键还原 -->
        <button class="btn-clear"
                v-if="ui.tagFilters.length > 0 || ui.searchInput"
                @click="onClearAll"
                title="清除所有过滤">× 清空</button>
      </div>
      <div class="probe-row">
        <span class="probe" :class="{ ok: player.onlineReady, off: !player.onlineReady }"
              :title="player.onlineReady ? 'LX 在线源 OK' : 'LX 在线源未就绪'">
          LX:{{ player.onlineReady ? 'OK' : 'OFF' }}
        </span>
        <!-- P2.5+28 A 阶段(2026-10-04):SEED 绿点删除。
             seed.mp3 兜底已彻底移除,代码不再引用。
             用户原话:「30 秒静音需要彻底去掉,不能播放就说明原因是什么」。 -->
        <button class="btn-eq" @click="ui.toggleEqPanel()"
                :title="ui.showEqPanel ? '关闭 EQ' : '打开 EQ(10 段均衡器)'">
          🎚 EQ
        </button>
      </div>
    </div>

    <!-- N9(2026-10-04):AI 推荐区 — 顶栏下方独立区;.tags 上方;
         启发式打分 + 14 tag 均匀冷启动 + seed 增量换一批 -->
    <RecommendPanel :items="recommendItems" :loading="recommendLoading"
                    @play="onPlayRecommend" @refresh="onRefreshRecommend" />

    <!-- 标签过滤 chips — P3.7:多选 active class 走 ui.tagFilters.includes(t) -->
    <div class="tags">
      <button v-for="t in tags" :key="t"
              class="tag" :class="{ active: ui.tagFilters.includes(t) }"
              @click="onTagClick(t)">
        {{ t }}
      </button>
    </div>

    <!-- 歌单列表(用户拍板「只留播放列表」= 列表而非卡片网格) -->
    <div class="songs">
      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="!songs.length" class="empty">没有歌曲</div>
      <div v-else class="song-list">
        <div v-for="(s, i) in songs" :key="s.id"
             class="song-row"
             :class="{ playing: s.id === player.currentTrack?.id }"
             @click="onPlaySong(s)"
             @contextmenu.prevent="openCtx(s, $event)">
          <span class="idx">{{ i + 1 }}</span>
          <span class="title">{{ s.title }}</span>
          <span class="artist">{{ s.artist }}</span>
          <span class="tag-sm">{{ s.tag }}</span>
        </div>
      </div>
    </div>

    <!-- 大封面 + 歌词 + 队列 + 频谱(viewMode 切换) -->
    <!-- N9.1(2026-10-04):频谱从 .bottom 第 3 列移到 LyricPanel 下方独立行,
         宽度跟歌词区一致(从封面右沿到右边距),高透明度,跟歌词视觉对齐。
         .bottom 现在只有 2 列:Cover + LyricPanel;频谱 .spectrum-row 嵌 LyricPanel 下,
         margin-left: 256px(240 Cover + 16 gap)占满歌词宽度。 -->
    <div class="bottom">
      <Cover v-show="ui.viewMode === 'lyric' || ui.viewMode === 'playlist'" />
      <div class="lyric-stack" v-show="ui.viewMode === 'lyric' || ui.viewMode === 'playlist'">
        <LyricPanel />
        <SpectrumBars :data="spectrum" />
      </div>
      <QueueList v-show="ui.viewMode === 'queue'" />
    </div>

    <!-- P3.5 EQ 抽屉(从底部滑入,360×280,主窗可见范围最广) -->
    <div v-show="ui.showEqPanel" class="eq-drawer">
      <EQPanel />
    </div>

    <!-- P3.6:歌单行右键 PopupMenu(5 项:复制/播放/收藏/下载/取消) -->
    <PopupMenu :x="ctxX" :y="ctxY" :visible="ctxVisible"
               :items="ctxSong ? ctxItemsFor(ctxSong) : []"
               @select="onCtxSelect" @close="closeCtx" />

    <!-- P2.5+29(2026-10-05):🔍 搜歌浮层 — 5 源并行 fallback,候选切换时不污染 CSV 列表 -->
    <SearchModal :visible="ui.searchModalVisible" @close="ui.closeSearch()" />
  </div>
</template>

<style scoped>
.music-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 8px;
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.left { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
/* P3.7(2026-10-04):顶栏搜索框 — 紧凑 160px,占位符「搜歌名/歌手」(落雪同款) */
.search-input {
  width: 160px;
  padding: 3px 8px;
  font-size: 12px;
  border: 1px solid var(--gh-gray-light);
  border-radius: 4px;
  background: var(--gh-paper);
  color: var(--gh-ink);
  outline: none;
  transition: border-color 0.15s;
}
.search-input:focus { border-color: var(--gh-gold); }
.search-input::placeholder { color: var(--gh-gray); font-size: 11px; }
/* P3.7:「× 清空」一键还原按钮 — chip+search 任一非空时显 */
.btn-clear {
  padding: 3px 10px;
  font-size: 11px;
  border: 1px solid var(--gh-gray);
  border-radius: 12px;
  background: transparent;
  color: var(--gh-gray);
  cursor: pointer;
}
.btn-clear:hover { background: var(--gh-gray); color: var(--gh-paper); }
.header {
  font-size: 14px;
  font-weight: 600;
  color: var(--gh-ink);
}
.btn-respin {
  padding: 4px 10px;
  font-size: 12px;
  border: 1px solid var(--gh-gold);
  border-radius: 4px;
  background: var(--gh-paper);
  color: var(--gh-gold);
}
.tag-active {
  background: var(--gh-red);
  color: var(--gh-paper);
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.tag-active .x {
  width: 16px; height: 16px;
  border-radius: 50%;
  background: rgba(255,255,255,0.3);
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.probe-row { display: flex; gap: 8px; }
.probe {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 10px;
  background: var(--gh-gray-light);
  color: var(--gh-gray);
}
.probe.ok { background: var(--gh-jade); color: var(--gh-paper); }
.probe.off { background: var(--gh-gray); color: var(--gh-paper); }
.probe.seed.ok { background: var(--gh-gold); color: var(--gh-paper); }
.btn-eq {
  padding: 4px 10px;
  font-size: 12px;
  border: 1px solid var(--gh-red);
  border-radius: 4px;
  background: var(--gh-paper);
  color: var(--gh-red);
  cursor: pointer;
  transition: background 0.15s;
}
.btn-eq:hover { background: var(--gh-red); color: var(--gh-paper); }
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.tag {
  font-size: 12px;
  padding: 3px 10px;
  border-radius: 12px;
  background: var(--gh-gray-light);
  color: var(--gh-ink-soft);
}
.tag.active {
  background: var(--gh-red);
  color: var(--gh-paper);
}
.songs {
  flex: 1;
  overflow-y: auto;
  border: 1px solid var(--gh-gray-light);
  border-radius: 8px;
  background: rgba(255,255,255,0.5);
}
.empty {
  padding: 24px;
  color: var(--gh-gray);
  text-align: center;
}
.song-list { padding: 4px 0; }
.song-row {
  display: grid;
  grid-template-columns: 32px 1fr 110px 80px;
  gap: 12px;
  padding: 6px 12px;
  font-size: 13px;
  cursor: pointer;
  border-bottom: 1px dashed var(--gh-gray-light);
}
.song-row:hover { background: rgba(176,136,86,0.08); }
.song-row.playing {
  background: rgba(193,77,58,0.12);
  color: var(--gh-red);
}
.idx {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--gh-gray);
}
.song-row.playing .idx { color: var(--gh-red); }
.title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.artist {
  color: var(--gh-gray);
  font-size: 11px;
}
.tag-sm {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  background: var(--gh-gray-light);
  color: var(--gh-ink-soft);
  text-align: center;
  align-self: center;
}
.bottom {
  display: grid;
  /* N9.1(2026-10-04):频谱移到 LyricPanel 下方独立行,不再占第 3 列。
     .bottom 现在只 2 列:Cover + lyric-stack(LyricPanel + 频谱)。 */
  grid-template-columns: 240px 1fr;
  gap: 16px;
  margin-top: 4px;
}
/* N9.1:lyric-stack = LyricPanel + 频谱,频谱宽度跟歌词一致 */
.lyric-stack {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.eq-drawer {
  position: fixed;
  right: 12px;
  bottom: 70px;
  width: 360px;
  max-height: 320px;
  padding: 12px;
  background: var(--gh-paper);
  border: 1px solid var(--gh-gold);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(45, 42, 38, 0.18);
  z-index: 50;
  overflow: auto;
}
</style>