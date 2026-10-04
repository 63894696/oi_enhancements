---
name: p3-10-bubble-cancelled-privacy
description: P3.10b music recognition floating bubble 取消 — 用户隐私顾虑(2026-10-04)
metadata: 
  node_type: memory
  type: project
  originSessionId: 2d15161c-8c4f-49ad-a641-0066f302d833
  modified: 2026-10-04T03:28:27.935Z
---

# P3.10b — music recognition floating bubble 取消(2026-10-04)

## 决策

P3.10b **音乐识别浮泡功能本期不做**。

## 用户的演进路径

1. **初稿**:AudD / acrcloud / ShazamKit / midomi 调研 — 全部需 API key,用户原话「否则不做」
2. **SongRec 调研**:开源 Shazam 协议客户端(`songrec/songrec`),Rust 写,无 api_key。Windows MSI 13.22 MB ship
3. **同意 SongRec 路径**:用户「同意 SongRec 路径」
4. **再次拒绝**(2026-10-04):即便 SongRec 无需 key,`getUserMedia` 录音 + 音频指纹外传 Shazam 远程服务器被以**隐私顾虑** reject
   - 用户原话:「如果不能内置打包,需要通过指引的话,用户还有隐私担忧,干脆这个识别音乐功能不做了」
   - 用户原话:「不做音乐识别功能,不调用麦克风」

## Why — 隐私顾虑

- 桌面应用持续监听麦克风本身是 **隐私敏感** 行为
- 即便 Shazam 协议只外传「音频指纹元组」不外传原始音频,**多数用户不区分**
- 用户的信任阈值:**0 上传/外传**(而不是「只传指纹而非原始音频」)
- 这是用户对产品的整体隐私立场扩展:从「无 Key 调用」→「无 Key 且不外传任何数据」

## How to apply

下次用户(或我自己)提「识别 X」「智能匹配 X」类需要持续监听传感器(麦克风/摄像头/键盘)+ 外传匹配请求的功能:

1. **先看是否纯本地**:有本地数据库吗?(如本地音乐库指纹比对 = 接受)→ 若纯本地,可 ship
2. **若必须外传**:即便服务承诺「不上传原始数据」,**默认拒绝** — 用户对「外传」的容忍度极低
3. **不引导用户去装第三方工具来「装」这个功能** — 用户对「指引安装」的路径也不信任(理由同上,会觉得绕一圈还是隐私顾虑)
4. **替代方案**:走 PrisirAI 现有的本地能力扩展,如视频/音频 metadata(文件名/标签/ID3 信息)而非内容识别

## 关联

- `[[p3-10-toast-recon]]` — 早期调研,AudD 不可行
- `[[p3-10-toast-bubble-recon]]` — 早期调研,bubble + toast 双调研
- `[[p3-10-toast-shipped]]` — P3.10a toast 最终 ship(仅 toast,bubble 取消)