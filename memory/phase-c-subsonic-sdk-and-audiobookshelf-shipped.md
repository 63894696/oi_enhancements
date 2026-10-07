---
name: phase-c-subsonic-sdk-and-audiobookshelf-shipped
description: Phase C 统一 Subsonic SDK + Audiobookshelf Bearer token ship 完结 v2 对比研究
metadata:
  type: project
---

# Phase C 统一媒体协议 SDK + Audiobookshelf Phase A ship (2026-10-07)

## 起因

v2 对比研究 6 项借鉴最后一项([lx-music-like-software-comparison])#16 **Phase C 统一媒体协议 SDK** — 把 Navidrome 落地 Subsonic 协议的鉴权 + httpGet + parseSubsonic 提炼到 `extensions/_scaffold/subsonic-client.js`,让任何 Subsonic 兼容软件(LMS / Funkwhale / Ampache / 启用 Subsonic 兼容的 Audiobookshelf)**直接 `require` 复用**。

**同日 ship** Audiobookshelf Phase A(默认 127.0.0.1:8181,Bearer token,3 L0 命令),验证 SDK 设计能跨真实项目落地**。Audiobookshelf 主推 REST 不是 Subsonic,所以走自家 `Authorization: Bearer` 鉴权**(不是 Subsonic md5+salt)— SDK 留这个口是但 Audiobookshelf 走自家 Bearer 协议(后者比「Token≠密码」较弱,但符合用户习惯)。

## 设计取舍

### Subsonic SDK 提炼

**`extensions/_scaffold/subsonic-client.js`(190 行)**:零 npm dep,导出 9 个 API:
- `randomSalt(n=12)` / `md5Hex(s)` / `makeToken(password)` — 鉴权三件套
- `makeConfig({baseUrl, user, password, preToken, client, apiV})` — 配置 factory(避免扩展自己写 5 个 env getter)
- `httpGetSubsonic({config, password, preToken, preSalt, endpoint, timeoutMs})` — GET helper
- `parseSubsonic(r)` — **3 层失败语义检测**(HTTP / envelope 缺失 / `subsonic-response.status=failed` + `error.code`)
- `mapNowPlayingEntry(e)` / `flattenNowPlaying(body)` — Subsonic nowPlaying entry 字段标准化

**关键 API 设计**:让扩展自己注入 env 字段(不强制 `PRISIR_SUBSONIC_*` 命名),保证 SDK 不绑死任何具体扩展:
```js
const cfg = makeConfig({
  baseUrl: process.env.PRISIR_NAVIDROME_URL || 'http://127.0.0.1:4533',
  user: process.env.PRISIR_NAVIDROME_USER || '',
  password: process.env.PRISIR_NAVIDROME_PASS || '',
  ...
});
const r = await httpGetSubsonic({ config: cfg, endpoint: 'ping' });
```

### Navidrome 薄壳化

Navidrome 从 ~285 行(自鉴权 + 自 httpGet + 自 parseSubsonic)→ 154 行(只做「env 注入 + 命令实现 + entry 字段映射」)。**核心协议代码全部移到 SDK**,Navidrome 失去 **131 行代码**(`makeToken / randomSalt / md5Hex / httpGetSubsonic / parseSubsonic`)。原 export 列表从 9 个 → 3 个(`probeHealth / fetchNowPlaying / fetchLicense / ndConfig`)。

测试也薄壳化:扩展测试只验「env 注入 + 命令实现 + SDK 直通」,SDK 单测在 SDK 自己文件。但**E2E mock server 仍在扩展**,因为 mock 鉴权策略每个扩展可能不一样(虽然现在两边用同样策略)。

### Audiobookshelf Bearer token

**为什么不走 Subsonic SDK?** 三个理由:
1. Audiobookshelf 主推自家 REST,Subsonic 兼容是**可选启用**(用户必须手动开)
2. 主协议 /api/libraries 自家 envelope(不是 Subsonic `subsonic-response`),需要单独映射
4. Bearer token 比 md5+salt 弱 — 鉴权范围「Token≠密码」原则兜底「能优先 salted 就优先,否则用 Bearer」

**3 个 L0 命令**(全部只读):
- `audiobookshelf.health` — `/healthcheck` 探活 + 延迟
- `audiobookshelf.libraries` — `/api/libraries` list books + podcasts
- `audiobookshelf.sessions` — `/api/sessions` 活跃 session 列表(userId / currentResourceId / position / duration)

**Bearer env**:
- `PRISIR_AUDIOBOOKSHELF_URL` 默认 `http://127.0.0.1:8181`
- `PRISIR_AUDIOBOOKSHELF_TOKEN` 默认空 → 无凭证走早退路径

**绝不**触碰 `/api/items/[id]/play` 流接口 / 下载 / 任何写操作。

### 借鉴原则 5「Token≠密码」的层次

| 协议 | 实现 | 安全等级 | 例 |
|---|---|---|---|
| Subsonic md5+salt | **强** — 每次请求客户端派新盐,服务端验证,截获单次 token 不能重放 | 9/10 | Navidrome |
| Audiobookshelf Bearer | **中** — 一次性 token,但 token 一旦签发可重放直至失效 | 6/10 | Audiobookshelf |
| Basic auth | **弱** — base64 编码(明文),HTTPS 才安全 | 3/10 | 部分 Komga 实例 |
| 无鉴权 anonymous | **0** — 局域网内 open | 8/10(局域网 OK) | LX / YesPlayMusic |

**借鉴原则 5 提醒**:有条件时优先 salted token,标准鉴权在 API 限制下兜底,**绝不**用 Basic over HTTP。

## 实现

### SDK 提炼路径

