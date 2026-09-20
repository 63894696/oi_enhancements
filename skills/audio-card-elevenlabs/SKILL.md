---
name: audio-card-elevenlabs
description: |
  在线 TTS / 配音(ElevenLabs)。需 ELEVENLABS_API_KEY。
  Use when "ElevenLabs", "专业配音", "克隆声音", "voice clone", "有声书配音"。
  业界 MOS 4.6 最高,支持 voice clone / multilingual / 实时流式。
license: MIT
triggers: ElevenLabs, 专业配音, 克隆声音, voice clone, 有声书配音, TTS
requirements: ELEVENLABS_API_KEY 环境变量, Pro 起步 $22/月(10k 字符免费 tier)
allowed-tools: ["Bash", "Read", "Write", "WebFetch"]
---

# audio-card-elevenlabs

在线 TTS(ElevenLabs)。**联网**。

## 跑法

```bash
python scripts/check.py
python scripts/generate.py "Hello, world." --voice Rachel --output generated/hello.mp3
```

## Voice ID

预置 voice 见 https://api.elevenlabs.io/v1/voices; 自定义 clone 走 ElevenLabs UI。