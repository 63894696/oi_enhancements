<script setup lang="ts">
// LyricProgressBar.vue — P3.1(2026-10-03)歌词窗进度条组件。
//
// 与 ProgressBar.vue 的区别:
//   - 不绑 player store(lyric store 独立)
//   - 接受 props(current/duration)+ emit(seek) 由父组件控制
//   - 高度 2px(歌词窗 360px 高度紧凑)
//   - 拖动时即时视觉,松手才发 seek(防高频 IPC)
//
// 复用 ProgressBar.vue 的三层布局 + dragging 模式范式(借鉴 Vue-mmPlayer)。
import { computed, ref } from 'vue'

const props = defineProps<{
  current: number      // 秒
  duration: number     // 秒
}>()

const emit = defineEmits<{
  seek: [offset: number]
}>()

const dragging = ref(false)
const dragPct = ref(0)  // 拖动时的临时 pct(松手才发 seek)
const bar = ref<HTMLElement | null>(null)

const pct = computed(() => {
  if (props.duration <= 0) return 0
  return Math.max(0, Math.min(1, props.current / props.duration))
})
const showPct = computed(() => dragging.value ? dragPct.value : pct.value)

function clientPct(e: MouseEvent | TouchEvent): number {
  if (!bar.value) return 0
  const rect = bar.value.getBoundingClientRect()
  const cx = 'touches' in e ? e.touches[0].clientX : e.clientX
  return Math.max(0, Math.min(1, (cx - rect.left) / rect.width))
}

function onDown(e: MouseEvent) {
  if (props.duration <= 0) return
  dragging.value = true
  dragPct.value = clientPct(e)
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  e.preventDefault()
}
function onMove(e: MouseEvent) {
  if (!dragging.value) return
  dragPct.value = clientPct(e)
}
function onUp(e: MouseEvent) {
  if (!dragging.value) return
  dragging.value = false
  const p = clientPct(e)
  emit('seek', p * props.duration)
  document.removeEventListener('mousemove', onMove)
  document.removeEventListener('mouseup', onUp)
}

function fmt(sec: number): string {
  if (!sec || !isFinite(sec)) return '00:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}
</script>

<template>
  <div class="lp-row">
    <span class="lp-time">{{ fmt(current) }}</span>
    <div class="lp-bar" ref="bar" @mousedown="onDown">
      <div class="lp-bg"></div>
      <div class="lp-active" :style="{ width: (showPct * 100) + '%' }"></div>
      <div class="lp-thumb" :style="{ left: (showPct * 100) + '%' }"
           v-show="duration > 0"></div>
    </div>
    <span class="lp-time">{{ fmt(duration) }}</span>
  </div>
</template>

<style scoped>
.lp-row {
  display: grid;
  grid-template-columns: 36px 1fr 36px;
  align-items: center;
  gap: 8px;
  height: 16px;
  -webkit-app-region: no-drag;       /* 不抢顶部 drag-bar 拖动 */
}
.lp-time {
  font-family: var(--font-mono, monospace);
  font-size: 9px;
  color: rgba(246, 241, 231, 0.55);
  text-align: center;
  -webkit-app-region: no-drag;
}
.lp-bar {
  position: relative;
  height: 2px;
  cursor: pointer;
  transition: height 0.15s;
}
.lp-bar:hover { height: 4px; }
.lp-bg {
  position: absolute; left: 0; top: 0; height: 100%;
  width: 100%;
  background: rgba(246, 241, 231, 0.18);
  border-radius: 1px;
}
.lp-active {
  position: absolute; left: 0; top: 0; height: 100%;
  background: linear-gradient(90deg, #c14d3a, #b08856);
  border-radius: 1px;
  transition: width 60ms linear;
}
.lp-thumb {
  position: absolute;
  top: 50%;
  width: 8px; height: 8px;
  border-radius: 50%;
  background: #f6f1e7;
  transform: translate(-50%, -50%);
  box-shadow: 0 1px 3px rgba(0,0,0,0.5);
  opacity: 0;
  transition: opacity 150ms;
}
.lp-bar:hover .lp-thumb { opacity: 1; }
</style>
