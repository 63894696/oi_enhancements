# PrisirAI 视频能力扩展 + YouTube 海外接入(P3j T12 + T13)

> 2026-09-25 ship。配合 P3j T11 视频创作模块,在同一后端同一端口下扩展「视频处理」
> +「字幕处理」+「发布数据分析」3 个 creator,并接海外 YouTube。

## 1. 这是什么

在 P3j T11 视频创作(6 creator: tts / asr / assemble / image-gen / video-gen /
orchestrate)基础上,新增 3 个 creator + 1 个海外 platform publisher:

| 模块 | 类型 | 端依赖 | 关键能力 |
|------|------|--------|----------|
| **video-ops** | creator | `skills/shared/scripts/video_ops.py` | ffmpeg/ffprobe 封装: cut / concat / speed / aspect / compress / bgm / watermark / info / frame / gif / text / silence-cut / mute-cut |
| **subtitle-ops** | creator | `skills/shared/scripts/subtitle_ops.py` | 字幕: parse / extract / merge / build / convert / **burn** 烧录 |
| **publish-analytics** | creator | `openclaw/skill-publish-analytics/scripts/analyze.py` | 确定性计算: time / tags / types / growth / all / selftest |
| **youtube** | publisher | `google-api-python-client` + yt-dlp | Data API v3: OAuth + upload / list / channel_stats |

## 2. 跟 P3j T11 / T10 的边界

- **共后端共端口** — 所有新端点加到 `prisIragent-wechat-publisher.py`,无新进程。
- **共注册表范式** — 3 creator 走 `register_creator()` / 1 publisher 走
  `register_publisher()`,对齐 web_search/publisher/video_creator 三个 register_*。
- **共降级范式** — creator 未就绪 / 缺 token / 子进程失败 → `ok=False + reason`,
  永不抛栈。
- **共前端 Tab** — 「🔧 视频处理」「📈 数据分析」「📺 YouTube」3 个 Tab,
  沿用 `data-tab=` 切 Tab + `api()` fetch + `showModal()` 确认卡。

## 3. 3 新 Creator 详解(P3j T12)

### 3.1 video-ops(13 子命令)

最常用 6 子命令(其他 7 由 Web UI select 透传):

| 子命令 | 用途 | 关键参数 |
|--------|------|----------|
| `cut` | 裁剪 | `--start 00:00:05 --end 00:00:15` 或 `--duration` |
| `concat` | 拼接 | `--concat_args "C:/v1.mp4|C:/v2.mp4"` |
| `aspect` | 横竖比 | `--aspect 9:16` |
| `compress` | 压缩 | `--crf 28` (越小越清晰) |
| `bgm` | 加背景音乐 | `--music C:/bgm.mp3 --volume 0.3` |
| `watermark` | 加水印 | `--image C:/logo.png --position bottomright` |
| `info` | ffprobe 元数据 | (只 -i) |
| `frame` | 抽帧 | `--time 00:00:10` |
| `gif` | 转 GIF | `--fps 15 --width 480` |
| `text` | 文字覆盖 | `--text "PrisirAI"` |
| `silence-cut` / `mute-cut` | 静音检测切 | `--threshold -30dB` |
| `speed` | 变速 | `--factor 1.5` |

### 3.2 subtitle-ops(6 子命令)

| 子命令 | 用途 | 关键参数 |
|--------|------|----------|
| `parse` | srt/vtt/ass → JSON | `-i / -o` |
| `extract` | 提取待译文本 | (供 LLM) |
| `merge` | 原文字幕 + 译文 → 双语 | `--src` `--trans` |
| `build` | JSON → 字幕文件 | `-i .json / -o .srt` |
| `convert` | 格式互转 srt/vtt/ass | `--target srt` |
| `burn` | **烧录进视频(硬)或挂载(软)** | `-i video --sub srt -o out --soft?` |

`burn` 是最有用的:一站式给视频加字幕,不调 LLM 也行(有现成字幕)。

### 3.3 publish-analytics(6 模式)

| mode | 用途 | 数据依赖 |
|------|------|----------|
| `selftest` | 自检(永远可跑) | ❌ 无 |
| `time` | 最佳发布时段 | 需要 data |
| `tags` | 标签效果 | 需要 data |
| `types` | 内容类型对比 | 需要 data |
| `growth` | 增长归因 | 需要 data + follower_log |
| `all` | time+tags+types 默认组合 | 需要 data |

