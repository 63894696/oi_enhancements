"""prisIr_graph_recall_bridge.py — 把 prisIr_graph 召回结果桥接到 OIMemory pre_chat 注入层

公开 API:
    recall_for_task(query, k=5, ...) → list[VaultHit]
    format_vault_hits_for_prompt(hits, max_chars=1500) → str

设计:
- 默认 min_l0_hits=2(MCP 工具默认 3)→ 更激进地走 L0 路径节省 token
- snippet 200 字符 + node.path 便于 LLM 反查
- max_chars 截断防爆 token(对齐 oi_memory_hooks max_recall_chars=1500 默认)
- 不修改 OIMemory schema,只在 hooks 层追加
- bridge 失败静默(外部 try/except 包住)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import prisIr_graph_concept as _concept
import prisIr_graph_store as _store

# ── 常量 ──────────────────────────────────────────────────────────
SNIPPET_CHARS = 200  # 单条 snippet 截断
DEFAULT_MAX_HITS = 5
DEFAULT_MIN_L0_HITS = 2  # 比 MCP 工具默认 3 更激进(hook 场景 token 优先)


# ── 数据结构 ──────────────────────────────────────────────────────
@dataclass
class VaultHit:
    """vault 结构化召回条目 — 供 pre_chat 注入。"""
    layer: str           # L0_wikilink / L1_fresh / L1_cache_hit / L0_miss_no_candidates
    node_id: str
    title: str
    path: str            # vault-relative,POSIX 风格
    snippet: str         # vault 文件正文前 200 字符(去 frontmatter)
    score: float         # cosine(L1) / 1.0(L0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "node_id": self.node_id,
            "title": self.title,
            "path": self.path,
            "snippet": self.snippet,
            "score": self.score,
        }


# ── 内部 helper ───────────────────────────────────────────────────
def _resolve_vault_dir(vault_dir: Path | None) -> Path:
    if vault_dir is not None:
        return vault_dir
    try:
        from vault_tools import VAULT_DIR as V
        return V
    except Exception:
        return Path(r"C:\Users\Administrator\Documents\ObsidianVault")


def _read_snippet(path: str, vault_dir: Path) -> str:
    """读 vault 文件正文前 200 字符(去 frontmatter)。失败返 ''。"""
    if not path or not vault_dir:
        return ""
    p = vault_dir / path
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    # 去 frontmatter
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4:].strip()
    return text[:SNIPPET_CHARS]


# ── 公开 API ──────────────────────────────────────────────────────
def recall_for_task(
    query: str,
    k: int = DEFAULT_MAX_HITS,
    min_l0_hits: int = DEFAULT_MIN_L0_HITS,
    vault_dir: Path | None = None,
    db_path: Path | None = None,
    cache_path: Path | None = None,
) -> list[VaultHit]:
    """调 concept_search 拿候选 + 读 vault 正文构造 VaultHit 列表。

    失败 / 空 query / 全 miss 都返 []。
    """
    if not query or not query.strip():
        return []

    vdir = _resolve_vault_dir(vault_dir)
    kwargs: dict[str, Any] = {
        "query": query,
        "limit": k,
        "min_l0_hits": min_l0_hits,
        "vault_dir": vdir,
    }
    if db_path is not None:
        kwargs["db_path"] = db_path
    if cache_path is not None:
        kwargs["cache_path"] = cache_path

    try:
        r = _concept.concept_search(**kwargs)
    except Exception:
        return []
    if not r.get("ok"):
        return []

    layer = r.get("layer", "L1_fresh")
    hits: list[VaultHit] = []
    for item in r.get("results", []):
        path = item.get("path", "")
        snippet = _read_snippet(path, vdir)
        try:
            score = float(item.get("cosine", 1.0))
        except (TypeError, ValueError):
            score = 1.0
        hits.append(VaultHit(
            layer=layer,
            node_id=item.get("id", ""),
            title=item.get("title", ""),
            path=path,
            snippet=snippet,
            score=score,
        ))
    return hits


def format_vault_hits_for_prompt(
    hits: list[VaultHit] | list[dict],
    max_chars: int = 1500,
) -> str:
    """把 VaultHit 列表拼成可注入 prompt 的字符串。空列表返 ''。"""
    if not hits:
        return ""

    lines = ["[vault graph recall — related notes]"]
    for i, h in enumerate(hits, 1):
        # 兼容 VaultHit 和 dict
        if isinstance(h, VaultHit):
            layer, title, path, snippet, score = h.layer, h.title, h.path, h.snippet, h.score
        else:
            layer = h.get("layer", "?")
            title = h.get("title", "?")
            path = h.get("path", "?")
            snippet = h.get("snippet", "")
            score = h.get("score", 1.0)
        snippet = (snippet or "").replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        layer_short = layer.split("_")[0] if layer else "?"
        score_str = f" score={score:.2f}" if layer_short == "L1" else ""
        lines.append(f"  {i}. [{layer_short}] {title} ({path}){score_str}: {snippet}")
    lines.append("[End vault recall]")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n[truncated]"
    return out


# ── Smoke ─────────────────────────────────────────────────────────
def _smoke() -> None:
    import tempfile
    import prisIr_graph_build as _build

    with tempfile.TemporaryDirectory(prefix="prisIr_bridge_smoke_") as tmp:
        tmpdir = Path(tmp)
        vault = tmpdir / "vault"
        vault.mkdir()
        (vault / "ai.md").write_text(
            "---\ntags: [ai]\n---\n# AI 笔记\n讨论 Transformer 架构与神经网络。",
            encoding="utf-8",
        )
        (vault / "mm.md").write_text(
            "# 多模态\nCLIP 是经典跨模态模型。",
            encoding="utf-8",
        )

        db = tmpdir / "graph.db"
        cache = tmpdir / "cache.db"
        r = _build.build_vault(vault, db)
        assert r["ok"]

        # L0 命中:query="ai" 命中 ai.md 标题,bridge 默认 min_l0_hits=2
        # "ai" 只命中 1 个标题 → L0 不够 → 走 L1 候选 → 测试环境无 embedding → 返 0
        # 所以 smoke 用 min_l0_hits=1 显式让 L0 触发
        hits = recall_for_task("ai", k=3, min_l0_hits=1,
                               vault_dir=vault, db_path=db, cache_path=cache)
        print(f"[smoke] L0 命中 'ai' (min_l0_hits=1): {len(hits)} 条")
        assert len(hits) >= 1, f"应至少 1 命中,实际 {len(hits)}"
        h0 = hits[0]
        assert "AI" in h0.title or "ai" in h0.title.lower()
        # snippet 不一定包含 Transformer(因 query=ai L0 命中只取标题/frontmatter)
        print(f"  layer={h0.layer} title={h0.title} path={h0.path}")

        # format 输出
        out = format_vault_hits_for_prompt(hits)
        print(f"[smoke] format 长度={len(out)},含 'vault graph recall': {'vault graph recall' in out}")
        assert "[vault graph recall" in out
        assert "[End vault recall]" in out
        assert "AI" in out  # 标题含 AI

        # 截断
        out_trunc = format_vault_hits_for_prompt(hits, max_chars=80)
        assert "[truncated]" in out_trunc
        print(f"  [PASS] 截断: {len(out_trunc)} 字符,尾='{out_trunc[-30:]}'")

        # 空 query
        empty = recall_for_task("")
        assert empty == []
        print(f"  [PASS] 空 query 返 []")

        # dict 输入兼容
        dict_hits = [h.to_dict() for h in hits]
        out2 = format_vault_hits_for_prompt(dict_hits)
        assert "[vault graph recall" in out2
        print(f"  [PASS] dict 输入兼容")

        print("[smoke] ALL PASS")


if __name__ == "__main__":
    _smoke()