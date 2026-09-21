'use strict';

/**
 * marketplace 远端镜像 v0.1.0 (P2.5+B-4.F, 2026-09-21)
 *
 * 通过 Prisir 免注册论坛(forum_relay)做 workflow bundle 的远端镜像 —
 * 替代 GitHub,落到「PrisirAI 对话」子版(browser/shell)。
 *
 * 协议(与 forum_relay.py 严格双向):
 *   hello → welcome {pow_bits, boards, last_seq}
 *   read  → history {msgs:[{post, post_id, seq, ...}], last_seq}
 *   post  → ack {post_id, seq} | nack {reason}
 * 帖子 schema:
 *   { v, kind, board, parent, body, author_pub, author_fp, ts,
 *     pow: {alg, bits, nonce}, sig, attachment? }
 *
 * 身份(独立 Ed25519 keypair,首启生成,持久化):
 *   ~/.prisir/marketplace_identity.key   ← 32 字节 seed,base64 编码
 *
 * 约束:
 *   - Node 22+ 内置 WebSocket(零外部依赖)
 *   - canon 严格匹配 forum_relay.canon:sort_keys + (',', ':') + ensure_ascii=False
 *   - attachment 字段入 canon(签名覆盖附件完整性)
 *   - 匿名公开读(read 帧无需签名)— list / fetch 用未签连接也行
 *
 * 命令:
 *   market.list      { since_seq? }                        → { ok, posts, last_seq }
 *   market.fetch     { post_id }                           → { ok, post_id, title, attachment }
 *   market.publish   { title, description?, attachment_*} → { ok, post_id, seq, confirmed }
 *   market.identity  ()                                    → { ok, identity }
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');

const ext = new PrisIrExt({ id: 'marketplace', name: 'Workflow 远端镜像(论坛)', version: '0.1.0' });

// ─── 配 ──────────────────────────────────────────────────────────
const FORUM_WS_URL = process.env.PRISIR_FORUM_WS_URL || 'ws://127.0.0.1:18812';
const MARKET_BOARD = 'browser/shell';  // 「PrisirAI 对话」子版
const POW_BITS = parseInt(process.env.PRISIR_MARKET_POW_BITS || '18', 10);
const POST_TS_SKEW_MS = 600_000;        // 服务端允许 ±10min,这里保险 9min

// ─── Marketplace 身份(独立 Ed25519) ───────────────────────────────
function _identityPath() {
  return path.join(os.homedir(), '.prisir', 'marketplace_identity.key');
}
function _loadOrCreateIdentity() {
  const fp = _identityPath();
  fs.mkdirSync(path.dirname(fp), { recursive: true });
  if (fs.existsSync(fp)) {
    try {
      const seed = Buffer.from(fs.readFileSync(fp, 'utf8').trim(), 'base64');
      if (seed.length === 32) {
        // Node 18+: 用 seed 32 字节构造 ed25519 privateKey
        return crypto.createPrivateKey({
          key: Buffer.concat([Buffer.from('302e020100300506032b657004220420', 'hex'), seed]),
          format: 'der', type: 'pkcs8',
        });
      }
    } catch (e) { /* 重新生成 */ }
  }
  const { privateKey } = crypto.generateKeyPairSync('ed25519');
  const der = privateKey.export({ format: 'der', type: 'pkcs8' });
  const seed = der.subarray(der.length - 32);
  fs.writeFileSync(fp, seed.toString('base64'));
  return privateKey;
}
function _pubB64Url(priv) {
  const pubDer = crypto.createPublicKey(priv).export({ format: 'der', type: 'spki' });
  const raw32 = pubDer.subarray(pubDer.length - 32);
  return raw32.toString('base64url');
}
function _fp(pubB64url) {
  const raw = Buffer.from(pubB64url, 'base64url');
  return crypto.createHash('sha256').update(raw).digest('base64url').slice(0, 16).replace(/=/g, '');
}

