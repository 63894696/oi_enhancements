---
name: p3-9-music-song-pool-v2-shipped
description: P3.9 — song_pool v2 接入 (841 行 14 标签) + 9 列元数据 + id 显式 spXXX + duration_sec 显示用
metadata:
  type: project
---

P3.9 是 P2.5+23(真歌名池 v1)+ P2.5+24(music Vue 重写)之后的歌池数据升级 ship。subagent 已 ship `song_pool_v2.csv`(841 行 14 标签),本次 ship 把 song_pool 从 v1(388 行 6 标签)切到 v2(841 行 14 标签),保持外部 API 与 favorite/download 流程不变。

## 决策背景

P2.5+23 ship 后用户提了:
> 「我们手动输入的歌曲随机列表,能抓取更多类型(抛开 xx 平台或者最近时间这种因素命名的标签)补充到 999 吗?」

**3 项拍板**:
- 数据源 = 派 subagent 抓新歌 + AI 分类(已 ship 841 行 / 14 标签)
- 接入 = 替换 `song_pool_template.csv` 为 v2 版本,迁移旧 favorite/download 元数据
- 字段 = `id, title, artist, tag, duration_sec, is_favorite, download_count, play_count, last_played_iso`(9 列,对比 v1 的 3 列)

## v1 vs v2 区别

| 维度 | v1 (P2.5+23) | v2 (P3.9) |
|------|---------------|-----------|
| 行数 | 388 (实测 381 unique) | 841 (实测 840 unique) |
| 标签数 | 6 (ACG神曲/熬夜修仙/巴士随身听/古风/华语/欧美) | 14 (流行/古风/粤语/民谣/纯音乐/摇滚/电子/R&B/嘻哈/世界音乐/影视 OST/儿童/圣诞/怀旧金曲) |
| 列数 | 3 (歌名/歌手/标签) | 9 (id/title/artist/tag/duration_sec/is_favorite/download_count/play_count/last_played_iso) |
| ID 格式 | sha1(title\|artist)[:12] | 显式 sp001..sp841 |
| 时长 | 无 (PlayerService 跑 audio.duration) | 估算 duration_sec (从 v2 CSV 直接读,前端做 fallback) |
| BOM | 有 (UTF-8-sig) | 无 |

## 14 标签分布 (subagent 实测)

| 标签 | 实际 | 目标 | 偏差 |
|---|---|---|---|
| 怀旧金曲 80-00 | 157 | 80 | +77 (溢出) |
| 粤语 | 86 | 80 | +6 |
| 纯音乐 | 80 | 80 | ✓ |
| 流行 | 77 | 100 | -23 |
| 影视 OST | 55 | 60 | -5 |
| 古风 | 53 | 80 | -27 |
| 世界音乐/民族 | 51 | 50 | +1 |
| 电子 | 50 | 50 | ✓ |
| 摇滚 | 49 | 50 | -1 |
| 民谣 | 48 | 50 | -2 |
| 儿童/胎教 | 45 | 40 | +5 |
| 嘻哈 | 31 | 40 | -9 |
| 圣诞/节日 | 30 | 30 | ✓ |
| R&B | 29 | 40 | -11 |

总计 841 (目标 830 ±50,实际 +11 略高)。

## 实现

### 1. 文件切换
- `mv song_pool_template.csv song_pool_v1.csv` (备份)
- `cp song_pool_v2.csv song_pool.csv` (主用)
- `song_pool_v2.csv` ship 后可删(同船),只留 `song_pool.csv` 主 + `song_pool_v1.csv` 备份

### 2. song_pool.py 重写
**新字段 (SongMeta dataclass)**:
```python
@dataclass
class SongMeta:
    id: str            # v2 = spXXX 显式;v1 = sha1[:12]
    title: str
    artist: str
    tag: str
    duration_sec: int = 0       # P3.9 新增
    is_favorite: int = 0
    download_count: int = 0
    play_count: int = 0
    last_played_iso: str = ""
```

**新 _parse_csv_rows**:
- `encoding="utf-8-sig"` (容 v1 BOM + v2 无 BOM)
- Schema 探测:`header[0].lower() == "id"` → v2 分支
- v2 分支:9 列解析,显式 id,duration_sec 用 `_safe_int(row[4])` 容错
- v1 分支:3 列 + `_safe_id(sha1)` 派生 id,duration_sec=0

**新 _safe_int helper**:
```python
def _safe_int(s, default: int = 0) -> int:
    if s is None: return default
    s2 = str(s).strip()
    if not s2: return default
    try: return int(float(s2))  # 容 "269.0"
    except (ValueError, TypeError): return default
```

**dedup 改为按 id 去重** (防御性):
- v2 subagent 给 `sp138` 重复 × 2 首歌 (一千个伤心的理由 + 一路上有你)
- dedup 按 id 保第一行 + warn 日志记录 dup_ids

### 3. 前端 store 加 duration_sec fallback (player.ts)
- `playById`:`duration: song.duration_sec ?? song.duration ?? 0`
- bootstrap 队列:同样 `duration_sec ?? duration ?? 0`
- 效果:点歌「晴天」→ progress bar 立即显示 04:29 (而不是等 mock.js 拉 audio.duration)

