// pcm-worklet.js — AudioWorklet processor,把麦克风 float32 转 PCM16k Int16 mono
// 2026-09-16 M3.3:百炼 Paraformer-realtime-v2 只吃 PCM16kHz16bit mono
class PcmWorkletProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this._targetRate = (options && options.processorOptions && options.processorOptions.targetRate) || 16000;
    this._chunkMs = 100;  // 100ms 一帧
    this._samplesPerChunk = this._targetRate * this._chunkMs / 1000;  // 1600
    this._ring = new Float32Array(this._samplesPerChunk * 4);  // 4 chunk 容量
    this._ringWrite = 0;
    this._ringRead = 0;
    this._enabled = false;
    this.port.onmessage = (e) => {
      if (e.data === "start") this._enabled = true;
      else if (e.data === "stop") this._enabled = false;
    };
  }

  process(inputs) {
    if (!this._enabled) return true;
    const inp = inputs[0];
    if (!inp || !inp[0] || inp[0].length === 0) return true;
    const mono = inp[0];  // 第 0 路(channel 0)
    // 单声道源直接送;立体声混到 mono
    const ch0 = inp.length > 1 && inp[1] && inp[1].length ? inp[1][0] : null;
    const n = mono.length;
    // 简单降采样:如果 sampleRate != targetRate,线性插值
    const inRate = sampleRate;
    const ratio = inRate / this._targetRate;
    // 计算本帧应产出多少 PCM 样本
    const outN = Math.floor(n / ratio);
    const pcm = new Int16Array(outN);
    for (let i = 0; i < outN; i++) {
      // 取源位置(浮点),加权平均 ch0/ch1
      const srcPos = i * ratio;
      const i0 = Math.floor(srcPos);
      const i1 = Math.min(n - 1, i0 + 1);
      const frac = srcPos - i0;
      let s0 = mono[i0];
      let s1 = mono[i1];
      if (ch0) {
        s0 = (s0 + ch0[i0]) * 0.5;
        s1 = (s1 + ch0[i1]) * 0.5;
      }
      let v = s0 + (s1 - s0) * frac;
      // clip
      if (v > 1) v = 1; if (v < -1) v = -1;
      // 转 int16 little-endian
      const iv = v < 0 ? Math.round(v * 0x8000) : Math.round(v * 0x7fff);
      pcm[i] = iv;
    }
    // 发到主线程
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
    return true;
  }
}

registerProcessor("pcm-worklet", PcmWorkletProcessor);