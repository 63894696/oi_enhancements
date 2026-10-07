# 被删 LX-like 软件对比研究 — PrisirAI 优化借鉴清单 (2026-10-07)

> **背景**:v2 调研([[lx-music-like-software-survey]])按 R-1(无 API)+ R-2(与已有能力重复)删了 6 整类 + 13 项目共 40+ 候选。用户 2026-10-07 拍板:**每个被删项做完整档案,看 PrisirAI 有什么可以参考优化的**。
>
> 范围:**只分析被删项目**,不重复 v2 已 ship 的 LX / SiYuan 内容。重点关注 **设计模式 / 用户体验 / 性能可靠性 / 安全隐私 / 跨平台策略** 5 维度。

---

## 0. 总览:被删项目分布

| 类别 | 被删项目数 | 与 PrisirAI 重复能力 | 主要借鉴维度 |
|------|------------|---------------------|--------------|
| 截图 OCR | 3 | handraw-style / screenshot-search / free-for-dev | UX(快捷键 / 默认关 Open API) |
| 剪贴板/输入法 | 5 | —(PrisirAI 不读) | **安全/隐私(红线教育)** |
| 密码 | 4 | —(红线) | **安全/隐私(zero-knowledge / 不可逆加密)** |
| 启动器 | 11 | 主对话意图路由 + task-runner | **设计模式(plugin.json manifest / preload IPC)** |
| 终端 MCP | 6 | skills 工作台 run_loop | **设计模式(stdio JSON-RPC / SDK 复用)** |
| 音乐 | 2 | LX 已 ship | **设计模式(api URL env 覆盖 / 失败语义优先)** |
| 视频 | 4 | 视频创作 6 creator | **设计模式(unix socket / mutating 红线)** |
| 电子书 | 3 | fcontent-engine | **设计模式(SQLite readonly / metadata.db 直读)** |
| RSS | 4 | jina-reader / feedparser / Agent-Reach | **UX(API token ≠ 密码)** |
| 笔记 | 2 | Obsidian context-graph | **设计模式(markdown vault / frontmatter)** |
| 媒体 | 2 | Navidrome/Audiobookshelf(下一个 ship) | **设计模式(Subsonic 协议 / REST API)** |

**借鉴优先级总表**(从 40+ 项目提炼的优化点):

| 优先级 | 借鉴主题 | 实施难度 | 预期效果 |
|--------|----------|----------|----------|
| **P0** | LLM 朗读 4 项红线(密码 / 剪贴板 / 主密码 / 元数据不可逆) | 低(文案 + 弹卡) | 杜绝用户隐私误用 |
| **P0** | LX 失败语义优先 → 普及到所有 Phase A 扩展 | 低(template 已固化) | 错误时 LLM 直接解释,无需 stack trace |
| **P0** | env 覆盖本地服务地址 → 统一 `PRISIR_<NAME>_URL/DB/PATH` 规范 | 低(文档化即可) | 用户改端口 / db 路径零摩擦 |
| **P1** | SQLite readonly mode 打开 → 推广到 Calibre metadata.db / Joplin / Logseq / TriliumNext 等本地库 | 低(`file:...?mode=ro`)| 防止任何扩展误写用户 vault |
| **P1** | uTools plugin.json manifest 思路 → PrisirAI 扩展 inventory schema 增强 | 中(改 `_EXT_USE_CASES` 注入) | LLM 引用更精准,自动指挥「uTools 该不该开」 |
| **P1** | Pot-desktop 默认关策略 → 推广到所有网络类扩展 | 中(需 L1/L2 权限默认关)| 默认安全 |
| **P2** | Subsonic 协议设计 → PrisirAI 「统一媒体协议」抽象 | 高(SDK 主协议推)| 一次接入 Navidrome / Audiobookshelf / LMS / Ampache |
| **P2** | KeePassXC Argon2 设计思想 → 推广 PrisirAI config.yaml 加密存储 | 中(已有 config.yaml)| 加密本地 Key 不可逆 |
| **P3** | mpv unix socket 简洁 → PrisirAI 本地进程桥参考 | 高(新架构)| 终端本地 socket 风格 |

---

## 1. 截图 / OCR / 翻译(3 项目)

### 1.1 [Pot-desktop](https://github.com/pot-app/pot-desktop) — 删除理由:已被覆盖

| 维度 | 内容 |
|------|------|
| 核心设计 | Electron 翻译聚合器,集成 20+ 翻译 API(谷歌/DeepL/腾讯/火山等)|
| PrisirAI 对应能力 | `handraw-style` 风格化截图 / `screenshot-search` 存档 / `free-for-dev` 资源检索 |
| 差异点 | Pot 强调「翻译聚合」,PrisirAI 强调「截图 + 检索」,场景不同 |
| **可借鉴点 1** | **Open API 默认关闭**(用户需手动设置 → 开发者 → 启用) → PrisirAI 应推广「所有网络类扩展默认 L0 只读」 |
| **可借鉴点 2** | 配置 UI 直观(下拉选服务 + 填 Key),PrisirAI 的 [[prisIr-media-keys]] 已 ship 同款设计 |
| **可借鉴点 3** | Pot 的 `config.json` 加密本地存储翻译 API Key → 借鉴到 PrisirAI config.yaml(已有,可验证加密)|
| 实施难度 | 低 / 低 / 中 |

