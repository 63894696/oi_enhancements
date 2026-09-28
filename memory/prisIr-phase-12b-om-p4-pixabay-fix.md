---
name: prisIr-phase-12b-om-p4-pixabay-fix
description: Phase 12b OM-P4 Pixabay 修复 — 删瞎编的 search_music + search_images 真集成 + scoring 同步(2026-09-28)
metadata:
  type: project
---

# Phase 12b OM-P4 Pixabay 修复 ship(2026-09-28)

承接 [[prisIr-phase-12-om-p4-free-resources]] + 用户实测 key + 官方文档调研 + 二次真调确认。

## 关键发现(用户原话 + 实测)
「我获得了pexabay的key并设置在系统用户环境变量,请获取并访问https://pixabay.com/api/docs/了解测试可用性」

**实测结果**:
| 端点 | 实测状态 |
|------|---------|
| `/api/?key=…&q=test` | ✓ 200,total=1194 真实 |
| `/api/videos/?key=…&q=cat` | ✓ 200,total=1096 视频,duration=11s,4 quality URL |
| **`/api/audio/?key=…&q=test`** | **❌ 403 Forbidden — 端点不存在** |
| `X-RateLimit-Remaining/Limit/Reset` | **99/100/60s**(实测,Pixabay 速率是 100/60s,非官方文档说的 5000/小时) |

**根因**:Phase 12 ship 的 `pixabay_client.search_music()` 调用 `/api/audio/`,我**自己编了一个端点**。

**用户纠正(2026-09-28)**:**Pixabay 网页 https://pixabay.com/music/ 有海量 BGM 可下载,但公开 REST API 不开放 Music 端点**。我之前笼统说「Pixabay 没音乐」也不准 — 是「API 没音乐」。网页有 Music + Sound Effects + Photos + Videos + Illustrations + Vector 等多分类,但 API 只开放 images + videos。这是 ship 前的「网页 vs API 区分」缺位。

## 修复内容

### 1. pixabay_client.py 重构(~280 行)
- **删除** `search_music()` + `BGMHit` 类(瞎编)
- **删除** `PIXABAY_AUDIO_URL` 常量
- **新增** `search_images(query, image_type, limit)` — 真调 `/api/?image_type=`,返 `ImageHit`(id/tags/width/height/previewURL/webformatURL/largeImageURL/pageURL/user/likes)
- `search_videos` 升级:video URL 从嵌套 dict 抽 `{quality: url}`;加 `page_url/user/downloads` 字段
- `probe_key` 升级:parse `X-RateLimit-Limit/Reset`(原本只读 Remaining);`per_page=1` → `3`(Pixabay 最小有效值,实测 per_page=1 返 400 out of valid range)
- 文档注释改:`5000/小时` → `100/60秒`(实测);声明「Pixabay 没有音频 API」

### 2. free_resource_fetcher.py 改 `free_bgm` + 加 `free_stock_image`
- `free_bgm` 不再调 search_music,改走 `archive_org_client.search_videos(mediatype="audio")`(archive.org 公开 mp3)
- **新增** `free_stock_image(query, image_type, limit)` facade
- `free_stock_video` dedup 逻辑修正:Pixabay 用 `id`,archive 用 `identifier`,字段名不同需容错

### 3. video_provider_scoring.py 同步
- `pixabay_music` provider 不删(避免 OM-P2 测试全断),但把 `availability=1.0/quota=0.85` 改成 `availability=0.0/quota=0.0` → `pick_best` 自动判 ineligible
- `pick_best('music')` 实测:从原选 pixabay_music 改为选 fma_music ✓
- description 标注「Pixabay 无音频 API,不可真调」

### 4. 测试升级 13 → 18
新增/改写:
- **#7 probe_key()** — 真探测返 99/100,带 rate_limit_limit/reset 字段
- **#8 search_music 已删除** — assert 不存在
- **#9 search_images 真调** — `cat` 返 3 hits,首条 2064x1410,tags=['cat','animal','cat portrait']
- **#10 search_videos 真调** — duration=11s,videos keys=['large','medium','small','tiny']
- **#13 archive.org audio** — 新增(为 free_bgm 改路径补证据)
- **#16 free_bgm → archive.org audio** — 验证改路径生效
- **#17 free_stock_image** — 新增 facade 测试

