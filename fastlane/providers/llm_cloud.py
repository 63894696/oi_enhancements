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
        # 2026-07-03 H-7:传 endpoint 让 tls13_client 按 host 策略(SiliconFlow 走 TLS 1.2)
        async with tls13_client(timeout_s=self.config.get("timeout_s", 90), endpoint=self.endpoint) as client:
            r = await client.post(self.endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"{self.name} 响应解析失败:{str(data)[:200]}") from e
        # 2026-07-03 H-8:云端 LLM 空 content 必须抛错,让 CloudRouter 降级
        if not text or not text.strip():
            raise RuntimeError(f"{self.name} 空 content(响应:{str(data)[:200]})")
        return text


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
    """本地 llama.cpp server 兜底(F-5:所有云 LLM 失败 → 本地;loopback http 例外)

    2026-07-12 替换: Ollama → llama.cpp mtmd (MiniCPM-V-4.6 Q4_K_M)
    - 本地: 127.0.0.1:8099 (本地 llama_local_server.py)
    - VPS:  https://llama.dreamproject.qzz.io (nginx 反代 127.0.0.1:8099)
    - API 兼容 OpenAI /v1/chat/completions 格式
    - 推理速度 ~18 tok/s (CPU, 2 threads)
    - 无 VPN 时用 VPS 域名;有 VPN 时可切本地

    2026-08-02 注意: 类名 `OllamaLocalLLMAdapter` 是历史命名,实际指向 llama-server
    (MiniCPM-V),非 Ollama。新代码请用 factory.py 中注册的 display name "llama-server"。
    后续可考虑重命名为 `LlamaServerAdapter`(破坏性变更,需同步更新 import)。
    """

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = dict(config or {})
        cfg.setdefault("name", "llama-server-local")
        # 2026-07-12: 默认 VPS 公网域名 (无 VPN 也能用)
        # 有 VPN 时可配置 base_url="http://127.0.0.1:8099" 走本地
        base = cfg.get("base_url", "https://llama.dreamproject.qzz.io").rstrip("/")
        cfg.setdefault("endpoint", f"{base}/v1/chat/completions")
        super().__init__(cfg)
        self.model = cfg.get("model", "MiniCPM-V-4.6")

    async def generate(self, prompt: Messages, **kwargs: Any) -> str:
        import httpx

        payload = {
            "model": self.model,
            "messages": _as_messages(prompt),
            "stream": False,
            "max_tokens": kwargs.get("max_tokens", 512),
            "temperature": kwargs.get("temperature", 0.7),
        }
        async with httpx.AsyncClient(timeout=self.config.get("timeout_s", 120)) as client:
            r = await client.post(self.endpoint, json=payload)
            r.raise_for_status()
            data = r.json()
        # 2026-08-02 修法: MiniCPM-V 的回复有时在 content,有时在 reasoning_content
        # VPS llama-server 与 llama_local_server.py 格式已统一,都把 reasoning_content
        # 放在 message 内,无需两层 fallback。统一逻辑:先 content,空则 reasoning_content。
        try:
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError(f"{self.name} 空 choices,响应:{str(data)[:200]}")
            msg = choices[0].get("message", {})
            text = msg.get("content", "") or msg.get("reasoning_content", "")
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"{self.name} 响应解析失败:{str(data)[:200]}"
            ) from e
        if not text or not text.strip():
            raise RuntimeError(
                f"{self.name} 空结果(content='', reasoning_content=''),响应:{str(data)[:200]}"
            )
        return text
