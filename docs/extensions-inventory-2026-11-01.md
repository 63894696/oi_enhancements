# 64 个扩展盘点与整合建议（2026-11-01）

## 0. 先说结论

**64 个不是 64 个功能，是 1 个工厂跑了 4 批货。**

`extensions/_scaffold/` 里的 `create-ext.js` + `bearer-client.js` + `custom-auth-client.js` + `subsonic-client.js` 是模板；`sdk/` 是运行时契约；`marketplace/` + `_store/` 是分发。四批货分别是：

| 批次 | 时间 | 数量 | 形态 |
|---|---|---|---|
| 本地能力工具 | 09-20 | 20+ | 60–200 行，小而实 |
| 数据汇总 promo | 09-27 ~ 10-02 | 6 | 4,428–20,155 行，几乎全是内嵌数据 |
| prompt 素材 | 09-27 | 2 | 3,030 / 5,984 行 |
| 只读桥接 bridge-status | 10-07 ~ 10-10 | 27 | 400–665 行，高度同构 |

**真正的建议：不该按"64 个扩展"管理，该按 3 类资产合并。**

| 类别 | 现状 | 建议 |
|---|---|---|
| 只读桥接 | 27 个独立扩展 | **合并成 1 个通用 bridge 运行时**，27 个降级为配置条目 |
| 本地能力工具 | 20+ 个独立扩展 | **合并成 1 个 local-capabilities**，按 tool 分派 |
| 数据/prompt 素材 | 8 个，约 76,000 行 | **移出 `extensions/`**，放 `assets/`，不进打包 |

理由见下。

---

## 1. 分类明细

### 1.1 基础设施（6，保留）

| 目录 | 行数 | 职责 |
|---|---|---|
| `_scaffold` | 648 | 模板工厂：`create-ext.js` + 三种 auth client |
| `sdk` | 229 | `@prisir/extension-sdk`，stdio NDJSON JSON-RPC |
| `marketplace` | 569 | 论坛远端镜像 workflow bundle |
| `_store` | 645 | 本地商店（tgz + index.json） |
| `_build` | 74 | 构建脚本 |
| `extensions` | 0 | 空壳，可删 |

### 1.2 只读桥接（27，建议合并）

25 个 `*-bridge-status` + 2 个 `*-indexer`，全部 Phase A（只白名单 GET，0 上传）：

```
simplex  hedgedoc  trilium  paperless  vaultwarden  habitica  linkwarden
gitea    immich    plausible  lx-music  navidrome   outline   kavita
nextcloud  plex    audiobookshelf  bookstack  mealie  yesplaymusic
funkwhale  firefly-iii  drone  komga  miniflux
calibre-metadata-indexer  siyuan-vault-indexer
```

代码量 400–665 行/个，按认证方式三选一（`bearer-client` / `custom-auth-client` / `subsonic-client`），**同构度极高**。

**问题**：27 个独立扩展 = 27 次安装 + 27 次授权 + 27 份重复的 GET 白名单逻辑。对一个"一次装好"的桌面产品，这是安装成本灾难。

**建议**：抽一个 `bridge-runtime` 扩展，配置化：

```yaml
bridges:
  - kind: bearer          # bearer | custom | subsonic
    name: gitea
    base: http://127.0.0.1:3000
    token_ref: keyring://gitea
    allow: [GET /api/v1/repos/*]
  - kind: bearer
    name: immich
    base: http://127.0.0.1:2283
    token_ref: keyring://immich
```

27 个 Phase A 的资产（每个服务的端点清单、License 记录、CVE 评估）全部保留为配置与文档，代码只留一份。

> 这不是"删掉你的工作"，是把 27 份拷贝收敛成 1 份实现 + 27 条配置。筛选标准（`memory/extension-ship-filter.md` 的 7 条）不需要改。

### 1.3 本地能力工具（20+，保留但合并）

按行数排序：

| 工具 | 行数 | 能力 | 对"全场景"的价值 |
|---|---|---|---|
| `task-runner` | 924 | DAG 任务编排 + 定时触发 + 执行历史（node:sqlite） | ★★★ 这是工单模型的天然载体 |
| `web-watch` | 750 | URL 监控 + SHA256 比对 + 变化 toast | ★★★ 典型 Source |
| `ext-mermaid` | 381 | 对话内 mermaid 渲染 | ★★ |
| `system-watchdog` | 361 | 电源/内存/空闲巡检 | ★★ |
| `keystroke-emit` | 199 | SendInput 模拟键盘/鼠标 | ★★★ "手"的核心 |
| `git-stats` | 152 | git log 统计 | ★ |
| `process-scan` | 140 | 枚举进程 + kill | ★★★ |
| `code-snippets` | 139 | 片段检索 + 插入 workdir | ★★ |
| `todo` | 133 | 本地待办 + 提醒 | ★★ |
| `window-list` | 128 | 枚举窗口 + 关闭/最小化/置顶 | ★★★ focus context 基础 |
| `ascii-tree` | 119 | 目录树可视化 | ★ |
| `scheduled-task` | 119 | schtasks 包装 | ★★ |
| `app-launcher` | 116 | 启动 exe / URL | ★★★ |
| `http-request` | 116 | REST 调用 | ★★ |
| `json-format` | 116 | JSON 格式化 | ★ |
| `pomodoro` | 112 | 番茄钟 | ○ |
| `pr-review` | 108 | git diff 切 hunk + 审查模板 | ★ |
| `sequence-builder` | 105 | 自然语言 → 时序图 | ★ |
| `quick-note` | 104 | Markdown 笔记 CRUD | ★★ |
| `regex-tester` | 87 | 正则测试 | ★ |
| `clipboard` | 85 | 剪贴板读写 | ★★★ |
| `timestamp` | 74 | 时间格式互转 | ○ |
| `base64-codec` | 60 | 编解码 | ○ |

