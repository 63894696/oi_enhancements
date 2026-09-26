# PrisirAI 多媒体创作 Key 配置 + 视频 Tab 横幅 — P3j T17 + T17-H + T18 全 ship

2026-09-26 ship。**T17-A/B/C/D/E + T17-H + T18-A/B/C/D 全 ship** — 多媒体模型 key 单独分模块页填(仿 LLM/ASR 范式),视频 Tab 顶部横幅持续显示依赖状态,主对话 EXEC 失败时给中文 hint + 「点此去配置」链接,且每个 provider 卡片可点 🔬 真探活验证 key 有效性。

## 问题

T16 接入主对话 EXEC 后,用户在主对话说「帮我做个 PrisirAI 视频」→ LLM 输出 `[[EXEC: video.create ...]]` → 真发时底层 SILICONFLOW_API_KEY 缺失,**fail-soft 返 `ok=False` + 一句英文 reason**。用户在主对话看到的是干瘪的红 ❌,不知道是占位图模式、edge-tts 外网不通、还是 Whisper 没下载 — **配置项散落在各 form 旁,缺乏统一入口**。

**用户原话**:「真正生成开发时需要调用专业模型,必须先填入此类模型的 key,虽然完全用开源免费工具生成也可以,但画质效果难以保证,这些有必要写在特定功能页面提醒用户」

## 设计

### 1. 后端:`companion/media_keys.py`

**Provider 注册表**(对齐 LLM/ASR 范式):
| id | title | env_var | 用途 | 免费替代 |
|----|-------|---------|------|----------|
| `siliconflow` | SiliconFlow(AI 配图/视频) | `SILICONFLOW_API_KEY` | image-gen / video-gen | 占位图/视频 |
| `dashscope` | 通义千问 CosyVoice TTS | `DASHSCOPE_API_KEY` | 高级 TTS(可选) | edge-tts |
| `openai` | OpenAI(高级 TTS / 备选配图) | `OPENAI_API_KEY` | 高级 TTS 备选(可选) | edge-tts |
| `whisper` | 本地 Whisper 模型大小 | (无 key) | faster-whisper 本地 ASR | — |

**持久化位置**:`%PRISIR_DATA_DIR%/media_keys.json`(默认 `~/.prisirai/`)

**核心 API**:
- `load_media_keys(data_dir)` / `save_media_keys(...)` / `public_media_keys(...)` — 仿 `companion_asr_providers` 范式
- `apply_post(data_dir, posted)` — POST 时过滤 `***` / 空 / `…` 不覆盖原值
- `resolve_media_key(name, env_var)` — **优先级: Easel .env > media_keys.json > process env**
- `media_status(data_dir)` — 汇总 7 类依赖(粗粒度 3 类:已配 / 免费替代 / 没装)

### 2. HTTP 端点(在 wechat-publisher 里,跟视频 Tab 同源)

| 端点 | 方法 | 作用 |
|------|------|------|
| `/api/media/keys` | GET | 4 provider + mask 后值 + `_present` 标志 |
| `/api/media/keys` | POST | 保存(`***` 不覆盖) |
| `/api/media/status` | GET | 7 类依赖 ready/mode/hint |

### 3. 视频 Tab 顶部 deps-banner(3 档视觉)

```css
.deps-banner.deps-loading   灰: 加载中
.deps-banner.deps-all-ok    绿: ✅ 全部就绪 · SF=OK · TTS=edge · ASR=base
.deps-banner.deps-partial   黄: ⚠ 配图/视频走占位图 · 缺: SILICONFLOW_API_KEY · ffmpeg
.deps-banner.deps-missing   红: ❌ Easel 项目根目录未找到
```

进入 Tab 自动 fetch `/api/media/status`,点击展开 🎨 配置卡片。

### 4. 「🎨 多媒体创作 Key 配置」卡片

4 provider 子卡片,每个含:
- `title` + 状态灯(`✅ 已配` / `⚠ 未配`,由 `_present` 标志判定)
- `api_key` 输入框(mask 后显示) / `base_url`(仅 openai) / `model` 下拉(仅 whisper)
- 「💾 保存」按钮 → POST `/api/media/keys` → toast 反馈

### 5. 主对话 EXEC 失败 hint

`agent_main_chat_hook.translate_exec_error(error)` 把英文 reason 翻成人话 + 给配置链接:

```python
_ERROR_ZH = [
    ("Easel 未装 或 SILICONFLOW_API_KEY 未配置",
     "AI 配图/视频依赖未就绪", "/media-keys"),
    ("url_error", "视频后端未启动", "/media-keys"),
    ("[WinError 10061]", "视频后端连接失败", "/media-keys"),
    ("ffmpeg", "ffmpeg 未安装", "/media-keys"),
    ("missing_required", "必填参数缺失", ""),
    ("endpoint_not_found", "endpoint 未注册", ""),
    ("capability_not_found", "能力 ID 未注册", ""),
    ("api_key", "API key 未配置或无效", "/media-keys"),
    ("Whisper", "Whisper 模型未就绪", "/media-keys"),
]
```

