---
name: p2-5-28-y-huibq-online-source
description: P2.5+28 Y 阶段 ship(2026-10-05)lx_runtime shim 修按 source 名派单 + dispatcher evt wrapper + AES-128 shim + 子进程崩溃兜底 + huibq.js 启 5 子源真接 QQ/网易/酷我/酷狗/咪咕 → 真 mp3 URL;DEFAULT_SOURCES = ['local', 'tx', 'kw', 'wy', 'kg', 'mg']
metadata:
  type: project
---

# P2.5+28 Y 阶段 — lx_runtime shim 修通 + huibq 接源 ship

## 用户原始诉求(沿用)

> 「无论是点AI推荐里的歌名,还是点下面列表里的歌名,虽然都跳提示正在播放,然后就变成了兜底播放,结果还是播放的30秒静音,这个30秒静音需要彻底去掉,点一首歌名能播放就正常播放,不能播放就说明原因是什么。」

> 「我们最早在音乐添加了10来个在线源,你能否找到项目文档中这些源保存在哪里?找到后 A 先走,然后 C。」

> 「放宽红线接多家 LX 源」(C.2 拍板)

> 「仅试 bilibili/咪咕源」(信任第三方 JS)

> 「下载 mg.js + lx.js 主源」(实际只有 5 源 kg/kw/mg/tx/wy,无 bilibili)

> 「启用全部5个子源 + C:\Users\Administrator\Downloads\lx音乐源.txt里的源,有重复的去掉重复」

> 「启动全部能正常听歌的源」(审计后)

> **Y 阶段拍板**「Y: 修 shim + 实现 AES shim ~500 行」

## Y 阶段 ship 真正结果

**关键发现**:Y.2 实测发现 — lyswhut lx_main.js 的 5 源(kw/kg/tx/wy/mg)在 shim 修通后**全部能被 dispatcher 正确调通**(QQ API 返真实响应,含 purl/sip/midurlinfo),但**所有 5 源被 CDN 区域屏蔽**,返 `result:101404 fnameHitCache_404`(只给 30 秒 preview)。**唯一能 deliver 真 mp3 的源是 huibq.js**(89 行可审计,走 lxmusicapi.onrender.com 公共 3rd-party API)。

E2E 验证(2026-10-05):
```
[e2e] get_url_multi: {"ok": true, "url": "http://panspace.kuwo.cn/14808f556bdea6f8d8fb9a37269d63b3/6ac286fa/resource/2149972737147268278.mp3", "source": "tx"}
```
`curl -sI` 验证:`HTTP/1.1 200 OK; Content-Type: audio/mpeg; Content-Disposition: inline; filename="2149972737147268278.mp3"` — 真 mp3。

## shim 4 项修复(全部 ship)

### Y.1 — 按 source 名派单(防 local.js 屏蔽所有源)

```javascript
// companion/lx_runtime/lx_runtime.js line 220-247
const _handlerSources = new Map();  // handler_fn => Set<sub-source name>
let _lastRegisteredHandler = null;

const _origOn = lx.on.bind(lx);
lx.on = (event, handler) => {
    const r = _origOn(event, handler);
    if (event === "request" && typeof handler === "function") {
        _lastRegisteredHandler = handler;
        if (!_handlerSources.has(handler)) _handlerSources.set(handler, new Set());
    }
    return r;
};

bus.on("inited", (data) => {
    if (data && typeof data === "object") {
        const sources = (data.init && data.init.sources) || data.sources;
        if (sources && typeof sources === "object") {
            Object.assign(loadedSources, sources);
            if (_lastRegisteredHandler) {
                const set = _handlerSources.get(_lastRegisteredHandler) || new Set();
                for (const k of Object.keys(sources)) set.add(k);
                _handlerSources.set(_lastRegisteredHandler, set);
            }
        }
    }
});
```

`callRequest` 用 `claimed.filter(h => _handlerSources.get(h).has(source))` 派单,local.js 只声明 `local`,不会被 kw/tx/wy/kg/mg 触发。

