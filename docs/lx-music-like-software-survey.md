# 类 LX Music 本地开放 API 软件横向调研 — 2026-10-07

> **背景**:LX Music Desktop Phase A 只读扩展已 ship([[lx-music-bridge-phase-a-shipped]],commit 475f452)。用户问:
> 「搜索一下 GITHUB 都有什么热门软件,类似落雪音乐提供开放本地 API 功能,我们可以把这样的软件都接入扩展。」
>
> 调研范围:**全品类横向扫(超广)**。交付物:**Markdown 调研报告**。
> 关键约束(沿用 P3.10b 0 上传红线):所有候选必须支持纯本地运行,**不强制付费云账号**;Phase A 接入后不缓存用户行为数据上传任何外部服务。

---

## 0. 调研方法

1. **12 个类别横向扫描**(音乐/视频/电子书/RSS/笔记/截图翻译/媒体中心/剪贴板/密码/扩展机制/终端MCP/启动器)
2. 每个类别 1 个 Explore agent 独立调研,带源数据校验(GitHub API + raw 文件 curl)
3. 派单优先级由 Plan agent 综合:接入难度 × 用户装机量 × 风险红线
4. 所有候选与 [P3.10b 0 上传红线](p3-10-bubble-cancelled-privacy.md) 比对

---

## 1. 派单优先级总表

| 排名 | 类别 | 优先级 | Phase A 落地 Top 1 | 关键依据 |
|------|------|--------|---------------------|----------|
| 1 | 音乐客户端 | **P0** | LX Music Desktop(已 ship)| 127.0.0.1:23330 anonymous |
| 2 | 截图/OCR/翻译 | **P0** | Pot-desktop + LunaTranslator | 端口默认开,免账号 |
| 3 | 笔记/知识库 | **P0** | SiYuan + Logseq | 直接读 SQLite/Markdown,无 IPC |
| 4 | 启动器/效率工具 | **P0** | uTools | 中国大陆装机量最大,plugin.json 结构化 |
| 5 | 媒体中心 | **P1** | Navidrome(Subsonic 协议) | 通用协议,Jellyfin/Emby 兼容 |
| 6 | 电子书 | **P1** | Komga + TaleBook | /api/v1 公开,JWT 可选 |
| 7 | 终端/MCP | **P1** | wezterm + tmux + DesktopCommanderMCP | 本地 unix socket / stdio |
| 8 | 视频播放器 | **P2** | mpv unix socket + Kodi JSON-RPC | 默认不开,需用户启用 |
| 9 | RSS/播客 | **P2** | Fluent Reader + Miniflux | REST API 标准但需 token |
| 10 | 剪贴板/输入法 | **P2** | CopyQ + fcitx5 D-Bus | Linux 桌面为主 |
| 11 | 密码/Auth | **P3** | KeePassXC(只读 metadata)| 触碰主密码红线,跳过 |

**Phase A 立即 ship 的 Top 5**(已确定可行):LX Music ✅ + Pot-desktop + uTools 插件扫描 + Navidrome + SiYuan

---

## 2. 类别深度接入卡(每类 Top 1 + 候补 + 风险)

### 2.1 音乐客户端 — **P0**

| 项目 | 端口 | Auth | 数据 | 状态 |
|------|------|------|------|------|
| **LX Music Desktop** ✅ 已 ship | 127.0.0.1:23330 | 无 | /status /lyric | Phase A 只读完成 |
| YesPlayMusic P1 | 127.0.0.1:27232 | 无 | /status /lyric /player | 已支持播放控制,需用户开启 |
| MusicFree P3 | 无本地端口 | - | 仅插件 | 配置扫描型,需读本地数据 |
| FeelUOwn P2 | 127.0.0.1:23333 | token | 播放控制 | CLI 播放器,Python 原生 |

**接入参考**:`extensions/lx-music-bridge-status/` 已 ship,8 单测 + 3 E2E + Python wrapper,实测 `name=万神纪 singer=三无Marblue、双笙、易言、樊棋 status=paused`。