### 1.2 [LunaTranslator(开源版)](https://github.com/HIllya51/LunaTranslator) — 删除理由:已被覆盖

| 维度 | 内容 |
|------|------|
| 核心设计 | 游戏 / 视频实时 OCR + 翻译 hook,内置 Tesseract / PaddleOCR / 多个翻译 API |
| PrisirAI 对应能力 | `handraw-style-prompter` 截图风格化 + `screenshot-search` OCR 搜索 |
| 差异点 | Luna 强调「实时挂载游戏 / 视频源」,PrisirAI 强调「截图存档 + LLM 引用」 |
| **可借鉴点** | Luna 的 OCR fallback chain(Tesseract → PaddleOCR → 失败返空) → 借鉴到 PrisirAI OCR fallback(已有 fcontent FTS5) |
| 实施难度 | 低 |

### 1.3 [owocr](https://github.com/aurippo/owocr) — 删除理由:已被覆盖

| 维度 | 内容 |
|------|------|
| 核心设计 | macOS 截图 OCR CLI,集成 Apple Vision / Tesseract / PaddleOCR |
| PrisirAI 对应能力 | `screenshot-search` 已用 Tesseract + 多 OCR 后端 |
| 差异点 | owocr 是 macOS 限定 CLI,PrisirAI 跨平台 GUI |
| **可借鉴点** | owocr 的「OCR provider 可热替换」(从 env 切 Tesseract → Apple Vision) → PrisirAI 可加 `PRISIR_OCR_BACKEND` env |
| 实施难度 | 中(改 fcontent + screenshot-search)|

---

## 2. 剪贴板 / 输入法(5 项目)

> **整类删除的核心原因**:**剪贴板内容 = 用户隐私**。PrisirAI 主对话不主动读剪贴板,只在用户显式「粘贴」时由前端 HTML `<textarea>` 自然接收,不经任何扩展。

### 2.1 [CopyQ](https://github.com/hluk/CopyQ) — 删除理由:隐私红线

| 维度 | 内容 |
|------|------|
| 核心设计 | 跨平台剪贴板管理器(D-Bus / Win/macOS),SQLite 存储历史,FIFO 限 5000 条 |
| PrisirAI 对应能力 | (无,PrisirAI 不读剪贴板)|
| 差异点 | PrisirAI 主对话主动不读剪贴板历史,与 CopyQ 设计目标相反 |
| **可借鉴点 1** | **D-Bus 接口稳定性**:CopyQ 用 `org.copyq.copyq` D-Bus service,跨平台统一 |
| **可借鉴点 2** | CopyQ 提供 `copyq get 0/text` 单条读命令,**不主动 bulk read** → 借鉴到「单条读取优于批量」原则 |
| **可借鉴点 3** | CopyQ 历史默认 5 分钟去重 + 敏感数据(密码)黑名单 → PrisirAI 隐私守则扩展 |
| **P0 借鉴** | **写入 README「PrisirAI 不读剪贴板」承诺**,任何 user 报告「为什么不能 X」时直接引用 |
| 实施难度 | 低(README 文字)|

### 2.2 [fcitx5](https://github.com/fcitx/fcitx5) — 删除理由:隐私红线

| 维度 | 内容 |
|------|------|
| 核心设计 | Linux 输入法框架,D-Bus `org.fcitx.Fcitx5` 暴露 API(GetCurrentIM / SetCurrentIM / 输入法列表)|
| PrisirAI 对应能力 | (无,PrisirAI 主对话不主动切输入法)|
| **可借鉴点** | fcitx5 的 **「state 自省」边界**:只暴露当前输入法名 + 切换,**不暴露**用户输入内容 → 借鉴到任何「用户输入上下文」扩展 |
| 实施难度 | —(不实施,只是设计原则参考)|

### 2.3 / 2.4 / 2.5 clipper / ibus / RIME — 删除理由:同上隐私 | 合并输入
| **统一借鉴** | 所有输入法扩展都遵守 **「暴露 metadata,不暴露输入内容」** 原则;PrisirAI 任何「上下文获取」类扩展(如浏览器 active tab、IDE 当前文件)都应只暴露 metadata,不读具体内容 |

---

## 3. 密码 / Auth(4 项目)

> **整类删除的核心原因**:**触碰主密码 = 触碰红线**。Phase A 设计原则是「不触碰 secret」。

### 3.1 [KeePassXC](https://github.com/keepassxreboot/keepassxc) — 删除理由:主密码红线

