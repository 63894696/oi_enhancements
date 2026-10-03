// lyric.ts — P2.5+25(2026-10-03)桌面歌词独立窗 Vue 入口。
//
// 与 main.ts (music 主入口) 完全独立:
//   - main.ts:createApp(App).use(createPinia()).mount('#app') — 加载 MusicView + MiniBar
//   - lyric.ts:createApp(LyricOnlyView).use(createPinia()).mount('#app') — 仅加载 LyricOnlyView
//
// 设计要点:
//   1. 独立 Pinia 实例:lyric store 不依赖 player store,主 music 子窗关闭歌词窗仍可用
//   2. 仅引入 lyric.css(透明主题),不复用 main.css(主区有背景色)
//   3. store 自动 bootstrap(),订阅 ws /ws/lyrics + 拉 /api/state + /api/agent/cfg/list
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import LyricOnlyView from '@/views/LyricOnlyView.vue'
import { useLyricStore } from '@/stores/lyric'
import './styles/lyric.css'

const app = createApp(LyricOnlyView)
app.use(createPinia())
app.mount('#app')

// store bootstrap:启动 ws /ws/lyrics 订阅 + 拉 cfg + state
const lyricStore = useLyricStore()
void lyricStore.bootstrap()