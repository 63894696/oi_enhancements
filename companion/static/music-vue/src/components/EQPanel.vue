<script setup lang="ts">
// EQPanel.vue — P3.5(2026-10-04)music 10 段 EQ 主面板
// 借鉴落雪 lx-music EQ 面板布局:
//   - 顶行:主开关 + preset select + 重置按钮
//   - 主区:10 个 vertical slider(31.25Hz-16kHz 对数均布,落雪 / Spotify / 网易云 / QQ 同款)
//   - 每段 dB 显示 + 滑杆 + Hz 标签
//   - enabled=false 时整面板灰显(opacity 0.4)
//
// 状态同步路径:
//   - User 拖 → onGain → eq.setGain → 本地 + EqEngine.apply(实时音频) + IPC 持久化 + 广播
//   - 任一窗改 → 主进程广播 → 本窗 attachBroadcast 收到 → applyRemote → 反向同步 UI
import { computed, onBeforeUnmount, onMounted } from 'vue'
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

function fmtGain(g: number): string {
  const v = Number(g) || 0
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(1)}`
}

function onGain(i: number, e: Event) {
  const v = parseFloat((e.target as HTMLInputElement).value)
  void eq.setGain(i, v)
}

function onEnabled(e: Event) {
  const b = (e.target as HTMLInputElement).checked
  void eq.setEnabled(b)
}

function onPreset(e: Event) {
  const name = (e.target as HTMLSelectElement).value
  void eq.applyPreset(name)
}

function onReset() { void eq.reset() }
</script>

<template>
  <div class="eq-panel">
    <div class="eq-head">
      <label class="toggle" :title="eq.enabled ? '点击关闭 EQ' : '点击开启 EQ'">
        <input type="checkbox" :checked="eq.enabled" @change="onEnabled" />
        <span class="status">{{ eq.enabled ? '🟢 EQ 开启' : '⚫ EQ 关闭' }}</span>
      </label>
      <select class="preset" :value="eq.preset" @change="onPreset">
        <option v-for="k in PRESET_KEYS" :key="k" :value="k">{{ EQ_PRESETS[k].name }}</option>
        <option v-if="eq.preset === 'custom'" value="custom">自定义</option>
      </select>
      <button class="reset" @click="onReset" title="重置为平直(主开关保留)">🔄</button>
    </div>
    <div class="eq-sliders" :class="{ off: !eq.enabled }">
      <div v-for="(hz, i) in EQ_BANDS" :key="i" class="band">
        <div class="val" :class="{ pos: eq.gains[i] > 0, neg: eq.gains[i] < 0 }">
          {{ fmtGain(eq.gains[i]) }}
        </div>
        <input
          type="range"
          min="-12"
          max="12"
          step="0.5"
          :value="eq.gains[i]"
          @input="(e) => onGain(i, e)"
          class="vertical"
          orient="vertical"
          :disabled="!eq.enabled"
        />
        <div class="hz">{{ bandLabel(hz) }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.eq-panel {
  font-family: var(--font-mono, monospace);
  color: var(--gh-ink, #2d2a26);
  -webkit-app-region: no-drag;
  user-select: none;
}
.eq-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  gap: 8px;
}
.toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  cursor: pointer;
  -webkit-app-region: no-drag;
}
.toggle input { accent-color: var(--gh-red, #c14d3a); cursor: pointer; }
.status { color: var(--gh-ink, #2d2a26); }
.preset {
  font-size: 12px;
  padding: 3px 6px;
  border: 1px solid var(--gh-gold, #b08856);
  border-radius: 4px;
  background: var(--gh-paper, #faf7f1);
  color: var(--gh-ink, #2d2a26);
  cursor: pointer;
  -webkit-app-region: no-drag;
}
.reset {
  font-size: 14px;
  padding: 2px 8px;
  border: 1px solid var(--gh-gold, #b08856);
  border-radius: 4px;
  background: var(--gh-paper, #faf7f1);
  color: var(--gh-gold, #b08856);
  cursor: pointer;
  -webkit-app-region: no-drag;
}
.reset:hover { background: rgba(176, 136, 86, 0.1); }

.eq-sliders {
  display: flex;
  gap: 6px;
  justify-content: center;
  padding: 8px 4px;
}
.eq-sliders.off { opacity: 0.4; }
.band {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  font-size: 10px;
}
.val {
  font-size: 10px;
  color: var(--gh-gray, #8a847a);
  min-height: 12px;
  font-weight: 600;
}
.val.pos { color: var(--gh-red, #c14d3a); }
.val.neg { color: #4a7a8c; }
.hz {
  font-size: 10px;
  color: var(--gh-gray, #8a847a);
  min-height: 12px;
}
input.vertical {
  writing-mode: vertical-lr;
  direction: rtl;
  width: 22px;
  height: 160px;
  accent-color: var(--gh-red, #c14d3a);
  cursor: pointer;
  -webkit-app-region: no-drag;
}
input.vertical:disabled { cursor: not-allowed; }
</style>