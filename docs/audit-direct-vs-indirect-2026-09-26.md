# PrisirAI 间接接入审计(P3j T23,2026-09-26)

## Context

**用户原话**:
> 我们在接agent-reach之前,可能也接过别的类似的能力层项目而没有直接接上游工具,
> 可以对PrisirAI做一次接入检查,做类似直接接上游工具的调整。

**核心问题**:PrisirAI 里有没有「间接接入上游工具」的项目(走能力层/中间件,而非直接调),
应该改成跟 T21-A/B/C(feedparser/yt-dlp/gh CLI)+ T22-A/B(Exa/HN)一样的「直接调上游 API/库」模式?

## 审计范围

| 类别 | 文件 | 数量 |
|------|------|------|
| **直接整合(已 ship)** | feedparser / yt-dlp / gh CLI / jina reader+search / HackerNews Algolia / Exa MCP / YouTube Data API v3 | 7 项 |
| **子进程包装的间接接入** | agent_reach_bridge / easel_bridge / video_creator(Easel 9 creator)/ gh_bridge(直接整合产物) | 4 文件 |
| **PrisirAI 内部 HTTP 代理** | port_registry(proxy_post 走 aiohttp → companion 子服务) | 1 文件 |

## 审计结论

### 4 处子进程包装逐一评估

| 间接接入 | 上游工具性质 | 直接接入价值 | 决策 |
|---------|------------|-----------|------|
| **agent_reach_bridge.py** | 已是封装完好的 CLI(内部已直调 feedparser/yt-dlp) | ❌ 低 | **🚫 不做** |
| **easel_bridge.py** | 平台专属 — 公众号扫码/小红书 cookie/B站 cookie/AI 反检测 | ❌ 极低 | **🚫 不做** |
| **video_creator.py** (9 creator) | 同上 + Easel 工程目录编排 + .env(SILICONFLOW_KEY 等) | ❌ 极低 | **🚫 不做** |
| **gh_bridge.py** | 已是直接整合产物(T21-C ship) | — | ✅ 已是 ship 状态,不算「间接」 |

**关键洞察**:
- 「能力层/中间件」分两种:
  - **真上游工具**(feedparser/yt-dlp/gh CLI/Exa/Algolia/HN/Jina)— 应该直接调;T21/T22 已全部 ship
  - **平台专属能力**(扫码登录/cookie/反检测/平台 API)— 这不是「上游工具」,是平台能力封装;**不能也不应该直接 import**;子进程桥 = 唯一正确设计
- agent-reach 子进程桥 ≠ 「能力层间接接入」;agent-reach 自己**已经**是 CLI 封装层(直接调 feedparser/yt-dlp);我们再用子进程桥是「接 CLI 而非接 Python 库」,这是设计意图(T20 ship 时已固化)
- Easel 脚本 = **平台能力工厂**(playwright + Chromium + cookie + 扫码 + AI 反检测 + MD 排版);「直接 import edge-tts」**会失去 Easel 的工程目录/cookie/proxy/.env 编排能力**

### 决策详情

#### 1. agent-reach 子进程桥 — 保留

**Why 保留**:
- agent-reach 已 ship(0.1.0,只 rss + youtube 两个 channel)
- 内部实现就是直接调 feedparser / yt-dlp(无中间层)
- 子进程 = 版本隔离(agent-reach 升级 0.x → 1.x 不会拖垮主进程)+ 异常隔离(子进程死锁不波及主进程)
- T20 ship 时已固化此决策

**何时重新评估**:
- agent-reach 引入 Python 库模式(从 CLI 转 SDK)且接口稳定 2 个 minor 版本
- 我们 ship 直接 feedparser/yt-dlp 后,agent-reach 失去「补充能力」价值

#### 2. Easel 子进程桥(发布/AI 评分/MD→HTML)— 保留

**Why 保留**:
- Easel 脚本内有 playwright + Chromium + Chrome 扩展(`mp.weixin.qq.com` 后台抓取)
- 含 cookie 文件管理 + 扫码登录 + AI 反检测(公众号审核规避)
- 含 MD→HTML 排版 + theme 系统
- 「直接 import easel」=「pip install easel」= 拉 25+ 依赖 + 强制装 Playwright + Chromium + Chrome 扩展,违反模块化原则(已经在 easel_bridge.py docstring 顶部明确说明)

**何时重新评估**:
- mp.weixin.qq.com 后台改为开放 API(可能性<5%,微信不会开放)
- 我们 ship 自研「公众号开放 API 投稿」(需要企业资质 + 微信支付 API)