`data` 是 Easel `weixin_mp_stats.py` 拉的本地 JSONL 缓存路径(用户在 Easel
项目根跑一次 stats 命令即生成)。

## 4. YouTube Publisher(P3j T13)

### 4.1 独立模块

`prisir_work/youtube_bridge.py` — 不走 Easel 子进程,因为:
- YouTube Data API v3 要 google-auth OAuth(浏览器跳转 + token 持久化)
- yt-dlp 走 youtube-dl 协议,与 Easel 依赖解耦更稳

### 4.2 OAuth 流程

```
1) 用户在 GCP Console 申请 OAuth client(类型: Desktop app)
2) 下载 client_secrets.json,放到 ~/.prisIrai/youtube_client_secrets.json
3) Web UI「🔑 授权」按钮 → 浏览器跳转 Google 登录
4) 回调到 http://127.0.0.1:8765 → token 落 ~/.prisIrai/youtube_token.json
5) 之后所有 upload / list / stats 都走 refresh_token 静默刷新
```

### 4.3 4 方法

| 方法 | 用途 | 关键参数 |
|------|------|----------|
| `auth(port=8765)` | OAuth 一次性授权 | port=回调端口 |
| `upload(video, title, description, tags, category_id, privacy, exec_real)` | 上传视频 | exec_real=False=dry-run 校验 |
| `list_videos(max_results, exec_real)` | 列自己频道 | exec_real=False=dry-run |
| `channel_stats(exec_real)` | 频道统计(订阅/观看/视频数) | dry-run 默认 |

### 4.4 Privacy

- `private` — 仅自己(默认)
- `unlisted` — 有链接可看,不公开
- `public` — 公开(任何人能看,搜得到)

前端弹确认卡(对齐权限闸 v1)强调「public 后任何人能看」。

## 5. 用户视角用法

### 5.1 Web UI(后端启动后)

打开 `http://127.0.0.1:<port>/`

**「🔧 视频处理」Tab**:
- 选 op → 填 input/output/额外参数 → 执行
- 「🔍 只查元数据」走 `/api/video/info`(ffprobe)
- 「🔥 烧录」走 `/api/video/subtitle/burn`

**「📈 数据分析」Tab**:
- 选 mode(默认 selftest) → 可选 data → 分析

**「📺 YouTube」Tab**:
- 探测 → 看 ready 状态
- OAuth 授权 → 浏览器跳一次
- 上传(默认 dry-run)→ 勾选 exec_real 真传
- 列我视频 → 列自己的

### 5.2 HTTP API

```
# video_ops 透传
POST /api/video/create {"creator":"video-ops", op:"cut", input, output, start, end}

# subtitle_ops 透传
POST /api/video/create {"creator":"subtitle-ops", op:"parse", input, output}

# publish-analytics 透传
POST /api/video/create {"creator":"publish-analytics", mode:"time", data}

# 视频元数据
GET /api/video/info?path=C:/v.mp4

# 字幕烧录
POST /api/video/subtitle/burn {"input", "sub", "output", "soft"}

# 数据分析
GET /api/analytics?mode=time|tags|types|growth|all|selftest[&data=...&follower_log=...&profile=...]

# 数据分析(老路径桥接)
GET /api/stats?mode=selftest   # 同上

# YouTube
GET  /api/youtube/status
GET  /api/youtube/auth?port=8765
POST /api/youtube/upload {"video", "title", "description", "tags", "category_id", "privacy", "exec_real"}
GET  /api/youtube/list?max_results=10&exec_real=false
GET  /api/youtube/stats
```

### 5.3 Python 内嵌

```python
from prisir_work.video_creator import (
    VideoOpsCreator, SubtitleOpsCreator, PublishAnalyticsCreator,
)

# 裁剪
r = VideoOpsCreator().create(op="cut", input="in.mp4", output="out.mp4",
                              start="00:00:05", end="00:00:15")

# 字幕烧录
r = SubtitleOpsCreator().create(op="burn", input="v.mp4",
                                 sub="a.srt", output="v_burned.mp4")

# 数据分析
r = PublishAnalyticsCreator().create(mode="selftest")  # 永远 OK

# YouTube
from prisir_work.publisher import YoutubePublisher
yp = YoutubePublisher()
r = yp.publish_video(video="v.mp4", title="t", privacy="private")  # 默认 dry-run
r = yp.list_videos(max_results=10)
r = yp.channel_stats()
```

