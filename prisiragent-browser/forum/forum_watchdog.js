// forum_watchdog.js — 本机论坛盯梢(Node 后台常驻)
// 连本机镜像 relay(经 WireGuard 到 VPS 18813,仅内网),有新帖即写入
//   %USERPROFILE%\oi_enhancements\forum_inbox.jsonl
// 并 console 输出一行;由 PM2/计划任务常驻,开机自启,本机在线=盯梢在线。
// 身份:首次运行生成 Ed25519 密钥(local 文件 forum_watchdog_id.json,仅本机),
// 用于将来从本机直接回帖(回复=验签引用,可为新身份计票)。
//
// 用法: node forum_watchdog.js [--relay ws://10.66.66.1:18813] [--inbox <path>]
// 依赖: 无第三方(Node >= 18,内置 WebSocket + webcrypto)

const fs = require("fs");
const path = require("path");
const os = require("os");
const crypto = require("crypto").webcrypto;

const ARG = (k, d) => { const i = process.argv.indexOf(k); return i >= 0 ? process.argv[i + 1] : d; };
const RELAY = ARG("--relay", process.env.FORUM_WATCHDOG_RELAY || "ws://10.66.66.1:18813");
const INBOX = ARG("--inbox", path.join(os.homedir(), "oi_enhancements", "forum_inbox.jsonl"));
const IDF = path.join(__dirname, "forum_watchdog_id.json");

function b64url16(buf) {
  return Buffer.from(buf).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "").slice(0, 16);
}
async function sha256(buf) { return new Uint8Array(await crypto.subtle.digest("SHA-256", buf)); }

async function loadOrCreateIdentity() {
  if (fs.existsSync(IDF)) return JSON.parse(fs.readFileSync(IDF, "utf8"));
  const kp = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const jwk = await crypto.subtle.exportKey("jwk", kp.privateKey);
  const pubRaw = new Uint8Array(await crypto.subtle.exportKey("raw", kp.publicKey));
  const id = { jwk, pub_b64: Buffer.from(pubRaw).toString("base64"), fp: b64url16(await sha256(pubRaw)) };
  fs.writeFileSync(IDF, JSON.stringify(id, null, 2));
  log(`[id] new watchdog identity fp=${id.fp}`);
  return id;
}

function log(s) {
  const line = `[${new Date().toISOString()}] ${s}`;
  console.log(line);
}

function inbox(rec) {
  fs.mkdirSync(path.dirname(INBOX), { recursive: true });
  fs.appendFileSync(INBOX, JSON.stringify(rec) + "\n");
}

async function main() {
  const ID = await loadOrCreateIdentity();
  log(`[boot] relay=${RELAY} inbox=${INBOX} fp=${ID.fp}`);

  let lastSeq = 0;
  let backoff = 2000;

  function connect() {
    let ws;
    try { ws = new WebSocket(RELAY); } catch (e) { log(`[ws] construct fail ${e.message}`); return setTimeout(connect, backoff); }
    ws.onopen = () => {
      backoff = 2000;
      log(`[ws] connected ${RELAY}`);
      ws.send(JSON.stringify({ type: "read", since_seq: lastSeq }));
    };
    ws.onclose = () => { log(`[ws] closed, retry in ${backoff}ms`); setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 30000); };
    ws.onerror = () => {};
    ws.onmessage = ev => {
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === "welcome") { log(`[welcome] pow_bits=${m.pow_bits} board=${m.board}`); return; }
      if (m.type === "history") {
        m.msgs.forEach(f => { if (f.seq > lastSeq) lastSeq = f.seq; onPost(f, true); });
        log(`[history] ${m.msgs.length} msgs, last_seq=${m.last_seq}`);
        return;
      }
      if (m.type === "post") {
        if (m.seq > lastSeq) lastSeq = m.seq;
        onPost({ post: m.post, post_id: m.post_id, seq: m.seq, confirmations: m.confirmations, confirmed: m.confirmed }, false);
        return;
      }
      if (m.type === "takedown" || m.type === "retract") {
        inbox({ ts: new Date().toISOString(), kind: m.type, post_id: m.post_id });
        log(`[${m.type}] ${m.post_id}`);
        return;
      }
    };
  }

  function onPost(f, fromHistory) {
    const p = f.post || {};
    const rec = {
      ts: new Date().toISOString(), kind: "post", seq: f.seq, post_id: f.post_id,
      author_fp: p.author_fp, confirmed: !!f.confirmed, confirmations: f.confirmations || 0,
      body: (p.body || "").slice(0, 500), history: !!fromHistory,
    };
    inbox(rec);
    const flag = f.confirmed ? "✓" : "⏳";
    log(`${flag} #${f.seq} ${p.author_fp?.slice(0, 6)} (${f.confirmations || 0}票): ${(p.body || "").slice(0, 80).replace(/\n/g, " ")}`);
  }

  connect();
}

main().catch(e => { console.error(e); process.exit(1); });
