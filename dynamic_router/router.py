"""
动态路由核心实现：将语音指令映射到相应的增强器模块
整合：PerceptionHub + IntentClassifier + DynamicRouter 三者协同

2026-07-03 恢复:本文件曾被 IM 客户端抽象代码整体覆盖,DynamicRouter 一度丢失。
IM 抽象已迁至 oi_enhancements/im_clients/,本文件恢复为语义路由器。
"""
import asyncio
import re
import sys
import time
import traceback
from typing import Callable, Dict, Awaitable, Optional, Any
from pathlib import Path
from datetime import datetime

# 延迟导入，避免循环依赖
def _lazy_import(module_name: str):
    """延迟导入模块，避免循环导入"""
    module = __import__(module_name, fromlist=[''])
    return module


def _oi_sm():
    """加载 oi_enhancements/shared_memory 包(绕开 vendor/peekaboo 同名模块遮蔽)。

    vision/desktop import 时会把 vendor/peekaboo 插到 sys.path[0],
    直接 `from shared_memory import store` 会命中 vendor 的裸类模块。
    """
    import importlib.util
    name = "oi_enh_shared_memory"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent.parent / "shared_memory" / "__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ==================== 路由映射表 ====================
# 关键词映射 → (处理函数, 参数提取器)
ROUTE_MAP: Dict[str, tuple] = {
    # 桌面操作类
    "聚焦窗口|切换窗口|鼠标": ("desktop", "window_ops"),
    "热键": ("desktop", "hotkey"),
    # 视觉类
    "截图|截屏": ("vision", "capture"),
    "窗口列表|前台窗口": ("vision", "window_info"),
    # 记忆类
    "存储记忆|记住|保存": ("memory", "store"),
    "回忆|查找|检索": ("memory", "retrieve"),
    "统计|多少条": ("memory", "stats"),
    # 默认
    "默认": (None, None),
}


# ==================== IntentClassifier ====================
class IntentClassifier:
    """意图分类器，基于关键词进行快速匹配"""
    def __init__(self):
        self.patterns = ROUTE_MAP

    def classify(self, instruction: str) -> tuple:
        """
        分类指令
        Returns: (module_type, handler_name, params)
        """
        instruction = instruction.strip().lower()
        for pattern, (mod, handler) in self.patterns.items():
            if re.search(pattern, instruction):
                return (mod, handler, self._extract_params(instruction))
        return (None, "default", {})

    def _extract_params(self, instruction: str) -> dict:
        """从指令中提取参数"""
        params = {}
        # 简单提取窗口标题
        for keyword in ["微信", "Claude", "team-web", "code", "记事本"]:
            if keyword in instruction:
                params["window_title"] = keyword
                break
        return params


# ==================== PerceptionHub 代理 ====================
class PerceptionHubProxy:
    """感知层代理，封装来自多个模组的当前状态"""
    def __init__(self, hub_name: str = "oi_perception"):
        self.hub_name = hub_name

    def get_latest_speech(self) -> str:
        """获取最近的语音识别结果(写端 schema: title="asr/final", tags=["asr","final"])"""
        try:
            recent = _oi_sm().retrieve("asr", layers=["L3"], limit=10, hub_name=self.hub_name)
            hits = [h for h in recent.get("hits", []) if len(h.get("content", "")) > 2]
            if hits:
                # peekaboo 按 access_count 排序,不保证时间序 → 自己按 created_at 取最新
                latest = max(hits, key=lambda h: h.get("created_at", ""))
                return latest["content"]
        except Exception:
            pass
        return ""

    def get_foreground_window(self) -> dict:
        """获取前台窗口信息"""
        try:
            vision_mod = _lazy_import("vision")
            return vision_mod.get_foreground_window()
        except Exception:
            return {"status": "error", "hwnd": None}

    def get_window_list(self) -> dict:
        """获取窗口列表"""
        try:
            vision_mod = _lazy_import("vision")
            return vision_mod.list_windows()
        except Exception:
            return {"status": "error", "count": 0}