// ─── canon(与 forum_relay.canon 严格一致) ─────────────────────────
function _canon(obj) {
  // Python: json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
  if (obj === null || typeof obj !== 'object') return JSON.stringify(obj);
  if (Array.isArray(obj)) return '[' + obj.map(_canon).join(',') + ']';
  const keys = Object.keys(obj).sort();
  const pairs = [];
  for (const k of keys) {
    // Python dict 字段值若是 dict/array 走递归 — JSON.stringify 默认行为 OK
    pairs.push(JSON.stringify(k) + ':' + _canon(obj[k]));
  }
  return '{' + pairs.join(',') + '}';
}

// ─── PoW 算力 ─────────────────────────────────────────────────────
function _checkPow(digest, bits) {
  const nFull = Math.floor(bits / 8);
  const rem = bits % 8;
  for (let i = 0; i < nFull; i++) if (digest[i] !== 0) return false;
  if (rem && (digest[nFull] >> (8 - rem)) !== 0) return false;
  return true;
}
function _solvePow(signedViewBase, bits) {
  const view = { ...signedViewBase };
  for (let n = 0; n < 2 ** 53; n++) {
    view.pow = { ...signedViewBase.pow, nonce: n };
    const c = _canon(view);
    const d = crypto.createHash('sha256').update(c).digest();
    if (_checkPow(d, bits)) return n;
  }
  throw new Error('pow: nonce exhausted');
}

// ─── WebSocket 长连接(单例) ──────────────────────────────────────
let _ws = null;
let _helloOk = false;
let _recvQueue = [];
let _identity = null;
let _authorPub = '';
let _authorFp = '';

async function _ensureConnected() {
  if (_ws && _helloOk) return;
  if (!_identity) {
    _identity = _loadOrCreateIdentity();
    _authorPub = _pubB64Url(_identity);
    _authorFp = _fp(_authorPub);
    ext.log('info', `marketplace identity fp=${_authorFp}`);
  }
  if (!_ws || _ws.readyState !== 1) {
    _ws = new WebSocket(FORUM_WS_URL);
    _helloOk = false;
    await new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('ws connect timeout 10s')), 10000);
      _ws.addEventListener('open', () => {
        clearTimeout(t);
        _ws.send(JSON.stringify({ type: 'hello' }));
      }, { once: true });
      _ws.addEventListener('message', (ev) => {
        let m;
        try { m = JSON.parse(ev.data); } catch { return; }
        if (m.type === 'welcome') _helloOk = true;
        const p = _recvQueue.shift();
        if (p) p.resolve(m);
      });
      _ws.addEventListener('error', (e) => {
        clearTimeout(t);
        reject(new Error('ws error: ' + (e.message || 'unknown')));
      });
      _ws.addEventListener('close', () => {
        _helloOk = false;
        // 拒绝挂起的请求
        while (_recvQueue.length) _recvQueue.shift().reject(new Error('ws closed'));
      });
    });
  }
  if (!_helloOk) {
    await new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('welcome timeout 8s')), 8000);
      _recvQueue.push({
        resolve: (m) => { clearTimeout(t); m.type === 'welcome' ? resolve() : reject(new Error('not welcome: ' + m.type)); },
        reject: (e) => { clearTimeout(t); reject(e); },
      });
    });
  }
}

function _wsRequest(payload, timeoutMs) {
  return new Promise((resolve, reject) => {
    if (!_ws || _ws.readyState !== 1) return reject(new Error('ws not connected'));
    const pending = {
      resolve: (m) => resolve(m),
      reject: (e) => reject(e),
    };
    const t = setTimeout(() => {
      const idx = _recvQueue.indexOf(pending);
      if (idx >= 0) _recvQueue.splice(idx, 1);
      reject(new Error('ws response timeout ' + timeoutMs + 'ms'));
    }, timeoutMs);
    pending.resolve = (m) => { clearTimeout(t); resolve(m); };
    pending.reject = (e) => { clearTimeout(t); reject(e); };
    _recvQueue.push(pending);
    try { _ws.send(JSON.stringify(payload)); }
    catch (e) { clearTimeout(t); _recvQueue.splice(_recvQueue.indexOf(pending), 1); reject(e); }
  });
}