**最长前缀匹配** — 避免短串误吞。`build_exec_result` 失败时自动附 `zh/hint/link` 字段,前端 `renderCapExecResult` 检测后渲染「💡 点此去配置」链接(占位未实接,留给 T17-H/T18 续接)。

## 关键文件

| 文件 | 行数 | 作用 |
|------|------|------|
| [companion/media_keys.py](../companion/media_keys.py) | 280 | 持久化 + provider 注册表 + fallback 链路 |
| [companion/prisIragent-wechat-publisher.py](../companion/prisIragent-wechat-publisher.py) | +50 | 3 个端点 + DATA_DIR |
| [companion/prisIragent-wechat-publisher/static/index.html](../companion/prisIragent-wechat-publisher/static/index.html) | +200 | deps-banner DOM + CSS + JS + 4 provider 卡片 |
| [prisir_work/agent_main_chat_hook.py](../prisir_work/agent_main_chat_hook.py) | +70 | translate_exec_error + exec_result 附 hint/link |
| [tests/test_media_keys.py](../tests/test_media_keys.py) | 13 cases | load/save/mask/resolve/translate 全覆盖 |
| [verify_wechat_publisher.py](../verify_wechat_publisher.py) | +3 check | module import + HTTP 端点 + UI DOM |

## 测试 & verify

- **tests/test_media_keys.py**:**13/13 passed**
- **tests/test_media_keys_probe.py**(T18-D):**7/7 passed**
- **tests/test_render_cap_exec_zh.py**(T17-H):**5/5 passed**
- **tests/test_agent_video.py**(T16):**111/111 passed**(无回归)
- **verify_wechat_publisher.py**:**32/32 passed**(含 HTTP)
  - `media_keys module import (P3j T17-A)`:模块 OK;PROVIDERS 4 个
  - `HTTP media routes (3 ep, P3j T17-B)`:keys GET n=4; keys POST 400? + 200; status n=7
  - `media UI DOM (P3j T17-D/E)`:UI DOM 元素 + CSS + JS 10/10 ✓
  - `probe_provider (P3j T18-A)`:返 shape={ok,status,latency_ms,hint,mode}
  - `HTTP /api/media/test (P3j T18-B)`:400? + whisper + siliconflow 3 路都返 200
  - `zh/link + autoFocusFromUrl (P3j T17-H)`:app.js zh/link + CSS + autoFocusFromUrl 3/3 ✓

## T17-H 主对话前端接 hint/link

`agent_main_chat_hook.build_exec_result` 失败时已附 `zh/hint/link` 字段(T17-C),T17-H 把这些字段接到主对话前端:

**[companion/static/app.js](companion/static/app.js) `renderCapExecResult` 增量**:
```js
// P3j T17-H: 中文翻译 + hint/link 跳转
if (!ok && m.zh) {
    const zhDiv = document.createElement("div");
    zhDiv.className = "cap-exec-zh";
    zhDiv.textContent = "💡 " + m.zh;
    div.appendChild(zhDiv);
}
if (!ok && m.link) {
    const publisherBase = "http://localhost:18899";
    const focusId = String(m.link).replace(/^\//, "");
    const a = document.createElement("a");
    a.className = "cap-exec-link";
    a.textContent = "💡 " + (m.hint || "点此去配置") + " →";
    a.href = publisherBase + "/?focus=" + encodeURIComponent(focusId);
    a.target = "_blank";
    div.appendChild(a);
}
```

**配套 CSS**([companion/static/guohua-theme.css](companion/static/guohua-theme.css)):
```css
.cap-exec-zh { color: #5a5a5a; margin-top: 4px; font-size: 12px; }
```

**配套 wechat-publisher URL focus**([companion/prisIragent-wechat-publisher/static/index.html](companion/prisIragent-wechat-publisher/static/index.html)):
```js
// P3j T17-H: ?focus=media-keys → 自动展开配置卡 + scrollIntoView
(function autoFocusFromUrl(){
    const params = new URLSearchParams(location.search);
    const focus = params.get('focus');
    if (!focus) return;
    const map = { 'media-keys': 'media-keys-card' };
    const elId = map[focus];
    if (!elId) return;
    setTimeout(() => {
        document.querySelectorAll('.tab-btn').forEach(b => {
            if (b.dataset && b.dataset.tab === 'video') b.click();
        });
        setTimeout(() => {
            const el = document.getElementById(elId);
            if (el) {
                el.style.display = 'block';
                el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }, 200);
    }, 300);
})();
```

**完整数据流**:
```
主对话 EXEC 失败
  ↓ ws event: capability_exec_result {ok:false, zh:"...", hint:"💡...", link:"/media-keys"}
  ↓ app.js renderCapExecResult
  ↓ 渲染中文 + "💡 点此去配置 →" 链接到 localhost:18899/?focus=media-keys

用户点链接 → 新窗口打开 wechat-publisher
  ↓ autoFocusFromUrl() 读 ?focus=media-keys
  ↓ 自动切到 🎬 视频 Tab + 展开 #media-keys-card + scrollIntoView
```

