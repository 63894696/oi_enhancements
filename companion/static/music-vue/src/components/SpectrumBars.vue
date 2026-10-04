<script setup lang="ts">
// SpectrumBars.vue — P3.8(2026-10-04)music 16 段 spectrum bar 渲染组件。
//
// 原理:
//   - 接收 props.data: number[](16 段 0-1 归一)
//   - 每段 = vertical bar,transform-origin: bottom,height 按 data[i] 比例缩放
//   - gold/red 主题色 linear-gradient(与现有 cover/queue 视觉一致)
//   - 静音(全 0)时 height: 2px,避免完全不可见
//   - transition: height 60ms linear → 平滑无 jitter
//
// 借鉴:
//   - 落雪 lx-music-desktop:vertical bar + gold/red 渐变
//   - Spotify 桌面:对数分布 + 60fps 渲染
//   - 网易云桌面:bar 间距适中 + 圆角顶部
//
// 约束:
//   - 仅在主 music 子窗(MusicView)挂载
//   - 纯渲染组件,不持有 Web Audio / AnalyserNode / 不做 IPC / WS
import { SPECTRUM_BANDS, SPECTRUM_BAR_COUNT } from '@/composables/useSpectrum'

defineProps<{
  data: number[]
}>()

function bandLabel(hz: number): string {
  if (hz >= 1000) return `${hz / 1000}k`
  return `${hz}`
}

function barHeight(v: number): string {
  // v 0-1 → 2px-100%(静音时 2px)
  const pct = Math.max(2, Math.min(100, v * 100))
  return `${pct}%`
}
</script>

<template>
  <div class="spectrum" :title="`16 段实时频谱(EQ shaping 后)`">
    <div
      v-for="(v, i) in data"
      :key="i"
      class="bar"
      :style="{ height: barHeight(v) }"
    >
      <div class="hz-tip">{{ bandLabel(SPECTRUM_BANDS[i]) }}</div>
    </div>
  </div>
</template>

<style scoped>
.spectrum {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  height: 200px;
  padding: 4px 2px 18px 2px;
  gap: 2px;
  background: linear-gradient(180deg, rgba(250, 247, 241, 0.4), rgba(176, 136, 86, 0.05));
  border: 1px solid var(--gh-gold, #b08856);
  border-radius: 6px;
  -webkit-app-region: no-drag;
  user-select: none;
}
.bar {
  flex: 1;
  min-width: 4px;
  background: linear-gradient(to top, var(--gh-gold, #b08856) 0%, var(--gh-red, #c14d3a) 100%);
  border-radius: 2px 2px 0 0;
  transform-origin: bottom center;
  transition: height 60ms linear;
  position: relative;
  cursor: default;
}
.bar:hover { opacity: 0.85; }
.hz-tip {
  position: absolute;
  bottom: -16px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 9px;
  color: var(--gh-gray, #8a847a);
  font-family: var(--font-mono, monospace);
  white-space: nowrap;
  pointer-events: none;
}
</style>
