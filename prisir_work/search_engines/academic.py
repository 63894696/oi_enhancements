"""academic.py — 学术 / 论文 预搜索服务(免 key)。

清单(来自 _engines_manifest.py):
  - arxiv               — arXiv e-Print archive (Atom XML API)
  - pubmed              — PubMed (NCBI E-utilities esearch + esummary)
  - semantic_scholar    — Semantic Scholar Graph (免 key 受限,RPS < 100)
  - crossref            — Crossref REST API (DOI / metadata)
  - europepmc           — Europe PMC REST API
  - pdbe                — Protein Data Bank Europe API
  - astrophysics        — NASA ADS API(免 key 部分)
  - openairedatasets    — OpenAIRE Datasets (Graph API)
  - openairepublications — OpenAIRE Publications (Graph API)
  - core.ac.uk          — CORE 学术聚合 API(免 key 受限)
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any

from prisir_work.search_engines._common import (
    _between, _http_get, _json_get, _strip_html, _strip_xml, _truncate,
)


def arxiv(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """arXiv e-Print archive(免 key,Atom XML)。"""
    if not query:
        return []
    url = ("http://export.arxiv.org/api/query?" + urllib.parse.urlencode({
        "search_query": f"all:{query.strip()}",
        "max_results": str(max(1, min(limit, 20))),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }))
    try:
        xml = _http_get(url, timeout=8.0,
                        headers={"Accept": "application/atom+xml"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in xml.split("<entry>")[1:]:
        entry_id = _between(chunk, "<id>", "</id>").strip()
        title_text = _strip_xml(_between(chunk, "<title>", "</title>"))
        summary = _strip_xml(_between(chunk, "<summary>", "</summary>"))
        if not entry_id:
            continue
        out.append({
            "url": entry_id,
            "title": title_text,
            "snippet": _truncate(summary, 300),
        })
        if len(out) >= limit:
            break
    return out


def pubmed(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """PubMed — esearch 返 ID 列表 → esummary 取 title/source。"""
    if not query:
        return []
    try:
        # 1) search
        search_url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
                      + urllib.parse.urlencode({
                          "db": "pubmed",
                          "term": query.strip(),
                          "retmax": str(max(1, min(limit, 20))),
                          "retmode": "json",
                      }))
        s_data = _json_get(search_url, timeout=8.0)
        ids = ((s_data.get("esearchresult") or {}).get("idlist") or [])
        if not ids:
            return []
        # 2) summary
        sum_url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
                   + urllib.parse.urlencode({
                       "db": "pubmed",
                       "id": ",".join(ids),
                       "retmode": "json",
                   }))
        sum_raw = _json_get(sum_url, timeout=8.0)
        result = (sum_raw.get("result") or {})
        out: list[dict[str, Any]] = []
        for pid in ids:
            doc = result.get(pid) or {}
            title = (doc.get("title") or "").strip()
            source = (doc.get("source") or "").strip()
            pubdate = (doc.get("pubdate") or "").strip()
            if not title:
                continue
            snippet = " · ".join(filter(None, [source, pubdate]))
            out.append({
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                "title": title,
                "snippet": snippet,
            })
        return out[:limit]
    except Exception:
        return []


def semantic_scholar(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Semantic Scholar Graph API(免 key, 受 RPS 限制)。"""
    if not query:
        return []
    url = ("https://api.semanticscholar.org/graph/v1/paper/search?"
           + urllib.parse.urlencode({
               "query": query.strip(),
               "limit": str(max(1, min(limit, 20))),
               "fields": "title,abstract,url,year,authors",
           }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Semantic Scholar)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("data") or [])[:limit]:
            title = (r.get("title") or "").strip()
            url_v = (r.get("url") or "").strip()
            abstract = (r.get("abstract") or "").strip()
            year = r.get("year") or ""
            if not title or not url_v:
                continue
            out.append({
                "url": url_v,
                "title": f"{title} ({year})" if year else title,
                "snippet": _truncate(abstract, 300),
            })
        return out
    except Exception:
        return []


