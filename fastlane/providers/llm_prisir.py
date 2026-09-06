"""llm_prisir.py — Prisir 多平台密钥管理 + 智能路由(无账号定位)

用户自填模型平台 key(OpenAI / Anthropic / 自定义 OpenAI 兼容端点),
密钥只存本地 SQLite(无账号、无云同步),按任务类型智能路由到最合适模型。

设计锚点(用户原话):
  "他们两者都不支持第三方key,我们要引导用户自行填入模型平台key,
   实现路由自动分任务调用模型"

- PrisirKeyStore: SQLite 本地密钥库(可选手工口令加密;默认本地明文+权限位,
  与 Chromium Web Data 同级,后续接 DPAPI)
- PrisirRouter: 任务分类(代码/创意/快速/长上下文) → 选模型
  路由策略: smart(智能) / openai / anthropic / local(本地优先) / 指定模型
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import require_env, tls13_client

Messages = List[Dict[str, str]]

_DEFAULT_DB = Path(os.environ.get(
    "PRISIR_KEY_DB",
    str(Path.home() / ".local" / "share" / "prisir" / "keys.db"),
))


# ============================================================
# 密钥库
# ============================================================
class PrisirKeyStore:
    """本地 SQLite 密钥库: 每平台一行 {platform, api_key, base_url, model, meta, updated}"""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or _DEFAULT_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS platform_keys(
                    platform TEXT PRIMARY KEY,
                    api_key TEXT NOT NULL DEFAULT '',
                    base_url TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    meta TEXT NOT NULL DEFAULT '{}',
                    updated INTEGER NOT NULL DEFAULT 0
                )"""
            )

    def set_key(self, platform: str, api_key: str, base_url: str = "",
                model: str = "", meta: Optional[dict] = None) -> None:
        with sqlite3.connect(self.db_path) as c:
            c.execute(
                """INSERT INTO platform_keys(platform, api_key, base_url, model, meta, updated)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(platform) DO UPDATE SET
                     api_key=excluded.api_key, base_url=excluded.base_url,
                     model=excluded.model, meta=excluded.meta, updated=excluded.updated""",
                (platform, api_key, base_url, model,
                 json.dumps(meta or {}, ensure_ascii=False), int(time.time())),
            )

    def get_key(self, platform: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as c:
            row = c.execute(
                "SELECT platform, api_key, base_url, model, meta, updated FROM platform_keys WHERE platform=?",
                (platform,),
            ).fetchone()
        if not row:
            return None
        return {
            "platform": row[0], "api_key": row[1], "base_url": row[2],
            "model": row[3], "meta": json.loads(row[4] or "{}"), "updated": row[5],
        }

    def list_platforms(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as c:
            rows = c.execute(
                "SELECT platform, api_key, base_url, model, meta, updated FROM platform_keys ORDER BY platform"
            ).fetchall()
        out = []
        for r in rows:
            key = r[1] or ""
            out.append({
                "platform": r[0],
                "has_key": bool(key),
                "key_hint": (key[:7] + "…" + key[-4:]) if len(key) > 11 else ("***" if key else ""),
                "base_url": r[2], "model": r[3],
                "meta": json.loads(r[4] or "{}"), "updated": r[5],
            })
        return out

    def delete_key(self, platform: str) -> None:
        with sqlite3.connect(self.db_path) as c:
            c.execute("DELETE FROM platform_keys WHERE platform=?", (platform,))


# ============================================================
# 任务分类 → 路由
# ============================================================
_CODE_HINTS = re.compile(
    r"(```|def |class |import |function|代码|编程|debug|报错|bug|报错|编译|算法|python|javascript|rust|c\+\+|sql|api|脚本)", re.I)
_LONG_HINT = 3000  # 字符数阈值,超过视为长上下文
_FAST_HINTS = re.compile(r"(是什么|什么意思|翻译|天气|计算|多少|定义|who is|what is|translate)", re.I)


def classify_task(text: str) -> str:
    """粗分类: code / creative / long / fast / general"""
    t = text or ""
    if len(t) > _LONG_HINT:
        return "long"
    if _CODE_HINTS.search(t):
        return "code"
    if _FAST_HINTS.search(t) and len(t) < 200:
        return "fast"
    return "general"


# 各任务类型的平台偏好序(用户可覆盖)
_TASK_PREFERENCE: Dict[str, List[str]] = {
    "code": ["openai", "anthropic", "custom"],
    "creative": ["anthropic", "openai", "custom"],
    "general": ["anthropic", "openai", "custom"],
    "fast": ["openai", "custom", "anthropic"],
    "long": ["anthropic", "openai", "custom"],
}


# ============================================================
# 路由器
# ============================================================
class PrisirRouter:
    """Prisir 智能路由: 按策略 + 任务类型选平台, 调用对应 LLM。"""

    def __init__(self, store: Optional[PrisirKeyStore] = None):
        self.store = store or PrisirKeyStore()

    # 已知平台的官方默认端点+模型: 用户只填 key(不填 base_url/模型)即可用,
    # 修「填了 minimaxi/deepseek/ollama key 却判无可用平台」缺陷。
    # 端点与本仓 prisir_philosopher.py / team_lead_tools.py / model_providers.py 保持一致。
    _KNOWN_PLATFORM_DEFAULTS: Dict[str, Dict[str, str]] = {
        "openai":    {"base_url": "https://api.openai.com/v1",     "model": "gpt-4o"},
        "anthropic": {"base_url": "https://api.anthropic.com",     "model": "claude-opus-5"},
        "minimaxi":  {"base_url": "https://api.minimaxi.com/v1",   "model": "MiniMax-M1"},
        "minimax":   {"base_url": "https://api.minimaxi.com/v1",   "model": "MiniMax-M1"},
        "deepseek":  {"base_url": "https://api.deepseek.com/v1",   "model": "deepseek-chat"},
        "ollama":    {"base_url": "https://ollama.com/v1",         "model": "qwen2.5"},
        "yunbailian": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                      "model": "qwen-plus"},
        "qwen":      {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                      "model": "qwen-plus"},
        "agnes":     {"base_url": "https://openrouter.ai/api/v1",  "model": "anthropic/claude-3.5-sonnet"},
    }

    # ---- 平台装配 ----
    def _platform_cfg(self, platform: str) -> Optional[Dict[str, Any]]:
        rec = self.store.get_key(platform)
        if not rec or not rec["api_key"]:
            return None
        cfg = dict(rec)
        known = self._KNOWN_PLATFORM_DEFAULTS.get(platform)
        if known:
            # 已知平台: 缺 base_url/model 用官方默认补齐
            cfg["base_url"] = cfg["base_url"] or known["base_url"]
            cfg["model"] = cfg["model"] or known["model"]
        if platform == "openai":
            cfg["fast_model"] = cfg["meta"].get("fast_model", "gpt-4o-mini")
        elif not known:  # custom / 未登记的自定义端点: 仍需 base_url 才可用
            if not cfg["base_url"]:
                return None
            cfg["model"] = cfg["model"] or "default"
        return cfg

    def available_platforms(self) -> List[str]:
        return [p["platform"] for p in self.store.list_platforms() if p["has_key"]]

    def route(self, messages: Messages, strategy: str = "smart",
              task_type: Optional[str] = None,
              exclude: Optional[set] = None) -> Dict[str, Any]:
        """选平台。返回 {platform, cfg, task_type} 或抛 RuntimeError。
        exclude: 本次跳过这些平台名(故障转移拉黑用)。"""
        text = " ".join(m.get("content", "") for m in messages[-3:])
        tt = task_type or classify_task(text)

        if strategy in ("openai", "anthropic"):
            order = [strategy]
        elif strategy == "local":
            order = ["custom"]
        elif strategy.startswith("custom"):
            order = [strategy]
        else:  # smart
            order = list(_TASK_PREFERENCE.get(tt, _TASK_PREFERENCE["general"]))

        # 用户实际填了 key 的平台,可能在偏好序之外(自定义平台名)——补齐到序尾,
        # 保证「多设几个端点自动故障转移」覆盖所有已配端点,不只是 openai/anthropic/custom。
        for p in self.available_platforms():
            if p not in order:
                order.append(p)

        excl = exclude or set()
        for platform in order:
            if platform in excl:
                continue
            cfg = self._platform_cfg(platform)
            if cfg:
                return {"platform": platform, "cfg": cfg, "task_type": tt}
        raise RuntimeError(
            f"无可用模型平台(策略={strategy}, 已填key={self.available_platforms()}, "
            f"已拉黑={sorted(excl)})。"
            "请到 Prisir AI 设置页填入 OpenAI / Anthropic / 自定义端点 key。")

    # 调用失败时可安全重试下一平台的错误(402订阅墙/429限流/超时/5xx/连接错)。
    # 4xx 里 400(请求体非法)/401(key 错)/403(无权限)是配置问题,换平台无意义,不重试。
    _RETRYABLE_HTTP = {402, 408, 409, 425, 429, 500, 502, 503, 504}

    def _is_retryable(self, exc: Exception) -> bool:
        """判断调用异常是否值得换平台重试。"""
        import httpx
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            return code in self._RETRYABLE_HTTP or code >= 500
        # 网络层(超时/连接/解析)一律可换平台
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                            httpx.NetworkError, httpx.RemoteProtocolError)):
            return True
        # RuntimeError 多为「空 content / 解析失败 / 无可用平台」——空响应可换平台
        msg = str(exc)
        if "空 content" in msg or "响应解析失败" in msg or "空响应" in msg:
            return True
        return False

    # ---- 调用 ----
    async def generate(self, messages: Messages, strategy: str = "smart",
                       temperature: float = 0.7, max_tokens: int = 4096) -> Dict[str, Any]:
        """路由 + 调用 + 跨平台故障转移。返回 {text, platform, model, task_type, failover?}。

        2026-09-06 故障转移:route() 按偏好序(含所有已配端点)选平台;调用抛可重试错误
        (402/429/超时/5xx/连接错/空响应)就拉黑该平台换下一个,直至成功或全挂。
        返回带 failover=[{platform, error}...] 记录转移轨迹(不含 key)。
        """
        exclude: set = set()
        failover: list = []
        last_err: Optional[Exception] = None
        while True:
            try:
                pick = self.route(messages, strategy, exclude=exclude)
            except RuntimeError as e:
                # 没有更多可用平台
                if last_err is not None:
                    raise RuntimeError(
                        f"所有已配平台均失败:{failover and ' → '.join(f['platform'] for f in failover)};"
                        f"最后错误: {last_err}") from last_err
                raise
            cfg, platform = pick["cfg"], pick["platform"]
            try:
                # 协议分派:平台 anthropic,或自定义端点 meta.proto=anthropic → Anthropic Messages
                proto = (cfg.get("meta") or {}).get("proto", "")
                use_anthropic = (platform == "anthropic") or (proto == "anthropic")
                if use_anthropic:
                    text = await self._call_anthropic(cfg, messages, temperature, max_tokens)
                    model = cfg["model"]
                else:
                    model = cfg["model"]
                    if pick["task_type"] == "fast" and cfg.get("fast_model"):
                        model = cfg["fast_model"]
                    text = await self._call_openai_compat(cfg, messages, temperature, max_tokens, model)
                out = {"text": text, "platform": platform, "model": model,
                       "task_type": pick["task_type"]}
                if failover:
                    out["failover"] = failover
                return out
            except Exception as e:  # noqa: BLE001
                last_err = e
                if not self._is_retryable(e):
                    raise
                # 记录转移(不含 key),拉黑换下一个
                failover.append({"platform": platform,
                                 "error": f"{type(e).__name__}: {str(e)[:80]}"})
                exclude.add(platform)

    async def _call_openai_compat(self, cfg: Dict[str, Any], messages: Messages,
                                  temperature: float, max_tokens: int, model: str) -> str:
        base = cfg["base_url"].rstrip("/")
        endpoint = f"{base}/chat/completions"
        payload = {"model": model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        headers = {"Authorization": f"Bearer {cfg['api_key']}"}
        async with tls13_client(timeout_s=90, endpoint=endpoint) as client:
            r = await client.post(endpoint, json=payload, headers=headers)
            # 部分模型(如 kimi coding)只接受固定 temperature,报 400 invalid temperature → 去掉重试
            if r.status_code == 400 and "temperature" in r.text.lower():
                payload.pop("temperature", None)
                r = await client.post(endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"{cfg['platform']} 响应解析失败:{str(data)[:200]}") from e
        if not text or not text.strip():
            raise RuntimeError(f"{cfg['platform']} 空 content(响应:{str(data)[:200]})")
        return text

    async def _call_anthropic(self, cfg: Dict[str, Any], messages: Messages,
                              temperature: float, max_tokens: int) -> str:
        """Anthropic Messages API(非 OpenAI 协议)"""
        endpoint = cfg["base_url"].rstrip("/") + "/v1/messages"
        system = ""
        msgs = []
        for m in messages:
            if m.get("role") == "system":
                system = m.get("content", "")
            else:
                msgs.append({"role": m["role"], "content": m.get("content", "")})
        payload: Dict[str, Any] = {
            "model": cfg["model"], "max_tokens": max_tokens,
            "temperature": temperature, "messages": msgs,
        }
        if system:
            payload["system"] = system
        headers = {
            "x-api-key": cfg["api_key"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        async with tls13_client(timeout_s=90, endpoint=endpoint) as client:
            r = await client.post(endpoint, json=payload, headers=headers)
            # 部分模型只接受固定 temperature,报 400 → 去掉重试
            if r.status_code == 400 and "temperature" in r.text.lower():
                payload.pop("temperature", None)
                r = await client.post(endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        try:
            parts = data.get("content", [])
            text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        except (KeyError, TypeError) as e:
            raise RuntimeError(f"anthropic 响应解析失败:{str(data)[:200]}") from e
        if not text or not text.strip():
            raise RuntimeError(f"anthropic 空 content(响应:{str(data)[:200]})")
        return text


# ============================================================
# 主对话路径(rc=2 错误字符串)可重试判定 —— PrisirRouter._is_retryable 的字符串版
# ============================================================
# run_conversation(prisiragent_cli)把 LLM 异常吞成 rc=2 字符串(不抛异常),主对话
# 故障转移只剩错误文本可判。此函数与 _is_retryable(异常版)共用 _RETRYABLE_HTTP 语义:
#   402/408/409/425/429/5xx/超时/连接错/空响应 → 换平台
#   400(非法体)/401(key错)/403(无权限) → 配置错,换平台无意义
# 400-temperature 已在 _completion_with_temperature_fallback 内部回退过,换平台无意义 → 不重试。
_RETRYABLE_HTTP = PrisirRouter._RETRYABLE_HTTP
_NET_HINTS = ("timeout", "timed out", "connect", "connection", "network",
              "remote protocol", "eof", "空 content", "空响应", "响应解析失败")
_CODE_RE = re.compile(r"\b([45]\d\d)\b")


def is_retryable_error_str(err: str) -> bool:
    """判断主对话 rc=2 的错误串是否值得拉黑当前平台换下一个重试。"""
    s = (err or "").lower()
    # 明确非重试:400-temperature(已内部回退)/401/403 配置错
    if "temperature" in s and "400" in s:
        return False
    m = _CODE_RE.search(s)
    if m:
        code = int(m.group(1))
        if code in (401, 403):
            return False
        if code == 400:
            return False  # 非 temperature 的 400 是请求体非法,换平台无意义
        return code in _RETRYABLE_HTTP or code >= 500
    # 无状态码:看网络/空响应关键词
    if any(h in s for h in _NET_HINTS):
        return True
    # 兜底:无法判定时宁可多转移一次(全平台挂会自然终止),不漏真故障
    return True


# ============================================================
# 端点模型列表拉取(参考翻译插件 engines.listModels:GET {base}/models)
# ============================================================
def list_endpoint_models(base_url: str, api_key: str = "", timeout_s: float = 15.0) -> Dict[str, Any]:
    """从 OpenAI 兼容端点拉可取模型列表。返回 {ok, models, error}。

    同步实现(供设置页「拉取模型」用)。只列模型名,不回显 key。
    Anthropic 协议端点(走 /v1/messages 的那类)多数无 /models,
    但同 host 的 OpenAI 兼容侧(/v1)有 — 拉模型时自动换成 /v1 再试。
    """
    import httpx  # 延迟导入,避免无 httpx 环境影响其它路径
    from urllib.parse import urlparse
    base = (base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "models": [], "error": "no_base_url"}

    def _fetch(url: str) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        with httpx.Client(timeout=timeout_s) as client:
            r = client.get(url, headers=headers)
        if r.status_code != 200:
            return {"ok": False, "models": [], "error": f"HTTP {r.status_code}"}
        data = r.json()
        arr = data.get("data") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if not isinstance(arr, list):
            arr = []
        models = []
        for m in arr:
            if isinstance(m, str):
                models.append(m)
            elif isinstance(m, dict):
                mid = m.get("id") or m.get("name") or m.get("model")
                if mid:
                    models.append(str(mid))
        return {"ok": bool(models), "models": models, "error": None if models else "empty"}

    try:
        result = _fetch(f"{base}/models")
        # anthropic 协议端点 404 → 换同 host 的 OpenAI 兼容侧(/v1)再拉一次
        if not result["ok"] and result["error"] == "HTTP 404" and base.endswith("/anthropic"):
            parsed = urlparse(base)
            alt_base = f"{parsed.scheme}://{parsed.netloc}/v1"
            result = _fetch(f"{alt_base}/models")
            if result["ok"]:
                result["note"] = "models_from_openai_compat_side"
        return result
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "models": [], "error": f"{type(e).__name__}: {e}"}


# ============================================================
# 延续话题(任务#7): AI 回答末尾带 2-5 个相关延续话题(学 Perplexity)
# ============================================================
_FOLLOWUP_PROMPT = (
    "基于上面的问答,生成 {n} 个用户最可能想接着问的相关延续话题。"
    "要求:每条不超过 20 字,是问句或祈使句,彼此角度不同(优缺点/实现/资源/对比/深入)。"
    "只输出 JSON 数组字符串,不要其他内容。例: [\"话题1\",\"话题2\"]"
)


async def generate_followups(router: PrisirRouter, question: str, answer: str,
                             n: int = 4, strategy: str = "smart") -> List[str]:
    """生成 2-5 个延续话题。失败返回 [](不阻塞主回答)。"""
    n = max(2, min(5, n))
    convo = [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer[:2000]},
        {"role": "user", "content": _FOLLOWUP_PROMPT.format(n=n)},
    ]
    try:
        res = await router.generate(convo, strategy=strategy, temperature=0.7, max_tokens=300)
        text = res["text"].strip()
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return []
        arr = json.loads(m.group(0))
        return [str(x)[:60] for x in arr if isinstance(x, str)][:n]
    except Exception:  # noqa: BLE001
        return []
