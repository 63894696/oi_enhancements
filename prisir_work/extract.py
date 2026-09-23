# -*- coding: utf-8 -*-
"""JSON Schema 结构化抽取 v0.1.0 (P2.5+16e, 2026-09-23)

输入 URL,先 web_fetch 抓内容,再按 schema 抽出结构化数据。
LLM 可选,默认 regex 启发式(零成本、零依赖)。
任何环节失败降级,warnings 透出。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

log = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _step(name: str, **extra) -> dict:
    out = {"step": name, "ok": True, "duration_ms": 0}
    out.update(extra)
    return out


def _normalize_schema(schema: dict | list) -> dict:
    """list → dict 简化入口。

    list 形态可以是:
      - [{"name": "title", "type": "string", "required": True}, ...]
      - ["title", "author", "price"]   ← 简化入口(全部默认 string)
    dict 形态:{"type": "object", "fields": [...]} 或 {"type": "object", "properties": {...}}
    """
    if isinstance(schema, list):
        normalized = []
        for f in schema:
            if isinstance(f, dict):
                normalized.append(f)
            elif isinstance(f, str) and f.strip():
                # 简化入口:纯字符串 → {"name": str, "type": "string", "required": False}
                normalized.append({"name": f.strip(), "type": "string", "required": False})
        return {"type": "object", "fields": normalized}
    if not isinstance(schema, dict):
        return {"type": "object", "fields": []}
    if "fields" in schema:
        return schema
    if "properties" in schema:
        fields = [{"name": k, "type": v.get("type", "string"), "required": False}
                  for k, v in schema["properties"].items()]
        return {"type": "object", "fields": fields}
    return schema


def _extract_via_llm(content: str, schema_fields: list[dict], llm_call: Callable) -> dict | None:
    """调 LLM 抽结构化数据。失败 → 返 None。"""
    fields_desc = ", ".join(f"{f['name']}({f['type']},{'required' if f.get('required') else 'optional'})"
                            for f in schema_fields)
    prompt = (
        f"从以下网页内容按字段抽取 JSON 数据。\n"
        f"字段: {fields_desc}\n"
        f"要求: 输出纯 JSON 对象 {{field_name: value, ...}},缺失字段填 null;list 类型返 [];\n"
        f"不要输出任何解释文字。\n\n"
        f"网页内容(前 3000 字):\n{content[:3000]}\n\n"
        f"JSON:"
    )
    try:
        raw = llm_call(prompt)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except Exception as e:  # noqa: BLE001
        log.warning("llm extract failed: %s", e)
    return None


# ─── regex 启发式 ────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_META_NAME_RE = re.compile(
    r'<meta\s+name=["\']?([^"\'>\s]+)["\']?\s+content=["\']?([^"\'>]+)["\']?',
    re.IGNORECASE
)
_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
_H_RE = re.compile(r"<h[1-6][^>]*>([^<]+)</h[1-6]>", re.IGNORECASE)
_PRICE_RE = re.compile(r"\$?\s*(\d{1,7}(?:[.,]\d{1,2})?)")
_INT_RE = re.compile(r"-?\d+")
_FLOAT_RE = re.compile(r"-?\d+\.?\d*")
_LI_RE = re.compile(r"<li[^>]*>([^<]+)</li>", re.IGNORECASE)


def _strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    return _WS_RE.sub(" ", text).strip()


def _meta_content(html: str, name: str) -> str | None:
    for mm in _META_NAME_RE.finditer(html):
        if mm.group(1).lower() == name.lower():
            return mm.group(2).strip()
    return None


def _extract_via_regex(html: str, schema_fields: list[dict]) -> dict:
    """regex 启发式抽取。零依赖。"""
    out: dict[str, Any] = {}
    text = _strip_html(html)
    title_meta = _TITLE_RE.search(html)
    title = (title_meta.group(1).strip() if title_meta else "")

    for f in schema_fields:
        name = f["name"]
        ftype = f.get("type", "string")
        lname = name.lower()

        if ftype == "list":
            items = _LI_RE.findall(html)
            out[name] = [_strip_html(x) for x in items[:20]] or None
            continue

        if ftype == "number":
            m = _PRICE_RE.search(text) or _FLOAT_RE.search(text)
            if m:
                try:
                    out[name] = float(m.group(1).replace(",", "."))
                except ValueError:
                    out[name] = None
            else:
                out[name] = None
            continue

        if ftype == "integer":
            # 多个整数时取最大(更可能是 count/reviews 等指标)
            ints = [int(x) for x in _INT_RE.findall(text)]
            out[name] = max(ints) if ints else None
            continue

        if ftype == "boolean":
            out[name] = None  # regex 没法可靠判 boolean
            continue

        # string
        if "title" in lname or "name" in lname:
            if title and ("title" in lname):
                out[name] = title
                continue
            meta = _meta_content(html, "title") or _meta_content(html, "og:title")
            if meta:
                out[name] = meta
                continue
            h = _H_RE.search(html)
            out[name] = _strip_html(h.group(1)) if h else (text[:200] or None)
        elif "desc" in lname or "summary" in lname or "content" in lname:
            meta = _meta_content(html, "description") or _meta_content(html, "og:description")
            out[name] = meta or (text[:200] or None)
        elif "author" in lname:
            out[name] = _meta_content(html, "author")
        elif "keyword" in lname or "tag" in lname:
            kw = _meta_content(html, "keywords")
            if kw:
                out[name] = [k.strip() for k in kw.split(",") if k.strip()]
            else:
                out[name] = None
        else:
            # 默认:第一个 meta 标签
            meta = _meta_content(html, lname) or _meta_content(html, f"og:{lname}")
            out[name] = meta or (text[:200] or None)
    return out


def extract(url: str, schema: dict | list, *, timeout: float = 12.0,
            llm_call: Callable | None = None, use_fetch: bool = True) -> dict:
    """从 URL 抽结构化数据。失败降级,绝不抛。

    Returns:
    {
        'ok': True,
        'url': str,
        'schema': dict,                # 标准化后的 schema
        'data': dict,                   # 字段名 → 值
        'warnings': list[str],
        'mode': 'llm' | 'regex' | 'hybrid',
        'steps': [...]
    }
    """
    warnings: list[str] = []
    steps: list[dict] = []

    norm_schema = _normalize_schema(schema or {})
    fields = norm_schema.get("fields", [])

    if not url or not isinstance(url, str) or not url.strip():
        return {"ok": False, "url": url, "schema": norm_schema,
                "data": {}, "warnings": ["empty_url"], "mode": "regex", "steps": []}
    if not fields:
        return {"ok": False, "url": url, "schema": norm_schema,
                "data": {}, "warnings": ["empty_schema"], "mode": "regex", "steps": []}

    # ── fetch ──
    content = ""
    t0 = _now_ms()
    if use_fetch:
        try:
            from prisir_work import web_fetch as _wf
            r = _wf.fetch(url, options={"timeout": timeout})
            if r.get("ok") and r.get("content"):
                content = r["content"]
            else:
                warnings.append("fetch_failed")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"fetch_error:{type(e).__name__}")
    else:
        content = url  # 允许直接传 HTML 字符串当 url 内容(测试用)
    steps.append(_step("fetch", content_chars=len(content), duration_ms=_now_ms() - t0))

    if not content:
        return {"ok": True, "url": url, "schema": norm_schema,
                "data": {}, "warnings": warnings, "mode": "regex", "steps": steps}

    # ── LLM 路径(可选) ──
    llm_data: dict | None = None
    if llm_call is not None:
        t0 = _now_ms()
        llm_data = _extract_via_llm(content, fields, llm_call)
        steps.append(_step("llm", ok=llm_data is not None, duration_ms=_now_ms() - t0))
        if llm_data is None:
            warnings.append("llm_failed")

    # ── regex 路径 ──
    t0 = _now_ms()
    regex_data = _extract_via_regex(content, fields)
    steps.append(_step("regex", fields=len(fields), duration_ms=_now_ms() - t0))

    # ── merge ──
    if llm_data:
        # LLM 为主,regex 补 None 字段
        merged = {}
        for f in fields:
            v = llm_data.get(f["name"])
            if v is None or v == "" or v == []:
                merged[f["name"]] = regex_data.get(f["name"])
            else:
                merged[f["name"]] = v
        mode = "llm"
    else:
        merged = regex_data
        mode = "regex"

    return {
        "ok": True,
        "url": url,
        "schema": norm_schema,
        "data": merged,
        "warnings": warnings,
        "mode": mode,
        "steps": steps,
    }


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) < 3:
        print('usage: python -m prisir_work.extract "url" "field1,field2,..."')
        raise SystemExit(2)
    url_arg = _sys.argv[1]
    fields = [{"name": f.strip(), "type": "string", "required": False}
              for f in _sys.argv[2].split(",") if f.strip()]
    print(json.dumps(extract(url_arg, fields), ensure_ascii=False, indent=2))
