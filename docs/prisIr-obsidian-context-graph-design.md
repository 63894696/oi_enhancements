# prisIr_graph — Obsidian vault wikilink 上下文图谱(设计文档)

## 背景

V7 Go 解决的真问题:**Agent 每次请求都要重新发现上下文 = 数十次搜索 + token 持续消耗 + 漏关系里的关键信息**。V7 Go 用 GPT-5.6/6 云端模型预抽取实体建图,查询时 traverse 不重新发现。

PrisirAI + Obsidian 现状完全成立 —— Agent 找 vault 内容靠 `vault_search`(Everything + AnyTXT 双引擎),每次都是关键词平铺召回,winkilink 跨链的关系被压平成字符串,命中靠运气。

## 设计借鉴 vs 不抄

### 借鉴 V7 Go 的设计哲学

| 借鉴点 | 我们的落点 |
|---|---|
| 「预先建图,traverse 不重新发现」工作流 | `prisIr_graph build` 一次建图,`prisIr_graph_traverse` 反复查询不重发现 |
| 「上下文图谱 vs 长上下文成本低一个数量级」理论 | 节点 1436 / 边 2028 / DB 9.7MB / traverse < 100ms |
| 节点 + 关系 + 证据的三元组语义 | node 存 frontmatter(JSON)+ headings + path;edge 带 line_no + section 当证据源 |
| BFS N-hop 遍历 | `traverse(root, hops, per_hop, direction)` SQL 自连接递归实现 |

### 不抄的事

| 不抄 | 原因 |
|---|---|
| 云端 LLM 走抽取 | PrisirAI 差异化是「本地优先 + 隐私默认」,上传 vault 砸差异化 |
| Zvec / cognee / NetworkX 等图数据库引擎 | SQLite + SQL BFS 已够用,杀鸡用牛刀 |
| 接入 SharePoint/Google Drive | 只走 Obsidian,vault 是唯一数据源 |
| 99.9% 准确率神话 | 零 LLM,wikilink 抽取确定性 100% |

## 架构

```
prisIr_graph/
├── prisIr_graph_store.py       # SQLite schema + CRUD + stats(纯函数)
├── prisIr_graph_build.py       # vault 扫描 + frontmatter/heading/wikilink 解析
├── prisIr_graph_query.py       # BFS traverse + 模糊 search + neighbors
├── prisIr_graph_tools.py       # MCP TOOL_DEFS + HANDLERS(dynamic_registry 自动注册)
├── prisIr_graph.py             # argparse CLI(build/traverse/search/stats/neighbors)
└── test_prisir_graph.py        # 单元 + 集成测试(21 用例全绿)

mcp_prisiragent_server/
└── prisIr_graph_tools.py       # 实际挂在 MCP server 的工具层(从仓库根 import 上述模块)
```

## 数据模型

```sql
CREATE TABLE nodes (
    id           TEXT PRIMARY KEY,    -- MD5(vault-relative-path)[:16]
    title        TEXT NOT NULL,       -- 文件名去 .md
    path         TEXT NOT NULL UNIQUE, -- POSIX 风格,vault-relative
    frontmatter  TEXT,                -- JSON 序列化(default=str 处理 datetime)
    headings     TEXT,                -- JSON 数组
    mtime        REAL NOT NULL,
    indexed_at   REAL NOT NULL
);

CREATE TABLE edges (
    src_id       TEXT NOT NULL,
    dst_title    TEXT NOT NULL,
    dst_id       TEXT,                -- 命中节点时填,孤立链接 NULL
    kind         TEXT NOT NULL DEFAULT 'wikilink',
    line_no      INTEGER NOT NULL,
    section      TEXT,
    FOREIGN KEY (src_id) REFERENCES nodes(id) ON DELETE CASCADE
);
```

## 接口设计

### CLI

```bash
python prisIr_graph.py build                           # 增量(默认 vault)
python prisIr_graph.py build --rebuild --debug         # 强制全量 + 错误样本
python prisIr_graph.py traverse "PrisirAI v2.3.0 ..."  # 起点可以是 title 或 ID
python prisIr_graph.py traverse "X" --hops 4 --direction out --limit 30
python prisIr_graph.py search "Prisir" --limit 10
python prisIr_graph.py stats
python prisIr_graph.py neighbors "PrisirAI v2.3.0 ..." --depth 3
```

