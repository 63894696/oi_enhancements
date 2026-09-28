# prisirmp — 个人微信公众号历史 recall 设计文档

**作者**:Claude Code (Opus 5)
**日期**:2026-09-24
**Phase**:0 (本地 CLI 验证)
**状态**:Phase 0 ship(本地库 + CLI + E2E + 单元测试全绿)

---

## 1. Context 与决策

用户决策:「个人公众号历史 recall」是独立应用而非 PrisirAI 功能,Phase 0 走**独立 CLI/服务**形态(与 `prisir_work/` / `prisir_findex/` / `prisir_fcontent/` 平级)。

Phase 0 目标:**用户自己用两周验证召回质量,再决定是否合入 PrisirAI 主对话链(Phase 2)**。

外部依赖:`TANGandXUE/wcdb-key-tool`(MIT,568★,Linux/Win/macOS 三平台)做密钥提取;**只调其 CLI 子进程,不内嵌代码**(避免双线维护 + License 风险)。

---

## 2. 与 PrisirAI 主线的关系

| 维度 | 现状 | 说明 |
|------|------|------|
| 代码位置 | `prisirmp/` 独立目录 | 不进 `prisIragent_web.py` / `companion/` / `prisIr_web.py` |
| 依赖方向 | 零依赖 PrisirAI 内部 | 只用 stdlib(sqlite3 / xml / hashlib / subprocess) |
| 包体积 | ~12KB Python 代码 | PyInstaller 装包视情况定,源码不急 |
| Phase 2 接入点 | `prisir_work/web_search.register_provider("wx_recall", fn)` | 范式已存在,Phase 0 不动 |
| 用户拍板权 | Phase 1 用两周再定 | Phase 2 之前 PrisirAI 主线零改动 |

**Phase 2 接合入的零耦合特性**:走 `register_provider` 注册,用户没跑过微信 → provider 不出现 → RRF 自动降级。无需 PrisirAI 端任何适配。

---

## 3. Goals (Phase 0)

1. 单 Python 包 `prisirmp/`,独立 SQLite 库 `prisirmp.db`(源码运行落模块目录;`%LOCALAPPDATA%\prisirmp\` 后续 PyInstaller 再切)
2. ETL 管线:`extract-key → decrypt-messages → parse-appmsg → index → search`
3. **安全红线**:100% 本机;解密 db 缓存可清;搜索结果不出本机索引;可选 consent 卡
4. E2E 自检脚本 `verify.py` 全绿;跨平台(Linux/Win/MacOS)

## Non-Goals (Phase 0 明确不做)

- ❌ 不接 PrisirAI web_search / capability(Phase 2)
- ❌ 不做 UI / Web 前端
- ❌ 不做实时监控(只用历史索引,实时订阅是另一个产品)
- ❌ 不抓视频号(微信 db 不缓存视频号)
- ❌ 不内嵌 wcdb-key-tool(只调外部二进制)

---

## 4. 数据流

```
┌─────────────────┐  subprocess    ┌──────────────────┐
│ wcdb-key-tool   │◀──────────────│ prisirmp/extract  │
│  (外部二进制)    │  --extract    │  .wcdb_extract()  │
│ MIT,三平台 568★ │  --decrypt    │  .wcdb_decrypt()  │
└─────────────────┘                └──────────────────┘
        │                                  │
        │ raw_key(32B hex)                 │
        ▼                                  ▼
┌─────────────────┐                ┌──────────────────┐
│ Message_*.db    │  AES-CBC      │ parse_appmsg_xml │
│ (SQLCipher 加密) │ ────────────▶ │ Type=49 articles │
│ %USERPROFILE%\  │  page-by-page │ 纯 stdlib XML     │
│ xwechat_files\  │                │ 兼容 CDATA/坏 XML │
└─────────────────┘                └──────────────────┘
        │                                  │
        │ wcdb-key-tool decrypt             │ 批 500~1000 篇
        ▼                                  ▼
┌──────────────────┐              ┌──────────────────┐
│ 解密后 db 缓存   │              │ articles 表       │
│ %LOCALAPPDATA%\  │              │ + articles_fts   │
│ prisirmp\cache\  │              │ (普通 FTS5 表    │
│ <account_id>\    │              │  非 external)    │
└──────────────────┘              └──────────────────┘
                                          │
                                          ▼
                                 ┌──────────────────┐
                                 │ search("query")  │
                                 │ → {hits, total}  │
                                 │ ↓ ** ** ANSI 高亮 │
                                 └──────────────────┘
```

---

## 5. SQLite Schema(踩坑防御版)

```sql
-- 状态持久化(重启不丢 last_index / wcdb_key)
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);

