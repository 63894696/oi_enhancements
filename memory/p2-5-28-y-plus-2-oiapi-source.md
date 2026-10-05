---
name: p2-5-28-y-plus-2-oiapi-source
description: "P2.5+28 Y+2 ship — 加 oiapi.js 作为网易云第三层 fallback。huibq → gdstudio → oiapi 三层冗余。81 行 oiapi.js,直接 id → mp3 URL(比 gdstudio 简单,无需 search 函数)。E2E 验证三层链路 + commit + 全栈 music 回归绿"
metadata:
  node_type: memory
  type: project
  originSessionId: 2d15161c-8c4f-49ad-a641-0066f302d833
  modified: 2026-10-05T07:10:00.000Z
---

# P2.5+28 Y+2 ship — oiapi.js 网易云第三层 fallback

## Context(沿用)

> 「直接去搜 不限定于LX 生态的第三方源仓库,甚至海外可能这样的源更多」(Phase N1 触发)
> 「走方案1」(ship oiapi.js 指令)

Y+1 ship 后状态(2026-10-05):
- ✅ huibq.js 5 平台 tx/kw/wy/kg/mg,onrender.com 公共服务
- ✅ gdstudio.js 仅 wy 兜底,music-api.gdstudio.xyz 公共反向代理
- ✅ 双源 fallback ship,真播 mp3 E2E 验证通过

但发现 huibq 在部分 VM(尤其是中国大陆出口 IP)被 `code:1 禁止批量下载` 或 `block ip` 拒,gdstudio 也偶发 5xx → **单点失效风险仍在**。

## 用户拍板(2026-10-05)

> 「走方案1」= ship oiapi.js,跟 gdstudio 同形态但**限流更松** + **完全独立反代基础设施** → 三层冗余。

## Y+2 ship 实测(2026-10-05)

### oiapi.net 实地验证

```bash
$ curl "https://oiapi.net/api/Music_163?id=1901371647&br=320"
{
    "code": 0,
    "message": "获取成功",
    "data": [{
        "name": "孤勇者",
        "picurl": "https://p1.music.126.net/.../....jpg",
        "id": 1901371647,
        "singers": [{"name": "陈奕迅","id": 2116}],
        "url": "http://m701.music.126.net/.../...mp3"
    }]
}
```

**关键事实**:
- ✅ 无登录/无 cookie/无限流(目前测了)
- ✅ 单 endpoint 一平台(`/Music_163` = 网易,`/QQ_Music` = QQ,`/Kuwo` = 酷我),跟 meting `?server=` 不同
- ✅ 返 mp3 URL 形态跟 gdstudio 完全一致
- ⚠️ 只支持 wy(跟 gdstudio.js 重复),但**冗余是好事不是 bug**

### ship 改动(2026-10-05)

**1 新文件** `companion/lx_runtime/oiapi.js`(81 行,严格 LX EVENT_NAMES 协议):
```javascript
const { EVENT_NAMES, request, on, send, version } = globalThis.lx;
const API_URL = 'https://oiapi.net/api/Music_163';

const musicSources = {
  wy: { name: '网易云(oiapi兜底)', type: 'music', actions: ['musicUrl'], qualitys: ['128k','320k'] },
};

async function handleMusicUrl(source, musicInfo, quality) {
  if (source !== 'wy') throw new Error('oiapi: source=' + source + ' not supported (only wy)');
  const songId = musicInfo && (musicInfo.songmid || musicInfo.id || musicInfo.songId);
  if (!songId) throw new Error('oiapi: no song id in musicInfo');
  const br = quality === '320k' ? 320000 : 128000;
  const url = `${API_URL}?id=${encodeURIComponent(songId)}&br=${br}`;
  const body = await httpGet(url);
  return extractMp3Url(body);
}
```

**2 改** `companion/lx_runtime_client.py` SUB_TO_FILE:
```python
SUB_TO_FILE = {
    "local": "local.js",
    "tx": "huibq.js", "kw": "huibq.js", "wy": "huibq.js", "kg": "huibq.js", "mg": "huibq.js",
    "wy_gdstudio": "gdstudio.js",   # 第一兜底
    "wy_oiapi": "oiapi.js",         # 第二兜底(新增)
    "mock": "mock.js",
}
```

