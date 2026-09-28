# PrisirAI Agent 自然语言 → 视频能力 — P3j T14 文档

2026-09-26 ship。**Agent 代执行视频/YouTube 操作** — 用户说人话,不需要操作 Web UI 的 creator/op/endpoint 技术命名。

## 背景

用户在 P3j T11/T12/T13 后看到 Web UI 增了「🎬 视频」「🔧 视频处理」「📈 数据分析」「📺 YouTube」4 个 Tab,每个 Tab 下面还有 form 表单 + creator/op 字段。这对技术用户友好,但**非技术用户看不懂**:「video-ops 的 op 选 burn?」「video.cut 的 start/end 怎么填?」。

→ **抽象层 + 自然语言入口**:把 9 creator + 5 YouTube 操作收敛成 12 个「自然语言能力」,agent 解析用户意图,自动选能力 + 填 defaults + 调端点。

## 设计

```
用户说人话                 agent 解析              执行
─────────                ─────────              ────
"帮我做个 9:16 短视频"    parse_intent           execute("…", dry_run=True)
"主题是 X,文案是 Y"   →   {capability: video.create, args: {...}, missing: []}
                          fill_defaults          ↓
                          {aspect_ratio: 9:16,   execute(query, confirm_cb)
                           duration: 60, ...}
```

### 1. 能力抽象层(capability.py 扩展)

12 个能力注册表项,每个含 `title`(人话) + `keywords`(检索词) + `risk`(L0/L1/L2/L3) + `endpoint`(实际路径) + `confirm`(确认文案)。

| 能力 ID | 标题 | Risk | 端点 |
|---------|------|------|------|
| `video.list` | 查看 9 种视频能力 | L0 | `/video/list` |
| `video.create` | 一句话做视频(主题+文案→配音+字幕+终片) | L2 | `/video/orchestrate` |
| `video.tts` | 文字转语音 | L1 | `/video/tts` |
| `video.asr` | 自动字幕识别 | L1 | `/video/asr` |
| `video.cut` | 裁剪视频片段 | L1 | `/video/cut` |
| `video.bgm` | 加背景音乐 | L1 | `/video/bgm` |
| `video.burn` | 字幕烧录 | L1 | `/video/burn` |
| `video.info` | 查视频元数据 | L0 | `/video/info` |
| `video.analyze` | 发布数据分析 | L0 | `/video/analyze` |
| `youtube.upload` | 上传 YouTube | L3 | `/youtube/upload` |
| `youtube.list` | 列 YouTube 频道视频 | L0 | `/youtube/list` |
| `youtube.status` | YouTube 桥接状态 | L0 | `/youtube/status` |

### 2. 端口代理层(port_registry.py)

主服务(PrisirWork 18826)要代理到 companion wechat-publisher 子服务。端口共享机制:
- HKCU\Software\PrisirAI\<name> (Windows)
- `companion/_prisir_registry/<name>.json` (跨平台 fallback)

`proxy_post(name, path, body, timeout)` → 走 urllib.request,失败/未注册 → 返 `ok=False + reason`,绝不抛栈。

### 3. 端点代理层(endpoints.py)

12 个新端点,每个都是 `_REGISTRY[endpoint].handler` 透传到子服务:
```python
@register("/video/orchestrate", method="POST", risk="L2", auth=True)
def _video_orchestrate(body):
    body.setdefault("aspect_ratio", "9:16")
    body.setdefault("duration", 60)
    return _proxy("wechat_publisher_port", "POST",
                  "/api/video/orchestrate", body, timeout=900), 200
```

### 4. 自然语言解析(agent_natural_video.py)

`parse_intent(query)` 顺序匹配 12 个 regex 模式 → 命中第一个 + 抽取 args + 检查 missing。

**优先级排序**(关键):
1. YouTube 优先于通用(避免 "上传到 youtube" 命中 video 上传)
2. 视频创作优先于裁剪("帮我做个视频" 不要命中 cut)
3. 字幕烧录优先于 ASR("烧字幕" 不要命中 asr)
4. 裁剪有"裁/切/剪/截取/cut/trim" 动作 + 视频片段

`_extract_args()` 抽:
- 路径(`C:/xx.mp4` / `/Users/xx` / `~/xx`)— 按扩展名判 type
- 时间(`HH:MM:SS` / `MM:SS` / `30s` / `1分`)
- 画幅(`9:16` / `16:9` / `1:1`)
- topic / script(关键词:"主题是", "文案是")
- YouTube privacy(`public/公开` / `unlisted/不公开` / `private/私密`)
- analyze mode(`time/tags/types/growth`)
- 字幕烧录的路径顺序 — 自动按扩展名判 input vs sub

### 5. Defaults 补全

`_DEFAULTS[capability]` 提供 sensible defaults:
- `video.create` → 9:16, 60s, with_subtitle=True, with_images=False
- `video.tts` → zh-CN-YunxiNeural 音色
- `video.asr` → base 模型, srt 格式
- `youtube.upload` → private, category_id=22, exec_real=False

`fill_defaults(cap, args)` → 已有的不动,缺的补全。**不替用户拍必填**(topic/script/title 缺就保留 missing)。

### 6. 执行

`execute(query, *, dry_run=True, confirm_callback=None)`:
1. `parse_intent(query)` → IntentResult
2. `fill_defaults(intent.capability, intent.args)` → 完整 body
3. dry_run=True(默认)→ 返 preview,不真发
4. `confirm_callback` 存在 → 调一次,False 则 user_declined
5. 真发 → `endpoints._REGISTRY[cap_entry["endpoint"]].handler(body)` → 走 port_registry

