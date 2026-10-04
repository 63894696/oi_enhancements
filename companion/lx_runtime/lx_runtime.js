// lx_runtime.js — M3.28 Phase 1 PoC: jsdom + axios shim for LX Music source protocol
//
// 单 source(默认 juhe.js),stdio JSON Lines RPC。
// 接受 JSON Lines 请求 → 调 juhe source handler → 把 URL 返给 Python。
//
// 用法:
//   node lx_runtime.js                       # 默认加载 juhe.js
//   LX_SOURCES=juhe.js,ikun.js node .        # 多源(Phase 2+)
//   LX_API_KEY=xxx node .                    # (预留)API key
//
// stdin  : {"id":"1","action":"musicUrl","source":"kw","info":{...}}\n
// stdout : {"id":"1","ok":true,"result":"http://..."}\n
//         {"id":"1","ok":false,"err":"..."}\n
// stderr : [lx_runtime] xxx(诊断)

"use strict";

const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { EventEmitter } = require("events");
const axios = require("axios");

// ============================================================
// 0. 把 console.log/info/warn 重定向到 stderr
// ============================================================
// 关键:M3.28 Phase 1.5 修复。node 的 console.log 默认写 stdout,
// 但 lyswhut 主源大量用 console.log 输出响应详情({code: -1001, msg: ...})
// 这会污染走 stdout 的 JSON Lines RPC 协议,Python 客户端 readline() 拿到
// 的不是 JSON 而是 console.log 输出。
// 修复:console.* 全部改走 process.stderr;process.stdout.write 只用于 RPC 响应。
const _stderrWrite = (...args) => process.stderr.write(args.join(" ") + "\n");
console.log = (...args) => {
    try {
        process.stderr.write(args.map(a => typeof a === "string" ? a : JSON.stringify(a)).join(" ") + "\n");
    } catch (_) { /* swallow wy.js-style console.log(resp.body=null) throws */ }
};
console.info = console.log;
console.warn = console.log;
console.error = (...args) => {
    try {
        process.stderr.write(args.map(a => typeof a === "string" ? a : JSON.stringify(a)).join(" ") + "\n");
    } catch (_) { /* swallow */ }
};
console.debug = console.log;

const SCRIPTS_DIR = __dirname;
const SOURCE_FILES = process.env.LX_SOURCES
    ? process.env.LX_SOURCES.split(",").map(s => s.trim()).filter(Boolean)
    : ["mock.js"];  // Phase 1 默认 mock(juhe 后端实测 "source not match",DNS 墙挡 ikun)

// ============================================================
// 1. jsdom 构造 window — 给 source 提供 globalThis.lx
// ============================================================
const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    url: "http://localhost/",
    pretendToBeVisual: true,
    runScripts: "outside-only",
});
const win = dom.window;

// ============================================================
// 2. shim globalThis.lx
// ============================================================
const bus = new EventEmitter();
bus.setMaxListeners(50);

