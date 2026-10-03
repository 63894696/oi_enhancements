<script setup lang="ts">
// Toast.vue — P2.5+24(2026-10-03)
// 顶层 toast 容器,自动消失(TTL 由 ui store 管理)。
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
</script>

<template>
  <div class="toast-stack">
    <div v-for="t in ui.toasts" :key="t.id"
         class="toast" :class="t.type">
      {{ t.text }}
    </div>
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
@keyframes fadein {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>