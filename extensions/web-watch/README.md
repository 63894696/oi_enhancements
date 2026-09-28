# web-watch v0.1.0 — URL 内容监控 + Windows toast 通知

盯一个 URL,周期性抓,内容变化 / 抓取失败 / 恢复 都弹 Windows toast。
零 npm 依赖(Node 内置 `node:sqlite` + `child_process` + `crypto`),
抓取复用主进程 `prisir_work.web_fetch.fetch`(并发竞速 + 7d 缓存 + SSRF 兜底)。

## 命令

| 命令 | 参数 | 返回 |
| --- | --- | --- |
| `watch.add`    | `{ url, interval_sec?, selector? }` | `{ ok, id, watch }` |
| `watch.list`   | `{ limit? }`                        | `{ ok, watches, total }` |
| `watch.remove` | `{ id }`                            | `{ ok, removed }` |
| `watch.run`    | `{ id, force? }`                    | `{ ok, run_id, changed, summary }` |
| `watch.check`  | `{ all: true }` 或 `{ id }`         | `{ ok, ran, changed }` |

- `interval_sec` 默认 600(10 分钟),范围 [10, 86400]
- `selector` 是可选 CSS 选择器(只对 HTML 内容生效);极简实现,够 `body` `#main` `.price` 这种
- 变化检测:SHA256(normalize(content, selector))
- 首次抓取只存 hash 不告警;后续变化才弹 toast

## 用例

### 盯 GitHub releases
```
watch.add({ url: 'https://api.github.com/repos/microsoft/vscode/releases/latest', interval_sec: 3600 })
```

### 盯竞品/价格页
```
watch.add({ url: 'https://example.com/pricing', selector: '.price-table', interval_sec: 1800 })
```

### 盯论坛公告
```
watch.add({ url: 'https://bbs.babelspan.com/forum', selector: '#announce', interval_sec: 300 })
watch.check({ all: true })   ← 手动一次跑齐
```

### 失败恢复
抓取连续失败 → 写 `fetch_failed` 通知;再次成功 → 写 `recovered`(若旧 hash 仍在则继续比对)。
所有失败都不阻塞主流程,只写 SQLite + 弹 toast;toast 本身失败也只是 log warning。

## 存储

- SQLite:`PRISIR_EXT_HOME/state.db`(跟 task-runner 同款,独立)
- `watches` 表(id/url/selector/interval/last_hash/last_checked_at/...)
- `notifications` 表(id/watch_id/url/detected_at/diff_kind/summary/read)

## toast 截图占位

```
┌──────────────────────────────────┐
│ PrisirAI                         │
│ ─────────────────────────────────│
│ Web-Watch content_changed        │
│ https://example.com/pricing      │
│ 内容变化(382 bytes, fetcher=...) │
└──────────────────────────────────┘
```

实际样式跟系统主题一致(走 ToastText02)。`notify.ps1` 是单独的手动调用入口:

```powershell
powershell -File notify.ps1 "Title here" "Message here"
```
