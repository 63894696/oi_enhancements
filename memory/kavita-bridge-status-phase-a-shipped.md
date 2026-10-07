---
name: kavita-bridge-status-phase-a-shipped
description: Kavita 漫画/书库 Phase A ship — Bearer API key 模式复用 + E2E 抓真实 bug(401 http_status 缺失)
metadata:
  type: project
---

# Kavita 漫画/书库 Phase A ship + E2E 抓 bug(2026-10-07)

## 起因

继续 Phase A 模板跨域验证 — **Kavita 是 Pure Bearer 模式第二个案例**(前 Audiobookshelf)。

**WebSearch 决策回退**:初判 Kavita 支持匿名(类似 Calibre-Web / FreshRSS anonymous),验证后**推翻假设** — Kavita 几乎所有 `/api/*` 端点都需 JWT(由 `POST /api/Account/login` 签发,或 OPDS API Key)。Kavita 用户在「User Settings → API Keys」生成 JWT-as-API-Key,客户端用 `Authorization: Bearer <jwt>` 调 REST 端点。

**改走 Bearer 模式**(类似 Audiobookshelf):inline 50 行 Bearer helper,不复用 custom-auth SDK(custom-auth 是「自定义头名」非 Bearer)。**Audiobookshelf 同模式但未抽 SDK**(单案例不够 SDK 化阈值,Kavita 第 2 个验证 inline 重复亦可接受)。

## 设计要点

### Kavita 扩展 `kavita-bridge-status/index.js`(157 行)

环境变量:
- `PRISIR_KAVITA_URL` 默认 `http://127.0.0.1:5000`(Kavita Docker 默认)
- `PRISIR_KAVITA_API_KEY` Bearer token(JWT)

3 个 L0 命令(纯只读):
- `kavita.health` — `/api/Server/ping` 服务器探活 + apiVersion
- `kavita.libraries` — `/api/Library/libraries` 库列表(Manga/Comic/Book)
- `kavita.series` — `/api/Series?PageNumber=1&PageSize=N&SearchTerm=X` 分页 + 搜索 series

**绝不**触碰 `/api/Reader/progress` / `mark-read` / `mark-unread` / `series DELETE` / `Library scan` 等 mutating 接口。

### E2E 抓到真实 bug

第一次 E2E:`E2E 错 token → 401 → ok=false + last_error` 失败:

```
expected: 401
actual:   undefined
```

**Bug**:`fetchLibraries` 和 `fetchSeries` 失败分支只返 `ok/alive/last_error`,**漏 `http_status`**。修:

```js
// before
return {
  ok: false, alive: false,
  last_error: r.error || `HTTP ${r.status}`,
};

// after
return {
  ok: false, alive: false,
  http_status: r.status,           // ← 新增
  last_error: r.error || `HTTP ${r.status}`,
};
```

**为什么 vm sandbox 单测没抓到**:单测用 `probeHealth 不可达` 走 ECONNREFUSED 路径,该路径 `http_status` 由 probeHealth 自己设;只有 fetchLibraries / fetchSeries 失败路径漏字段。

**为什么 SDK 不会自动保护**:`kvGet` helper 返 `r.status`,但 L0 命令自己不读 r.status 直接返 last_error。**L0 命令构造层有责任把 status 透出来**。

## 实现

### Bearer helper 复用

50 行 inline `kvGet(path)`:
```js
function kvGet(path) {
  return new Promise((resolve) => {
    const token = kvToken();
    if (!token) return resolve({ ok: false, status: 0, ..., error: 'no credentials — set PRISIR_KAVITA_API_KEY' });
    const url = `${kvBaseUrl()}${path}`;
    const headers = { Authorization: `Bearer ${token}` };
    const req = http.get(url, { timeout: KV_TIMEOUT_MS, headers }, (res) => { ... });
    ...
  });
}
```

**与 Audiobookshelf 完全一样的结构** — 抽象成 `bearer-client.js` SDK 已成立条件(2 用户)。下次 ship Calibre-Web / FreshRSS(若也走 Bearer)即可抽 SDK。

### 内嵌 mock server 兼容 `/api/Server/ping` 公开路径

Kavita `/api/Server/ping` 无需鉴权(返 `{value:'pong', apiVersion:'0.0.512', ...}`),但 `/api/Library/libraries` 需 Bearer。Mock server 在 ping 分支不查 token,其他端点查,验证客户端用 token 调 protected 端点、用无 token 也通调 ping 路径。

