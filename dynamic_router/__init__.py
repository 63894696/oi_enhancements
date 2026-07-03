"""OI 动态路由增强器 — 2026-07-02 ship

解决 voice_input 三 ADR 调研里的关键需求:
  "根据指令要求,调用子代从系统环境变量获取 KEY 使用其它模型平台,
   而不至于自己断开连接"

核心设计:
  1. LLM 在 chat 模式可调 interpreter.dynamic_route(task, platform_hint) → 子代调
  2. 子代读 Windows 注册表 env var(STEPFUN_API_KEY / BAILIAN_API_KEY / SILICONFLOW_API_KEY ...)
  3. 用 threading 异步调,不阻塞主 OI chat loop(避免 fake_success 现象)
  4. 失败自动试下一个平台(BAILIAN→SILICONFLOW→STEPFUN→ARK→EDGE 顺序)
  5. 不影响 LLM 主对话继续,后台异步执行

用法:
  from oi_enhancements.dynamic_router import install, route_task
  install(interpreter)  # 永久挂 5 工具到 interpreter

  # OI chat 模式 LLM 可在 code block 调:
  # interpreter.dynamic_route(task='翻译 Hello', platform_hint='SILICONFLOW')
  # 或:
  # interpreter.route_with_key(task='用 Qwen 总结文本', env_key='BAILIAN_API_KEY', base_url='https://dashscope...')
"""
from __future__ import annotations
import os
import sys
import json
import time
import threading
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, Future

import winreg


# ============================================================
# Windows 注册表读 env(统一入口)
# ============================================================
def _read_env(name: str) -> str:
    """从 Windows 注册表 HKEY_CURRENT_USER\Environment 读 env var(2026-07-02 ship)

    这样 LLM 子代可以"从系统环境变量获取 KEY",不需要 user 在 Python 进程里 export
    """
    try:
        reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
        val, _ = winreg.QueryValueEx(reg, name)
        winreg.CloseKey(reg)
        return val
    except FileNotFoundError:
        return ""


# ============================================================
# 平台配置(可路由的平台列表)
# ============================================================
PLATFORM_CONFIG = {
    "STEPFUN": {
        "base_url": "https://api.stepfun.com/v1",
        "api_key_env": "STEPFUN_API_KEY",
        "models": ["step-3.7-flash", "step-3.5-flash-2603", "step-r1.5"],
        "endpoint_type": "openai_compatible",
        "default_model": "step-3.7-flash",
        "rating": "★★★(推理强,中文 TTS)",
    },
    "BAILIAN": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "BAILIAN_API_KEY",
        "models": ["qwen3-coder-plus", "qwen3-max", "deepseek-v3.2", "qwen3-omni-flash"],
        "endpoint_type": "openai_compatible",
        "default_model": "qwen3-coder-plus",
        "rating": "★★★(中文 LLM 强,多模态)",
    },
    "SILICONFLOW": {
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key_env": "SILICONFLOW_API_KEY",
        "models": ["Qwen/Qwen2.5-7B-Instruct", "deepseek-ai/DeepSeek-V3.2", "Qwen/Qwen3.6-35B-A3B"],
        "endpoint_type": "openai_compatible",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
        "rating": "★★★(免费 + 大量模型)",
    },
    "ARK": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key_env": "ARK_API_KEY",
        "models": ["doubao-seed-1-6-flash-250715", "doubao-1-5-thinking-pro-250415"],
        "endpoint_type": "openai_compatible",
        "default_model": "doubao-seed-1-6-flash-250715",
        "rating": "★★(豆包 vision 强)",
    },
    "MINIMAX": {
        "base_url": "https://api.MiniMax.chat/v1",
        "api_key_env": "MINIMAX_API_KEY",
        "models": ["MiniMax-Text-01", "speech-2.6-hd"],
        "endpoint_type": "openai_compatible",
        "default_model": "MiniMax-Text-01",
        "rating": "★★(MiniMax 强)",
    },
    "ZAI": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZAI_API_KEY",
        "models": ["glm-4.6", "glm-5.2"],
        "endpoint_type": "openai_compatible",
        "default_model": "glm-4.6",
        "rating": "★★(GLM,代码强)",
    },
    "SENSENOVA": {
        "base_url": "https://token.sensenova.cn/v1",
        "api_key_env": "SENSENOVA_API_KEY",
        "models": ["sensenova-6.7-flash-lite", "deepseek-v4-flash"],
        "endpoint_type": "openai_compatible",
        "default_model": "sensenova-6.7-flash-lite",
        "rating": "★★(商汤)",
    },
}

