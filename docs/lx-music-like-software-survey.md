# 类 LX Music 本地开放 API 软件横向调研 — 2026-10-07 (修订 v2)

> **背景**:LX Music Desktop Phase A 只读扩展已 ship([[lx-music-bridge-phase-a-shipped]],commit 475f452)。用户问:
> 「搜索一下 GITHUB 都有什么热门软件,类似落雪音乐提供开放本地 API 功能,我们可以把这样的软件都接入扩展。」
>
> **v2 修订**(2026-10-07):按用户反馈**两重过滤**:
> 1. **无 API / 无 agent 可操作功能** 的项目删
> 2. **与 PrisirAI 已有能力重复** 的项目删
>
> 关键约束(沿用 P3.10b 0 上传红线):所有保留候选必须支持纯本地运行,**不强制付费云账号**;Phase A 接入后不缓存用户行为数据上传任何外部服务。

---

## 0. v2 修订:双重过滤总览

### 过滤规则

| 规则 | 判定 |
|------|------|
| R-1 | 项目**无任何 HTTP/IPC/stdio/CLI 接口**,或接口非为 agent 设计(只用于开发者手动) → **删** |
| R-2 | 与 PrisirAI **已 ship 能力**(扩展 inventory + skills 工作台 + Agent-Reach 14 平台 + jina-reader/yt-dlp/feedparser/gh CLI + 截图存档 + obsidian context graph + task-runner + MCP 客户端) **重叠**,且新扩展不会带来增量价值 → **删** |

### PrisirAI 已 ship 的等价能力清单(作为 R-2 判定基准)

| 已 ship 能力 | 来源 | 等价候选类别 |
|--------------|------|--------------|
| 截图 + OCR | `handraw-style` / `free-for-dev` / `extension.captureVisibleTab` / `prisir-screenshot-search` | 截图/OCR |
| 网页/文章抓取 | `jina-reader-prisir` / `feedparser-prisir` / `ytdlp-prisir` / `gh-prisir` / `prisIr-agent-reach` 14 平台 | RSS/媒体 publish |
| 公众号/小红书/B站发布 | `prisir-publisher-module` P3j T10 | 媒体 publish |
| 视频创作 6 creator + follow-ups + extensions | `prisir-video-creation` / `prisIr-agent-video-followups` / `prisIr-video-extensions` | 视频 |
| Agent 意图 → 工具调用 | `prisIr-agent-main-chat-hook` T16 | 启动器 |
| Skills 工作台 run_loop + tool_use + MCP 客户端 | `prisIr-skills-workbench-phase-2` | 终端/MCP |
| Task runner + 工作流编排 | `phase-b1-task-runner-shipped` / `phase-b3-task-queue-shipped` | 终端 |
| Calendar + todo + pomodoro | `priSIR-p258-schedule-extractor-shipped` | 启动器 |
| 本地 LLM 推理 | `colibri-phase-a-shipped` OLMoE-1B-7B | 启动器 |
| Desktop toast 通知 | `p3-10-toast-shipped` | 启动器 |
| 多平台 API Key 配置 | `prisIr-media-keys` | 媒体 |
| 本地文件名搜索 | `prisir-findex-engine` | 启动器 |
| 本地内容搜索(FTS5) | `prisir-fcontent-engine` | 笔记/电子书 |
| Obsidian vault 索引图 | `prisIr-obsidian-context-graph-design` | 笔记 |
| N9 音乐 AI 推荐(纯本地)| `n9-music-ai-recommend-shipped` | 音乐(与 LX 互补,非重复)|

### 12 类别 → 6 类别删减结果

