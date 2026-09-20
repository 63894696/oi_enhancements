# PrisirAI 扩展 API v0.1 — 协议 + SDK + HTTP 端点参考

> **状态**:Phase 2(2026-09-20)正式发布。Phase 1 已 ship(ext-mermaid 示例扩展)。
> **配套**:[[prisIr-extension-roadmap-2026-09-20]] 路线 + [[prisIr-extension-ui-mock-2026-09-20]] B 形态 UI + [[prisIr-ext-phase1-shipped]] 落地实录。

---

## 0. 概述

PrisirAI 扩展是一个 Node.js 子进程,通过 **stdio NDJSON JSON-RPC** 跟主进程通信。

```
┌──────────────────────────────────────────────────────────────┐
│              PrisirAI 主进程 (Python)                         │
│   ThreadingHTTPServer 18802                                  │
│   SQLite chat.sqlite3 + ~/.prisir/extensions/installed.json │
│   _EXTENSIONS 注册表 + _ext_procs 子进程表                   │
└────────────────────┬─────────────────────────────────────────┘
                     │ stdio (每行一个 JSON 对象,UTF-8)
                     │
              ┌──────┴──────┐
              ▼             ▼
     ┌─────────────┐ ┌─────────────┐
     │ ext-mermaid │ │ 你的扩展     │
     │  node 子进程 │ │  node 子进程 │
     └─────────────┘ └─────────────┘
```

**协议特性**:
- 1 行 = 1 个 JSON 对象(尾随 `\n`)
- 不压缩(避免调试负担)
- 启动时主进程发 `initialize` 通知握手
- 扩展主动可调 `ui.inject` 推卡片 / `log` 打日志 / `invoke` 同/异步入参
- 命令注册 → 主进程可通过 HTTP `op=invoke` 触发
- 钩子订阅 → 主进程推 `event` 通知(session.message / config.changed / ...)

---

## 1. JSON-RPC 2.0 消息格式

### 1.1 主进程 → 扩展(request + notification)

```json
{"jsonrpc":"2.0","id":1,"method":"render.mermaid","params":{"code":"graph LR;A-->B"}}
{"jsonrpc":"2.0","method":"event","params":{"type":"session.message","session_id":"abc","text":"..."}}
{"jsonrpc":"2.0","method":"config.changed","params":{"settings":{"theme":"dark"}}}
```

### 1.2 扩展 → 主进程(response + notification)

```json
{"jsonrpc":"2.0","id":1,"result":{"html":"<svg>...</svg>","type":"card"}}
{"jsonrpc":"2.0","method":"ui.inject","params":{"session_id":"abc","card_id":"m-1","html":"<svg>...</svg>"}}
{"jsonrpc":"2.0","method":"log","params":{"level":"info","msg":"rendered svg 1.2KB"}}
```

### 1.3 错误响应

```json
{"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"code empty"}}
```

---

## 2. SDK API(`@prisir/extension-sdk`)

### 2.1 安装(本地路径)

```bash
cd extensions/my-ext
npm install ../sdk
```

或 `package.json` 加 `"@prisir/extension-sdk": "file:../sdk"`。

### 2.2 入口文件模板(`index.js`)

```javascript
const { PrisIrExt } = require('@prisir/extension-sdk');

const ext = new PrisIrExt({
  id: 'my-ext',              // 必须,小写字母/数字/-/_
  name: '我的扩展',            // 显示名
  version: '0.1.0',
});

// 注册命令(主进程可调,AI 也可调)
ext.registerCommand('say.hello', async (args, ctx) => {
  return { message: `hello, ${args.name || 'world'}!` };
});

// 注册会话消息钩子(AI 输出文本时触发)
ext.onSessionMessage(async (msg, ctx) => {
  console.log('AI said:', msg.text);
  if (msg.text.includes('画图')) {
    ctx.ext.injectCard({
      sessionId: ctx.sessionId,
      html: '<div>🎨 触发画图</div>',
    });
  }
});

// 启动 stdio JSON-RPC
ext.start();
```

### 2.3 PrisIrExt 类方法

| 方法 | 用途 | 返回 |
|------|------|------|
| `registerCommand(method, handler)` | 注册命令;handler 签名 `(args, ctx) => result\|Promise` | `this`(链式) |
| `registerPanel(id, def)` | 注册抽屉面板(Phase 2 新增) | `this` |
| `registerFileProvider(scheme, handler)` | 注册 `prisir-ext://<scheme>/path` 虚拟文件 | `this` |
| `onSessionMessage(fn)` | 订阅 AI 输出消息 | `this` |
| `onSessionStart(fn)` | 订阅会话开始 | `this` |
| `onSessionEnd(fn)` | 订阅会话结束 | `this` |
| `injectCard({sessionId, cardId, html, type})` | 主动推卡片到主会话 | void |
| `log(level, msg)` | 打日志到主进程 log | void |
| `start()` | 启动 readline + signal handler | Promise |

