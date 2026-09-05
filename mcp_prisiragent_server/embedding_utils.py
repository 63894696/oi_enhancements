"""embedding_utils.py — 共享 Bailian embedding 工具模块

供 kg_tools.py 和 hook 脚本共用，避免重复代码。
走 Bailian text-embedding-v4 API（OpenAI 兼容端点）。

用法:
    from embedding_utils import bailian_embed
    vec = bailian_embed("hello world")
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

_BAILIAN_BASE = os.environ.get(
    "BAILIAN_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
_BAILIAN_KEY = os.environ.get("BAILIAN_API_KEY", "")
_EMBED_MODEL = "text-embedding-v4"
_EMBED_DIM = 1024

# Process-in-memory cache keyed by text hash (TTL 7 days)
_embed_cache: dict[str, dict[str, Any]] = {}


def _cache_key(text: str) -> str:
    """Simple hash for cache key."""
    return hashlib_text(text)


def _hashlib_text(text: str) -> str:
    """Minimal hash for cache key (no external dep)."""
    h = 0
    for c in text:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return f"{h:x}"


def bailian_embed(text: str, model: str = _EMBED_MODEL) -> list[float] | None:
    """Call Bailian text-embedding-v4, return vector or None on failure.

    Uses process-in-memory cache with 7-day TTL.
    Falls back to keyword if embedding API unavailable.
    """
    if not _BAILIAN_KEY:
        return None

    ck = _cache_key(text)
    entry = _embed_cache.get(ck)
    if entry and (time.time() - entry.get("_ts", 0)) < 7 * 86400:
        return entry["vector"]

    try:
        payload = json.dumps({"model": model, "input": text}).encode()
        req = urllib.request.Request(
            _BAILIAN_BASE + "/embeddings",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_BAILIAN_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            vec = data["data"][0]["embedding"]
            _embed_cache[ck] = {"vector": vec, "_ts": time.time()}
            return vec
    except Exception:
        return None


def bailian_cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two normalized vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = (sum(x * x for x in a)) ** 0.5
    norm_b = (sum(x * x for x in b)) ** 0.5
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)