#### 3. Easel 视频 9 creator — 保留

**Why 保留**:
- tts/asr/assemble/image-gen/video-gen 各自都强依赖 Easel 工程目录布局
- 编排器(Orchestrator)的核心价值 = 「主题→终片」pipeline,拆散 = 失去编排
- SILICONFLOW_API_KEY 在 Easel .env 管(不在 PrisirAI env)— 直接调需复制整套 key 管理
- edge-tts 联网 / faster-whisper 大模型下载 / ffmpeg 长路径 — 都已包在 Easel 脚本里

**何时重新评估**:
- 我们 ship 自研视频编排 pipeline(工程量 = 重写 1/3 Easel,不划算)
- SILICONFLOW_API_KEY 改在 PrisirAI 内统一管(env_keys.py 已有),可直接抽 5 creator

#### 4. port_registry → companion 子服务 — 不算间接接入

**Why 保留**:
- 不是「上游工具」概念 — 是 PrisirAI 自身的跨子服务通信(HTTP proxy)
- 主服务(PrisirWork 18826)代理请求到子服务(wechat-publisher 18899 / music 18860 等)
- 跨进程 IPC 用 HTTP 是行业惯例(FastAPI / Flask / Django 都这么干)

## 已 ship 的「直接整合」(作为基线)

| 工具 | 文件 | 路径 |
|------|------|------|
| feedparser | `prisir_work/web_fetch_feedparser.py` | T21-A |
| yt-dlp | `prisir_work/web_fetch_ytdlp.py` | T21-B |
| gh CLI | `prisir_work/gh_bridge.py` | T21-C |
| jina reader/search | `prisir_work/web_fetch_jina.py` | T20-I / T20-I.2 |
| YouTube Data API v3 + yt-dlp 字幕 | `prisir_work/youtube_bridge.py` | T13-B |
| HackerNews Algolia | `prisir_work/hn_bridge.py` | T22-B |
| Exa MCP | `prisir_work/exa_bridge.py` | T22-A |

## 决策汇总(给未来的自己看)

| 用户问 | 答 |
|--------|----|
| 「agent-reach 子进程能改成直接调它的 Python 库吗?」 | ❌ agent-reach 没有 Python 库(纯 CLI);我们也已 ship 直接调它内部的 feedparser/yt-dlp |
| 「Easel 脚本能直接 import 调吗?」 | ❌ 那等于 pip install easel(25+ 依赖 + Playwright + Chromium);Easel 脚本内的 cookie/扫码/反检测都是平台能力,非「上游工具」 |
| 「video_creator 的 9 个 creator 能直接调 edge-tts/faster-whisper/ffmpeg 吗?」 | ❌ 会失去 Easel 工程目录编排 + .env(SILICONFLOW_KEY)+ Orchesrator pipeline |
| 「port_registry 的 proxy_post 能改成 in-process 调用吗?」 | ❌ 那不是上游工具,是 PrisirAI 自身子服务 IPC;HTTP 跨进程是正确的 |

## 何时再次触发审计

- 新接「能力层项目」时(类似 agent-reach) — 先问「它内部是直接调上游还是又包了一层?」
- Easel 仓库发生架构变更(去掉 playwright 依赖、改 SDK 形式) — 重新评估
- 公司政策变化(微信公众号开放 API / PrisirAI 统一管 SILICONFLOW_KEY) — 重新评估
- 新需求出现「应该改直接调上游」的项目(具体问题具体分析)

## Critical Files

**新建**
- [docs/audit-direct-vs-indirect-2026-09-26.md](docs/audit-direct-vs-indirect-2026-09-26.md)
- [memory/audit-direct-vs-indirect.md](memory/audit-direct-vs-indirect.md)

**修改**
- [memory/MEMORY.md](memory/MEMORY.md) — index 加 1 行

**不动**
- [prisir_work/agent_reach_bridge.py](prisir_work/agent_reach_bridge.py)
- [prisir_work/easel_bridge.py](prisir_work/easel_bridge.py)
- [prisir_work/video_creator.py](prisir_work/video_creator.py)
- [prisir_work/port_registry.py](prisir_work/port_registry.py)

## Verification

不需要新增测试(无代码改动)。仅:
```bash
# 1. 跑全套 verify 确认无回归
python verify_wechat_publisher.py -SkipHttp

# 2. grep 确认 4 个 bridge 文件未改
git diff prisir_work/agent_reach_bridge.py prisir_work/easel_bridge.py \
        prisir_work/video_creator.py prisir_work/port_registry.py
# 期望:无 diff
```

## Risks

无 — 这是决策文档,不动代码。