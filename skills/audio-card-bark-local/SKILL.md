---
name: audio-card-bark-local
description: |
  本地 TTS / 音效生成(Bark,Suno 开源)。完全离线,调用户已装的 bark + GPU。
  Use when "TTS", "语音合成", "念一句", "读出来", "音效", "Bark", "配音"。
  需 NVIDIA GPU 4GB+ (small) / 12GB+ (full)。
license: MIT
triggers: TTS, 语音合成, 念一句, 读出来, 音效, Bark, 配音, read aloud
requirements: pip install bark, NVIDIA GPU 4GB+ (small) / 12GB+ (full)
allowed-tools: ["Bash", "Read", "Write"]
---

# audio-card-bark-local

本地 TTS + 音效(Bark)。**离线**。

## 跑法

```bash
pip install bark
python scripts/generate.py "Hello, world." --output generated/hello.wav --voice v2/en_speaker_6
```

## Voice preset

参见 https://github.com/suno-ai/bark/blob/main/bark/assets/prompts/README.md