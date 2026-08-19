//! Prisir Browser 内置 ASR 引擎 — SenseVoice Small 本地推理 (Rust + ort)
//!
//! 移植自已验证的 Python 参考实现:
//!   `voice_input/src/voice_input/asr/sensevoice.py` (P0,逻辑同源)
//!
//! 流程:16kHz mono PCM → WavFrontend(预加重/分帧/mel/log/CMVN/LFR)
//!   → ONNX 推理 → CTC greedy decode → 过滤 <|...|> 特殊 token → 文本
//!
//! textnorm 值(见 lingxi_config.py):14=带标点+数字规整(with_itn),15=不带(wo_itn)。
//! Python 侧旧代码曾误传 2(见 dictation_tool.py 注释),本实现默认 14 对齐 lingxi_app。

use ndarray::{Array2, Array3};
use ort::session::builder::GraphOptimizationLevel;
use ort::session::Session;
use ort::value::Value;
use rustfft::{num_complex::Complex32, Fft};
use std::path::{Path, PathBuf};
use std::sync::Arc;

// ---- WavFrontend 参数(对齐 config.yaml / sensevoice.py) ----
pub const SAMPLE_RATE: u32 = 16000;
pub const N_MELS: usize = 80;
pub const FRAME_LENGTH_MS: u32 = 25;
pub const FRAME_SHIFT_MS: u32 = 10;
pub const LFR_M: usize = 7;
pub const LFR_N: usize = 6;
pub const N_FFT: usize = 512;
pub const PREEMPH_COEF: f32 = 0.97;
pub const BLANK_ID: i64 = 0;

pub type AsrResult<T> = Result<T, AsrError>;

#[derive(Debug)]
pub enum AsrError {
    Ort(ort::Error),
    Io(std::io::Error),
    Json(serde_json::Error),
    ModelNotFound(PathBuf),
    InvalidAudio(String),
}

impl std::fmt::Display for AsrError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AsrError::Ort(e) => write!(f, "ort error: {e}"),
            AsrError::Io(e) => write!(f, "io error: {e}"),
            AsrError::Json(e) => write!(f, "json error: {e}"),
            AsrError::ModelNotFound(p) => write!(f, "model not found: {}", p.display()),
            AsrError::InvalidAudio(m) => write!(f, "invalid audio: {m}"),
        }
    }
}

impl std::error::Error for AsrError {}
impl From<ort::Error> for AsrError {
    fn from(e: ort::Error) -> Self {
        AsrError::Ort(e)
    }
}
impl From<std::io::Error> for AsrError {
    fn from(e: std::io::Error) -> Self {
        AsrError::Io(e)
    }
}
impl From<serde_json::Error> for AsrError {
    fn from(e: serde_json::Error) -> Self {
        AsrError::Json(e)
    }
}
impl From<hound::Error> for AsrError {
    fn from(e: hound::Error) -> Self {
        AsrError::InvalidAudio(format!("wav: {e}"))
    }
}

// ============================================================
// 特征提取
// ============================================================
fn hamming_window(len: usize) -> Vec<f32> {
    if len <= 1 {
        return vec![1.0; len];
    }
    (0..len)
        .map(|i| 0.54 - 0.46 * (2.0 * std::f32::consts::PI * i as f32 / (len - 1) as f32).cos())
        .collect()
}

fn preemphasis(x: &[f32], coef: f32) -> Vec<f32> {
    if x.is_empty() || coef <= 0.0 {
        return x.to_vec();
    }
    let mut out = Vec::with_capacity(x.len());
    out.push(x[0]);
    for i in 1..x.len() {
        out.push(x[i] - coef * x[i - 1]);
    }
    out
}

/// HTK mel 滤波器组 (n_mels × (n_fft/2+1))
fn mel_filterbank(n_mels: usize, n_fft: usize, sr: u32) -> Array2<f32> {
    let hz_to_mel = |f: f32| 2595.0 * (1.0 + f / 700.0).log10();
    let mel_to_hz = |m: f32| 700.0 * (10f32.powf(m / 2595.0) - 1.0);

    let mel_min = hz_to_mel(0.0);
    let mel_max = hz_to_mel(sr as f32 / 2.0);
    let mel_points: Vec<f32> = (0..=n_mels + 1)
        .map(|i| mel_min + (mel_max - mel_min) * i as f32 / (n_mels + 1) as f32)
        .collect();
    let hz_points: Vec<f32> = mel_points.iter().map(|&m| mel_to_hz(m)).collect();

    let n_bins = n_fft / 2 + 1;
    let bin_freq: Vec<f32> = (0..n_bins)
        .map(|j| (sr as f32 / 2.0) * j as f32 / (n_bins - 1) as f32)
        .collect();

    let mut fb = Array2::<f32>::zeros((n_mels, n_bins));
    for i in 0..n_mels {
        let (left, center, right) = (hz_points[i], hz_points[i + 1], hz_points[i + 2]);
        for (j, &f) in bin_freq.iter().enumerate() {
            if f < left || f > right {
                continue;
            }
            fb[(i, j)] = if f <= center {
                (f - left) / (center - left)
            } else {
                (right - f) / (right - center)
            };
        }
    }
    fb
}

