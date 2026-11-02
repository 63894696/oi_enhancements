# 仓库体检与调整建议（2026-11-01）

审阅范围：`oi_enhancements` 全仓，57 个已跟踪顶层目录 + 407 个顶层条目，约 25GB。

---

## 0. 一句话诊断

**不是"功能加多了"，是"同一件事做了四遍，而且四个版本分别停在不同的月份"。**
语音有 4 套实现、UI 有 6 个壳、输入法有 4 套；而 7 月 3 日批量导入的 16 个目录此后再没动过，其中两个仍是 Core 组件的依赖。

---

## 1. 清理结果（已完成）

| 处理 | 内容 | 体积 |
|---|---|---|
| 移入隔离区 `_cleanup_2026-11-01/` | 281 项：顶层 `.tmp_*`、`_tmp_*`、散落 `_*.py/png/log/txt/json/db` 探针 | 2.0 GB |
| 已删除 | 字面量 `%TEMP%/`、`C:UsersAdministrator...prisir_worktests/`（路径分隔符丢失产生的空目录）、34 个 `__pycache__`、`.pytest_cache` | ~12 MB |
| 未动 | 所有 git 已跟踪文件（含 26 个已入库的报告/测试脚本） | — |

- 清单：`_cleanup_2026-11-01/manifest.txt`（名称 + 字节数）
- 回滚：把隔离区内容移回原位即可，**没有任何 hard delete**
- 顶层条目 407 → 341

**仍可回收但需要你确认的：**

| 项 | 体积 | 说明 |
|---|---|---|
| `prisiragent-tauri/src-tauri/target/` | 9.8 GB | Rust 编译产物，可重建（耗时较长） |
| `installer/_dist + _staging + _staging2 + stage` | 2.8 GB | 四份安装包暂存，互为历史版本 |
| `_secb_profile` / `_vc2_profile` / `_secbrowser_profile` | 2.8 GB | Chromium 自动化 profile，**可能含已登录会话** |
| `_comet_probe` | 0.3 GB | 同上 |
| `dist/` + `build/` | 1.3 GB | 打包产物 |
| `prisir_ime/target` + `prisIragent_ime/target` + `prisir_findex/target` | 1.7 GB | Rust 编译产物 |
| `.tmp_ciku.db` | 0.16 GB | 词库数据库，不敢当临时文件处理 |

合计约 **18.8 GB**。

---

## 2. 活跃度地图（按最后一次提交）

| 时间 | 子项目 | 提交数 |
|---|---|---|
| 2026-10-10 | `memory/`、`extensions/`(64 个扩展)、`tests/` | 104 / 43 / — |
| 2026-10-06 | `prisiragent_web.py`（15,623 行单文件）、`prisir_work/` | 52 |
| 2026-10-05 | `companion/`（云端语音陪聊）、`prisiragent-shell/` | 47 / 22 |
| 2026-09-28 | `fastlane/`、`agent_shell/`、`mcp_prisiragent_server/`、`prisiragent-tauri/` | 15 / 2 / 3 / 6 |
| 2026-09-18 | `aureon/`、`prisIragent_ime/`、`prisir_fcontent/`、`prisir_asr/` | 4 / 1 / 2 / — |
| 2026-09-05 | `prisir_ime/`、`prisir_findex/`、`prisir_android/`、`securedm-shell/` | 12 / 8 / — / — |
| **2026-07-03** | **16 个目录冻结于此** | 多为 1 |
| 从未提交 | `mcp_prisiragent_v2_self_fix/`、`mcp_prisiragent_v3_plan_parallel/`(51.6MB) | 0 |

**7 月 3 日冻结清单**：`a11y_extract` `audio` `audio_voice_eval` `browser_use_cli` `desktop` `dynamic_router` `im_clients` `perception` `shared_memory` `shared_memory_recompile` `stagehand_oi` `stream_watchdog` `subagent_depth` `vendor` `vision` `voice_input`

---

## 3. 五个结构性问题（按严重度）

### 3.1 语音分叉成两条互不相通的链路 —— 最严重