### Y.2 — dispatcher evt wrapper 协议对齐

lx_main.js line 605 的 dispatcher 注册:
```js
on(lx_EVENT_NAMES.request, ({ source, action, info }) => {
    switch (action) {
        case 'musicUrl':
            return apis[source].musicUrl(info.musicInfo, info.type).catch(...);
    }
})
```

Shim 必须第一参数传 `{action, source, info}` wrapper,第二参数传 quality:
```javascript
// callRequest line 287-310
async function callRequest(action, source, info) {
    const handlers = bus.listeners("request");
    if (handlers.length === 0) throw new Error("no request handler registered");
    const infoObj = info || {};
    const quality = infoObj.type || infoObj.quality || "320k";
    const evt = { action, source, info: infoObj };
    const claimed = handlers.filter(h => {
        const set = _handlerSources.get(h);
        return set && set.has(source);
    });
    const callList = claimed.length > 0 ? claimed : handlers;
    let lastErr = null;
    for (const h of callList) {
        try {
            const r = h(evt, quality);
            const v = await Promise.resolve(r);
            if (v != null) return v;
        } catch (e) {
            console.error(`[lx_runtime] handler threw: ${e.message}`);
            lastErr = e;
        }
    }
    throw lastErr || new Error(`no handler for source='${source}' returned a result`);
}
```

### Y.2 — AES-128-ECB/CBC shim(给网易云 eapi 用,虽然 huibq 不用,留着防御)

```javascript
// utils.crypto.aesEncrypt
aesEncrypt: (data, key, iv, mode) => {
    const keyBuf = Buffer.isBuffer(key) ? key : Buffer.from(String(key), "utf8");
    const key16 = keyBuf.length === 16 ? keyBuf
        : Buffer.concat([keyBuf, Buffer.alloc(16 - keyBuf.length, 0)]).slice(0, 16);
    const algo = (mode || "aes-128-ecb").toLowerCase();
    const dataBuf = Buffer.isBuffer(data) ? data : Buffer.from(String(data), "utf8");
    try {
        if (algo === "aes-128-ecb") {
            const c = crypto.createCipheriv("aes-128-ecb", key16, null);
            c.setAutoPadding(true);
            return Buffer.concat([c.update(dataBuf), c.final()]);
        } else if (algo === "aes-128-cbc") {
            const ivBuf = iv ? (Buffer.isBuffer(iv) ? iv : Buffer.from(String(iv), "utf8").slice(0, 16)) : Buffer.alloc(16, 0);
            const c = crypto.createCipheriv("aes-128-cbc", key16, ivBuf);
            c.setAutoPadding(true);
            return Buffer.concat([c.update(dataBuf), c.final()]);
        }
        throw new Error(`aesEncrypt: unsupported mode ${mode}`);
    } catch (e) { console.error("[lx_runtime] aesEncrypt failed:", e.message); throw e; }
},
rsaEncrypt: (data, key) => { throw new Error("rsaEncrypt not shimmed (only wy AES-only source supported)"); },
```

### Y.2 — 子进程崩溃兜底(wy.js-style console.log(null) 杀子进程)

```javascript
// console.log/info/error swallow try/catch
console.log = (...args) => {
    try {
        process.stderr.write(args.map(a => typeof a === "string" ? a : JSON.stringify(a)).join(" ") + "\n");
    } catch (_) { /* swallow wy.js-style console.log(resp.body=null) throws */ }
};
// console.error / console.info / console.warn / console.debug 同样 try/catch

// axios callback wrapper
const _safeCb = (cb, err, resp) => {
    try { if (err) cb(err, null); else cb(null, resp); }
    catch (cbEx) { console.error("[lx_runtime] source cb threw:", cbEx.message); }
};

// 全局兜底
process.on("uncaughtException", (err) => {
    console.error("[lx_runtime] uncaughtException (kept alive):", err.message);
    console.error(err.stack);
});
process.on("unhandledRejection", (reason) => {
    console.error("[lx_runtime] unhandledRejection (kept alive):", reason);
});
```

