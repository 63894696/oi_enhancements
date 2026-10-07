# PrisirAI 扩展规范 (Extension Spec v0.1)

> 范围:PrisirAI 扩展开发 / 审计 / 部署的统一规范。本规范由 [v2 对比研究](../memory/lx-music-like-software-comparison.md) 提炼的 10 大原则 + 5 步实施法 + 5 个 Phase A 实战扩展验证 沉淀。

---

## 0. 适用范围

- **Phase A** (只读):本规范 v0.1 范围,任何「不修改数据、不读外部网络」的可观测本地软件。
- **Phase B** (写 / 控制):v0.2 范围(待 spec),需 L1/L2 权限门禁 + 用户逐条勾。
- **Phase C** (跨软件协同):v0.3 范围(待 spec),需 Subsonic 等统一协议 SDK 沉淀。

---

## 1. 5 步实施法

每新加一个 Phase A 扩展,**必须**按这 5 步走(顺序):

### 1.1 端口 / 路径确认

| 类型 | 检查项 |
|---|---|
| HTTP 桥(LX / YesPlayMusic / Navidrome)| 默认端口 + 端点路径 + 鉴权方式(anonymous / token / API key / Basic)|
| SQLite 索引(SiYuan / Calibre)| 数据库文件默认路径 + 模板探测 + 版本兼容(老版本 table fallback)|
| 其他(剪贴板 / 截图 / 系统监控)| 平台特定路径 + 权限要求 |

**失败语义优先** — 不要先想 happy path,先想「不可达 / 鉴权失败 / 数据库锁 / 字段缺失」时返什么。

### 1.2 失败语义优先(借鉴原则 P0)

**每一层失败都必须有结构化 `last_error`**,从最坏情况设计:

```js
{ ok: false, alive: false, last_error: 'HTTP 503 + timeout detail' }
```

- **HTTP 类** — `{ ok, alive, last_error: 'HTTP N + message' }`
- **SQLite 类** — `{ ok, alive, last_error: 'sqlite 3 open fail: ' + e.message }`
- **Subsonic 类** — 3 层失败路径:HTTP / envelope 缺失 / `subsonic-response.status='failed'` + `error.code`
- **never throw** — 总是返对象,让上游 invoke caller 决定怎么处理。

### 1.3 env 覆盖(借鉴原则 P2)

**任何配置项都必须有 env 覆盖**,优先级:
1. `PRISIR_<EXT>_<KEY>` 环境变量(用户 / CI 注入)
2. 默认值(写在代码注释里)

env 命名规范:
- `PRISIR_<EXT>_URL` — 服务端 URL(HTTP 类)
- `PRISIR_<EXT>_DB` — 数据库路径(SQLite 类)
- `PRISIR_<EXT>_USER` — 用户名(鉴权类)
- `PRISIR_<EXT>_PASS` — 密码(鉴权类,**绝不 commit**)
- `PRISIR_<EXT>_TOKEN` — 预派生 token(可选,跳过明文密码)
- `PRISIR_<EXT>_CLIENT` — client 名(协议层 c 参数)
- `PRISIR_<EXT>_API_V` — 协议版本

**绝不 commit 明文密码**。`package.json` 只声明权限,运行时由用户通过主壳 / 配置注入。

### 1.4 测试三件套

每个 Phase A 扩展**必须**有 3 层测试:

1. **Node 端单测**(`__tests__/run.js`):
   - 默认跑:vm sandbox 注入 SDK stub,测不可达 / 数据解析 / env 覆盖 / token 派生
   - 加 `--e2e` 跑:起临时 Node http.createServer mock 真实协议响应
   - E2E mock server **必须按真实协议模拟**(鉴权 mock 用客户端发的盐重算 token 验证,**绝不**硬盐值 — 否则掩盖客户端鉴权 bug)

2. **Python wrapper**(`tests/test_<ext>.py`):
   - 4 case:Node 单测全过 / Node E2E 全过 / SDK 注册命令 / JS 语法 parse
   - CI 跑测试时统一覆盖

3. **主仓联合回归**:
   - 所有 Phase A 扩展的 Python wrapper 同时跑,验证无相互回归

### 1.5 L0 / L1 / L2 权限分级

| 级别 | 含义 | 弹卡 | 例 |
|---|---|---|---|
| **L0** | 纯只读 / 注入 UI 通知 | **不弹** | `ai.invoke.command:lx.status`、`ui.inject.notification` |
| **L1** | 写本地配置 / 触发本地服务 | 弹卡 + 倒计时 + 拒绝按钮 | `state.write.lx`、`local.process.spawn` |
| **L2** | 跨软件协调 / 不可逆操作 | 弹卡 + 二次确认 | `cross.ext.invoke`、`file.delete.confirm` |

Phase A 默认 L0。Phase B 必须用户**逐条**勾。Phase C 必须全局扫一遍组合,确认无串联权限放大风险。

---