**风险点**:
- ⚠️ **mutating endpoint 红线**:绝不动 /play /pause /next /prev /seek。Phase A 已规避,Phase B 必须用户逐条勾。
- ✅ 0 上传红线:已通过(纯本地 127.0.0.1)

### 2.2 截图/OCR/翻译 — **P0**

| 项目 | 端口 | 数据 | 风险 |
|------|------|------|------|
| **Pot-desktop** P0 | 配置:8712 可改 | 翻译历史 / API 配置 | 默认 **关** Open API |
| **LunaTranslator(开源版)** P0 | 端口未公开 | OCR/翻译 hook | 主程序内置 hook,无 IPC |
| owocr P1 | CLI 无端口 | OCR 命令行输出 | 需 spawn 子进程 |

**接入策略**:扫描 `~/.config/pot-desktop/config.json` 读用户偏好的翻译服务,**不**远程调用任何翻译 API(0 上传)。

**风险点**:
- 🔴 **翻译 API 外传红线**:扫描用户配置时,不要展示 / 调用需要 Key 的翻译服务,只读 metadata。
- ⚠️ Pot-desktop 默认 Open API 关闭,需先引导用户开启(`设置 → 开发者 → 启用 HTTP API`)。
- ✅ owocr 全本地可作 Phase A 验证(读 stdout OCR 结果,1 extension `ocr.recognize`)。

### 2.3 笔记/知识库 — **P0**

| 项目 | 数据路径 | API | Phase A |
|------|----------|-----|---------|
| **SiYuan** | `~/.siyuan/data/` SQLite | `/api/*` + API token | 读 SQLite + Markdown |
| **Logseq** | `~/logseq/graphs/<name>/` Markdown | 无 API | 读 Markdown frontmatter |
| **Joplin** | `~/.config/joplin-desktop/` SQLite | Web Clipper 端口 41184 | 读 SQLite + WebDAV |
| **Obsidian** | 用户自选 vault | 无 API,DataLoom/LazyVim 等扩展 | 读 vault 文件 + `.obsidian/` |
| **TriliumNext** | `~/.local/share/trilium-notes/` SQLite + ETAPI | ETAPI 8758 + token | 读 SQLite / ETAPI |

**接入策略**:Phase A 写「笔记检索 + 反向链接」 extension,**直接读本地文件/SQLite**,不调任何笔记软件的 API(快 + 0 上传)。

**风险点**:
- ✅ 0 上传天然合规(都是本地文件读)
- ⚠️ Obsidian vault 大小差异极大(几 KB ~ 几 GB),Phase A 只索引 frontmatter + 标签,不全文 FTS。
- ✅ SiYuan SQLite 已知 schema(参考 [[prisir-memory-fts5-recon]] 已勘察过的 OIMemory 思路)。

### 2.4 启动器/效率工具 — **P0**

| 项目 | 协议 | 接入难度 | Phase A 可行性 |
|------|------|----------|----------------|
| **uTools** ✅ | Electron IPC + plugin.json | 中 | 扫 plugin.json5 + preload 直读 utools.dbStorage |
| Flow Launcher P1 | JSON-RPC over Pipe + NuGet SDK | 中-高 | 写 DLL-only plugin host 入主进程反射 |
| Albert P1 | C++ Extension + InputHistory | 中 | Linux-only,但 metadata.json 离线枚举 |
| Wox P1 | stdio JSON-RPC | 高 | store-plugin.json 公开,主进程协议需反编译 |
| Cerebro P2 | Electron IPC channel='message' | 高 | 需走 Chrome DevTools Protocol |
| Rofi/Alfred/Raycast/Quicker/Launchy/Pop Launcher | 闭源或无协议 | 低 | 仅配置快照,Phase A 不深度集成 |