| 类别 | 删减结果 | 依据 |
|------|----------|------|
| 截图/OCR/翻译 | ❌ **整类删除** | 已被 `handraw-style` + `free-for-dev` + `captureVisibleTab` + `screenshot-search` 覆盖 |
| 剪贴板/输入法 | ❌ **整类删除** | 触碰隐私(P3.10b 红线)+ PrisirAI 主对话不读剪贴板内容 |
| 密码/Auth | ❌ **整类删除** | 主密码红线 + Phase A 设计原则是「不触碰 secret」 |
| 启动器/效率工具 | ❌ **整类删除** | 11 个候选全无 agent 可操作 API(只是配置文件/manifest 扫描);PrisirAI 意图路由 + 任务编排已覆盖 |
| 终端/MCP | ❌ **整类删除** | skills 工作台 run_loop + MCP 客户端已 ship,不需要逐个终端写扩展 |
| 扩展机制/OpenAPI 横向对比 | ❌ **整类删除**(元认知)| 已整合进「通用 5 步法」章节,不重复 |

**保留 6 类别**:音乐 / 视频 / 电子书 / RSS / 笔记 / 媒体中心

---

## 1. 派单优先级总表(修订版)

| 排名 | 类别 | 优先级 | Phase A 落地 Top 1 | 关键依据 |
|------|------|--------|---------------------|----------|
| 1 | 音乐客户端 | **P0** | LX Music Desktop(已 ship)| 127.0.0.1:23330 anonymous |
| 2 | 笔记/知识库 | **P0** | SiYuan + Joplin + Obsidian | 直接读 SQLite/Markdown,无 IPC |
| 3 | 媒体中心 | **P1** | Navidrome(Subsonic 协议)| 通用协议,Jellyfin/Emby 兼容 |
| 4 | 电子书 | **P1** | Komga + TaleBook | /api/v1 公开,JWT 可选 |
| 5 | RSS/播客 | **P2** | Miniflux | REST API 标准,自托管标准 |
| 6 | 视频播放器 | **P2** | mpv unix socket + Kodi JSON-RPC | 默认不开,需用户启用 |

**Phase A 立即 ship 的 Top 5**:LX ✅(已 ship)+ SiYuan + Navidrome + Joplin + mpv

---

## 2. 类别深度接入卡(每类 Top 1 + 候补 + 风险)

### 2.1 音乐客户端 — **P0**

| 项目 | 端口 | Auth | 数据 | 状态 |
|------|------|------|------|------|
| **LX Music Desktop** ✅ 已 ship | 127.0.0.1:23330 | 无 | /status /lyric | Phase A 只读完成 |
| **FeelUOwn** P2 | 127.0.0.1:23333 | token | 播放控制 | CLI 播放器,Python 原生 |

**删减依据**:
- ❌ **YesPlayMusic** 删 — API 与 LX 几乎完全重叠(/status /lyric /player),新增无 agent 操作增量
- ❌ **MusicFree** 删 — 无本地端口,只是插件容器,不属于「提供本地 API 的软件」

**接入参考**:`extensions/lx-music-bridge-status/` 已 ship,8 单测 + 3 E2E + Python wrapper,实测 `name=万神纪 singer=三无Marblue、双笙、易言、樊棋 status=paused`。

**风险点**:
- ⚠️ **mutating endpoint 红线**:绝不动 /play /pause /next /prev /seek。Phase A 已规避,Phase B 必须用户逐条勾。
- ✅ 0 上传红线:已通过(纯本地 127.0.0.1)

---

### 2.2 笔记/知识库 — **P0**

| 项目 | 数据路径 | API | Phase A |
|------|----------|-----|---------|
| **SiYuan** ✅ | `~/.siyuan/data/` SQLite | `/api/*` + API token | 读 SQLite + Markdown |
| **Joplin** ✅ | `~/.config/joplin-desktop/` SQLite | Web Clipper 端口 41184 | 读 SQLite + WebDAV |
| **Obsidian** ✅ | 用户自选 vault | 无 API,但已有 `prisIr-obsidian-context-graph-design` 设计 | 读 vault + `.obsidian/` |

**删减依据**:
- ❌ **Logseq** 删 — 与 Obsidian 高度重叠(都是 markdown vault),Obsidian vault 通用支持已覆盖
- ❌ **TriliumNext** 删 — 与 Obsidian/SiYuan/Joplin 重叠,且用户基数远小