# ==================== 动态路由器 ====================
class DynamicRouter:
    """
    核心职责：
    1. 接收用户指令
    2. 调用 IntentClassifier 解析语义
    3. 调用 PerceptionHub 获取上下文
    4. 分发到对应增强器模块
    5. 记录操作到 shared_memory (L3)
    """
    def __init__(self, hub_name: str = None):
        # time.time() 而非 asyncio.get_event_loop().time():
        # 后者在无事件循环的线程里会抛异常,且 3.12 起已弃用
        self.hub_name = hub_name or f"oi_dynamic_{int(time.time())}"
        self.classifier = IntentClassifier()
        self.perception = PerceptionHubProxy(self.hub_name)
        self._last_instruction: str = ""
        # 初始化记录
        self._log_action("router_init", "动态路由器已初始化")

    def _log_action(self, key: str, value: str, level: str = "L3"):
        """记录操作到 shared_memory"""
        try:
            _oi_sm().store(level, key, value, tags=["dynamic_router"], hub_name=self.hub_name)
        except Exception:
            pass

    async def route(self, instruction: str) -> str:
        """
        主入口：处理用户指令
        """
        self._last_instruction = instruction
        self._log_action("user_query", instruction)

        # 1. 语义解析
        module_type, handler_name, params = self.classifier.classify(instruction)

        if module_type is None:
            return self._handle_unknown(instruction)

        # 2. 根据模块类型调用对应处理器
        handlers = {
            "desktop": self._handle_desktop,
            "vision": self._handle_vision,
            "memory": self._handle_memory,
        }

        handler = handlers.get(module_type)
        if handler:
            return await handler(handler_name, params)

        return f"不可达的模块: {module_type}"

    async def _handle_desktop(self, handler_name: str, params: dict) -> str:
        """处理桌面操作指令"""
        try:
            desktop_mod = _lazy_import("desktop")

            if handler_name == "window_ops":
                window_title = params.get("window_title", "")
                if window_title:
                    result = desktop_mod.focus_window(window_title)
                    self._log_action("window_focus", f"聚焦 {window_title}")
                    return f"已聚焦窗口: {window_title} (status={result.get('status', 'unknown')})"
                result = desktop_mod.get_mouse_position()
                return f"鼠标位置: {result}"
            elif handler_name == "hotkey":
                result = desktop_mod.hotkey("ctrl", "escape")
                return f"热键执行: {result}"

            return f"未知的 desktop handler: {handler_name}"
        except Exception as e:
            return f"桌面操作错误: {str(e)}"

    async def _handle_vision(self, handler_name: str, params: dict) -> str:
        """处理视觉/窗口指令"""
        try:
            vision_mod = _lazy_import("vision")

            if handler_name == "capture":
                result = vision_mod.capture_screen(monitor_index=0, max_width=320)
                return f"截图成功 (base64 长度: {len(result.get('base64', ''))})"
            elif handler_name == "window_info":
                fg = vision_mod.get_foreground_window()
                win_list = vision_mod.list_windows()
                count = win_list.get("count", 0)
                return f"前台窗口: {fg.get('status')}, 共 {count} 个窗口"

            return f"未知的 vision handler: {handler_name}"
        except Exception as e:
            return f"视觉操作错误: {str(e)}"

    async def _handle_memory(self, handler_name: str, params: dict) -> str:
        """处理记忆操作指令"""
        try:
            if handler_name == "store":
                key, value = "test_store", "动态路由测试数据"
                _oi_sm().store("L3", key, value, hub_name=self.hub_name)
                self._log_action("memory_store", f"存储 {key}")
                return f"已存储 {key} 到 memory"
            elif handler_name == "retrieve":
                result = _oi_sm().retrieve("L3", hub_name=self.hub_name)
                count = result.get("count", 0)
                return f"检索到 {count} 条 L3 记录"
            elif handler_name == "stats":
                stats = _oi_sm().get_stats(hub_name=self.hub_name)
                total = stats.get("stats", {}).get("total_memories", 0)
                return f"memory 总计 {total} 条记录"

            return f"未知的 memory handler: {handler_name}"
        except Exception as e:
            return f"记忆操作错误: {str(e)}"

    def _handle_unknown(self, instruction: str) -> str:
        """处理未知指令"""
        return f"未识别指令: {instruction}，请重新表述"

    def get_last_instruction(self) -> str:
        return self._last_instruction


# ==================== 单例导出 ====================
router = DynamicRouter()
