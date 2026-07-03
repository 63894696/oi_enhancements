"""把 OI audio 增强器真正装到 OI 0.4.3 的 OpenInterpreter 单例上

OI 0.4.3 没有原生 tool_manager API,实际模式:
  1. 动态给 OpenInterpreter 类加方法(monkey-patch)
  2. 把方法的描述符加到 system_message 的 # tools 区
  3. chat() 时 LLM 可以通过 code 块直接调这些方法

跑法:
  python install_to_oi.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 加 audio_voice_eval/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 加 oi_enhancements/

# 1) 导入 audio 增强器(直接 import 模块,绕过 oi_enhancements package 路径)
import importlib.util
_audio_spec = importlib.util.spec_from_file_location(
    "audio_enhancer",
    Path(__file__).resolve().parent.parent / "audio" / "__init__.py"
)
audio = importlib.util.module_from_spec(_audio_spec)
_audio_spec.loader.exec_module(audio)
print(f"✓ 导入 audio 增强器 (PLATFORM_MATRIX {len(audio.PLATFORM_MATRIX)} 平台)")

# 2) 触发 OI 0.4.3 单例
from interpreter import interpreter
_ = interpreter.computer  # 触发 init
print(f"✓ 触发 OI 单例: {type(interpreter).__name__}")

# 3) 调 audio.install() — 2026-07-02 升级版:永久 system_message 补丁 + 5 工具挂载
result = audio.install(interpreter)
print(f"✓ audio.install() status: {result['status']}")
print(f"  工具: {result['installed_tools']}")
print(f"  永久 system_message 补丁: {result['system_message_patch_file']}")
print(f"  system_message 含 Audio Tools: {'Audio Tools' in (interpreter.system_message or '')}")
print(f"  interpreter.audio_asr callable: {callable(getattr(interpreter, 'audio_asr', None))}")
print(f"  interpreter.audio_tts callable: {callable(getattr(interpreter, 'audio_tts', None))}")
print(f"  interpreter.audio_translate callable: {callable(getattr(interpreter, 'audio_translate', None))}")

# 4) 保存 install log
INSTALL_LOG = Path.home() / ".oi" / "audio_enhancer_install.json"
INSTALL_LOG.parent.mkdir(parents=True, exist_ok=True)
import json
from datetime import datetime
INSTALL_LOG.write_text(json.dumps({
    "timestamp": datetime.now().isoformat(),
    "oi_module": str(type(interpreter).__module__),
    "install_method": "audio.install() 2026-07-02 upgrade",
    "tools_added": result["installed_tools"],
    "system_message_patch_file": result["system_message_patch_file"],
    "system_message_chars": len(interpreter.system_message or ""),
    "status": "ok",
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n→ Install log: {INSTALL_LOG}")
print(f"\n→ 装好了!现在 OI 默认 LLM 都知道 audio 工具(永久补丁)")
print(f"→ 可以 `interpreter.audio_asr(...)` / `.audio_tts(...)` / `.audio_chat(...)` / `.audio_translate(...)` / `.audio_platforms()`")