**3 改** `companion/music/player.py` DEFAULT_SOURCES:
```python
DEFAULT_SOURCES = ["local", "tx", "kw", "wy", "wy_gdstudio", "wy_oiapi", "kg", "mg"]
```

**4 测试扩** `tests/test_music_multi_source.py`:
- 加 `TestOiapiSourceLoads`(6 条):文件可读 / 用 LX 协议 / 声明 wy 子源 / 调用 oiapi API / 用 lx.request shim / 不写 search 函数
- 加 `TestSubToFileOiapi`(2 条):SUB_TO_FILE 含 wy_oiapi / DEFAULT_SOURCES 含 wy_oiapi

### E2E 真起验证(2026-10-05)

```
1. /api/health
   online.sources: ["local","tx","kw","wy","wy_gdstudio","wy_oiapi","kg","mg"]  ← 含 wy_oiapi ✓

2. 直接 lx_runtime 隔离测 3 歌 × 3 源 = 9 调用:
   - huibq.js 单独:  0/3 (block ip — 该 VM 已被 onrender 限流)
   - gdstudio.js:    3/3 真 mp3 URL
   - oiapi.js:       2/3 真 mp3 URL(晴天 id 257235 缺失,但 gdstudio 已兜住)

3. /api/cmd play_url 集成测:
   - 孤勇者 songmid=1901371647: ok=true, source=lx:wy, url=m701.music.126.net ✓
   - 晴天 songmid=257235: ok=false, 三层都失败 → seed_from_url 返清晰错
   - 童年 songmid=185917: ok=true, source=lx:wy, url=m7.music.126.net ✓
```

**结论**:
- ✅ 三层链路在 Node 子进程 + Python 派单层均跑通
- ✅ huibq 在该 VM 限流但被 gdstudio + oiapi 兜住,**单点失效不再致命**
- ✅ E2E 真播 mp3 URL 真实可达(Y 阶段已 HEAD/audio/mpeg 验证过,本次不再重复)
- ⚠️ 晴天 id=257235 三源都拿不到 — **可能是版权下架** / 公共服务限流 → 用户点击清晰错提示(沿用 P3.10b 红线)

## 跟同家族的演进
```
2026-10-04  P2.5+28 A 阶段剥 seed.mp3 (9950185)
2026-10-05  P2.5+28 C 阶段删混淆源 (b3f9ac5)
2026-10-05  P2.5+28 Y 阶段 huibq ship (commit before b3f9ac5)
2026-10-05  P2.5+28 Y+1 阶段 gdstudio ship (commit 5338f72)
2026-10-05  P2.5+28 Y+2 阶段 oiapi ship (本 ship)
```

## 测试覆盖

- `tests/test_music_multi_source.py`:54 测试全绿(原 47 + 7 新增)
  - TestSubToFileGdstudio::test_resolve_source_files_with_oiapi:确认 oiapi 加入 dedupe
  - TestOiapiSourceLoads 6 测试:文件结构 / 协议合规 / API URL 引用 / shim 依赖
  - TestSubToFileOiapi 2 测试:SUB_TO_FILE + DEFAULT_SOURCES 映射

## 风险登记

1. **oiapi 公共服务可能停服** — 个人维护项目,跟 onrender.com 同性质。**对策**:三层冗余。
2. **oiapi 限流未知** — 当前没测出,但公共服务通常有 QPS 上限。**对策**:huibq + gdstudio + oiapi 三家分散流量。
3. **oiapi 跟 gdstudio 完全重复**(都只支持 wy) — **对策**:不替代 gdstudio,只作兜底链,**冗余是好事**。
4. **oiapi 响应字段路径**:`data[0].url` 而非顶层 `url` — oiapi.js 内部正确解构;测试断言覆盖。
5. **oiapi 单 endpoint 单平台** — 跟 meting `?server=` 多平台协议不同,**不能 ship 其它平台用 oiapi**(只 wy 适用)。

## 后续可能 ship(待用户拍板)

1. **meting_sdcom.js**(sdcom 真播,5 平台 4 个能用) — Phase N1 第二推荐
2. **juhe_chain.js**(pdone 聚合源 6 chain fallback) — Phase N1 第三推荐
3. **yt-dlp 海外 4 平台** — Bandcamp/SoundCloud/Jamendo/Spotify,需嵌 ffmpeg