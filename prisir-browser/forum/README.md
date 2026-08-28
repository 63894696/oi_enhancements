# F-1 — Prisir 免注册论坛浏览端(chrome://forum)

> 公钥即账号、签名即发帖、帖子是自包含可独立验证的签名对象。
> 本轮**纯 JS 先行**(不依赖重编译):`window.ForumNative` 未就绪时降级 WebCrypto + localStorage,
> 诚实标注;重编译后无缝切原生代签,私钥不出原生层(与 mixin 同构的可插拔签名层)。

## 契约来源(全部已定稿)
| 契约 | 文件 | 关系 |
|------|------|------|
| 协议定稿 | `docs/prisir-forum-protocol-2026-08-21.md` | canon/帖子对象/WS 帧/确认计数 |
| Mojo 接口 | `forum.mojom`(本目录) | 原生代签契约,重编译时实现 |
| 身份根 | `prisir-browser/agent/identity.mojom` | DeriveKey("forum-sign") + SignWith 薄壳转发目标 |
| 可插拔签名模式 | `prisir-browser/mixin/group.html:197-242` | 本目录 forum_client.js 直接复刻 |

## 文件清单
| 文件 | 作用 |
|------|------|
| `forum.html` | chrome://forum 页面:身份横幅 / 两级板块树导航 / 发帖框(PoW+签名)/ 帖子流三态渲染 / 回复=验签引用 |
| `forum_client.js` | 协议层:canon / Ed25519 双轨签名 / PoW 求解(分片不卡 UI)/ WS 客户端 / 阅读侧验签 |
| `forum_store.js` | 本地存档:我的帖子副本(「数据属于个人」落地)/ 关注身份 / 增量游标,localStorage(MVP) |
| `forum_verify.js` | 三态渲染:✓已确认 / ⏳未确认(暖底+谨防冒名)/ ✗验签失败或已撤下(折叠) |
| `forum.mojom` | 原生契约(本轮定稿,重编译实现) |
| `forum_watchdog.js` | 本机盯梢:Node 常驻连 WG 镜像(18813),新帖落 `~/oi_enhancements/forum_inbox.jsonl`,开机自启 |

## 多板块(F-2,2026-08-21)
板块是 relay 白名单(`forum_relay.py` BOARDS),**两级树:域(3 个恒定)→ 板块(随版本增减)**:
- `browser` 浏览器:translate/search/network/agent/findex/ime/shell/forum
- `babelspan` 内容站(按书类):literature/nonfiction/genre/podcast
- `meta` 站务:lobby(默认落地)/tea/meta

`welcome` 帧下发 boards 目录,客户端渲染导航;`read` 可按 `board` 过滤(增量同步仍按全局 seq)。**不开放自由开版**——防滥用、防主题稀释;长期死版归档降格(板块生命周期,借大站治理教训,不抄其爬塔信息流)。

## 视觉(F-2)
浅色极简,Babelspan 品牌色板顺延(`brand-naming.md`):paper 底 / ink 字 / **copper 铜径=航道**(唯一强调)/ **teal 书光=信标**。零图片零 webfont 零框架,单 HTML <15KB,反爬塔即反信息流(无点赞/热榜/红点)。静态资源 nginx `no-store`(降 CF 缓存陈旧坑,曾致旧 client `bad_board`)。

## 红线(JS 先行期的诚实标注)
- **私钥**:当前 WebCrypto + `localStorage["forum_id_jwk"]`(页面顶部横幅明示「JS 降级模式」);
  重编译后 `window.ForumNative` 就绪,私钥迁原生层(identity 根派生),localStorage 不再存私钥。
- **零信任 relay**:每帖阅读侧 `verifyPost` 自验(签名+fp+PoW),不因 relay 已验而省略。
- **撤回 ≠ 全网删除**:本地副本(forum_store)在 retract/takedown 后仍保留,可审计。

## 开发期直连(不经重编译)
1. 起 relay: `FORUM_PORT=18812 POW_BITS=8 python forum_relay.py`(仓库根)
2. 起静态服务: `cd prisIr-browser/forum && python -m http.server 18940`
3. 浏览器开 `http://127.0.0.1:18940/forum.html?relay=ws://127.0.0.1:18812`,双窗口双身份 E2E

## 生产端点(2026-08-21 部署)
- **公开论坛**: `wss://bbs.babelspan.com/forum`(主站域,CF 橙云 + Let's Encrypt,VPS nginx → 127.0.0.1:18812,systemd `forum-relay`)
- **页面降级入口**: `https://bbs.babelspan.com/forum.html`(生产正式入口是 chrome://forum,此为浏览器直开降级)
- **本机盯梢镜像**: `ws://10.66.66.1:18813`(同脚本独立实例,**仅 WireGuard wg0 内网可达**,不公网暴露;
  配套 `forum_watchdog.js` 常驻本机,新帖落 `~/oi_enhancements/forum_inbox.jsonl`,开机在线=盯梢在线,第一时间收用户反馈)

## 重编译挂接步骤(列入 §6.1 检查清单)
1. **复制**: `prisir-browser/forum/*.{html,js}` → `chrome/browser/resources/prisir/forum/`
2. **WebUI**: 注册 `chrome://forum`(参照 mixin dm.html 挂法)
3. **Mojo**: 实现 `forum.mojom` handler 薄壳,转发 `identity.DeriveKey("forum-sign","ed25519-sign")` + `SignWith`;页面侧 `window.ForumNative = {getIdentity, sign}` 绑 Mojo 管线
4. **检查清单新增**: chrome://forum 可开;发帖签名走原生层(DevTools 确认 `window.ForumNative` 存在);localStorage 无 `forum_id_jwk` 明文私钥
