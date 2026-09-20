# PrisirAI 装包策略 memo — 旅行助手 Phase 2 期间

> 状态:Phase 2 启动期一次性确认 · 2026-09-19
> 决策:**Phase 2 + 关联功能开发期间不打安装包,等使用测试 + 关联功能就绪后统一打**

---

## 1. 为什么现在不打

1. **PyInstaller frozen 的 `dist\PrisirAI.exe` 不带运行时新增代码** — 任何 `oiagent_web.py` / `prisIr_calendar/` / `prisIragent_calendar/` 的改动都要重打 exe 才生效
2. **旅行助手是新产品线** — 用户还在认知阶段,频繁装包体验差,先在 dev 模式跑稳
3. **关联功能开发同期进行** — 视频笔记内容、推荐模型等可能并行 ship,装包应等所有改动汇总后一次性打
4. **NSIS MUI_ICON 修复**(`task #20`)+ **Tauri 托盘菜单新增**(`task #19`)+ **calendar.rs 子进程**(`task #21`)等 Rust 端改动,需 `cargo build --release` 重出 `prisirai-shell.exe`

## 2. 期间 dev 用户怎么用

### Windows dev 工作流
```bash
# 1. 起 prisIragent_web.py(绑 18880)
cd C:\Users\Administrator\oi_enhancements
python prisIragent_web.py --port 18880

# 2. 浏览器打开
start http://localhost:18880/prisiragent/calendar

# 3. 任务栏图标右键测试(需要 cargo build 一次)
cd prisIragent-tauri/src-tauri
cargo build --release
../target/release/prisirai-shell.exe
```

### 校验 checklist
- [ ] `pytest prisIr_calendar/tests/` → 176+ 测试绿
- [ ] `cargo check` (task #19 + #21 后)
- [ ] `curl http://localhost:18880/prisiragent/api/calendar/timeline?days=14` → JSON
- [ ] 浏览器手动打开 `/prisIragent/calendar` → 双按钮时间线
- [ ] 任务栏右键 → 「📅 打开日历」菜单项存在 + 点击可触发

## 3. 何时重打(以及打什么)

### 触发条件(任一)
- 旅行助手 Phase 2 E2E 通过(task #25)
- 关联功能(视频笔记 / 推荐模型 / 其他)ship 到位
- NSIS 任何代码改动
- Tauri 任何 .rs 文件改动

### frozen 必须包含的新文件清单(给 PyInstaller 重打准备)
```
# 新增 Python 包
prisIr_calendar/                    (~5500 行 Python + tests)
prisIragent_calendar/                (UI 静态文件)

# 新增 Python 模块(已存在目录)
user_profile.py                      (+280 行 travel slot)

# 修改文件(frozen 覆盖)
prisIragent_web.py                   (+5 路由 +6 handler)
prisIragent_coworker/permissions/    (+5 文件:calendar_* + maps_*)
oiagent_web.py                       (+1 _PRESET_KEYWORDS 元组)
solutions_learner.py                 (+1 CATEGORIES 项)
docs/preset-solutions-index.md       (+1 行)
docs/prisIr-calendar-*.md            (+3 设计文档,docs/ 已被 frozen 包含)

# 修改 Rust 文件(独立 cargo build)
prisIragent-tauri/src-tauri/src/lib.rs           (+1 托盘菜单项)
prisIragent-tauri/src-tauri/src/calendar.rs      (新文件,music.rs 模式)
prisIragent-tauri/src-tauri/Cargo.toml           (+1 open 依赖)
installer/prisirai.nsi                           (MUI_ICON 改路径)
```

### 不进 frozen 的文件
- `tests/screenshots/` — 历史 E2E 产物,装包不需要
- `.tmp_*` — 调试临时
- `installer/_staging/` — 装包阶段临时
- `.tmp_extract/` — 调试临时

## 4. 装包流程(沿用现有)

```
1. PyInstaller 重打
   pyinstaller PrisirAI-core.spec  → dist/PrisirAI.exe

2. Tauri 重编
   cd prisIragent-tauri/src-tauri
   cargo build --release
   → target/release/prisirai-shell.exe

3. NSIS 打包
   cd installer
   makensis prisirai.nsi
   → dist/PrisirAI-Setup-2.x.x.exe

4. 烟测(VM001 镜像)
   - 装上,起服务,确认任务栏右键看到「📅 打开日历」
   - 点击 → 浏览器打开 calendar
   - 添加事件 + dismiss + scan 全流程通
```

## 5. 反 flattery 标注

- **不**承诺装包时间表(用户拍板「等使用测试 + 关联功能开发到位」)
- **不**把现有 frozen exe 描述为「已含旅行助手」(它没有)
- **不**为 dev 模式做 UI 美化(装包后才有 polish)

## 6. 关联参考

- [PrisirAI 整合方案](docs/../) — 总架构
- [PrisirAI 开发者模式装包(已废弃)](../../.claude/projects/C--Users-Administrator-oi-enhancements/memory/prisirai-devmode-anchor.md) — 历史备忘,纯用户装包后 dev 模式移除
- [PrisirAI-Setup 本机+VM001 双侧验证通过](../../.claude/projects/C--Users-Administrator-oi-enhancements/memory/prisirai-setup-host-vm001-verified.md) — 装包双侧验证流程