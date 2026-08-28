// test_forum_canon.js — F-1a 对拍:JS 端 canon/sha256/Ed25519 须与 test_forum_canon.py 逐字节一致
// 用法: node test_forum_canon.js   (Node >= 20,用 webcrypto)
// 读 forum_test_vectors.json(Python 端真实计算产物),逐条重算并断言一致。
const { webcrypto } = require("crypto");
const fs = require("fs");

// ── 与 prisIr-browser/mixin/group.html:158-167 逐行对齐 ──
const enc = new TextEncoder();
function b64(buf) { return Buffer.from(new Uint8Array(buf)).toString("base64"); }
function b64d(s) { return new Uint8Array(Buffer.from(s, "base64")); }
function b64url16(buf) { return b64(buf).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "").slice(0, 16); }
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

(async () => {
  const data = JSON.parse(fs.readFileSync(__dirname + "/forum_test_vectors.json", "utf-8"));
  let pass = 0, fail = 0;
  for (const { tag, post } of data.cases) {
    const fails = [];
    // 1. canon + PoW:重算 sha256(canon(无 sig/post_id)) 须满足 pow.bits 且复现
    const signed = Object.fromEntries(Object.entries(post).filter(([k]) => k !== "sig" && k !== "post_id"));
    const d1 = new Uint8Array(await webcrypto.subtle.digest("SHA-256", enc.encode(canon(signed))));
    if (!checkPow(d1, post.pow.bits)) fails.push("PoW 不满足");
    // 2. 签名验真:用 author_pub 验 sig 对 canon(signed)
    try {
      const pub = await webcrypto.subtle.importKey("raw", b64d(post.author_pub), { name: "Ed25519" }, false, ["verify"]);
      const ok = await webcrypto.subtle.verify({ name: "Ed25519" }, pub, b64d(post.sig), enc.encode(canon(signed)));
      if (!ok) fails.push("签名验签失败");
    } catch (e) { fails.push("验签异常: " + e.message); }
    // 3. author_fp 一致性:sha256(pub_raw) → b64url16
    const dfp = new Uint8Array(await webcrypto.subtle.digest("SHA-256", b64d(post.author_pub)));
    if (b64url16(dfp) !== post.author_fp) fails.push("author_fp 不一致");
    // 4. post_id 一致性:sha256(canon(含 sig,无 post_id)) → b64url16
    const full = Object.fromEntries(Object.entries(post).filter(([k]) => k !== "post_id"));
    const d2 = new Uint8Array(await webcrypto.subtle.digest("SHA-256", enc.encode(canon(full))));
    if (b64url16(d2) !== post.post_id) fails.push(`post_id 不一致: JS=${b64url16(d2)} PY=${post.post_id}`);
    if (fails.length) { fail++; console.log(`✗ ${tag}: ${fails.join("; ")}`); }
    else { pass++; console.log(`✓ ${tag}  post_id=${post.post_id}  fp=${post.author_fp}`); }
  }
  console.log(`\n${pass}/${pass + fail} 通过`);
  process.exit(fail ? 1 : 0);
})();
