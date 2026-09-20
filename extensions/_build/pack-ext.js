#!/usr/bin/env node
'use strict';

/**
 * PrisirAI 扩展打包工具 — `node pack-ext.js <ext-id> [out-dir]`
 *
 * 默认 in: ../<ext-id>/
 *       out: ~/.prisir/store/<ext-id>-<version>.tgz
 *
 * 产出纯 tarball(无 node_modules),装时主进程会 provision SDK。
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync } = require('child_process');
const crypto = require('crypto');

const extId = (process.argv[2] || '').trim();
if (!extId || !/^[a-z0-9][a-z0-9_-]{0,31}$/.test(extId)) {
  console.error('用法: node pack-ext.js <ext-id> [out-dir]');
  process.exit(1);
}

const here = __dirname;
const src = path.resolve(here, '..', extId);
if (!fs.existsSync(src)) {
  console.error('扩展目录不存在:', src);
  process.exit(1);
}

const pkgPath = path.join(src, 'package.json');
const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
const version = pkg.version || '0.1.0';

const outDir = process.argv[3]
  ? path.resolve(process.argv[3])
  : path.join(os.homedir(), '.prisir', 'store');
fs.mkdirSync(outDir, { recursive: true });

const tgzName = `ext-${extId}-${version}.tgz`;
const tgzPath = path.join(outDir, tgzName);
// 临时 .tar 落在 home(~/.prisir/_tmp/),避开 os.tmpdir 在 MSYS 下路径被 tar 误判的问题
const tmpDir = path.join(os.homedir(), '.prisir', '_tmp');
fs.mkdirSync(tmpDir, { recursive: true });
const tmpTar = path.join(tmpDir, `__pack_${extId}_${Date.now()}.tar`);

// MSYS tar 不会把 -C C:\... 当成本地盘符解析,改用 MSYS 路径 + cygpath 转
const srcMsys = (() => {
  // C:\Users\... → /c/Users/...
  if (/^[A-Za-z]:[\\\/]/.test(src)) return '/' + src[0].toLowerCase() + src.slice(2).replace(/\\/g, '/');
  return src.replace(/\\/g, '/');
})();
const tmpTarMsys = (() => {
  if (/^[A-Za-z]:[\\\/]/.test(tmpTar)) return '/' + tmpTar[0].toLowerCase() + tmpTar.slice(2).replace(/\\/g, '/');
  return tmpTar.replace(/\\/g, '/');
})();

// 1) 用 tar 把 src/* 打成未压缩的 .tar(显式跳过 node_modules / .git / .log)
try {
  execSync(
    `tar --exclude='node_modules' --exclude='.git' --exclude='*.log' ` +
    `--exclude='__pycache__' -cf "${tmpTarMsys}" -C "${srcMsys}" .`,
    { stdio: ['ignore', 'inherit', 'inherit'], shell: 'bash' }
  );
} catch (e) {
  console.error('tar 失败:', e.message);
  process.exit(1);
}

// 2) gzip 压缩
execSync(`gzip -f "${tmpTarMsys}"`, { stdio: 'inherit', shell: 'bash' });
const gzPath = tmpTar + '.gz';
fs.renameSync(gzPath, tgzPath);

// 3) 计算 SHA256
const hash = crypto.createHash('sha256');
hash.update(fs.readFileSync(tgzPath));
const sha256 = hash.digest('hex');

const size = fs.statSync(tgzPath).size;

console.log(JSON.stringify({
  ext_id: extId,
  version,
  tgz_path: tgzPath,
  tgz_name: tgzName,
  size_bytes: size,
  sha256,
}, null, 2));
