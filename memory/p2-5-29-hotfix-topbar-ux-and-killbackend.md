# P2.5+29 hotfix — killBackend 漏 music-web + 顶栏搜索入口歧义 (2026-10-05)

## 用户原始反馈(2 项)

1. **Bug #1 — 托盘退出后 music-web 残留**
   > 「请检查系统是否有 PrisirAI 进程?如果有就杀掉,重新拉起 PrisirAI」
   > 「请起 Electron 主壳。这有个问题,我在发消息之前已经从系统托盘右键退出过 PrisirAI,为什么你检查时还有后台进程没退干净,不应该在退出的时候相关的进程都终止吗?」

2. **Bug #2 — 顶栏 🔍 按钮跟 P3.7 输入框混淆**
   > 「我点搜索的时候并没看到什么弹层,然后输入内容发现原来是在随机的 60 首歌名中找歌,而不是从网络获取」

## 根因分析

### Bug #1:`killBackend()` PowerShell 正则漏掉 music-web

**原始正则**:`'prisIragent_web|PrisirAI\.exe.*--port'`

只匹配下划线连接的 `prisIragent_web.py`,**漏掉**连字符的 `prisiragent-music-web.py`(music 后端的实际文件名)。

`prisIragent-music-web.py --port 18803` 这个进程在托盘右键退出后没被 sweep,导致 18803 端口残留 + 后端进程依旧在。

**修复**:`'prisIragent[_-]?(web|music)|PrisirAI\.exe.*--port'` — 字符类 `[_-]?` 同时覆盖下划线和连字符,`(web|music)` 覆盖两个 web 模块。

### Bug #2:顶栏两个搜索入口视觉距离不够**

**P2.5+29(2026-10-05 ship)**:顶栏左侧加 🔍 按钮 → 弹 SearchModal(5 源并行在线搜歌)。

**P3.7(2026-10-04 ship)**:顶栏右侧放 `<input type="search">` → 仅过滤 CSV(840 首本地)。

两个搜索入口并排靠左对齐,中间没任何视觉分隔。用户点了右边的 `<input>`,以为是 P2.5+29 的 modal,实际只过滤了 CSV(840 首歌中的匹配项)。

**用户原话**:
> 「输入内容发现原来是在随机的 60 首歌名中找歌,而不是从网络获取」

— 误把 P3.7 当 P2.5+29。

**修复**:
1. 🔍 按钮加金边 + 加 padding + 加大字号 + hover 高亮 — 让它从一堆灰色 chip 中脱颖而出
2. 加 `<span class="topbar-sep">` 视觉分隔线 — 两个搜索入口之间 1px×18px 的金灰色分隔
3. P3.7 输入框 placeholder 加 `本地过滤(仅 840 首)` 明确范围 — 一眼看出「这只是本地的」
4. 按钮文字从 `🔍 搜歌` → `🔍 在线搜歌` — 直接说「在线」

## ship 改动(1 commit)

### 1. `prisiragent-shell/main.js` killBackend 正则

```diff
- "($_.CommandLine -match 'prisIragent_web|PrisirAI\\.exe.*--port') } | " +
+ "($_.CommandLine -match 'prisIragent[_-]?(web|music)|PrisirAI\\.exe.*--port') } | " +
```

+ 注释写明历史 bug 在 2026-10-05 修了 + 漏的是 music-web 模块

### 2. `companion/static/music-vue/src/views/MusicView.vue` 顶栏 UX

+ `.btn-search` CSS — 金边 + 加 padding + hover 反色 + disabled 灰
+ `.topbar-sep` 分隔线样式
+ `.search-input` 宽度 160px → 180px(稍微加大,配合加长 placeholder 文字)
+ 模板:`🔍 搜歌` → `🔍 在线搜歌`;placeholder `搜歌名/歌手` → `本地过滤(仅 840 首)`;加 `<span class="topbar-sep">`
+ 注释更新:N9.1 fix 段写在 P2.5+29 注释下面

## 验证

### 1. 单元 + 集成测试(全栈 music 回归)