def crossref(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Crossref REST API(免 key)。"""
    if not query:
        return []
    url = ("https://api.crossref.org/works?" + urllib.parse.urlencode({
        "query": query.strip(),
        "rows": str(max(1, min(limit, 20))),
    }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Crossref; mailto:test@example.com)"})
        out: list[dict[str, Any]] = []
        for r in ((data.get("message") or {}).get("items") or [])[:limit]:
            title_list = r.get("title") or []
            url_list = r.get("URL") or ""
            abstract = _strip_html(r.get("abstract") or "")
            container = (r.get("container-title") or [""])[0] if r.get("container-title") else ""
            year = ""
            issued = r.get("issued") or {}
            dp = issued.get("date-parts") or [[]]
            if dp and dp[0]:
                year = str(dp[0][0])
            if not title_list or not url_list:
                continue
            title = _strip_html(title_list[0] or "")
            out.append({
                "url": url_list,
                "title": f"{title} ({year})" if year else title,
                "snippet": _truncate(" · ".join(filter(None, [container, abstract])), 300),
            })
        return out
    except Exception:
        return []


def europepmc(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Europe PMC REST API(免 key)。"""
    if not query:
        return []
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
           + urllib.parse.urlencode({
               "query": query.strip(),
               "pageSize": str(max(1, min(limit, 20))),
               "format": "json",
           }))
    try:
        data = _json_get(url, timeout=8.0)
        out: list[dict[str, Any]] = []
        for r in ((data.get("response") or {}).get("result") or data.get("resultList") or {}).get("result", []) or []:
            if isinstance(r, dict):
                items = [r]
            else:
                items = r or []
            for it in items[:limit]:
                title = it.get("title") or ""
                pmid = it.get("pmid") or it.get("id") or ""
                doi = it.get("doi") or ""
                url_v = it.get("fullTextUrlList") or {}
                source = it.get("journalTitle") or it.get("bookTitle") or ""
                year = it.get("pubYear") or ""
                if isinstance(title, list):
                    title = title[0] if title else ""
                title = _strip_html(str(title)).strip()
                if not title:
                    continue
                # url href
                if isinstance(url_v, dict):
                    ftl = url_v.get("fullTextUrl") or []
                    url_str = (ftl[0].get("url", "") if ftl else "")
                else:
                    url_str = ""
                if not url_str:
                    url_str = (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid
                               else f"https://doi.org/{doi}" if doi else "")
                if not url_str:
                    continue
                snippet = " · ".join(filter(None, [source, str(year)]))
                out.append({
                    "url": url_str,
                    "title": title,
                    "snippet": snippet,
                })
                if len(out) >= limit:
                    return out
        return out
    except Exception:
        return []


