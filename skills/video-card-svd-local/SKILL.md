---
name: video-card-svd-local
description: |
  本地短视频生成(Stable Video Diffusion)。把图片 + prompt 翻译成 25 帧短视频。
  Use when "视频生成", "出视频", "video gen", "SVD", "Stable Video Diffusion"。
  需 NVIDIA GPU 16GB+ (SVD) / 24GB+ (SVD-XT)。
license: MIT
triggers: 视频生成, 出视频, video gen, SVD, Stable Video Diffusion, generate video
requirements: diffusers + CUDA, NVIDIA GPU 16GB+ (SVD) / 24GB+ (SVD-XT)
allowed-tools: ["Bash", "Read", "Write"]
---

# video-card-svd-local

本地短视频生成(Stable Video Diffusion)。**离线**。

## 跑法

```bash
pip install diffusers torch transformers accelerate
python scripts/generate.py input.png --output generated/clip.mp4 --frames 25 --fps 6
```

## 注意

- 单张 GPU 限制,一次只跑一个 clip
- 25 帧约 1 分钟(4090 上),CPU 不可用
- 输出 mp4 用 imageio[ffmpeg] 编码