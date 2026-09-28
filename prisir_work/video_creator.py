"""prisir_work/video_creator.py — 视频创作能力门面(P3j T11)。

定位:F2「视频创作」门面 — 与 publisher 平级,PrisirAI 主对话 / 扩展 / shell 在不知道
Easel 内部 SKILL 编排的前提下发起「做一条视频」「给这段文字配音」「给这段音频出字幕」请求。

形态对齐:
  · web_search.register_provider   — 多个 provider 并存,降级而非崩溃
  · publisher.register_publisher   — 同上,平级模块
  · video_creator.register_creator  — 同样范式,creator 失败 → ok=False + reason

Creator 列表(Easel 端 subprocess 桥接,2026-09-25 ship):
  · TtsCreator      — 文字 → 语音(edge-tts / 闭源 CosyVoice2);自动读 .env VOICE_PROVIDER
  · AsrCreator      — 音频/视频 → 字幕(faster-whisper);中文默认 base 模型
  · AssembleCreator — storyboard JSON → 终片 mp4(ffmpeg 合成)
  · ImageGenCreator — text → image(SILICONFLOW_API_KEY)
  · VideoGenCreator — text → video(SILICONFLOW_API_KEY)
  · Orchestrator    — 主题 → 终片(分镜→配图→配音→字幕→BGM→合成) 一键编排

环境约定(对齐 SKILL.md 规范):
  · Easel 根 = EASEL_ROOT / settings.json / 候选路径(复用 easel_bridge.find_easel_root)
  · API key = 读 ~/work/zju_easel/.env(SKILL.md 明确禁止用 env/printenv 推断)
  · 子进程 cwd 落 Easel 根(脚本多用相对路径)

降级范式:任何 creator 未就绪 / 关键脚本缺失 / 子进程失败 → 返 ok=False + reason,绝不抛栈。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

__all__ = [
    "VideoCreateResult",
    "VideoCreator",
    "TtsCreator",
    "AsrCreator",
    "AssembleCreator",
    "ImageGenCreator",
    "VideoGenCreator",
    "Orchestrator",
    "VideoOpsCreator",
    "SubtitleOpsCreator",
    "PublishAnalyticsCreator",
    "register_creator",
    "create",
    "list_creators",
]


# ---------------------------------------------------------------------------
# .env 注入(对齐 SKILL.md 规范:只读 ~/work/zju_easel/.env)
# ---------------------------------------------------------------------------

def _load_easel_env() -> dict[str, str]:
    """读 Easel 项目根 .env(优先级最高);缺则返空 dict。

    不读 ~/.prisIrai/.env,不读 HKCU,不读 process env(避免污染)— 用户在
    Easel .env 里配 SILICONFLOW_API_KEY / VOICE_PROVIDER 等即可。
    """
    from .easel_bridge import find_easel_root
    root = find_easel_root()
    if not root:
        return {}
    env_path = root / ".env"
    if not env_path.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                out[k] = v
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------------------
# 结果 dataclass
# ---------------------------------------------------------------------------

@dataclass
class VideoCreateResult:
    """统一 creator 结果。"""
    ok: bool
    creator: str = ""
    artifact: dict[str, Any] = field(default_factory=dict)
    # artifact 字段约定:
    #   audio_path / subtitle_path / video_path / image_paths[] / storyboard_path
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "creator": self.creator,
            "artifact": self.artifact, "error": self.error,
            "raw": self.raw,
        }


# ---------------------------------------------------------------------------
# VideoCreator 协议
# ---------------------------------------------------------------------------

class VideoCreator:
    """creator 协议。name / ready / create 是必需的。"""
    name: str = ""
    title: str = ""

    @property
    def ready(self) -> bool: ...

    def status(self) -> dict[str, Any]: ...

    def create(self, **kwargs: Any) -> VideoCreateResult: ...


# ---------------------------------------------------------------------------
# 内部 subprocess 工具
# ---------------------------------------------------------------------------

def _run_creator_subprocess(script_rel: str, args: list[str],
                            *, timeout: int = 120,
                            cwd_root: Optional[Path] = None,
                            env_extra: Optional[dict[str, str]] = None,
                            input_text: Optional[str] = None) -> VideoCreateResult:
    """跑 Easel 端 creator 脚本,统一返 VideoCreateResult。"""
    from .easel_bridge import find_easel_root
    root = cwd_root or find_easel_root()
    if not root:
        return VideoCreateResult(ok=False, error="Easel 根目录未找到")
    script = root / script_rel
    if not script.is_file():
        return VideoCreateResult(ok=False, error=f"creator 脚本缺失: {script_rel}")
    env = os.environ.copy()
    # Easel .env 注入(优先级高于现有 env,但仍允许 OS env 覆盖)
    easel_env = _load_easel_env()
    env.update(easel_env)
    if env_extra:
        env.update(env_extra)
    try:
        r = subprocess.run(
            [sys.executable, str(script), *args],
            input=input_text, capture_output=True, text=True,
            timeout=timeout, cwd=str(root),
        )
    except subprocess.TimeoutExpired:
        return VideoCreateResult(ok=False, error=f"timeout {timeout}s")
    except Exception as e:  # noqa: BLE001
        return VideoCreateResult(ok=False, error=f"{type(e).__name__}: {e}")
    parsed: dict[str, Any] = {}
    try:
        for line in reversed((r.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                parsed = json.loads(line)
                break
    except Exception:
        parsed = {}
    return VideoCreateResult(
        ok=(r.returncode == 0),
        raw={
            "rc": r.returncode,
            "stderr_tail": (r.stderr or "")[-500:],
            "stdout_tail": (r.stdout or "")[-500:],
            "parsed": parsed,
        },
        error="" if r.returncode == 0 else f"rc={r.returncode}",
    )


# ---------------------------------------------------------------------------
# 5 个具体 Creator
# ---------------------------------------------------------------------------

class TtsCreator:
    """文字 → 语音(edge-tts / 闭源 CosyVoice2)。
    CLI: skills/shared/scripts/tts.py speak --text/--file → --output
    """
    name = "tts"
    title = "文字转语音(TTS)"

    @property
    def ready(self) -> bool:
        from .easel_bridge import find_easel_root
        root = find_easel_root()
        return bool(root and (root / "skills/shared/scripts/tts.py").is_file())

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "engine": "auto (优先闭源 VOICE_PROVIDER,否则 edge)",
            "default_voice": "zh-CN-YunxiNeural (edge) / alex (closed)",
        }

    def create(self, *, text: str = "", file: str = "",
               output: str, voice: str = "", rate: str = "",
               engine: str = "auto", subtitle: str = "",
               fmt: str = "auto") -> VideoCreateResult:
        if not text and not file:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error="text 或 file 至少一个必填")
        if not Path(file).is_file() if file else False:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error=f"输入文件不存在: {file}")
        # 父目录自动建
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        args = ["speak"]
        if text:
            args += ["--text", text]
        if file:
            args += ["--file", file]
        args += ["--output", output]
        if voice:
            args += ["--voice", voice]
        if engine and engine != "auto":
            args += ["--engine", engine]
        if rate:
            args += ["--rate", rate]
        if subtitle:
            args += ["--subtitle", subtitle]
        if fmt and fmt != "auto":
            args += ["--format", fmt]
        r = _run_creator_subprocess("skills/shared/scripts/tts.py", args,
                                    timeout=180)
        r.creator = self.name
        if r.ok:
            r.artifact = {"audio_path": output}
            if subtitle:
                r.artifact["subtitle_path"] = subtitle
        return r


class AsrCreator:
    """音频/视频 → 字幕(faster-whisper)。
    CLI: skills/shared/scripts/asr.py transcribe -i input -o output
    """
    name = "asr"
    title = "语音转字幕(ASR)"

    @property
    def ready(self) -> bool:
        from .easel_bridge import find_easel_root
        root = find_easel_root()
        return bool(root and (root / "skills/shared/scripts/asr.py").is_file())

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "default_model": "base (中文 OK,首次约 150MB)",
            "models": ["tiny", "base", "small", "medium", "large-v3"],
        }

    def create(self, *, input: str, output: str = "",
               fmt: str = "srt", model: str = "base",
               language: str = "", device: str = "cpu",
               compute_type: str = "int8",
               res: str = "1080x1920") -> VideoCreateResult:
        if not Path(input).is_file():
            return VideoCreateResult(ok=False, creator=self.name,
                                     error=f"输入不存在: {input}")
        # output 缺省 → 自动生成
        if not output:
            in_path = Path(input)
            out_dir = in_path.parent
            output = str(out_dir / f"{in_path.stem}.{fmt}")
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        args = ["transcribe", "--input", input, "--output", output,
                "--format", fmt, "--model", model,
                "--device", device, "--compute-type", compute_type,
                "--res", res]
        if language:
            args += ["--language", language]
        # large 模型首次需下 3GB → 给 30min timeout
        to = 1800 if model in ("medium", "large", "large-v3") else 300
        r = _run_creator_subprocess("skills/shared/scripts/asr.py", args,
                                    timeout=to)
        r.creator = self.name
        if r.ok:
            r.artifact = {"subtitle_path": output, "model": model}
        return r


class AssembleCreator:
    """storyboard JSON → 终片 mp4(ffmpeg)。
    CLI: skills/openclaw/auto-short-video/scripts/assemble.py assemble
    """
    name = "assemble"
    title = "分镜合成终片(assemble)"

    @property
    def ready(self) -> bool:
        from .easel_bridge import find_easel_root
        root = find_easel_root()
        if not root:
            return False
        return (root / "skills/openclaw/auto-short-video/scripts/assemble.py").is_file()

    def status(self) -> dict[str, Any]:
        return {"ready": self.ready, "default_size": "1080x1920 (9:16)"}

    def create(self, *, storyboard: str, output: str,
               pad_mode: str = "auto") -> VideoCreateResult:
        if not Path(storyboard).is_file() and storyboard != "-":
            return VideoCreateResult(ok=False, creator=self.name,
                                     error=f"storyboard 不存在: {storyboard}")
        # output 必须位于 outputs/<主题>/ — 自动适配
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        args = ["assemble", "--storyboard", storyboard, "--output", output,
                "--pad-mode", pad_mode]
        r = _run_creator_subprocess(
            "skills/openclaw/auto-short-video/scripts/assemble.py",
            args, timeout=900)  # 合成最长 15min
        r.creator = self.name
        if r.ok:
            r.artifact = {"video_path": output}
        return r


class ImageGenCreator:
    """text → image(SILICONFLOW_API_KEY)。
    CLI: skills/shared/scripts/ai_image.py
    """
    name = "image-gen"
    title = "AI 配图(text2img)"

    @property
    def ready(self) -> bool:
        from .easel_bridge import find_easel_root
        root = find_easel_root()
        if not root or not (root / "skills/shared/scripts/ai_image.py").is_file():
            return False
        # 还需要 SILICONFLOW_API_KEY
        return bool(_load_easel_env().get("SILICONFLOW_API_KEY"))

    def status(self) -> dict[str, Any]:
        env = _load_easel_env()
        return {
            "ready": self.ready,
            "has_siliconflow_key": bool(env.get("SILICONFLOW_API_KEY")),
            "hint": "在 ~/work/zju_easel/.env 配 SILICONFLOW_API_KEY=...",
        }

    def create(self, *, prompt: str, output: str, size: str = "1024x1024",
               count: int = 1) -> VideoCreateResult:
        if not prompt:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error="prompt 必填")
        if not self.ready:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error="Easel 未装 或 SILICONFLOW_API_KEY 未配置")
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        args = ["--prompt", prompt, "--output", output,
                "--size", size, "--count", str(count)]
        r = _run_creator_subprocess("skills/shared/scripts/ai_image.py",
                                    args, timeout=120)
        r.creator = self.name
        if r.ok:
            r.artifact = {"image_paths": [output]}
        return r


class VideoGenCreator:
    """text → video(SILICONFLOW_API_KEY)。
    CLI: skills/shared/scripts/ai_video.py
    """
    name = "video-gen"
    title = "AI 视频片段(text2video)"

    @property
    def ready(self) -> bool:
        from .easel_bridge import find_easel_root
        root = find_easel_root()
        if not root or not (root / "skills/shared/scripts/ai_video.py").is_file():
            return False
        return bool(_load_easel_env().get("SILICONFLOW_API_KEY"))

    def status(self) -> dict[str, Any]:
        env = _load_easel_env()
        return {
            "ready": self.ready,
            "has_siliconflow_key": bool(env.get("SILICONFLOW_API_KEY")),
            "hint": "视频生成比配图贵 — 默认 1 段",
        }

    def create(self, *, prompt: str, output: str, duration: int = 4) -> VideoCreateResult:
        if not prompt:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error="prompt 必填")
        if not self.ready:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error="Easel 未装 或 SILICONFLOW_API_KEY 未配置")
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        args = ["--prompt", prompt, "--output", output,
                "--duration", str(duration)]
        r = _run_creator_subprocess("skills/shared/scripts/ai_video.py",
                                    args, timeout=600)  # 视频生成慢
        r.creator = self.name
        if r.ok:
            r.artifact = {"video_clip_paths": [output]}
        return r


# ---------------------------------------------------------------------------
# 高阶编排:主题 → 终片
# ---------------------------------------------------------------------------

class Orchestrator:
    """主题 → 终片 一键编排。

    流程(对应 auto-short-video SKILL):
      1. video-script 生成口播文案 → 多句分镜
      2. (可选)image-gen 逐句配图 → outputs/<主题>/assets/shot{N}.png
      3. tts 配音 → outputs/<主题>/assets/narration.mp3
      4. asr 反向出字幕 → outputs/<主题>/assets/subtitle.srt
                          或用 tts --subtitle 直接出
      5. (可选)ai-music 选 BGM → outputs/<主题>/assets/bgm.mp3
      6. assemble 合成 → outputs/<主题>/final.mp4

    编排边界:
      · 不替你写文案(那是 LLM 的活);接受你已经写好的 script
      · 真要跑全链 — 需要 SILICONFLOW_API_KEY + edge-tts 联网(外网代理)
      · 默认走「最小链」:script 已经传 → tts → assemble(无字幕无图无 BGM,纯文字视频)

    对齐 SKILL.md「画幅确认硬门」:aspect_ratio + duration 必填。
    """
    name = "orchestrate"
    title = "主题→终片一键编排"

    @property
    def ready(self) -> bool:
        from .easel_bridge import find_easel_root
        root = find_easel_root()
        if not root:
            return False
        # 至少 tts + assemble 在;脚本生成由 caller 喂
        return (
            (root / "skills/shared/scripts/tts.py").is_file()
            and (root / "skills/openclaw/auto-short-video/scripts/assemble.py").is_file())

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "stages": ["script(input)", "image-gen(opt)", "tts",
                       "subtitle", "bgm(opt)", "assemble"],
            "default_chain": "script(input) → tts → assemble",
        }

    def create(self, *, topic: str, output_dir: str,
               script: str = "", aspect_ratio: str = "9:16",
               duration: int = 60, voice: str = "",
               with_subtitle: bool = True,
               with_images: bool = False) -> VideoCreateResult:
        """编排入口。

        Args:
          topic:       视频主题(用户话)— 决定输出目录名
          output_dir:  输出根目录(默认 ~/work/zju_easel/outputs/<topic>/)
          script:      已写好的口播文案(必填,否则返失败)
          aspect_ratio: 9:16 / 16:9 / 1:1
          duration:    目标时长(秒)
          voice:       TTS 音色(edge 音色名 或 闭源 voice-id)
          with_subtitle: 出 SRT 并烧录
          with_images: 是否走 AI 配图(需 SILICONFLOW_API_KEY)
        """
        if not topic or not script:
            return VideoCreateResult(
                ok=False, creator=self.name,
                error="topic 和 script 必填(script 由 LLM 生成后传入)")
        if not self.ready:
            return VideoCreateResult(
                ok=False, creator=self.name,
                error="Easel 未装 或 tts/assemble 脚本缺失")

        # 准备目录
        out_root = Path(output_dir).expanduser().resolve()
        assets = out_root / "assets"
        assets.mkdir(parents=True, exist_ok=True)

        size_map = {"9:16": (1080, 1920), "16:9": (1920, 1080),
                    "1:1": (1080, 1080)}
        if aspect_ratio not in size_map:
            return VideoCreateResult(
                ok=False, creator=self.name,
                error=f"aspect_ratio 必须是 9:16/16:9/1:1,当前 {aspect_ratio}")
        w, h = size_map[aspect_ratio]

        # 写文案到文件(tts 接受 --file 长文本推荐)
        script_path = assets / "script.txt"
        script_path.write_text(script, encoding="utf-8")

        narration_path = assets / "narration.mp3"
        subtitle_path = assets / "subtitle.srt" if with_subtitle else ""
        final_path = out_root / "final.mp4"

        stages_done: list[str] = []
        stages_failed: list[str] = []

        # Stage 1: tts 配音
        tts_args = {
            "file": str(script_path), "output": str(narration_path),
            "voice": voice or "",
        }
        if with_subtitle:
            tts_args["subtitle"] = subtitle_path
        tts_r = TtsCreator().create(**tts_args)
        if not tts_r.ok:
            stages_failed.append(f"tts: {tts_r.error}")
            return VideoCreateResult(
                ok=False, creator=self.name,
                error=f"Stage tts 失败:{tts_r.error}",
                artifact={"script_path": str(script_path),
                           "output_dir": str(out_root)})
        stages_done.append("tts")

        # Stage 2: (可选)配图 — 这里只搭骨架,真发留给 SKILL.md 编排层
        # 视频自动化是 LLM + agent 的活,creator 只暴露零件。

        # Stage 3: 构造 storyboard JSON(目前只有 1 shot — 用 narration 总时长)
        # 拿 narration 时长(ffprobe 简单)
        try:
            from .easel_bridge import easel as _easel
            eb = _easel()
            if eb.ready:
                # 复用 easel_bridge 已有的 ffprobe(暂时直接调)
                import subprocess
                probe = subprocess.run(
                    ["ffprobe", "-v", "error",
                     "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1",
                     str(narration_path)],
                    capture_output=True, text=True, timeout=10,
                )
                dur = float((probe.stdout or "60").strip() or "60")
            else:
                dur = float(duration)
        except Exception:
            dur = float(duration)

        # 1-shot storyboard:一张占位图(或纯背景)+ narration 总时长
        # 不强求 image-gen — 用一个空白 placeholder 也可以(assemble 内部处理)
        sb = {
            "size": f"{w}x{h}",
            "image_motion": "ken-burns",
            "shots": [
                {"image": str(assets / "placeholder.png"),
                 "duration": round(dur, 1),
                 "caption": topic},
            ],
            "narration": str(narration_path),
        }
        if with_subtitle and subtitle_path and Path(subtitle_path).is_file():
            sb["subtitle"] = subtitle_path
        sb_path = out_root / "storyboard.json"
        sb_path.write_text(json.dumps(sb, ensure_ascii=False, indent=2),
                          encoding="utf-8")

        # 占位 placeholder:用 PIL 画一个纯色图,assemble 兜底
        try:
            from PIL import Image
            img = Image.new("RGB", (w, h), color=(20, 20, 28))
            img.save(assets / "placeholder.png")
        except Exception:
            # PIL 缺失 — assemble 仍会报图缺失;不强依赖
            pass

        # Stage 4: assemble
        asm_r = AssembleCreator().create(storyboard=str(sb_path),
                                          output=str(final_path))
        if not asm_r.ok:
            stages_failed.append(f"assemble: {asm_r.error}")
            return VideoCreateResult(
                ok=False, creator=self.name,
                error=f"Stage assemble 失败:{asm_r.error}",
                artifact={"script_path": str(script_path),
                           "narration_path": str(narration_path),
                           "storyboard_path": str(sb_path),
                           "output_dir": str(out_root),
                           "stages_done": stages_done})
        stages_done.append("assemble")

        return VideoCreateResult(
            ok=True, creator=self.name,
            artifact={
                "video_path": str(final_path),
                "script_path": str(script_path),
                "narration_path": str(narration_path),
                "subtitle_path": str(subtitle_path) if with_subtitle else "",
                "storyboard_path": str(sb_path),
                "output_dir": str(out_root),
                "stages_done": stages_done,
                "aspect_ratio": aspect_ratio,
                "duration_sec": round(dur, 1),
            })


# ---------------------------------------------------------------------------
# P3j T12-A:扩展 creator — video_ops / subtitle_ops / publish_analytics
# ---------------------------------------------------------------------------
# 全部走 Easel 端确定性脚本(无 LLM 依赖,无需付费 key — video_ops 只需 ffmpeg,
# subtitle_ops 同样,publish_analytics 读本地缓存).失败统一 ok=False + reason.

class VideoOpsCreator:
    """通用视频处理(ffmpeg/ffprobe 封装)。

    子命令(对应 skills/shared/scripts/video_ops.py):
      · cut / concat / speed / silence-cut / mute-cut
      · text / aspect / frame / gif / compress
      · bgm / watermark / info

    通过 op= 参数选子命令;其余 kwargs 透明转发给对应子命令的 --xxx 参数。
    通用约定:所有子命令接受 -i INPUT / -o OUTPUT;只有 info 不要 -o。

    artifact 字段:
      · output_path   — 产物路径(cut/concat/.../watermark 都返)
      · info          — info 子命令返 ffprobe JSON(其他子命令为空)
    """
    name = "video-ops"
    title = "视频处理(cut/concat/bgm/watermark/aspect/info …)"

    # 子命令 → 输入输出参数约定
    _SUB_COMMANDS_WITH_IO = {
        "cut", "concat", "speed", "silence-cut", "mute-cut", "text",
        "aspect", "frame", "gif", "compress", "bgm", "watermark",
    }

    @property
    def ready(self) -> bool:
        from .easel_bridge import find_easel_root
        root = find_easel_root()
        if not root:
            return False
        return (root / "skills/shared/scripts/video_ops.py").is_file()

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "ops": sorted(self._SUB_COMMANDS_WITH_IO | {"info"}),
            "requires_ffmpeg": True,
        }

    # 创建时只暴露 input/output;其他 kwargs 直接转发 — 走 create_op
    def create(self, *, op: str, input: str = "",
               output: str = "", **opts: Any) -> VideoCreateResult:
        if not op:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error="op 必填(cut/concat/aspect/info …)")
        if op not in self._SUB_COMMANDS_WITH_IO and op != "info":
            return VideoCreateResult(
                ok=False, creator=self.name,
                error=f"未知 op: {op}(支持: "
                      f"{sorted(self._SUB_COMMANDS_WITH_IO | {'info'})})")
        if not input:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error="input 必填(视频文件路径)")
        if not Path(input).is_file():
            return VideoCreateResult(ok=False, creator=self.name,
                                     error=f"输入视频不存在: {input}")
        if op != "info" and not output:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error=f"op={op} 必填 output(产物路径)")
        if not self.ready:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error="Easel video_ops.py 缺失")

        args = [op, "-i", input]
        if op != "info":
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            args += ["-o", output]
        # opts 透明转发 — 短横线转长横线(下划线→横线,常见 CLI 习惯)
        for k, v in opts.items():
            if v is None or v == "" or v is False:
                continue
            flag = "--" + k.replace("_", "-")
            if isinstance(v, bool):
                if v:  # 只在 True 时加(空 flag)
                    args.append(flag)
            else:
                args += [flag, str(v)]

        # 渲染/转码耗时可观 → 默认 30min
        to = 1800
        if op == "info":
            to = 30  # ffprobe 极快
        elif op == "frame":
            to = 60  # 单帧抽取
        elif op == "gif":
            to = 600  # GIF 编码慢

        r = _run_creator_subprocess(
            "skills/shared/scripts/video_ops.py", args, timeout=to)
        r.creator = self.name
        if r.ok:
            r.artifact = {"output_path": output} if op != "info" else {}
            if op == "info":
                # info 走 stdout JSON 行;parsed 已存入 raw["parsed"]
                r.artifact["info"] = r.raw.get("parsed", {})
        return r


class SubtitleOpsCreator:
    """字幕解析 / 合并 / 烧录(确定性;翻译由 LLM 完成)。

    子命令(对应 skills/shared/scripts/subtitle_ops.py):
      · parse    — 解析 srt/vtt/ass → JSON
      · extract  — 提取待译文本(供 LLM)
      · merge    — 原文字幕 + 译文 → 双语字幕
      · build    — 从 JSON 构建字幕
      · convert  — 字幕格式互转
      · burn     — 字幕烧录进视频

    artifact 字段:
      · subtitle_path — 输出字幕路径(parse/extract/merge/build/convert)
      · video_path    — 烧录后视频路径(burn)
    """
    name = "subtitle-ops"
    title = "字幕处理(parse/merge/build/convert/burn)"

    _SUBS_WITH_OUTPUT = {"parse", "extract", "merge", "build", "convert"}
    _SUBS_NO_INPUT_FILE = set()  # 都需要输入字幕
    _SUBS_WITH_VIDEO = {"burn"}

    @property
    def ready(self) -> bool:
        from .easel_bridge import find_easel_root
        root = find_easel_root()
        if not root:
            return False
        return (root / "skills/shared/scripts/subtitle_ops.py").is_file()

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "ops": ["parse", "extract", "merge", "build", "convert", "burn"],
            "note": "翻译需 LLM;creator 只负责确定性文本与烧录",
        }

    def create(self, *, op: str, input: str = "",
               sub: str = "", output: str = "",
               **opts: Any) -> VideoCreateResult:
        if not op:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error="op 必填(parse/merge/build/convert/burn …)")
        all_ops = {"parse", "extract", "merge", "build", "convert", "burn"}
        if op not in all_ops:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error=f"未知 op: {op}(支持: {sorted(all_ops)})")
        if not self.ready:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error="Easel subtitle_ops.py 缺失")

        args: list[str] = [op]

        # 参数路由
        if op == "burn":
            # burn 用 -i 视频 --sub 字幕 -o 输出
            if not input or not Path(input).is_file():
                return VideoCreateResult(ok=False, creator=self.name,
                                         error=f"burn.input 视频不存在: {input}")
            if not sub or not Path(sub).is_file():
                return VideoCreateResult(ok=False, creator=self.name,
                                         error=f"burn.sub 字幕不存在: {sub}")
            if not output:
                return VideoCreateResult(ok=False, creator=self.name,
                                         error="burn.output 必填")
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            args += ["-i", input, "--sub", sub, "-o", output]
        else:
            # 其他 op 都是字幕文件 in → 字幕 out
            if not input or not Path(input).is_file():
                return VideoCreateResult(ok=False, creator=self.name,
                                         error=f"{op}.input 字幕不存在: {input}")
            if not output:
                return VideoCreateResult(ok=False, creator=self.name,
                                         error=f"{op}.output 必填")
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            args += ["-i", input, "-o", output]

        # opts 转发
        for k, v in opts.items():
            if v is None or v == "" or v is False:
                continue
            flag = "--" + k.replace("_", "-")
            if isinstance(v, bool):
                if v:
                    args.append(flag)
            else:
                args += [flag, str(v)]

        to = 600 if op == "burn" else 120
        r = _run_creator_subprocess(
            "skills/shared/scripts/subtitle_ops.py", args, timeout=to)
        r.creator = self.name
        if r.ok:
            if op == "burn":
                r.artifact = {"video_path": output}
            else:
                r.artifact = {"subtitle_path": output}
        return r


class PublishAnalyticsCreator:
    """发布数据归因分析(确定性计算,不联网,读本地数据)。

    子命令(对应 skill-publish-analytics/scripts/analyze.py):
      · time    — 最佳发布时段
      · tags    — 标签效果
      · types   — 内容类型对比
      · growth  — 增长归因
      · all     — A+B+C 默认组合

    数据源(从 web_search.register_provider 同源):
      · --data        公众号 wx jsonl 数据(从 weixin_mp_stats.py 拉的本地缓存)
      · --follower-log 粉丝增长日志(可选)
      · --profile     按 profile 字段过滤(可选)

    artifact 字段:
      · report        — 解析后的 JSON 报告
      · report_path   — 报告落盘路径(如有)
    """
    name = "publish-analytics"
    title = "发布数据分析(time/tags/types/growth)"

    _MODES = {"time", "tags", "types", "growth", "all", "selftest"}

    @property
    def ready(self) -> bool:
        from .easel_bridge import find_easel_root
        root = find_easel_root()
        if not root:
            return False
        return (root / "skills/openclaw/skill-publish-analytics/scripts/analyze.py").is_file()

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "modes": sorted(self._MODES),
            "data_source": "weixin_mp_stats 本地缓存 或 --data 自定路径",
            "selftest_supported": True,
        }

    def create(self, *, mode: str = "all", data: str = "",
               follower_log: str = "", profile: str = "",
               output: str = "") -> VideoCreateResult:
        if mode not in self._MODES:
            return VideoCreateResult(
                ok=False, creator=self.name,
                error=f"未知 mode: {mode}(支持: {sorted(self._MODES)})")
        if not self.ready:
            return VideoCreateResult(ok=False, creator=self.name,
                                     error="Easel publish-analytics analyze.py 缺失")

        args = [mode]
        if data:
            if not Path(data).is_file():
                return VideoCreateResult(
                    ok=False, creator=self.name,
                    error=f"data 文件不存在: {data}")
            args += ["--data", data]
        if follower_log:
            if not Path(follower_log).is_file():
                return VideoCreateResult(
                    ok=False, creator=self.name,
                    error=f"follower_log 不存在: {follower_log}")
            args += ["--follower-log", follower_log]
        if profile:
            args += ["--profile", profile]

        # selftest 是自检模式,不依赖外部数据 — 永远可跑
        to = 30 if mode == "selftest" else 300
        r = _run_creator_subprocess(
            "skills/openclaw/skill-publish-analytics/scripts/analyze.py",
            args, timeout=to)
        r.creator = self.name
        if r.ok:
            r.artifact = {"report": r.raw.get("parsed", {}),
                          "mode": mode}
            if output:
                # 把 stdout 报告落盘
                try:
                    Path(output).parent.mkdir(parents=True, exist_ok=True)
                    Path(output).write_text(
                        r.raw.get("stdout_tail", ""), encoding="utf-8")
                    r.artifact["report_path"] = output
                except Exception as e:  # noqa: BLE001
                    # 落盘失败不阻塞主流程
                    r.artifact["report_save_error"] = str(e)
        return r


# ---------------------------------------------------------------------------
# 注册表 + 路由
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, VideoCreator] = {}


def register_creator(c: VideoCreator) -> None:
    _REGISTRY[c.name] = c


def get(name: str) -> VideoCreator | None:
    return _REGISTRY.get(name)


def list_creators() -> list[dict[str, Any]]:
    return [{"name": c.name, "title": c.title, "ready": bool(c.ready)}
            for c in _REGISTRY.values()]


def create(creator_name: str, **kwargs: Any) -> VideoCreateResult:
    """按 creator 名路由;失败/不在 → 返 ok=False + reason。"""
    c = _REGISTRY.get(creator_name)
    if c is None:
        return VideoCreateResult(
            ok=False, creator=creator_name,
            error=f"未知 creator: {creator_name}(已注册: "
                  f"{[x.name for x in _REGISTRY.values()]})")
    return c.create(**kwargs)


def _register_defaults() -> None:
    register_creator(TtsCreator())
    register_creator(AsrCreator())
    register_creator(AssembleCreator())
    register_creator(ImageGenCreator())
    register_creator(VideoGenCreator())
    register_creator(Orchestrator())
    # P3j T12-A:扩展 creator(3 个)
    register_creator(VideoOpsCreator())
    register_creator(SubtitleOpsCreator())
    register_creator(PublishAnalyticsCreator())


# ════════════════════════════════════════════════════════════════════════════
# P10 OM-P2:Provider 7 维度自动选最优(可选 hook,creator 内部可调)
# ════════════════════════════════════════════════════════════════════════════

def pick_provider_for_creator(tag: str, context: dict[str, Any] | None = None,
                              candidates: list[str] | None = None) -> Optional[str]:
    """为某类 creator(tts/image2video/music/stock_video)选最优 provider 名。

    用法:TtsCreator 内部需要选 piper / edge_tts / elevenlabs / cosyvoice2 之一时,
        调用 pick_provider_for_creator("tts", context={"budget_remaining": 0.5})。
    全 fail 返 None,creator 走自己的 fallback。

    接受 [[prisIr-phase-10-om-p2-scoring]]。
    """
    try:
        from .video_provider_scoring import pick_best
    except ImportError:
        return None
    ps = pick_best(providers=candidates, tag=tag, context=context)
    return ps.name if ps else None


_register_defaults()