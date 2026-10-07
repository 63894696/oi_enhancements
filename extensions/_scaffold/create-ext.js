#!/usr/bin/env node
'use strict';

/**
 * PrisirAI 扩展脚手架 — `node create-ext.js <ext-id> [display-name]`
 *
 * 用法:
 *   cd extensions/_scaffold
 *   node create-ext.js my-ext "我的扩展"
 *
 * 产出:
 *   ../my-ext/
 *     package.json
 *     index.js
 *     README.md
 *
 * 5 分钟出 demo,然后:
 *   cd ../my-ext
 *   npm install   # 装 SDK
 *   # 在 PrisirAI 抽屉里:📦 安装本地扩展 → ../my-ext → 启用
 */

const fs = require('fs');
const path = require('path');

const extId = (process.argv[2] || '').trim();
const displayName = (process.argv[3] || '').trim() || extId;

if (!extId || !/^[a-z0-9][a-z0-9_-]{0,31}$/.test(extId)) {
  console.error('用法: node create-ext.js <ext-id> [display-name]');
  console.error('  ext-id: 小写字母/数字/-/_,首字符必须字母或数字');
  console.error('  例:    node create-ext.js my-ext "我的扩展"');
  process.exit(1);
}

const here = __dirname;
const target = path.resolve(here, '..', extId);

if (fs.existsSync(target)) {
  console.error('目录已存在:', target);
  process.exit(1);
}

fs.mkdirSync(target, { recursive: true });

// ── package.json ──
// manifest schema 字段(详见 docs/extension-spec.md §3):
//   prisIrPermissions  L0/L1/L2 权限列表,主进程按风险等级弹卡
//   prisIrFeatures     ['read-only', 'needs-native', 'requires-local-server', ...]
//                      用于商店筛选 / AI inventory 决定能否本类操作
//   prisIrPlatforms    ['win32', 'darwin', 'linux']  跨平台支持(空数组 = 不声明 = 主进程默认 win32)
const pkg = {
  name: extId,
  displayName,
  version: '0.1.0',
  description: `${displayName} — PrisirAI 扩展`,
  author: 'Your Name <you@example.com>',
  main: 'index.js',
  type: 'commonjs',
  license: 'MIT',
  prisIrPermissions: [
    'ai.invoke.command:hello.world',
    'ui.inject.card',
  ],
  prisIrFeatures: [],
  prisIrPlatforms: [],
  dependencies: {
    '@prisir/extension-sdk': 'file:../sdk',
  },
};
fs.writeFileSync(path.join(target, 'package.json'),
  JSON.stringify(pkg, null, 2) + '\n', 'utf8');

// ── index.js ──
const js = `'use strict';

/**
 * ${displayName} v0.1.0
 * 由 extensions/_scaffold/create-ext.js 生成
 */

const { PrisIrExt } = require('@prisir/extension-sdk');

const ext = new PrisIrExt({
  id: '${extId}',
  name: '${displayName}',
  version: '0.1.0',
});

// 注册命令(主进程可调,AI 也可调)
ext.registerCommand('hello.world', async (args, ctx) => {
  const name = String(args.name || 'world');
  const greeting = args.lang === 'zh' ? '你好' : 'Hello';
  return { message: \`\${greeting}, \${name}!\` };
});

// 注册会话消息钩子(AI 输出文本时触发)
ext.onSessionMessage(async (msg, ctx) => {
  if (!ctx.sessionId) return;
  const text = String(msg.text || '');
  // 简单触发:用户/AI 提到 "${extId}" 就推一张欢迎卡
  if (text.includes('${extId}')) {
    ext.injectCard({
      sessionId: ctx.sessionId,
      cardId: \`\${extId}-\${Date.now()}\`,
      html: \`<div class="ext-card">
  <div class="ext-card-head">
    <span class="ext-badge">🧩 \${ext.name}</span>
    <span class="ext-title">v\${ext.meta.version}</span>
  </div>
  <div class="ext-card-body" style="background:#fdfcf8;padding:14px">
    <div style="color:#2f3a34;font-size:14px">
      👋 你好!这是 <b>\${ext.name}</b> 注入的卡片。
    </div>
    <div style="color:#7a8580;font-size:11px;margin-top:6px">
      修改本文件 (extensions/${extId}/index.js) 即可定制行为。
    </div>
  </div>
</div>\`,
    });
  }
});

ext.start().catch((e) => {
  console.error('start failed:', e.message);
  process.exit(1);
});
`;
fs.writeFileSync(path.join(target, 'index.js'), js, 'utf8');

// ── README.md ──
const readme = `# ${displayName}

> PrisirAI 扩展(由 \`extensions/_scaffold/create-ext.js\` 生成)

## 安装

\`\`\`bash
cd ../${extId}
npm install
\`\`\`

## 注册到 PrisirAI

打开 PrisirAI → 顶栏 🧩 扩展 → 📦 安装本地扩展 → 选本目录路径(\`extensions/${extId}\`)→ 启用。

## 开发

改 \`index.js\` → 在抽屉里点 ⏸ 禁用 → ▶ 启用(自动重启 Node 子进程)。

## 命令列表

- \`hello.world\` — 打招呼,返回 \`{message: "Hello, world!"}\`

## 钩子

- \`onSessionMessage\` — AI 输出文本时触发,检测到 "${extId}" 关键词就推卡片

## 参考

- 协议:[\`docs/prisir-extension-api-v0.1.md\`](../../docs/prisir-extension-api-v0.1.md)
- SDK:[\`extensions/sdk/index.js\`](../sdk/index.js)
- 示例:[\`extensions/ext-mermaid/index.js\`](../ext-mermaid/index.js)
`;
fs.writeFileSync(path.join(target, 'README.md'), readme, 'utf8');

// ── .gitignore ──
fs.writeFileSync(path.join(target, '.gitignore'),
  'node_modules/\n*.log\n.DS_Store\n', 'utf8');

console.log('✅ 已生成:', target);
console.log('   ├─ package.json');
console.log('   ├─ index.js');
console.log('   ├─ README.md');
console.log('   └─ .gitignore');
console.log('');
console.log('下一步:');
console.log('  cd ' + path.relative(process.cwd(), target));
console.log('  npm install');
console.log('  # 然后在 PrisirAI 🧩 抽屉里:📦 安装本地扩展 → 选本目录');
