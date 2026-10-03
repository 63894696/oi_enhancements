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
//
// P2.5+26(2026-10-03):bootstrap 后 IPC getLyricState 拉初始态,锁态加 .lyric-locked class
// (lyric.css 改 #drag-bar { no-drag }) + 订阅 onLyricStateChanged 响应主进程 toggle 推。

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useLyricStore } from '@/stores/lyric'
import LyricProgressBar from '@/components/LyricProgressBar.vue'

const lyric = useLyricStore()

const lock = ref(false)             // 锁拖动?true = 不可拖
// P3.2(2026-10-03)视觉调档 — 透明度 0.3-1.0 + 字号缩放 0.7-1.6
const opacity = ref(0.85)
const scale = ref(1.0)
let unsubscribeState: (() => void) | null = null

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

onMounted(async () => {
  const w = window as any
  // 1) bootstrap 拉初始态(避免重启后 lock/opacity/scale 对不上 UI)
  if (typeof w?.prisIragent?.getLyricState === 'function') {
    try {
      const s = await w.prisIragent.getLyricState()
      if (s?.ok) {
        if (typeof s.lockDrag === 'boolean') lock.value = s.lockDrag
        if (typeof s.opacity === 'number') opacity.value = s.opacity
        if (typeof s.scale === 'number') scale.value = s.scale
      }
    } catch (_) {}
  }
  // 2) 订阅主进程 toggle 推过来的状态变化
  if (typeof w?.prisIragent?.onLyricStateChanged === 'function') {
    unsubscribeState = w.prisIragent.onLyricStateChanged((payload: any) => {
      if (!payload) return
      if (typeof payload.lockDrag === 'boolean') lock.value = payload.lockDrag
      if (typeof payload.opacity === 'number') opacity.value = payload.opacity
      if (typeof payload.scale === 'number') scale.value = payload.scale
    })
  }
})

onBeforeUnmount(() => {
  if (unsubscribeState) { unsubscribeState(); unsubscribeState = null }
})

function onDblClick() {
  const w = window as any
  if (typeof w?.prisIragent?.closeLyric === 'function') {
    void w.prisIragent.closeLyric()
  }
  // 普通浏览器 / dev 模式:无 IPC,静默不报错
}

// P3.1(2026-10-03)歌词进度条拖动跳转 — emit seek → store seek(走 /api/cmd)
function onSeek(offsetSec: number) {
  void lyric.seek(offsetSec)
}

// P3.2(2026-10-03)视觉调档 — 实时改 ref + 推 IPC 落盘
function onOpacityChange(e: Event) {
  const v = parseFloat((e.target as HTMLInputElement).value)
  opacity.value = v
  const w = window as any
  if (typeof w?.prisIragent?.setLyricOpacity === 'function') {
    void w.prisIragent.setLyricOpacity(v)
  }
}
function onScaleChange(e: Event) {
  const v = parseFloat((e.target as HTMLInputElement).value)
  scale.value = v
  const w = window as any
  if (typeof w?.prisIragent?.setLyricScale === 'function') {
    void w.prisIragent.setLyricScale(v)
  }
}
</script>

<template>
  <div class="lyric-only-view" :class="{ 'lyric-locked': lock }" @dblclick="onDblClick">
    <!-- 顶部 6px 拖动条 — Electron transparent 窗整窗可拖,lock 后 no-drag(lyric.css) -->
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

    <!-- P3.1(2026-10-03)进度条 — 2px 紧凑,点击/拖动跳转(emit seek → store.seek → /api/cmd) -->
    <div id="progress-zone">
      <LyricProgressBar
        :current="lyric.progress"
        :duration="lyric.duration"
        @seek="onSeek"
      />
    </div>

    <!-- P3.2(2026-10-03)视觉调档面板 — hover meta 时露出,opacity + scale 滑杆 -->
    <div id="settings-panel">
      <div class="setting-row">
        <label>透明度 <span class="val">{{ Math.round(opacity * 100) }}%</span></label>
        <input type="range" min="30" max="100" :value="Math.round(opacity * 100)"
               @input="onOpacityChange" />
      </div>
      <div class="setting-row">
        <label>字号 <span class="val">{{ Math.round(scale * 100) }}%</span></label>
        <input type="range" min="70" max="160" :value="Math.round(scale * 100)"
               @input="onScaleChange" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.lyric-only-view {
  width: 100vw;
  height: 100vh;
  position: relative;
  /* 透明背景 — 由 lyric.css 全局 body 控制 */
}
/* P3.1 进度条区 — 在 #meta 上方 24px,占底部 16px 高,不可抢拖动 */
#progress-zone {
  position: fixed;
  bottom: 22px;     /* #meta 之上 */
  left: 24px;
  right: 24px;
  z-index: 11;
  -webkit-app-region: no-drag;
}
/* P3.2 视觉调档面板 — 顶部中央,鼠标 hover 整体露出(默认半透明) */
#settings-panel {
  position: fixed;
  top: 8px;          /* #drag-bar(6px)下方 */
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 12px;
  z-index: 12;
  background: rgba(31, 27, 22, 0.55);
  border-radius: 8px;
  padding: 6px 12px;
  opacity: 0.25;
  transition: opacity 250ms;
  -webkit-app-region: no-drag;
  pointer-events: auto;       /* 即使整体半透明,滑杆仍可拖 */
}
.lyric-only-view:hover #settings-panel,
#settings-panel:hover {
  opacity: 1;
}
.setting-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  color: var(--lyrics-color, #f6f1e7);
  -webkit-app-region: no-drag;
}
.setting-row label {
  display: flex;
  flex-direction: column;
  gap: 1px;
  font-family: var(--font-mono, monospace);
  user-select: none;
}
.setting-row .val {
  font-weight: 700;
  color: #c14d3a;
}
.setting-row input[type="range"] {
  width: 70px;
  accent-color: #c14d3a;
  cursor: pointer;
}
</style>