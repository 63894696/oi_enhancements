<script setup lang="ts">
// RecommendPanel.vue — N9(2026-10-04)music AI 歌单推荐面板。
//
// 原理:
//   - 接收 props.items: Array<{id,title,artist,tag,score,reason}> + props.loading
//   - 顶栏:「✨ AI 推荐 · N 首」+ 冷启动徽章(若全部 reason 以「冷启动」开头)+ 🔄 刷新按钮
//   - 单行:rank · title · artist · tag · reason · ▶ 播放按钮
//   - click row → emit('play', song);click 🔄 → emit('refresh')
//   - 空态:「暂无推荐(收藏几首歌后会变得更精准)」或 loading…
//
// 借鉴:
//   - Spotify Discover Weekly:list + 行内 ▶ + refresh
//   - 网易云「每日推荐」:徽章 + 列表
//   - 落雪 lx-music-desktop:行内 reason 文案
//
// 约束:
//   - 仅在主 music 子窗(MusicView)挂载
//   - 纯渲染组件,无 IPC / WS / fetch(由 MusicView 调 /api/recommend 后传 props 进来)
defineProps<{
  items: Array<{ id: string; title: string; artist: string; tag: string; score: number; reason: string }>
  loading: boolean
}>()

const emit = defineEmits<{
  play: [item: { id: string; title: string; artist: string; tag: string }]
  refresh: []
}>()

function isColdStart(items: Array<{ reason: string }>): boolean {
  if (!items.length) return false
  // 全部 reason 以「冷启动」开头 → 冷启动徽章
  return items.every((it) => (it.reason || '').startsWith('冷启动'))
}
</script>

<template>
  <div class="recommend">
    <div class="head">
      <span class="title">
        ✨ AI 推荐
        <span class="count" v-if="items.length">· {{ items.length }} 首</span>
        <span class="badge-cold" v-if="isColdStart(items)">冷启动</span>
      </span>
      <button class="refresh" @click="emit('refresh')" :disabled="loading"
              :title="loading ? '加载中…' : '换一批推荐'">
        🔄 刷新
      </button>
    </div>
    <div class="body">
      <div v-if="loading" class="empty">加载推荐中…</div>
      <div v-else-if="!items.length" class="empty">
        暂无推荐(收藏几首歌后会变得更精准)
      </div>
      <div v-else class="rec-list">
        <div v-for="(it, i) in items" :key="it.id" class="rec-row"
             @click="emit('play', it)" :title="`点击播放: ${it.title} - ${it.artist}`">
          <span class="rank">{{ i + 1 }}</span>
          <span class="title-cell">{{ it.title }}</span>
          <span class="artist">{{ it.artist }}</span>
          <span class="tag-sm">{{ it.tag || '?' }}</span>
          <span class="reason">{{ it.reason }}</span>
          <button class="play-btn" @click.stop="emit('play', it)"
                  :title="`播放 ${it.title}`">▶</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.recommend {
  border: 1px solid var(--gh-gold, #b08856);
  border-radius: 6px;
  background: rgba(250, 247, 241, 0.6);
  padding: 8px 12px;
  -webkit-app-region: no-drag;
  user-select: none;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  gap: 8px;
}
.title {
  font-size: 13px;
  font-weight: 600;
  color: var(--gh-ink, #2d2a26);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.count {
  font-size: 11px;
  color: var(--gh-gray, #8a847a);
  font-weight: 400;
}
.badge-cold {
  font-size: 10px;
  padding: 1px 6px;
  background: var(--gh-gold, #b08856);
  color: var(--gh-paper, #faf7f1);
  border-radius: 8px;
  font-weight: 500;
}
.refresh {
  font-size: 12px;
  padding: 3px 8px;
  border: 1px solid var(--gh-gold, #b08856);
  border-radius: 4px;
  background: var(--gh-paper, #faf7f1);
  color: var(--gh-gold, #b08856);
  cursor: pointer;
  -webkit-app-region: no-drag;
}
.refresh:hover:not(:disabled) {
  background: rgba(176, 136, 86, 0.15);
}
.refresh:disabled { opacity: 0.5; cursor: not-allowed; }

.body { max-height: 240px; overflow-y: auto; }
.empty {
  font-size: 12px;
  color: var(--gh-gray, #8a847a);
  text-align: center;
  padding: 16px 0;
}
.rec-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.rec-row {
  display: grid;
  grid-template-columns: 24px 1fr 90px 70px 1fr 28px;
  gap: 8px;
  align-items: center;
  padding: 4px 6px;
  border-radius: 3px;
  cursor: pointer;
  font-size: 12px;
  -webkit-app-region: no-drag;
}
.rec-row:hover { background: rgba(176, 136, 86, 0.12); }
.rank {
  color: var(--gh-gray, #8a847a);
  font-family: var(--font-mono, monospace);
  text-align: right;
}
.title-cell {
  color: var(--gh-ink, #2d2a26);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.artist {
  color: var(--gh-gray-soft, #6b645a);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tag-sm {
  font-size: 10px;
  color: var(--gh-gold, #b08856);
  background: rgba(176, 136, 86, 0.1);
  padding: 1px 5px;
  border-radius: 3px;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.reason {
  color: var(--gh-gray, #8a847a);
  font-size: 11px;
  font-style: italic;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.play-btn {
  font-size: 11px;
  padding: 1px 6px;
  background: var(--gh-red, #c14d3a);
  color: white;
  border: none;
  border-radius: 3px;
  cursor: pointer;
  -webkit-app-region: no-drag;
}
.play-btn:hover { opacity: 0.85; }
</style>
