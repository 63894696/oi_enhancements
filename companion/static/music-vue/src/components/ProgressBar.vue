<script setup lang="ts">
// ProgressBar.vue — P2.5+24(2026-10-03)
// 借鉴 Vue-mmPlayer mm-progress.vue 的三层进度条:bg + buffered + active。
// 拖动 thumb 时进入 dragging 态,松手后 seek。
import { ref } from 'vue'
import { usePlayerStore } from '@/stores/player'

const player = usePlayerStore()
const dragging = ref(false)
const bar = ref<HTMLElement | null>(null)

function pct(e: MouseEvent | TouchEvent): number {
  if (!bar.value) return 0
  const rect = bar.value.getBoundingClientRect()
  const clientX = 'touches' in e ? e.touches[0].clientX : e.clientX
  return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
}

async function onDown(e: MouseEvent) {
  if (player.duration <= 0) return
  dragging.value = true
  const p = pct(e)
  await player.seek(p * player.duration)
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

function onMove(e: MouseEvent) {
  if (!dragging.value) return
  const p = pct(e)
  player.currentTime = p * player.duration  // 即时视觉
}

async function onUp(e: MouseEvent) {
  if (!dragging.value) return
  dragging.value = false
  const p = pct(e)
  await player.seek(p * player.duration)
  document.removeEventListener('mousemove', onMove)
  document.removeEventListener('mouseup', onUp)
}
</script>

<template>
  <div class="pb" ref="bar" @mousedown="onDown">
    <div class="bg"></div>
    <div class="buffered" :style="{ width: (player.bufferedPct * 100) + '%' }"></div>
    <div class="active" :style="{ width: (player.progress * 100) + '%' }"></div>
    <div class="thumb" :style="{ left: (player.progress * 100) + '%' }"
         v-show="player.duration > 0"></div>
  </div>
</template>

<style scoped>
.pb {
  position: relative;
  height: 4px;
  cursor: pointer;
  transition: height 0.15s;
  margin: 6px 0;
}
.pb:hover { height: 6px; }
.bg {
  position: absolute; left: 0; top: 0; height: 100%;
  width: 100%;
  background: var(--gh-gray-light);
  border-radius: 2px;
}
.buffered {
  position: absolute; left: 0; top: 0; height: 100%;
  background: rgba(176, 136, 86, 0.35);
  border-radius: 2px;
}
.active {
  position: absolute; left: 0; top: 0; height: 100%;
  background: var(--gh-red);
  border-radius: 2px;
}
.thumb {
  position: absolute;
  top: 50%;
  width: 12px; height: 12px;
  border-radius: 50%;
  background: var(--gh-red);
  transform: translate(-50%, -50%);
  box-shadow: 0 1px 4px rgba(0,0,0,0.3);
}
</style>