## 2. SDK 协议

### 2.1 通信层

主进程 → ext:stdin NDJSON(每行一个 JSON 对象)
ext → 主进程:stdout NDJSON

JSON-RPC 2.0 子集:

```json
// 主 → ext (request)
{"jsonrpc":"2.0","id":1,"method":"lx.status","params":{}}

// ext → 主 (response)
{"jsonrpc":"2.0","id":1,"result":{"ok":true,...}}

// ext → 主 (notification)
{"jsonrpc":"2.0","method":"ui.inject","params":{...}}
```

### 2.2 注册 API

```js
const { PrisIrExt } = require('@prisir/extension-sdk');
const ext = new PrisIrExt({ id, name, version });
ext.registerCommand('my.cmd', async (args, ctx) => result);
ext.registerPanel(id, { title, icon, render });
ext.registerFileProvider(scheme, (path) => Buffer);
ext.onSessionMessage((msg, ctx) => ...);
await ext.start();
```

### 2.3 跨扩展调用 (Phase B-1)

```js
const result = await ext.invokeExt('target-ext-id', 'method', {param: 1}, timeoutMs=10000);
```

---

## 3. manifest schema

每扩展根目录 `package.json` 必须含:

```jsonc
{
  "name": "ext-id",                    // [required] 小写字母/数字/-/_,kebab-case
  "displayName": "显示名",              // [required]
  "version": "0.1.0",                  // [required] semver
  "description": "一句话描述",          // [required] 提「纯只读 / 0 上传 / 借鉴原则」
  "author": "PrisirAI 官方 / Your Name",
  "main": "index.js",
  "type": "commonjs" | "module",
  "license": "MIT",

  // ── PrisirAI 扩展 manifest ──
  "prisIrPermissions": [                // [required] L0/L1/L2 权限列表
    "ai.invoke.command:ext.action",
    "ui.inject.notification"
  ],
  "prisIrFeatures": [                  // [optional] feature tags,见 §3.1
    "read-only",
    "requires-local-server"
  ],
  "prisIrPlatforms": [                 // [optional] 跨平台支持,空数组 = 不声明 = 默认 win32
    "win32", "darwin", "linux"
  ],

  "dependencies": {
    "@prisir/extension-sdk": "file:../sdk"
  }
}
```

### 3.1 `prisIrFeatures` 标签规范

