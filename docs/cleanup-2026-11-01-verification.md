# 大扫除 · 核实结果与处置建议（2026-11-01 下午）

对四项疑问逐一实测。**本文修正了 `extensions-inventory-2026-11-01.md` 里的两处统计错误**。

---

## ① 25 个自托管桥接：全部未在使用

### 实测证据

| 检查项 | 结果 |
|---|---|
| 默认端点 | 25 个全部指向 `127.0.0.1` + 各家默认端口 |
| 本机监听端口 | 8181 / 4533 / 32400 / 2283 / 25600 / 5225 / 3000 / 5000 / 9000 / 23330 / 13378 **全部无监听** |
| 撞端口的两个 | 8000 = **ssh 进程**（SSH 隧道），8080 = **pythonw**（不是 drone/firefly-iii/trilium/miniflux） |
| 持久化配置 | `%APPDATA%\PrisirAI` **不存在** → 没有任何桥接存过 endpoint 或 token |
| Docker | 无容器在跑 |

### 结论

25 个桥接是**按 ship 标准批量产出的能力储备**，不是"原来在用后来停用"——它们从未被真正配置过。

| 判定 | 数量 | 对象 |
|---|---|---|
| 从未使用 | 25 | 全部 `*-bridge-status` |
| 目标已装但无数据 | 1 | `calibre-metadata-indexer`（Calibre 已装于 `C:\Program Files\Calibre2`，但**无书库**） |
| 目标未装 | 1 | `siyuan-vault-indexer`（SiYuan 未装） |

### 处置建议

**不要删代码，要换形态**：

1. 抽 `bridge-runtime`（1 个扩展），把 25 份 GET 白名单 + auth 逻辑收敛成 3 种 kind（`bearer` / `custom` / `subsonic`）+ 配置
2. 25 个 Phase A 的**调研资产保留**（每家的端点清单、License 记录、CVE 评估都在各自 `memory/*-phase-a-shipped.md` 里），只是不再各自成扩展
3. 只保留你确认会用的 3–5 家做配置示例，其余归档到 `archive/extensions/2026-10/`
4. `calibre-metadata-indexer` 保留但标记"待有书库后启用"；`siyuan-vault-indexer` 直接归档

**需要你回答的唯一问题**：这 25 家里，你**打算**用哪几家？（不是"现在在用"，是"以后想在面板上看到"）

---

## ② promo：不是小说抓取插件，是 agent 的 API 目录导航器

### ⚠️ 先更正我上一轮的两处错误

| 我上轮说的 | 实际 |
|---|---|
| "73,423 行代码，几乎全是内嵌数据" | **错**。真实是 **292–380 行/个**，合计约 4,300 行代码 + `data/services.json` 19,531 行 / 521KB |
| "把网页内容抄进 JS，不产生 Trigger" | **错**。它是带 4 个命令的 agent 工具 |

统计错因：`Get-ChildItem -Include *.js -Recurse` 把 `node_modules` 里的 JS 也算进去了。

### 它实际是什么

`public-apis-promo/index.js`（341 行）头部注释：

```
public-apis-promo v0.1.0 — 公共 API 资源导航器(Phase B)
数据来源: 本地 data/{categories,services,meta}.json
          (从 public-apis/public-apis 快照抽取, 2026-10-02)
          本扩展只读快照, 绝不联网回写。

命令:
  api.find           { query?, category?, limit? }   关键词 + 分类双过滤
  api.list_categories {}                            列所有分类 + 条目数
  api.detail         { name }                       查单个 API 的完整描述
  api.random         { category? }                  随机推荐(给 LLM「给我一个 X」用)
```

**`api.random` 的注释直接写着"给 LLM 用"，`Phase C 接入主对话 EXEC 标记, LLM 自动识别「找 API」类需求`。**

所以它是**给 agent 用的能力**：你问"我想做个股价提醒，有什么现成 API"，agent 查本地快照回答，不联网。

你记的"小说网站正文抓取插件"是另一个项目，这批不是。

### 但确实有残缺

