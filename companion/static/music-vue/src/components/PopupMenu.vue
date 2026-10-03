<script setup lang="ts">
// PopupMenu.vue — P3.3(2026-10-03)N7 长按收藏菜单通用 popup。
//
// 设计:
//   - props: x/y 屏幕坐标 + items: MenuItem[] + visible
//   - MenuItem: { key, label, icon?, danger?, hidden? }
//   - emits: select(item) / close
//   - click-outside (document mousedown) → close
//   - ESC 键 → close
//   - 不抢外部 click:@mousedown.stop 防 click-outside 立刻触发
//   - 借鉴 YesPlayMusic Popup + 落雪 LY 右键菜单范式
//   - clamp 到 viewport 内(popupX = min(x, innerWidth - 180))
//
// 用法(在 MiniBar.vue):
//   <PopupMenu :x="popupX" :y="popupY" :visible="popupVisible"
//              :items="itemsForTrack()"
//              @select="onSelectMenu" @close="popupVisible = false" />
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'

export interface MenuItem {
  key: string
  label: string
  icon?: string
  danger?: boolean
  hidden?: boolean
}

const props = defineProps<{
  x: number
  y: number
  items: MenuItem[]
  visible: boolean
}>()

const emit = defineEmits<{
  (e: 'select', item: MenuItem): void
  (e: 'close'): void
}>()

const visibleItems = computed(() =>
  (props.items || []).filter((i) => !i.hidden),
)

// 视口 clamp:菜单宽 ~180,高 ~32*N + 8;防止越界
const popupStyle = computed(() => {
  const MENU_W = 200
  const MENU_H_PER = 32
  const H_PAD = 8
  const px = Math.max(8, Math.min(props.x, window.innerWidth - MENU_W))
  const maxY = window.innerHeight - (visibleItems.value.length * MENU_H_PER + H_PAD)
  const py = Math.max(8, Math.min(props.y, Math.max(8, maxY)))
  return { left: px + 'px', top: py + 'px' }
})

function onDocMouseDown(ev: MouseEvent) {
  if (!props.visible) return
  // 如果 click 在 popup 内(popup @mousedown.stop)不会到这里;
  // 这里仅处理 popup 外部的 click → close
  emit('close')
}

function onDocKeyDown(ev: KeyboardEvent) {
  if (!props.visible) return
  if (ev.key === 'Escape') emit('close')
}

function onPick(item: MenuItem) {
  emit('select', item)
}

onMounted(() => {
  document.addEventListener('mousedown', onDocMouseDown)
  document.addEventListener('keydown', onDocKeyDown)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocMouseDown)
  document.removeEventListener('keydown', onDocKeyDown)
})

// visible 切换时若有滚动 → 关(避免 popup 漂移)
watch(() => props.visible, (v) => {
  if (v) {
    window.addEventListener('scroll', () => emit('close'), { once: true, capture: true })
  }
})
</script>

<template>
  <div v-if="visible && visibleItems.length > 0"
       class="popup-menu"
       :style="popupStyle"
       @mousedown.stop
       role="menu">
    <button v-for="item in visibleItems"
            :key="item.key"
            class="menu-item"
            :class="{ danger: item.danger }"
            @click="onPick(item)"
            role="menuitem"
            :data-key="item.key">
      <span class="icon">{{ item.icon || '' }}</span>
      <span class="label">{{ item.label }}</span>
    </button>
  </div>
</template>

<style scoped>
.popup-menu {
  position: fixed;
  z-index: 999;
  background: var(--gh-paper, #faf7f1);
  border: 1px solid var(--gh-gray-light, #d8d2c5);
  border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
  padding: 4px 0;
  min-width: 160px;
  max-width: 240px;
  user-select: none;
}
.menu-item {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
  padding: 8px 12px;
  border: 0;
  background: transparent;
  cursor: pointer;
  font-size: 13px;
  text-align: left;
  color: var(--gh-ink, #2b2620);
  font-family: inherit;
}
.menu-item:hover {
  background: rgba(193, 77, 58, 0.08);
}
.menu-item.danger {
  color: #c14d3a;
}
.menu-item.danger:hover {
  background: rgba(193, 77, 58, 0.15);
}
.menu-item .icon {
  width: 18px;
  flex-shrink: 0;
  font-size: 14px;
  text-align: center;
}
.menu-item .label {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>