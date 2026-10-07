---
name: calibre-metadata-indexer-phase-a-shipped
description: Calibre metadata.db 索引扩展 Phase A 只读 ship
metadata:
  type: project
---

# Calibre metadata.db 索引 Phase A 只读 ship (2026-10-07)

## 起因

v2 对比研究([[lx-music-like-software-comparison]])P0 借鉴清单第 1 项 — Calibre metadata.db 5 分钟 ship。

**SQLite readonly 原则(原则 3)首次落地**:`new DatabaseSync(`file:${db_path}?mode=ro`, { readOnly: true })` 推广到 Calibre SQLite 库,为 Phase B Calibre / Joplin / Logseq 等所有本地 SQLite 库铺路。

## 设计取舍

**严格只读 + SQLite readonly mode**:Calibre metadata.db 是用户**唯一可信数据**(改 metadata 不动 .epub/.mobi 文件本身,但改 db 会丢失用户整理的 tag / series / cover 等元数据)。我们只 SELECT,**绝不** UPDATE/DELETE/INSERT。

**Calibre WAL journal**:Calibre 用 WAL mode(metadata.db-wal + metadata.db-shm sidecar),readonly mode 读 SQLite 在 WAL 模式下仍可读,且不会触发写。

**5 个 L0 命令**(全部只读):
- `calibre.health` — db 可读 + books_total + authors_total
- `calibre.books.list {limit, offset}` — 列书(按 timestamp 倒序,JOIN authors GROUP_CONCAT)
- `calibre.books.search {query, limit}` — LIKE 模糊搜(Calibre 无 FTS5)
- `calibre.authors.list {limit}` — 列作者(book_count DESC)
- `calibre.tags.list {limit}` — 列 tags(老版本 Calibre 没 data_tags 表,自动 fallback 返空)

**LIKE 防注入**:`%` `_` `\` 三个特殊字符转义(`ESCAPE '\'`),防 SQL 注入 / LIKE 通配符误用。

**100% 本地 + env 覆盖**:`calibreDbPath()` 默认 `~/Calibre Library/metadata.db`,可被 `PRISIR_CALIBRE_DB` 环境变量覆盖。

## Calibre schema 依据