| 维度 | 内容 |
|------|------|
| 核心设计 | 开源密码管理器,Argon2 主密码 + AES256-KDF + TOTP/YubiKey |
| PrisirAI 对应能力 | (无) |
| 差异点 | KeePass 主张 zero-knowledge(KDBX 文件用主密码加密,内存里也不持久化)|
| **可借鉴点 1** | **Argon2 内存硬性**:主密码永远不写磁盘明文,只在内存短暂存在 → 借鉴到 PrisirAI config.yaml(API Key 写本地,但绝不导出 / 打印)|
| **可借鉴点 2** | KeePassXC CLI(`keepassxc-cli show -s` 单条读取)只暴露单条,**不批量** | PrisirAI 扩展读 Secret 类数据也应「单条」 |
| **可借鉴点 3** | KeePassXC **不**支持云同步(KDBX 文件用户自己管)→ PrisirAI 应有「任何 Key 写本地 userData,**不**写 git / 不上传」承诺 |
| **P0 借鉴** | **任何 L1 权限(读 metadata)与 L2 权限(读 secret)必须在文案上让用户清楚**,PrisirAI 权限闸 v1.0 已有 |
| 实施难度 | 中(改权限闸文案 + 风险级配色)|

### 3.2 [Bitwarden](https://github.com/bitwarden) — 删除理由:主密码红线
| **可借鉴点** | Bitwarden 的 zero-knowledge 架构(服务端拿的是加密 blob,主密码只有 client 知道) → PrisirAI config.yaml 同样:用户 Key 只在本地,服务器(若有)拿密文 |

### 3.3 [1Password](https://1password.com) — 闭源,商业
| **可借鉴点** | 1Password 的「Secret Reference」(用户写 `op://vault/item/field` 引用,运行时不暴露明文)→ PrisirAI 可用同款「变量引用」设计减少主对话看到明文 Key 的次数 |

### 3.4 [pass(gpg)](https://www.passwordstore.org/) — gpg + git
| **可借鉴点** | pass 的 `pass show` 单条读;**不**支持 `pass show --all`(显式拒绝) | PrisirAI 任何 secret 读类操作都应有「逐条勾选」机制 |

---

## 4. 启动器 / 效率工具(11 项目)

> **整类删除的核心原因**:11 个候选**全部无 agent 可操作 API**,只是配置文件 / manifest 扫描。PrisirAI 主对话意图路由(task-runner / skills 工作台)已能覆盖「按需选择工具」。

### 4.1 [uTools](https://github.com/uTools-Labs) — **重点借鉴对象**(中国装机量最大)

| 维度 | 内容 |
|------|------|
| 核心设计 | Electron + Node.js 插件体系,**plugin.json5** manifest + **preload 阶段执行** + utools.* API 全集 |
| PrisirAI 对应能力 | task-runner SDK + 扩展 inventory + 主对话意图路由 |
| 差异点 | uTools 设计目标是「启动器 + 插件市场」,PrisirAI 是「AI 主对话 + 工具暴露」 |
| **可借鉴点 1(P0)** | **plugin.json manifest schema**:`logo / preload / main / name / version / pluginName / description / author / homepage / features[] / platform[]` → PrisirAI 扩展 `package.json` 应加对应字段:`homepage / author / features[]`(描述 agent 可用场景)|
| **可借鉴点 2(P1)** | **preload 阶段执行**:utools plugin 的 `preload.js` 在 main 加载前执行,可用 utools.* 全 API → 借鉴到 PrisirAI 扩展「preload 阶段」:扩展 start 前可注册依赖、读 config,init 后再 register command |
| **可借鉴点 3(P1)** | **features[].cmds[]**:用户输入关键字(如 `feature list utools`)触发插件 → 借鉴到 PrisirAI 主对话「extension.suggest」:LLM 主动列出相关扩展的能力清单 |
| **可借鉴点 4(P0)** | uTools 插件不强制云账号,纯本地 | **强化 PrisirAI 隐私红线文案**:任何要求云账号的扩展默认 P3,需用户主动勾选 |
| 实施难度 | 中(改扩展 manifest schema + 文档)|

### 4.2 [Wox](https://github.com/Wox-launcher/Wox) — 重点借鉴

| 维度 | 内容 |
|------|------|
| 核心设计 | C# / Go 主机 + Node.js/Python 插件 stdio JSON-RPC;**store-plugin.json** 公开 manifest |
| **可借鉴点 1(P1)** | **store-plugin.json 公开 schema**:`Id, Name, Author, Version, MinWoxVersion, Runtime, Description, IconUrl, Website, DownloadUrl, ScreenshotUrls, SupportedOS, DateCreated, DateUpdated` → 借鉴到 `extensions/_store/index.json`(PrisirAI 已有但字段不完整,需补 ScreenshotUrls / MinRuntime 等)|
| **可借鉴点 2** | `Runtime: 'nodejs' \| 'python'` 多语言插件支持 → PrisirAI 扩展 SDK 已支持 Node.js,Phase C 可加 Python 插件(读已有 Python 工具)|
| 实施难度 | 低(补 manifest 字段)|