| | 链路 A（端侧） | 链路 B（云端陪聊） |
|---|---|---|
| 入口 | `agent_shell`（浮动球 + PTT） | `companion`（网页 + 长激活） |
| ASR | `voice_input`：SenseVoice 本地 onnx | `companion_asr.py`：百炼 Paraformer / funasr / whisper |
| TTS | `voice_input`：Windows SAPI | 各自实现 |
| Provider 抽象 | ❌ 无 | ✅ `companion_asr_providers.py` + settings.json |
| 会话 | 无独立会话 | 自有 session + 留痕 + 续接 |
| 最后提交 | 2026-07-03 | 2026-10-05 |
| 彼此引用 | 0 | 0 |

`companion/prisIragent-companion-web.py` 开头的注释已经论证过"工作向 vs 陪伴向必须拆开"——这个判断是对的，但拆完之后**底层 ASR/TTS 没有再合回去**。于是同一件事有了两套互不兼容的实现，一套停在 7 月。

**直接后果**：上一轮我建议给 `voice_input` 加 echo guard / KWS / 填充语，落点选错了。那些能力应该加在"合并后的 voice service"上，而不是给一个 4 个月没动的模块续命。

### 3.2 Core 组件依赖 7 月的僵尸模块

`CORE-COMPONENTS.md` 把 `agent_shell/*` 列为 Core，而：

- `agent_shell/asr_stream.py` → `voice_input/clients/oi_client.py`（非 Core，7 月）
- `agent_shell/oi_pipeline.py` → `dynamic_router/router.py`（Core，但仅 1 次提交，7 月）

法律层面：Core 组件依赖非 Core 路径，会让 §3 的"源码可用义务"边界变模糊。
工程层面：这条语音链路的任何改动都要先唤醒两个僵尸模块。

### 3.3 六个 UI 壳

| 壳 | 技术 | 最后提交 | 状态 |
|---|---|---|---|
| `prisiragent-shell` | Electron | 10-05 | **主壳，无语音** |
| `companion` | Web(FastAPI) | 10-05 | 语音陪聊，独立服务 |
| `prisiragent-tauri` | Tauri | 09-28 | 与 Electron 完全重叠，9.8GB target |
| `agent_shell` | Python + tkinter | 09-28 | 浮动球，唯一语音入口，无主界面 |
| `securedm-shell` | — | 09-05 | 未明 |
| `prisir_android` | 资源壳 | 09-05 | 仅图标/资源 |

主壳（Electron）**没有语音**，有语音的浮动球**没有主界面**，语音陪聊在**第三个进程**里。用户要在三个窗口之间理解同一个 agent。

### 3.4 输入法与 ASR 的平台化重复

- IME：`prisIragent_ime`（TSF 系统级，最复杂也最有用）、`prisir_ime`（浏览器内置）、`fcitx5-prisir`（Linux addon）、仓外 `C:/Users/Administrator/voice_input/lingxi_ime/`（Python 原型）
- ASR：`voice_input`（Python 参考实现）、`prisir_asr`（Rust+ort 移植版，注释写明"移植自 voice_input"）、`companion`（云端）

移植本身是合理的（Python 参考 → Rust 生产），但**参考实现与生产实现同时留在主干**，且没有标明哪个是 SSOT。

### 3.5 仓库级的卫生问题

- 137 个 `.tmp_*` / `_*` 文件被 git 跟踪，其中顶层 26 个（22 份任务报告 + 3 个测试 + 2 个图标生成脚本）
- 两个 `mcp_prisiragent_*` 目录从未提交（0 commits），其中 v3 占 51.6MB
- `%TEMP%/` 这种字面量目录出现在仓库里，说明有脚本把环境变量当字符串拼路径
- `companion/` 里混着模型训练脚本（`_ch_train_all.sh`、`_download_unified.sh`、`_export_heads.py`）和运行时服务

---

## 4. 云端化趋势下的定位判断

你提到"海外厂商的 agent 开始云端化，智能跟鼠标"。我认同这是分水岭，但它对本地产品的含义常被误读。

**云端化不会淘汰本地层，它把本地层的价值压缩成三件事：**

| 维度 | 为什么云端替代不了 |
|---|---|
| **触达** | 云 agent 摸不到你的窗口句柄、输入法状态、剪贴板、光标位置、未上传的文件 |
| **边界** | 哪些字节可以出本机，必须由本地裁决（`voice_input` 的"零外发"就是这个卖点的雏形） |
| **首响应** | 光标跟随、候选词、悬浮球状态需要 <100ms，云 RTT 做不到 |

