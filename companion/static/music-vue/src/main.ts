// main.ts — P2.5+24(2026-10-03)Vue 3 + Pinia 入口
// P3.5(2026-10-04)加 hash 路由分发:
//   默认路由 /#/music → App.vue(MusicView + MiniBar)
//   独立 EQ 窗路由 /#/eq-window → EqWindowView.vue(360×420 透明 BrowserWindow 用)
//
// 没引 vue-router:简单 hash 检测 + 动态组件,符合本项目「轻量、够用」原则。
import { createApp, defineAsyncComponent } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import './styles/main.css'

function pickEntry(): string {
  const h = (typeof window !== 'undefined' ? window.location.hash : '') || ''
  if (h.startsWith('#/eq-window') || h.startsWith('#eq-window')) return 'eq'
  return 'music'
}

const Entry = pickEntry() === 'eq'
  ? defineAsyncComponent(() => import('./views/EqWindowView.vue'))
  : App

const app = createApp(Entry)
app.use(createPinia())
app.mount('#app')
