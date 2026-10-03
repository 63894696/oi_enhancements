<script setup lang="ts">
// Cover.vue — P2.5+24(2026-10-03)
// 大封面 + 旋转 + 磨砂玻璃背景(YesPlayMusic 范式)。
// 离线/无 artwork 时显示首字 + 国画主题色。
import { computed } from 'vue'
import { usePlayerStore } from '@/stores/player'

const player = usePlayerStore()
const initial = computed(() => {
  const t = player.currentTrack
  if (!t) return '曲'
  const c = (t.title || t.artist || '?').trim()
  return c.charAt(0)
})
const artistInitial = computed(() => {
  const t = player.currentTrack
  if (!t) return ''
  return (t.artist || '').charAt(0)
})

function fmt(sec: number): string {
  if (!sec || !isFinite(sec)) return '00:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}
</script>

<template>
  <div class="cover-wrap">
    <div class="cover-disc" :class="{ spinning: player.status === 'playing' }">
      <div class="cover-inner">
        <span class="cover-initial">{{ initial }}</span>
        <span class="cover-artist">{{ artistInitial }}</span>
      </div>
      <div class="cover-center"></div>
    </div>
    <div class="cover-time" v-if="player.currentTrack">
      {{ fmt(player.currentTime) }} / {{ fmt(player.duration) }}
    </div>
  </div>
</template>

<style scoped>
.cover-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 16px 0;
}
.cover-disc {
  width: 220px;
  height: 220px;
  border-radius: 50%;
  background:
    radial-gradient(circle at center, var(--gh-paper) 0%, var(--gh-paper) 24%,
                    var(--gh-ink-soft) 25%, var(--gh-ink) 100%);
  position: relative;
  box-shadow: 0 8px 28px rgba(0,0,0,0.25);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: spin 30s linear infinite;
  animation-play-state: paused;
}
.cover-disc.spinning { animation-play-state: running; }
@keyframes spin {
  from { transform: rotate(0); }
  to { transform: rotate(360deg); }
}
.cover-inner {
  width: 78%;
  height: 78%;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--gh-red), var(--gh-gold));
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  color: var(--gh-paper);
  font-family: var(--font-song);
  position: relative;
  overflow: hidden;
}
.cover-initial {
  font-size: 96px;
  font-weight: 700;
  line-height: 1;
}
.cover-artist {
  font-size: 22px;
  opacity: 0.7;
  margin-top: 6px;
}
.cover-center {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--gh-ink);
  position: absolute;
  z-index: 2;
}
.cover-time {
  margin-top: 14px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--gh-gray);
}
</style>