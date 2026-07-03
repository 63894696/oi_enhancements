"""
Cloud Adapter Base Interface(2026-07-03 修复重写)

修复的阻断缺陷:
1. 原版三个方法全是 @abstractmethod 而子类各只实现一个 → 所有子类都
   无法实例化。现在基类提供默认实现,子类只覆写自己支持的通道
   (ASR→stream_process,LLM/IM→send_request)。
2. 补上 ADR-002 承诺但缺失的两块地基:
   - F-5 自动降级:CloudRouter 按优先级尝试 adapter,失败切下一个,
     降级日志明示「FastLane 降级:A → B,原因:...」
   - F-6 网络策略:enforce_https 拒绝明文 HTTP;make_ssl_context 强制
     TLS 1.3 + 证书校验(禁 verify=False / 自签)
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Any, Dict, List, Optional


# ============================================================
# F-6:网络守卫
# ============================================================

def enforce_https(url: str) -> str:
    """拒绝任何明文 HTTP 端点(ADR-002 F-6)

    例外:loopback(127.0.0.1/localhost)允许 http —— F-6 管的是云端出网,
    本地兜底(ADR-001 daemon / Ollama)走 loopback 明文属于设计内。
    """
    lowered = url.lower()
    if lowered.startswith("https://"):
        return url
    if lowered.startswith("http://"):
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        if host in ("127.0.0.1", "localhost", "::1") or host.startswith("127."):
            return url
    raise ValueError(f"FastLane 强制 HTTPS,拒绝端点: {url}")


def make_ssl_context() -> ssl.SSLContext:
    """强制 TLS 1.3 最低版本 + 证书校验的客户端 SSL 上下文(ADR-002 F-6)"""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


# ============================================================
# 适配器基类
# ============================================================

class CloudAdapter:
    """云服务适配器基类

    提供统一的云服务调用接口。基类给出安全的默认行为,子类按需覆写:
    - health_check: 默认按 endpoint 配置返回状态
    - send_request: 默认 NotImplementedError(REST 型服务覆写)
    - stream_process: 默认 NotImplementedError(流式服务覆写)
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = config.get("name", self.__class__.__name__)
        self.logger = logging.getLogger(self.name)
        endpoint = config.get("endpoint", "")
        # 配置了 endpoint 就在构造期做 F-6 检查,fail-fast
        self.endpoint = enforce_https(endpoint) if endpoint else ""

    async def health_check(self) -> Dict[str, Any]:
        """检查服务健康状态(骨架阶段:按配置返回;真实现覆写为 GET /health)"""
        if not self.endpoint:
            return {"status": "not_configured", "adapter": self.name}
        return {"status": "ok", "adapter": self.name, "endpoint": self.endpoint}

    async def send_request(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """发送 REST 请求"""
        raise NotImplementedError(f"{self.name} 不支持 REST 调用")

    async def stream_process(self, data_stream) -> asyncio.Queue:
        """处理流式数据流"""
        raise NotImplementedError(f"{self.name} 不支持流式处理")


# ============================================================
# 三类客户端(骨架实现,真云接入时替换内部逻辑)
# ============================================================

class CloudASRClient(CloudAdapter):
    """云端 ASR 客户端:WebSocket 流式识别,实时转录

    2026-07-03 M-7:标记 @deprecated,新代码应走 CloudRouter(真降级链)
    """

    async def stream_process(self, data_stream) -> asyncio.Queue:
        """处理语音流式数据,返回转录结果队列"""
        queue: asyncio.Queue = asyncio.Queue()
        async for chunk in data_stream:
            await queue.put(chunk.get("text", ""))
        return queue

    async def transcribe_chunks(self, chunks: List[Dict[str, Any]]) -> List[str]:
        """REST 风格:一批 chunk → 文本列表(供 main.py /transcribe,可 JSON 序列化)

        2026-07-03 M-7:标 deprecated,新代码应走 asr_router.call_with_fallback
        """
        import warnings
        warnings.warn(
            "CloudASRClient.transcribe_chunks 是 @deprecated 骨架接口,新代码应走 CloudRouter。"
            "计划 v1.0 移除。",
            DeprecationWarning,
            stacklevel=2,
        )
        return [c.get("text", "") for c in chunks]


class CloudLLMClient(CloudAdapter):
    """云端 LLM 客户端:文本生成 / 对话

    2026-07-03 M-7:标记 @deprecated,新代码应走 llm_router.call_with_fallback
    """

    async def send_request(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        import warnings
        warnings.warn(
            "CloudLLMClient.send_request 是 @deprecated 骨架接口,新代码应走 llm_router.call_with_fallback。"
            "计划 v1.0 移除。",
            DeprecationWarning,
            stacklevel=2,
        )
        return {
            "status": "ok",
            "generated_text": payload.get("prompt", ""),
            "model": self.config.get("default_model", "qwen-max"),
        }


class CloudIMClient(CloudAdapter):
    """云端 IM 客户端:即时消息发送

    2026-07-03 H-3 修法:之前是纯 stub 假成功(`return {"status": "sent"}`)。
    现在接 ghostline 仓 SimpleXCLIAdapter 真实发 IM(走 simplex-chat CLI 单次模式)。
    配置来源:im.cli_path / im.smp_server / im.enabled(跟 ghostline 仓同字段名)
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._simplex = None  # 懒加载,首次 send 才实例化
        self._init_error: Optional[str] = None
        # 预读配置,如果 enabled=False 或 cli_path 空,记下原因供 health_check 报告
        self._enabled = bool(config.get("enabled", False))
        self._cli_path = config.get("cli_path", "").strip()
        self._smp_server = config.get("smp_server", "").strip()

    def _get_simplex(self):
        """懒加载 ghostline 仓 SimpleXCLIAdapter(跨仓 import)

        2026-07-03 H-3 修法:用 sys.path 注入 ghostline 仓 src/ + voice_input_ghostline.im.simplex 作为 module
        """
        if self._simplex is not None or self._init_error is not None:
            if self._init_error:
                raise RuntimeError(self._init_error)
            return self._simplex
        if not self._enabled:
            self._init_error = "IM 未启用(im.enabled=false)"
            raise RuntimeError(self._init_error)
        if not self._cli_path:
            self._init_error = "im.cli_path 未配置"
            raise RuntimeError(self._init_error)
        try:
            # 跨仓 import:把 ghostline 仓 src/ 加到 sys.path,然后 import 整个 voice_input_ghostline.im.simplex package
            import importlib
            import sys
            from pathlib import Path
            ghostline_src = Path("C:/Users/Administrator/voice_input_ghostline/src")
            if not (ghostline_src / "voice_input_ghostline").exists():
                self._init_error = f"ghostline 仓 src/voice_input_ghostline/ 不存在:{ghostline_src}"
                raise RuntimeError(self._init_error)
            ghostline_src_s = str(ghostline_src)
            if ghostline_src_s not in sys.path:
                sys.path.insert(0, ghostline_src_s)
            simplex_mod = importlib.import_module("voice_input_ghostline.im.simplex")
            self._simplex = simplex_mod.SimpleXCLIAdapter(self._cli_path)
            return self._simplex
        except Exception as e:
            self._init_error = f"SimpleXCLIAdapter 初始化失败:{e}"
            raise RuntimeError(self._init_error)

    async def health_check(self) -> Dict[str, Any]:
        """2026-07-03 H-3:真实探活(simplex /users),而不是 echo"""
        if not self._enabled:
            return {"status": "disabled", "adapter": self.name, "reason": "im.enabled=false"}
        if not self._cli_path:
            return {"status": "unavailable", "adapter": self.name, "reason": "im.cli_path 未配置"}
        try:
            simplex = self._get_simplex()
            available = simplex.is_available()
            return {
                "status": "ok" if available else "unavailable",
                "adapter": self.name,
                "backend": "simplex",
                "cli_path": self._cli_path,
                "reason": None if available else "simplex-chat 探活失败",
            }
        except RuntimeError as e:
            return {"status": "unavailable", "adapter": self.name, "reason": str(e)}

    async def send_request(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """2026-07-03 H-3:真发 IM(走 ghostline 仓 SimpleXCLIAdapter)

        payload 字段:text(str) + target(str,@联系人/#群)
        """
        text = payload.get("text", "").strip()
        target = payload.get("target", "").strip()
        if not text:
            return {"status": "fail", "error": "payload.text 不能为空"}
        if not target or target[0] not in ("@", "#"):
            return {"status": "fail", "error": f"payload.target 需以 @(联系人)或 #(群)开头:{target!r}"}
        try:
            simplex = self._get_simplex()
        except RuntimeError as e:
            return {"status": "fail", "error": str(e)}
        # SimpleXCLIAdapter.send 是同步方法,run_in_executor 异步化
        import asyncio
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, simplex.send, text, target)
        # result 形如 {"status": "ok"/"fail", "target": ..., "detail": ...}
        if result.get("status") == "ok":
            return {
                "status": "sent",
                "target": target,
                "backend": "simplex",
                "detail": result.get("detail"),
            }
        return {
            "status": "fail",
            "target": target,
            "error": result.get("error", "simplex 发送失败"),
        }


# ============================================================
# F-5:自动降级路由
# ============================================================

class CloudRouter:
    """按优先级依次尝试 adapter,失败自动切下一个(ADR-002 F-5)

    用法:
        router = CloudRouter([doubao_asr, qwen_asr, local_sensevoice])
        texts = await router.call_with_fallback("transcribe_chunks", chunks)
    """

    def __init__(self, adapters: List[CloudAdapter], logger: Optional[logging.Logger] = None):
        if not adapters:
            raise ValueError("CloudRouter 至少需要一个 adapter")
        self.adapters = adapters
        self.logger = logger or logging.getLogger("CloudRouter")

    async def call_with_fallback(self, method: str, *args, **kwargs) -> Any:
        last_err: Optional[Exception] = None
        for i, adapter in enumerate(self.adapters):
            try:
                return await getattr(adapter, method)(*args, **kwargs)
            except Exception as e:
                last_err = e
                nxt = self.adapters[i + 1].name if i + 1 < len(self.adapters) else "无(全部失败)"
                # F-5:降级时日志明示
                self.logger.warning(
                    f"FastLane 降级:{adapter.name} → {nxt},原因:{type(e).__name__}: {e}"
                )
        raise RuntimeError(f"FastLane 所有 adapter 均失败,最后错误: {last_err}") from last_err
