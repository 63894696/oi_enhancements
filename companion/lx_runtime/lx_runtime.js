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
console.log = (...args) => process.stderr.write(args.map(a => typeof a === "string" ? a : JSON.stringify(a)).join(" ") + "\n");
console.info = console.log;
console.warn = console.log;
console.error = (...args) => process.stderr.write(args.map(a => typeof a === "string" ? a : JSON.stringify(a)).join(" ") + "\n");
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
        axios.request(config).then(
            (resp) => {
                const headersObj = {};
                if (resp.headers && typeof resp.headers.forEach === "function") {
                    resp.headers.forEach((v, k) => { headersObj[k] = v; });
                } else if (resp.headers) {
                    Object.assign(headersObj, resp.headers);
                }
                cb(null, {
                    statusCode: resp.status,
                    body: resp.data,
                    headers: headersObj,
                });
            },
            (err) => cb(err, null)
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
            aesEncrypt: () => { throw new Error("aesEncrypt not shimmed (Phase 1 不需要)"); },
            rsaEncrypt: () => { throw new Error("rsaEncrypt not shimmed (Phase 1 不需要)"); },
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
// LX source 通过 lx.on("request", handler) 注册回调 — handler 就是函数本体。
// 我们只需要在 RPC 时取 bus.listeners("request") 即可,无需中间收集。

bus.on("inited", (data) => {
    if (data && typeof data === "object") {
        // juhe 的 init 形如 { init: { sources: {...}, ... }, update: {...} }
        const sources = (data.init && data.init.sources) || data.sources;
        if (sources && typeof sources === "object") {
            Object.assign(loadedSources, sources);
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
    const evt = { action, source, info: info || {} };
    // Phase 1:串行调用所有 handler,第一个返回真值即为结果。
    // (LX 原协议是按 source 分发;Phase 1 单 source 简化。)
    let lastErr = null;
    for (const h of handlers) {
        try {
            const r = h(evt);
            const v = await Promise.resolve(r);
            if (v != null) return v;
        } catch (e) {
            lastErr = e;
        }
    }
    throw lastErr || new Error("all handlers returned null");
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
