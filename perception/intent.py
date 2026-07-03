"""
感知层子模块：语义意图解析
基于关键词和上下文快照进行简单意图识别
"""
import re
from typing import Tuple, Optional


# 定义意图模板：关键词 -> 意图标签
INTENT_PATTERNS = {
    # 窗口操作意图
    r"聚焦.*窗口|切换.*窗口": "window_focus",
    r"截图|截屏|截个图": "screen_capture",
    r"鼠标.*位置|鼠标移到": "mouse_move",
    
    # 记忆操作意图
    r"存储|记住|保存": "memory_store",
    r"回忆|查找|搜索": "memory_retrieve",
    r"统计|多少条|记录数": "memory_stats",
    
    # 综合意图
    r"什么.*窗口|当前.*窗口": "context_query",
    r"刚才.*说|语音.*内容": "speech_query",
    r"帮助|怎么|怎么做|如何": "help_request",
}


class IntentClassifier:
    """
    基于规则的简单意图分类器
    后续可升级为 ML 分类器
    """
    def __init__(self):
        pass

    def classify(self, instruction: str) -> Tuple[str, Optional[dict]]:
        """
        解析指令文本，返回意图标签和参数
        Returns:
            (intent_label, params_dict)
        """
        instruction = instruction.strip().lower()
        
        for pattern, intent in INTENT_PATTERNS.items():
            match = re.search(pattern, instruction)
            if match:
                # 提取参数（实际使用时可优化）
                params = self._extract_params(instruction, intent)
                return (intent, params)
        
        return ("unknown", {})

    def _extract_params(self, instruction: str, intent: str) -> dict:
        """
        从指令中提取参数
        """
        params = {}
        
        if intent == "window_focus":
            # 尝试提取窗口标题
            match = re.search(r"(?:窗口|窗口)(.+?)(?:请|帮|给|把)", instruction)
            if not match:
                match = re.search(r"(?:聚焦|切换)(.+?)$", instruction)
            if match:
                params["window_title"] = match.group(1).strip()
        
        elif intent == "screen_capture":
            # 提取截图参数
            match = re.search(r"(\d+)", instruction)
            if match:
                params["monitor_index"] = int(match.group(1))
        
        elif intent == "memory_retrieve":
            # 提取查询关键词
            match = re.search(r"(?:查找|搜索|回忆)(.+?)$", instruction)
            if match:
                params["query"] = match.group(1).strip()
        
        elif intent == "memory_store":
            # 提取存储内容
            match = re.search(r"(?:存储|记住|保存)(.+?)$", instruction)
            if match:
                params["content"] = match.group(1).strip()
        
        return params


# 单例导出
classifier = IntentClassifier()