### 2.4 上下文对象 `ctx`

```typescript
ctx = {
  sessionId: string,       // 当前会话 ID(从主进程推过来)
  ext: PrisIrExt,          // 自身引用(便于递归调用)
  // 后续 Phase 2+ 会扩展:user, workdir, history, ...
}
```

### 2.5 设置变更监听

```javascript
ext.registerCommand('config.demo', async (args, ctx) => {
  const settings = args.settings || {};   // 主进程推过来的当前 settings
  return { using: settings.theme || 'default' };
});
```

主进程修改 settings → 主进程 POST `/api/extensions?op=config` → 主进程推 `config.changed` 通知 → SDK 自动写到 `ext._settings`(开发者可读)。

---

## 3. 主进程 HTTP API

所有端点前缀 `/prisiragent/api/extensions`。

### 3.1 `GET /extensions` 列表

**响应**:
```json
{
  "ok": true,
  "extensions": [
    {
      "id": "mermaid",
      "name": "设计稿渲染",
      "version": "0.1.0",
      "desc": "在 AI 对话里检测 mermaid fenced block → 渲染 SVG",
      "author": "PrisirAI 官方",
      "enabled": true,
      "running": true,
      "settings": {"theme": "auto"},
      "permissions": ["ai.invoke.command:render.mermaid", "ui.inject.card"],
      "installed_at": 1789839333.0
    }
  ]
}
```

### 3.2 `POST /extensions` op 分发

#### op=list(同 GET)

#### op=install

**请求**:
```json
{
  "op": "install",
  "id": "mermaid",
  "path": "C:/path/to/ext",                              // 本地路径(开发模式)
  "url": "https://github.com/.../mermaid-0.1.0.tgz",      // Phase 2.3 商店路径(可 file:// 或 https://)
  "sha256": "920c62dd4b873825df72ac631949677cf532f147"    // 可选但强烈推荐:下载后立即校验
}
```

**URL 流程(Phase 2.3+)**:
1. 下载 tarball(.tgz/.tar.gz/.tar,Phase 2.4 接 .zip)
2. SHA256 校验(若提供)— 不匹配 → 400 拒绝
3. 解压到 `~/.prisir/extensions/<id>/`,自动 strip 顶层目录(`ext-mermaid/` / `package/`)
4. 自动 provision SDK:`node_modules/@prisir/extension-sdk` 软链到本仓 `extensions/sdk/`
5. 读 `package.json` 拿 name/displayName/version/permissions,写入注册表

**path 流程(开发)**:
- `path` 存在 + 是目录
- 目录里必须有 `index.js` 或 `main.js` 入口
- `package.json` 读 `name` / `displayName` / `version` / `description` / `author` / `prisIrPermissions`

**响应**(成功):
```json
{"ok": true, "extension": {...}, "sha256": "...", "size": 5771}
```

**响应**(SHA256 不匹配):
```json
{"ok": false, "error": "sha256 mismatch: got 920c62dd... expected 00000000..."}
```

**响应**(URL 404):
```json
{"ok": false, "error": "download failed: [Errno 2] No such file or directory: ..."}
```

#### op=uninstall

**请求**: `{"op":"uninstall","id":"mermaid","remove_files":false}`
**行为**:Phase 1/2 默认保留目录(避免误删),设 `remove_files:true` 才删 `~/.prisir/extensions/<id>/`

#### op=enable / op=disable

**请求**: `{"op":"enable","id":"mermaid"}`
**行为**:enable → spawn Node 子进程;disable → SIGTERM → SIGKILL 兜底

#### op=config

**请求**:
```json
{
  "op": "config",
  "id": "mermaid",
  "settings": {"theme": "dark", "format": "svg"}
}
```

**行为**:更新 `installed.json` 的 settings,推 `config.changed` 通知给子进程。

#### op=invoke

**请求**:
```json
{
  "op": "invoke",
  "id": "mermaid",
  "method": "render.mermaid",
  "params": {"code": "graph LR;A-->B", "title": "流程"},
  "timeout": 10
}
```

