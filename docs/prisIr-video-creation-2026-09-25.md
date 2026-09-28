# PrisirAI 视频创作模块 — 安装 / 使用 / 集成指南(P3j T11)

> 2026-09-25 ship。配合 `prisIragent-wechat-publisher` 后端,新增「🎬 视频」Tab + 6 creator。

## 1. 这是什么

PrisirAI 视频创作模块走 **Easel 子进程桥接** 路径,与 P3j T10 发布模块共后端共端口,
Web UI 上同一站打开。涉及 6 个 Easel 端 skill / 脚本:

| Creator | Easel 端脚本 | 作用 | 依赖 |
|---------|-------------|------|------|
| **tts**         | `skills/shared/scripts/tts.py`               | 文字 → 语音 | edge-tts(外网代理)/ 闭源 TTS(CosyVoice2) |
| **asr**         | `skills/shared/scripts/asr.py`               | 音频/视频 → 字幕 | faster-whisper(本地) |
| **assemble**    | `skills/openclaw/auto-short-video/scripts/assemble.py` | storyboard JSON → 终片 mp4 | ffmpeg |
| **image-gen**   | `skills/shared/scripts/ai_image.py`         | text → image | SILICONFLOW_API_KEY |
| **video-gen**   | `skills/shared/scripts/ai_video.py`         | text → video | SILICONFLOW_API_KEY(更贵) |
| **orchestrate** | 串联 tts+assemble 编排入口                   | 主题+script → final.mp4 | 需 tts+assemble 即可,无需 AI key |

## 2. 端到端流程(对齐 auto-short-video SKILL)

```
   ┌──────────────────────┐
   │ 主题(topic) + 文案   │ ← LLM 生成(由 agent/用户)
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ TTS 配音              │ ← tts creator(读 VOICE_PROVIDER/.env)
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ (可选)AI 配图         │ ← image-gen creator(SILICONFLOW_API_KEY)
   │ (可选)AI 视频片段     │ ← video-gen creator(更贵)
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ (可选)字幕烧录        │ ← tts --subtitle 自动出 SRT
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ assemble 合成         │ ← ffmpeg 拼图/片段 + 配音 + 字幕
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ final.mp4             │ → 一键投到 B站(走 P3j T10 publisher)
   └──────────────────────┘
```

## 3. 用户视角用法

### 3.1 一键出片(Web UI)

1. 打开 `http://127.0.0.1:<port>/`
2. 进「🎬 视频」Tab
3. 填主题 + 文案分镜(由 LLM 生成,贴进来)
4. 选画幅(9:16 竖 / 16:9 横 / 1:1 方)+ 时长 + 音色
5. 点「🎬 生成视频」 → 弹确认卡(画幅/扣费提示)→ 确认 → 跑编排
6. 出片后底部出现「📺 投到 B站(dry-run)」按钮 → 一键投稿

### 3.2 单零件调用(Web UI)

「🔧 零件调用」section:
- TTS — 输入文字 → 输出 mp3(可同时出 SRT)
- ASR — 输入音视频 → 输出 srt/ass/txt/json
- assemble — 输入 storyboard JSON → 输出 mp4
- image-gen / video-gen — 配图/片段(需 SILICONFLOW_API_KEY)

### 3.3 HTTP API

```
GET  /api/video/creators        # 列出 6 creator + ready
GET  /api/video/env             # 列出 Easel .env 里配的 key 名(不返 value)
POST /api/video/create          # 通用 creator 调用
                                # body: {creator: "tts", text:"...", output:"..."}
POST /api/video/orchestrate     # 编排入口
                                # body: {topic, script, aspect_ratio, duration,
                                #        voice, with_subtitle, with_images}
POST /api/video/publish         # 视频→B站投稿闭环
                                # body: {video_path, title, partition, desc, tag,
                                #        cover, exec_real}
```

完整 body 字段见 `companion/prisIragent-wechat-publisher.py:400+`。

### 3.4 Python 内嵌调用