# 全局 fallback 顺序(用户调 route_task 不指定平台时按这顺序试)
# BAILIAN 优先(中文 LLM 强),SILICONFLOW 第二(免费+无 rate limit),STEPFUN 第三(推理强但 RPM 10)
DEFAULT_FALLBACK_ORDER = ["BAILIAN", "SILICONFLOW", "STEPFUN", "ARK", "MINIMAX", "ZAI", "SENSENOVA"]


# ============================================================
# 核心:route_task 子代调(2026-07-02 ship)
# ============================================================
def _call_chat_completion(platform: str, messages: list, model: str = None,
                           max_tokens: int = 4000, temperature: float = 0.7,
                           timeout: int = 60) -> dict:
    """调某个平台的 chat completion endpoint(走 OpenAI 兼容协议)

    这是同步阻塞调,会被 route_task 包到线程里跑(不阻塞主 OI chat)
    """
    cfg = PLATFORM_CONFIG.get(platform)
    if not cfg:
        return {"status": "error", "platform": platform, "reason": f"unknown platform {platform}"}

    api_key = _read_env(cfg["api_key_env"])
    if not api_key:
        return {"status": "error", "platform": platform, "reason": f"{cfg['api_key_env']} not in env"}

    model = model or cfg["default_model"]
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    data = json.dumps(body).encode("utf-8")
    url = f"{cfg['base_url']}/chat/completions"
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }, method="POST")
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
        dt = int((time.time() - t0) * 1000)
        m = payload["choices"][0]["message"]
        return {
            "status": "ok",
            "platform": platform,
            "model": model,
            "content": m.get("content", ""),
            "latency_ms": dt,
        }
    except urllib.error.HTTPError as e:
        body_e = e.read().decode("utf-8", errors="ignore")[:200]
        return {"status": "error", "platform": platform, "model": model,
                "http_code": e.code, "reason": body_e}
    except Exception as e:
        return {"status": "error", "platform": platform, "model": model,
                "reason": f"{type(e).__name__}: {str(e)[:200]}"}


def route_task(task: str, messages: list = None, platform_hint: str = "auto",
               model_hint: str = None, max_tokens: int = 4000,
               temperature: float = 0.7, fallback: bool = True,
               timeout: int = 60) -> dict:
    """动态路由任务到指定平台(2026-07-02 ship,通用 KEY 切换)

    Args:
        task: 任务描述(供 logging 用)
        messages: OpenAI 格式 chat messages [{"role":..., "content":...}]
        platform_hint: 'auto' | 'STEPFUN' | 'BAILIAN' | ... | 'SILICONFLOW' | 'EDGE'
        model_hint: 可选,指定模型;不指定走平台 default
        fallback: 失败是否自动试下一个平台(默认 True)
        timeout: 单平台超时(秒)

    Returns:
        {"status":"ok"|"error", "platform":..., "model":..., "content":..., "latency_ms":..., ...}
    """
    if messages is None:
        messages = [{"role": "user", "content": task}]

    # 决定平台顺序
    if platform_hint == "auto":
        order = DEFAULT_FALLBACK_ORDER
    elif platform_hint.upper() in PLATFORM_CONFIG:
        order = [platform_hint.upper()]
    else:
        return {"status": "error", "reason": f"unknown platform {platform_hint}"}

    last_error = None
    for plat in order:
        # 读 env var 拿 key
        cfg = PLATFORM_CONFIG[plat]
        api_key = _read_env(cfg["api_key_env"])
        if not api_key:
            last_error = f"{cfg['api_key_env']} not in env"
            if not fallback:
                return {"status": "error", "platform": plat, "reason": last_error}
            continue  # 试下一个平台

        # 调
        result = _call_chat_completion(plat, messages, model=model_hint,
                                        max_tokens=max_tokens,
                                        temperature=temperature, timeout=timeout)
        if result.get("status") == "ok":
            return result
        last_error = result
        if not fallback:
            return result

    return {
        "status": "error",
        "reason": "all platforms failed",
        "last_error": last_error,
        "tried_platforms": order,
    }


# ============================================================
# 异步版:不阻塞主 OI chat loop(2026-07-02 ship)
# ============================================================
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dynamic_router")


def route_task_async(task: str, messages: list = None, platform_hint: str = "auto",
                     model_hint: str = None, max_tokens: int = 4000,
                     temperature: float = 0.7, fallback: bool = True,
                     callback=None) -> Future:
    """异步路由任务(返回 Future,不阻塞)

    用法:
        future = route_task_async(task='翻译 Hello', platform_hint='SILICONFLOW')
        # 主循环继续做别的事
        result = future.result(timeout=60)  # 需要时再阻塞拿结果
        # 或注册 callback:
        future.add_done_callback(lambda f: print(f.result()))
    """
    future = _executor.submit(
        route_task, task, messages, platform_hint, model_hint,
        max_tokens, temperature, fallback, 60,
    )
    if callback is not None:
        future.add_done_callback(lambda f: callback(f.result()))
    return future