### MCP 工具(dynamic_registry 自动接入,119 个工具总数 + 4 新增)

- `prisIr_graph_traverse(root, hops, per_hop, direction, limit)` — BFS N 跳
- `prisIr_graph_search(query, limit)` — 模糊搜节点
- `prisIr_graph_stats()` — 图谱健康度
- `prisIr_graph_build(vault?, rebuild?, debug?)` — 触发增量建图(Agent 默认不调)

## 关键决策

1. **零 LLM 抽取** — Obsidian 自带结构(`[[wikilink]]` 是天然边,frontmatter 是天然属性,heading 是天然层级),1410 md vault 30 秒扫完
2. **三次 pass**:
   - 第一遍:扫文件 → 解析 → upsert node + edges
   - 第二遍:`resolve_orphans` 把孤立 wikilink 按 title 模糊匹配命中已有 node
   - 第三遍:`delete_node_orphans` 对账删除 vault 中已不存在的文件
3. **resolve_orphans 三级 fallback**:精确 == > 前缀 LIKE > 包含 LIKE(Obsidian wikilink 常省略日期后缀,精确匹配率 50% → 模糊后 95%+)
4. **PyYAML datetime round-trip** — `json.dumps(default=str)` 处理 frontmatter 里的 date/created_at 字段
5. **节点 ID 哈希路径** — 文件重命名后 ID 变,通过 title 模糊查询兜底
6. **孤立链接保留** — `dst_id=NULL` 仍入边表,traverse 返回 `resolved=false`,用户可看到「PrisirAI 装包 v2.2.0」尚未建笔记

## 实测数据(2026-09-23 跑在 1436 md 真 vault)

| 指标 | 值 |
|---|---|
| 节点数 | 1436 |
| 边数 | 2028 |
| 真正孤立边 | 956(47%) |
| 解析后孤立 | 0 错误,解析中实际填上 1072 条 dst_id |
| DB 大小 | 9.7MB |
| 首次 build | 26-30s |
| 增量 build(mtime 未变) | 6.1s |
| Traverse 2 跳典型查询 | < 100ms |
| 出度最大节点 | 41 个 wikilink |
| 入度最大节点 | 59 个 back-link |

## 边界与留口子

### 本 plan 不做(留口子给后续)

| 方向 | 描述 | 触发条件 |
|---|---|---|
| **B** | 本地 bge-small-zh-v1.5 抽取实体/关系 | 用户需要搜「概念级」而非「笔记标题级」时 |
| **C** | 网页/微信/聊天记录 source 类型接入 | 用户开始跨载体引用(如把微信文章链接当 vault 节点) |
| **D** | Traverse 结果注入 OIMemory L2 recall | 产品决定(参考 [[prisir-memory-fts5-recon]] 已定调「不做自动注入」,但 traverse 是结构化结果,可能开新口子) |
| **E** | 图谱可视化(类似 Mandol) | wfmodal 加 viz 面板时 |
| **F** | Vault 外链爬取(抓 [[不存在的笔记]] 的链接并尝试落 vault) | 用户希望打通「未建笔记」流 |

### 已知限制

1. **vault 内同名文件** — resolve 时取 ID 字典序第一个,不做歧义检测
2. **wikilink alias** — `[[X|Y]]` 只取 X,Y 当前不入 metadata
3. **embed/链接块代码** — 当前正则不过滤代码块内的 `[[ ]]`,可能会误抽
4. **frontmatter 嵌套结构** — PyYAML 解析后整 dict JSON 化,反序列化时类型信息丢(`date → str`)

## 关联记忆

- [[prisir-graph-shipped]] — ship 详情 + 实战数据
- [[prisir-memory-fts5-recon]] — 上一轮定调「OIMemory 不升级 + 只做 vault 引导」,本 plan 是其下一步
- [[prisir-findex-engine]] / [[prisir-fcontent-engine]] — 文件名/全文侧检索,本图谱是关系侧,三者正交

## 文件清单