## T18 provider 真探活

**后端 helper** [companion/media_keys.py](companion/media_keys.py):
```python
def probe_provider(name: str, api_key: str = "",
                   base_url: str = "",
                   timeout: float = 8.0) -> dict:
    """真探活 provider,返 {ok, status, latency_ms, hint, mode}。

    - siliconflow / openai: GET {base_url}/models Bearer sk-...(不扣费)
    - dashscope:    GET compatible-mode/v1/models Bearer sk-...
    - whisper:      faster-whisper 导入 + 模型 cache 目录检查
    """
```

**HTTP 端点** `/api/media/test`(POST):
```python
async def _h_media_test(req):
    body = await req.json() if req.body_exists else {}
    provider = (body.get("provider") or "").strip()
    api_key = body.get("api_key") or resolve_media_key(provider, ...)
    base_url = body.get("base_url", "")
    timeout = float(body.get("timeout") or 8.0)
    result = await loop.run_in_executor(
        None, lambda: probe_provider(provider, api_key, base_url, timeout))
    return web.json_response({"ok": True, **result})
```

**前端 🔬 测试按钮**(per provider 卡片):
```js
async function testMediaProvider(pid) {
    const statusEl = document.getElementById('mp-status-' + pid);
    if (statusEl) statusEl.textContent = '⏳ 测试中...';
    // 收集该 provider 的 field value + POST /api/media/test
    const r = await api('/api/media/test', {method: 'POST', body: ...});
    if (r.ok) {
        const detail = `✅ ${r.latency_ms}ms · status=${r.status}`;
        toast(detail, 'ok');
    } else {
        const detail = `❌ ${r.hint}`;
        toast(detail, 'err');
    }
}
```

**探活规则**(不扣费):
| provider | URL | 返 ok | 返 ok=False |
|----------|-----|-------|-------------|
| siliconflow | `GET {base_url}/models` | 200 | 401/403=key 失效;timeout=网络问题;5xx=平台故障 |
| openai | `GET {base_url}/models` | 200 | 同上 |
| dashscope | `GET compatible-mode/v1/models` | 200 | 同上 |
| whisper | 本地 | pkg+cache OK | pkg 缺 / cache 缺 |

## 关键决策

1. **不替换 Easel .env 为唯一源** — 它由 Easel 自己管,我们**镜像一份**便于 UI 配。真发仍走 `_load_easel_env()`(最高优先级),用户在 PrisirAI 配置后建议同步到 Easel .env(在 hint 文案明示)
2. **`***` 不覆盖** — 对齐 LLM/ASR settings 范式,前端把"未改动的 mask"字段当 placeholder,不污染已存值
3. **粗粒度 3 类** — banner 只分 `全绿/部分/缺失`,不在 banner 上展示 provider 粒度(进配置卡才看得到)
4. **多入口提醒**: 视频 Tab 横幅(主动)+ 主对话 EXEC 失败 hint(被动);不全靠被动等用户问
5. **DATA_DIR 同源 wechat-publisher**: 不跟 companion LLM/ASR 共享 `settings.json`,独立 `media_keys.json`(避免 settings.json 过大污染 LLM 设置 UI)

## 易踩坑

1. **Easel .env 优先级** — `resolve_media_key` 必须先读 Easel 否则改了 PrisirAI 配置不生效
2. **secret mask `_present` 标志** — 前端只靠 `api_key` 字段是 `'***' or 'sk-…'` 无法判定"是否填过",加 `_present` 布尔
3. **T16 不回归** — `build_exec_result` 加字段必须 try/except 包裹,失败时保持原 shape(否则前端 T16-D 渲染炸)
4. **verify_wechat_publisher.py 顺序** — 函数定义必须在 CHECKS 列表之前(Python 闭包 lookup 时机)
5. **`tests/` 没 `__init__.py`** — `python -m unittest discover` 找不到,要 `python tests/test_media_keys.py` 直接跑(已有 `if __name__ == "__main__"` 块兜底)

## 下一步(留接口给后续)

- **T19** (后续):Whisper 自动下载 — `resolve_media_key` 检测到 model=large 但本地无 → 提示「首次下载 3GB」并提供一键下载
- **T20** (后续):URL `?focus=` 支持多目标(不只是 media-keys),可对接「任务列表」、「发布历史」等

## 关联

- [[prisir-agent-main-chat-hook]] — P3j T16 主对话 EXEC hook(本任务在它之上加 hint)
- [[prisir-agent-video-followups]] — P3j T15 Web UI「💬 一句话」(banner 沿用其 tab 结构)
- [[prisir-video-creation]] — P3j T11 video_creator(本任务是其依赖状态展示层)