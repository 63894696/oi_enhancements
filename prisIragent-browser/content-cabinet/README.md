# Prisir 内容柜(浏览器内嵌版)

> 来源:`D:\Projects\babelspan\apps\public\shelf.html`(babelspan 站内容柜,496 行纯前端)。
> 本版:浏览器内嵌功能页,与站端同 `localStorage['babelspan-shelf-v1']` 数据格式,可互相导出导入搬运。

## 文件
- `content-cabinet.html` — 内嵌版主文件(自包含,零外链)
- `prisir-logo-48.png` — favicon(Prisir 母标 metal-v2)

## 与站端 shelf.html 的差异(仅 4 处剥离/改写,自研功能全保留)
| 处 | 改动 | 理由 |
|---|---|---|
| Google Fonts 外链 | **剥**,用系统字体栈兜底(Georgia/serif + system-ui/sans) | 本地优先,零外链;唯一第三方依赖 |
| `sendBeacon('/api/track')` 站内统计 | **剥**(用户拍板) | 内嵌无此后端,且本地优先不上报 |
| 站内导航(返回海图/为何建站) | **剥**,改 Prisir 内嵌语境 | 浏览器内嵌无站内路由 |
| 标题/favicon/footer | 改 Prisir 品牌 + 本地图标 | 命名统一 |

## 保留的自研功能(全部)
- 三视图:书架(书脊)/ 海报墙(悬停详情)/ 清单(表格)
- 增删改查 + 状态切换(想要/已完成)+ 类型(书/影视/动漫/综艺/音乐/游戏)
- 导入 JSON 自动合并去重(同标题+作者视为同一条)、导出 JSON
- 海报自动搜集桥(`window.postMessage` `__ctShelf` 通道 ↔ Prisir 翻译插件;未装插件优雅降级为 emoji 兜底)

## E2E 实测(2026-08-14,file:// 打开)
- 渲染:标题/三视图/增导导按钮全在,空态正常 ✅
- 零外链脚本/字体 ✅
- 添加→localStorage 写入→渲染→计数→空态隐藏 全链路 ✅
- 导出 JSON 结构正确(app/version/items)✅
- 测试数据已清理 ✅

## 编译集成方式(待 M4 统一处理)
作为浏览器功能按钮入口,开一个内嵌页指向本 HTML。数据本就 localStorage,符合本地优先红线。
若要持久化到 profile 级(与 M2 智能体存储一致的 `threads.json` 思路),后续可把 localStorage 换成浏览器 profile 文件——本期不动,localStorage 已满足本地优先。
