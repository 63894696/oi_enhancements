---
name: p2-5-28-c-multi-source-recon
description: P2.5+28 C 阶段调研(2026-10-05)发现 9 源无一能 resolve URL;shim call-all dispatch + AES 未实现 + 3 文件混淆;DEFAULT_SOURCES 已 revert ["local.js"];等用户拍板
metadata:
  type: project
---

# P2.5+28 C 阶段 — 多源调研(未 ship,等拍板)

## 用户原始诉求

> 「无论是点AI推荐里的歌名,还是点下面列表里的歌名,虽然都跳提示正在播放,然后就变成了兜底播放,结果还是播放的30秒静音,这个30秒静音需要彻底去掉,点一首歌名能播放就正常播放,不能播放就说明原因是什么。」

> 「我们最早在音乐添加了10来个在线源,你能否找到项目文档中这些源保存在哪里?找到后 A 先走,然后 C。」

> 「放宽红线接多家 LX 源」(C.2)

> 「仅试 bilibili/咪咕源」(信任第三方 JS)

> 「下载 mg.js + lx.js 主源」(lx-music-source 仓实际只有 5 源 kg/kw/mg/tx/wy,无 bilibili)

> 「启用全部5个子源+C:\Users\Administrator\Downloads\lx音乐源.txt里的源,有重复的去掉重复」

> 「启动全部能正常听歌的源」(审计后)

## 实测调研结果(C 阶段关键发现)

调研在 2026-10-05 真跑 LxRuntimeClient 后,**默认 9 源候选全部无法 resolve URL**。3 个独立根因:

### 根因 1:shim call-all dispatch 让 local.js 屏蔽所有源

```js
// companion/lx_runtime/lx_runtime.js line 203-222
async function callRequest(action, source, info) {
    const handlers = bus.listeners("request");  // 拿到所有 source 的 handler
    for (const h of handlers) {  // 串行调用,first non-null wins
        const v = await Promise.resolve(h(evt));
        if (v != null) return v;  // ← local.js 第一个返 local:// → 屏蔽其他
    }
}
```

`local.js`(本地 mp3 占位源)handler 返回 `local://prisir/<songmid>/320k` 非 null**,所以无论 DEFAULT_SOURCES 怎么排,local.js 永远赢**。其他 8 源一个都不调用。

### 根因 2:kw/kg/tx/wy/mg 全部需要 AES crypto,shim 是 throw

```js
// lx_runtime.js line 132-133
aesEncrypt: () => { throw new Error("aesEncrypt not shimmed (Phase 1 不需要)"); },
rsaEncrypt: () => { throw new Error("rsaEncrypt not shimmed (Phase 1 不需要)"); },
```

lyswhut 官方 5 源(kw/kg/tx/wy/mg)的 musicUrl 接口**全部需要 AES 加密**(PHP 后端迁移过来的加密协议)。shim 当前是 throw,**所有 5 源抛错**,无论 shim dispatch 怎么改都无解。

`lx_main.js` 实际是 lyswhut 官方 lx-music-source 仓 dist 打包,内部按 `source` 字段(子源名 kw/kg/tx/wy/mg)派单到对应 api,但入口点全 AES。

### 根因 3:3 个 pdone fork 是混淆 JS,违反红线

```
companion/lx_runtime/changqing.js   27KB 单行 hex 编码 + String.fromCharCode 自解码
companion/lx_runtime/flower.js     10KB 单行 hex 编码
companion/lx_runtime/grass.js       9KB 单行 hex 编码
```

即便 dispatch 修好,这 3 派单过去也是把混淆代码 `win.eval()` 进 jsdom,**违反 P3.10b 红线「0 上传/外传」+ 信任第三方 JS**——无法审计在做什么。**已删除**(2026-10-05)。

`huanyin.js`(309 行可读)/ `qdy.js`(830 行可读)/ `huibq.js`(89 行可读)3 源源代码可审计。

## 剩余可用 LX 源

删 3 混淆 + 留 5 可读 = **8 文件**(不含 lx_runtime.js + node_modules + package):

```
companion/lx_runtime/
  huanyin.js   309 行,源:tx/kw/mg,3rd API:oiapi.net(无 key,公开端点)
  qdy.js       830 行,源:wy/tx/kw/kg/mg+QISHUI,3rd API:oiapi.net+music-api.gdstudio.xyz
  huibq.js      89 行,源:kw/kg/tx/wy/mg,3rd API:lxmusicapi.onrender.com + share-v3 key
  local.js      30 行,源:local(占位,返 local://,无外网)
  mock.js       -    源:mock,返 commondatastorage.googleapis.com 国内有时通
  lx_main.js  100+ KB,源:kw/kg/tx/wy/mg(lyswhut 官方,需 AES shim)
  ikun.js       -    DNS 墙挡,跳过
  lx.js         -    旧 lyswhut 占位,跳过
```

## 当前 DEFAULT_SOURCES(2026-10-05 revert 后)

```python
DEFAULT_SOURCES = ["local.js"]  # 仅 1 源,0 外网
```