```bash
python -m pytest tests/test_music_multi_source.py \
       tests/test_music_favorite_menu.py tests/test_music_lyric_*.py \
       tests/test_music_vue_build.py tests/test_song_pool_and_favorite.py \
       tests/test_eq_p35.py tests/test_chip_search_p37.py \
       tests/test_download_toast_p36.py tests/test_toast_p310a.py \
       tests/test_spectrum_p38.py tests/test_recommend_n9.py \
       tests/test_music_search_endpoint.py -q
# 期望 517+ passed (跟 P2.5+29 ship 前一致)
```

### 2. vite build

```bash
cd companion/static/music-vue && npm run build
# vue-tsc --noEmit 0 error + vite build 1.07s 0 error
# dist/assets/main-9um_IvC4.js(新 bundle 哈希,旧的是 main-DG8eaXag.js)
```

### 3. E2E puppeteer 端到端

+ 打开 music 子窗 http://127.0.0.1:18803
+ `getComputedStyle(.btn-search).borderColor` = `rgb(176, 136, 86)` (gh-gold 金色)
+ 按钮 textContent = `🔍 在线搜歌`
+ 输入框 placeholder = `本地过滤(仅 840 首)`
+ 存在 `<span class="topbar-sep">`
+ 点 🔍 按钮 → modal `display !== 'none'`(已 puppeteer 验证 modalMask=true)

### 4. killBackend 实测

+ 启动 Electron 主壳(4 进程:1 主 + 3 子)
+ 托盘右键退出
+ PowerShell `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'prisIragent' }`
  - 修复前:只剩 prisiragent_web 死了,music-web 还在(进程命令 `...python.exe companion/prisIragent-music-web.py --port 18803`)
  - 修复后:prisIragent_web + prisiragent-music-web 全死
+ 端口探活:`curl -s http://127.0.0.1:18803/health` → connection refused(干净)

## 与既有 ship 的关系

```
2026-10-04  P3.7 chip 多选 + search input(本地 CSV 搜)
2026-10-05  P2.5+29 search endpoint + modal + 5 源并行 fallback (commit 77cc886)
2026-10-05  P2.5+29 hotfix(本 ship):
              - killBackend 正则漏 music-web(托盘退出残留)
              - 顶栏搜索入口歧义(用户误点 P3.7 以为是 P2.5+29)
```

## 关键决策记录

1. **不把 P3.7 输入框改成只弹 modal** — P3.7 是「本地 CSV 过滤」(840 首),P2.5+29 是「在线搜」(全网)。两种 UX 各有价值,只调视觉距离。
2. **金边 + 「在线」字样 + 占位加「本地」** — 双重 cue 让用户一眼分清两个入口。
3. **killBackend 正则字符类 `[_-]?`** — 兼容下划线和连字符两种文件名范式(主 web 用下划线,music-web 用连字符)。
4. **不在 main.js 加 `app.on('quit')` 强杀** — 已 ship 的 `before-quit` + `will-quit` 路径够用,killBackend 是兜底 sweep;只改正则即可,不改整体结构。

## Why / How to apply

**Why**:PrisirAI 主壳的进程清理逻辑不能漏模块。顶栏有多个相似入口时要视觉/文字双重 cue,避免用户混淆。

**How to apply**:
- 任何 PowerShell CIM 过滤正则加新模块时,先用字符类 `[_-]?` 而非字面量,兼容下划线/连字符
- 任何顶栏加新按钮都要跟既有类似按钮做视觉距离 + 文字双重 cue(颜色 + 文字说明)
- 任何 ship 后的 UX 修复都先 commit,再写 ship memory,最后更新 MEMORY.md 索引(三步走,别跳)

## 给用户的下一步

+ 重启 PrisirAI Electron 主壳(`pkill prisiragent; ./node_modules/.bin/electron .`)
+ 顶栏「🔍 在线搜歌」按钮一眼能跟「本地过滤(仅 840 首)」输入框分清
+ 点 🔍 → 弹 SearchModal → 搜「孤勇者」/「周杰伦 晴天」 → 应该看到 3+ 条结果
+ 点结果行 → modal 关 + audio 播 mp3
+ 重启后再开 modal → 历史 query chips 可见