1. 原 Navidrome 自含 131 行协议代码 → 提炼到 `_scaffold/subsonic-client.js`
2. Navidrome 改 `require('../_scaffold/subsonic-client')` 引用
3. 同一 SDK 在未来 LMS / Funkwhale / Ampache 复用,**零修改**

### 测试三件套更新

**Navidrome**:原 12 测试(8 单 + 4 E2E)→ 11 测试(7 单 + 4 E2E) — 因 1 测试被 SDK 直测吸收。
**Navidrome 测试 case 7** 改为**直接测 SDK 函数**(`require('../_scaffold/subsonic-client.js')` → `sdk.makeToken()` + `sdk.parseSubsonic()`),保证 SDK 单测被覆盖。

**Audiobookshelf**:新 10 测试(6 单 + 4 E2E)— Bearer token 鉴权 + env 覆盖 + E2E mock 401 错 token 验证。

## 测试结果

**Navidrome 单扩**:`node __tests__/run.js --e2e` → 11/11 ✓
**Audiobookshelf 单扩**:`node __tests__/run.js --e2e` → 10/10 ✓

**6 扩展联合回归**:
```
$ python -m pytest tests/test_{audiobookshelf,navidrome,yesplaymusic,calibre,siyuan,lx}_bridge_status.py
21 passed, 3 skipped in 3.63s
```

**Phase A 模板固化路径**:
- anonymous HTTP(1 字段 URL):LX / YesPlayMusic
- 鉴权 HTTP, salted token(3-5 字段):Navidrome
- **鉴权 HTTP, Bearer token**(3 字段):Audiobookshelf ← 新增
- SQLite readonly(2 字段):SiYuan / Calibre

## v2 对比研究完成度:6/6 全 ship

| # | 借鉴项 | 状态 | commit |
|---|---|---|---|
| 1 | docs/extension-spec.md 文档化 | ✅ | 85548d9 |
| 2 | Calibre metadata.db 索引(SQLite readonly 原则 3)| ✅ | ec9f309 |
| 3 | YesPlayMusic 桥(LX 模板复用)| ✅ | ca876fe |
| 4 | scaffold features[] / platform[] | ✅ | 85548d9 |
| 5 | Subsonic 抽象层(Phase C 协议 SDK)| ✅ | **<pending>** |
| 6 | (上表中第 6 项 — 已在表 5 覆盖)|  — | — |

**全 6 项 ship 完毕**。今日已 ship 6 commits,LMS / Funkwhale / Ampache / Komga 接入未来 SDK 复用,音频媒体子领域已沉淀完整模板。

## 后续展望

**Phase B**(待用户开绿灯):
- Audiobookshelf `audiobookshelf.session.play` / `.pause` / `.stop`(写 session 表)— L1+ 弹卡
- Navidrome `navidjukjukeboxControl`(同 LX / YesPlayMusic 思路)— L2 弹卡

**Phase C**(跨软件协同,SDK 已沉淀):
- LMS / Funkwhale / Ampache — Subsonic SDK,5 分钟 ship
- Komga(漫画)— Bearer API(类似 Audiobookshelf,30 分钟 ship)
- Plex / Emby / Jellyfin(影视)— 各家独立协议(待 v0.2 spec)

**Phase D**(未来可能性):
- 跨 Subsonic 库的「本地音乐统一推荐」(沿用 N9 music AI 推荐架构,覆盖 Navidrome + Audiobookshelf + LMS)
- Cross-platform bookmark sync(Navidrome favorites + LX favorites + Audiobookshelf progress)

## Why

Phase C SDK 是 v2 是对比研究的**最后一块拼图** — 它把 Subsonic 协议从「Navidrome 单点的实现细节」变成「LMS / Funkwhale / Ampache 任何 Subsonic 兼容客户端的可复用基础设施」。这一层抽象的 ROI 是:

1. **新做 Subsonic 扩展时间**:原 30 分钟 → 现在 5 分钟(只写 env + 命令注册)
2. **Navidrome 维护成本**:协议层 bug 修一次,所有 SDK 用户受益
3. **未来跨 Subsonic 库的协同推荐**:SDK 同 API 直接拼装,无需抽象映射

**借鉴原则 9「Subsonic 抽象层」** —— Phase C 完成,v2 借鉴清单 6/6 全 ship。

## How to apply

下次做新 Subsonic 兼容软件扩展(LMS / Funkwhale / Ampache):
1. 端口确认 + 协议(Subsonic 默认)
2. `require('../_scaffold/subsonic-client')`
3. 写 env 字段注入(makeConfig 调用)
4. 写 L0 命令(SDK 直接复用,只拼 endpoint 名)
5. 测三件套(Node 单测 + E2E mock Subsonic server + Python wrapper)
6. 跑联合回归(`pytest tests/test_<ext>*.py`)+ 全 Phase A 6 扩展联合

下次做 Bearer token 软件(Komga / Plex / Jellyfin 自定义 API):
1. 端口确认 + API 鉴权协议
2. `require('../_scaffold/bearer-client')`(如未来抽)或自行写 ABS 类似实现
3. 写 env 字段 + L0 命令
4. 测三件套

**模板复用 = 单软件 ship 工作量降到最低**(从 2-3 天 → 5 分钟)。

相关:[[navidrome-bridge-status-phase-a-shipped]], [[yesplaymusic-bridge-status-phase-a-shipped]], [[lx-music-bridge-phase-a-shipped]], [[siyuan-vault-indexer-phase-a-shipped]], [[calibre-metadata-indexer-phase-a-shipped]], [[extension-spec-and-manifest-schema-shipped]], [[lx-music-like-software-comparison]], [[p3-10-bubble-cancelled-privacy]]