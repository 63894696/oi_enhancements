# PrisirAI 多平台发布模块 — 安装 / 使用 / 集成指南

> 2026-09-25 ship(P3j T10 全 8 步)。本文档面向用户和后续开发者。

## 1. 这是什么

PrisirAI 多平台发布模块(`prisIragent-wechat-publisher` + `prisir_work/publisher.py`)是一个**完整可独立运行**的发布模块,形态对齐 music(日历/托盘/Shell 联动,自带 Web UI)。

支持平台(2026-09-25 ship 时已实现):
| 平台 | 走法 | 备注 |
|------|------|------|
| 微信公众号 | Easel 扫码会话(无需 AppID/IP 白名单) | 真发 |
| 小红书图文 | Easel skill-xhs-publisher(Playwright) | `--exec` 真发 |
| B站视频投稿 | Easel skill-bilibili-upload(biliup CLI) | `--exec` 真发 |
| 抖音 / 知乎 / 视频号 | 占位(NullPublisher) | ok=False + reason |

支持接入:
- **Web UI**:http://127.0.0.1:&lt;port&gt;/
- **HTTP API**:`/api/{health,state,platforms,recall,login,publish,stats}`
- **托盘子菜单**:`agent_shell` 托盘 → 「📢 打开发布面板」
- **CLI**(可选):`python -c "from prisir_work.publisher import publish; ..."`

## 2. 一键安装

### 2.1 装发布后端

```powershell
powershell -ExecutionPolicy Bypass -File .\install_wechat_publisher.ps1
```

默认装:
1. `aiohttp` / `pyyaml`(PrisirAI 后端)
2. `playwright` + Chromium(Easel 扫码登录)
3. Easel 仓库(`git clone --depth=1` 到 `~/work/zju_easel`)
4. Easel Python 三方依赖(Pillow / opencv / markdown / jieba 等)
5. `wcdb-key-tool` Windows 二进制(可选,只本地 recall 用)

幂等:每步先 `pip show` 检查再装。

### 2.2 Skip 模式

```powershell
# 已手动装 Easel
.\install_wechat_publisher.ps1 -SkipEasel

# 不想下 wcdb-key-tool
.\install_wechat_publisher.ps1 -SkipWcdb

# 只想看会做什么
.\install_wechat_publisher.ps1 -DryRun
```

### 2.3 自定义路径

```powershell
.\install_wechat_publisher.ps1 -EaselRoot "D:\projects\Easel" -WcdbToolRoot "D:\tools"
```

## 3. 启动

```bash
python companion\prisIragent-wechat-publisher.py --port 0
# 输出:
# starting prisIragent-wechat-publisher on 127.0.0.1:3767 (pid=25024)
# HKCU\Software\PrisirAI\wechat_publisher_port = 0x49b6
```

打开浏览器访问 stdout 里的 port(0 = 自动分配,写 HKCU + `_prisir_registry/wechat_publisher_port.json`)。

## 4. 首次使用

1. 打开 Web UI 主页
2. 进「⚙️ 平台」页 → 点每个平台的「扫码登录」
   - 公众号:浏览器出 mp.weixin.qq.com 二维码,管理员扫码
   - 小红书:浏览器出 xiaohongshu.com 二维码
   - B站:浏览器出 bilibili.com 二维码 + cookies.json 落盘
3. 进「📤 发布」页 → 选平台 → 填参数 → 发布
   - 公众号:HTML 草稿(标题 / cover / digest / author)
   - 小红书:图文(title / content / images / tags)— 默认 dry-run,勾 `exec_real` 才真发
   - B站:视频(video / title / partition / desc / tag)— 默认 dry-run

## 5. HTTP API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/state` | 服务状态(端口 / pid / uptime / publishers 列表 / 上次发布结果) |
| GET | `/api/platforms` | 列出所有平台 + 登录态(慢 — 调 whoami) |
| GET | `/api/recall?q=...&limit=20` | prisirmp 公众号历史 recall(FTS5) |
| POST | `/api/login` `{"platform":"wechat-oa"}` | 触发某平台扫码登录(异步) |
| POST | `/api/publish` | 发草稿(body 见下) |
| GET | `/api/stats?platform=wechat-oa&count=30` | 公众号数据回收 |
| GET | `/ws/state` | WS 状态广播 |
| GET | `/` | 主页(发布 / 数据管理面板) |

### 5.1 publish body 三种模式

