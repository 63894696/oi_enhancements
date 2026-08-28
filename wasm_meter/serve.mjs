import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const here = dirname(fileURLToPath(import.meta.url));
const wasm = readFileSync(join(here, 'target', 'wasm32-unknown-unknown', 'release', 'wasm_meter.wasm'));
const html = readFileSync(join(here, 'index.html'));
createServer((req, res) => {
  if (req.url === '/m.wasm') { res.writeHead(200, {'Content-Type':'application/wasm'}); res.end(wasm); }
  else { res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'}); res.end(html); }
}).listen(18931, '127.0.0.1', () => console.log('serving on 18931'));