### 4. 测试改造
**保留** (v2 仍适用):
- `test_load_visible_is_60` (visible 60 不变)
- `test_visible_is_substring_of_all` (不变)
- `test_no_dup_in_all` (不变 — 但需要 dedup 修复)
- `test_all_have_title` (不变)
- `test_shuffle_produces_different_orders` (不变)
- `test_list_tags_only_unique` (不变)
- `test_api_songs_get / respin / tag_filter` (路由不变)

**改写**:
- `test_load_count`:[380, 388] → [820, 850]
- `test_id_is_stable` (sha1 12 hex) → `test_id_is_v2_explicit` (spXXX 格式)
- `test_immortals_doublequote_parsed` (v1-specific) → `test_qingtian_exists` (sp001 必在)
- `test_tags_present`:ACG神曲/熬夜修仙/巴士随身听/古风 → 流行/古风/纯音乐/电子 (新增 NOT IN v1-specific 检查)
- `test_list_visible_tag_filter`:tag=ACG神曲 → tag=古风

**新增**:
- `test_v2_csv_format_loaded` (≥800 首 duration_sec>0 + sp001=晴天 必 dur=269)
- `test_id_is_v2_explicit` (≥820 spXXX id)

## 关键发现 & 修复

1. **subagent bug: sp138 重复** — 给一千个伤心的理由和一路上有你都分配了 sp138
   - 防御性修复:song_pool.load() 按 id 去重 + warn 日志 (而不是强求 subagent 改 CSV)
   - 一路上有你被丢弃 (用户后续发现可手动重补到 sp842)

2. **API 字段命名差异** — 后端 SongMeta 新字段是 `duration_sec`,前端原本读 `song.duration`
   - 兼容修复:`duration_sec ?? duration ?? 0`
   - 比直接 rename 字段名风险小(老 favorites cache 仍有 duration)

3. **v1 兼容保留** — `_parse_csv_rows` 走 header[0]=='id' 探测,v1 路径仍可走
   - 测试如需,可 `SongPoolCatalog(csv_path=Path("song_pool_v1.csv"))` 测试 v1

## 测试结果

```
pytest tests/test_song_pool_and_favorite.py -v
27 passed (含 v2 5 新测试 + v1 兼容测试 + API 路由测试)
```

```
pytest tests/test_song_pool_and_favorite.py \
       tests/test_music_lyric_vue_p26.py \
       tests/test_music_lyric_p31.py \
       tests/test_music_lyric_p32.py
113 passed (27 + 31 + 19 + 36)
```

```
vite build
lyric.js 4.46→7.53 KB (P3.1+P3.2 已 ship,P3.9 不改)
main.js 19.60→19.63 KB (+30 字节,duration_sec ?? 三元运算)
vue-tsc 0 error
```

## E2E 验证步骤

1. 起 Electron 壳:`node prisiragent-shell/main.js`
2. 托盘 → 🎵 音乐 → 打开
3. 期望:
   - 60 首可见 (随机洗)
   - 14 个标签过滤条 (含「流行/古风/纯音乐/电子」)
   - 「晴天」标题显示时长 04:29
   - 进度条 100% = 04:29 (而非 0:00 + 等待 audio.duration)
4. 点播「晴天」 → 进度条立即显示 100% 上限
5. 切「古风」标签 → 期望看到周杰伦《青花瓷》(sp003)
6. 收藏 / 下载 sp001 → cache/晴天.mp3 出现

## 与既有 ship 的关系

```
2026-10-03  P2.5+23  真歌名池 v1 + 收藏/下载 ship (388 行 6 标签)
2026-10-03  P2.5+24  music Vue 3 + Pinia 重做 (主 Vue 工程)
2026-10-03  P2.5+25  桌面歌词独立窗 ship
2026-10-03  P2.5+26  alwaysOnTop/lockDrag + 状态持久化
2026-10-03  P3.1+P3.2  歌词窗体验 ship (dc3b83a)
2026-10-03  本计划    P3.9 song_pool v2 接入 (841 行 14 标签)   ← 本次 ship
```

后续 (本 ship 外):
- **v2 清理**: ship 后 `rm song_pool_v2.csv`,只留 `song_pool.csv` 主 + `song_pool_v1.csv` 备份
- **P3.10** (待拍板): v2 CSV 真歌曲的 lyric 实际可拉性验证 (目前 sp001..sp841 都只是 title/artist,duration 是估算;后续看 LRC / lyric provider 命中率)
- **P3.11** (待拍板): 备份 v1.csv 删除决策 (用户说「不」就保留)
- **后续第二批新歌** (待用户拍板): subagent 再抓 158 首达 999

## 关键文件

| 文件 | 类型 | 行数 |
|------|------|------|
| `companion/music/song_pool.py` | 改 | +60 (新字段 + 9 列解析 + id dedup) |
| `companion/music/song_pool.csv` | 新 | (cp from v2,841 行 14 标签) |
| `companion/music/song_pool_v1.csv` | 备份 | (mv from template,388 行 6 标签) |
| `companion/static/music-vue/src/stores/player.ts` | 改 | +6 (duration_sec fallback × 2) |
| `tests/test_song_pool_and_favorite.py` | 改 | +30 (v2 断言 + 5 新测试) |

总计 2 改 1 新 1 备份,1 commit。