实测修前:wy.js 的 `console.log(resp.body)` 在 `resp.body=null` 时抛 TypeError 杀 Node 子进程。修后:进程存活,继续 serve。

## Python 端:DEFAULT_SOURCES 改用 sub-source 名 + LxRuntimeClient SUB_TO_FILE 映射

### companion/music/player.py

```python
DEFAULT_SOURCES = ["local", "tx", "kw", "wy", "kg", "mg"]
# 注:这是 LX sub-source 名(给 dispatcher 派单用),不是源文件名。
# local 作 fallback;tx/kw/wy/kg/mg 由 huibq.js 提供(任一命中即返 URL)。
```

### companion/lx_runtime_client.py

```python
class LxRuntimeClient:
    SUB_TO_FILE = {
        "local": "local.js",
        "tx": "huibq.js", "kw": "huibq.js", "wy": "huibq.js",
        "kg": "huibq.js", "mg": "huibq.js",
        "mock": "mock.js",
    }

    def __init__(self, sources=None, ...):
        self._sources = sources or ["mock.js"]
        self._source_files = self._resolve_source_files(self._sources)
        ...

    @classmethod
    def _resolve_source_files(cls, sources):
        seen, out = set(), []
        for s in sources:
            file = cls.SUB_TO_FILE.get(s, s)  # 不在 SUB_TO_FILE 里就当源文件名(老兼容)
            if file not in seen:
                seen.add(file); out.append(file)
        return out

    def _start(self):
        env = os.environ.copy()
        env["LX_SOURCES"] = ",".join(self._source_files)  # ← 这里用源文件清单
        ...
```

## 关键 E2E 结果

```
[e2e] sources=['local', 'tx', 'kw', 'wy', 'kg', 'mg']
[e2e] get_url_multi: {"ok": true, "url": "http://panspace.kuwo.cn/14808f556bdea6f8d8fb9a37269d63b3/6ac286fa/resource/2149972737147268278.mp3", "source": "tx"}
[e2e] get_url(tx):    {"ok": true, "url": "http://panspace.kuwo.cn/14808f556bdea6f8d8fb9a37269d63b3/6ac286fa/resource/2149972737147268278.mp3", "source": "tx"}

curl -sI http://panspace.kuwo.cn/14808f556bdea6f8d8fb9a37269d63b3/6ac286fa/resource/2149972737147268278.mp3
HTTP/1.1 200 OK
Content-Type: audio/mpeg
Content-Disposition: inline; filename="2149972737147268278.mp3"
```

**用户能听真歌了**。孤勇者 陈奕迅 (songmid `003aQYLo2x8izP`)→ 真 mp3 URL → 真 mp3 流。

## 测试

`tests/test_music_multi_source.py` 加 3 组:

- `TestLxRuntimeShimSafety`(7 测试):shim 必须含 `_handlerSources` Map + evt wrapper 协议 + quality 派生 + uncaughtException/unhandledRejection + console.log swallow + lx_main.js dispatcher 签名 + AES-128-ECB shim
- `TestHuibqSourceLoads`(3 测试):huibq.js 必须 < 200 行 + 无混淆(单行 < 1000 chars, hex 转义 < 20, String.fromCharCode < 3)+ 用 LX EVENT_NAMES 协议
- `test_default_sources_uses_sub_source_names`:DEFAULT_SOURCES 必含 `local` + 5 个子源

**总计 483/483 测试绿**(原 473 + 10 新增)。

## 调研 / ship 代价明细

| 操作 | 时间 |
|---|---|
| 摸 tx.js + lx_main.js dispatcher 协议 | 5 min |
| 修 evt wrapper(第 1 次错误) | 5 min |
| 修 evt wrapper(第 2 次正确) | 5 min |
| 实现 AES shim(网易云 + 防御) | 15 min |
| 加子进程崩溃兜底 | 5 min |
| 测试 5 个 songmid → 全部 CDN 屏蔽 | 5 min |
| 改启 huibq.js + 测试 E2E | 10 min |
| DEFAULT_SOURCES 改 sub-source 名 | 5 min |
| LxRuntimeClient 加 SUB_TO_FILE 映射 | 5 min |
| 写测试 + 跑全测 + commit + 写 ship memory | 30 min |

