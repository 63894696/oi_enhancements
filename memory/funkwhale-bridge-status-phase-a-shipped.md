---
name: funkwhale-bridge-status-phase-a-shipped
description: Funkwhale Phase A ship — Subsonic SDK 第三次复用(零修改)+ .view 后缀端点
metadata:
  type: project
---

# Funkwhale 当前播放 Phase A ship (2026-10-07)

## 起因

Phase C Subsonic SDK 第二次 ship 时([[phase-c-subsonic-sdk-and-audiobookshelf-shipped]])已声明 LMS / Funkwhale / Ampache 接入工作降到 5 分钟。Audiobookshelf 用 Bearer 走自家实现不算真复用,所以 **Funkwhale 是 SDK 第一个真正"零修改"复用的扩展**(Navidrome + LMS + Funkwhale + Ampache 全是 Subsonic 协议)。

LMS 因用 Telnet/HTTP 9000 端口的私有 JSON-RPC,无法用 Subsonic SDK(无 Subsonic 兼容),改用 Funkwhale。Funkwhale 是联邦(federation)音乐平台,主推 Subsonic 兼容 API + OpenSubsonic(.view 后缀端点)。

## 设计要点

### 端点差异(OpenSubsonic 规范)

| 软件 | 协议版本 | 端点风格 |
|---|---|---|
| Navidrome | Subsonic 旧版 | `/rest/ping` |
| **Funkwhale** | OpenSubsonic | `/rest/ping.view` |
| LMS | 无 Subsonic | Telnet/HTTP 9000 私有 |
| Ampache | Subsonic 旧版 | `/ping` |

**SDK 设计支持任意 endpoint 字符串**(如 `ping.view`),扩展自己拼后缀。Subsonic SDK 提炼时已留口。

### env 字段(独立命名空间,不复用 Navidrome 的 PRISIR_NAVIDROME_*)

- `PRISIR_FUNKWHALE_URL` 默认 `http://127.0.0.1:5000`(Funkwhale Docker 默认端口,不是 Funkwhale-front 默认 5000)
- `PRISIR_FUNKWHALE_USER` 默认空
- `PRISIR_FUNKWHALE_PASS` 默认空
- `PRISIR_FUNKWHALE_TOKEN` 可选(预派生跳过明文)
- `PRISIR_FUNKWHALE_CLIENT` 默认 `prisirai`
- `PRISIR_FUNKWHALE_API_V` 默认 `1.16.1`(Funkwhale 支持至 1.16.0,默认 1.16.1 降级兼容)

**为什么不用 `PRISIR_SUBSONIC_*` 通用命名?** 因为每个 Subsonic 实例(env)独立,避免将来同时跑 Navidrome + Funkwhale 双实例互覆盖。扩展自己字段空间是 SDK 设计原则。

### 联邦 (federation-aware) feature tag

Funkwhale 是联邦音乐平台(类似 Mastodon 联邦架构),扩展 feature 加 `'federation-aware'` 标记 — 提醒系统:数据可能来自其他实例,缓存策略(本项目**不缓存**)与本地库不同。

### 3 个 L0 命令(纯只读)

- `funkwhale.health` — `/rest/ping.view` 探活 + 延迟
- `funkwhale.now-playing` — `/rest/getNowPlaying.view` 当前播放条目(flatten map)
- `funkwhale.license` — `/rest/getLicense.view` 授权信息 + server version

**绝不**触碰 `jukeboxControl` / `stream` / `scrobble` / `set` / `download` 等 mutating 接口。

## 实现

### 复用 SDK 的代码

Funkwhale `index.js` 只有 ~142 行,核心全部来自 SDK:
```js
const { makeConfig, httpGetSubsonic, parseSubsonic,
        mapNowPlayingEntry, flattenNowPlaying } =
  require('../_scaffold/subsonic-client');

const r = await httpGetSubsonic({ config: cfg, endpoint: 'ping.view' });
const p = parseSubsonic(r);
return { ok: true, alive: true,
         fw_url: cfg.baseUrl(), latency_ms: dt, http_status: r.status };
```

**SDK 9 个 API 全零修改复用**:randomSalt / md5Hex / makeToken / makeConfig / httpGetSubsonic / parseSubsonic / mapNowPlayingEntry / flattenNowPlaying。

### Navidrome 与 Funkwhale 对比(几乎一致)