def pdbe(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Protein Data Bank Europe — PDB ID 全文搜索 API(免 key)。"""
    if not query:
        return []
    url = ("https://www.ebi.ac.uk/pdbe/search/pdb/select?"
           + urllib.parse.urlencode({
               "q": query.strip(),
               "rows": str(max(1, min(limit, 20))),
               "fl": "pdb_id,title,structure_determination_method,release_year",
               "json.nl": "arrarr",
           }))
    try:
        data = _json_get(url, timeout=8.0)
        out: list[dict[str, Any]] = []
        docs = ((data.get("response") or {}).get("docs") or [])[:limit]
        for d in docs:
            pdb_id = d.get("pdb_id") or ""
            title = _strip_html(d.get("title") or "").strip()
            method = d.get("structure_determination_method") or ""
            year = d.get("release_year") or ""
            if not pdb_id or not title:
                continue
            out.append({
                "url": f"https://www.ebi.ac.uk/pdbe/entry/pdb/{pdb_id}",
                "title": title,
                "snippet": " · ".join(filter(None, [method, year])),
            })
        return out
    except Exception:
        return []


def astrophysics(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """NASA Astrophysics Data System(免 key, 返回 HTML 需解析)。"""
    if not query:
        return []
    url = ("https://ui.adsabs.harvard.edu/search/q="
           + urllib.parse.urlencode({"q": query.strip()})[2:]  # already-encoded 'q=' prefix
           + "&sort=relevance")
    try:
        html = _http_get(url, timeout=8.0)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    # ADS HTML 复杂,仅提 <a class="paper-link"> 区域近似
    for chunk in html.split('<li class="paper-item">')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        title = _strip_html(_between(chunk, '<h3 class="paper-title">', '</h3>'))
        if not link or not title:
            continue
        out.append({
            "url": "https://ui.adsabs.harvard.edu" + link if link.startswith("/") else link,
            "title": title,
            "snippet": "",
        })
    return out


def openairedatasets(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """OpenAIRE Datasets Graph API(免 key)。"""
    if not query:
        return []
    url = ("https://api.openaire.eu/graph/v2/datasets/search?"
           + urllib.parse.urlencode({
               "query": query.strip(),
               "page": "0",
               "size": str(max(1, min(limit, 20))),
           }))
    try:
        data = _json_get(url, timeout=8.0)
        out: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:limit]:
            title = r.get("title") or ""
            url_v = r.get("url") or ""
            desc = r.get("description") or ""
            if not title or not url_v:
                continue
            out.append({
                "url": url_v,
                "title": _strip_html(str(title)).strip(),
                "snippet": _truncate(_strip_html(str(desc)), 300),
            })
        return out
    except Exception:
        return []


def openairepublications(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """OpenAIRE Publications Graph API(免 key)。"""
    if not query:
        return []
    url = ("https://api.openaire.eu/graph/v2/publications/search?"
           + urllib.parse.urlencode({
               "query": query.strip(),
               "page": "0",
               "size": str(max(1, min(limit, 20))),
           }))
    try:
        data = _json_get(url, timeout=8.0)
        out: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:limit]:
            title = r.get("title") or ""
            url_v = r.get("url") or ""
            desc = r.get("description") or ""
            if not title or not url_v:
                continue
            out.append({
                "url": url_v,
                "title": _strip_html(str(title)).strip(),
                "snippet": _truncate(_strip_html(str(desc)), 300),
            })
        return out
    except Exception:
        return []


def core_ac_uk(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """CORE 聚合搜索(免 key,有限 RPS)。"""
    if not query:
        return []
    url = ("https://api.core.ac.uk/v3/search/works?"
           + urllib.parse.urlencode({
               "q": query.strip(),
               "limit": str(max(1, min(limit, 20))),
           }))
    try:
        data = _json_get(url, timeout=8.0)
        out: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:limit]:
            title = r.get("title") or ""
            url_v = r.get("downloadUrl") or r.get("sourceFulltextUrl") or ""
            abstract = r.get("abstract") or ""
            if not title or not url_v:
                continue
            out.append({
                "url": url_v,
                "title": _strip_html(str(title)).strip(),
                "snippet": _truncate(_strip_html(str(abstract)), 300),
            })
        return out
    except Exception:
        return []


def register_all() -> None:
    """注册本文件所有 provider 到 web_search._PROVIDERS。"""
    from prisir_work import web_search as _ws  # 局部 import 避免循环
    _ws.register_provider("arxiv", arxiv)
    _ws.register_provider("pubmed", pubmed)
    _ws.register_provider("semantic_scholar", semantic_scholar)
    _ws.register_provider("crossref", crossref)
    _ws.register_provider("europepmc", europepmc)
    _ws.register_provider("pdbe", pdbe)
    _ws.register_provider("astrophysics", astrophysics)
    _ws.register_provider("openairedatasets", openairedatasets)
    _ws.register_provider("openairepublications", openairepublications)
    _ws.register_provider("core.ac.uk", core_ac_uk)