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

    # ---- 平台装配 ----
    def _platform_cfg(self, platform: str) -> Optional[Dict[str, Any]]:
        rec = self.store.get_key(platform)
        if not rec or not rec["api_key"]:
            return None
        cfg = dict(rec)
        if platform == "openai":
            cfg.setdefault("base_url", "") or cfg.update(base_url="https://api.openai.com/v1")
            cfg["base_url"] = cfg["base_url"] or "https://api.openai.com/v1"
            cfg["model"] = cfg["model"] or "gpt-4o"
            cfg["fast_model"] = cfg["meta"].get("fast_model", "gpt-4o-mini")
        elif platform == "anthropic":
            cfg["base_url"] = cfg["base_url"] or "https://api.anthropic.com"
            cfg["model"] = cfg["model"] or "claude-opus-5"
        else:  # custom / 其他自定义端点
            if not cfg["base_url"]:
                return None
            cfg["model"] = cfg["model"] or "default"
        return cfg

    def available_platforms(self) -> List[str]:
        return [p["platform"] for p in self.store.list_platforms() if p["has_key"]]

    def route(self, messages: Messages, strategy: str = "smart",
              task_type: Optional[str] = None) -> Dict[str, Any]:
        """选平台。返回 {platform, cfg, task_type} 或抛 RuntimeError。"""
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

        for platform in order:
            cfg = self._platform_cfg(platform)
            if cfg:
                return {"platform": platform, "cfg": cfg, "task_type": tt}
        raise RuntimeError(
            f"无可用模型平台(策略={strategy}, 已填key={self.available_platforms()})。"
            "请到 Prisir AI 设置页填入 OpenAI / Anthropic / 自定义端点 key。")

    # ---- 调用 ----
    async def generate(self, messages: Messages, strategy: str = "smart",
                       temperature: float = 0.7, max_tokens: int = 4096) -> Dict[str, Any]:
        """路由 + 调用。返回 {text, platform, model, task_type}。"""
        pick = self.route(messages, strategy)
        cfg, platform = pick["cfg"], pick["platform"]

        # 协议分派:平台 anthropic,或自定义端点 meta.proto=anthropic → Anthropic Messages
        proto = (cfg.get("meta") or {}).get("proto", "")
        use_anthropic = (platform == "anthropic") or (platform == "custom" and proto == "anthropic")
        if use_anthropic:
            text = await self._call_anthropic(cfg, messages, temperature, max_tokens)
            model = cfg["model"]
        else:
            model = cfg["model"]
            if pick["task_type"] == "fast" and cfg.get("fast_model"):
                model = cfg["fast_model"]
            text = await self._call_openai_compat(cfg, messages, temperature, max_tokens, model)

        return {"text": text, "platform": platform, "model": model, "task_type": pick["task_type"]}

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
# 端点模型列表拉取(参考翻译插件 engines.listModels:GET {base}/models)
# ============================================================
def list_endpoint_models(base_url: str, api_key: str = "", timeout_s: float = 15.0) -> Dict[str, Any]:
    """从 OpenAI 兼容端点拉可取模型列表。返回 {ok, models, error}。

    同步实现(供 oiagent_web 设置页「拉取模型」用)。只列模型名,不回显 key。
    Anthropic 协议端点多数无 /models,失败时返回 error 由前端回退手填。
    """
    import httpx  # 延迟导入,避免无 httpx 环境影响其它路径
    base = (base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "models": [], "error": "no_base_url"}
    url = f"{base}/models"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
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
