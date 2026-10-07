---
name: lx-music-bridge-phase-a-shipped
description: Phase A 只读 LX Music Desktop 桥 — extension/ship 总结
metadata:
  type: project
---

# LX Music Desktop 桥 Phase A 只读扩展 ship (2026-10-07)

## 起因

P3.10b 拒绝音乐识别(Shazam 外传录音,触 0 上传红线)。但用户本地装 LX Music Desktop,通过 Open API(默认 127.0.0.1:23330)已经能匿名读 /status + /lyric。LX Music Desktop 已装且默认启 Open API,这是纯本地的元数据/歌词桥。

用户问:"之前通过落雪音乐开放 API 搭建 127.0.0.1:23330 的桥,同样可以加入到 PrisirAI 的扩展里吗?"

决策:**只读 Phase A** 先行,验证价值后再开 Phase B/C(播放控制 + AI 推荐)。

## 设计取舍

**严格只读**:仅注册 `lx.status` / `lx.lyric` / `lx.health` 三个命令。绝不动 /play /pause /next /prev /seek 等 mutating 接口。权限声明里只列 4 项 L0:`ai.invoke.command:lx.*` + `ui.inject.notification`,无任何 write/state-mutating 权限。

**100% 本地**:`lxBaseUrl()` 默认 `http://127.0.0.1:23330`,可通过 `PRISIR_LX_URL` 环境变量改端口。`http.get()` 调本地,无任何外网请求。沿用 P3.10b 0 上传红线。

**失败语义优先**:不抛异常,返 `{ ok: false, lx_alive: false, last_error: ... }`。LX 没装 / 没启 Open API / 端口改 / 防火墙拦截时,主对话 LLM 直接读 `last_error` 给用户解释,无需 traceback。

**不缓存**:每次 invoke 现取。LX Desktop 切歌 / 暂停 / 重启都立即生效,避免陈旧状态。

## 实测端点(2026-10-06)

- `/status` 200 OK:返 JSON `{status, name, singer, albumName, duration, progress, playbackRate, lyricLineText}`。**status 字段取值**:playing / paused / stopped / unknown
- `/lyric` 200 OK:返 LRC 文本(含 `[ar:][ti:][al:]` 元数据 + `[mm:ss.fff]` 时间戳)
- `/songList` `/playList` 403:LX Desktop 没启 Origin 校验或权限
- `/source` `/user` `/info` 等其他:401

实测 LX 数据:`name=万神纪 singer=三无Marblue、双笙(陈元汐)、易言、樊棋 status=paused` — 真实 LRC 解析成功,currentIndex 拿到正确行。

## 实现

**Node 端 index.js (~200 行)**:无 npm 依赖,纯 `http.get` + LRC 正则 + `vm` sandbox 友好导出。

`parseLrc(raw)` 正则 `/^\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]/`:
- 跳过 `[ar:][ti:][al:][by:][offset:]` 等元数据标签
- `ms` 字段支持 `.` 或 `:` 分隔,自动 padEnd 到 3 位毫秒
- 按 `t_ms` 升序排序返回 `[{t_ms, text}]`

`currentIndex(lines, progress_ms)`:线性扫描返回最后一个 `t_ms <= progress_ms` 的 index(progress=0 返 -1)。

**测试**:8 个 Node 单测 + 3 个 LX 真实 E2E:

```
✓ parseLrc 标准格式 + 元数据过滤
✓ parseLrc 空 / null / 无时间标签
✓ currentIndex 边界(0 / 在中间 / 末行)
✓ fetchStatus 不可达 → ok=false + lx_alive=false
✓ parseLrc 多时间戳同行取第一段
✓ fetchLyric 不可达 → ok=false
✓ probeHealth 不可达 → lx_alive=false + 延迟合理
✓ lxBaseUrl env 覆盖默认

[E2E] 真实 LX Desktop @ 127.0.0.1:23330
   name=万神纪 singer=三无Marblue、双笙(陈元汐)、易言、樊棋 status=paused
✓ E2E fetchStatus 真实拉取 → lx_alive=true
✓ E2E fetchLyric 真实解析
✓ E2E probeHealth 延迟 < 500ms

[结果] ✓ 11  ✗ 0
```

**Python wrapper test** (`tests/test_lx_music_bridge_status.py`):4 个 case,调 Node test runner + 验证 SDK 命令注册。默认 3 passed + 1 skipped(LX E2E)。设 `PRISIR_LX_E2E=1` 跑全 4 个,实测全过。

## 文件清单

- `extensions/lx-music-bridge-status/package.json` — manifest,4 个 L0 权限
- `extensions/lx-music-bridge-status/index.js` — 实现 + module.exports 测试钩子
- `extensions/lx-music-bridge-status/__tests__/run.js` — Node 单测 + E2E
- `tests/test_lx_music_bridge_status.py` — Python pytest wrapper

## 主仓回归

`python seed.py`:1156 PASS(+1,从 LX 测试加),4 ship漏同步(老的),1 真 fail = `test_wechat_publisher_module.py`(并行污染,单跑 16/16 全过 — 不是本扩展破的)。

## 后续 Phase B/C 边界

**Phase B**(待用户开绿灯):播放控制 + LRC 滚动条 + 收藏 tag — 写权限需要 `lx.play / lx.pause / lx.next / lx.prev / lx.seek` 命令 + `state.write.lx_favorite` 权限。Phase B 必须用户**逐条**勾,绝不能自动 ship。

**Phase C**(更远):基于已收藏 + 最近播放的本地启发式推荐 + 与 P2.5+8 calendar/todo/pomodoro 编排联动。沿用 N9 music AI 推荐架构(纯本地 + 0 上传)。

## Why

0 上传红线不能破 LX 桥也不能破。LX Desktop 本身已在本地跑(用户已确认),读它的 Open API 是和读本地 SQLite 一样安全。**关键差别**:绝不调它的 mutating endpoint,绝不缓存用户行为数据上传任何外部服务。

## How to apply

下次遇到「本地已装某软件,能否做 X 桥」类问题:
1. 先确认软件是否已装 + 端口可读(curl /status 看响应)
2. 确认只读 endpoint 都列出来(实测哪些 200 / 哪些 401-403)
3. Phase A 只读先行,失败语义优先,环境变量覆盖端口
4. 测试要 Node 真实端到端 + Python wrapper 默认 skip,env 标志放行
5. Phase B/C 严格 gated 在 Phase A 验证后

相关:[[p3-10-bubble-cancelled-privacy]], [[N9 music AI 歌单推荐]], [[ext-inventory-injected]]