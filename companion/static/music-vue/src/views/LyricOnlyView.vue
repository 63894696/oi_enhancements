<script setup lang="ts">
// LyricOnlyView.vue — P2.5+25(2026-10-03)桌面歌词独立窗主视图。
//
// 设计:
//   - 整窗 100vh × 100vw,body background: transparent(lyric.css 已设)
//   - 顶部 6px 拖动条 -webkit-app-region: drag(Electron transparent 窗可拖动)
//   - 主区:#lyrics-stage 居中渲染歌词行,active 行渐变高亮 + scale 1.06
//   - 双击歌词 → 调用 window.prisIragent.closeLyric() IPC 通知主进程关闭窗
//   - 进度条底部 1px(走 lyric store progressPct → CSS var --progress)
//   - 鼠标 hover 露出底部 meta(曲名 + 艺人 + 连接状态)
//
// 借鉴:
//   - YesPlayMusic 行级高亮 + transform 居中
//   - 旧 lyrics.html/css/js 的拖动条 + 透明 + 进度条设计

import { computed, ref, watch } from 'vue'
import { useLyricStore } from '@/stores/lyric'

const lyric = useLyricStore()

const connTag = computed(() => {
  if (lyric.connected) return '🟢'
  return '⌛'
})

// 监听 lines 变化,scrollIntoView 居中 active 行(借鉴 YesPlayMusic)
const stageRef = ref<HTMLElement | null>(null)
watch(() => lyric.currentIdx, () => {
  const stage = stageRef.value
  if (!stage) return
  // 用 setTimeout 让 DOM 更新完再 scroll
  setTimeout(() => {
    const actEl = stage.querySelector('.line.active')
    if (actEl) {
      actEl.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, 50)
})

// 监听 progress 触发 store 重算 currentIdx
watch(() => lyric.progress, () => {
  lyric.onProgressChange()
})

function onDblClick() {
  const w = window as any
  if (typeof w?.prisIragent?.closeLyric === 'function') {
    void w.prisIragent.closeLyric()
  }
  // 普通浏览器 / dev 模式:无 IPC,静默不报错
}
</script>

<template>
  <div class="lyric-only-view" @dblclick="onDblClick">
    <!-- 顶部 6px 拖动条 — Electron transparent 窗整窗可拖 -->
    <div id="drag-bar"></div>

    <!-- 主歌词区 -->
    <main id="lyrics-stage" ref="stageRef">
      <template v-if="lyric.lines.length > 0">
        <div
          v-for="(line, i) in lyric.lines"
          :key="i"
          class="line"
          :class="{
              active: i === lyric.currentIdx,
              prev: i === lyric.currentIdx - 1,
              next: i === lyric.currentIdx + 1,
            }"
        >
          {{ line.text }}
        </div>
      </template>
      <div v-else class="empty">(等待播放...)</div>
    </main>

    <!-- 底部元数据条 -->
    <footer id="meta">
      <span id="track-title">{{ lyric.track?.title || '—' }}</span>
      <span id="track-artist">{{ lyric.track?.artist || '—' }}</span>
      <span id="conn-tag">{{ connTag }}</span>
    </footer>
  </div>
</template>

<style scoped>
.lyric-only-view {
  width: 100vw;
  height: 100vh;
  position: relative;
  /* 透明背景 — 由 lyric.css 全局 body 控制 */
}
</style>