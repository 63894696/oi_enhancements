<script setup lang="ts">
// EqInline.vue — P3.5(2026-10-04)歌词窗 #settings-panel 末尾紧凑 EQ。
//
// 设计目标:
//   - 体积小(歌词窗很窄,典型 360-560px 宽)
//   - 6 预置 + 主开关 + 10 段 mini slider(每段 16px 宽 × 50px 高)
//   - enabled=false 时整体灰显,与 EQPanel 同 opacity 0.4 风格
//   - 不重复实现 IPC,完全复用 useEqStore(setup store 共享状态)
//
// 放在 #settings-panel 末尾(EqInline + 主开关 + preset select 同排),
// LyricOnlyView 模板挂载到 #settings-panel children 末尾。

import { onBeforeUnmount, onMounted } from 'vue'
import { useEqStore } from '@/stores/eq'
import { EQ_BANDS, EQ_PRESETS, PRESET_KEYS } from '@/services/eq'

const eq = useEqStore()

let unsub: (() => void) | null = null

onMounted(async () => {
  await eq.bootstrap()
  unsub = eq.attachBroadcast()
})

onBeforeUnmount(() => {
  if (unsub) { unsub(); unsub = null }
})

function bandLabel(hz: number): string {
  return hz >= 1000 ? `${hz / 1000}k` : `${hz}`
}

function onGain(i: number, e: Event) {
  const v = parseFloat((e.target as HTMLInputElement).value)
  void eq.setGain(i, v)
}

function onEnabled(e: Event) {
  void eq.setEnabled((e.target as HTMLInputElement).checked)
}

function onPreset(e: Event) {
  void eq.applyPreset((e.target as HTMLSelectElement).value)
}
</script>

<template>
  <div class="eq-inline">
    <div class="head">
      <label class="toggle" :title="eq.enabled ? '点击关闭 EQ' : '点击开启 EQ'">
        <input type="checkbox" :checked="eq.enabled" @change="onEnabled" />
        <span>EQ</span>
      </label>
      <select class="preset" :value="eq.preset" @change="onPreset" :disabled="!eq.enabled">
        <option v-for="k in PRESET_KEYS" :key="k" :value="k">{{ EQ_PRESETS[k].name }}</option>
        <option v-if="eq.preset === 'custom'" value="custom">自定义</option>
      </select>
    </div>
    <div class="sliders" :class="{ off: !eq.enabled }">
      <div v-for="(hz, i) in EQ_BANDS" :key="i" class="band">
        <input
          type="range"
          min="-12"
          max="12"
          step="0.5"
          :value="eq.gains[i]"
          @input="(e) => onGain(i, e)"
          class="mini"
          orient="vertical"
          :disabled="!eq.enabled"
          :title="`${bandLabel(hz)}Hz ${eq.gains[i]?.toFixed(1)}dB`"
        />
        <div class="hz">{{ bandLabel(hz) }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.eq-inline {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 0;
  -webkit-app-region: no-drag;
  user-select: none;
}
.head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
}
.toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  -webkit-app-region: no-drag;
  font-size: 11px;
}
.toggle input { accent-color: var(--gh-red, #c14d3a); cursor: pointer; }
.preset {
  font-size: 10px;
  padding: 2px 4px;
  border: 1px solid var(--gh-gold, #b08856);
  border-radius: 3px;
  background: var(--gh-paper, #faf7f1);
  color: var(--gh-ink, #2d2a26);
  cursor: pointer;
  -webkit-app-region: no-drag;
}
.sliders {
  display: flex;
  gap: 2px;
  justify-content: center;
}
.sliders.off { opacity: 0.4; }
.band {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
}
.mini {
  writing-mode: vertical-lr;
  direction: rtl;
  width: 14px;
  height: 50px;
  accent-color: var(--gh-red, #c14d3a);
  cursor: pointer;
  -webkit-app-region: no-drag;
}
.mini:disabled { cursor: not-allowed; }
.hz {
  font-size: 8px;
  color: var(--gh-gray, #8a847a);
  font-family: var(--font-mono, monospace);
}
</style>