# ============================================================
# 列出可用平台(给 LLM 用)
# ============================================================
def list_platforms() -> dict:
    """列出所有可用平台 + 状态(从 env var 检查 KEY 是否存在)

    Returns:
        {
          "platforms": [
            {"name": "BAILIAN", "has_key": True, "models": [...], "rating": "..."},
            ...
          ],
          "fallback_order": [...],
          "default_platform": "BAILIAN"
        }
    """
    platforms = []
    for name, cfg in PLATFORM_CONFIG.items():
        has_key = bool(_read_env(cfg["api_key_env"]))
        platforms.append({
            "name": name,
            "api_key_env": cfg["api_key_env"],
            "has_key": has_key,
            "default_model": cfg["default_model"],
            "models": cfg["models"],
            "rating": cfg["rating"],
        })
    return {
        "platforms": platforms,
        "fallback_order": DEFAULT_FALLBACK_ORDER,
        "default_platform": next((p["name"] for p in platforms if p["has_key"]), None),
        "available_count": sum(1 for p in platforms if p["has_key"]),
        "total_count": len(platforms),
    }


# ============================================================
# OI installer
# ============================================================
DYNAMIC_ROUTER_SYSTEM_MESSAGE = """

# Dynamic Router (动态平台路由 — 2026-07-02 ship)

你可以通过 **dynamic_router** 在多个 LLM 平台间动态切换,而不需要重启 OI 或换 env。

调 `interpreter.dynamic_route(...)` 子代调,主 OI chat 不阻塞,失败自动 fallback。

## 7 个可用平台(从 env 读 KEY,无需重启)
- BAILIAN(默认,中文 LLM 强,qwen3-coder-plus / qwen3-max / deepseek-v3.2)
- SILICONFLOW(免费,Qwen2.5-7B / DeepSeek-V3.2 / Qwen3.6-35B-A3B)
- STEPFUN(step-3.7-flash,推理强)
- ARK(豆包 doubao-seed-1-6-flash)
- MINIMAX(MiniMax-Text-01)
- ZAI(glm-4.6,代码强)
- SENSENOVA(商汤 sensenova-6.7-flash-lite)

## 调用模式(在 ```python 代码块里)
```python
# 同步(阻塞拿结果):
result = interpreter.dynamic_route(task='用 Qwen 总结', platform_hint='SILICONFLOW')
print(result.get('content', ''))

# 异步(不阻塞主循环,后台跑):
future = interpreter.dynamic_route_async(task='翻译 Hello', platform_hint='BAILIAN')
# 主循环可继续做别的事
result = future.result(timeout=60)

# 列可用平台(从 env 读 KEY):
interpreter.dynamic_platforms()
```

## 关键原则
1. **不要重启 OI 来换平台** — 用 dynamic_route 即可
2. **不要自己 pip install** openai / requests / httpx — urllib 已内置
3. **不要 import** openai / requests 之类的库
4. **不要手动从 env 读 KEY** — dynamic_route 自己读,失败自动 fallback
5. 默认 fallback 顺序: BAILIAN → SILICONFLOW → STEPFUN → ARK → MINIMAX → ZAI → SENSENOVA
6. 异步版用 `dynamic_route_async`,主 OI chat 不会卡
"""


def install(interpreter) -> dict:
    # 1) 拼 system_message
    if "Dynamic Router (动态平台路由" not in (interpreter.system_message or ""):
        interpreter.system_message = (interpreter.system_message or "") + DYNAMIC_ROUTER_SYSTEM_MESSAGE

    # 2) 挂 4 个工具到 interpreter 类
    cls = type(interpreter)

    def _dynamic_route(self, task, platform_hint="auto", model_hint=None,
                       messages=None, max_tokens=4000, temperature=0.7,
                       fallback=True):
        return route_task(task, messages, platform_hint, model_hint,
                          max_tokens, temperature, fallback, 60)

    def _dynamic_route_async(self, task, platform_hint="auto", model_hint=None,
                             messages=None, callback=None):
        return route_task_async(task, messages, platform_hint, model_hint,
                                4000, 0.7, True, callback)

    def _dynamic_platforms(self):
        return list_platforms()

    def _dynamic_executor(self):
        return _executor

    cls.dynamic_route = _dynamic_route
    cls.dynamic_route_async = _dynamic_route_async
    cls.dynamic_platforms = _dynamic_platforms
    cls.dynamic_executor = _dynamic_executor

    return {
        "status": "ok",
        "tools": ["dynamic_route", "dynamic_route_async", "dynamic_platforms", "dynamic_executor"],
        "platforms_count": len(PLATFORM_CONFIG),
        "fallback_order": DEFAULT_FALLBACK_ORDER,
        "description": f"dynamic_router ship 了 4 个工具,7 平台可路由,失败自动 fallback",
    }