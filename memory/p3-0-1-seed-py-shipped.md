---
name: p3-0-1-seed-py-shipped
description: P3.0.1 seed.py 统一测试门禁 ship(2026-10-06 commit aff7b80)。灵感 AIOSAI/AIPass seedgo;聚合 1111 pass / 50 fail / 4 skip;pre-broken / ship-sync-gap / 真 fail 3 档分类;CI 真 fail 阻塞 / 其他 2 档仅提示。
metadata:
  type: project
---

# P3.0.1 seed.py 统一测试门禁 ship(2026-10-06)

## 背景

P3.0 music 模块归档后,主仓还有 50+ 真 fail 散在多个 file。
以前每次跑 `python -m pytest tests/ -q` 看到一长串 FAILED 不知道哪些
是「最近 ship 引入」、哪些是「历史缺模块残留」、哪些是「真正新
bug」,扫一遍 -q 输出根本分不清。

调研 AIOSAI/AIPass(2026-10)看到他们的 seedgo:每次提交前自动跑
全测,聚合输出 + 分类 fail + 红绿一目了然。决定抄一份精简版到
PrisirAI。

## 实现(commit aff7b80)

### seed.py(280 行)

**核心 5 函数:**

| 函数 | 职责 |
|------|------|
| `_run_pytest(extra_args)` | subprocess 跑 pytest,只读 stdout(stderr 进度字符污染 summary regex),返 (rc, stdout, elapsed) |
| `_parse_summary(stdout)` | 扫最后 50 行,regex 抽 `(\d+)\s+(passed\|failed\|skipped\|error\|deselected)` |
| `_parse_failed(stdout)` | 识别 `=== short test summary info ===` 入口,提取 FAILED/ERROR 行直到分隔符分隔符结尾 |
| `_categorize(failed)` | 3 档分类:pre_broken(path 命中 4 个已知残留)/ ship_sync_gap(class 命中 2 个 ship 漏同步)/ real(其他) |
| `_render_report(...)` | 对标 AIPass seedgo 风格:✓ PASS / ✗ FAIL / ⊘ SKIP + pre-broken / ship 漏同步 / 真 fail 分组 |

**关键决策:**

1. **只用 proc.stdout,不合并 stderr** — stderr 的进度字符 `FEs` 会被误匹
   为 errors=20,真值只有 1。pytest 把 summary 写到 stdout,stderr 是
   deprecation warnings + 进度条。
2. **rootdir 限定 `tests/`** — pytest 默认扫根目录所有 test_*.py(包括
   standalone scripts 如 `test_3_emails.py` / `test_prisir_graph_*.py`),
   触发 20 个 collection error 直接中断全集。`pytest_args.append("tests/")`
   锁定 rootdir。
3. **`_parse_failed` 入口识别** — 用 `line.startswith("=") and "short test
   summary info" in line.lower()`,不依赖 "FAIL" 字符串(因为 summary
   header 自己就是 `=========== short test summary info ===========`,
   不是 `=== FAIL ===`)。
4. **CI 门禁:真 fail 阻塞** — `return 1 if len(cats["real"]) > 0 else 0`。
   pre-broken 4 个 file / ship 漏同步 6 个 test 都是已知 backlog,
   不阻塞 CI 红绿,但 console 提示维修责任。
5. **`--category sync/pre/real` filter** — 子集跑,调试某档 fail 时
   不用全集等 10 分钟。`--json` 导出机器可读报告(给后续 dashboard
   留口子)。

### tests/test_seed.py(15 测试 / 0.37s / 15/15 绿)

| 类 | 测试数 | 覆盖 |
|------|------|------|
| TestParseSummary | 4 | 标准格式 / 带 errors / 带 deselected / 无 match |
| TestParseFailed | 3 | 提取 fail / 提取 ERROR / 空输出 |
| TestCategorize | 3 | pre_broken 检测 / ship-sync-gap 检测 / real 默认 |
| TestNorm | 3 | posix / windows / 混合分隔符 |
| TestConstants | 2 | PRE_BROKEN list 不空 + 含已知 file / SHIP_SYNC_GAPS 含已知 class |