-- 已解密账号清单
CREATE TABLE accounts (
  account_id TEXT PRIMARY KEY,      -- md5(account_dir)[:16]
  account_dir TEXT NOT NULL,
  decrypted_dir TEXT NOT NULL,
  last_decrypt INTEGER NOT NULL,
  message_count INTEGER DEFAULT 0
);

-- 公众号文章元数据 + 截断正文 4KB
CREATE TABLE articles (
  id INTEGER PRIMARY KEY,
  account_id TEXT NOT NULL,
  biz TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,       -- 去重主键
  publisher TEXT, desc TEXT,
  ts INTEGER NOT NULL,
  source INTEGER DEFAULT 0,
  thumburl TEXT, text_tok TEXT,
  text TEXT                       -- 截断 4KB,供 snippet 用
);
CREATE INDEX idx_articles_ts ON articles(ts DESC);
CREATE INDEX idx_articles_biz ON articles(biz);

-- FTS5 普通表(非 external-content)— text 列存原文,snippet() 才出可读片段
CREATE VIRTUAL TABLE articles_fts USING fts5(
  url UNINDEXED,                  -- join 锚点(articles 也按 url 查)
  title, desc, publisher, text_tok, text,
  tokenize='unicode61'
);
```

### 5.1 关键踩坑(抄自 [[prisir-fcontent-engine]])

| 坑 | 表现 | 防御 |
|----|------|------|
| FTS5 external-content | `snippet()` 永远 NULL | **用普通 FTS5 表**,text 列存截断原文 4KB |
| enable 持锁扫描死锁 | 锁内再开锁 → 卡死 | **RLock** + 真扫描放锁外 + `_flush` 自锁 |
| DELETE 清 FTS5 | 与并发写互锁 | **DROP+重建**(范式见 engine.py:287) |
| CJK unicode61 不分词 | 中文整词查不到 | **应用层 tokenize** unigram + bigram + ASCII 词 |
| URL 同文章不同 url | 不同追踪参数产生多份 | `_normalize_url()` 剥 scene/clicktime,留 `__biz/mid/idx/sn/chksm` |
| Type!=5 的 appmsg | 误把文件/小程序当文章 | `parse_appmsg_xml` type 子字段校验,非 5 返 None |
| publisher 多结构 | `<sourceusername>` 嵌套位置不定 | multi-fallback:item/source → item/datadesc/source → datadesc.text → item.text → itertext |

---

## 6. CLI 命令(`python -m prisirmp <cmd>`)

| 命令 | 作用 | 平台差异 |
|------|------|---------|
| `status` | 列出账号 / 索引数 / last_index | — |
| `search "<q>"` | 返 top N 文章 `{url, title, publisher, ts, snippet}` | — |
| `clear` | DROP+重建(保留账号配置) | — |
| `verify` | 跑 E2E 自检(14 步) | — |
| `extract` | 调 `wcdb-key-tool extract` 拿 key | Win=CNG / Linux=GDB |
| `decrypt --src=...` | 调 `wcdb-key-tool decrypt` 解 Message_*.db → 缓存 | Win/MacOS/Linux 全 wcdb 支持 |
| `index` | 解密 db 解析 Type=49 → 入 articles + FTS5 | — |

### 6.2 安全红线

- **默认不解密**:`extract`/`decrypt` 必须用户显式 CLI 调用(零静默行为)
- **解密缓存目录隔离**:`%LOCALAPPDATA%\prisirmp\cache\<account_id>\`(Win)/ `~/.local/share/prisirmp/cache/<account_id>/`(Linux),**不入主项目**
- **status 不打印原文**:只返元数据
- **search 只返截断 snippet**:不出本机索引,无原文外发通道
- **`clear` 可逆**:只清 articles 不清 accounts,重 `index` 即恢复
- **密钥存本地 meta 表**:用户显式 `--consent` 后续加(对齐 [[prisIrAI-perm-gate-v1]] 模式)

---

## 7. wcdb-key-tool 子进程调用

```python
# 路径查找顺序
PATH → ~/.local/share/prisirmp/toolbin/
    → ~/.prisirmp/toolbin/
    → ./prisirmp_toolbin/

# extract
[bin, "extract"]  # 输出抓最长 64 hex 串

