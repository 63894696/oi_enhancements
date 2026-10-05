---
name: p3-0-music-module-archive
description: P3.0 music 模块完全脱离 PrisirAI 主仓 ship(2026-10-05)。本地代码存 GitHub PrisirAImusicarchive 独立仓,主仓 -25414 行 music 代码。P3.10b 0 上传红线保留。
metadata:
  type: project
---

# P3.0 music 模块完全脱离 PrisirAI 主仓(2026-10-05 ship)

## 背景

P2.5+22 ~ P3.8 全周期 ship 链(60+ commits)拼凑出一个 music 模
块:在线搜歌 modal + 多源 fallback(LX huibq/gdstudio/oiapi × wy/tx/
kw/kg/mg)+ 桌面歌词子窗 + EQ 均衡器 + AI 推荐歌单 + Toast + 收藏
toggle。技术栈纯本地、无第三方云、上传 0。

P2.5+29 收尾把「点搜索按回车无反应」(async loop bug)修齐,但用户
实测发现整条播放链路永远走不通:5 源 LX 在本地实际能下到曲很少,
即便下到 .mp3 也常常 codec 不兼容或者 URL 在数小时后就 410。

用户原话:「完全没有可用资源就放弃音乐模块,把这个模块从
PrisirAI 脱离出来。建个非公开 github 仓保存代码,等合适的时
候重启模块。」

## 全流程 commits(主仓 audit-2026-09 分支)

1. **d356a20** chore(archive): git rm music 模块(2026-10-05)
   -105 文件 / -25414 行
   包含 companion/music/* (lx_runtime / static/music / port_config.py
   / song_pool.py 等)、tests/test_music* (15 个)、tests/test_lx_*、
   tests/test_song_pool_*、memory/music 配套、P2.5+28 之前的 music
   memos、companion/lx_runtime/、companion/_tmp_lx/。

2. **0d814ac** refactor(shell): archive music/lyric/eq 模块入口 (P3.0)
   Electron 主壳 4 文件精简:
   - main.js:19 个 music/lyric/eq 函数/IPC/tray 块删,startMusic() 保留
     为 no-op 桩函数,killBackend 正则收紧到 prisiragent_web 单点。
   - preload.js:prisIragent IPC 命名空间整段 stub,渲染层误访问拿
     undefined(无 throw,符合 sandbox 预期)。
   - port_config.js / config_loader.js:music 端口配置整段删。
   - node -c 语法全过;残留 music 字样均为归档注释。

3. **2910718** fix(web): port_config 从 companion/music/ 救回主仓根
   + 测试清理 (P3.0)
   companion/music/ 删后,prisIragent_web.py 三处
   'from companion.music.port_config import' 全部 ImportError → 走
   fallback warning log,端口配置静默退化。修法:
   - port_config.py 从 archive 仓复制回主仓根,剥离 music 字段
     (DEFAULT_MUSIC_PORT / _migrate_legacy_music_port /
     LEGACY_MUSIC_REG_VAL / 'name == music' 分支)
   - prisIragent_web.py:4 处 import 改 from port_config import
   - tests/test_electron_subwindows.py:
     * TestMusicToast 类整段删(companion/static/music/app.js 已 git rm)
     * test_main_js_has_required_functions 移除 musicProc.kill() 断言

## Archive 仓

路径:D:\PrisirAImusicarchive(本地非公开)
Commit:52304f3 archive(music): 归档 PrisirAI music 模块(2026-10-05)
125 文件 / 34 memory / 15 test / README.md + docs/{architecture,
restart-guide}.md
.gitignore 排除 node_modules / dist / _tmp_lx

**推送**:在 GitHub 创建非公开仓名 PrisirAImusicarchive(等用户拍板),
然后 `git remote add origin <url> && git push -u origin master`。
本次 ship 不主动推,等用户拍板。

## 测试回归结果

`python -m pytest tests/ --ignore=tests/prisiragent_cli_db_test.py
 --ignore=tests/prisiragent_handoff_test.py
 --ignore=tests/prisiragent_shell_ux_test.py
 --ignore=tests/test_song_pool_and_favorite.py -q`
 → 65 failed, 1097 passed, 5 skipped(9m29s)

### Music 归档造成的 fail(本 commit 修完)
- test_electron_subwindows.py::TestMusicToast::test_music_app_js_play_btn_handles_empty_queue
  → 整段 TestMusicToast 类删(2910718)
- test_electron_subwindows.py::TestMainJsSyntax::test_main_js_has_required_functions
  → musicProc.kill() 断言删(2910718)

### 已知遗留 fail(非 music 归档,本 scope 外)
- test_electron_subwindows.py::TestPrisirAgentWebWfmodalHash (3 个)
- test_electron_subwindows.py::TestExtRespawnHotfix (3 个)
- test_e2e_phase2.py::TestPrisirAgentWebWfmodalHash + TestTauriMenuAnchors + TestTask26MenuAudit
- test_e2e_phase2.py::test_scenario_4_full_stack_journey
- 大量 test_preset_travel / test_profile_travel / test_solutions_learner / test_phase_7 / test_phase_8(独立功能模块历史 fail)

来源:eb01210 (2026-10-03 fix(shell+web): 4 子窗 ship 后 bug 修齐)
+ b14985a (2026-10-03 fix(ext-bridge): task-runner 一次启) + 5496d97
等 commit 在 audit 分支 ancestry 里没 ship 过 master —— disk 工
作树包含这些 ship 代码(通过 git diff prisIragent_web.py 可看到
+35 行 P2.5+21 hotfix + wfmodal hash 代码),但 git HEAD/index 是
775c1ee carryforward 镜像的旧版(无 _ext_respawn_total /
wfModalOpen / history.replaceState)。这是 commit history 错位,
test_electron_subwindows.py 的期望引用了 ship 代码 → 静态扫 fail。

修法不在本 scope(超出 music 归档)。

## 安全/隐私红线(必须保留)

P3.10b(2026-10-04)用户原话保留:
- 「干脆这个识别音乐功能不做了」(SongRec/getUserMedia/Shazam reject)
- 「不做音乐识别功能,不调用麦克风」
- **用户隐私阈值:0 上传/外传,不只是无 Key**(红线)

archive 仓代码已包含 P3.10b 拒绝决定。如果未来重启音乐模块,
新功能默认 0 上传(纯本地匹配/纯本地存储/纯本地处理),除非用
户明确允许。

## 决策参考

- 用户明确选「完整:推独立 remote + 主仓彻底删除」(不是 local-only)
- 用户明确选「本地不存,只存放在 github,创建仓名
  PrisirAImusicarchive 存放」 — 本地 D:/PrisirAImusicarchive 是临时
  staging,推 GitHub 后可删除(用户未明示,留待拍板)
- 重启音乐模块的合适时机(用户原话):「等合适的时候重启模块」
  — 推测条件:有付费 API 接入 / 本地曲库达到 ~50GB / 第三方
  LX 源恢复稳定 / 或用户重新主动要求

**Why:** music 模块已 ship 3 个月,播放链始终走不通;0 上传红线 + 5
源 LX 持续 410 + 用户主动放弃 = 归档比再 ship 更划算。

**How to apply:** 下次有用户主动说「重启 music」时,从
D:/PrisirAImusicarchive clone → 在新分支 develop,先验证
P3.10b 0 上传红线依旧成立(无 Key 0 上传,有 Key 也 0 上传)。
test_electron_subwindows.py 已知 fail 在新分支要分批修
(优先级 wfmodal hash > ext_respawn > menu audit)。