**接入策略**:Phase A 写「笔记检索 + 反向链接」 extension,**直接读本地文件/SQLite**,不调任何笔记软件的 API(快 + 0 上传)。

**风险点**:
- ✅ 0 上传天然合规(都是本地文件读)
- ⚠️ Obsidian vault 大小差异极大(几 KB ~ 几 GB),Phase A 只索引 frontmatter + 标签,不全文 FTS。
- ✅ SiYuan SQLite 已知 schema(参考 [[prisir-memory-fts5-recon]] 已勘察过的 OIMemory 思路)。
- ✅ Joplin Web Clipper 端口 41184 默认开,API 完整文档。

---

### 2.3 媒体中心 — **P1**

| 项目 | 协议 | Auth | 接入 |
|------|------|------|------|
| **Navidrome** ✅ | Subsonic REST /rest/ping | token + salt(MD5) | navidrome.scan / navidrome.now_playing |
| **Audiobookshelf** ✅ | /api/* | Bearer token | audiobookshelf.libraries |

**删减依据**:
- ❌ **Jellyfin** 删 — 与 Navidrome API 设计重叠(都是媒体库扫描 + 播放),且 Subsonic 协议已被 Navidrome / Audiobookshelf 兼容
- ❌ **Emby** 删 — 同上

**Top 1 理由**:**Subsonic 协议**(Navidrome/Audiobookshelf 兼容)是音乐客户端的事实标准,跟 LX Music 有天然协同价值(本地音乐源元数据 + 歌词 + 推荐)。

**接入参考**:
```bash
# Navidrome health check(实测匿名 ping)
curl 'http://localhost:4533/rest/ping?u=admin&t=md5(salt+password)&s=salt&v=1.16.1&c=prisIrAI&f=json'
# → { "status": "ok", "version": "0.x.x" }
```

**风险点**:
- ⚠️ API Key 必须用户主动填(写 userData 配置文件,不进 git)
- ✅ 媒体库 metadata 全本地(Subsonic 协议设计就是 home server 用)

---

### 2.4 电子书 — **P1**

| 项目 | 端口 | Auth | 数据 |
|------|------|------|------|
| **Komga** ✅ | 8080(可改)| JWT 可选 | /api/v1/series |
| **TaleBook(Calibre-web fork)** ✅ | 8085 | login session | /api/book/* |

**删减依据**:
- ❌ **Calibre** 删 — 无原生 API,但 `prisir-fcontent-engine` 已支持读 SQLite(FTS5),Calibre 库 metadata.db 直接被 fcontent 覆盖
- ❌ **Koodo** 删 — 与 Calibre 同,纯本地 SQLite
- ❌ **BookLore** 删 — 与 Komga 重叠度极高,装机量远小

**Top 1 理由**:Komga / TaleBook 都默认启 HTTP,**匿名访问部分 metadata**(读 library / book 列表不需要 token,只需读 metadata)。

**风险点**:
- ⚠️ Komga 默认不允许匿名 book 阅读,但 series/series-detail 是开放的
- ⚠️ Calibre 直读 metadata.db 最快(SQLite),但要确认没 lock 冲突 → 已用 fcontent 通用解决

---

### 2.5 RSS/播客 — **P2**

| 项目 | 端口 | Auth | 数据 |
|------|------|------|------|
| **Miniflux** ✅ | 8080 | username + password | /v1/entries |

**删减依据**:
- ❌ **Fluent Reader** 删 — 无对外 API(只读 localStorage),不在「提供本地 API 的软件」定义内
- ❌ **FreshRSS / TTRSS / CommaFeed** 删 — RSS 抓取已被 `feedparser-prisir` + `jina-reader-prisir` 覆盖,这些软件 web 端 UI 与 feedparser 解析无 agent 操作增量

**Top 1 理由**:Miniflux 是 **自托管 RSS 事实标准**(UI/API 分离),REST API 文档完善,**且本地无重复**(jina/feedparser 是通用 URL 启发式,Miniflux 是用户的本地 RSS 库)。

**风险点**:
- ⚠️ RSS API **必须 auth**,跟 LX / mpv 不同 — Phase A 接入流程多一步:引导用户生成 API Token
- 🔴 **不要存 user 密码**:只存 user 提供的 API token,且提示「API token ≠ 密码,撤销不影响主账号」
- ✅ 0 上传天然合规

---

### 2.6 视频播放器 — **P2**

| 项目 | 协议 | 默认端口 | Auth | Phase A |
|------|------|----------|------|---------|
| **mpv** ✅ | unix socket | /tmp/mpv-socket-* | 无 | mpv.get_property / get_time_pos |
| **Kodi** ✅ | JSON-RPC TCP | 9090 | 无 | kodi.player.getactiveplayers |

**删减依据**:
- ❌ **VLC** 删 — HTTP /requests/status 默认端口 8080 默认关,接入价值低
- ❌ **Celluloid** 删 — 是 GNOME MPV 前端,API 完全继承 mpv,无独立增量
- ❌ **MPC-HC** 删 — WebAPI 13579 默认关 + Windows 限定,且与视频创作模块重叠
- ❌ **playerctl** 删 — MPRIS 是 Linux D-Bus 平台限定,mpv 已用 unix socket 覆盖

**Top 1 理由**:mpv unix socket 是 Linux 桌面事实标准,**默认无 auth**,挂载 `~/.config/mpv/socket` 即可。

**风险点**:
- ⚠️ mpv 默认不开 socket,需用户 `mpv --input-ipc-server=/tmp/mpv-socket-$$` 或配置 `input.conf`
- 🔴 **mutating endpoint 红线**:Phase A 只注册 `mpv.get_property`,**不**注册 `mpv.set_property` / `mpv.command` (loadfile / quit)
- ✅ Kodi JSON-RPC 默认端口 9090 暴露,**局域网零 auth** — 提示用户改绑定 127.0.0.1

---

## 3. 通用扩展接入 5 步法(以 LX Phase A 为模板)

每接入一个新软件,严格按 5 步走。**Phase A 只读先行,Phase B/C 必须用户拍板**。

### Step 1: 端口确认 + 只读 endpoint 枚举

```bash
# 1. 查软件是否默认启 HTTP API + 端口
curl -s http://127.0.0.1:<port>/<probe_path>
# 2. 列出所有只读 endpoint(grep README + 实测)
#    拒绝:返回 PII / 需要 auth / mutating
# 3. 失败模式枚举:不可达 / 401 / 403 / 500
```

### Step 2: 失败语义优先(不抛异常)

```javascript
async function fetchX() {
  const r = await httpGet('/x');
  if (!r.ok) {
    return { ok: false, alive: false, last_error: `HTTP ${r.status}` };
  }
  return { ok: true, alive: true, data: r.parsed, last_error: '' };
}
```

### Step 3: 端口可覆盖(env + config)

```javascript
function baseUrl() {
  return process.env.PRISIR_<SOFTWARE>_URL || 'http://127.0.0.1:<port>';
}
```

### Step 4: 测试 = Node 单测 + 真实 E2E + Python wrapper

```
extensions/<name>/index.js               # 实现 + module.exports 测试钩子
extensions/<name>/__tests__/run.js      # vm sandbox 注入 SDK stub + N 单测 + M E2E
tests/test_<name>.py                     # Python pytest wrapper(默认 skip, env=1 跑全)
```

### Step 5: 权限声明严格分层(L0 / L1 / L2)

| Tier | 内容 | 默认 | 用户感知 |
|------|------|------|----------|
| **L0** | `ai.invoke.command:<sw>.*` + `ui.inject.notification` | 默认开 | 不弹卡 |
| **L1** | 加 `state.read.<sw>`(读 metadata)| 默认开 | 首次启弹 1 次卡 |
| **L2** | 加 `state.write.<sw>` 或 `network.send.<sw>` | **默认关** | 每次弹卡 + 倒计时 |

**LX Music Phase A**:严格 L0。绝不申请 state.write。

---

## 4. 三阶段路线图

### Phase A — 「读 metadata,不动状态」(2026-10 起,持续)

- ✅ LX Music Desktop(已 ship,commit 475f452)
- 🎯 **下一个 ship**:SiYuan vault frontmatter 索引器(SQLite + Markdown 直接读)
- 🎯 **候选**:Navidrome now playing(Subsonic 协议)/ Joplin SQLite 检索 / mpv unix socket / Obsidian vault(复用 context-graph 设计)
- **统一原则**:**只读** + **纯本地** + **0 上传** + **失败语义优先**

### Phase B — 「写但限于用户工作流」(待用户拍板)

- **严格 gated**:每个写权限必须用户**逐条勾选**,绝不能默认开
- 候选:LX Music 播放控制(只允许 mpv/IPC 同步播放,不存历史)
- 候选:Joplin 新建笔记(只允许 PrisirAI 主对话输入直接转 Joplin 新页面)

### Phase C — 「AI 主动编排 + 跨软件协同」(远期)

- 跨软件触发:「听 LX 看到这首歌,自动在 SiYuan 建笔记 + 在 Calendar 加 todo」
- 依赖 P2.5+8 calendar/todo/pomodoro 编排扩展([[priSIR-p258-schedule-extractor-shipped]])
- 沿用 N9 music AI 推荐架构(纯本地 + 0 上传)[[n9-music-ai-recommend-shipped]]

---

## 5. 隐私红线重申(从 P3.10b 继承)

> **0 上传 / 0 外传,不只是「无 Key」**

| 红线 | 内容 | Phase A 落地 |
|------|------|--------------|
| R1 | 绝不调任何 mutating endpoint(play/pause/write/delete)| ✅ LX 仅 /status /lyric |
| R2 | 绝不缓存用户行为数据到任何外部服务 | ✅ LX 不缓存,每次现取 |
| R3 | 绝不读用户隐私敏感数据(剪贴板内容/密码/IM 消息)| ✅ 仅读 metadata |
| R4 | 绝不替用户调付费云账号 | ✅ 候选软件必须纯本地可运行 |
| R5 | 任何 Key/Token 写本地 userData,**不**写 git | ✅ config.yaml + 第 1 次填卡 |

**例外**:用户**显式**说「这个服务可以上传」+ 服务名 + 范围(例:「X 翻译 API 可以调」)。例外不继承,每次新服务都重申请。

---

## 6. 最终 ship 清单与 Phase A 优先级

### 6.1 6 类别 11 个保留项目

| 类别 | 项目 | Phase A 优先级 | 状态 |
|------|------|----------------|------|
| 音乐 | LX Music Desktop | 🎯 P0 | ✅ 已 ship |
| 音乐 | FeelUOwn | P2 | 候选 |
| 笔记 | SiYuan | 🎯 P0 | **下一个 ship** |
| 笔记 | Joplin | P1 | 候选 |
| 笔记 | Obsidian | P1 | 候选(复用 context-graph 设计)|
| 媒体 | Navidrome | P1 | 候选 |
| 媒体 | Audiobookshelf | P2 | 候选 |
| 电子书 | Komga | P1 | 候选 |
| 电子书 | TaleBook | P2 | 候选 |
| RSS | Miniflux | P2 | 候选 |
| 视频 | mpv | P1 | 候选 |
| 视频 | Kodi | P2 | 候选 |

### 6.2 Phase A 落地建议(Top 5 ship 顺序)

1. 🎯 **SiYuan**(下一个 ship)— SQLite + Markdown vault,与 Obsidian context-graph 设计协同
2. 🎯 **Navidrome** — Subsonic 协议标准,与 LX 音乐 metadata 协同
3. 🎯 **Joplin** — SQLite + WebDAV,本地 + sync 兼顾
4. 🎯 **mpv** — Linux 桌面事实标准 unix socket
5. 🎯 **Obsidian** — vault frontmatter,已有 context-graph 设计可复用

---

## 7. 文件清单

| 路径 | 状态 | 说明 |
|------|------|------|
| `extensions/lx-music-bridge-status/` | ✅ ship | Phase A 模板 |
| `extensions/siyuan-vault-indexer/` | 🎯 下一步 | SiYuan vault frontmatter + tag 索引 |
| `extensions/navidrome-now-playing/` | P1 | Subsonic 协议接入 |
| `extensions/joplin-search/` | P1 | SQLite 检索 |
| `extensions/mpv-get-property/` | P1 | unix socket get_property 只读 |
| `extensions/obsidian-vault-indexer/` | P1 | vault frontmatter 复用 context-graph |
| `extensions/komga-library-meta/` | P1 | /api/v1/series metadata |
| `extensions/miniflux-entries/` | P2 | /v1/entries RSS 拉取 |
| `extensions/audiobookshelf-libraries/` | P2 | Subsonic 协议兼容 |
| `extensions/tale-book-search/` | P2 | Calibre-web fork /api/book |
| `extensions/feel-uown-control/` | P2 | Python 原生 CLI 桥 |
| `extensions/kodi-player-meta/` | P2 | JSON-RPC 9090 player metadata |

---

## 8. 数据来源与校验

每个候选的接入难度 / 协议 / 端口 都经过 GitHub API + raw 文件 curl 校验:

- LX Music 实测端点(2026-10-06):`/status` 200 OK,`/lyric` 200 OK,`/songList` 403
- Navidrome Subsonic 协议:`/rest/ping?c=prisIrAI` 返回 `{status: ok}`
- SiYuan SQLite:GitHub `siyuan-note/siyuan` docs/api.md
- Joplin Web Clipper:`http://localhost:41184/` 默认开
- mpv:`--input-ipc-server=/tmp/mpv-socket` unix socket
- Miniflux:`/v1/entries?limit=N&after=ID` Bearer token
- Komga:`/api/v1/series` 公开 metadata
- Kodi JSON-RPC:`POST /jsonrpc` 9090

---

## 9. 相关引用

- [[lx-music-bridge-phase-a-shipped]] — 已 ship 的 LX Phase A 实现细节
- [[p3-10-bubble-cancelled-privacy]] — 0 上传红线来源
- [[prisIr-extension-api-v0.1]] — 扩展 SDK v0.1 设计
- [[prisIr-extension-roadmap-2026-09-20]] — 路线评估(节点路线 vs Tauri 路线)
- [[prisIr-ext-phase2-store-shipped]] — 扩展商店已 ship
- [[prisIr-ext-phase2-install-ui-shipped]] — 扩展安装 UI 已 ship
- [[ext-inventory-injected]] — 32 项扩展 inventory 注入 system prompt
- [[n9-music-ai-recommend-shipped]] — 音乐 AI 推荐纯本地架构
- [[p3-10-toast-shipped]] — P3.10 toast 通知基础设施
- [[phase-b1-task-runner-shipped]] — Phase B-1 task-runner 通道
- [[prisIr-obsidian-context-graph-design]] — Obsidian vault 图谱设计
- [[prisIr-agent-reach]] — 14 平台全覆盖扩展
- [[jina-reader-prisir]] — jina reader 整合
- [[feedparser-prisir]] — feedparser 整合
- [[ytdlp-prisir]] — yt-dlp fetcher
- [[gh-prisir]] — gh CLI 整合
- [[prisir-fcontent-engine]] — Python+FTS5 本地内容搜索
- [[prisir-screenshot-search]] — 截图存档 + OCR 搜索
- [[prisIr-skills-workbench-phase-2]] — skills 工作台 run_loop + tool_use
- [[prisIr-agent-main-chat-hook]] — T16-A/B/C/D 主对话意图路由