const lx = {
    EVENT_NAMES: {
        request: "request",
        inited: "inited",
        updateAlert: "updateAlert",
        // 一些 source 会引用下列事件名,即使 Phase 1 不触发,也要存在
        request_later: "request_later",
        music_lyric: "music_lyric",
        music_img: "music_img",
    },
    /**
     * request(url, options, callback) — axios 适配 LX 协议
     * options: { method, headers, body, timeout, follow_max, ... }
     * callback(err, { statusCode, body, headers })
     */
    request: (url, options = {}, cb) => {
        const method = String(options.method || "GET").toLowerCase();
        const config = {
            url,
            method,
            headers: options.headers || {},
            timeout: options.timeout || 10000,
            maxRedirects: options.follow_max != null ? options.follow_max : 5,
            validateStatus: () => true,  // 任意状态码都不抛
            responseType: "text",
            transformResponse: [(d) => {
                // 跟 LX 真实浏览器行为对齐:JSON 响应自动 parse,否则留原字符串
                if (typeof d !== "string") return d;
                const trimmed = d.trim();
                if (!trimmed) return d;
                if (trimmed[0] === "{" || trimmed[0] === "[") {
                    try { return JSON.parse(trimmed); } catch (_) { return d; }
                }
                return d;
            }],
        };
        if (options.body != null) {
            config.data = options.body;
        }
        // P2.5+28 Y 阶段(2026-10-05):源 cb 包 try/catch + 同步抛错转 reject。
    // 关键设计:source 调 request(url, opt, cb) 时,把它自己写的 cb 包成 wrappedCb —
    //   1) 设 done 标志
    //   2) try/catch 包 source 的 cb body,throw 转 onError(err)
//   3) 回调 return undefined → 让 source 的 Promise 永远不 resolve(它读 resp.body=null 抛错)
    //    会 unhandled-reject,但 process 已装 uncaughtException 兜底
    const _safeCb = (cb, err, resp) => {
        try {
            if (err) cb(err, null);
            else cb(null, resp);
        } catch (cbEx) {
            console.error("[lx_runtime] source cb threw:", cbEx.message);
            // swallow — source Promise 不会 resolve/reject,Python 端 readline 超时
            // (call_timeout=12s) 后客户端自己放弃,不会 hang
        }
    };

    axios.request(config).then(
            (resp) => {
                const headersObj = {};
                if (resp.headers && typeof resp.headers.forEach === "function") {
                    resp.headers.forEach((v, k) => { headersObj[k] = v; });
                } else if (resp.headers) {
                    Object.assign(headersObj, resp.headers);
                }
                _safeCb(cb, null, {
                    statusCode: resp.status,
                    body: resp.data,
                    headers: headersObj,
                });
            },
            (err) => _safeCb(cb, err, null)
        );
    },
    on: (event, handler) => {
        bus.on(event, handler);
    },
    send: (event, data) => {
        bus.emit(event, data);
    },
    utils: {
        buffer: {
            from: (s, enc) => Buffer.from(s, enc || "utf8"),
            bufToString: (b, enc) => Buffer.isBuffer(b) ? b.toString(enc || "utf8") : String(b),
            newBuffer: (size) => Buffer.alloc(size),
        },
        crypto: {
            md5: (s) => crypto.createHash("md5").update(String(s)).digest("hex"),
            randomBytes: (n) => crypto.randomBytes(n).toString("hex"),
            // P2.5+28 Y 阶段(2026-10-05):AES-128-ECB + RSA shim 给网易云 musicUrl 用。
            // 网易云 eapi 协议用 AES-128-ECB 加密请求体,key = 'e82ckenh8dichen8',无 iv(ECB 不用)。
            // kw/kg/wy/mg 都可能调 aesEncrypt。Node crypto 原生支持 AES-128-ECB。
            aesEncrypt: (data, key, iv, mode) => {
                // data: string | Buffer;key: string(8字节 eapiKey 实际 8 chars,我们 pad 到 16);
                //   Node crypto 要求 AES-128 key = 16 bytes;若 key 长度不是 16,pad 到 16 (zero-fill)。
                // mode: 'aes-128-ecb' | 'aes-128-cbc' 等;暂只实现 ECB/CBC。
                const keyBuf = Buffer.isBuffer(key) ? key : Buffer.from(String(key), "utf8");
                const key16 = keyBuf.length === 16
                    ? keyBuf
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
                } catch (e) {
                    console.error("[lx_runtime] aesEncrypt failed:", e.message);
                    throw e;
                }
            },
            // rsaEncrypt:kugou/migu 等源用来加密 songmid。Node crypto 默认不直接支持
            // 「RSA 加密短数据 PKCS1 v1.5」(原生 RSA-OAEP),但 lyswhut lx-music-source 多数
            // 用 RSA + 自写 base64。这里只实现占位:用 publicEncrypt 返回 Buffer,若用户源用
            // PKCS1 v1.5 直接调会报错 — 后续若需要再扩。Phase Y 暂只对 wy(只用 AES)开启。
            rsaEncrypt: (data, key) => {
                console.error("[lx_runtime] rsaEncrypt called but not fully shimmed (Phase Y only wy=AES works)");
                throw new Error("rsaEncrypt not shimmed (only wy AES-only source supported)");
            },
        },
        zlib: {
            inflateRaw: (b) => require("zlib").inflateRawSync(b),
            deflateRaw: (b) => require("zlib").deflateRawSync(b),
        },
        env: {
            // 部分 source 会探测环境
            isWindows: process.platform === "win32",
            isMac: process.platform === "darwin",
            isLinux: process.platform === "linux",
        },
    },
    version: "M3.28-shim-1.0",
    env: "mobile",
    currentScriptInfo: null,  // 加载每个 source 前会被覆盖
};

win.lx = lx;
// 顺手挂 globalThis(juhe 用 `globalThis.lx`)
win.globalThis = win;  // jsdom 默认 window 就是 globalThis

// ============================================================
// 3. 加载 LX sources
// ============================================================
const loadedSources = {};
// P2.5+28 Y 阶段(2026-10-05):handler → 拥有 sub-source 名映射,用于按 source 派单。
// 之前"call-all,first non-null wins"会让 local.js(handler 声明 {local: ...})屏蔽所有源
// ——其他 handler 即便声明 {kw,kg,tx,wx,mg} 也永远轮不到,因为 local 先返 local://。
// 修复:每个 handler 在 inited 阶段声明自己拥有哪些 sub-source 名,
// RPC 时按 source 名精确派单给声明该名的 handler(可能有多个 → 顺序轮询);
// 未声明该 source 名的 handler 不参与(local 不会被 kw 调用触发)。
const _handlerSources = new Map();  // handler_fn => Set<sub-source name>
let _lastRegisteredHandler = null;

