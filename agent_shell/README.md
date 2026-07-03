# Agent Shell S1+S2

三理念(base / FastLane / GhostLine)统一交互壳。

## 启动

```powershell
cd C:\Users\Administrator\oi_enhancements
python -m agent_shell
```

首次运行生成 `~/.oi_agent/shell.yaml`。

## 依赖

- **必需**: `httpx`, `pyyaml`, `pynput`
- **托盘(可选)**: `pystray`, `Pillow`
- **浮动球 / 顶栏**: tkinter(Windows 自带)
- **PTT 流式 ASR**: `sounddevice`, `websockets`

```powershell
pip install httpx pyyaml pynput pystray Pillow sounddevice websockets
```

## 前提

对应理念的 orchestrator 已启动(例如 base 8730、ghostline 8740)。

## 浮动球(默认 UI)

屏幕右下角出现可拖动的圆形指示器:

| 视觉 | 含义 |
|---|---|
| 球体颜色 | agent 状态(idle 绿 / listening 蓝 / thinking 橙 …) |
| 中心字 | 听 / 思 / 说 / ○ 等状态缩写 |
| 外圈脉冲 | listening / thinking / speaking 时呼吸动画 |

**交互**

| 操作 | 效果 |
|---|---|
| 拖动 | 任意放置,位置写入 `shell.yaml` |
| 单击 | 展开/收起详情气泡(完整状态行) |
| 双击 | 切换 presence |
| 右键 | 打断(Esc 同等) |

顶栏(`ui.status_bar: true`)仍可选,默认关闭。

## 功能

| 功能 | 状态 |
|---|---|
| 浮动球状态 + 动画 | ✅ |
| 理念切换(托盘菜单) | ✅ |
| 轮询 GET /agent_state | ✅ |
| 热键即时 UI 反馈 | ✅ |
| PTT → WS 流式 ASR | ✅ |
| 语音 → DynamicRouter 管线 | ✅ S3 |
| GhostLine blocked / FastLane degraded 健康合成 | ✅ S3 |
| OI thinking/speaking 钩子 | ✅ `agent_shell.hooks` |
| presence / Esc 热键 | ✅ |
| IM | **不含**(独立项目) |

## 配置示例

```yaml
ui:
  floating_orb: true
  status_bar: false
  orb:
    size: 56
    x: 1200   # 拖动后自动保存
    y: 680
```

## OI 钩子

```python
from agent_shell.hooks import AgentStateHooks

hooks = AgentStateHooks(orch_port=8730)
hooks.thinking("推理中…")
hooks.idle()
```

## 热键(默认)

| Profile | PTT | Presence |
|---|---|---|
| base | Ctrl+Shift+Space | Ctrl+Shift+P |
| fastlane | Ctrl+Shift+Space | Ctrl+Shift+P |
| ghostline | Ctrl+Alt+Space | Ctrl+Alt+P |

全局: Esc = 打断

## 已知问题

浮动球 Windows 点击/拖动问题见 [KNOWN_ISSUES.md](KNOWN_ISSUES.md),整体联调阶段统一修复。临时可设 `ui.floating_orb: false` + `ui.status_bar: true`。