```jsonc
// A) 公众号 HTML 草稿
{
  "platform": "wechat-oa",
  "mode": "html",         // 可省,默认 html
  "html_path": "C:/path/to/article.html",
  "title": "标题",
  "cover": "C:/path/to/cover.jpg",
  "digest": "摘要",         // 可选
  "author": "署名"          // 可选
}

// B) 小红书图文
{
  "platform": "xhs",
  "mode": "text",
  "title": "标题",
  "content": "正文",
  "images": "img1.jpg,img2.jpg",  // 可选
  "tags": "tag1,tag2",            // 可选
  "exec_real": false              // true = 真发
}

// C) B站视频
{
  "platform": "bilibili",
  "mode": "video",
  "video": "C:/path/to/video.mp4",
  "title": "标题",
  "partition": "知识",            // 可选
  "tid": 124,                     // 可选(分区数字)
  "desc": "简介",                 // 可选
  "tag": "tag1,tag2",             // 可选
  "cover": "C:/path/to/cover.jpg",  // 可选
  "exec_real": false              // true = 真投稿
}
```

返回结构(降级范式):
```json
{
  "ok": false,
  "platform": "xhs",
  "title": "测试",
  "error": "rc=1",
  "artifact": {},
  "raw": {"ok": false, "rc": 1, "parsed": {}, ...}
}
```

**`ok=false` 但 status=200 是预期**(降级而非崩溃)。

## 6. 自检

```bash
# 全量自检(11 项 — 含 HTTP 6 endpoint)
python verify_wechat_publisher.py

# 只跑静态检查(不起服务 — 适合 CI)
python verify_wechat_publisher.py -SkipHttp
```

预期:11 passed, 0 failed。

## 7. 集成点

### 7.1 托盘子菜单(已 ship)

`agent_shell/tray.py` 接受 `extra_actions=[...]`,在 profile 切换菜单后插入「📢 打开发布面板」「📅 打开日历」「🎵 打开音乐」三项,自动探测 HKCU 注册端口 + 浏览器跳转。

### 7.2 Tauri 壳端口钉死(已 ship)

端口写 `HKCU\Software\PrisirAI\wechat_publisher_port`(REG_DWORD)+ `_prisir_registry/wechat_publisher_port.json`(跨平台)。Tauri 壳 + companion 启动时探测 → 复用现有端口。

### 7.3 注册新 publisher

```python
from prisir_work.publisher import (
    PlatformPublisher, PlatformPublishResult, register_publisher
)

class MyPlatformPublisher:
    name = "my-platform"
    title = "我的平台"
    @property
    def ready(self): return True
    def status(self): return {"loggedIn": True}
    def login(self): return PlatformPublishResult(ok=True, platform=self.name)
    def publish_html(self, html_path, *, title, cover, **kw):
        return PlatformPublishResult(ok=True, platform=self.name, title=title)

register_publisher(MyPlatformPublisher())
```

## 8. 边界 / 红线

1. **降级而非崩溃**:任何 publisher 未就绪 / 文件不存在 / 子进程失败,统一返 `ok=False + reason`,绝不抛栈。
2. **exec_real 默认 False**:小红书 / B站默认 dry-run(只校验参数不真发),必须显式勾选才真发布。
3. **本地扫码**:不接 AppID / OAuth,所有平台都走浏览器扫码会话(Easel 自带)。
4. **subprocess timeout**:`whoami` 30s / `publish` 300s / `login` 15-20s。超时 → ok=False。
5. **端口冲突**:默认 `--port 0` 自动分配,避免与 music / calendar 撞。

## 9. 关联模块

| 模块 | 路径 | 作用 |
|------|------|------|
| Easel 桥接 | `prisir_work/easel_bridge.py` | subprocess 调 Easel 7 个 CLI |
| 多平台抽象 | `prisir_work/publisher.py` | Protocol + 注册表 + 6 平台 |
| 后端主入口 | `companion/prisIragent-wechat-publisher.py` | aiohttp + HKCU 注册 |
| Web UI | `companion/prisIragent-wechat-publisher/static/index.html` | 4 tab |
| 托盘联动 | `agent_shell/app.py:_extra_menu` | 「📢 打开发布面板」 |
| 安装脚本 | `install_wechat_publisher.ps1` | 一键装全栈 |
| 自检 | `verify_wechat_publisher.py` | 11 项 E2E |
| 测试 | `tests/test_wechat_publisher_module.py` | 16 cases |

## 10. Roadmap

- [ ] 抖音 / 知乎 / 视频号接入(Easel 都有 skill,等对接)
- [ ] AI 草稿自动生成(LLM → MD → Easel md2html → publish)
- [ ] 草稿定时发布(scheduler + Easel 延迟投稿)
- [ ] 发布回执(media_id → 链接 / 二维码)
- [ ] 跨账号管理(一个 Easel 多公众号 YAML)