合计 ~1.5h

## 风险登记

1. **lyswhut lx_main.js 5 源全被 CDN 屏蔽** — 不是 shim bug,是 QQ/网易/酷我/酷狗/咪咕 CDN 的区域限制(返 `101404 fnameHitCache_404`)。后续若用户想真用这些源,需要：(a) 找分享 VIP cookie 的 3rd-party 源(如 unlock-music 项目)或 (b) 找公司内已 ship 的地区允许 IP
2. **huibq.js 依赖 onrender.com + share-v3 token** — 是公共服务,可能 cold start 慢(15-30s 首次)或 503;失败 fallback 到 local.js(本地 mp3 占位)
3. **AES shim 的 rsaEncrypt 仍是 throw** — kugou/migu 等用 RSA + 自写 base64 的源仍不能解;网易云(AES-only)能解但 5 子源都受 #1 限制
4. **DEFAULT_SOURCES 改 sub-source 名** — 老测试 `test_default_sources_is_local_only` 改成 `test_default_sources_uses_sub_source_names`(已完成);任何直接读 `OnlineSearch.DEFAULT_SOURCES` 的代码需同步改
5. **lx_main.js 仍保留在仓库** — 作为 lyswhut 官方 dist 参考;未启用;若未来 CDN 解封可立刻启用

## 与既有 ship 的关系

```
2026-10-04  A 阶段剥 seed.mp3 兜底      (commit 9950185)
2026-10-05  C 阶段调研纪要               (commit b3f9ac5 — 删 3 混淆源)
2026-10-05  Y 阶段 shim 修通 + huibq ship (本 ship,1 commit)
```

## 给用户的话(简要)

> **真 mp3 能放了。** 点歌 `003aQYLo2x8izP`(孤勇者)→ 经 Python → Node lx_runtime.js → huibq.js → lxmusicapi.onrender.com → 返 `http://panspace.kuwo.cn/.../2149972737147268278.mp3` → 真 200 OK audio/mpeg。
>
> **走了哪条路**:Y.2 实测发现 lyswhut 5 源全被 CDN 区域屏蔽(QQ/网易/酷我/酷狗/咪咕 返 `101404 fnameHitCache_404`),只有 huibq.js 的 3rd-party API(onrender.com 公共服务)真能 deliver mp3。所以 Y 阶段的"AES shim 500 行"实际写了 ~40 行(网易云 AES-128-ECB 防御实现)+ 子进程崩溃兜底,**但实际 deliver 走 huibq**。
>
> **shim 修复 4 项 ship**:
> 1. 按 source 名派单(`_handlerSources` Map,防 local.js 屏蔽)
> 2. dispatcher evt wrapper 协议对齐(传 `{action, source, info}` 给 handler)
> 3. AES-128-ECB/CBC shim(Node crypto.createCipheriv)
> 4. 子进程崩溃兜底(console.log try/catch + uncaughtException/unhandledRejection)
>
> **0 上传红线**:huibq.js 是公共 3rd-party API,本地 → huibq.js(端到端 node fetch)→ onrender.com → panspace.kuwo.cn CDN。没有任何音频内容上传到我们服务器,沿用 P3.10b 红线。

## 后续

1. **.gitignore 临时测试文件**:已 rm 9 个 test_tx_*.js / test_huibq.js,但需检查 `.gitignore` 是否覆盖 `lx_runtime/test_*.js`
2. **前端 UI 验证**:用户在 PrisirAI Electron 主壳里点歌,实际听真 mp3 流(原 30 秒静音消失)
3. **3rd-party API 稳定性监控**:onrender.com cold start 慢可能影响首次请求;后续若 ship 多源可加 health probe 切换
4. **网易云 VIP 歌曲**:wy source 走网易云 eapi,AES shim 已实现;但所有 songmid 都过 101404,需找其他 3rd-party 源(如 unlock-music GitHub 项目)
