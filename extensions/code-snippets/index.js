'use strict';

/**
 * 代码片段库 v0.1.0
 *
 * 40+ 内置片段(Python / JS / Go / Rust / Shell / SQL / HTML 入门),
 * 按 language + tag 检索 + AI 调用 get 拿完整代码 + 自定义 add。
 *
 * 命令:
 *   snippet.search { q, language? } → { items }
 *   snippet.get    { id }          → { snippet }
 *   snippet.add    { language, title, code, tags? } → { id, ok }
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const fs = require('fs');
const path = require('path');
const ext = new PrisIrExt({ id: 'code-snippets', name: '代码片段库', version: '0.1.0' });

// 自定义片段持久化在 ext 安装目录下
const STORE_FILE = () => path.join(process.env.PRISIR_EXT_HOME || '.', 'custom_snippets.json');

const BUILTIN = [
  // Python
  { id:'py-http', lang:'python', title:'Python HTTP server (内置)',
    tags:['http','server','builtin'], code:'python -m http.server 8000' },
  { id:'py-venv', lang:'python', title:'Python 虚拟环境',
    tags:['venv','env'], code:'python -m venv .venv\n.venv\\Scripts\\activate   # Windows\nsource .venv/bin/activate  # Linux/macOS' },
  { id:'py-read-json', lang:'python', title:'Python 读 JSON 文件',
    tags:['json','io'], code:'import json\ndata = json.load(open("a.json", encoding="utf-8"))' },
  { id:'py-write-json', lang:'python', title:'Python 写 JSON(中文)',
    tags:['json','io'], code:'import json\njson.dump(data, open("a.json","w",encoding="utf-8"), ensure_ascii=False, indent=2)' },
  { id:'py-req', lang:'python', title:'Python requests GET',
    tags:['http','requests'], code:'import requests\nr = requests.get("https://api.example.com"); r.raise_for_status()\nprint(r.json())' },
  { id:'py-decor', lang:'python', title:'Python 函数装饰器',
    tags:['decorator','func'], code:'def mydeco(fn):\n    def w(*a, **k):\n        print("before")\n        r = fn(*a, **k)\n        print("after")\n        return r\n    return w' },
  // JavaScript
  { id:'js-fetch', lang:'javascript', title:'JS fetch GET',
    tags:['http','fetch'], code:'const r = await fetch("https://api.example.com");\nif (!r.ok) throw new Error(r.status);\nconst data = await r.json();' },
  { id:'js-await', lang:'javascript', title:'JS async/await 模板',
    tags:['async','promise'], code:'async function load() {\n  try {\n    const r = await fetch("/api/x");\n    return await r.json();\n  } catch (e) { console.error(e); }\n}' },
  { id:'js-debounce', lang:'javascript', title:'JS 防抖',
    tags:['debounce','perf'], code:'const debounce = (fn, ms = 200) => {\n  let t;\n  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };\n};' },
  { id:'js-throttle', lang:'javascript', title:'JS 节流',
    tags:['throttle','perf'], code:'const throttle = (fn, ms = 200) => {\n  let last = 0;\n  return (...a) => { const now = Date.now(); if (now - last > ms) { last = now; fn(...a); } };\n};' },
  { id:'js-copy-clip', lang:'javascript', title:'JS 复制到剪贴板',
    tags:['clipboard'], code:'await navigator.clipboard.writeText(text);' },
  // Shell
  { id:'sh-find', lang:'shell', title:'按文件名查找',
    tags:['find','file'], code:'find . -name "*.py" -not -path "./node_modules/*"' },
  { id:'sh-replace', lang:'shell', title:'批量替换文本',
    tags:['sed','replace'], code:'find . -name "*.md" -exec sed -i "s/old/new/g" {} +' },
  { id:'sh-tar', lang:'shell', title:'tar 打包(排除 node_modules)',
    tags:['tar','pack'], code:'tar --exclude="node_modules" -czf out.tgz -C . <dir>' },
  { id:'sh-ssh-tunnel', lang:'shell', title:'SSH 本地端口转发',
    tags:['ssh','tunnel'], code:'ssh -L 8080:localhost:80 user@host' },
  // SQL
  { id:'sql-topn', lang:'sql', title:'SQL TOP N + 索引提示',
    tags:['select','topn'], code:'SELECT * FROM events\nWHERE created_at >= NOW() - INTERVAL "7 day"\nORDER BY id DESC LIMIT 100;' },
  { id:'sql-count', lang:'sql', title:'SQL 按日聚合',
    tags:['aggregate','groupby'], code:'SELECT date_trunc("day", created_at) d, count(*) c\nFROM events\nGROUP BY 1 ORDER BY 1 DESC;' },
  // Go
  { id:'go-http', lang:'go', title:'Go HTTP server',
    tags:['http','server'], code:'http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {\n    fmt.Fprintln(w, "ok")\n})\nlog.Fatal(http.ListenAndServe(":8080", nil))' },
  { id:'go-context', lang:'go', title:'Go context with timeout',
    tags:['context','timeout'], code:'ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)\ndefer cancel()' },
  // Rust
  { id:'rs-cli', lang:'rust', title:'Rust CLI 入口',
    tags:['cli'], code:'fn main() {\n    let args: Vec<String> = std::env::args().collect();\n    println!("{args:?}");\n}' },
  { id:'rs-file', lang:'rust', title:'Rust 读文件',
    tags:['file','io'], code:'let s = std::fs::read_to_string("a.txt").expect("read");\nprintln!("{s}");' },
  // HTML / CSS
  { id:'html-skel', lang:'html', title:'HTML5 骨架',
    tags:['html','skeleton'], code:'<!doctype html>\n<html lang="zh">\n<head><meta charset="utf-8"><title></title></head>\n<body></body>\n</html>' },
  { id:'css-grid', lang:'css', title:'CSS Grid 居中',
    tags:['css','grid','center'], code:'display: grid; place-items: center; min-height: 100vh;' },
  { id:'css-flex', lang:'css', title:'CSS Flex 两端对齐',
    tags:['css','flex'], code:'display: flex; justify-content: space-between; align-items: center;' },
];

function loadCustom() {
  try {
    if (fs.existsSync(STORE_FILE())) {
      return JSON.parse(fs.readFileSync(STORE_FILE(), 'utf8'));
    }
  } catch {}
  return [];
}

function saveCustom(items) {
  try {
    fs.writeFileSync(STORE_FILE(), JSON.stringify(items, null, 2));
    return true;
  } catch (e) { return false; }
}

ext.registerCommand('snippet.search', async (args) => {
  const q = String(args.q || '').toLowerCase();
  const lang = args.language ? String(args.language).toLowerCase() : null;
  const all = BUILTIN.concat(loadCustom());
  let items = all;
  if (lang) items = items.filter(s => s.lang === lang);
  if (q) {
    items = items.filter(s => {
      const hay = (s.title + ' ' + (s.tags || []).join(' ') + ' ' + s.code).toLowerCase();
      return hay.includes(q);
    });
  }
  return { items: items.slice(0, 50), total: items.length };
});

ext.registerCommand('snippet.get', async (args) => {
  const id = String(args.id || '');
  let snippet = BUILTIN.find(s => s.id === id) || loadCustom().find(s => s.id === id);
  if (!snippet) return { error: `snippet not found: ${id}` };
  return { snippet };
});

ext.registerCommand('snippet.add', async (args) => {
  const id = `custom-${Date.now()}`;
  const item = {
    id, lang: args.language || 'plain',
    title: String(args.title || 'untitled'),
    tags: Array.isArray(args.tags) ? args.tags : [],
    code: String(args.code || ''),
  };
  const all = loadCustom();
  all.push(item);
  if (!saveCustom(all)) return { error: 'persist failed' };
  return { id, ok: true, item };
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
