// forum_client.js — Prisir 免注册论坛协议层(身份/签名/PoW/WS 帧)
// 契约: docs/prisir-forum-protocol-2026-08-21.md(已定稿,canon 双向对拍 5/5 绿)
// 双轨签名红线(与 mixin/group.html:197-242 同构):
//   window.ForumNative 就绪(重编译后) → 原生代签,私钥不出原生层;
//   未就绪(当前纯 JS 期) → WebCrypto + localStorage 降级,诚实标注,重编译后无缝切换。

const FORUM = (() => {
  const enc = new TextEncoder();

  // ── 编码工具(与 group.html:158-160 逐行对齐) ──
  function b64(buf) { return btoa(String.fromCharCode(...new Uint8Array(buf))); }
  function b64d(s) { return Uint8Array.from(atob(s), c => c.charCodeAt(0)); }
  function b64url16(buf) { return b64(buf).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "").slice(0, 16); }
  // 与 mixin canon() 逐字节一致:对象键递归排序、无空白、非 ASCII 直出 UTF-8
  function canon(o) {
    if (Array.isArray(o)) return "[" + o.map(canon).join(",") + "]";
    if (o && typeof o === "object") return "{" + Object.keys(o).sort().map(k => JSON.stringify(k) + ":" + canon(o[k])).join(",") + "}";
    return JSON.stringify(o);
  }
  function checkPow(digest, bits) {
    const full = Math.floor(bits / 8), rem = bits % 8;
    for (let i = 0; i < full; i++) if (digest[i] !== 0) return false;
    if (rem && (digest[full] >> (8 - rem)) !== 0) return false;
    return true;
  }

  // ── 身份(Ed25519;双轨:原生代签优先,降级 WebCrypto+localStorage) ──
  // 红线:私钥不出原生层(方案对话拍板)。window.ForumNative 就绪时,
  // 身份/签名由原生层代做,本页拿不到私钥;未就绪(纯 JS 期)退回 WebCrypto localStorage。
  let ID = null; // {priv|null(原生层时为空), pub, pubRaw, user_id(fp), _native}
  function nativeReady() { return !!(window.ForumNative && window.ForumNative.sign); }

  async function loadOrCreateIdentity() {
    if (nativeReady()) {
      // 原生层托管身份:派生 key_id="forum-sign" 子密钥,私钥不出原生层
      const id = await window.ForumNative.getIdentity(); // {pub_raw(b64), user_id}
      return { priv: null, pub: null, pubRaw: b64d(id.pub_raw), user_id: id.user_id, _native: true };
    }
    const saved = localStorage.getItem("forum_id_jwk");
    if (saved) {
      try {
        const jwk = JSON.parse(saved);
        const priv = await crypto.subtle.importKey("jwk", jwk, { name: "Ed25519" }, true, ["sign"]);
        const pub = await crypto.subtle.importKey("jwk", { ...jwk, priv: undefined, d: undefined, key_ops: ["verify"], ext: true }, { name: "Ed25519" }, true, ["verify"]);
        return await makeId(priv, pub);
      } catch (e) { console.warn("身份恢复失败,重新生成", e); }
    }
    const kp = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
    const jwk = await crypto.subtle.exportKey("jwk", kp.privateKey);
    localStorage.setItem("forum_id_jwk", JSON.stringify(jwk));
    return await makeId(kp.privateKey, kp.publicKey);
  }
  async function makeId(priv, pub) {
    const pubRaw = await crypto.subtle.exportKey("raw", pub);
    const digest = await crypto.subtle.digest("SHA-256", pubRaw);
    return { priv, pub, pubRaw, user_id: b64url16(digest) };
  }

  async function signBytes(data) {
    if (ID && ID._native) {
      const r = await window.ForumNative.sign(Array.from(data)); // 原生代签
      return r.sig; // b64
    }
    return b64(await crypto.subtle.sign({ name: "Ed25519" }, ID.priv, data));
  }

  // ── PoW 求解(分片,不卡 UI;bits 由 relay welcome 下发) ──
  async function solvePow(post, bits, onProgress) {
    let nonce = 0;
    while (true) {
      post.pow = { alg: "sha256-b64", bits, nonce };
      const d = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(canon(post))));
      if (checkPow(d, bits)) return post;
      nonce++;
      if (nonce % 5000 === 0) {
        if (onProgress) onProgress(nonce);
        await new Promise(r => setTimeout(r, 0)); // 让出事件循环
      }
      if (nonce > 3_000_000) throw new Error("PoW 求解超限(3M)");
    }
  }

  // ── 构造签名帖子 ──
  async function makePost(body, { kind = "post", parent = null, board = "lobby", powBits = 18, onProgress } = {}) {
    if (!ID) throw new Error("身份未初始化");
    const post = {
      v: 1, kind, board, parent,
      body, author_pub: b64(ID.pubRaw), author_fp: ID.user_id,
      ts: new Date().toISOString().replace(/\.\d+Z$/, "Z"), pow: null
    };
    await solvePow(post, powBits, onProgress);
    post.sig = await signBytes(enc.encode(canon(post)));
    return post;
  }
  function postIdOf(post) {
    return crypto.subtle.digest("SHA-256", enc.encode(canon(post))).then(d => b64url16(d));
  }

  // ── 阅读侧验签(零信任 relay,每帖自验) ──
  async function verifyPost(post) {
    try {
      const { sig, ...signed } = post;
      const pub = await crypto.subtle.importKey("raw", b64d(post.author_pub), { name: "Ed25519" }, false, ["verify"]);
      const okSig = await crypto.subtle.verify({ name: "Ed25519" }, pub, b64d(sig), enc.encode(canon(signed)));
      const dfp = await crypto.subtle.digest("SHA-256", b64d(post.author_pub));
      const okFp = b64url16(dfp) === post.author_fp;
      const dpow = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(canon(signed))));
      return okSig && okFp && checkPow(dpow, post.pow.bits);
    } catch (e) { return false; }
  }

  // ── WS 客户端 ──
  function connect(url, handlers) {
    const ws = new WebSocket(url);
    ws.onopen = () => { ws.send(JSON.stringify({ type: "hello" })); handlers.onOpen && handlers.onOpen(); };
    ws.onmessage = ev => {
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      handlers.onFrame && handlers.onFrame(m);
    };
    ws.onclose = () => handlers.onClose && handlers.onClose();
    ws.onerror = e => handlers.onError && handlers.onError(e);
    return ws;
  }

  return { canon, b64, b64d, b64url16, checkPow, loadOrCreateIdentity, makePost,
           postIdOf, verifyPost, connect, nativeReady,
           get ID() { return ID; }, set ID(v) { ID = v; } };
})();

// Node 自测导出(浏览器忽略)
if (typeof module !== "undefined") module.exports = FORUM;
