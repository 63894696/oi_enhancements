<script setup lang="ts">
// QueueList.vue — P2.5+24(2026-10-03)
// 队列列表(下次播放的 N 首)。轻量版:仅 id/title/artist,cursor 高亮。
import { usePlayerStore } from '@/stores/player'

const player = usePlayerStore()
</script>

<template>
  <div class="queue-list">
    <h3 class="title">播放队列 ({{ player.playlist.length }})</h3>
    <div v-if="!player.playlist.length" class="empty">队列为空</div>
    <ol v-else>
      <li v-for="(t, i) in player.playlist" :key="t.id"
          :class="{ playing: t.id === player.currentTrack?.id }"
          @dblclick="player.playById(t.id)">
        <span class="idx">{{ i + 1 }}</span>
        <span class="name">{{ t.title }}</span>
        <span class="artist">{{ t.artist }}</span>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.queue-list {
  background: rgba(255,255,255,0.4);
  border: 1px solid var(--gh-gray-light);
  border-radius: 8px;
  padding: 12px 16px;
}
.title {
  font-size: 13px;
  color: var(--gh-gray);
  margin-bottom: 8px;
  font-weight: 600;
}
.empty {
  color: var(--gh-gray);
  font-size: 13px;
  padding: 8px 0;
}
ol {
  list-style: none;
  padding: 0;
  max-height: 240px;
  overflow-y: auto;
}
li {
  display: grid;
  grid-template-columns: 28px 1fr auto;
  gap: 12px;
  align-items: center;
  padding: 6px 0;
  cursor: pointer;
  border-bottom: 1px dashed var(--gh-gray-light);
  font-size: 13px;
}
li:hover { background: rgba(176,136,86,0.08); }
li.playing {
  color: var(--gh-red);
  font-weight: 600;
}
.idx {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--gh-gray);
}
li.playing .idx { color: var(--gh-red); }
.name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.artist {
  color: var(--gh-gray);
  font-size: 11px;
}
</style>