### 4.3 [Flow Launcher](https://github.com/Flow-Launcher/Flow.Launcher) — 重点借鉴
|  节点 | NuGet SDK `Flow.Launcher.Plugin` |
| **可借鉴点 1** | **NuGet 包化 SDK**:Flow Launcher 把 IPlugin 接口发布到 NuGet,作者直接 `dotnet add package Flow.Launcher.Plugin` → 借鉴到 PrisirAI `@prisir/extension-sdk` 已发布到 `extensions/sdk` 目录,Phase C 可 ship npm 公开包 |
| **可借鉴点 2** | JSON-RPC over Pipe(Windows Named Pipe / Unix socket) → 借鉴到 PrisirAI SDK 已用 stdio NDJSON,跨平台一致 |
| 实施难度 | 中(ship npm 公开包)|

### 4.4 [Albert](https://github.com/albertlauncher/albert) — 重点借鉴
| 节点 | C++ Extension + pybind11 桥;InputHistory 类直接暴露 `add / next / prev` |
| **可借鉴点** | **InputHistory 类**:Albert 的 `next(pattern)` + `prev(pattern)` 暴露历史命令,允许外部只读访问 → 借鉴到 PrisirAI 主对话 history:已有 SQLite `chat.sqlite3`,扩展可注册 `chat.history.search` 命令读历史 |
| 实施难度 | 低(已有 SQLite,加 1 个命令)|

### 4.5 [Cerebro](https://github.com/cerebroapp/cerebro) — 部分借鉴
| 节点 | Electron IPC channel='message' 硬编码 |
| **可借鉴点** | **Electron IPC channel 硬编码**:`ipcRenderer.send('message', {message, payload})` → PrisirAI 扩展若用 Electron 兼容,IPC channel 应可配置 |
| 实施难度 | —(PrisirAI 不用 Electron 主对话)|

### 4.6 [Rofi](https://github.com/davatorium/rofi) — 设计参考
| 节点 | 纯 CLI,无持久化主进程,stdin/stdout 协议 |
| **可借鉴点** | **「CLI 优于 GUI」** 设计:无持久化 = 无内存泄漏,PrisirAI 操作复用模态(已有)不依赖 fresh state |
| 实施难度 | — |

### 4.7 [Alfred](https://www.alfredapp.com/) — 闭源,部分借鉴
| 节点 | 闭源 macOS;SDK alfy (2,654 stars) + alfred-workflow deanishe (2,962 stars) MIT |
| **可借鉴点 1(P1)** | **SDK 独立仓 + MIT**:Alfred 主程序闭源但 SDK 全开源让作者贡献 → PrisirAI SDK 已有但生态弱,Phase C 鼓励「SDK 增强 PR」 |
| **可借鉴点 2** | Alfred 的 **info.plist** 配置文件(类似 manifest) → 借鉴到 PrisirAI 扩展配置 |
| 实施难度 | 低 |

### 4.8 [Raycast](https://github.com/raycast/extensions) — 闭源,部分借鉴
| 节点 | 闭源,extensions 仓 MIT 公开 |
| **可借鉴点** | **扩展仓全开源**(作者可读其他人 extension source 借鉴) | PrisirAI 鼓励「扩展仓库公开 PR」(参考 `extensions/_store/index.json`) |

### 4.9 [Quicker](https://github.com/cuiliang/Quicker) — 闭源
| 节点 | 闭源商业;8000+ 共享动作 |
| **可借鉴点** | **「共享动作」概念**:Quicker 鼓励用户分享自定动作 → PrisirAI 鼓励用户分享自定工作流(marketplace 已支持)|

### 4.10 [Launchy](https://github.com/Launchy/Launchy) — 停滞
| 节点 | 项目停滞(2014 年最后版本)|
| **可借鉴点(反例)** | 项目停滞原因:闭源主程序 + 小众插件系统 = 难迁移 → PrisirAI 应避免「扩展版本耦合」主程序,SDK 解耦(已做)|

### 4.11 [Pop!_OS Launcher (COSMIC)](https://github.com/pop-os/cosmic-launcher) — 仍在 alpha
| **可借鉴点(反例)** | 无插件 API = 无生态 → PrisirAI SDK 必须公开稳定接口(已有)|

---

## 5. 终端 / MCP(6 项目)

> **整类删除的核心原因**:skills 工作台 run_loop + tool_use + MCP 客户端已 ship([[prisIr-skills-workbench-phase-2]])。任何 MCP server 都直接复用 SDK,不需要单独写终端扩展。

### 5.1 [wezterm](https://github.com/wez/wezterm) / [tmux](https://github.com/tmux/tmux) / [kitty](https://github.com/kovidgoyal/kitty) — 重点借鉴

| 维度 | 内容 |
|------|------|
| 核心设计 | wezterm unix socket `/run/user/1000/wezterm/wezterm-gui-*`;tmux `/tmp/tmux-*/default`;kitty `$KITTY_LISTEN_ON` |
| PrisirAI 对应能力 | skills 工作台 + MCP 客户端(已 ship)|
| **可借鉴点 1** | **unix socket 路径统一规则**:`$XDG_RUNTIME_DIR/<app>/<session>` (XDG Base Directory) | PrisirAI 任何本地 socket 扩展应遵守 XDG(已有 `~/.prisir/` 替代)|
| **可借鉴点 2** | **「不默认暴露 socket」**:wezterm 需 `--listen-on` 才开 socket,tmux 需 `-L <name>` 才开 | 借鉴到 PrisirAI 任何「本地服务」类扩展默认 L0 不开,需用户启用 |
| 实施难度 | 低(规范文档化)|