**响应**(成功):
```json
{"ok": true, "result": {"html": "<svg>...</svg>", "type": "card"}}
```

**响应**(失败):
```json
{"ok": false, "error": "rpc timeout or failed"}
```

#### op=browse(Phase 2.3 商店雏形)

**请求**:
```json
{"op":"browse"}
{"op":"browse","q":"mermaid"}              // 大小写不敏感,匹配 id/name/desc/tags
{"op":"browse","page":2,"per_page":10}
{"op":"browse","force":true}                // 强制刷新缓存(开发用)
```

**数据源**:
1. 环境变量 `PRISIR_EXT_STORE_URL` 显式覆盖(测试用)
2. 默认 `file:///<repo>/extensions/_store/index.json`(离线开发)
3. Phase 3 起指向 `https://raw.githubusercontent.com/PrisirAI/extensions/main/index.json`

**缓存**:1h 内存,失败回退最近一次成功快照(stale=true)

**响应**:
```json
{
  "ok": true,
  "items": [
    {
      "id": "mermaid",
      "name": "设计稿渲染",
      "desc": "...",
      "long_desc": "完整 markdown 描述(详情页用)",
      "version": "0.1.0",
      "tarball_url": "https://github.com/.../mermaid-0.1.0.tgz",
      "sha256": "920c62dd4b873825...",
      "size_kb": 6,
      "author": "PrisirAI 官方",
      "tags": ["diagram","svg","official"],
      "stars": 12,
      "downloads": 240,
      "updated": "2026-09-20",
      "installed": true,            // 是否在本机已装
      "enabled": true,              // 是否已启用
      "permissions": ["..."]
    }
  ],
  "total": 4,
  "page": 1,
  "per_page": 20,
  "cached_at": 1789871945.0,
  "ttl": 3600,
  "url": "file:///.../extensions/_store/index.json",
  "stale": false,
  "version": 1,
  "updated_at": "2026-09-20"
}
```

---

## 4. 协议错误码

| code | 含义 |
|------|------|
| -1 | 通用错误(handler 抛异常) |
| -32601 | Method not found(扩展注册的命令不存在) |
| -32602 | Invalid params(JSON 解析失败) |
| -32603 | Internal error(子进程崩) |

---

## 5. 扩展 `package.json` 规范

```json
{
  "name": "my-ext",
  "displayName": "我的扩展",        // 可选,优先于 name
  "version": "0.1.0",
  "description": "一句话说明",
  "author": "Your Name <email>",
  "main": "index.js",                // 或 "main.js"
  "type": "commonjs",                // Phase 1 只支持 CJS,Phase 2 接 ESM
  "license": "MIT",
  "prisIrPermissions": [              // 必填,白名单制
    "ai.invoke.command:my.command",
    "ui.inject.card",
    "file.read.workdir",
    "network.https"
  ],
  "dependencies": {
    "@prisir/extension-sdk": "file:../sdk"
  }
}
```

### 5.1 权限命名空间(Phase 2 雏形)

| 权限 | 含义 |
|------|------|
| `ai.invoke.command:<name>` | AI 可调用指定命令 |
| `ui.inject.card` | 可推卡片到主对话 |
| `ui.panel:<id>` | 可注册抽屉面板 |
| `file.read.workdir` | 读 workdir 文件 |
| `file.read.ext` | 读自己扩展目录 |
| `network.https` | 出站 HTTPS |
| `shell.exec` | 执行 shell(默认禁用,需用户白名单) |

### 5.2 入口文件要求

- `index.js` 或 `main.js`(优先 index.js)
- 必须 `require('@prisir/extension-sdk')` 并 `await ext.start()`
- CommonJS(`type: "commonjs"`),Phase 2 末尾接 ESM

---

## 6. 进程模型与生命周期

### 6.1 启动顺序

```
1. 主进程 spawn `node <entry>` → 子进程就绪
2. 主进程发 initialize 通知(握手)
3. 扩展 start() 启动 readline pump
4. 子进程可立即开始接收 command / event
5. 扩展可主动 injectCard / log
```

### 6.2 优雅退出

```
disable → 主进程 SIGTERM → 2s 后 SIGKILL → 子进程 exit
```

### 6.3 超时

- 单次 invoke 默认 10s(`_EXT_RPC_TIMEOUT`),可传 `timeout` 覆盖
- 子进程 stdout 读不到一行 → 主进程标 `running: false`(下次 list 体现)
- 子进程 stderr 自动转发到主进程 log,前缀 `[ext:<id>]`

---

## 7. 安全红线(必须遵守)

