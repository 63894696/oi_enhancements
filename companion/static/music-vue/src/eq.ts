// eq.ts — P3.5(2026-10-04)桌面 EQ 独立窗 Vue 入口。
//
// 与 main.ts / lyric.ts 同构(独立 Pinia 实例 + 独立 CSS):
//   - main.ts:MusicView + MiniBar + EQPanel(抽屉)
//   - lyric.ts:LyricOnlyView + EqInline(#settings-panel 末尾)
//   - eq.ts:EqWindowView(360×420 透明独立窗主体)
//
// hash 路由:#/eq-window(对应 prisiragent-shell main.js openEqWindow loadURL)
// 设计要点:hash 在 URL 但 vite 默认不解析,需手动跳到 #/eq-window
//         main.ts 也已支持 hash → 动态 import EqWindowView,但这里独立入口
//         直接挂载更稳。
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import EqWindowView from '@/views/EqWindowView.vue'
import './styles/main.css'

// hash 兜底:加载时若 hash 没指 /eq-window,补一下以便 main.ts / 普通模式可识别
if (typeof window !== 'undefined' && !window.location.hash.startsWith('#/eq')) {
  window.location.hash = '#/eq-window'
}

const app = createApp(EqWindowView)
app.use(createPinia())
app.mount('#app')
