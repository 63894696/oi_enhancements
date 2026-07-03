"""F-2:云端 LLM provider(OpenAI 兼容协议 × N 家 + 本地 Ollama 兜底)

统一契约:async generate(prompt|messages) -> str
一个 OpenAICompatLLM 类覆盖 Qwen(DashScope)/ DeepSeek(SiliconFlow)/
StepFun / OpenRouter —— 它们全是 OpenAI Chat Completions 协议,只差
base_url + key + model。
"""
from __future__ import annotations

from typing import Any, Dict, List, Union

from ..adapters import CloudAdapter
from .base import require_env, tls13_client

Messages = Union[str, List[Dict[str, str]]]


def _as_messages(prompt: Messages) -> List[Dict[str, str]]:
    if isinstance(prompt, str):
        return [{"role": "user", "content": prompt}]
    return list(prompt)


class OpenAICompatLLM(CloudAdapter):
    """OpenAI Chat Completions 兼容客户端(所有主流国产云都支持)"""

    def __init__(self, config: Dict[str, Any]):
        cfg = dict(config)
        base_url = cfg.get("base_url", "").rstrip("/")
        cfg.setdefault("endpoint", f"{base_url}/chat/completions")
        super().__init__(cfg)
        self.base_url = base_url
        self.model = cfg["model"]
        env_keys = cfg.get("env_keys") or ()
        self.api_key = cfg.get("api_key") or require_env(*env_keys)
        self.temperature = cfg.get("temperature", 0.7)

    async def generate(self, prompt: Messages, **kwargs: Any) -> str:
        payload = {
            "model": self.model,
            "messages": _as_messages(prompt),
            "temperature": kwargs.get("temperature", self.temperature),
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with tls13_client(timeout_s=self.config.get("timeout_s", 90)) as client:
            r = await client.post(self.endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"{self.name} 响应解析失败:{str(data)[:200]}") from e


def qwen_llm(**overrides: Any) -> OpenAICompatLLM:
    """Qwen-Max via DashScope 兼容模式(BAILIAN_API_KEY)"""
    cfg = {
        "name": "qwen-max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
        "env_keys": ("BAILIAN_API_KEY", "DASHSCOPE_API_KEY"),
    }
    cfg.update(overrides)
    return OpenAICompatLLM(cfg)


def deepseek_llm(**overrides: Any) -> OpenAICompatLLM:
    """DeepSeek-V3.2 via SiliconFlow(SILICONFLOW_API_KEY)"""
    cfg = {
        "name": "deepseek-v3.2",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3.2",
        "env_keys": ("SILICONFLOW_API_KEY",),
    }
    cfg.update(overrides)
    return OpenAICompatLLM(cfg)


def stepfun_llm(**overrides: Any) -> OpenAICompatLLM:
    """StepFun(STEPFUN_API_KEY)"""
    cfg = {
        "name": "stepfun",
        "base_url": "https://api.stepfun.com/v1",
        "model": "step-2-mini",
        "env_keys": ("STEPFUN_API_KEY",),
    }
    cfg.update(overrides)
    return OpenAICompatLLM(cfg)


class OllamaLocalLLMAdapter(CloudAdapter):
    """本地 Ollama 兜底(F-5:所有云 LLM 失败 → 本地;loopback http 例外)"""

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = dict(config or {})
        cfg.setdefault("name", "ollama-local")
        base = cfg.get("base_url", "http://127.0.0.1:11434").rstrip("/")
        cfg.setdefault("endpoint", f"{base}/api/chat")
        super().__init__(cfg)
        self.model = cfg.get("model", "qwen2.5:7b-instruct-q4_K_M")

    async def generate(self, prompt: Messages, **kwargs: Any) -> str:
        import httpx

        payload = {"model": self.model, "messages": _as_messages(prompt), "stream": False}
        async with httpx.AsyncClient(timeout=self.config.get("timeout_s", 120)) as client:
            r = await client.post(self.endpoint, json=payload)
            r.raise_for_status()
            data = r.json()
        # 2026-07-03 H-4 修法:本地兜底空字符串必须抛错(让 CloudRouter 降级)
        # 之前 r.json().get("message", {}).get("content", "") 空结果被当成功
        text = (data.get("message") or {}).get("content", "")
        if not text:
            raise RuntimeError(
                f"本地 Ollama LLM 空结果(content=''),响应:{str(data)[:200]}"
            )
        return text