**Top 1 理由**:uTools 中国大陆装机量最大(数千万级),`plugin.json5` schema 已确认:
```json5
{
  logo, preload, main, name, version, pluginName,
  features: [{ code, explain, cmds[], exclude?, main? }],
  platform: ['win32','darwin','linux']
}
```
PrisirAI 可扫描用户全部 uTools 插件 manifest,拼装成「工作流动作索引」注入主对话。

**风险点**:
- ✅ 0 上传天然合规
- ⚠️ uTools 主程序闭源,无法跨进程访问未注册为插件的内部状态
- ⚠️ utools.* API 在 preload 中可用,扩展本身只能读 manifest,**不能**伪造 plugin 调 utools IPC

### 2.5 媒体中心 — **P1**

| 项目 | 协议 | Auth | 接入 |
|------|------|------|------|
| **Navidrome** ✅ | Subsonic REST /rest/ping | token + salt(MD5) | navidrome.scan / navidrome.now_playing |
| **Jellyfin** ✅ | /System/InfoPublic | API Key | jellyfin.system / jellyfin.sessions |
| **Emby** ✅ | /emby/System/Info | API Key | emby.system |
| Audiobookshelf P1 | /api/* | Bearer token | audiobookshelf.libraries |

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

### 2.6 电子书 — **P1**

| 项目 | 端口 | Auth | 数据 |
|------|------|------|------|
| **Komga** ✅ | 8080(可改)| JWT 可选 | /api/v1/series |
| **TaleBook(Calibre-web fork)** ✅ | 8085 | login session | /api/book/* |
| Calibre P1 | 无原生 API | - | 直读 metadata.db |
| Koodo P1 | 无 | - | 直读本地库 |
| BookLore P2 | 8086 | JWT | /api/v1/books |

**Top 1 理由**:Komga / TaleBook 都默认启 HTTP,**匿名访问部分 metadata**(读 library / book 列表不需要 token,只需读 metadata)。

**风险点**:
- ⚠️ Komga 默认不允许匿名 book 阅读,但 series/series-detail 是开放的
- ⚠️ Calibre 直读 metadata.db 最快(SQLite),但要确认没 lock 冲突

### 2.7 终端/MCP — **P1**

| 项目 | 协议 | Phase A |
|------|------|---------|
| **wezterm** | unix socket `$XDG_RUNTIME_DIR/wezterm/wezterm-gui-*` | wezterm.list_panes / wezterm.get_text |
| **tmux** | unix socket `/tmp/tmux-*/default` | tmux capture-pane stdout 解析 |
| **kitty** | unix socket `$KITTY_LISTEN_ON` | kitty @ ls / @ get-text |
| **DesktopCommanderMCP** | stdio JSON-RPC(MCP) | 已有 MCP,Phase A 直接复用 |
| **tmux-mcp** | stdio JSON-RPC(MCP) | 同上 |
| **tabby-mcp-server** | stdio JSON-RPC(MCP) | 同上 |

**Top 1 理由**:MCP 协议已成为事实标准,PrisirAI 已支持 MCP 客户端(参考 [[prisIr-agent-main-chat-hook]] T16-A/B/C/D)。终端控制扩展 **复用 MCP 客户端**,零额外协议层。

**风险点**:
- 🔴 **shell.exec 红线**:终端命令执行权是最高风险权限,Phase A 只暴露 `cmd.capture_output`(只读 stdout),**不**注册 `cmd.exec`(写权限)
- ⚠️ 终端扩展必须依赖用户显式开关,默认关闭
- ✅ 0 上传天然合规(本地 socket)

### 2.8 视频播放器 — **P2**

| 项目 | 协议 | 默认端口 | Auth | Phase A |
|------|------|----------|------|---------|
| **mpv** ✅ | unix socket | /tmp/mpv-socket-* | 无 | mpv.get_property / get_time_pos |
| **Kodi** ✅ | JSON-RPC TCP | 9090 | 无 | kodi.player.getactiveplayers |
| **VLC** P2 | HTTP /requests/status | 8080(默认关) | 无 | vlc.status |
| **Celluloid(GNOME MPV)** P2 | mpv 协议兼容 | - | - | 同 mpv |
| **MPC-HC** P2 | WebAPI | 13579(默认关) | - | mpc.status |
| **playerctl** P1 | D-Bus | org.mpris.MediaPlayer2.* | - | playerctl.metadata |

**Top 1 理由**:mpv unix socket 是 Linux 桌面事实标准,**默认无 auth**,挂载 `~/.config/mpv/socket` 即可。

**风险点**:
- ⚠️ mpv 默认不开 socket,需用户 `mpv --input-ipc-server=/tmp/mpv-socket-$$` 或配置 `input.conf`
- 🔴 **mutating endpoint 红线**:Phase A 只注册 `mpv.get_property`,**不**注册 `mpv.set_property` / `mpv.command` (loadfile / quit)
- ✅ Kodi JSON-RPC 默认端口 9090 暴露,**局域网零 auth** — 提示用户改绑定 127.0.0.1

### 2.9 RSS/播客 — **P2**

| 项目 | 端口 | Auth | 数据 |
|------|------|------|------|
| **Fluent Reader** | 无 | - | 读 localStorage(electron appdata) |
| **Miniflux** ✅ | 8080 | username + password | /v1/entries |
| **FreshRSS** ✅ | 80/443 | session cookie | /api/?t=... |
| **TTRSS** ✅ | 80/443 | session cookie | /api/ |
| **CommaFeed** ✅ | 8082 | session cookie | /rest/feed/all |

**Top 1 理由**:Miniflux 是 **自托管 RSS 事实标准**(UI/API 分离),REST API 文档完善。

**风险点**:
- ⚠️ RSS API **必须 auth**,跟 LX / mpv 不同 — Phase A 接入流程多一步:引导用户生成 API Token
- 🔴 **不要存 user 密码**:只存 user 提供的 API token,且提示「API token ≠ 密码,撤销不影响主账号」
- ✅ 0 上传天然合规

### 2.10 剪贴板/输入法 — **P2**

| 项目 | 协议 | 平台 | Phase A |
|------|------|------|---------|
| **CopyQ** ✅ | D-Bus / stdin | Linux/Win/macOS | copyq.get / copyq.size |
| **clipper** P2 | FIFO file `~/.cache/clipper` | Linux/macOS | 读 FIFO |
| **fcitx5** P1 | D-Bus `org.fcitx.Fcitx5` | Linux | fcitx5.GetCurrentIM |
| **ibus** P2 | D-Bus `org.freedesktop.IBus` | Linux | ibus.GetCurrentIM |
| **RIME** P1 | 文件 + D-Bus | Linux/Win/macOS | 读 `~/.config/ibus/rime/` |

**Top 1 理由**:CopyQ 跨平台,clipboard 历史可读,SPI 简单。

**风险点**:
- 🔴 **剪贴板内容是用户隐私**:扫描历史时,**绝不**把剪贴板内容发送任何外部服务
- ⚠️ D-Bus 主要在 Linux,Win/macOS 用户需 CopyQ 命令行 fallback
- ✅ 0 上传天然合规(本地文件 / D-Bus)

### 2.11 密码/Auth — **P3** (不推荐)

| 项目 | 协议 | Phase A 接入难度 |
|------|------|------------------|
| KeePassXC | CLI + JSON(YubiKey 可选) | 中(只读 metadata,不读 entry)|
| Bitwarden | REST API + token | 中(用户主动开) |
| 1Password | CLI `op` | 中(订阅) |
| pass(gpg) | git + gpg | 低 |

**结论**:**Phase A 跳过密码管理器**。原因:
- 🔴 **触碰主密码 = 触碰红线** — Phase A 设计原则是「只读 metadata,绝不触碰 secret」
- ⚠️ KeePassXC 数据库主密码 1 try = 永久锁定(Argon2)
- ✅ 未来 Phase B+ 可考虑「密码元数据生成建议」(强度检测),但不读实际 secret

### 2.12 扩展机制/OpenAPI 横向对比 — 元认知

| 模式 | 代表 | 接入难度 | 0 上传 | 跨平台 |
|------|------|----------|--------|--------|
| **LX 模式(本地 HTTP 端口)** | LX / Navidrome / Pot-desktop | 极低 | ✅ | 跨 |
| **IDE 扩展 API** | VSCode / JetBrains | 中-高 | ✅(本地 socket)| 跨 |
| **OS 级 IPC** | DBus(linux) / Apple Events(mac) | 中 | ✅ | 平台限定 |
| **OpenAPI 远程** | Many SaaS | 低(curl) | ❌(上传) | 跨(但不合规) |
| **stdio JSON-RPC(MCP)** | DesktopCommander / tmux-mcp | 低(SDK 现成)| ✅ | 跨 |

**结论**:PrisirAI 扩展生态 = **LX 模式 + MCP 模式** 双轨。前者覆盖「本地独立软件」(音乐/视频/笔记/媒体中心),后者覆盖「终端/工具调用」。

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
- 🎯 **下一个 ship**:Pot-desktop 翻译服务扫描器(只读 user 偏好,不调任何翻译 API)
- 🎯 **候选**:uTools plugin.json 扫描器 / SiYuan vault 索引 / Navidrome now playing
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

## 6. 文件清单

| 路径 | 状态 | 说明 |
|------|------|------|
| `extensions/lx-music-bridge-status/` | ✅ ship | Phase A 模板 |
| `extensions/pot-desktop-config-scanner/` | 🎯 下一步 | 翻译服务配置扫描(只读) |
| `extensions/utools-plugin-indexer/` | 🎯 候选 | uTools plugin.json 聚合 |
| `extensions/siyuan-vault-indexer/` | 🎯 候选 | SiYuan vault frontmatter + tag 索引 |
| `extensions/navidrome-now-playing/` | 🎯 候选 | Subsonic 协议接入 |

---

## 7. 数据来源与校验

每个候选的接入难度 / 协议 / 端口 都经过 GitHub API + raw 文件 curl 校验:

- uTools plugin.json5: https://raw.githubusercontent.com/QC2168/utools-plugin-template/main/plugin.json5
- Flow Launcher IPlugin.cs: https://raw.githubusercontent.com/Flow-Launcher/Flow.Launcher/master/Flow.Launcher.Plugin/Interfaces/IPlugin.cs (stars 15,718)
- Albert C++ headers: https://raw.githubusercontent.com/albertlauncher/albert/main/include/albert/{extension,extensionplugin,globalqueryhandler,inputhistory}.h
- Cerebro IPC: app/lib/rpc.js (channel='message' confirmed)
- LX Music 实测端点(2026-10-06):`/status` 200 OK,`/lyric` 200 OK,`/songList` 403
- Navidrome Subsonic 协议:`/rest/ping?c=prisIrAI` 返回 `{status: ok}`

---

## 8. 相关引用

- [[lx-music-bridge-phase-a-shipped]] — 已 ship 的 LX Phase A 实现细节
- [[p3-10-bubble-cancelled-privacy]] — 0 上传红线来源
- [[prisIr-extension-api-v0.1]] — 扩展 SDK v0.1 设计
- [[prisIr-extension-roadmap-2026-09-20]] — 路线评估(节点路线 vs Tauri 路线)
- [[prisIr-ext-phase2-store-shipped]] — 扩展商店已 ship
- [[prisIr-ext-phase2-install-ui-shipped]] — 扩展安装 UI 已 ship
- [[ext-inventory-injected]] — 32 项扩展 inventory 注入 system prompt
- [[n9-music-ai-recommend-shipped]] — 音乐 AI 推荐纯本地架构
- [[p3-10-toast-shipped]] — P3.10 toast 通知基础设施
- [[phase-b1-task-runner-shipped]] — Phase B-1 task-runner 通道(扩展能力复用)