pub struct FeatureExtractor {
    fft: Arc<dyn Fft<f32>>,
    window: Vec<f32>,
    filterbank: Array2<f32>,
    frame_len: usize,
    frame_shift: usize,
}

impl FeatureExtractor {
    pub fn new(sr: u32) -> Self {
        let frame_len = (FRAME_LENGTH_MS * sr / 1000) as usize;
        let frame_shift = (FRAME_SHIFT_MS * sr / 1000) as usize;
        let mut planner = rustfft::FftPlanner::<f32>::new();
        let fft = planner.plan_fft_forward(N_FFT);
        FeatureExtractor {
            fft,
            window: hamming_window(frame_len),
            filterbank: mel_filterbank(N_MELS, N_FFT, sr),
            frame_len,
            frame_shift,
        }
    }

    /// WavFrontend:FFT → mel → log → CMVN → LFR 拼接。返回 (T, N_MELS*LFR_M)
    pub fn extract(&self, audio: &[f32]) -> Array2<f32> {
        let audio = preemphasis(audio, PREEMPH_COEF);
        let n = audio.len();
        let frame_len = self.frame_len;
        let frame_shift = self.frame_shift;

        let n_frames = if n < frame_len {
            1
        } else {
            (n - frame_len) / frame_shift + 1
        };

        // 分帧 + 加窗 + rFFT + 功率谱
        let n_bins = N_FFT / 2 + 1;
        let mut mel_log = Array2::<f32>::zeros((n_frames, N_MELS));
        let mut buf: Vec<Complex32> = vec![Complex32::new(0.0, 0.0); N_FFT];
        let mut scratch = vec![Complex32::new(0.0, 0.0); self.fft.get_inplace_scratch_len()];

        for t in 0..n_frames {
            let start = t * frame_shift;
            for b in buf.iter_mut() {
                *b = Complex32::new(0.0, 0.0);
            }
            for i in 0..frame_len {
                let s = if start + i < n { audio[start + i] } else { 0.0 };
                buf[i] = Complex32::new(s * self.window[i], 0.0);
            }
            let mut out: Vec<Complex32> = vec![Complex32::new(0.0, 0.0); N_FFT];
            self.fft
                .process_outofplace_with_scratch(&mut buf, &mut out, &mut scratch);
            let buf = &out;

            // 功率谱 → mel → log10
            for m in 0..N_MELS {
                let mut acc = 0.0f32;
                for j in 0..n_bins {
                    let w = self.filterbank[(m, j)];
                    if w == 0.0 {
                        continue;
                    }
                    let re = buf[j].re;
                    let im = buf[j].im;
                    acc += (re * re + im * im) * w;
                }
                mel_log[(t, m)] = acc.max(1e-10).log10();
            }
        }

        // CMVN(var norm)
        for m in 0..N_MELS {
            let mut mean = 0.0f32;
            for t in 0..n_frames {
                mean += mel_log[(t, m)];
            }
            mean /= n_frames as f32;
            let mut var = 0.0f32;
            for t in 0..n_frames {
                let d = mel_log[(t, m)] - mean;
                var += d * d;
            }
            let std = (var / n_frames as f32).sqrt() + 1e-5;
            for t in 0..n_frames {
                mel_log[(t, m)] = (mel_log[(t, m)] - mean) / std;
            }
        }

        // LFR 拼接
        let n_frames = if n_frames < LFR_M {
            // zero pad 到 LFR_M
            let mut padded = Array2::<f32>::zeros((LFR_M, N_MELS));
            padded
                .slice_mut(ndarray::s![0..n_frames, ..])
                .assign(&mel_log);
            mel_log = padded;
            LFR_M
        } else {
            n_frames
        };

        let n_lfr = (n_frames - LFR_M) / LFR_N + 1;
        let mut out = Array2::<f32>::zeros((n_lfr, N_MELS * LFR_M));
        for i in 0..n_lfr {
            let start = i * LFR_N;
            for k in 0..LFR_M {
                for m in 0..N_MELS {
                    out[(i, k * N_MELS + m)] = mel_log[(start + k, m)];
                }
            }
        }
        out
    }
}

