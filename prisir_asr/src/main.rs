//! CLI:prisir_asr <model_dir> <wav> [language] [textnorm]
//! 验证 P0:音频文件 → 文字(对标 Python sensevoice.py)

use prisir_asr::SenseVoiceASR;
use std::time::Instant;

fn init_ort() {
    // load-dynamic:指向 pip onnxruntime 的 capi 目录(onnxruntime.dll 所在)
    let dir = std::env::var("ORT_LIB_LOCATION").unwrap_or_else(|_| {
        // 默认:工程内 vendored ORT 1.29(与 ort rc.13 默认 api-27 匹配)
        let exe = std::env::current_exe().unwrap();
        exe.parent().unwrap().parent().unwrap().parent().unwrap()
            .join("ort_bin").to_string_lossy().into_owned()
    });
    ort::init_from(dir + "/onnxruntime.dll").expect("load onnxruntime.dll");
}

fn main() {
    init_ort();
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        eprintln!("Usage: prisir_asr <model_dir> <wav> [language=1] [textnorm=14]");
        std::process::exit(1);
    }
    let model_dir = &args[1];
    let wav = &args[2];
    let language: i32 = args.get(3).and_then(|s| s.parse().ok()).unwrap_or(1);
    let textnorm: i32 = args.get(4).and_then(|s| s.parse().ok()).unwrap_or(14);

    if std::env::var("DUMP_FEATS").is_ok() {
        let pcm = prisir_asr::load_wav_f32(wav).expect("read wav");
        let fe = prisir_asr::FeatureExtractor::new(prisir_asr::SAMPLE_RATE);
        let f = fe.extract(&pcm);
        eprintln!("RS feats shape {:?}", f.dim());
        let row0: Vec<String> = (0..8).map(|i| format!("{:.4}", f[(0, i)])).collect();
        eprintln!("RS f[0,:8] [{}]", row0.join(", "));
        return;
    }

    let t0 = Instant::now();
    let mut asr = match SenseVoiceASR::load(model_dir) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("加载模型失败: {e}");
            std::process::exit(1);
        }
    };
    eprintln!("✓ 模型加载: {} (用时 {:?})", asr.model_dir.display(), t0.elapsed());

    let pcm = match prisir_asr::load_wav_f32(wav) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("读 wav 失败: {e}");
            std::process::exit(1);
        }
    };
    eprintln!("✓ 音频: {} samples ({:.2}s)", pcm.len(), pcm.len() as f32 / 16000.0);

    let t1 = Instant::now();
    match asr.transcribe_pcm(&pcm, language, textnorm) {
        Ok(text) => {
            eprintln!("✓ 推理用时 {:?}", t1.elapsed());
            println!("{text}");
        }
        Err(e) => {
            eprintln!("识别失败: {e}");
            std::process::exit(1);
        }
    }
}
