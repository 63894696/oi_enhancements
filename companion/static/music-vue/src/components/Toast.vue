<script setup lang="ts">
// Toast.vue — P2.5+24(2026-10-03) → P3.3(2026-10-03)加 list 类型分支
// 顶层 toast 容器,自动消失(TTL 由 ui store 管理)。
// P3.3(2026-10-03):list 类型支持多行列表(收藏列表 8s TTL)
//   渲染:.list-title(粗体 + 「已收藏 (N)」) + ul.list-items(每行 一首)
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
</script>

<template>
  <div class="toast-stack">
    <template v-for="t in ui.toasts" :key="t.id">
      <!-- P3.3 多行列表 toast -->
      <div v-if="t.type === 'list'" class="toast list-toast">
        <div class="list-title">{{ t.title }} ({{ t.items.length }})</div>
        <ul class="list-items">
          <li v-for="(it, i) in t.items" :key="i">
            <span class="li-title">{{ it.title }}</span>
            <span v-if="it.subtitle" class="li-sub"> - {{ it.subtitle }}</span>
          </li>
        </ul>
      </div>
      <!-- 普通 toast -->
      <div v-else class="toast" :class="t.type">
        {{ t.text }}
      </div>
    </template>
  </div>
</template>

<style scoped>
.toast-stack {
  position: fixed;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 9999;
  pointer-events: none;
  max-width: 480px;
}
.toast {
  padding: 10px 18px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--gh-paper);
  background: var(--gh-ink);
  box-shadow: 0 4px 16px rgba(0,0,0,0.3);
  animation: fadein 0.2s ease;
}
.toast.warn { background: var(--gh-gold); }
.toast.error { background: var(--gh-red); }
.toast.info { background: var(--gh-ink); }

/* P3.3(2026-10-03)list 类型 — 收藏列表 8s TTL */
.toast.list-toast {
  color: var(--gh-ink, #2b2620);
  background: var(--gh-paper, #faf7f1);
  border: 1px solid var(--gh-gray-light, #d8d2c5);
  padding: 12px 18px;
  text-align: left;
  min-width: 240px;
  max-width: 360px;
}
.list-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--gh-red, #c14d3a);
  margin-bottom: 6px;
  font-family: var(--font-mono, monospace);
}
.list-items {
  list-style: none;
  padding: 0;
  margin: 0;
  max-height: 200px;
  overflow-y: auto;
}
.list-items li {
  font-size: 12px;
  color: var(--gh-ink, #2b2620);
  padding: 3px 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: inherit;
}
.list-items .li-sub {
  color: var(--gh-gray, #8a8275);
  font-size: 11px;
}
@keyframes fadein {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>