1. **白名单 npm 包**:v0.1 禁止引用非 `@prisir/*` 包(避免供应链);Phase 3 放开白名单
2. **不写 workdir 外**:扩展 `file.read.workdir` 必须 realpath 校验
3. **不修改主进程状态**:子进程崩溃不污染主进程;重启即可恢复
4. **不联网默认**:Phase 1 不允许出站;`network.https` 权限要用户授权
5. **不收集用户数据**:权限声明要写清楚用途,扩展 estop(Phase 4)可一键停所有扩展
6. **签名验证**:Phase 3 起商店扩展必须 HMAC 签名(初版 GitHub Secrets 出公钥)
7. **资源限制**:v0.1 不限 CPU/内存(本地信任);Phase 3 接 `prisir_coworker` 沙箱

---

## 8. 示例:完整扩展代码

完整示例见 `extensions/ext-mermaid/` (~350 行)。最小骨架:

```javascript
// extensions/my-ext/package.json
{
  "name": "my-ext",
  "displayName": "我的扩展",
  "version": "0.1.0",
  "description": "打招呼",
  "main": "index.js",
  "prisIrPermissions": ["ai.invoke.command:say.hello"]
}

// extensions/my-ext/index.js
const { PrisIrExt } = require('@prisir/extension-sdk');
const ext = new PrisIrExt({ id: 'my-ext', name: '我的扩展', version: '0.1.0' });

ext.registerCommand('say.hello', async (args) => {
  return { message: `Hello, ${args.name || 'world'}!` };
});

ext.start();
```

测试:
```bash
# 1. 注册
curl -X POST http://127.0.0.1:18802/prisiragent/api/extensions \
  -H 'Content-Type: application/json' \
  -d '{"op":"install","id":"my-ext","path":"C:/Users/.../my-ext"}'

# 2. 启用
curl -X POST .../extensions -H 'Content-Type: application/json' \
  -d '{"op":"enable","id":"my-ext"}'

# 3. invoke
curl -X POST .../extensions -H 'Content-Type: application/json' \
  -d '{"op":"invoke","id":"my-ext","method":"say.hello","params":{"name":"Prisir"}}'
# → {"ok":true,"result":{"message":"Hello, Prisir!"}}
```

---

## 9. 调试技巧

### 9.1 单独跑扩展

```bash
cd extensions/my-ext
# 模拟主进程发请求(stdin 喂 JSON)
echo '{"jsonrpc":"2.0","id":1,"method":"say.hello","params":{"name":"test"}}' | node index.js
# 应输出 {"jsonrpc":"2.0","id":1,"result":{...}}
```

### 9.2 看主进程日志

主进程 log 文件 `D:/Temp/prisir_web.log`,扩展 stdout/stderr 转发前缀 `[ext:<id>]`:
```
[ext:mermaid] starting
[ext:mermaid] initialized (main session=...)
[ext:mermaid] detected 1 mermaid block(s)
```

### 9.3 DevTools 断点(Phase 2 末尾)

`node --inspect=0.0.0.0:9229 index.js` → Chrome `chrome://inspect` 接进去。
Phase 1 不支持 inspect(主进程 Popen 没传 inspect flag)。

---

## 10. 版本兼容

- **API v0.1**(当前):stdio NDJSON,字段 `params` / `result`,权限白名单
- **v0.2**(计划):ESM 支持 + WebSocket transport(浏览器内扩展)+ 扩展 estop
- **v1.0**(目标):签名强制 + 商店审计 + 资源沙箱

**主进程对扩展的兼容性**:
- 老 SDK 跑新主进程:可用(主进程是兼容层)
- 新 SDK 跑老主进程:**可能不工作**(SDK 可能调用 v0.2+ 字段)
- 推荐:扩展锁定 SDK 版本 `"@prisir/extension-sdk": "0.1.x"`

---

## 11. 相关

- [[prisIr-extension-roadmap-2026-09-20]] — 路线 + 5 阶段计划
- [[prisIr-ext-phase1-shipped]] — Phase 1 落地实录(含 6 个 bug)
- [[prisIr-extension-ui-mock-2026-09-20]] — B 形态抽屉 UI
- [[prisir-diagram-design-integration]] — 品牌令牌来源(扩展复用)
- `extensions/sdk/index.js` — SDK 源码(~200 行)
- `extensions/ext-mermaid/index.js` — 完整示例扩展(~350 行)
- `docs/prisir-extension-author-guide.md`(Phase 2 末尾新增) — 扩展作者写扩展的 checklist
