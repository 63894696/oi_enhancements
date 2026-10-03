<script setup lang="ts">
// MusicView.vue — P2.5+24(2026-10-03)
// 主视图:顶栏(真歌名池 60 + 标签过滤 + 换一批) + 主区(大封面 + 歌词 + 队列)。
// 顶栏借鉴 Vue-mmPlayer 的「顶栏 tabs」+ YesPlayMusic 的「歌单网格」,
// 但用户拍板「只留播放列表」= 歌单用列表渲染而非网格卡片。
import { onMounted, ref, computed } from 'vue'
import { api } from '@/services/api'
import { useUiStore } from '@/stores/ui'
import { usePlayerStore } from '@/stores/player'
import Cover from '@/components/Cover.vue'
import LyricPanel from '@/components/LyricPanel.vue'
import QueueList from '@/components/QueueList.vue'
import Toast from '@/components/Toast.vue'

const ui = useUiStore()
const player = usePlayerStore()

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

async function loadSongs() {
  loading.value = true
  try {
    const tag = ui.tagFilter ? `?tag=${encodeURIComponent(ui.tagFilter)}` : ''
    const r = await api(`/api/songs${tag}`)
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

async function onTagClick(tag: string) {
  await ui.setTag(tag === ui.tagFilter ? '' : tag)
  await loadSongs()
}

async function onPlaySong(s: Song) {
  await player.playById(s.id)
  ui.pushToast('info', `正在播放: ${s.title} - ${s.artist}`)
}

onMounted(loadSongs)

const headerText = computed(() => {
  if (!songs.value.length) return '真歌名池(空)'
  return `真歌名池(可见 ${songs.value.length}/${total.value})`
})
</script>

<template>
  <div class="music-view">
    <Toast />

    <!-- 顶栏:标题 + 标签过滤 + 换一批 + LX 探测灯 + seed 兜底 -->
    <div class="topbar">
      <div class="left">
        <span class="header">{{ headerText }}</span>
        <button class="btn-respin" @click="onRespin" :disabled="loading" v-if="!ui.tagFilter">
          🔄 换一批
        </button>
        <span v-if="ui.tagFilter" class="tag-active">
          #{{ ui.tagFilter }}
          <button class="x" @click="onTagClick(ui.tagFilter)" title="清除">×</button>
        </span>
      </div>
      <div class="probe-row">
        <span class="probe" :class="{ ok: player.onlineReady, off: !player.onlineReady }"
              :title="player.onlineReady ? 'LX 在线源 OK' : 'LX 在线源未就绪'">
          LX:{{ player.onlineReady ? 'OK' : 'OFF' }}
        </span>
        <span class="probe seed" :class="{ ok: player.seedFallback }"
              title="seed.mp3 兜底">
          SEED:{{ player.seedFallback ? '✓' : '✗' }}
        </span>
      </div>
    </div>

    <!-- 标签过滤 chips -->
    <div class="tags">
      <button v-for="t in tags" :key="t"
              class="tag" :class="{ active: t === ui.tagFilter }"
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
             @click="onPlaySong(s)">
          <span class="idx">{{ i + 1 }}</span>
          <span class="title">{{ s.title }}</span>
          <span class="artist">{{ s.artist }}</span>
          <span class="tag-sm">{{ s.tag }}</span>
        </div>
      </div>
    </div>

    <!-- 大封面 + 歌词 + 队列(viewMode 切换) -->
    <div class="bottom">
      <Cover v-show="ui.viewMode === 'lyric' || ui.viewMode === 'playlist'" />
      <LyricPanel v-show="ui.viewMode === 'lyric' || ui.viewMode === 'playlist'" />
      <QueueList v-show="ui.viewMode === 'queue'" />
    </div>
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
.left { display: flex; align-items: center; gap: 12px; }
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
  grid-template-columns: 240px 1fr;
  gap: 16px;
  margin-top: 4px;
}
</style>