| 扩展 | index.js | 状态 |
|---|---|---|
| `public-apis-promo` | 341 行 | ✅ 完整 |
| `public-apis-cn-promo` | 345 行 | ✅ 完整 |
| `n0shake-public-apis-promo` | 292 行 | ✅ 完整 |
| `awesome-selfhosted-promo` | 380 行 | ✅ 完整 |
| `awesome-hub-promo` | **缺** | ❌ 半成品 |
| `free-for-dev-promo` | **缺** | ❌ 半成品 |

### 处置建议

**保留能力，合并实现**：

1. 4 个完整的数据集合并成 1 个 `api-catalog` 扩展，统一 `api.find` / `api.detail` / `api.random`
2. 删 2 个没有 `index.js` 的半成品（`awesome-hub-promo`、`free-for-dev-promo`）——数据可从源仓库重抓
3. 数据文件保留为 `data/*.json`，不进 JS

理由：agent 确实需要"给我找一个能用的 API"这个能力，但**不需要 4 个扩展各带一份数据**。

---

## ③ 本地工具：确实是 agent 的自带工具箱，不能删

### 证据链

1. **`memory/agent-tool-survey.md`**（2026-09-26）记录了能力层决策，原则是"零成本开放接入"，并明确"已 ship: Tavily / Exa / HN Algolia / feedparser / yt-dlp / gh CLI / jina"
2. **`C:\Users\Administrator\.prisir\store\`** 里 09-20 那批**全部已打包安装**：`ext-app-launcher-0.1.0.tgz`、`ext-ascii-tree`、`ext-base64-codec`、`ext-clipboard`、`ext-code-snippets`、`ext-ext-mermaid`、`ext-git-stats` …
3. **`extensions/_store/index.json`** 有完整中文描述（mermaid 标注"M3.36 Phase 1 ship 的示例扩展"）
4. 每个扩展**只用 Node 内置模块**（`node:sqlite`、`child_process`、`os`），无 npm 运行时依赖 → 正是"用户装完即用、无需外部依赖"

**你的记忆是对的。这批是 agent 的原生能力层。**

### 但要分三层，不是全留

| 层级 | 工具 | 理由 |
|---|---|---|
| **核心（必留）** | `keystroke-emit` `window-list` `process-scan` `clipboard` `app-launcher` `http-request` `task-runner` `web-watch` | 直接操作机器，云端替代不了；`task-runner` 还是工单模型的天然载体 |
| **有用（留）** | `code-snippets` `quick-note` `todo` `scheduled-task` `system-watchdog` `pr-review` `git-stats` `ext-mermaid` `json-format` `regex-tester` `sequence-builder` | 各自解决一个具体场景，代码 60–380 行，成本低 |
| **存疑（可清）** | `pomodoro` `timestamp` `base64-codec` `ascii-tree` | agent 自己就会算时间戳/base64/格式化树，不需要专门扩展提供 |

### 处置建议

1. 20+ 个合并为 `local-capabilities` 一个扩展，内部按 tool 名分派 —— **代码一行不改，只换外壳**
2. 只清掉上面 4 个存疑项
3. `task-runner` 单独保留为独立扩展（它是工单模型的载体，不该被埋进大杂烩）

---

## ④ IME：不是词频学习修好的，是 DP 的 bug

### 先回答你的疑问

**你的假设不成立。** 用已装词库（`ciku.db` + `ciku.user.db` 副本）实测：

| 调用 | 结果 |
|---|---|
| `query("zhonghua")` | 第一候选 **中华人民共和国**（weight 500703） |
| `smart_sentence("zhonghuarenmingongheguo")` | **中华人名哦喝过**（仍然错） |
| `user_list()` | 学过的都是短词：这是 / 不是 / 工作目录 / 加上 / 打包 / 输入 / 路径 / 验证码…**没有** `zhonghuarenmingongheguo` |

**你打 `zhonghua` 看到"中华人民共和国"，走的是候选列表（`query` 前缀匹配），不是整句识别（`smart_sentence`）。两条不同的代码路径。** `verify.py` 测的是后者，所以它错；你打字看到的是前者，所以它对。

词频学习**没有**修这个问题，因为这条路径压根没被触发过。

### 两个真 bug

**(a) DP 窗口 4 段，词长 7 音节** — `src/engine.rs:484`

```rust
let start = if i >= 4 { i - 4 } else { 0 };   // 最多 4 个 segment 拼一个词
```

`zhonghuarenmingongheguo` 是 7 音节。除非 `segment()` 把它归并成 ≤4 段，否则"中华人民共和国"这个整词在 DP 里**根本不可达**。

**(b) `bonus = 20.0` 破坏了代价函数** — `src/engine.rs:493-494`

```rust
let bonus = if pseq.len() >= 2 { 20.0 } else { 0.0 };
let score = dp[j].0 + (weight.max(1) as f64).ln() + bonus;
```

词库最大权重 500703 → `ln(500703) ≈ 13.1`。
**而任何双音节词的 +20 都大于任何单词的 log 权重上限。**

所以这个 DP 实际在最大化「**多音节词的个数**」，不是概率：

- 中华人民共和国（1 个词）：`+20`，总计 ≈ 33.1
- 中华 + 人名 + 哦喝 + 过（4 个词）：`+60~80`，必然胜出

这就是"中华人名哦喝过"的成因。

### 修复方向

| 问题 | 修法 |
|---|---|
| (b) 常数失衡 | `bonus` 必须远小于 `ln(weight)` 的动态范围。改成 `bonus = 0.3 * 段数`，或直接用 `ln(词频概率)` 做代价 |
| (a) 窗口太窄 | 窗口按 `segment` 的最长词长度动态算，别写死 4 |
| 兜底 | 进 `smart_sentence` 前先 `query(整串拼音)`，若首候选的拼音序列与输入完全匹配且长度 ≥ 5，直接返回 —— 一行代码，覆盖绝大多数长词 |

### 安装包核对（不用启动阿里云）

| 项 | 结果 |
|---|---|
| Dropbox 安装包 | `LingxiIME-Windows-x64-1.0.0-beta.18-beta-Setup.exe`（09-09, 310MB）**与已装版本一致，没有更新包** |
| 其余包 | Android apk (beta.1) / Linux deb (0.2.0) / PrisirAI-Setup-2.7.7.exe (09-14) |
| 源码是否更新 | 6 个 `.rs` 在 09-09 之后改过 → **需重构建才对得上** |
| 本机工具链 | `cargo 1.95.0` + `rustc 1.95.0` + `node v22.22.2` **齐全** |

**不需要阿里云抢占实例。** 本机就能 `cargo build --release`，比拉一台 Windows spot 实例快得多也便宜。

---

## ⑤ 知识库过期项（顺带盘点）

| 位置 | 规模 | 状态 | 建议 |
|---|---|---|---|
| `memory/`（仓库内） | 105 篇 | 活跃，9–10 月 | **保留**，是真实进度线索（含 ship 标准、phase-a 记录） |
| Obsidian `01-Daily/` | 到 2026-09-week4 停 | 过期 1 个多月 | 保留最近 2 周，更早的归档 |
| Obsidian `experiences/` | 含 6 月 Tauri 三篇 | 是**另一个项目**（SimpleX） | 加 `project:` 字段区分，避免与 PrisirAI 混淆 |
| 仓库根 `_p2b*_report.md` | 22 份 | 任务报告，已入库 | 移到 `docs/reports/2026-08/` |
| Obsidian 中 `node_modules` | 有多处 | 被误纳入 vault | 加 `.obsidianignore` 或移出，影响检索速度 |

---

## 处置清单（等你确认后执行）

| # | 动作 | 影响 | 风险 |
|---|---|---|---|
| 1 | 删 `awesome-hub-promo`、`free-for-dev-promo`（无 index.js） | −2 扩展 | 无，可重抓 |
| 2 | 删 `pomodoro`/`timestamp`/`base64-codec`/`ascii-tree` | −4 扩展 | 无 |
| 3 | 25 桥接收敛为 `bridge-runtime` + 配置 | 27 → 1 | 中，需迁移配置 |
| 4 | 20 工具合并为 `local-capabilities`（task-runner 独立） | 20 → 2 | 中，需改 manifest |
| 5 | 4 个 promo 数据合并为 `api-catalog` | 4 → 1 | 低 |
| 6 | 修 `smart_sentence` 的 bonus 与窗口，重构建 IME | — | 中，需回归测试 |
| 7 | 仓库根 22 份报告移到 `docs/reports/` | — | 无 |

---

*本文所有结论均为实测，未修改任何代码或词库。测试用的词库是 `D:\Temp\ime_test\` 下的副本。*
