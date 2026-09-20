'use strict';

/**
 * Base64 编解码 v0.1.0
 *
 * 命令:
 *   b64.encode { text, url_safe?, hex? } → { encoded, mode }
 *   b64.decode { text, url_safe? }       → { decoded, hex? }
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const ext = new PrisIrExt({ id: 'base64-codec', name: 'Base64 编解码', version: '0.1.0' });

function detectB64(s) {
  const cleaned = String(s || '').trim();
  if (/^[A-Za-z0-9+/]+=*$/.test(cleaned)) return 'standard';
  if (/^[A-Za-z0-9\-_]+=*$/.test(cleaned)) return 'urlsafe';
  return null;
}

ext.registerCommand('b64.encode', async (args) => {
  const text = String(args.text || '');
  const buf = Buffer.from(text, 'utf8');
  if (args.hex) {
    return { encoded: buf.toString('hex'), mode: 'hex' };
  }
  const urlSafe = !!args.url_safe;
  let encoded = buf.toString('base64');
  if (urlSafe) encoded = encoded.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return { encoded, mode: urlSafe ? 'urlsafe' : 'standard', bytes_in: buf.length };
});

ext.registerCommand('b64.decode', async (args) => {
  const text = String(args.text || '').trim();
  const detected = detectB64(text);
  let std = text;
  if (detected === 'urlsafe') {
    std = text.replace(/-/g, '+').replace(/_/g, '/');
    while (std.length % 4) std += '=';
  }
  try {
    const buf = Buffer.from(std, 'base64');
    return { decoded: buf.toString('utf8'), mode: detected || 'standard', bytes_out: buf.length };
  } catch (e) {
    return { error: e.message };
  }
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
