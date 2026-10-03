<script setup lang="ts">
// MiniBar.vue — P2.5+24(2026-10-03)
// 底部 88px mini bar:封面 56 + 曲名艺人 + ⏮⏯⏭ + 三层进度 + ♥ 收藏 + 🔊 音量。
import { computed } from 'vue'
import { usePlayerStore } from '@/stores/player'
import { useUiStore } from '@/stores/ui'
import ProgressBar from './ProgressBar.vue'

const player = usePlayerStore()
const ui = useUiStore()

const isPlaying = computed(() => player.status === 'playing')
const canFav = computed(() => !!player.currentTrack)

function fmt(sec: number): string {
  if (!sec || !isFinite(sec)) return '00:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

async function onToggle() {
  await player.toggle()
}
async function onNext() {
  await player.playNext()
}
async function onPrev() {
  await player.playPrev()
}
async function onFav() {
  const r = await player.toggleFavorite()
  if (r?.favorited != null) {
    ui.pushToast(r.favorited ? 'info' : 'warn',
                 r.favorited ? `已收藏: ${r.title || player.currentTrack?.title}` :
                               `已取消收藏: ${r.title || player.currentTrack?.title}`)
  }
}
function onVolume(e: Event) {
  const v = parseFloat((e.target as HTMLInputElement).value) / 100
  player.setVolume(v)
}
</script>

<template>
  <div class="minibar">
    <!-- 左:封面 56 + 曲名艺人 -->
    <div class="left">
      <div class="cover-sm">{{ player.currentTrack?.title?.charAt(0) || '曲' }}</div>
      <div class="meta">
        <div class="title">{{ player.currentTrack?.title || '尚未选曲' }}</div>
        <div class="artist">{{ player.currentTrack?.artist || '' }}</div>
      </div>
    </div>

    <!-- 中:三件套 + 进度 + 时间 -->
    <div class="center">
      <div class="ctrls">
        <button class="ctrl" @click="onPrev" :disabled="!player.currentTrack" title="上一首">⏮</button>
        <button class="ctrl primary" @click="onToggle" :disabled="!player.currentTrack" :title="isPlaying ? '暂停' : '播放'">
          {{ isPlaying ? '⏸' : '▶' }}
        </button>
        <button class="ctrl" @click="onNext" :disabled="!player.currentTrack" title="下一首">⏭</button>
      </div>
      <div class="time-row">
        <span class="time">{{ fmt(player.currentTime) }}</span>
        <ProgressBar />
        <span class="time">{{ fmt(player.duration) }}</span>
      </div>
    </div>

    <!-- 右:♥ 收藏 + 🔊 音量 -->
    <div class="right">
      <button class="ctrl fav" :class="{ active: player.isFavorite }"
              @click="onFav" :disabled="!canFav" :title="player.isFavorite ? '已收藏' : '收藏'">
        {{ player.isFavorite ? '♥' : '♡' }}
      </button>
      <div class="vol">
        <span class="vol-icon">🔊</span>
        <input type="range" min="0" max="100" :value="player.volume * 100"
               @input="onVolume" orient="vertical" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.minibar {
  display: grid;
  grid-template-columns: 240px 1fr 200px;
  align-items: center;
  height: 100%;
  padding: 0 16px;
  gap: 16px;
}
.left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.cover-sm {
  width: 56px; height: 56px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--gh-red), var(--gh-gold));
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  color: var(--gh-paper);
  font-weight: 700;
  flex-shrink: 0;
}
.meta { min-width: 0; flex: 1; }
.title {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--gh-paper);
}
.artist {
  font-size: 11px;
  color: var(--gh-gray-light);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.center { display: flex; flex-direction: column; gap: 2px; }
.ctrls {
  display: flex;
  justify-content: center;
  gap: 18px;
  align-items: center;
}
.ctrl {
  font-size: 22px;
  color: var(--gh-paper);
  padding: 4px 8px;
}
.ctrl.primary {
  font-size: 30px;
  color: var(--gh-red-soft);
}
.time-row {
  display: grid;
  grid-template-columns: 48px 1fr 48px;
  align-items: center;
  gap: 8px;
}
.time {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--gh-gray-light);
  text-align: center;
}
.right {
  display: flex;
  align-items: center;
  gap: 14px;
  justify-content: flex-end;
}
.ctrl.fav {
  font-size: 20px;
}
.ctrl.fav.active { color: var(--gh-red-soft); }
.vol {
  display: flex;
  align-items: center;
  gap: 6px;
}
.vol-icon { font-size: 14px; color: var(--gh-gray-light); }
input[type="range"] {
  width: 80px;
  accent-color: var(--gh-red);
}
</style>