**问题**：每个 60–200 行，20 个独立扩展意味着 20 份 manifest、20 次安装、20 个进程入口。

**建议**：合并成一个 `local-capabilities`，内部按 tool 名分派。20 个工具的代码一行不用改，只是换了一层外壳。

**判定依据**：这 20 个是唯一"云端 agent 替代不了"的部分——它们直接操作你的机器。`keystroke-emit`、`window-list`、`process-scan`、`clipboard`、`app-launcher` 加起来 670 行，是整套系统里性价比最高的代码。

### 1.4 数据汇总 promo（6，建议移出）

| 扩展 | 行数 | 实质 |
|---|---|---|
| `public-apis-promo` | 20,155 | public-apis 51 分类 1953 条 |
| `awesome-selfhosted-promo` | 19,290 | awesome-selfhosted 95 分类 |
| `public-apis-cn-promo` | 14,068 | 国内 API 54 分类 1493 条 |
| `free-for-dev-promo` | 9,885 | 57 分类 1300+ 免费 SaaS |
| `awesome-hub-promo` | 5,597 | 汇总 |
| `n0shake-public-apis-promo` | 4,428 | 56 分类 481 条 |
| **合计** | **73,423** | **内嵌静态数据，非能力** |

73,423 行里绝大部分是硬编码的 JSON 字面量。它们不提供服务、不调 API、不产生 Trigger——**是资料，不是扩展**。

**建议**：移到 `assets/data/api-catalogs/`，或直接删除（源仓库随时可重新抓取）。**不要让它们进安装包。**

### 1.5 prompt 素材（2，建议移出）

| 扩展 | 行数 | 实质 |
|---|---|---|
| `agency-roles` | 5,984 | 角色 prompt 库 |
| `handraw-style-prompter` | 3,030 | yang0/handraw-style 的 278 风格 + 36 颜色 + 120 版式 |

同样是数据。建议并入 `assets/prompts/`。

---

## 2. 与"agent 智能全场景"目标的对应

按上一条回复里的 Source 矩阵，这 64 个扩展的定位是：

| 扩展类别 | 在矩阵里的角色 |
|---|---|
| 本地能力工具 | 提供 **执行手段**（工单的 Executor 调用它们） |
| 只读桥接 | 提供 **Trigger 来源**（自托管服务状态变化） |
| 数据/prompt 素材 | 既不是 Trigger 也不是手段，是**上下文素材** |
| 基础设施 | 分发与运行时 |

**所以"哪些无用"这个问题的答案是：**

- **无用的不是功能，是形态。** 27 个桥接和 20 个工具的功能都有用，但"一个功能一个扩展"的形态在 40+ 个之后开始产生负收益。
- **真正无用的是 73,423 行 promo**：它们没有任何执行路径，只是把网页内容抄进 JS 文件。

---

## 3. 建议动作

| 优先级 | 动作 | 影响面 |
|---|---|---|
| P0 | 6 个 promo + 2 个 prompt 素材移出 `extensions/` 到 `assets/` | 减 76k 行，减 8 个扩展 |
| P1 | 20+ 本地工具合并为 `local-capabilities` | 20 → 1 |
| P2 | 27 个桥接合并为 `bridge-runtime` + 配置 | 27 → 1 |
| P3 | 删空的 `extensions/extensions/` | 1 |

做完之后：**64 → 5 个扩展**（`bridge-runtime` / `local-capabilities` / `sdk` / `marketplace` / `_store`）+ 一批资产。

---

## 4. 待你确认

1. 25 个自托管服务，**哪些你实际在用**？只保留在用的那些做配置项，其余连配置一起归档。
2. promo 那 73k 行，是**删掉**还是**移进 assets 留档**？
3. 本地 20 个工具里，`pomodoro` / `timestamp` / `base64-codec` / `ascii-tree` 这几个明显不属于"agent 能力"，要不要一并清掉？

---

*盘点基于扩展 manifest 与源码行数统计；分类依据沿用 `memory/extension-ship-filter.md` 的 7 条 ship 标准，未修改该标准。*
