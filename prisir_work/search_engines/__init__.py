"""prisIr_work/search_engines — SearXNG 启用无 key 引擎 76 provider 模块。
- 借鉴 searxng/searxng/settings.yml,snapshot 2026-10-02
- 9 个类别子文件,各 register_all() 一次性注册到 web_search._PROVIDERS
- 复用 web_search 的 RRF 融合 / LRU 缓存 / 并发隔离
- 失败返 [],不 raise;ban 字典自动降权风控引擎
"""
from __future__ import annotations