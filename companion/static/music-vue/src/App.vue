<script setup lang="ts">
// App.vue — P2.5+24(2026-10-03)
// 主入口,顶部 sidebar(≡ 模式切换/节点指示) + 主区(歌词+队列) + 底部 mini bar(三件套+进度+音量)。
// 三个子组件(MusicView/MiniBar/LyricPanel)在 Step 5 完善。
import { onMounted } from 'vue'
import { usePlayerStore } from '@/stores/player'
import { useUiStore } from '@/stores/ui'
import MusicView from '@/views/MusicView.vue'
import MiniBar from '@/components/MiniBar.vue'

const player = usePlayerStore()
const ui = useUiStore()

onMounted(async () => {
  await player.bootstrap()
  ui.bootstrap()
})
</script>

<template>
  <div class="music-app">
    <aside class="music-sidebar">
      <button class="sb-btn" :class="{ active: ui.viewMode === 'playlist' }"
              @click="ui.viewMode = 'playlist'" title="播放列表">≡</button>
      <button class="sb-btn" :class="{ active: ui.viewMode === 'lyric' }"
              @click="ui.viewMode = 'lyric'" title="歌词">词</button>
      <button class="sb-btn" :class="{ active: ui.viewMode === 'queue' }"
              @click="ui.viewMode = 'queue'" title="队列">⌚</button>
      <div class="sb-spacer"></div>
      <span class="sb-probe" :class="player.status" :title="player.status">{{ player.status[0].toUpperCase() }}</span>
    </aside>

    <main class="music-main">
      <MusicView />
    </main>

    <footer class="music-minibar">
      <MiniBar />
    </footer>
  </div>
</template>

<style scoped>
.sb-btn {
  width: 40px;
  height: 40px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  transition: background 0.15s;
}
.sb-btn:hover { background: rgba(255,255,255,0.08); }
.sb-btn.active { background: var(--gh-red); color: var(--gh-paper); }
.sb-spacer { flex: 1; }
.sb-probe {
  width: 32px; height: 32px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700;
  background: var(--status-idle);
  color: var(--gh-paper);
}
.sb-probe.idle { background: var(--status-idle); }
.sb-probe.loading { background: var(--status-loading); }
.sb-probe.buffering { background: var(--status-buffering); animation: pulse 1.2s ease-in-out infinite; }
.sb-probe.playing { background: var(--status-playing); }
.sb-probe.paused { background: var(--status-paused); }
.sb-probe.error { background: var(--status-error); }
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>