**"智能跟鼠标"的工程本质**：智能的落点从 chat 窗口移到"焦点所在"。这恰好是你们已有的资产——`prisIragent_ime`（跟焦点）、`prisir_fcontent/overlay_translate.py`（跟选区）、`agent_shell/floating_orb.py`（跟在屏幕角落）、`a11y_extract`（读焦点上下文）。但**三者各自判断焦点，互不共享**。

**结论：砍"脑"，加"手眼耳"。**

- 脑（模型/路由/编排）：`fastlane` 已经是对的抽象，继续做薄，不要自研
- 手（能执行）：`prisir_findex` / `prisir_fcontent` / `prisIragent_ime` / a11y —— 护城河，加重
- 眼（能看）：OCR / 屏幕理解 —— 加重
- 耳嘴（语音）：**三套重复，必须收敛成一套**，且要能 local/cloud 切换

---

## 5. 增删建议

### 5.1 砍 / 归档

| 对象 | 理由 | 动作 |
|---|---|---|
| `prisiragent-tauri` | 9.8GB target，09-28 停摆，与 Electron 壳零差异 | 移出主干到 `archive/` 分支 |
| `prisir_android` | 仅资源，09-05 后未动 | 归档 |
| `fcitx5-prisir` | 单文件 addon，09-06 后未动 | 归档，等有 Linux 用户再启 |
| `securedm-shell` | 09-05 后未动，定位未明 | 归档或并入主壳 |
| 7 月冻结的 16 个目录 | 见 §2 清单 | 全部迁到 `archive/2026-07/` |
| `dynamic_router` | 1 次提交，语义已被 `fastlane` 吸收，只剩 `oi_pipeline` 与 `im_clients` 两个调用方 | 先改调用方，再并入 `fastlane` |
| 64 个 `extensions` | 混着产品扩展与 `*-bridge-status` 演示 | 分类，演示类批量归档 |
| 22 份顶层 `_p2b*_report.md` | 任务报告，已入库 | 移到 `docs/reports/`，不要堆在根目录 |

### 5.2 合（消除重复实现）

| 重复 | 目标形态 |
|---|---|
| 6 个 UI 壳 | 留 `prisiragent-shell`（主壳，补语音）+ `agent_shell`（降级为纯指针层：orb + 热键，不做界面）+ `companion`（保持服务形态，不叫壳） |
| 4 套 ASR | 抽 `asr_provider` 契约 → `local`(SenseVoice/ort) + `cloud`(百炼) 两实现 |
| 2 套 TTS | 同契约：`local`(SAPI 兜底 / Piper 或 VITS 提质) + `cloud` |
| 4 套 IME | `prisIragent_ime`(TSF，唯一能跟焦点) 为 SSOT；`prisir_ime` 降为浏览器嵌入式；`fcitx5` 归档；`lingxi_ime` 明确标注"历史原型"或移出仓库 |
| Python 参考 vs Rust 生产 | 在 `prisir_asr/README.md` 与 `voice_input/README.md` 互相标明 SSOT 关系 |

### 5.3 加

1. **统一 Voice Service**（吸收 `companion` 的 provider 抽象 + `voice_input` 的本地能力）
   - 一份契约：`start / push_audio / events / close` + 事件字段 `type, text, ts, confidence, final`
   - 上一轮建议的 echo guard、常驻 KWS、4 秒填充语、`<think>` 流上过滤、CJK 分句，**全部落在这里**
   - 顺带修掉 `tts_daemon.py` 的 `/stop` 无调用方（Esc 停不掉朗读）

2. **统一的 focus context**（这是"智能跟鼠标"的工程落点）
   ```python
   focus_context = {app, window_title, cursor_rect, selected_text, ime_state, uia_role}
   ```
   orb、hover 翻译、IME、语音输入共用一份，不再各自调用 `GetForegroundWindow`

3. **出口策略（egress policy）显式化**
   - 云端化之后，"哪条链路走云、哪条必须本地"应该是配置项，不是架构假设
   - 把 `voice_input` 的"零外发"从 README 口号变成可校验的 per-channel 规则

### 5.4 不要再增

- ❌ 不要开第七个壳
- ❌ 不要开第五套 ASR/TTS（先定契约）
- ❌ 不要把语音做成独立产品（`voice_input` 三 daemon 的教训：进程越多，越没人维护）