## 测试结果(18/18 绿,含 5+ 次真 HTTP/WS)

```
✓ #1-5 edge-tts(中文+英文+空文本)
✓ #6 pixabay key 探测(5777…4ba4 34 字符)
✓ #7 probe_key() 有效,剩 99/100 (重置 60s)
✓ #8 search_music / BGMHit 已删除
✓ #9 search_images 'cat' → 3 hits(2064x1410)
✓ #10 search_videos 'cat' → 3 hits(duration=11s)
✓ #11-13 archive.org(可用 + movies + audio)
✓ #14 status 聚合(edge_tts/pixabay/archive 都 True)
✓ #15 free_tts 真调(18576 字节)
✓ #16 free_bgm → archive.org audio(3 hits)
✓ #17 free_stock_image 'cat' → 3 hits
✓ #18 free_stock_video → 3 hits
```

回归全绿:
- Phase 11 OM-P3 + 11-H:**22/22 绿**
- Phase 10 OM-P2:**13/13 绿**(#12 自动从 pixabay_music 落到 fma_music)
- Phase 9 OM-P1 + MA-P1:**14/14 绿**

## 累计测试 **229**

Phase 12 (224) + Phase 12b 新增 5 测(#8 删 + #9/#10/#13/#16/#17 真调) = 229

## 关键经验

1. **用户给了真 key** + 主动验证文档,**比我自查更彻底**。直接发现瞎编端点。
2. **真调之前不要假定 schema** — `search_music` 我臆造了 BGMHit 字段结构,官方压根没这响应。
3. **区分「网页有」vs「API 有」** — Pixabay Music 在 https://pixabay.com/music/ 可下,但 API 不开放。下次设计前要先确认是网页能力还是 API 能力。
4. **scoring 注册 ≠ 真集成** — `pixabay_music` 在 Phase 10 注册时是合理的(假设有 API),现在必须显式标 availability=0 让 pick_best 跳过,否则会被「理论最高分」误导。
5. **Pixabay 速率 100/60s(实测)非 5000/小时(官方文档)** — 文档旧或不准,实测为准。
6. **Pixabay `per_page` 最小 3(实测 1 返 400)** — 设计 probe 时踩坑。
7. **BGM 不必硬塞 Pixabay** — archive.org 公开音频是真存在的零依赖来源,跟 Pixabay 的 image/video 形成互补;真要 Pixabay Music 只能走手动下载到本地。

## 关联 / 引用

- [[prisIr-phase-12-om-p4-free-resources]] — Phase 12 原 ship(引入了瞎编的 search_music)
- [[prisIr-phase-10-om-p2-scoring]] — OM-P2 注册了 pixabay_music provider(quality=0.75 占位)
- [[prisIr-phase-11-om-p3-pre-compose]] — OM-P3 预算校验(suggest_replacements 会查 pixabay_music 的 cost_per_call=0)
- `prisIr_work/pixabay_client.py` — 重构后客户端
- `prisIr_work/free_resource_fetcher.py` — facade free_bgm 改路径
- `prisIr_work/video_provider_scoring.py:220` — pixabay_music availability=0
- `tests/test_phase_12_om_p4_free_resources.py` — 18 测试全绿

**How to apply:**
- 用户问「Pixabay 能下音乐吗」→ 网页能下(https://pixabay.com/music/),**API 不能**(无 /api/audio/);程序化请用 `free_bgm` 走 archive.org audio;手动下到本地后用本地路径
- 用户问「Pixabay 速率上限」→ 实测 100 请求/60 秒(看 X-RateLimit-* headers)
- 用户问「Pixabay 能下图片吗」→ 能,`search_images(query, image_type='all'|'photo'|'illustration'|'vector')` 返 webformatURL/largeImageURL
- 用户问「OM-P4 BGM 怎么走」→ `free_resource_fetcher.free_bgm(query, limit)` → archive.org audio mediatype
- pick_best('music') 自动选 fma_music(Pixabay 占位 availability=0)
- 看到「某服务有 X 功能」,先确认是**网页能力**还是 **API 能力**;API 通常比网页开放范围小