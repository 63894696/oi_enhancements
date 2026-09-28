"""prisirmp — 个人微信公众号历史 recall 工具(独立 CLI/服务)。

定位:
- 把本地微信 Message*.db(WCDB/SQLCipher 加密)里的公众号文章(Type=49 appmsg)
  解出来 → 入本地 SQLite FTS5 → 提供 CLI 搜索。
- 不接 PrisirAI 主线(Phase 2 再决定是否合入);独立可装、可跑、可卸。
- 100% 本机,解密后 db 缓存可清,搜索结果不出本机索引。

入口:
    python -m prisir_mp status
    python -m prisir_mp extract
    python -m prisir_mp decrypt
    python -m prisir_mp index
    python -m prisir_mp search "<query>"
    python -m prisir_mp clear
    python -m prisir_mp verify

设计参考:
- 范式抄 prisir_fcontent/engine.py(FTS5 普通表 + RLock + DROP+重建 + meta 持久化)
- 分词抄 prisir_fcontent/tokenize.py(CJK unigram+bigram + ASCII 词)
- 密钥提取走外部 TANGandXUE/wcdb-key-tool(MIT,三平台二进制),只调不嵌。
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]