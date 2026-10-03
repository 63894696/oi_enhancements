<script setup lang="ts">
// LyricPanel.vue — P2.5+24(2026-10-03)
// 行级高亮 + 二分查找 + transform 居中(YesPlayMusic utils/lyric.js 移植)。
// 借鉴 YesPlayMusic 的「当前行 1.4x,上方 0.5 opacity,下方 0.5 opacity」滚动范式。
import { computed, ref } from 'vue'
import { usePlayerStore } from '@/stores/player'

const player = usePlayerStore()
const panel = ref<HTMLElement | null>(null)

// 居中滚动:当前行移至 panel 中央
const lineHeight = 36
const offset = computed(() => {
  const idx = player.currentLyricIdx
  if (idx < 0) return 0
  const panelH = panel.value?.clientHeight ?? 400
  return -(idx * lineHeight) + panelH / 2 - lineHeight / 2
})

function fmtTime(ms: number): string {
  const sec = Math.floor(ms / 1000)
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}
</script>

<template>
  <div class="lyric-panel" ref="panel">
    <div class="lyric-empty" v-if="!player.lyricLines.length">
      <p>{{ player.currentTrack ? '本曲暂无歌词' : '尚未播放曲目' }}</p>
      <p class="hint">歌词由后端 lyric_provider 自动获取</p>
    </div>
    <div class="lyric-list" v-else :style="{ transform: `translateY(${offset}px)` }">
      <div v-for="(line, i) in player.lyricLines" :key="i"
           class="lyric-line"
           :class="{
             active: i === player.currentLyricIdx,
             before: i < player.currentLyricIdx,
             after: i > player.currentLyricIdx,
           }"
      >
        <span class="t">{{ fmtTime(line.time_ms) }}</span>
        <span class="txt">{{ line.text || '·' }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lyric-panel {
  position: relative;
  height: 360px;
  overflow: hidden;
  border: 1px solid var(--gh-gray-light);
  border-radius: 8px;
  background: rgba(255,255,255,0.4);
  padding: 0 24px;
}
.lyric-list {
  transition: transform 0.4s ease;
  padding: 180px 0;
}
.lyric-line {
  display: flex;
  gap: 16px;
  align-items: baseline;
  height: 36px;
  line-height: 36px;
  font-size: 15px;
  color: var(--gh-gray);
  opacity: 0.5;
  transition: opacity 0.3s, color 0.3s, transform 0.3s, font-size 0.3s;
}
.lyric-line .t {
  font-family: var(--font-mono);
  font-size: 11px;
  min-width: 44px;
}
.lyric-line.active {
  opacity: 1;
  color: var(--gh-red);
  font-size: 17px;
  transform: scale(1.04);
}
.lyric-line.before {
  opacity: 0.4;
}
.lyric-line.after {
  opacity: 0.35;
}
.lyric-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--gh-gray);
}
.lyric-empty .hint {
  font-size: 11px;
  margin-top: 6px;
  opacity: 0.7;
}
</style>