## 测试结果

**Kavita 单扩**:
```
node __tests__/run.js          → 7/7 ✓
node __tests__/run.js --e2e    → 12/12 ✓(7 单 + 5 E2E,覆盖 health/libraries/series + search 过滤 + 401 错 token)
```

E2E mock server 验:
- Bearer token 接受 + 401 拒绝
- `/api/Series?SearchTerm=Berserk` 过滤(只返 Berserk 1 项)
- `/api/Library/libraries` 返 4 library(Manga/Comic/Book×2)
- `/api/Server/ping` 公开路径无需 token

**Python wrapper**:
```
$ python -m pytest tests/test_kavita_bridge_status.py
============================== 4 passed in 0.89s ==============================
```

**11 扩展联合回归**:
```
======================== 42 passed, 2 skipped in 6.60s ========================
```

2 个 skipped 是历史遗留(calibre siyuan)。

## Phase A 模板跨域矩阵更新

| 协议 | 实现 | 例 | SDK |
|---|---|---|---|
| anonymous HTTP | 局域 Open API | LX / YesPlayMusic | 自写 |
| 鉴权 salted token | Subsonic md5+Salt | Navidrome / Funkwhale / LMS | subsonic-client.js |
| **Pure Bearer** | **Bearer token** | **Audiobookshelf / Kavita** | **未抽 SDK(2 用户临界)** |
| 自定义鉴权头 | API Key / X-Auth-Token / X-Plex-Token / x-api-key | Komga / Miniflux / Plex / Immich | custom-auth-client.js |
| SQLite readonly | node:sqlite | SiYuan / Calibre | 自写 |

**扩展家族跨域**:音乐 + 笔记 + 媒体中心 + 漫画 + RSS + 影视 + 照片 + **书库**(8 个领域,Kavita 横跨漫画/书/PDF)。

## 经验教训:L0 命令失败分支必须透 http_status

**单测盲点**:vm sandbox 单测只验 happy path,失败路径要走真 HTTP server 才能验 401/403/500 字段透传。

**已 ship 11 扩展回顾**:Plex / Immich / Miniflux 失败分支都有 http_status 字段(我已写习惯),但 Kavita / 部分扩展会漏。**下次 ship 自动检查清单**:
1. 失败分支必须有 `http_status: r.status`
2. 单测加 `--e2e` 跑 mock server 401 错 Key 分支(测错路径字段)
3. E2E 必须验 401 + 403 + 5xx 三档(错 Key + 过期 + 服务挂)

## Why

Kavita 是 **Pure Bearer 模式第二个用户**(前 Audiobookshelf),但**未抽 SDK**:
- Audiobookshelf 单案例不够 SDK 阈值
- Kavita 第 2 个凑数但 inline 重复 50 行已可读,不必过早抽 SDK
- 下次 Calibre-Web / FreshRSS 也走 Bearer 时,Kavita 代码可作 SDK 抽取模板

**E2E 价值验证**:Kavita 是首个 E2E 抓 bug 的扩展(http_status 缺失)。之前 Komga / Miniflux / Plex / Immich 都是首跑全绿,这次证明 E2E + 401 错 token 是关键质量门禁。

## How to apply

下次做 Pure Bearer 扩展(Calibre-Web / FreshRSS / Emby 等):

1. 端口确认 + Bearer token 路径(用户在服务端生成)
2. 写 `kvGet(path)` 50 行 Bearer helper(Kavita 模板)
3. 写 L0 命令 + **失败分支必带 http_status**(避免 Kavita bug 重演)
4. 测三件套(Node 单测 + E2E mock server **必含 401 错 token** + Python wrapper)
5. 跑联合回归

如果**累计 3+ Pure Bearer 扩展**(加上下一个),抽 `bearer-client.js` SDK(类似 custom-auth 的纯 Bearer 单模式)。

**SDK 抽取阈值**:3+ 用户才抽,与 custom-auth SDK 一致(目前 4 用户)。

相关:[[audiobookshelf-bridge-status-phase-a-shipped]], [[komga-bridge-status-phase-a-shipped]], [[miniflux-bridge-status-phase-a-shipped]], [[plex-bridge-status-phase-a-shipped]], [[immich-bridge-status-phase-a-shipped]], [[extension-spec-and-manifest-schema-shipped]], [[p3-10-bubble-cancelled-privacy]]
