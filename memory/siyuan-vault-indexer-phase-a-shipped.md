---
name: siyuan-vault-indexer-phase-a-shipped
description: SiYuan vault 索引器 Phase A 只读扩展 ship — SQLite 直读
metadata:
  type: project
---

# SiYuan vault 索引器 Phase A 只读 ship (2026-10-07)

## 起因

v2 LX-like 调研报告([[lx-music-like-software-survey]]) 拍板 Phase A Top 5 ship 顺序:

LX ✅(已 ship)→ **SiYuan**(下一个)→ Navidrome → Joplin → mpv → Obsidian

SiYuan 用户基数大(中文社区主流本地优先笔记),SQLite 索引库(blocks / blocks_fts5)是公开 schema,直读 = 100% 本地 + 0 上传 + 不动 .sy JSON 源文件。

## 设计取舍

**严格只读 + SQLite readonly mode**:`new DatabaseSync(`file:${db_path}?mode=ro`)` 打开 db,SQLite 拒绝任何 INSERT/UPDATE/DELETE。绝不触碰 .sy JSON 源文件(那是 SiYuan 唯一可信数据,改了会破坏用户 vault)。

**SiYuan 索引库设计**:SiYuan 启动时 `temp/siyuan.db` 从 `data/*.sy` JSON 重建,所以读 db 不需要担心 sync 锁冲突。但反之也成立 —— 读 db 不是 source of truth,改 db 不影响 SiYuan 数据。Phase A 完全合规。

**失败语义优先**:db 不存在 / 打开失败 / node:sqlite 不可用 → 返 `{ ok: false, alive: false, last_error: ... }`。Node 24+ 内置 node:sqlite(Node 22.5+ stable),**无 npm 依赖**。

**4 个 L0 命令**(全部只读,严格不写):
- `siyuan.health` — db 可读 + blocks 总数
- `siyuan.notebooks` — DISTINCT box 列所有 notebook + doc 数
- `siyuan.search {query, limit}` — FTS5 MATCH + snippet() 高亮(<< >> marker)
- `siyuan.block.get {id}` — 按 block ID 拿 markdown + content + tag

**100% 本地**:`siyuanDbPath()` 默认 `~/Documents/SiYuan/<workspace>/temp/siyuan.db`(扫第一个存在 db 的 workspace),可被 `PRISIR_SIYUAN_DB` 环境变量覆盖。

**FTS5 防注入**:`escapeFts()` 把 `"` 替换成 `""`,MATCH `"phrase"` 包裹,避免 query 解析错误或 SQL 注入。

## SiYuan schema 依据

