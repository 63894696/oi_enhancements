<script setup lang="ts">
// EqWindowView.vue — P3.5(2026-10-04)独立 EQ BrowserWindow 视图。
// 360×420 透明窗,从托盘「🎚 桌面 EQ → 打开 EQ 窗口」弹出。
//
// 完全复用 EQPanel(主面板已含 bootstrap + attachBroadcast + 全部交互),
// 仅额外加一个「关闭」按钮(独立窗没有 native 标题栏)。

import { onBeforeUnmount, onMounted } from 'vue'
import EQPanel from '@/components/EQPanel.vue'

const w = window as any
function closeWindow() {
  // 独立 BrowserWindow 自身可关(Electron 默认允许同源 window.close)
  if (typeof window !== 'undefined') {
    window.close()
  }
}

let unsub: (() => void) | null = null
onMounted(() => {
  // 让独立窗在用户点击 EQ 顶部时不会触发 -webkit-app-region: drag(否则点不到 close)
  document.documentElement.classList.add('eq-window-root')
})
onBeforeUnmount(() => {
  if (unsub) { unsub(); unsub = null }
})
</script>

<template>
  <div class="eq-window-root">
    <div class="titlebar">
      <span class="title">🎚 桌面 EQ</span>
      <button class="close" @click="closeWindow" title="关闭">✕</button>
    </div>
    <EQPanel />
  </div>
</template>

<style scoped>
.eq-window-root {
  width: 100%;
  height: 100%;
  background: rgba(250, 247, 241, 0.92);
  border: 1px solid var(--gh-gold, #b08856);
  border-radius: 8px;
  padding: 10px;
  font-family: var(--font-mono, monospace);
  -webkit-app-region: drag;
  display: flex;
  flex-direction: column;
  gap: 6px;
  box-sizing: border-box;
}
.titlebar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--gh-gold, #b08856);
}
.title {
  font-size: 13px;
  font-weight: 600;
  color: var(--gh-ink, #2d2a26);
}
.close {
  font-size: 12px;
  padding: 2px 8px;
  border: 1px solid var(--gh-red, #c14d3a);
  border-radius: 4px;
  background: transparent;
  color: var(--gh-red, #c14d3a);
  cursor: pointer;
  -webkit-app-region: no-drag;
}
.close:hover { background: var(--gh-red, #c14d3a); color: #fff; }
:deep(.eq-panel) { -webkit-app-region: no-drag; }
</style>