## 6. 安装 / 依赖

### 6.1 Easel 端(已自动)

`install_wechat_publisher.ps1` 已自动:
- 探测 `video_ops.py` / `subtitle_ops.py` / `analyze.py`
- 提示 ffmpeg(已装)

### 6.2 YouTube 端(用户自装)

```bash
pip install google-api-python-client google-auth-oauthlib yt-dlp
```

把 GCP 申请的 `client_secrets.json` 放到 `~/.prisIrai/youtube_client_secrets.json`,
首次在 Web UI 走「🔑 授权」即可。

### 6.3 检查脚本

```bash
python verify_wechat_publisher.py
# 21 passed(原 16 + T11 3 项 + T12 3 项 + T13 2 项 — 但部分重叠,实际 T12+T13 新增 5 项)
```

新增 5 项(verify 第 17-21 项):
1. **Easel video_ops + subtitle + analytics** — 3 端脚本到位
2. **video_ext creator import** — 3 新 creator 注册
3. **youtube_bridge import** — bridge + publisher 注册
4. **HTTP video ext routes (5)** — info/burn/analytics selftest/stats?mode 桥
5. **HTTP youtube routes (5)** — status/auth/upload 400/list/stats

## 7. 测试统计

```
tests/test_video_creator.py:  42 passed + 2 skipped   (P3j T11:23 + T12:19)
tests/test_youtube_bridge.py: 24 passed                 (P3j T13:24)
verify_wechat_publisher.py:   21/21 passed             (P3j T10:11 + T11:5 + T12:3 + T13:2)
```

## 8. 边界 / 红线

1. **降级而非崩溃** — 任何 creator/publisher 失败 → `ok=False + reason`
2. **视频处理走 ffmpeg** — 必须装 ffmpeg;`video_ops.py info` 不依赖 ffmpeg 之外的工具
3. **字幕烧录 CJK 字体** — srt 硬烧录需系统装中文字体;`--font-dir` 可指定目录
4. **publish-analytics 不联网** — 数据来自本地缓存;`time/tags/types/growth` 不调外网
5. **YouTube OAuth 一次性** — 浏览器跳转是阻塞调用;`run_local_server` 默认 8765 端口
6. **exec_real 默认 False** — 所有付费/外发操作默认 dry-run(对齐 P3j T10)
7. **public 后不可撤回** — Web UI 弹确认卡红字提示
8. **YouTube quota** — Data API v3 每日 10,000 unit;upload 算 1600 unit,慎用真传

## 9. 关联模块

| 模块 | 路径 | 作用 |
|------|------|------|
| 视频创作门面 | `prisir_work/video_creator.py` | 9 creator + 编排 |
| YouTube 桥接 | `prisir_work/youtube_bridge.py` | OAuth + upload/list/stats |
| 发布抽象 | `prisir_work/publisher.py` | 7 publisher(含 youtube) |
| 后端主入口 | `companion/prisIragent-wechat-publisher.py` | 共端口;28+ 端点 |
| Web UI | `companion/.../static/index.html` | 8 Tab(发布/视频/Recall/数据/平台/视频处理/数据分析/YouTube) |
| 自检 | `verify_wechat_publisher.py` | 21 项 E2E |
| 测试 | `tests/test_video_creator.py` + `tests/test_youtube_bridge.py` | 42 + 24 = 66 cases |

## 10. Roadmap

- [ ] `clipify` skill 接入 — 长视频 → 多段短视频
- [ ] `video-chapters` — ASR + LLM 章节时间戳
- [ ] `hook-generator` — 视频开头 hook 自动生成
- [ ] `comment-insights` — 评论分析(YouTube/B站/抖音)
- [ ] `seo-quality` — 发布前 SEO 评分
- [ ] YouTube Shorts 自动适配(竖屏 9:16 → Shorts)
- [ ] YouTube OAuth 自动续期(refresh_token 流程监控)
- [ ] TikTok / Instagram / X 接入(同样模式)