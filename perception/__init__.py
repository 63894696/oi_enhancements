"""
Perception Hub: 感知层实现(2026-07-03 P0 修复 import)

将 vision / desktop / memory / shared_memory 的输出聚合为统一的上下文快照,
供 DynamicRouter 做语义决策。

speech 数据流:voice_input ASR final → shared_memory L3(P1 hook 落地后自动写入),
本模块只读 shared_memory,不直接持有 voice_input daemon 连接。
需要直连 daemon 时用 get_voice_client()(懒加载,daemon 未启动时返回 None)。
"""
import importlib.util
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

# oi_enhancements 根目录(vision / desktop / memory / shared_memory 都是顶层模块)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from vision import list_windows, get_foreground_window, capture_screen
from desktop import get_mouse_position
from memory.oi_memory import OIMemory


def _load_oi_shared_memory():
    """显式按文件路径加载 oi_enhancements/shared_memory 包。

    不能直接 `import shared_memory`:vision/desktop 等模块 import 时会把
    vendor/peekaboo 插到 sys.path[0],vendor 里的 shared_memory.py(裸
    SharedMemory 类)会遮蔽我们的同名包。与 test_full_suite 的处理一致。
    """
    name = "oi_enh_shared_memory"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, _ROOT / "shared_memory" / "__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_sm = _load_oi_shared_memory()
store, retrieve, get_by_agent = _sm.store, _sm.retrieve, _sm.get_by_agent


def get_voice_client():
    """按需创建 VoiceInputClient(3 daemon 未启动 / auth_token 缺失时返回 None)"""
    repo = _ROOT / "voice_input"
    for p in (str(repo / "src"), str(repo / "clients")):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        from oi_client import VoiceInputClient
        return VoiceInputClient()
    except Exception:
        return None


class PerceptionHub:
    """
    统一的环境感知入口
    职责:
    1. 收集来自多模块的上下文数据
    2. 构建结构化的「环境快照」
    3. 提供给 DynamicRouter 做语义决策
    """
    def __init__(self, hub_name: Optional[str] = None):
        # 默认用 shared_memory 的公共 hub,保证能读到 voice_input 等其他模块写入的数据
        self.hub_name = hub_name or "oi_hub"
        self._memory = OIMemory()

    def take_snapshot(self) -> Dict[str, Any]:
        """同步拍摄当前环境状态"""
        return {
            "timestamp": datetime.now().isoformat(),
            "speech": self._get_latest_speech(),   # 语音转录
            "screen": self._get_screen_info(),     # 屏幕状态
            "desktop": self._get_desktop_info(),   # 桌面状态
            "memory": self._get_memory_context(),  # 记忆上下文
        }

    async def take_snapshot_async(self) -> Dict[str, Any]:
        """异步版本,防止阻塞事件循环"""
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.take_snapshot)

    def _get_latest_speech(self) -> Dict[str, Any]:
        """获取最新的语音识别结果(shared_memory L3,由 asr_memory_writer 写入)

        写端 schema:title="asr/final",tags=["asr","final"] → query 用 "asr" 才能命中。
        peekaboo retrieve 按 access_count 优先排序,不保证时间序,这里自己按
        created_at 取最新一条。
        """
        try:
            recent = retrieve("asr", layers=["L3"], limit=10, hub_name=self.hub_name)
            hits = [h for h in recent.get("hits", []) if h.get("content")]
            if hits:
                latest = max(hits, key=lambda h: h.get("created_at", ""))
                return {
                    "text": latest["content"],
                    "source": "asr_final",
                    "created_at": latest.get("created_at", ""),
                }
        except Exception:
            pass
        return {"text": "", "source": "empty"}

    def _get_screen_info(self) -> Dict[str, Any]:
        """获取屏幕和窗口信息"""
        info = {
            "foreground": get_foreground_window(),
            "window_list": list_windows(),
        }
        try:
            cap = capture_screen(monitor_index=0, max_width=320)
            # 快照里只留 base64 前 200 字符做指纹,全图由调用方按需取
            info["screenshot"] = cap.get("base64", "")[:200]
        except Exception:
            info["screenshot"] = ""
        return info

    def _get_desktop_info(self) -> Dict[str, Any]:
        """获取桌面状态(鼠标位置等)

        注:desktop.inspect_window(title) 需要窗口标题且要遍历 UIA 树,开销大,
        不进默认快照;调用方需要 UI 树时按需自己调。
        """
        return {"mouse_position": get_mouse_position()}

    def _get_memory_context(self, query: str = "") -> Dict[str, Any]:
        """获取记忆上下文,可选查询关键字"""
        if query:
            return retrieve(query, hub_name=self.hub_name)
        return get_by_agent("oi", limit=5, hub_name=self.hub_name)

    def log_to_memory(self, level: str, key: str, value: str, tags: list = None):
        """将感知数据记录到 memory 层"""
        store(level, key, value, tags=tags or [], hub_name=self.hub_name)


# 全局单例,便于其他模块引用
hub_instance = PerceptionHub()