### 7. CLI

`python -m prisir_work.cli video "帮我做个 9:16 短视频,主题 X,文案 Y"`:
- 默认 dry_run,列出命中能力 + 待补全参数 + 最终 body
- `--exec` → 真发
- `--no-confirm` → 跳过确认
- `--cap <id>` + `--body <json>` → 跳过 parse,直接走 capability
- `--json` → JSON 输出

`python -m prisir_work.cli capabilities`:
- 列出 video/youtube 类能力(人类可读 / --json)

## 关键文件

| 文件 | 作用 | 行数 |
|------|------|------|
| `prisir_work/agent_natural_video.py` | 自然语言 → capability 路由 + execute 闭环 | 437 |
| `prisir_work/port_registry.py` | 子服务端口共享读取 + proxy_get/post | 105 |
| `prisir_work/capability.py` | +11 capability 注册(T14 段) | +110 |
| `prisir_work/endpoints.py` | +12 endpoint 代理 | +200 |
| `prisir_work/cli.py` | 视频子命令 CLI | 170 |
| `tests/test_agent_video.py` | 单元测试 | 44 cases |
| `verify_wechat_publisher.py` | +1 check(P3j T14) | 22 checks |

## 测试

- `tests/test_agent_video.py`:**44/44 passed**
  - 22 个 parse_intent 用例(覆盖所有 12 个 capability + 边界)
  - 4 个 fill_defaults 用例
  - 4 个 execute 用例(dry_run / 真发 / 拒绝 / 无 confirm)
  - 11 个 CLI 用例(human / JSON / --cap / --exec / --no-confirm / 错误路径)
  - 3 个 dataclass.to_dict + intent_summary

- `verify_wechat_publisher.py`:**22/22 passed**(原 21 + T14 agent 检查)

## CLI 用例

```bash
# 1) 列出能力
$ python -m prisir_work.cli capabilities
  video.create           [L2] 一句话做视频:主题 + 文案 → ...
  youtube.upload         [L3] 把视频上传到 YouTube ...

# 2) 自然语言 dry_run
$ python -m prisir_work.cli video "帮我做个 9:16 短视频,主题 PrisirAI,文案 介绍 PrisirAI 的核心能力"
🔍 命中能力: video.create  (confidence=0.9)
📋 待补全参数: 无
📦 最终 body:
{
  "aspect_ratio": "9:16",
  "topic": "PrisirAI",
  "script": "介绍 PrisirAI 的核心能力",
  "duration": 60,
  "with_subtitle": true,
  "with_images": false
}

(加 --exec 真发;否则仅预览)

# 3) 真发
$ python -m prisir_work.cli video --exec "查 C:/v.mp4 多长多大"
✅ 能力: video.info
📦 body: {"path": "C:/v.mp4"}
📊 result: {...}

# 4) JSON 输出(对接 LLM)
$ python -m prisir_work.cli video --json "上传 C:/v.mp4 到 YouTube,标题 X,公开"
{
  "ok": true,
  "preview": true,
  "intent": {"capability": "youtube.upload", "args": {"video": "C:/v.mp4", "privacy": "public"}, "missing": ["title"], ...},
  "body": {"video": "C:/v.mp4", "privacy": "public", "category_id": "22", "exec_real": false},
  "missing": ["title"]
}
```

## 边界 / 易踩坑

1. **regex 顺序极重要** — YouTube > 视频创作 > 字幕烧录 > 裁剪/ASR > TTS。BGM/信息/分析最末
2. **路径顺序** — "把 srt 烧到 mp4" vs "把 mp4 烧 srt":按扩展名判 sub vs input
3. **capability 与 endpoint 是两层** — capability 是用户视角,endpoint 是 HTTP 实现。一个 capability 可绑多个 endpoint(目前没有)
5. **dry_run 默认开** — 真发必须显式 --exec 或 confirm_callback=True
4. **不替用户拍必填** — topic/script/title 缺就 missing,LLM 应追问用户,不要 LLM 瞎编
6. **port_registry 失败降级** — 子服务没起/超时 → 返 url_error,不抛栈
7. **CLI `--cap` 时 query 可省** — 直接传 capability id + body 即可

## 跟其他模块的边界

- [[prisir-video-creation]] — P3j T11 9 creator 共后端共端口
- [[prisir-video-extensions]] — P3j T12/T13 +3 creator + YouTube publisher
- [[prisir-publisher-module]] — P3j T10 多平台发布抽象
- [[prisir-easel-bridge]] — Easel 子进程桥接

## 下一步(可选)

- [ ] **Web UI 自然语言入口** — 在 4 Tab 之上加一个「💬 一句话」输入框,直接调 parse_intent
- [ ] **LLM 增强意图抽取** — 当前 regex 模式兜底,LLM 来了用它做二级召回 + 歧义消解
- [ ] **多轮对话补 missing** — 用户第一轮说 "做个视频",agent 看到 missing=[script],第二轮追问
- [ ] **capability.search LLM 友好化** — 把 keywords 暴露成自然语言 prompt 模板,让 LLM 直觉选
- [ ] **跨能力编排** — "做视频 + 上传 B站 + 上传 YouTube" 一次到位,workflow 串多 capability