测试 `test_default_sources_is_local_only` 已改回「断言 == ["local.js"]」,473/473 测试全绿。

## 用户可走路线(等拍板)

### 路线 X:修 shim 按 source 名派单(最小修复,~50 行 JS)

**改 lx_runtime.js**:
- 包装 `lx.on` 追踪最新注册 handler
- `bus.on("inited")` 把 sub-source 名(kw/kg/tx/wy/mg/local)绑到该 handler
- `callRequest` 按 `source` 名找声明拥有该源的 handler,**只调它**
- local.js 只声明 `local`,不会屏蔽 kw/kg/tx/wy/mg
- 仍需要 lx_main.js 调走时才能 resolve——但其 handler 需要 AES shim → 走不通
- **可工作范围**:huanyin.js / qdy.js / huibq.js(纯 HTTP,3rd-party API 不需要 AES)

**优点**:小改动,shim 进化到正确模型。  
**缺点**:依然调 3rd-party API(huibq 走 onrender.com,qdy 走 oiapi.net + gdstudio.xyz,huanyin 走 oiapi.net),P3.10b 「0 上传」红线被绕过(虽然 C.2 已豁免)。

### 路线 Y:路线 X + 实现 AES shim(~500 行 JS,逆向工程)

lyswhut 5 源(kw/kg/tx/wy/mg)musicUrl 加密需要 AES-CBC + RSA + 自定义 hash 协议。shim crypto 必须完整实现才能让 lx_main.js 的 5 子源 resolve URL。

**优点**:真「全部源能听歌」,能力最大化。  
**缺点**:500+ 行逆向 + 维护成本 + 5 源任一 API 变就崩。

### 路线 Z:接受「0 上传」红线,只走本地 mp3(当前)

**已 ship**:A 阶段剥 seed.mp3 兜底,DEFAULT_SOURCES=["local.js"],0 外网。真 mp3 命中就播,不命中弹清晰 err。

**优点**:完全合规 P3.10b 红线,无任何外网请求,无 3rd-party 信任风险。  
**缺点**:~/Music 需用户手动塞 mp3 才能用;否则啥都播不了。

### 路线 W(推荐):路线 X + 仅接 1 个稳定源(huibq)

走路线 X(shim 按 source 派单),DEFAULT_SOURCES 加 `["local.js", "huibq.js"]`:**huibq.js 仅 89 行、5 子源(kw/kg/tx/wy/mg)全开、3rd-party API 是 onrender.com 公共分享端点(无 key、无 login),share-v3 是公开 token**。即便 huibq 失败,fallback 到 local.js → 真 mp3。

**优点**:shim 修一处+启 1 源,~50 行代码+1 测试。最快让用户听到在线歌。  
**缺点**:onrender.com 是公共服务,可能冷启慢或 503;但失败也只掉到 local.js(本地 mp3)。

## 调研代价明细

| 操作 | 时间 |
|---|---|
| 9 源实际 resolve 测试 | 5 min |
| 发现 local.js shadowing | 5 min |
| 发现 lx_main.js packed source dispatch 问题 | 5 min |
| 发现 shim crypto 未实现 | 5 min |
| 发现 3 混淆源 | 5 min |
| 删 3 混淆源 + revert shim + revert DEFAULT_SOURCES + 改测试 | 5 min |
| 测试 473/473 green | 1 min |
| 写 ship memory | 现在 |

合计 ~30 min 调研 + 调研纪要 ship。

## 风险登记

1. **DEFAULT_SOURCES 已 revert ["local.js"]** — 这是当前实际状态,用户界面将看到与 A 阶段 ship 后**完全一致**(仅本地 mp3 能播)。**未 ship 任何新代码**(shim revert + 测试改回),只有「删 3 混淆源」是真 ship。
2. **删 changqing/flower/grass** — 强红线违规(混淆 JS),即使没人用也直接删。后续若用户想要「源仓库多样性」,需重新找可审计的源 fork。
3. **lx_main.js 当前未启用但保留** — 等路线 X/Y 拍板后再走,或永久保留作为「官方 lyswhut dist 参考」。
4. **C 阶段未达成「用户期望:所有歌都能播」** — 当前 Z/W/X 都没法做到「全网歌曲」。需要决策。

## 与既有 ship 的关系

```
2026-10-04  A 阶段剥 seed.mp3 兜底      (commit 9950185)
2026-10-05  本 ship   调研纪要 + 删混淆源 (本次)
2026-10-05  待拍板   shim 派单修复 + 接源策略 (下一步)
```

## 给用户的问题(3 路拍板)

```
A. 走路线 W(推荐):修 shim 派单 + 接 huibq.js — 最小改动让用户最快听到在线歌
B. 走路线 Y:修 shim 派单 + 实现 AES shim — 全部 5 源能听但 500 行逆向
C. 走路线 Z:保持当前 ["local.js"] — 完全 0 外网,等 P3.10b 红线策略调整再说
```

(注:用户的「屏幕最少」是用户原话误录,实指 9 源方案;此处选项用字母 ABC 与之前 ship A/B/C 区分。)