### 5.2 [DesktopCommanderMCP](https://github.com/wonderwhy-er/DesktopCommanderMCP) / [tmux-mcp](https://github.com/nicolaballotta/tmux-mcp-server) / [tabby-mcp-server](https://github.com/Thysrael/tabby-mcp-server) — 重点借鉴

| 维度 | 内容 |
|------|------|
| 核心设计 | stdio JSON-RPC 实现的 MCP server;MCP 客户端 SDK 现成 |
| PrisirAI 对应能力 | skills 工作台 run_loop(已 ship)|
| **可借鉴点 1(P0)** | **stdio JSON-RPC 统一协议**:MCP 已成事实标准 → PrisirAI 扩展 SDK 已用 stdio NDJSON,完全兼容 MCP |
| **可借鉴点 2** | **MCP server 列表**:[mcp.so](https://mcp.so) 已聚合 1000+ MCP server | PrisirAI 用户可任意接入第三方 MCP(扩展 SDK 兼容) |
| **可借鉴点 3** | **MCP server 安全边界**:DesktopCommanderMCP 提供 `shell.exec` 但弹卡告知风险 | 借鉴到 PrisirAI 权限闸 v1.0 已有 |
| 实施难度 | 低(SDK 已兼容)|

---

## 6. 音乐客户端(2 项目)

### 6.1 [YesPlayMusic](https://github.com/qier222/YesPlayMusic) — 删除理由:API 与 LX 高度重复

| 维度 | 内容 |
|------|------|
| 核心设计 | Vue 3 第三方网易云客户端,Electron + 桌面,默认开启 127.0.0.1:27232 Open API |
| PrisirAI 对应能力 | LX 已 ship,3 命令 lx.status / lx.lyric / lx.health |
| 差异点 | YesPlayMusic API 与 LX **完全同模式**:anonymous GET /status + /lyric,新增 YesPlayMusic 扩展无增量价值 |
| **可借鉴点 1(P0)** | **YesPlayMusic 也有 Open API**:意味着 LX 模板(失败语义优先 + env 覆盖)可直接复用,Phase A 加 YesPlayMusic 是 5 分钟工作 → **Phase B 候选**,不是 Phase A 必须 |
| **可借鉴点 2** | YesPlayMusic 默认开启(用户装上就开) | **对比**:LX 也默认开,PrisirAI 应保持「默认不写」原则不变 |
| 实施难度 | 极低(复用 LX 模板)|

### 6.2 [MusicFree](https://github.com/maotoumao/MusicFree) — 删除理由:无本地 API
| 节点 | 插件容器,无内置 Open API;插件 index 都是结构化 JSON |
| **可借鉴点** | **MusicFree 插件 manifest** = 纯 JSON,可离线枚举所有支持音源 | 借鉴到 PrisirAI 扩展 inventory 注入(已有 `[[ext-inventory-injected]]`)|

---

## 7. 视频播放器(4 项目)

### 7.1 [VLC](https://github.com/videolan/vlc) — 删除理由:端口默认关
| 节点 | HTTP /requests/status 默认 8080 关闭,需 `--http-password` + `--http-port` 启用 |
| **可借鉴点 1(P1)** | **「默认关」策略**:VLC 默认不开 HTTP,需用户显式启 | 借鉴到 PrisirAI 任何「需本地服务」扩展默认 L0 不开(推广 [[prisIr-agent-perm-gate-v1]] 已有)|
| **可借鉴点 2** | VLC 的「HTTP + Lua 远程脚本」设计 → 借鉴到 PrisirAI 扩展可选 Python/Node host 脚本(Phase C)|

### 7.2 [Celluloid](https://github.com/celluloid-player/celluloid) — 删除理由:mpv 兼容前端
| 节点 | GNOME MPV 前端,API 完全继承 mpv |
| **可借鉴点(反例)** | Celluloid 与 mpv API 完全重叠,只是前端不同 | PrisirAI 不应该 ship 重复前端壳 |

### 7.3 [MPC-HC](https://github.com/clsid2/mpc-hc) — 删除理由:视频创作模块覆盖
| 节点 | Windows WebAPI 默认 13579 关闭 |
| **可借鉴点(P0)** | **MPC-HC 的「WebAPI 路径」`:13579`**:沿用 MPC 历史上的「端口号 = 软件名首字母」惯例 | 借鉴到 PrisirAI 扩展候选的端口规范(已有 `prisir-port-config`)|

### 7.4 [playerctl](https://github.com/altdesktop/playerctl) — 删除理由:Linux MPRIS 平台限定
| 节点 | MPRIS D-Bus `org.mpris.MediaPlayer2.*` 协议 |
| **可借鉴点** | **MPRIS 标准**:playerctl 用 MPRIS 让任意应用控制任意媒体 | 借鉴到 PrisirAI 「统一媒体协议」抽象(Phase C 候选)|

---

## 8. 电子书(3 项目)

### 8.1 [Calibre](https://github.com/kovidgoyal/calibre) — 删除理由:fcontent 已覆盖

| 维度 | 内容 |
|------|------|
| 核心设计 | 桌面电子书管理,metadata.db 是 SQLite,FTS5 已内置 |
| PrisirAI 对应能力 | `prisir-fcontent-engine` Python+FTS5 通用本地内容搜索(已 ship)|
| **可借鉴点 1(P0)** | **metadata.db 直接 SQLite readonly mode 读**:Calibre 的 metadata.db 是公开 schema,Phase A 直接 `file:metadata.db?mode=ro` 读 → **实施 5 分钟**,作为 fcontent 子模块 |
| **可借鉴点 2** | Calibre 用 FTS5 + 元数据 tag/series/publisher 联合搜索 → 借鉴到 PrisirAI fcontent 已支持(同列即可)|
| **可借鉴点 3** | **metadata 改动不改源文件**:Calibre metadata.db 是索引,改 metadata 不动 .epub/.mobi 文件本身 | 借鉴到 PrisirAI 「metadata 与 source 分离」原则(已有 SQLite 设计) |
| 实施难度 | 极低(5 分钟)|

### 8.2 [Koodo Reader](https://github.com/troyeguo/koodo-reader) — 删除理由:本地 SQLite 同 Calibre
| **可借鉴点(反例)** | Koodo 把 SQLite 与 web 前端混合,改 metadata 会写 DB → PrisirAI 严格遵守 readonly mode,绝不写 DB |

### 8.3 [BookLore](https://github.com/booklore-app/booklore) — 删除理由:装机量小
| 节点 | /api/v1/books JWT Bearer token,与 Komga 高度重叠 |
| **可借鉴点** | BookLore 的 **JWT 短期 token**(15 分钟过期)设计 | 借鉴到 PrisirAI API Key 存储设计(已有)|

---

## 9. RSS / 播客(4 项目)

### 9.1 [Fluent Reader](https://github.com/yang991178/Fluent_Reader) — 删除理由:无本地 API
| 节点 | Electron + localStorage,无 HTTP API |
| **可借鉴点** | **localStorage 离线存储**:不依赖云,但也无 API | 借鉴到 PrisirAI 本地 storage 设计(已有 SQLite)|

### 9.2 [Miniflux](https://github.com/miniflux/miniflux) — **重点借鉴对象**(自托管 RSS 标准)

| 维度 | 内容 |
|------|------|
| 核心设计 | Go + SQLite,PostgreSQL;REST API /v1/entries 完全 self-hosted,默认端口 8080 |
| PrisirAI 对应能力 | `feedparser-prisir` 已 ship feedparser 库 + URL 启发式 picker |
| 差异点 | Miniflux 是「自托管 RSS 库」,PrisirAI 是「读外部 URL 列表」;**不重复** |
| **可借鉴点 1(P0)** | **Miniflux API token ≠ 密码**:Miniflux 在用户设置生成 API token,撤销不影响主账号 | 借鉴到 PrisirAI 「任何 API token 写本地 userData,提示『Token ≠ 密码』」|
| **可借鉴点 2** | Miniflux 提供 `/v1/entries?after=ID` 增量同步 → PrisirAI RSS 扩展可用 |
| **可借鉴点 3** | Miniflux 的「未读条目」语义清晰(已读/未读/标星) → 借鉴到 PrisirAI 主对话历史 |
| 实施难度 | 中 |

### 9.3 [FreshRSS](https://github.com/FreshRSS/FreshRSS) — 删除理由:web UI 与 feedparser 重复
| **可借鉴点** | FreshRSS 的 `?t=...` token 防 CSRF | 借鉴到 PrisirAI 任何 web 类扩展的 token 验证 |

### 9.4 [TTRSS / CommaFeed] — 删除理由:同上 web UI 重复 | 合并输入
| **统一借鉴** | RSS 类服务的 **「Read API」+ 「Write API」** 分离;PrisirAI 任何「订阅类」扩展都应分只读(L0)与写(L2)权限 |

---

## 10. 笔记 / 知识库(2 项目)

### 10.1 [Logseq](https://github.com/logseq/logseq) — 删除理由:markdown vault 与 Obsidian 重叠

| 维度 | 内容 |
|------|------|
| 核心设计 | local-first 笔记 + 日志,纯 markdown + EDN metadata;数据存 `~/logseq/graphs/<name>/` |
| PrisirAI 对应能力 | Obsidian vault 通用支持 + SiYuan SQLite 索引(刚 ship)|
| **可借鉴点 1(P1)** | **Logseq 的「EDN frontmatter」设计**:`{:title "..." :tags ["a" "b"]}` 嵌入文件头 | PrisirAI 已支持 Obsidian YAML,可加 Logseq EDN 解析器(Phase B)|
| **可借鉴点 2** | Logseq 的「block reference」`((block-id))` → PrisirAI 反向链接设计已支持 wikilink `[[id]]` |
| 实施难度 | 中 |

### 10.2 [TriliumNext](https://github.com/TriliumNext/Notes) — 删除理由:与 SiYuan/Obsidian 重叠
| 节点 | hierarchical note + SQLite + ETAPI port 8758 |
| **可借鉴点 1(P1)** | **TriliumNext ETAPI**(External Tree API):Web Clipper 风格本地 API,token 鉴权 | 借鉴到 PrisirAI 「ETAPI 协议」支持(Phase B 候选)|
| **可借鉴点 2** | TriliumNext 「cloning note」功能(克隆即包含(笔记+子笔记)) | 借鉴到 PrisirAI 「笔记模板克隆」设计 |

---

## 11. 媒体中心(2 项目)

### 11.1 [Jellyfin](https://github.com/jellyfin/jellyfin) — 删除理由:与 Navidrome 重叠

| 维度 | 内容 |
|------|------|
| 核心设计 | C# + REST API `/System/InfoPublic` 匿名 + `/Users/AuthenticateByName` JWT |
| PrisirAI 对应能力 | Navidrome Subsonic(下一个 ship)+ Audiobookshelf |
| **可借鉴点 1(P2)** | **Jellyfin 的「public endpoint + authenticated endpoint」分层**:`/System/InfoPublic` 匿名读系统元数据,`/Users/AuthenticateByName` 认证后才能操作 | 借鉴到 PrisirAI 「公开 metadata + 受控操作」权限分层(L0/L1/L2)|
| **可借鉴点 2** | Jellyfin **Jellyfin.ApiClient** SDK 自动生成 → 借鉴到 PrisirAI 「SDK 自动生成」Phase C |
| 实施难度 | 高(SDK 自动生成)|

### 11.2 [Emby](https://github.com/MediaBrowser/Emby) — 删除理由:商业,与 Jellyfin 重复
| **可借鉴点** | Emby 的「Active Recording」概念(类似电视录像)→ 不直接借鉴,PrisirAI 不涉及媒体录制 |

---

## 12. 跨项目提取的 5 大设计原则(PrisirAI 立即落地清单)

> 从 40+ 项目提炼的**全栈适用原则**,按优先级排序。

### P0 — 必须落地

#### 原则 1:失败语义优先(从 LX / YesPlayMusic / Pot-desktop 提炼)

```
async function fetchX() {
  const r = await httpGet('/x');
  if (!r.ok) {
    return { ok: false, alive: false, last_error: `HTTP ${r.status}` };
  }
  return { ok: true, alive: true, data: r.parsed, last_error: '' };
}
```

**适用**:任何 Phase A 扩展。**LX Phase A 已实现**,SiYuan Phase A 已复制。**实施**:操作复用 Stage 1 task-runner `task.run_with_retry` 已有类似设计。

#### 原则 2:env 覆盖本地服务地址(从 LX 风格)

```javascript
function baseUrl() {
  return process.env.PRISIR_<NAME>_URL || 'http://127.0.0.1:<port>';
}
function dbPath() {
  return process.env.PRISIR_<NAME>_DB || '<default>';
}
```

**适用**:所有本地服务 / 本地 db 类扩展。**统一规范**:`PRISIR_<NAME>_URL` / `PRISIR_<NAME>_DB` / `PRISIR_<NAME>_PATH`。**实施**:文档化到 `docs/extension-spec.md`。

#### 原则 3:SQLite readonly mode(从 Calibre / KeePassXC 提炼)

```javascript
new DatabaseSync(`file:${db_path}?mode=ro`, { readOnly: true });
```

**适用**:任何读 SQLite 的扩展。**SiYuan Phase A 已实现**。**Phase B 候选**:Calibre metadata.db / Joplin SQLite / Logseq EDN-Lite 等。

#### 原则 4:本地服务默认关闭(从 VLC / MPC-HC / wezterm 提炼)

| 类别 | 默认 | 用户启用方式 |
|------|------|--------------|
| 本地 HTTP 服务 | **默认关** | 用户显式开(如 VLC `--http-port`) |
| 本地 socket | **默认关** | 用户显式启(如 wezterm `--listen-on`) |
| 本地 IPC | **默认关** | 用户显式装扩展 |

**适用**:任何本地服务类扩展。**PrisirAI 已有权限闸 v1.0**,**强化**:任何「需本地服务」的扩展默认 L0 不开,需用户启用。

#### 原则 5:Token ≠ 密码承诺(从 Miniflux / Bitwarden / KeePassXC 提炼)

```
任何 API token 写本地 userData,
提示「Token ≠ 密码,撤销不影响主账号」,
绝不写 git,绝不外传。
```

**适用**:所有 L1 权限扩展。**PrisirAI 已有 config.yaml**,**强化**:文案必须明示。

---

### P1 — 值得做

#### 原则 6:plugin.json manifest schema 增强(从 uTools / Wox 提炼)

```json
{
  "name": "lx-music-bridge-status",
  "displayName": "落雪音乐当前播放(只读)",
  "description": "Phase A 只读扩展...",
  "homepage": "https://...",
  "author": "PrisirAI 官方",
  "features": [
    { "code": "lx.status", "explain": "读 LX 当前歌曲", "cmds": ["lx"] }
  ],
  "platform": ["win32", "darwin", "linux"]
}
```

**适用**:所有 Phase A 扩展。**PrisirAI 扩展 package.json 已有 name/displayName/description**,**补**:homepage / author / features[] / platform[]。**实施**:改 `_scaffold/create-ext.js` 模板 + 加 lint 校验。

#### 原则 7:Open API 默认关闭(从 Pot-desktop / VLC 提炼)

任何「需要本地服务」或「需要联网」的扩展:
- 默认 L0 不开网络 / 不开本地服务
- 需用户在权限闸 v1.0 弹卡勾选才生效

**适用**:所有 Phase B/C 扩展。**PrisirAI 权限闸 v1.0 已有**,**强化**:默认不勾。

#### 原则 8:zero-knowledge 原则(从 KeePassXC / Bitwarden 提炼)

```
1. 用户 Key 只在本地 config.yaml
2. 绝不写 git
3. 绝不打印到日志 / UI
4. 任何「引用」用变量名 `op://vault/item/field` 而非明文
```

**适用**:所有 L1/L2 权限扩展。**PrisirAI 已有**([[prisIrAI-perm-card-ui-v1]]),**强化**:扩展代码层面不允许 `console.log(token)`,需在 lint 层做静态检查。

---

### P2 — 长期演进

#### 原则 9:Subsonic 协议抽象(从 Navidrome / Audiobookshelf / LMS / Ampache 提炼)

```javascript
// 抽象 SubsonicClient
class SubsonicClient {
  constructor(baseUrl, user, password) { ... }
  ping() { return this.get('/rest/ping'); }
  getArtists() { return this.get('/rest/getArtists'); }
  getNowPlaying() { return this.get('/rest/getNowPlaying'); }
}
```

**适用**:PrisirAI 的「统一媒体协议」SDK。**Phase C 候选**,实施难度高,值得做(Navidrome + Audiobookshelf + LMS + Ampache + Funkwhale 5 个媒体服务器一次接入)。

#### 原则 10:mutating endpoint 红线(从 LX / mpv 提炼)

任何有 mutating endpoint 的扩展必须:
1. 默认只读
2. 任何写权限申请需用户**逐条勾选**(不可批量)
3. 写操作日志本地(不外传)
4. Phase B/C 严格 gated

**适用**:所有 mutating endpoint 扩展。**LX Phase A 已规避**,**SiYuan Phase A 已规避**。

---

## 13. PrisirAI 实施建议(下一步落地清单)

### 立即做(本 session 内可完成)

1. **写 `docs/extension-spec.md`** 把 P0 5 原则 + P1 3 原则文档化(沿用 LX/SiYuan 模板)
2. **改 `_scaffold/create-ext.js`** 加上 principles 6 schema 字段
3. **加 ESLint 规则** 禁止 `console.log(token)` / `console.log(apiKey)`(原则 8)

### 下个 ship(等用户拍板)

4. **Calibre metadata.db 扩展** — 5 分钟 ship,原则 3 落地
6. **YesPlayMusic 桥**(LX 模板复用,5 分钟)

### 中期(Phase C)

7. **统一媒体协议 SDK**(原则 9)— Navidrome + Audiobookshelf 一次接入
8. **MCP server 自动注册**(Phase C SDK,现有 skills 工作台 run_loop 兼容)

---

## 14. 文件清单

- `docs/lx-music-like-software-comparison.md` — 本报告
- `docs/lx-music-like-software-survey.md` — v2 调研(被对比项目清单来源)
- `docs/extension-spec.md` — 待写(原则文档化)

---

## 15. 相关引用

- [[lx-music-like-software-survey]] — v2 调研报告(被删项目清单)
- [[lx-music-bridge-phase-a-shipped]] — LX Phase A 模板(失败语义优先)
- [[siyuan-vault-indexer-phase-a-shipped]] — SiYuan Phase A(SQLite readonly 模板)
- [[prisIr-extension-api-v0.1]] — 扩展 SDK v0.1
- [[prisIr-extension-roadmap-2026-09-20]] — 路线评估
- [[prisIrAI-perm-gate-v1]] — 权限闸 v1.0(L0/L1/L2 分层基础)
- [[prisIrAI-perm-card-ui-v1]] — 权限闸弹卡 UI
- [[prisIr-skills-workbench-phase-2]] — skills 工作台 run_loop(MCP 客户端)
- [[prisir-fcontent-engine]] — Python+FTS5 通用本地内容搜索
- [[p3-10-bubble-cancelled-privacy]] — 0 上传红线来源
- [[ext-inventory-injected]] — 32 项扩展 inventory 注入 system prompt
- [[prisIr-media-keys]] — 多平台 Key 配置
- [[prisIr-obsidian-context-graph-design]] — Obsidian vault 图谱设计