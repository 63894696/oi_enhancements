# prisir_asr — Prisir Browser 内置 ASR 引擎 (P0)

SenseVoice Small 本地语音识别,Rust + ort(ONNX Runtime)。移植自已验证的 Python 参考
`voice_input/src/voice_input/asr/sensevoice.py`(逻辑同源,Python 保留为开发/测试主力)。

## P0 验收结果(2026-08-19)

| 判据 | 结果 |
|---|---|
| 加载 model.onnx + tokens.json | ✅ `SenseVoiceASR::load(model_dir)` |
| 音频 → 文字 | ✅ 60s 关雎 wav → 完整诗文 |
| 正确率对标 Python | ✅ `rs[1:] == py` 逐字符一致(唯一差异:ORT 1.29 vs 1.26 在首帧 argmax 平手分一个 `南`,非移植缺陷;特征已验证逐位相同) |
| 推理速度 | ✅ 60s 音频 ~3.4–3.9s(RTF≈0.06,与 Python 同级) |
| cdylib + C ABI | ✅ `prisir_asr.dll`,`prisir_asr_load/transcribe_wav/transcribe_pcm/free_string/free`,ctypes 实测通过 |

## 结构

- `src/lib.rs` — 特征提取(预加重/hamming/STFT/mel/log/CMVN/LFR)+ CTC greedy decode + `SenseVoiceASR`
- `src/ffi.rs` — C ABI(浏览器 Chromium FFI 用,P5)
- `src/main.rs` — CLI 验证程序
- `ort_bin/` — vendored ONNX Runtime 1.29 `onnxruntime.dll`(见下「关键决策」)

## 构建 / 运行

```bash
cargo build --release
# CLI: prisir_asr <model_dir> <wav> [language=1] [textnorm=14]
./target/release/prisir_asr.exe \
  "C:/Users/Administrator/AppData/Roaming/Shandianshuo/models/sensevoice-small" \
  some_16k_mono.wav 1 14
```

模型目录需含 `model.onnx` + `tokens.json`(默认用闪电说 SenseVoice Small,241MB)。
language: 0=auto 1=zh 2=en 3=ja 4=ko;textnorm: 14=带标点+数字规整(with_itn),15=不带(wo_itn)。

## 关键决策 / 坑(留给后续)

1. **ONNX Runtime 用 `load-dynamic` + vendored 1.29 dll**(`ort_bin/onnxruntime.dll`)。
   原因:`ort 2.0.0-rc.13` 默认特性含 `api-27`,`MINOR_VERSION=27`,会**拒绝** <1.27 的运行时
   (本机 pip 是 1.26 → `BadVersion`)。下载官方 `onnxruntime-win-x64-1.29.0.zip` 取出 dll 即可。
   这正是安装包该打包的运行时,顺带解决分发。
2. **`ort::init_from(path)` 是自由函数**(不是 `EnvironmentBuilder` 方法);`init_ort()` 在
   创建 `Session` 前调用一次。默认路径取 `current_exe()../../../ort_bin`,可被 `ORT_LIB_LOCATION` 覆盖。
3. **ort rc.13 用 ndarray 0.17**(不是 0.16)。builder 各步返回 `Error<SessionBuilder>`,
   需 `.map_err(|e| AsrError::Ort(e.into()))` 收敛到 `Error<()>`。
4. **输入 wav 必须 16kHz mono 16-bit**;采样率不对会拒绝(`load_wav_f32`)。上层(P1 loopback)
   负责重采样到 16k。
5. 特征提取与 Python 逐位一致(已 dump `f[0,:8]` 比对)。CTC 过滤 `<|...|>` 特殊 token,
   `▁`→空格。
6. 红线遵守:纯本地推理零外发;模型路径可配(为 P2 模型可插拔/engines.js 总线预留)。

## 下一步(P1)

轨 A 离线预生成字幕:WASAPI loopback 听系统音频 → 重采样 16k → 本引擎 → 带时间轴 SRT
(含加速静音)。本 crate 的 `transcribe_pcm` 直接复用。