// ─── helper:安全 filename + sha + size 计算 ──────────────────────
function _validateAttachmentFields(att) {
  if (!att || typeof att !== 'object') return 'attachment required';
  const { filename, mime, sha256, size, data_b64 } = att;
  if (typeof filename !== 'string' || !filename
      || filename.includes('/') || filename.includes('\\') || filename.includes('..')
      || filename.includes('\x00')) return 'bad filename';
  const ALLOWED_MIMES = new Set(['application/gzip', 'application/json',
                                  'application/zip', 'text/plain', 'application/octet-stream']);
  if (!ALLOWED_MIMES.has(mime)) return 'bad mime: ' + mime;
  if (typeof sha256 !== 'string' || sha256.length < 4 || sha256.length > 64) return 'bad sha256';
  if (!Number.isInteger(size) || size < 0 || size > 2 * 1024 * 1024) return 'bad size (max 2MB)';
  if (typeof data_b64 !== 'string' || !data_b64) return 'bad data_b64';
  // sha256 实际校验
  const raw = Buffer.from(data_b64, 'base64');
  const actual = crypto.createHash('sha256').update(raw).digest('base64url').slice(0, 16).replace(/=/g, '');
  if (actual !== sha256) return 'sha256 mismatch (actual=' + actual + ')';
  if (raw.length !== size) return 'size mismatch (raw=' + raw.length + ')';
  return null;
}

// ─── commands ────────────────────────────────────────────────────

// market.list:拉 forum PrisirAI 对话 子版的所有帖,过滤 [Prisir-Workflow] 前缀
ext.registerCommand('market.list', async (args = {}) => {
  await _ensureConnected();
  const since = parseInt(args.since_seq || 0, 10) || 0;
  const r = await _wsRequest({
    type: 'read',
    since_seq: since,
    board: MARKET_BOARD,
    include_takedown: false,
  }, 15000);
  if (r.type !== 'history') return { ok: false, error: 'unexpected: ' + r.type };
  const posts = (r.msgs || [])
    .filter(m => !m.taken_down && !m.retracted)
    .filter(m => (m.post?.body || '').startsWith('[Prisir-Workflow]'))
    .map(m => {
      const p = m.post;
      let meta = {};
      try {
        const after = (p.body || '').split('\n').slice(1).join('\n').trim();
        if (after) meta = JSON.parse(after);
      } catch {}
      return {
        post_id: m.post_id,
        seq: m.seq,
        title: meta.title || (p.body || '').slice(0, 60),
        author_fp: p.author_fp,
        ts: p.ts,
        bundle_filename: p.attachment?.filename,
        bundle_sha256: p.attachment?.sha256,
        bundle_size: p.attachment?.size,
        workflow_count: meta.workflow_count,
        ext_list: meta.ext_list,
        confirmations: m.confirmations,
        confirmed: m.confirmed,
      };
    });
  return { ok: true, posts, last_seq: r.last_seq };
});

// market.fetch:按 post_id 拉具体帖,返 attachment 给 web 端入库
ext.registerCommand('market.fetch', async (args = {}) => {
  if (!args.post_id) return { ok: false, error: 'post_id required' };
  await _ensureConnected();
  const since = parseInt(args.since_seq || 0, 10) || 0;
  const r = await _wsRequest({
    type: 'read', since_seq: since,
    board: MARKET_BOARD, include_takedown: false,
  }, 15000);
  if (r.type !== 'history') return { ok: false, error: 'unexpected: ' + r.type };
  const rec = (r.msgs || []).find(m => m.post_id === args.post_id);
  if (!rec) return { ok: false, error: 'not_found' };
  if (!rec.post.attachment) return { ok: false, error: 'no_attachment' };
  let meta = {};
  try {
    const after = (rec.post.body || '').split('\n').slice(1).join('\n').trim();
    if (after) meta = JSON.parse(after);
  } catch {}
  return {
    ok: true,
    post_id: args.post_id,
    title: meta.title || (rec.post.body || '').slice(0, 60),
    author_fp: rec.post.author_fp,
    ts: rec.post.ts,
    workflow_count: meta.workflow_count,
    attachment: {
      filename: rec.post.attachment.filename,
      mime: rec.post.attachment.mime,
      sha256: rec.post.attachment.sha256,
      size: rec.post.attachment.size,
      data_b64: rec.post.attachment.data_b64,
    },
  };
});