| 字段 | Navidrome | Funkwhale |
|---|---|---|
| 端口默认 | 4533 | 5000 |
| 端点后缀 | 无 | `.view`(OpenSubsonic) |
| 协议版本 | 1.16.1 | 1.16.1 |
| env 命名空间 | PRISIR_NAVIDROME_* | PRISIR_FUNKWHALE_* |
| SDK 改动 | 0 | 0 |
| 行数 | 154 | 142 |

唯一真差异是 endpoint 字符串拼 `.view` 后缀——SDK 直接透传,**扩展自己决定**。这证明 SDK 设计"透传"抽象正确。

## 测试结果

**Funkwhale 单扩**:
```
node __tests__/run.js          → 7/7 ✓
node __tests__/run.js --e2e    → 11/11 ✓
```

**Python wrapper**:
```
$ python -m pytest tests/test_funkwhale_bridge_status.py -v
test_js_syntax_parses PASSED                                  [ 25%]
test_node_unit_tests_all_pass PASSED                          [ 50%]
test_node_e2e_all_pass PASSED                                 [ 75%]
test_extension_register_commands_via_sdk PASSED               [100%]
============================== 4 passed in 0.94s ==============================
```

**7 扩展联合回归**(Phase A 模板):
```
$ python -m pytest tests/test_{funkwhale,audiobookshelf,
                               navidrome,yesplaymusic,lx_music,
                               calibre,siyuan}_*.py
======================== 25 passed, 3 skipped in 4.49s ========================
```

3 个 skipped 是历史遗留(LX / Calibre / SiYuan 各一个 e2e 用例需要真实本地实例)。

## Phase A 模板固化:4 类

| 协议 | 实现 | 例 | SDK |
|---|---|---|---|
| anonymous HTTP(1 字段 URL)| 局域网页面或 HTTP API | LX / YesPlayMusic | 自写 HTTP |
| **鉴权 HTTP, salted token**(5+ 字段)| Subsonic md5+Salt | Navidrome / **Funkwhale** | subsonic-client.js |
| **鉴权 HTTP, Bearer token**(3 字段)| Bearer over HTTPS | Audiobookshelf | 自写(SDK 待抽) |
| **SQLite readonly**(2 字段)| node:sqlite read-only | SiYuan / Calibre | 自写 |

**关键洞察**:同协议多软件可抽 SDK(Subsonic SDK),**异协议各自独立实现**(Bearer / SQLite / anonymous 各需独立封装)。

## Why

Funkwhale Phase A 是 **SDK 设计的一次真实回归测试**——Navidrome ship 后两天内是否真"5 分钟 ship"一个新 Subsonic 扩展?答案是 **是**。

1. **SDK 透传 endpoint 决策正确**:Navidrome(无后缀) + Funkwhale(.view 后缀)共用 9 API 零修改
2. **makeConfig factory 模式稳**:env 字段各扩展独立,SDK 不绑死 `PRISIR_SUBSONIC_*`
3. **parseSubsonic 3 层失败语义稳**:HTTP / envelope missing / status=failed + error.code 三态识别在 Funkwhale 完全一致
4. **mapNowPlayingEntry 字段映射稳**:两个扩展 Subsonic nowPlaying entry schema 完全相同

LMS / Ampache / Airsonic-Advanced 未来 ship 同样会是 5 分钟工作。

## How to apply

下次做新 Subsonic 兼容软件扩展(Ampache / Airsonic-Advanced / 等):

1. 端口确认 + endpoint 后缀(是否 .view OpenSubsonic)
2. `require('../_scaffold/subsonic-client')`
4. 写 env 字段(makeConfig 调用,字段名独立)
5. 写 L0 命令(SDK 直接复用,只拼 endpoint 名)
6. 测三件套(Node 单测 + E2E mock Subsonic server + Python wrapper)
7. 跑联合回归(全 Phase A 7 扩展)

每次 ~5 分钟,无协议层代码改动。

下次做 Bearer token 软件(Komga / Plex / Jellyfin 自定义 API):
- 优先抽 `bearer-client.js` SDK(类似 subsonic-client.js)
- Audiobookshelf 是首个 Bearer 案例,代码可作模板

**Phase A 模板 = 单软件 ship 工作量降到最低**(从 2-3 天 → 5 分钟)。

相关:[[phase-c-subsonic-sdk-and-audiobookshelf-shipped]], [[navidrome-bridge-status-phase-a-shipped]], [[audiobookshelf-bridge-status-phase-a-shipped]], [[extension-spec-and-manifest-schema-shipped]], [[p3-10-bubble-cancelled-privacy]]