// ============================================================
// CTC decoder
// ============================================================
pub fn greedy_decode(logits: &Array2<f32>, id_to_token: &[String], blank_id: i64) -> String {
    let (t_steps, vocab) = logits.dim();
    let mut result: Vec<i64> = Vec::new();
    let mut prev: i64 = -1;
    for t in 0..t_steps {
        let row = logits.row(t);
        let mut best = 0usize;
        let mut best_v = f32::NEG_INFINITY;
        for v in 0..vocab {
            let x = row[v];
            if x > best_v {
                best_v = x;
                best = v;
            }
        }
        let p = best as i64;
        if p != prev && p != blank_id {
            result.push(p);
        }
        prev = p;
    }

    let mut text = String::new();
    for &idx in &result {
        if idx < 0 || idx as usize >= id_to_token.len() {
            continue;
        }
        let tok = &id_to_token[idx as usize];
        if tok.starts_with("<|") && tok.ends_with("|>") {
            continue; // SenseVoice 特殊 token(语言/情感/itn 标记)
        }
        if tok == "▁" {
            text.push(' ');
        } else {
            text.push_str(&tok.replace('▁', " "));
        }
    }
    text.trim().to_string()
}

// ============================================================
// SenseVoice ASR 主类
// ============================================================
pub struct SenseVoiceASR {
    session: Session,
    id_to_token: Vec<String>,
    extractor: FeatureExtractor,
    pub model_dir: PathBuf,
}

impl SenseVoiceASR {
    /// 加载 model.onnx + tokens.json
    pub fn load(model_dir: impl AsRef<Path>) -> AsrResult<Self> {
        let dir = model_dir.as_ref().to_path_buf();
        let onnx = dir.join("model.onnx");
        let tokens = dir.join("tokens.json");
        if !onnx.exists() || !tokens.exists() {
            return Err(AsrError::ModelNotFound(dir));
        }

        let session = Session::builder()
            .map_err(AsrError::Ort)?
            .with_optimization_level(GraphOptimizationLevel::Level3)
            .map_err(|e| AsrError::Ort(e.into()))?
            .with_intra_threads(4)
            .map_err(|e| AsrError::Ort(e.into()))?
            .commit_from_file(&onnx)
            .map_err(|e| AsrError::Ort(e.into()))?;

        let tokens_vec: Vec<String> = serde_json::from_str(&std::fs::read_to_string(&tokens)?)?;

        Ok(SenseVoiceASR {
            session,
            id_to_token: tokens_vec,
            extractor: FeatureExtractor::new(SAMPLE_RATE),
            model_dir: dir,
        })
    }

    /// 16kHz mono f32 [-1,1] → 文本
    ///
    /// language: 0=auto 1=zh 2=en 3=ja 4=ko; textnorm: 14=with_itn(默认) 15=wo_itn
    pub fn transcribe_pcm(
        &mut self,
        pcm: &[f32],
        language: i32,
        textnorm: i32,
    ) -> AsrResult<String> {
        let feats = self.extractor.extract(pcm); // (T, 560)
        let t = feats.nrows();
        let feats3: Array3<f32> = feats.insert_axis(ndarray::Axis(0)); // (1, T, 560)

        let speech = Value::from_array(feats3)?;
        let speech_len = Value::from_array(ndarray::arr1(&[t as i32]))?;
        let lang = Value::from_array(ndarray::arr1(&[language]))?;
        let tn = Value::from_array(ndarray::arr1(&[textnorm]))?;

        let outputs = self.session.run(ort::inputs![
            "speech" => speech,
            "speech_lengths" => speech_len,
            "language" => lang,
            "textnorm" => tn,
        ])?;

        let (shape, data) = outputs[0].try_extract_tensor::<f32>()?;
        // (1, T, V) → (T, V)
        let t_out = shape[1] as usize;
        let v = shape[2] as usize;
        let logits = Array2::from_shape_vec((t_out, v), data.to_vec())
            .map_err(|e| AsrError::InvalidAudio(format!("logits reshape: {e}")))?;

        Ok(greedy_decode(&logits, &self.id_to_token, BLANK_ID))
    }

    /// 从 wav 文件识别(16kHz mono 16-bit)
    pub fn transcribe_wav(&mut self, wav_path: impl AsRef<Path>, language: i32, textnorm: i32) -> AsrResult<String> {
        let pcm = load_wav_f32(wav_path)?;
        self.transcribe_pcm(&pcm, language, textnorm)
    }
}

/// 读 16kHz mono 16-bit wav → f32 [-1,1]
pub fn load_wav_f32(path: impl AsRef<Path>) -> AsrResult<Vec<f32>> {
    let reader = hound::WavReader::open(path.as_ref())?;
    let spec = reader.spec();
    if spec.sample_rate != SAMPLE_RATE {
        return Err(AsrError::InvalidAudio(format!(
            "sample rate {} != {}",
            spec.sample_rate, SAMPLE_RATE
        )));
    }
    if spec.channels != 1 {
        return Err(AsrError::InvalidAudio("只支持 mono".into()));
    }
    let samples: Vec<f32> = match spec.sample_format {
        hound::SampleFormat::Int => reader
            .into_samples::<i16>()
            .map(|s| s.map(|v| v as f32 / 32768.0))
            .collect::<Result<_, _>>()?,
        hound::SampleFormat::Float => reader
            .into_samples::<f32>()
            .collect::<Result<_, _>>()?,
    };
    Ok(samples)
}

pub mod ffi;
