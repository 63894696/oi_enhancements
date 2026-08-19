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

---

# P1 轨 A 离线预生成字幕原型(2026-08-19 已跑通)

`subtitle_gen.py`:soundcard WASAPI loopback 听系统音频 → silero-vad 切句 →
Rust DLL(`transcribe_pcm`)→ 带时间轴 SRT + sidecar 元数据,存 `~/Downloads/PrisirSubtitles/`。

```bash
# 终端1:先起听写(等打印"引擎就绪")
python subtitle_gen.py --dur 65 --speed 1.0 --title 视频名 --url <URL> --speaker USB
# 终端2:引擎就绪后播放视频/音频(经声卡)
```

**实测(60s 关雎,USB 声卡 loopback)**:9 条字幕,全诗覆盖,标点正确(textnorm=14),
时间轴 0–60s 单调递增无漂移。识别与 P0 一致。

**关键坑(留给后续)**:
1. **时间轴漂移根因**:`silero_vad.VADIterator` 返回的 `start`/`end` 是**绝对样本数**且含
   `speech_pad`(默认 30ms)回退,指到当前 chunk **之前**。直接 `total+pos+ev['start']` 会错位漂移。
   **修法**:钳制在 `[当前块起点, 当前块起点+chunk]`(见 `capture_and_transcribe`)。
2. **采集帧数是准的**(实测 record 读到的样本=真实时长,无丢帧);`data discontinuity` 只是
   播放/采集时钟微小偏移的告警,可 `warnings.filterwarnings("ignore")`。漂移别赖采集,先查 VAD 语义。
3. **能量门控**消幻听:RMS<0.01 的静音段不过 ASR(否则出 "Yeah."/"没." 碎片);
   纯英文短碎片(≤12字符无中文)也丢弃(中文视频 VAD 误触发)。
4. **先加载模型+VAD 再开始采集**(`"引擎就绪"` 日志),避免错过音频开头。
5. **加速静音**:`--speed 2.0` 时按加速比回标时间轴(`start/end *= speed`)。
   P1 播放器未加速实测,2x 链路由 P2 接管(浏览器控制 `<video>.playbackRate` + 静音)。
6. **soundcard loopback**:`sc.get_microphone(扬声器名, include_loopback=True).recorder(...)`,
   48k stereo → `resample_poly(÷3)` → 16k mono。

**P1 红线遵守**:轨 A 只听系统音频输出(合法听写,不碰 DRM 视频内容);纯本地零外发;
复用 P0 Rust 引擎不重写识别。

**下一步 P2**:翻译插件"字幕"按钮加"加载本地字幕"(读 `~/Downloads/PrisirSubtitles/*.srt` +
L0 URL/ID 配对),ASR 字幕引擎注册进 `engines.js` 总线(默认 SenseVoice 兜底)。
