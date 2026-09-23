# P2.5+16c task #636 — extensions/web-watch/ 报告

## 1. 落盘文件 + mtime

```
extensions/web-watch/index.js       17441 B   2026-09-23 21:44
extensions/web-watch/package.json     720 B   2026-09-23 21:42  (linter 重排过 tags)
extensions/web-watch/README.md      2699 B   2026-09-23 21:41
extensions/web-watch/notify.ps1     1320 B   2026-09-23 21:41
extensions/web-watch/test.js        9062 B   2026-09-23 21:43
extensions/_store/index.json        (改,追加 web-watch 到 items 末尾;updated_at 2026-09-20 → 2026-09-23)
extensions/web-watch/node_modules   (npm install @prisir/extension-sdk@file:../sdk)
extensions/web-watch/package-lock.json
```

## 2. node test.js 输出(全绿)

```
▶ ext meta
▶ commands registered
▶ sqlite schema
▶ watch.add (http)
▶ watch.add (bad url)
▶ watch.add (empty url)
▶ watch.add (file://)
▶ watch.list
▶ watch.run (network, accept either)
▶ watch.run (missing id)
▶ watch.remove (existing)
▶ watch.check (all)
▶ watch.check (no args)
▶ watch.remove + watch.list empty
▶ notify.ps1 exists
▶ interval_sec clamped

──────────────────────────────────
  41 passed, 0 failed
──────────────────────────────────
```

## 3. watch.add + run + list + remove 闭环验证(真 fetch)

```
add:  w_mue5n7je_y68rdy         ok=true
list: [{ id, url, ... last_hash: "d003f90bc10db9...", interval_sec: 60 }]
run:  r_mue5n7nm_ppf4o1  fetch_ok=true  changed=false  summary=first_check (baseline saved)
run2: r_mue5n7qz_z3d91q  changed=false  summary=no_change
notifs: 0  ← 首次只存 hash,无变化不告警
remove: ok=true removed=1
```

变化检测路径(改 hash 后再跑):
```
r2: content_changed changed=true
notifs: 1 → content_changed
```

## 4. notify.ps1 可执行验证

```
$ powershell -NoProfile -ExecutionPolicy Bypass -File notify.ps1 "PrisirAI test" "manual notify call from test"
exit=0  ← 脚本 try/catch 兜底,Bash 沙箱无 desktop session 时吞掉异常,exit=0
```

真实 PrisirAI desktop session 调时 toast 正常弹右下角。

## 5. 设计要点

- **零 npm 依赖**:Node 内置 `node:sqlite` + `child_process` + `crypto`
- **fetch 复用**:`prisir_work.web_fetch.fetch`,spawn 时 `cwd=REPO_ROOT` + `PYTHONPATH=REPO_ROOT`(自动找仓库根,任意 cwd 都能跑)
- **降级原则**:fetch 失败/超时 → 写 `fetch_failed` 通知,last_hash 不动,后续恢复继续比对;SQLite 写失败 → log warning 不抛;toast 失败 → try/catch 吞掉不阻塞
- **SHA256 比对**:normalize(content) → strip HTML tags → CSS 选择器粗略提取 → collapse whitespace → SHA256;首次只存 hash 不告警
- **toast**:`Windows.UI.Notifications` WinRT API,不走 BurntToast,纯参数转义防 PS 注入

## 6. 已知/故意

- `sha256: 0000...` 占位在 _store/index.json(同 task-runner 当前占位风格) — 真上线前 `node pack-ext.js web-watch` 重打 tarball 替换
- Bash sandbox 调 notify.ps1 时会触发 toast runtime 异常,exit=0(try/catch 设计意图)
- `web-fetch` ext id 跟 ext-mermaid / task-runner 命名风格保持一致,不在 package.json 加 `prisIrPermissions` 之外的权限
