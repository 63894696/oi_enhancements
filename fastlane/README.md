# Cloud-First Architecture – FastLane

> A cloud-native voice interface with automatic fallback to local models.

## 当前状态(2026-07-03)

已实现(`fastlane/adapters/`,有测试覆盖,见 `test_full_suite.py` 第 9 节):

- `CloudAdapter` 基类 + `CloudASRClient` / `CloudLLMClient` / `CloudIMClient` 骨架
- `CloudRouter` — F-5 自动降级(按优先级尝试,失败切下一个,降级日志明示)
- `enforce_https` / `make_ssl_context` — F-6 强制 HTTPS + TLS 1.3 + 证书校验
- FastAPI 聚合服务 `fastlane.adapters.main:app`(/health /transcribe /generate /send_message)
- IM 抽象接口在 `oi_enhancements/im_clients/`(SimpleX / Matrix 参考实现)

尚未实现(下方目录树为 ADR-002 规划,**豆包/Qwen/Whisper/EdgeTTS 等真云接入还没有代码**):
asr_cloud / llm_cloud / tts_cloud 各具体 provider、预热脚本、`voice_input_fastlane` 独立仓库源码。

## Quick-start(当前可跑的部分)

```bash
# 启动云端聚合服务骨架
uvicorn fastlane.adapters.main:app --port 8760

# 真云 provider 接入后,把实例按优先级塞进 CloudRouter 即可获得自动降级:
#   CloudRouter([doubao_asr, qwen_asr, sensevoice_local])
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    FastLane 云优先架构                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐     ┌───────────────┐     ┌────────────┐ │
│  │   热键层    │────▶│  ASR 流式层   │────▶│   LLM 层   │ │
│  │ (hotkey.py) │     │  doubao/qwen   │     │  qwen/deepseek│ │
│  └─────────────┘     └───────────────┘     └────────────┘ │
│          │                      │                   │       │
│          ▼                      ▼                   ▼       │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │               记忆层 (AutoGenetic Memory)               │ │
│  │  - L0: identity  │  L1: essential  │  L2: on-demand    │ │
│  │  - L3: deep-search with vector similarity (FAISS)      │ │
│  └─────────────────────────────────────────────────────────┘ │
│                      │              │                        │
│                      ▼              ▼                        │
│              ┌──────────┐ ┌───────────────┐                │
│              │ TTS 云端 │ │   TTS 本地    │                │
│              │ edge/cosy│ │ SAPI fallback │                │
│              └──────────┘ └───────────────┘                │
│                      │              │                        │
│                      ▼              ▼                        │
│              ┌──────────────────────────────┐               │
│              │    IM 客户端抽象 (SimpleX/Matrix) │              │
│              └──────────────────────────────┘               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 目录结构

```
voice_input_fastlane/
├── docs/
│   └── adr/
│       └── ADR-002-fastlane-cloud-first.md
├── src/voice_input_fastlane/
│   ├── config.py
│   ├── asr_cloud/
│   │   ├── __init__.py
│   │   ├── doubao_streaming.py
│   │   ├── qwen_asr.py
│   │   ├── openai_whisper.py
│   │   └── sensevoice_local_fallback.py
│   ├── llm_cloud/
│   │   ├── __init__.py
│   │   ├── qwen.py
│   │   ├── deepseek.py
│   │   ├── claude.py
│   │   └── ollama_local_fallback.py
│   ├── tts_cloud/
│   │   ├── __init__.py
│   │   ├── edge_tts.py
│   │   ├── bailian_cosyvoice.py
│   │   └── sapi_fallback.py
│   ├── im/
│   │   ├── __init__.py
│   │   ├── wechat.py
│   │   ├── dingtalk.py
│   │   ├── feishu.py
│   │   └── slack.py
│   └── orchestrator.py
├── scripts/
│   ├── prewarm_connections.py
│   └── health_check_all.py
└── README.md
```

## 核心特性

- **自动降级**：云端失败 → 本地 SenseVoice / Ollama
- **SSL/TLS 1.3 强制**：阻止 HTTP 请求
- **预热机制**：启动即建立 TLS 连接，消除首次延迟
- **记忆持久化**：使用 AutoGenetic Memory，支持向量检索