```python
from prisir_work.video_creator import (
    TtsCreator, AsrCreator, AssembleCreator,
    ImageGenCreator, VideoGenCreator, Orchestrator,
    create, list_creators,
)

# 单零件
r = TtsCreator().create(text="你好世界", output="out.mp3",
                        voice="zh-CN-YunxiNeural")
print(r.ok, r.artifact, r.error)

# 一键编排
r = Orchestrator().create(
    topic="AI 改变办公的 3 个真相",
    script="第一句.../第二句.../第三句...",
    output_dir="D:/videos/ai-office",
    aspect_ratio="9:16", duration=60,
    voice="zh-CN-YunxiNeural",
    with_subtitle=True, with_images=False,
)
print(r.ok, r.artifact["video_path"])
```

## 4. API Key 配置

按 Easel SKILL.md 规范,**只读 `~/work/zju_easel/.env`**(不读 OS env / HKCU / PrisirAI 自己的 .env)。

需要:
- `SILICONFLOW_API_KEY=...` — 给 image-gen / video-gen
- `VOICE_PROVIDER=closed` — 强制走闭源 TTS(可选,默认 auto 优先闭源)
- `VOICE_API_KEY=...` — 闭源 TTS 用
- `https_proxy=http://...` — edge-tts / 闭源 TTS 走外网代理

不污染 OS env — 子进程跑 creator 时才注入。

## 5. 画幅/时长「确认硬门」

对齐 auto-short-video SKILL.md 的「制作/付费调用前必须追问」:
- **Web UI**:跑编排前弹确认卡(主题/文案长度/画幅/时长/字幕/配图 — 红字扣费提示)
- **HTTP API**:`aspect_ratio` 必须 `9:16/16:9/1:1`,`duration` 必须 int
- **Python API**:Orchestrator.create 用 keyword-only 必填

## 6. 一键安装

`install_wechat_publisher.ps1` 已包含 P3j T11 所需:
- ffmpeg(P3j T11 新增提示,如未装请手动装)
- Easel `tts.py / asr.py / ai_image.py / ai_video.py / assemble.py`(已自动探测)
- SILICONFLOW_API_KEY(需用户在 `~/work/zju_easel/.env` 自配)

## 7. 自检

```bash
python verify_wechat_publisher.py
# 16/16 passed(新增 5 项视频检查)
```

新增 5 项:
1. Easel 5 视频脚本到位
2. ffmpeg 安装
3. video_creator.import(6)— 6 creator 注册
4. video_creator.status(6)— ready 数
5. HTTP video 路由(creators/env/create validation/orchestrate 400/publish 400)

## 8. 边界 / 红线

1. **降级而非崩溃** — creator 未就绪 / 缺 key / 子进程失败 → 统一 `ok=False + reason`
2. **付费调用前必确认** — image-gen / video-gen 都有 modal 红字扣费提示
3. **exec_real 默认 False** — B站投稿默认 dry-run,与 P3j T10 一致
4. **不替你写文案** — Orchestrator 不接 LLM,只接受已写好的 script(那是 LLM / agent 的活)
5. **frame = 9:16 默认** — 默认竖屏适配抖音/小红书/视频号;B站/YouTube 用 16:9
6. **不污染 OS env** — `.env` 注入只在子进程跑 creator 时生效

## 9. 关联模块

| 模块 | 路径 | 作用 |
|------|------|------|
| 视频能力门面 | `prisir_work/video_creator.py` | 6 creator + 编排 |
| 后端主入口 | `companion/prisIragent-wechat-publisher.py` | 共端口;加 5 端点 |
| Web UI | `companion/.../static/index.html` | 「🎬 视频」Tab + 确认卡 |
| 自检 | `verify_wechat_publisher.py` | 16 项 E2E(11→16) |
| 测试 | `tests/test_video_creator.py` | 23 cases |
| 整合测试 | `tests/test_wechat_publisher_module.py` | 已含 video 路径 |

## 10. Roadmap

- [ ] LLM 写文案端点(`/api/video/script`)— 主题 → 分镜 JSON(对 LLM 直连)
- [ ] BGM 自动选(ai-music skill)
- [ ] 草稿定时发布(scheduler + 视频 cron)
- [ ] ai-video-gen 集成(aspect_ratio / 多镜头)
- [ ] short-drama 编排(角色/对白/多集)