来源:[Calibre manual db_schema.html](https://manual.calibre-ebook.com/develop/en/db_schema.html) + `calibre/library/sqlite.py`:

```sql
CREATE TABLE books(
    id, title, sort, timestamp, pubdate, series_index, author_sort,
    isbn, lccn, path, flags, cover, has_cover, uuid, application_id, marked
)
CREATE TABLE authors(id, name, sort, link)
CREATE TABLE books_authors_link(book, author)  -- 多对多
CREATE TABLE data_tags(id, name, sort)
CREATE TABLE books_tags_link(book, tag)        -- 多对多
CREATE TABLE data_series(id, name, sort)
CREATE TABLE books_series_link(book, series)   -- 多对多
```

注意:
1. `books_authors_link` 多对多 → SQL 里用 `GROUP_CONCAT(a.name, ', ')`
2. Calibre 无 FTS5 virtual table → 搜索走 `title LIKE '%query%' OR author_sort LIKE '%query%'`
3. 老版本 Calibre 没 `data_tags` 表 → 先 `sqlite_master` 探测,缺则返 `{ok: true, tags: []}`

## 实现

**Node 端 index.js (~210 行)**:无 npm 依赖,用 Node 24+ 内置 `node:sqlite`。

- `calibreDbPath()`:env 优先,默认 `~/Calibre Library/metadata.db`
- `openDb()`:lazy open + readonly mode + 单例缓存
- `probeHealth()`:`SELECT COUNT(*) FROM books + authors`
- `listBooks(limit, offset)`:`SELECT ... FROM books b ORDER BY timestamp DESC LIMIT ? OFFSET ?` + `GROUP_CONCAT` join authors
- `searchBooks(q, limit)`:`WHERE title LIKE ? ESCAPE '\\' OR author_sort LIKE ? ESCAPE '\\'`,`%` `_` `\` 三字符转义
- `listAuthors(limit)`:`SELECT a.id, a.name, COUNT(bal.book) FROM authors a LEFT JOIN books_authors_link bal ON ... GROUP BY a.id ORDER BY book_count DESC`
- `listTags(limit)`:先 `sqlite_master` 探测 `data_tags` 表存在,再 GROUP 查询

## 测试

**Node 端**(`__tests__/run.js`):8 单测 + 3 fixture E2E,11/11 全过:

```
✓ calibreDbPath env 覆盖默认
✓ probeHealth db 不存在 → ok=false
✓ listBooks 无 db → ok=false + alive=false
✓ searchBooks 空 query → ok + hits=[]
✓ searchBooks LIKE 转义 % _ 不破 SQL (50% / a_b 注入测试)
✓ listBooks limit clamp [1, 100]
✓ listAuthors 无 db → ok=false
✓ listTags 无 db → ok=false

[E2E] 真实 fixture metadata.db(6 books / 4 authors / 5 tags)
✓ E2E probeHealth → alive + books=6 + authors=4
✓ E2E listBooks 限 3 → 3 本 + 按 timestamp 倒序(1984 → 许三观 → 活着)
✓ E2E searchBooks "三体" → ≥3 hits

[结果] ✓ 11  ✗ 0
```

fixture 6 books(《三体》三册 + 《活着》+ 《许三观卖血记》+ 《1984》) + 4 authors + 5 tags,完整 Calibre schema 复刻。

**Python wrapper** (`tests/test_calibre_metadata_indexer.py`):4 case,默认 3 passed + 1 skipped,设 `PRISIR_CALIBRE_E2E=1` 跑全 4 个,实测全过。

## 文件清单

- `extensions/calibre-metadata-indexer/package.json` — manifest,6 个 L0 权限
- `extensions/calibre-metadata-indexer/index.js` — 实现 + module.exports 测试钩子
- `extensions/calibre-metadata-indexer/__tests__/run.js` — Node 单测 + E2E
- `extensions/calibre-metadata-indexer/package-lock.json` — SDK lock
- `tests/test_calibre_metadata_indexer.py` — Python pytest wrapper

## 主仓回归

`python -m pytest tests/test_calibre_metadata_indexer.py tests/test_siyuan_vault_indexer.py tests/test_lx_music_bridge_status.py`:9 passed + 3 skipped。

## 后续 Phase B/C 边界

**Phase B**(待用户开绿灯):写 metadata — 需 `calibre.books.update` / `calibre.books.add` / `calibre.books.delete` 命令,需 `state.write.calibre` 权限。Phase B 必须用户**逐条**勾。

**Phase C**(更远):基于 tag + series 的 AI 推荐,自动补 tag / 改 series。沿用 N9 music AI 推荐架构(纯本地 + 0 上传)。

## 与 PrisirAI 已有能力的关系

| 已有能力 | 关系 |
|----------|------|
| `prisir-fcontent-engine` Python+FTS5 | **互补不重复** — fcontent 是通用文件内容 FTS5,Calibre 是 SQLite metadata 索引 |
| `siyuan-vault-indexer` SQLite readonly 模板 | **同模板** — SiYuan 先 ship,Calibre 复用 readonly mode + env 覆盖 |
| `lx-music-bridge-status` HTTP 失败语义 | 原则相同,但 Calibre 是 SQLite 不是 HTTP |

## Why

0 上传红线不能破 Calibre 索引器。Calibre metadata.db 是用户花时间打的标签,**绝不能**让扩展误改。SQLite readonly mode = 硬性保险。

## How to apply

下次遇到「本地 SQLite 库」类软件(Calibre/Joplin/Logseq/TriliumNext/Anytype)做 Phase A:
1. 查 schema(用户文档 + 源码 SQLite CREATE TABLE)
2. SQLite readonly mode 打开(`file:...?mode=ro` + `readOnly: true`)
3. 探测表存在(老版本可能没建,先 SELECT name FROM sqlite_master WHERE type='table')
4. LIKE 模糊搜转义 `%` `_` `\`(无 FTS5 时唯一选择)
5. 多对多 join 用 `GROUP_CONCAT` 简化为单字段
6. 测试要 Node 真实端到端 + Python wrapper 默认 skip,env 标志放行

## 与 SiYuan 模板的差异

| 维度 | SiYuan | Calibre |
|------|--------|---------|
| 数据源 | SiYuan 重建的 blocks | Calibre metadata.db |
| 表数 | 1 主表 + 1 FTS5 | 6 主表 + 多对多 join |
| 搜索 | FTS5 MATCH + snippet() | LIKE 模糊 + 转义 |
| 默认路径 | `<workspace>/temp/siyuan.db` | `~/Calibre Library/metadata.db` |
| 主键 | 字符串(2026...id) | INTEGER autoincrement |
| env | PRISIR_SIYUAN_DB | PRISIR_CALIBRE_DB |

**统一模板**已固化:5 步法 = (端口/路径确认) + (失败语义优先) + (env 覆盖) + (测试三件套) + (权限分层)。SiYuan / Calibre 完全沿用,LX 模板微调(URL 而非 DB 路径)。

相关:[[lx-music-bridge-phase-a-shipped]], [[siyuan-vault-indexer-phase-a-shipped]], [[lx-music-like-software-comparison]], [[p3-10-bubble-cancelled-privacy]]