- `prisIr_graph.py` / `prisIr_graph_store.py` / `prisIr_graph_build.py` / `prisIr_graph_query.py`(仓库根)
- `prisIr_graph_concept.py` — B.v2 概念级搜索(L0/L1/L2 + 缓存)
- `prisIr_graph_import.py` — C 网页/微信 import(fetch_web/fetch_wechat + 落盘 + 触发 build)
- `mcp_prisiragent_server/prisIr_graph_tools.py`(MCP 接入层,7 工具)
- `test_prisir_graph.py`(仓库根,21 用例)
- `test_prisir_graph_concept.py`(仓库根,7 用例)
- `test_prisir_graph_import.py`(仓库根,10 用例)
- `memory/prisir-graph-shipped.md`(关联记忆)

---

## 方向 B.v2 — 按需 LLM 抽取(2026-09-24 ship)

### 触发

方向 A ship 后只覆盖「已知字符串」查询(标题级 wikilink 命中)。用户问「vault 里讲过哪些『自治系统』概念」时,概念词不命中任何 wikilink 标题 → L0 miss → 关键词平铺召回(走 vault_search)回到起点。

**哲学**:V7 Go Luna「按需抽取」+ agent 把 LLM 成本花在「有用户问的查询」上,无人值守时零成本。

### 三层 fallback

```
L0 — wikilink graph(已 ship,零 LLM)
  命中 ≥ min_l0_hits(默认 3)→ 直接返回
  ↓ miss
L1 — 按需 LLM 抽取(本 plan 新增)
  候选笔记选取:title 精确 > 前缀 LIKE > 包含 LIKE 三级 fallback,top K=20
  LLM 抽:本地 bge-small-zh-v1.5(后台预热)优先,失败兜远程 Bailian text-embedding-v4
  cosine 排序 → 返 top limit
  跨进程 SQLite 缓存:key = MD5(model + query + candidates_mtime_hash),7 天 TTL
  ↓ vault 文件被改(候选节点 mtime 变)
L2 — 缓存自然失效,自动重新抽取(无需手动 invalidate)
```

### 新 MCP 工具

- `prisIr_graph_concept_search(query, limit, model_preference, candidate_k, min_l0_hits)`
  - `model_preference`: `local` 强制本地 bge / `remote` 强制远程 Bailian / `auto` 本地优先失败兜远程(默认)
  - 返回字段:`layer`(`L0_wikilink` / `L0_miss_no_candidates` / `L1_cache_hit` / `L1_fresh` / `L1_no_model`)+ `model_used` + `cosine` 分数

### 关键设计决策

1. **远程 LLM 授权语义**:信任已有 `BAILIAN_API_KEY` = 默认放行。用户主动配 key 是显式同意,不弹卡。
2. **本地 fastembed 后台预热**:模块导入即起 daemon 线程;加载失败静默,后续走远程兜底。bge 首次 ~30s 期间,查询走 bailian;本地 ready 后自动切换。
3. **mtime 失效**:MIME hash = MD5(sorted([node.mtime]))。增量 build 更新 node.mtime → 自动触发 cache key 变更 → 下次查询重新抽取。
4. **信任边界**:本地 bge 用 `BAAI/bge-small-zh-v1.5`(B.v2 ship 改用此模型名);远程走 `embedding_utils.bailian_embed` 已有进程内 7 天 TTL 缓存。
5. **零成本优先**:L0 wikilink 命中 ≥ 3 时根本不调 LLM,平均 vault 查询成本远低于 kg_query 的全量 embedding 流。

### 复用既有

- `mcp_prisiragent_server/embedding_utils.py` — `bailian_embed(text)` / `bailian_cosine(a, b)`,远程 embedding 直接调
- `mcp_prisiragent_server/v030_local_embed_full.py` — fastembed 加载模式参考(改 `BAAI/bge-large-en-v1.5` 为 `BAAI/bge-small-zh-v1.5`)
- `mcp_prisiragent_server/kg_tools.py:216` kg_query_impl — hybrid search 设计参考
- `prisIr_graph_query.py:_query.search` — L0 miss 检测
- `prisIr_graph_store.py:get_node_by_id` — 候选节点 mtime 读取

### 实测

- 7 个新测试用例全绿:L0 命中跳过 L1 / 候选选取 / cache 写读一致 / mtime 变 cache 失效 / 无模型降级 / 候选三级 fallback
- 21 个方向 A 测试全绿(回归通过)
- MCP 工具总数 119 → 120(dynamic_registry 自动接入)