| 标签 | 含义 | 例 |
|---|---|---|
| `read-only` | **严格只读**,绝无写操作 | LX / YesPlayMusic / Navidrome / SiYuan / Calibre |
| `requires-local-server` | 依赖本地 HTTP 服务(用户先启动) | LX / YesPlayMusic / Navidrome |
| `requires-credentials` | 需要鉴权(用户名/密码/token) | Navidrome |
| `open-api-default-off` | 软件默认 Open API **关闭**,用户需进设置启用 | YesPlayMusic |
| `subsonic-protocol` | 走 Subsonic 协议(token = md5(pass+salt)) | Navidrome / Audiobookshelf(未来)|
| `local-file-access` | 读本地文件 / 数据库 | SiYuan / Calibre |
| `sqlite-readonly` | SQLite readonly mode 打开(`file:...?mode=ro`) | SiYuan / Calibre |
| `fts5-search` | FTS5 全文搜索 | SiYuan |
| `like-fuzzy-search` | LIKE 模糊搜(`%` `_` `\` 转义) | Calibre |
| `needs-native` | 需要原生二进制 / Node addon | (未来) |
| `scheduled-task` | 后台定时任务 | (未来)calendar-extractor |
| `process-scan` | 扫进程 / 端口 | (未来)app-launcher |
| `system-watchdog` | 系统级监控 | (未来) |

**推荐 feature 命名**:
- 小写 kebab-case
- 描述「数据源类型」+「访问方式」+「协议细节」
- 主进程读后可做:商店筛选 / AI inventory 决策 / 权限建议

### 3.2 `prisIrPlatforms` 标签规范

- 数组项:Node.js 平台字符串(`process.platform`)
- `win32` / `darwin` / `linux` / `android`
- 空数组 = 不声明 = 主进程按 win32 兜底(开发机默认)
- HTTP 桥无平台差异(仍推荐声明,便于 AI inventory 过滤)

---

## 4. 失败语义总表

| 失败类型 | 字段 | 例子 |
|---|---|---|
| 网络不可达 | `last_error: 'ECONNREFUSED 127.0.0.1:23330'` | LX(关) |
| HTTP 4xx/5xx | `last_error: 'HTTP 503'` | Navidrome(鉴权失败)|
| Subsonic 失败 | `last_error: 'status=failed code=40 msg=Wrong username'` | Navidrome |
| Envelope 缺失 | `last_error: 'Subsonic ... missing root envelope'` | Navidrome |
| SQLite 锁 / 不存在 | `last_error: 'sqlite open fail: SQLITE_CANTOPEN'` | SiYuan / Calibre |
| 鉴权缺凭证 | `last_error: 'no credentials — set PRISIR_NAVIDROME_PASS or PRISIR_NAVIDROME_TOKEN'` | Navidrome |
| LIKE/FTS5 语法错 | `last_error: 'FTS5 MATCH syntax: ...'` | SiYuan |
| JSON 解析错 | `last_error: 'JSON parse: Unexpected token ...'` | All HTTP |

**`alive` vs `ok`:**
- `alive = true` — 服务端活着(返回了响应),但 logical 失败(如 Subsonic code=40)
- `alive = false` — 服务端不可达 / 协议层失败
- `ok = true` — 业务逻辑成功(只有 alive + 业务 OK 才返 true)

---

## 5. 借鉴的 10 大设计原则(摘要)

### P0 立即落地(已 ship)

1. **失败语义优先** — 不抛异常,返 `{ ok: false, alive: false, last_error: ... }`(已 ship)
2. **env 覆盖** — `PRISIR_<EXT>_<KEY>`,默认写在代码注释(已 ship)
3. **SQLite readonly mode** — `file:...?mode=ro` 打开数据库(已 ship,Calibre+SiYuan)
4. **本地服务默认关闭** — 软件 Open API 默认 off,用户在软件设置手动开(借鉴 YesPlayMusic 案例)
5. **Token ≠ 密码** — salted token,截获单次 token 不能重放(借鉴 Subsonic)

### P1 已沉淀

6. **manifest schema 增强** — `prisIrFeatures` + `prisIrPlatforms` + 5 步法(本文档)
7. **Open API 默认关**(参见 P1-4)
8. **zero-knowledge** — 服务端不存密码哈希,只验证一次性 token

### P2 待落地

9. **Subsonic 抽象层** — `extensions/_scaffold/subsonic-client.js`(未来 Phase C)
10. **mutating 红线** — Phase B 写接口**必须**用户逐条勾,绝不自动调

---

## 6. 已知坑

| 坑 | 修复 |
|---|---|
| NTFS 大小写折叠 + git add 静默失败 | `git add` 用精确 case,见 [memory](../memory/ntfs-case-folding-git-add-fail.md) |
| `node_modules` gitignore 但测试需要 | `npm install` 后再跑测试,扩展不 ship `node_modules` |
| vm sandbox 里 `process` 默认 undefined | 必须显式 `sandbox.process = stubProcess`,且 stubProcess.env 在 vm.createContext 之前赋值 |
| Node 24+ 内置 `node:sqlite` | 无需 better-sqlite3 依赖,直接 `require('node:sqlite')` |
| Subsonic 协议 mock 必须用客户端发的盐 | 硬盐掩盖客户端鉴权 bug,E2E 必须按真实协议模拟 |

---

## 7. 实战模板(扩展做 Phase A 时直接套)

### 7.1 HTTP 桥模板(anonymous)

参考 `extensions/lx-music-bridge-status/` — 改 endpoint + JSON 解析即可,核心 6 处不变:

```js
function baseUrl() { return process.env.PRISIR_<EXT>_URL || 'http://127.0.0.1:<PORT>'; }
function httpGet(path) { /* ... GET + JSON.parse + {ok, status, parsed, body, url, error} */ }
function fetchXxx() { /* 解析 + 返 {ok, alive, ...} */ }
```

### 7.2 HTTP 桥模板(鉴权)

参考 `extensions/navidrome-bridge-status/` — 加 5 字段 env + token 派生 + 3 层失败语义:

```js
function makeToken(password) { /* md5(password + randomSalt) */ }
function httpGetSubsonic({password, preToken, preSalt, endpoint}) { /* ... */ }
function parseSubsonic(r) { /* 3 层失败语义 */ }
```

### 7.3 SQLite 索引模板

参考 `extensions/calibre-metadata-indexer/` — 改表名 / LIKE escape 即可,核心 4 处不变:

```js
function dbPath() { return process.env.PRISIR_<EXT>_DB || '<DEFAULT_PATH>'; }
function open() { return new DatabaseSync(`file:${dbPath()}?mode=ro`, { readOnly: true }); }
```

---

## 8. 参考

- [v2 对比研究 530 行调研](../memory/lx-music-like-software-comparison.md)
- [LX Music ship](../LX/../memory/lx-music-bridge-phase-a-shipped.md)
- [SiYuan vault ship](../memory/siyuan-vault-indexer-phase-a-shipped.md)
- [Calibre ship](../memory/calibre-metadata-indexer-phase-a-shipped.md)
- [YesPlayMusic ship](../memory/yesplaymusic-bridge-status-phase-a-shipped.md)
- [Navidrome ship](../memory/navidrome-bridge-status-phase-a-shipped.md)
- [扩展 API v0.1 文档](prisIr-extension-api-v0.1.md)
- [P3.10b 0 上传红线](../memory/p3-10-bubble-cancelled-privacy.md)