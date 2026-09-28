---
name: pixabay-no-audio-api
description: Pixabay 网页有 Music/Photos/Videos 多分类,但公开 REST API 只开放 images + videos,Music 只能手动下(2026-09-28 用户纠正)
metadata:
  type: reference
---

# Pixabay API vs 网页 区别(2026-09-28 用户纠正)

## 核心事实
Pixabay **网页**内容丰富(https://pixabay.com/),多个分类都可下载:
- Photos / Illustrations / Vector(图片)
- Videos(视频)
- **Music**(音频,海量免版税 BGM)
- Sound Effects(音效)
- ...等其它分类

但**公开 REST API**(https://pixabay.com/api/docs/)**只开放 2 个端点**:
- `/api/` — 图片(image_type: all/photo/illustration/vector)
- `/api/videos/` — 视频(mp4 直链,large/medium/small/tiny 4 档)

**Music 在 API 端**不开放。`/api/audio/` 返 403 Forbidden。

## 校正说明
我之前(Phase 12 OM-P4)在 pixabay_client.py 写 `search_music()` 调 `/api/audio/`,
是从「网页能看到 Music」推断「应该有 API」— **推断错了**。
2026-09-28 用户纠正:Pixabay 网页的 Music 是有的,但 API 不开放,只能手动下。

## 实测证据(用户 PIXABAY_API_KEY 配好后)
- `/api/?key=…&q=test` → 200,total=1194 images
- `/api/videos/?key=…&q=cat` → 200,total=1096 videos
- `/api/audio/?key=…&q=test` → **403 Forbidden**

## 速率上限(实测)
- `X-RateLimit-Remaining=99`, `X-RateLimit-Limit=100`, `X-RateLimit-Reset=60`
- **100 请求/60 秒**(实测,非官方文档说的 5000/小时)

## per_page 边界(实测)
- 最小值 = 3(`per_page=1` 返 400 `"per_page" is out of valid range`)
- 最大值 = 200

## BGM 真集成方案(PrisirAI 实际路径)
| 优先级 | 来源 | 客户端 |
|--------|------|--------|
| 1 | **archive.org audio**(mp3 公开下载,API 可用) | `archive_org_client.search_videos(mediatype="audio")` |
| 2 | **本地 fma 资源**(占位) | `video_provider_scoring` fma_music provider |
| 3 | **用户自带 mp3 文件** | `local_silence` fallback |
| 4 | **第三方付费 source** | (未集成,文档推荐) |
| ✗ | **Pixabay Music** | **API 不可用**;只能手动到 https://pixabay.com/music/ 下载 → 存本地路径再用 |

## How to apply
- 用户问「Pixabay 能不能下 BGM」→ 网页能下,API 不能;手动 https://pixabay.com/music/ 下载
- 用户问「Pixabay 速率」→ 100/60 秒(probe_key 返回的 X-RateLimit-* headers)
- 设计任何 Pixabay 客户端 → 只用 images + videos,**别碰 audio**
- pixabay_music provider 在 scoring 里 availability=0,避免被 pick_best 误选
- 看到「Pixabay 有 X 分类」时,要区分「网页有」vs「API 有」;API 只 2 个端点

## 实测证据(用户 PIXABAY_API_KEY 配好后)
- `/api/?key=…&q=test` → 200,total=1194 images
- `/api/videos/?key=…&q=cat` → 200,total=1096 videos
- `/api/audio/?key=…&q=test` → **403 Forbidden**

## 速率上限(实测)
- `X-RateLimit-Remaining=99`, `X-RateLimit-Limit=100`, `X-RateLimit-Reset=60`
- **100 请求/60 秒**(实测,非官方文档说的 5000/小时)

## per_page 边界(实测)
- 最小值 = 3(`per_page=1` 返 400 `"per_page" is out of valid range`)
- 最大值 = 200

## BGM 真集成方案(PrisirAI 实际路径)
| 优先级 | 来源 | 客户端 |
|--------|------|--------|
| 1 | archive.org audio(mp3 公开下载) | `archive_org_client.search_videos(mediatype="audio")` |
| 2 | 本地 fma 资源(占位) | `video_provider_scoring` fma_music provider |
| 3 | 用户自带 mp3 文件 | `local_silence` fallback |
| 4 | 第三方付费 source | (未集成,文档推荐) |

**不要再尝试**「Pixabay 找音乐」——这条路不存在。

## How to apply
- 用户问「Pixabay 能不能下 BGM」→ 不能,改用 archive.org audio
- 用户问「Pixabay 速率」→ 100/60 秒(probe_key 返回的 X-RateLimit-* headers)
- 设计任何 Pixabay 客户端 → 只用 images + videos,**别碰 audio**
- pixabay_music provider 在 scoring 里 availability=0,避免被 pick_best 误选