### 留口子(本 plan 不做)

- 方向 C:网页/微信/聊天记录 source 类型
- 方向 D:concept_search → OIMemory L2 recall 注入
- 方向 E:wfmodal 图谱可视化
- 方向 F:vault 外链爬取(`[[未建笔记]]`)
- 候选笔记正文向量预计算缓存(避免每次都重读 vault 文件)

---

## 方向 C — 网页 + 微信公众号 source 接入(2026-09-24 ship)

### 触发

方向 A + B.v2 ship 后,图谱只覆盖用户手动写的 vault md,跨载体引用是零(网页/微信文章无法自动入图)。用户跨载体引用越来越多,需要把外部内容也变成 vault 节点。

### 设计

```
Agent 调 prisIr_graph_import_web / prisIr_graph_import_wechat
  ↓
prisIr_graph_import.fetch_web(url) / fetch_wechat(url)
  ├─ _fetch_html:urllib + headers + timeout(微信走 MicroMessenger UA)
  ├─ _extract_meta / _extract_title / _html_to_markdown(去 script/style/comment)
  │   微信专用:_wechat_extract 截到 <div id="js_content"> 干掉 footer ad
  ├─ _render_frontmatter + _render_md(source: web|wechat, url, title, author, published, fetched_at, status: inbox, tags)
  ├─ _write_to_inbox:vault/00-inbox/{web,wechat}/YYYY-MM-DD-<slug>-<hash6>.md
  └─ _trigger_build_incremental:调 prisIr_graph_build.build_vault 让新节点入图

不做:
  ✗ 反向链接解析(web 节点里的 [[vault 笔记]] 不影响 vault 笔记的入向)
  ✗ 去重(同 URL 多次抓 → 多份 md,留口子 C.2)
  ✗ 图片下载 / PDF / 视频 / 订阅 / 代理池
```

### 新 MCP 工具

- `prisIr_graph_import_web(url)` — 抓任意 http/https 网页 → 落 vault/00-inbox/web/
- `prisIr_graph_import_wechat(url)` — 抓 mp.weixin.qq.com 文章(MicroMessenger UA 绕风控) → 落 vault/00-inbox/wechat/

### 关键决策

1. **不弹卡**:远程抓取是 agent 主动行为(等价于 curl),不需要用户额外授权 — 与远程 LLM 不同。
2. **微信 UA 必用**:`MicroMessenger/8.0.44`,memory/wechat-article-fetch-ua-bypass.md 已验证。
3. **文件名 URL-hash 区分**:`YYYY-MM-DD-<slug>-<hash6>.md`,hash6 = MD5(url)[:6],同 title 不同 URL 必然不同名。
4. **微信正文截到 `<div id="js_content">`**:干掉底部广告/推荐/关注区。
5. **不做反向链接**:web 节点的 outbound `[[vault 笔记]]` 仍正常入图(vault 笔记作为 dst 出现),但 vault 笔记本身的 frontmatter / 内容不被外部 import 修改。留口子方向 C.1。
6. **失败容错**:Content-Length > 5MB / 非 200 / 超时 / 空正文 全部返回 ok=False,落 inbox 兜底 inode + inbox 目录不会被垃圾文件污染。

### 文件

- `prisIr_graph_import.py` — fetch_web / fetch_wechat + HTML 解析 + 落盘 + 触发 build_vault
- `test_prisir_graph_import.py` — 10 个用例全绿(HTML 解析 / slug / fetch_web 落盘 / fetch_wechat UA / 不做反向链接 / 失败路径 / 大文件拦截)

### 实测

- 10/10 测试用例全绿
- smoke test 验证:fetch_web 落 00-inbox/web/*.md,fetch_wechat 走 MicroMessenger UA + js_content 截断
- MCP 工具总数 120 → 122(dynamic_registry 自动接入)

### 留口子

- **C.1**:web 节点 body 走 wikilink 解析反向填充 vault 笔记 metadata
- **C.2**:同 URL 去重检测 / 同 title 合并
- **C.3**:图片下载到 vault 本地 + 重写 markdown
- **C.4**:PDF / 视频 / 订阅 / 定时抓取