# decrypt
[bin, "decrypt", "-k", key_hex, "-d", src_dir, "-o", dst_dir]
```

**为什么不内嵌解密**: wcdb-key-tool 走 CNG (Win) / OpenSSL ctypes (Linux) / Mach-O 静态分析 (macOS) 三套平台路径,任何一处都对应当前微信内核版本;内嵌需同时维护三套 + 同步适配,成本 10x。**调子进程**,出问题用户更新 wcdb-key-tool,我们 release 节奏解耦。

---

## 8. Phase 路线

| Phase | 内容 | 决策点 |
|-------|------|-------|
| **0(已 ship)** | 本地 CLI + FTS5 + E2E + 单元测试 | — |
| **1(2 周试用)** | 用户跑 `extract → decrypt → index → search` 验召回质量;装包(PyInstaller)看日常 |
| **2(可选)** | 接 PrisirAI:`web_search.register_provider("wx_recall", fn)`;零耦合,RRF 自动融合 | Phase 1 用爽了才做 |
| **3(更后)** | 抓全文兜底([wechat-article-fetch-ua-bypass]];web_fetch.tune per-domain fetcher 已 ship);增量监听;UI | 用户拍板 |

---

## 9. E2E 验证(2026-09-24 ship 时跑通)

```bash
$ python tests/test_prisirmp.py
============================= test session starts =============================
collected 11 items
tests/test_prisirmp.py::test_parse_xml_basic PASSED                       [  9%]
tests/test_prisirmp.py::test_parse_xml_cdata PASSED                       [ 18%]
tests/test_prisirmp.py::test_parse_xml_non_article PASSED                 [ 27%]
tests/test_prisirmp.py::test_parse_xml_truncated PASSED                   [ 36%]
tests/test_prisirmp.py::test_url_normalize PASSED                         [ 45%]
tests/test_prisirmp.py::test_tokenize_consistency PASSED                  [ 54%]
tests/test_prisirmp.py::test_engine_upsert_search PASSED                  [ 63%]
tests/test_prisirmp.py::test_engine_clear_rebuild PASSED                  [ 72%]
tests/test_prisirmp.py::test_engine_persistence PASSED                    [ 81%]
tests/test_prisirmp.py::test_engine_accounts PASSED                       [ 90%]
tests/test_prisirmp.py::test_cli_status_help PASSED                       [100%]
============================= 11 passed in 0.70s ==============================

$ python -m prisirmp verify
[ 1] 空库 status: enabled=False count=0
[ 2] upsert 5 篇 → 入库 5
[ 3] status: count=5 last_index=1790220555
[ 4] search('深度学习') total=1 → ['深度学习入门:从感知机到 Transfo']
[ 5] search('分布式 一致性') → ['分布式系统一致性:Paxos 与 Raf']
[ 6] search('Python') → ['Python 异步编程实战']
[ 7] 空 query → total=5
[ 8] 重入库 5 篇 → 5 (url UNIQUE 应被替换,count 不涨)
[ 9] parse_appmsg_xml 边界用例 4/4
[10] URL 归一化: True
[11] clear → count=0
[12] 重启后 status: count=5 last_index=1790220555
[13] accounts 读回: [('acc_a', 1000), ('acc_b', 2000)]
[14] tokenize 一致: tokens[0]='深' match='"深度" AND "度学" AND "学习"'

[PASS] prisirmp 端到端自检 14 步全过
```

---

## 10. Rollback

- `prisirmp/` 全部为新增文件,无现有代码改动 → **删目录即 100% 回滚**
- 不用 PyInstaller(Phase 0 急的话才打)、不要改装包、不要改 web_search/capability
- Phase 1(用两周)决定是否进 Phase 2(接 PrisirAI)前,PrisirAI 主线 0 改动

---

## 11. Risks

| 风险 | 影响 | 缓解 |
|------|------|------|
| 微信版本更新 wcdb-key-tool 失效 | 中 | wcdb-key-tool README 承诺 ELF 静态分析自动适配;Win 4.1+ 走 CNG 只读扫描较稳;失败时错误信息清晰引导升级工具 |
| `prisir_fcontent.tokenize` 不能离线复用 | 低 | 已落 **直接拷贝**(80 行纯函数,import 隔离),与 fcontent 解耦 |
| 用户没 install wcdb-key-tool | 低 | `find_wcdb_key_tool()` 给出明确下载指引 + GitHub release 链接 |
| Type=49 appmsg XML 字段变动(微信升级) | 中 | `parse_appmsg_xml` 容错:缺字段返 None + 警告日志;不退化整套 |
| Windows 多用户/多账号 wx 路径不一致 | 低 | 自动探测 `%USERPROFILE%\Documents\xwechat_files` + 提示手动 `--src`;status 报告每个账号独立 article 数 |
| Phase 2 接合入时 fcontent.tokenize 已重构 | 低 | prisirmp 已自带 tokenize.py(拷贝版),零依赖 |

---

## 12. Memory 落点

- [[prisirmp-wechat-recall]] — 描述 + 关键陷阱(parse XML / wcdb-key-tool 子进程 / 不合入主线的设计)
- 链接 [[prisir-fcontent-engine]] — FTS5 schema / RLock / DROP+重建 范式来源
- 链接 [[wechat-article-fetch-ua-bypass]] — Phase 3 抓全文兜底参考