// market.publish:发 workflow bundle 帖到 forum
ext.registerCommand('market.publish', async (args = {}) => {
  if (!args.title) return { ok: false, error: 'title required' };
  if (!args.attachment_filename) return { ok: false, error: 'attachment_filename required' };
  const att = {
    filename: args.attachment_filename,
    mime: args.attachment_mime || 'application/gzip',
    sha256: args.attachment_sha256,
    size: args.attachment_size,
    data_b64: args.attachment_data_b64,
  };
  const attErr = _validateAttachmentFields(att);
  if (attErr) return { ok: false, error: 'attachment invalid: ' + attErr };

  await _ensureConnected();

  // 1) 拼 body:[Prisir-Workflow] 标题 + JSON-LD 元数据
  const meta = {
    title: args.title,
    description: args.description || '',
    workflow_count: args.workflow_count || 0,
    ext_list: args.ext_list || [],
    bundle_filename: att.filename,
    bundle_sha256: att.sha256,
    bundle_size: att.size,
  };
  const body = '[Prisir-Workflow]\n' + JSON.stringify(meta, null, 2);

  // 2) ts(ISO8601,服务端会校 ±10min)
  const ts = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');

  // 3) 算 PoW(把 attachment 也放进 signed_view,保证签名覆盖附件)
  const signedView = {
    v: 1, kind: 'post', board: MARKET_BOARD, parent: null, body,
    author_pub: _authorPub, author_fp: _authorFp, ts,
    pow: { alg: 'sha256-b64', bits: POW_BITS, nonce: 0 },
    attachment: att,
  };
  try {
    signedView.pow.nonce = _solvePow(signedView, POW_BITS);
  } catch (e) {
    return { ok: false, error: 'pow_solve_failed: ' + e.message };
  }

  // 4) 签名:Ed25519 over canon({...signedView})(attachment 已入,自带覆盖)
  const sigPayload = _canon(signedView);
  const sig = crypto.sign(null, Buffer.from(sigPayload), _identity).toString('base64');

  const postObj = { ...signedView, sig };

  // 5) 发帧
  const r = await _wsRequest({ type: 'post', post: postObj }, 30000);
  if (r.type === 'nack') return { ok: false, error: 'forum_nack:' + (r.reason || ''), post: postObj };
  if (r.type !== 'ack') return { ok: false, error: 'unexpected:' + r.type };
  return {
    ok: true,
    post_id: r.post_id,
    seq: r.seq,
    confirmed: r.confirmed,
    board: MARKET_BOARD,
    pow_bits: POW_BITS,
  };
});

// market.identity:返身份信息(供 web 设置面板展示)
ext.registerCommand('market.identity', async () => {
  if (!_identity) {
    _identity = _loadOrCreateIdentity();
    _authorPub = _pubB64Url(_identity);
    _authorFp = _fp(_authorPub);
  }
  return {
    ok: true,
    identity: {
      pub: _authorPub,
      fp: _authorFp,
      identity_file: _identityPath(),
      forum_url: FORUM_WS_URL,
      board: MARKET_BOARD,
      pow_bits: POW_BITS,
    },
  };
});

