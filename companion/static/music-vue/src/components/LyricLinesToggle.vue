<script setup lang="ts">
// LyricLinesToggle.vue — P3.4(2026-10-03)N4 歌词窗单/双行切换
// 仿 P3.2 setting-row 滑杆风格:小尺寸 label + 2 button(active 国画红)
//
// 流程:
//   用户点 button → IPC setLyricLines(v) → 主进程 _setLyricLines 写盘 + 推 webContents
//   → LyricOnlyView 订阅 onLyricStateChanged 更新 ref lines → 根 class 双绑生效
//
// 借鉴落雪 LY 右键菜单 / YesPlayMusic Popup 的「单/双行」开关范式。
import { ref } from 'vue'

const props = defineProps<{ value: 1 | 2 }>()
const emit = defineEmits<{ change: [v: 1 | 2] }>()
const lastErr = ref<string | null>(null)

async function pick(v: 1 | 2) {
  if (v === props.value) return
  const w = window as any
  if (typeof w?.prisIragent?.setLyricLines !== 'function') {
    // dev 模式 / 无 IPC(浏览器直接打开):优雅降级,本地更新但不持久化
    emit('change', v)
    return
  }
  try {
    const r = await w.prisIragent.setLyricLines(v)
    if (r?.ok) {
      emit('change', r.lines ?? v)
      lastErr.value = null
    } else {
      lastErr.value = r?.err || 'setLyricLines failed'
    }
  } catch (e) {
    lastErr.value = (e as Error)?.message || String(e)
  }
}
</script>

<template>
  <div class="lines-toggle">
    <label>显示</label>
    <div class="seg">
      <button class="seg-btn" :class="{ active: value === 1 }"
              @click="pick(1)" title="仅显示当前行">☝ 单行</button>
      <button class="seg-btn" :class="{ active: value === 2 }"
              @click="pick(2)" title="当前行 + 下一行预览">☟ 双行</button>
    </div>
    <span v-if="lastErr" class="err">{{ lastErr }}</span>
  </div>
</template>

<style scoped>
.lines-toggle {
  display: flex;
  flex-direction: column;
  gap: 1px;
  font-size: 10px;
  color: var(--lyrics-color, #f6f1e7);
  font-family: var(--font-mono, monospace);
  -webkit-app-region: no-drag;
}
.lines-toggle label {
  user-select: none;
}
.seg {
  display: flex;
  gap: 2px;
}
.seg-btn {
  background: transparent;
  color: var(--lyrics-color, #f6f1e7);
  border: 1px solid rgba(193, 77, 58, 0.4);
  border-radius: 3px;
  padding: 1px 6px;
  font-size: 10px;
  cursor: pointer;
  font-family: var(--font-mono, monospace);
  transition: background 140ms ease, color 140ms ease;
}
.seg-btn.active {
  background: #c14d3a;
  color: #faf7f1;
  border-color: #c14d3a;
}
.seg-btn:hover:not(.active) {
  background: rgba(193, 77, 58, 0.18);
}
.err {
  font-size: 9px;
  color: #c14d3a;
  margin-top: 1px;
}
</style>