来源:SiYuan GitHub `kernel/sql/database.go` master 分支([SiYuan 数据库功能详解](https://blog.csdn.net/gitblog_00449/article/details/152096972)+ [Siyuan数据库表与字段](https://siyuannote.com/article/1724743405)):

```sql
CREATE TABLE blocks (
  id, parent_id, root_id, hash, box, path, hpath,
  name, alias, memo, tag, content, fcontent, markdown,
  length, type, subtype, ial, sort, created, updated
)
CREATE VIRTUAL TABLE blocks_fts5 USING fts5(
  id UNINDEXED, parent_id UNINDEXED, root_id UNINDEXED,
  hash UNINDEXED, box UNINDEXED, path UNINDEXED, hpath,
  name, alias, memo, tag, content, fcontent, markdown UNINDEXED,
  length UNINDEXED, type UNINDEXED, subtype UNINDEXED, ial,
  sort UNINDEXED, created UNINDEXED, updated UNINDEXED,
  tokenize="siyuan"
)
```

注意:实测用 `tokenize="unicode61"` 替 `siyuan`(siyuan tokenize 是 SiYuan 自定义 unicode 分词,node sqlite 的 fts5 默认不带)。Phase A 通用 unicode61 足够搜中文(unicode61 也分词中文 CJK)。

## 实现

**Node 端 index.js (~190 行)**:无 npm 依赖,用 Node 24+ 内置 `node:sqlite`。

- `siyuanDbPath()`:env 优先,默认扫 `~/Documents/SiYuan/<ws>/temp/siyuan.db` 第一个存在
- `openDb()`:lazy open + readonly mode + 缓存单例(同进程只开一次)
- `probeHealth()`:`SELECT COUNT(*) FROM blocks` 验证可读
- `listNotebooks()`:`SELECT box, COUNT(*), MIN(hpath) FROM blocks WHERE type='d' GROUP BY box`
- `searchBlocks(q, limit)`:`SELECT ... FROM blocks_fts5 JOIN blocks ... WHERE MATCH ? ORDER BY rank LIMIT ?` + `snippet(blocks_fts5, 11, '<<', '>>', '...', 16)`
- `getBlock(id)`:`SELECT ... FROM blocks WHERE id = ?`,不存在返 `block: null`

## 测试

**Node 端**(`__tests__/run.js`):8 单测 + 3 fixture E2E,11/11 全过:

```
✓ escapeFts 双引号转义
✓ siyuanDbPath env 覆盖默认
✓ probeHealth db 不存在 → ok=false
✓ getBlock 空 id → last_error=empty id
✓ getBlock 不存在 id → block=null + ok=true
✓ listNotebooks 无 db → ok=false + alive=false
✓ searchBlocks 空 query → ok + hits=[]
✓ searchBlocks limit clamp [1, 100]

[E2E] 真实 fixture db(8 blocks / 2 notebooks)
✓ E2E probeHealth → alive + blocks_total=8
✓ E2E listNotebooks → 2 个 notebook
✓ E2E searchBlocks "Rust" → ≥2 hits (<<Rust>> 高亮)

[结果] ✓ 11  ✗ 0
```

fixture db 8 blocks / 2 box / 5 doc + 3 标题 / 5 段落,完整 SiYuan schema 复刻。

**Python wrapper** (`tests/test_siyuan_vault_indexer.py`):4 case,默认 3 passed + 1 skipped,设 `PRISIR_SIYUAN_E2E=1` 跑全 4 个,实测全过。

## 文件清单

- `extensions/siyuan-vault-indexer/package.json` — manifest,5 个 L0 权限
- `extensions/siyuan-vault-indexer/index.js` — 实现 + module.exports 测试钩子
- `extensions/siyuan-vault-indexer/__tests__/run.js` — Node 单测 + E2E
- `tests/test_siyuan_vault_indexer.py` — Python pytest wrapper

## 主仓回归

`python seed.py`:1161 PASS(+5 from LX + SiYuan),3 FAIL(老 ship 漏同步,非本扩展引入)。

## 后续 Phase B/C 边界

**Phase B**(待用户开绿灯):写 block — 需 `siyuan.block.create` / `siyuan.block.update` / `siyuan.block.delete` 命令,需 `state.write.siyuan` 权限 + API token(SiYuan 远程 API 用 `Authorization: Token xxx`)。Phase B 必须用户**逐条**勾。

**Phase C**(更远):基于 vault frontmatter + tag 的 AI 主动编排(自动建笔记 + 自动关联 + Calendar 联动)。沿用 P2.5+8 calendar/todo/pomodoro 编排架构。

## 与 PrisirAI 已有能力的关系

| 已有能力 | 关系 |
|----------|------|
| `prisIr-obsidian-context-graph-design` Obsidian vault 图谱 | **互补不重复** — Obsidian 读 markdown vault,SiYuan 读 SQLite db,两者都是 Phase A 笔记索引器 |
| `prisir-fcontent-engine` Python+FTS5 内容搜索 | **互补** — fcontent 是通用 FTS5 引擎,SiYuan 是 SiYuan 专用 SQLite 索引(自带 tokenize="siyuan") |
| `handraw-style` / `free-for-dev` 等扩展 | 无重叠(不是笔记) |

## Why

0 上传红线不能破 SiYuan 索引器。SiYuan vault 是用户知识核心,读 SQLite 索引 = 读本地数据,**绝不**写、绝不**外传**任何 block content。

## How to apply

下次遇到「本地已装某软件,能否做 X 桥」类问题:
1. 先确认软件是否已装 + 数据源路径(`<workspace>/temp/siyuan.db`)
2. 查 schema(`kernel/sql/database.go` 或 source code 关键表)
3. Phase A 只读先行,SQLite readonly mode 打开(`file:...?mode=ro`)
4. 失败语义优先 + env 覆盖 db 路径
5. 测试要 Node 真实端到端 + Python wrapper 默认 skip,env 标志放行
6. Phase B/C 严格 gated 在 Phase A 验证后

相关:[[lx-music-bridge-phase-a-shipped]], [[lx-music-like-software-survey]], [[p3-10-bubble-cancelled-privacy]], [[prisIr-obsidian-context-graph-design]]