// market.retract:B-4.F.A(2026-09-21)作者一键自删自己发的 marketplace 帖。
// 协议:发 kind='retract' 帧,parent=目标 post_id,author_fp 必须等于被删帖作者
// fp(forum_relay 验签 + 父帖存在 + fp 一致,任一不过返 nack)。
// 关键设计:
//   1) 用 marketplace ext 自己的 Ed25519 身份签名(跟 publish 同身份)—
//      marketplace_identity.key 是「marketplace 这个 ext」专用,不是「用户本人」,
//      但只要用户 + ext 同一人,语义就成立。
//   2) retract 帧不算主帖,server 端给 POST_ID 算 = sha256(canon(retract obj)),
//      不入主帖流,只标 parent post retracted=true。
//   3) PoW 仍需:retract 跟 post 一样要走 PoW(抗 spam retract),复用 _solvePow。
//   4) **先 read 一次** 校被删帖存在 + author_fp 是自己 — 早失败避免白算 PoW(2s 浪费)。
ext.registerCommand('market.retract', async (args = {}) => {
  const post_id = String(args.post_id || '').trim();
  const reason = String(args.reason || '').slice(0, 200);  // 选填,写进 retract body 留 trace
  if (!post_id) return { ok: false, error: 'post_id required' };

  await _ensureConnected();

  // 1) 拿自身身份(发 retract 必须用前面翻期同身份签)
  if (!_identity) {
    _identity = _loadOrCreateIdentity();
    _authorPub = _pubB64Url(_identity);
    _authorFp = _fp(_authorPub);
  }

  // 2) 早验证:读 history 找目标帖 + 校 fp 是自己
  //    不带 include_takedown(retract 帖不需验 “该帖未撤下”——已撤下不返则用户检不到,早返 not_found)
  const since = parseInt(args.since_seq || 0, 10) || 0;
  const hist = await _wsRequest({
    type: 'read', since_seq: since,
    board: MARKET_BOARD, include_takedown: false,
  }, 15000);
  if (hist.type !== 'history') return { ok: false, error: 'unexpected:' + hist.type };
  const target = (hist.msgs || []).find(m => m.post_id === post_id);
  if (!target) return { ok: false, error: 'post_not_found', hint: '该 post_id 在 marketplace 不可见(可能已撤下 / 已自删 / 从未存在)' };
  if (target.taken_down) return { ok: false, error: 'already_taken_down' };
  if (target.retracted) return { ok: false, error: 'already_retracted' };
  if (target.post?.author_fp !== _authorFp) {
    return {
      ok: false,
      error: 'not_your_post',
      hint: 'marketplace ext 的 Ed25519 身份 fp 与目标帖作者不一致;marketplace_identity.key 丢 / 重生过 / 换机器 都会这样。只能请运营者撤下。',
      your_fp: _authorFp,
      post_author_fp: target.post?.author_fp,
    };
  }

  // 3) 拼 retract 帖:body 可附简短 reason(不暴露内部 trace),parent=post_id
  const ts = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
  const body = reason ? '[retracted] ' + reason : '[retracted] author retract';

  // 4) signed_view(parent 必填,server 校存在)
  const signedView = {
    v: 1, kind: 'retract', board: MARKET_BOARD, parent: post_id, body,
    author_pub: _authorPub, author_fp: _authorFp, ts,
    pow: { alg: 'sha256-b64', bits: POW_BITS, nonce: 0 },
  };
  try {
    signedView.pow.nonce = _solvePow(signedView, POW_BITS);
  } catch (e) {
    return { ok: false, error: 'pow_solve_failed: ' + e.message };
  }

  // 5) 签名
  const sigPayload = _canon(signedView);
  const sig = crypto.sign(null, Buffer.from(sigPayload), _identity).toString('base64');
  const postObj = { ...signedView, sig };

  // 6) 发帧(同 post 帧格式,kind=retract)
  const r = await _wsRequest({ type: 'post', post: postObj }, 30000);
  if (r.type === 'nack') return { ok: false, error: 'forum_nack:' + (r.reason || ''), retract_post: postObj };
  if (r.type !== 'ack') return { ok: false, error: 'unexpected:' + r.type };

  return {
    ok: true,
    post_id,                          // 被删帖 ID
    retract_seq: r.seq,                // retract 帧本身的 seq(用于参考)
    retracted_at: ts,
    reason: body,
    note: '已自删;下次 market.list 该帖不再出现。其它人已在会话中的本地缓存不会自动消失(需重启 wfmodal / 重新查)。',
  };
});

if (require.main === module) {
  ext.start().catch((e) => { console.error('marketplace ext fatal:', e); process.exit(1); });
}

module.exports = ext;