---

## 6. 建议执行顺序

| 阶段 | 内容 | 风险 |
|---|---|---|
| **P0 现在** | 删隔离区；清 B 档构建产物（回收 ~18.8GB） | 零 |
| **P1 两周内** | `archive/2026-07` 迁出 16 个目录；tauri/android/fcitx5 归档；顶层报告归到 `docs/reports/` | 低（git 可回滚） |
| **P2 一个月** | 抽 `asr_provider` / `tts_provider` 契约，`companion` 与 `voice_input` 合并为一个 voice service | 中（动 Core 依赖） |
| **P3 之后** | 统一 focus context；主壳接语音，orb 退化为指针层 | 中 |

**关键次序理由**：P2 必须在 P3 之前。先统一语音，主壳才有东西可接；否则会把第三条语音链路焊进 Electron 壳。

---

## 6.5 四项决策的核查结果（2026-11-01 下午补充）

### ① 灵犀 IME：活的是 `prisIragent_ime` + `prisir_ime`，**两层不是两套**

| 层 | 目录 | 产物 | 状态 |
|---|---|---|---|
| TSF 外壳 | `prisIragent_ime/` | `prisir_ime_tsf.dll` | **已装**，SHA256 `042DCDF7…` 与 `target/release/` 完全一致 |
| 拼音引擎 | `prisir_ime/` | `prisir_ime.dll` | **已装**（1,961,984 字节，被外壳 FFI 调用） |

- 安装版本：`1.0.0-beta.18`（`C:\Program Files\PrisirIME\`），主 DLL 2026-09-09
- 用户词库 `models/ciku.user.db` 最后写入 **2026-10-28**，确认日活在用
- **源码比已装新**：`feedback.rs` `ffi.rs` `keystroke.rs` `lib.rs` `main.rs` `tsf_input_processor.rs` 均在 09-09 之后有改动 → 需重新构建安装才对得上
- 实测（`prisir_ime/verify.py`）：查询正确，但两个问题 ——
  - `zhonghuarenmingongheguo` → **"中华人名哦喝过"**（整句首选有 bug）
  - 热路径 **408–883 μs/query**，与注释里"对标 Python ~2μs"差两个数量级
- 可归档：`fcitx5-prisir/`（09-06 未动）、`C:\Program Files\PrisirIME\*.old_inst`（4 个备份，4.3 MB）
- **不可删**：仓外 `voice_input/lingxi_ime/`（Python 原型）——`prisir_ime/verify.py` 仍以它的 `ciku.db` 为路径，是词库来源
- 另注：安装目录含 `voice/lingxi_voice.exe`（46 MB）+ `sensevoice-small`，**本地语音资产已在 IME 里**

### ② 语音并入主壳 → 见 `docs/plan-2026-11-01-voice-mode-in-main-shell.md`

### ③ 64 个扩展 → 见 `docs/extensions-inventory-2026-11-01.md`

结论：不是 64 个功能，是 1 个工厂跑了 4 批货。建议 64 → 5 个扩展 + 一批资产。

### ④ `prisiragent-tauri`：**经验没有入库**

- Obsidian 1724 篇笔记中，**没有任何一篇提到 `prisiragent-tauri`**，也没有 `subwin` / `port_config`
- 仅有 6 月那三篇 Tauri 笔记，但那是 **SimpleX 桌面客户端**，不是同一个项目
- 已补录：`ObsidianVault/experiences/Tauri 壳 PrisirAI 实现经验与放弃决策 (2026-11-01).md`
  含六能力映射、端口三层契约、子窗 close→hide、配置四候选位，以及"输入法 AI 按钮命名事件桥"

---

## 7. 需要你拍板的四件事

1. **仓外 `C:/Users/Administrator/voice_input/`**（现在装的是 `lingxi_app.py` + `lingxi_ime/`）和仓内 `voice_input/` 哪份是活的？同名不同物，必须先定。
2. **`companion` 的长期形态**：独立服务（现在这样），还是并进主壳成为"语音模式"？
3. **64 个 extensions** 里哪些是产品、哪些是演示/占位？
4. **`prisiragent-tauri`** 彻底放弃，还是保留为未来的移动端/轻量端？

---

*本报告基于只读盘点；除 §1 的临时文件隔离外，未修改任何代码。*
