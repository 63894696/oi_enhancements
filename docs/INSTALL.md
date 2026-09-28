# PrisirAI 安装指引(INSTALL Guide)

> 本文档汇总 PrisirAI 各子能力模块的安装步骤。
> **必装项**标记 ✅,**强烈推荐**标记 ⚠,**可选**标记 ○。

## 1. Python 环境(必装 ✅)

PrisirAI 需要 Python 3.10+。

```bash
python --version   # 应显示 3.10 / 3.11 / 3.12
```

推荐 Python 3.12(已验证 3.12.9 可用)。

---

## 2. 核心依赖(必装 ✅)

```bash
pip install aiohttp pyyaml playwright
playwright install chromium
```

---

## 3. Agent-Reach(强烈推荐 ⚠) — 14 平台信息源

Agent-Reach 给 PrisirAI 提供小红书 / B站字幕 / GitHub / V2EX / RSS / YouTube字幕
等 14 个平台的「读+搜」能力,**无需 API key**。

### 3.1 安装

```bash
pip install agent-reach
```

### 3.2 验证安装

```bash
agent-reach doctor
```

应输出 14 平台状态:

```
✅ agent-reach v0.x.x
✅ xhs              OK
✅ bilibili-subtitle OK
✅ github           OK
✅ v2ex             OK
✅ youtube-subtitle OK
✅ rss              OK
✅ bilibili-search  OK
...
```

### 3.3 验证 PrisirAI 集成

启动 PrisirAI 后:

1. 浏览器访问 wechat-publisher(默认端口 18899)
2. 切到「🧩 扩展」Tab
3. 应看到顶部 banner 显示 `✅ agent-reach v0.x.x · N/14 平台就绪`
4. 14 平台卡片(小红书 / B站字幕 / GitHub / V2EX / YouTube字幕 / RSS + 8 个其他)
5. P0 6 平台默认 toggle 开启(左边框标记)

若显示 `❌ agent-reach 未安装`:

- 点 banner 右上「📖 安装指引」按钮 → 跳 GitHub README
- 或回到终端跑 `pip install agent-reach` 后重启 PrisirAI

### 3.4 平台依赖(按需)

部分平台依赖额外系统工具,首次调可能提示:

| 平台 | 依赖 | 用途 |
|------|------|------|
| 小红书 xhs | playwright(已有) | 反爬处理 |
| B站字幕 bilibili-subtitle | yt-dlp | 拉字幕 |
| YouTube 字幕 youtube-subtitle | yt-dlp | 拉字幕 |
| Exa / Jina | EXA_API_KEY / JINA_API_KEY env | 搜索 API |
| 微博 weibo | playwright(已有) | 反爬处理 |
| 知乎 zhihu | playwright(已有) | 反爬处理 |

装法:

```bash
pip install yt-dlp   # B站字幕 + YouTube字幕必须
```

### 3.5 故障排查

| 现象 | 排查 |
|------|------|
| ❌ banner 显示未安装 | `which agent-reach` 应有输出;否则 pip 装到别的 Python 环境了 |
| ⚠ doctor 返部分 warn | 看具体平台提示 — 多半是 yt-dlp / playwright 未装 |
| ❌ pip install 失败 | 需要 Python 3.10+,老版本会报语法错 |
| ⚠ B站字幕读不到 | 部分视频没字幕,换条试试 |
| ❌ `FileNotFoundError` 子进程 | PrisirAI Python 环境跟 pip 不一致,统一用 `python -m pip install` |

---

## 4. 多媒体创作依赖(可选 ○)

详见 [prisIr-media-keys-2026-09-26.md](prisIr-media-keys-2026-09-26.md) § 多媒体创作 Key 配置

### 4.1 SiliconFlow(推荐 ⚠)— AI 配图 / 视频

```bash
# 设置环境变量(写在 ~/.bashrc 或 Windows env)
export SILICONFLOW_API_KEY=sk-xxxxxxxx
```

获取:https://siliconflow.cn/ 注册 → 控制台 → API Keys

### 4.2 edge-tts(免费 ○)— 中文配音

```bash
pip install edge-tts
```

零配置,直接可用。

### 4.3 faster-whisper(推荐 ⚠)— 语音转字幕

```bash
pip install faster-whisper
```

首次使用自动下载模型 ~150MB。

### 4.4 ffmpeg(必装 ✅)— 视频合成 / 字幕烧录

Windows:https://www.gyan.dev/ffmpeg/builds/ 下载 full_build,解压后将 `bin/` 加到 PATH

Linux:

```bash
sudo apt install ffmpeg
```

验证:

```bash
ffmpeg -version
```

---

## 5. 多平台发布(可选 ○)

详见 [prisIr-publisher-module-2026-09-25.md](prisIr-publisher-module-2026-09-25.md)

各 publisher 的 cookie / token 配置入口:

- 公众号:PrisirAI 主程序 → 「🎬 视频/发布」→ 「📱 发布」Tab
- 小红书 / B站 / 知乎:同上
- YouTube:Google API Console 申请 OAuth client_id + client_secret

---

## 6. 视频创作 + 处理(可选 ○)

详见 [prisIr-video-creation-2026-09-25.md](prisIr-video-creation-2026-09-25.md)
+ [prisIr-video-extensions-2026-09-25.md](prisIr-video-extensions-2026-09-25.md)

需要 Easel 项目:

```bash
# 克隆 Easel 仓库(已装可跳过)
cd ~/work
git clone https://github.com/.../zju_easel.git
```

Easel 路径写入 `companion/priseasel/.env`:

```
EASEL_PATH=/path/to/zju_easel
```

---

## 7. 启动 PrisirAI

```bash
python prisiragent-companion-web.py
```

或启动 Tauri 壳:

```bash
cd prisiragent-tauri/src-tauri
cargo run --release
```

启动后:

- 主对话窗口:默认 18801(或托盘菜单切换)
- wechat-publisher:18899
- music / calendar / workflow:各有独立端口

---

## 8. 一键安装脚本(可选 ○)

```bash
# 一键安装所有必装 + 推荐项
curl -sL https://raw.githubusercontent.com/.../install.sh | bash
```

或本地:

```bash
./installer/install_all.sh
```

---

## 附录:验证清单

启动 PrisirAI 后,跑自检脚本:

```bash
python verify_wechat_publisher.py -SkipHttp   # 37/37 全绿
python verify_main_chat.py                    # 主对话 + capability 链路全绿
```

通过 = 安装完成。