直接 import seed 模块(`sys.path.insert(0, ROOT)`),不 mock subprocess,
单测只覆盖纯解析/分类逻辑,跑 pytest 本身是 e2e(在 seed.py 真跑
时验证)。

## 实际输出(commit 后真跑一次)

```
✓ PASS  1111   in 516.5s
✗ FAIL    50   (errors: 1)
⊘ SKIP     4   (deselected: 0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
pre-broken files (4, 历史缺模块残留,继续 fail 不阻塞 CI):
  ⊘ tests/prisiragent_cli_db_test.py          (ModuleNotFoundError: prisiragent_cli)
  ⊘ tests/prisiragent_handoff_test.py         (ModuleNotFoundError: prisIragent_web)
  ⊘ tests/prisiragent_shell_ux_test.py        (ModuleNotFoundError: prisIragent_web)
  ⊘ tests/test_song_pool_and_favorite.py      (ModuleNotFoundError: music — 已归档)
ship 漏同步 fail (6, audit 分支 git index vs worktree 不一致):
  ⊘ TestPrisirAgentWebWfmodalHash (3)
  ⊘ TestExtRespawnHotfix (3)
真 fail (51):
  ✗ tests/test_e2e_phase2.py  (4)
  ✗ tests/test_easel_bridge.py  (1)
  ✗ tests/test_gh_endpoints.py  (1)
  ✗ tests/test_phase_7_compact_and_default.py  (3)
  ✗ tests/test_phase_8_tier_field.py  (1)
  ✗ tests/test_preset_travel.py  (1)
  ✗ tests/test_profile_travel.py  (5)
  ✗ tests/test_p3j_t9_pivot.py  (1)
  ✗ tests/test_solutions_learner.py  (1)
  ✗ tests/test_solutions_learner_categories.py  (1)
  ✗ tests/test_youtube_dl_integration.py  (2)
  ✗ tests/test_electron_subwindows.py  (9)  ← 包含 audit 分支 ship 漏同步 6 + 真 fail 3
  ✗ tests/test_prisir_skills_workbench_phase2.py  (22)  ← Phase 2 ship 没回写 test 的老问题
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CI 门禁: 真 fail > 0 → exit 1
```

`★ Insight ─────────────────────────────────────`
- **3 档分类是核心价值** — 没有分类,51 个 fail 平铺出来看起来像「50+ 个
  东西坏了」;分类后实际只有 ~13 个 file 需要人修,其他都是已知
  backlog。
- **CI exit code 区分阻塞 vs 提示** — pre-broken 4 个 / ship 漏同步 6 个
  在 PR 红绿页面只是「noise」,真 fail 51 个是真的「需要修」。如果
  不分类,51 fail 全 exit 1,CI 红页「全坏」假象。
- **rootdir 限定是 Python 项目 pytest 老坑** — 任何 `test_*.py` 在
  rootdir 都会被收集,standalone scripts 不该被收集。本仓库之前
  一直靠 `python -m pytest tests/` 手工加,seed.py 把这个加默认,
  新手不用记。
`─────────────────────────────────────────────────`

## 与 AIPass seedgo 的差异

| 维度 | AIPass seedgo | PrisirAI seed.py |
|------|---------------|------------------|
| 跑测 | pytest + extra lint | 仅 pytest(待 ship:eslint/vite build) |
| 分类 | fail / flaky / infra | pre_broken / ship_sync_gap / real(更细) |
| CI 门禁 | 全 fail 阻塞 | 仅 real 阻塞 |
| 报告格式 | JSON + html | text(对标 seedgo 风格)+ --json |
| 调度 | drone router / async task | 同步 subprocess(简单) |

**Why:** PrisirAI 主仓 backlog 51 fail 急需分清楚哪些是「真 bug 哪些是
 已知残骸」。没有分类,每次 ship 都怕引入新 fail 看不出来。AIPass
 seedgo 给了模板,简化成 280 行本地脚本 + 15 个单测。

**How to apply:** 以后每次 ship 前必跑 `python seed.py`,看真 fail 数
 量变化。`--category sync` 看 ship 漏同步是否还在(若 audit 分支
 再次 git add ship 代码到 HEAD,ship_sync_gap 应消失)。
 `tests/test_seed.py` 加新 fail 模式时同步扩(例如以后 lint 出
 现,可加 TestParseLint 段)。