// 重写 lx.on 追踪最新注册的 handler — 给 _handlerSources 提供关联点
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
        // juhe 的 init 形如 { init: { sources: {...}, ... }, update: {...} }
        const sources = (data.init && data.init.sources) || data.sources;
        if (sources && typeof sources === "object") {
            Object.assign(loadedSources, sources);
            // 把刚才注册的 handler 绑上 sub-source 名
            if (_lastRegisteredHandler) {
                const set = _handlerSources.get(_lastRegisteredHandler) || new Set();
                for (const k of Object.keys(sources)) set.add(k);
                _handlerSources.set(_lastRegisteredHandler, set);
            }
        }
    }
});

for (const file of SOURCE_FILES) {
    const fullPath = path.join(SCRIPTS_DIR, file);
    if (!fs.existsSync(fullPath)) {
        console.error(`[lx_runtime] source not found: ${file}`);
        continue;
    }
    try {
        const code = fs.readFileSync(fullPath, "utf8");
        // currentScriptInfo 给 source 留 rawScript 钩子(部分 source 会用)
        lx.currentScriptInfo = {
            rawScript: code,
            fileName: file,
            filePath: fullPath,
        };
        win.currentScriptInfo = lx.currentScriptInfo;
        // 在 window 作用域里跑 source(juhe 期望 globalThis.lx)
        win.eval(code);
        console.error(`[lx_runtime] loaded source: ${file}`);
    } catch (e) {
        console.error(`[lx_runtime] load ${file} failed: ${e.message}`);
        console.error(e.stack);
    }
}

console.error(`[lx_runtime] inited sources: ${Object.keys(loadedSources).join(", ") || "(none)"}`);
console.error(`[lx_runtime] request handlers registered: ${bus.listenerCount("request")}`);
console.error(`[lx_runtime] ready (stdio JSON Lines RPC)`);

// ============================================================
// 4. RPC 处理
// ============================================================
async function callRequest(action, source, info) {
    const handlers = bus.listeners("request");
    if (handlers.length === 0) {
        throw new Error("no request handler registered (no source loaded?)");
    }
    // P2.5+28 Y.2 (2026-10-05):lx_main.js 在源码里注册的是 dispatcher handler:
    //   lx.on(EVENT_NAMES.request, ({ action, source, info }, quality) => {
    //     if (apis[source]) apis[source][action](info, quality);
    //   });
    // 所以 registered handler 的第一个参数就是 {action, source, info},第二参数是 quality。
    // 早期 commit 时我以为 handler 签名是 `musicUrl(info, quality)` 直接调,实测发现是 dispatcher。
    // 修正:第一参数传 {action, source, info},第二参数传 quality。
    const infoObj = info || {};
    const quality = infoObj.type || infoObj.quality || "320k";
    const evt = { action, source, info: infoObj };
    // 按 source 名派单
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

let lineBuf = "";
process.stdin.on("data", (chunk) => {
    lineBuf += chunk.toString("utf8");
    let idx;
    while ((idx = lineBuf.indexOf("\n")) !== -1) {
        const line = lineBuf.slice(0, idx).trim();
        lineBuf = lineBuf.slice(idx + 1);
        if (!line) continue;
        handleRpcLine(line).catch((e) => {
            console.error("[lx_runtime] handleRpcLine top-level err:", e);
        });
    }
});

async function handleRpcLine(line) {
    let req;
    try {
        req = JSON.parse(line);
    } catch (e) {
        process.stdout.write(JSON.stringify({ id: null, ok: false, err: `bad json: ${e.message}` }) + "\n");
        return;
    }
    const { id = null, action, source, info } = req;
    if (!action || !source) {
        process.stdout.write(JSON.stringify({ id, ok: false, err: "missing action/source" }) + "\n");
        return;
    }
    try {
        const result = await callRequest(action, source, info || {});
        process.stdout.write(JSON.stringify({
            id, ok: true,
            result: result == null ? null : result,
        }) + "\n");
    } catch (e) {
        const msg = (e && e.message) || String(e);
        process.stdout.write(JSON.stringify({ id, ok: false, err: msg }) + "\n");
    }
}

// 优雅退出
process.on("SIGTERM", () => process.exit(0));
process.on("SIGINT", () => process.exit(0));

// P2.5+28 Y 阶段(2026-10-05):兜底防子进程崩溃 — 部分 source 在异常路径会 throw 未捕获
// (例如 wy.js 在 resp.body=null 时 console.log 抛 TypeError)。_safeCb 已包,但仍有漏网。
// 加 uncaughtException handler 让任何漏过的 throw 仅 log,继续服务后续请求。
process.on("uncaughtException", (err) => {
    console.error("[lx_runtime] uncaughtException (kept alive):", err.message);
    console.error(err.stack);
});
process.on("unhandledRejection", (reason) => {
    console.error("[lx_runtime] unhandledRejection (kept alive):", reason);
});
