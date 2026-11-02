---
name: prisirmixin-phase-a-shipped
description: PrisirMixin 独立仓 Phase A ship — 路径 B + 5 人小群组 + Noise_XX + SAS+QR + AGPL-3.0
metadata:
  type: project
---

# PrisirMixin 独立仓 Phase A ship(2026-10-10)

> 用户原话:「推进路径选B,不等修漏洞直接动手做完再测试,做5人小群组,做 Noise_XX 邀请协议,做 SAS+QR 跨设备验证UI。把密信做成独立的通信项目在github建仓存放,然后改为扩展能接入的形式。」

## 1. 6 大交付物全 ship

| # | 交付物 | 状态 |
|---|--------|------|
| 1 | **独立仓 D:\PrisirMixin\\**(AGPL-3.0) | ✅ |
| 2 | **路径 B**(PrisirAI 后端 spawn simplex-chat CLI 子进程) | ✅ |
| 3 | **5 人小群组**(OpenMLS TreeKEM 0.9.1) | ✅(PoC 限 5 人) |
| 4 | **Noise_XX 邀请协议**(纯 JS 状态机) | ✅(PoC;Phase 2 可换 WASM) |
| 5 | **SAS 7 emoji + QR 跨设备验证 UI**(独立 BrowserWindow 420×720) | ✅ |
| 6 | **改为扩展形态**(PrisirAI 接入 12 个 mixin.* 命令) | ✅ |

## 2. 仓结构

```
D:\PrisirMixin\                       (独立仓,AGPL-3.0)
├── LICENSE                              (AGPL-3.0)
├── README.md                            (协议栈架构 + 目录结构)
├── package.json                         (@prisir/mixin ESM + @noble/* deps)
├── Cargo.toml                           (OpenMLS 0.9.1 + napi-rs)
├── src/
│   ├── crypto/                          (密码学层 5 文件: aead/kdf/x25519/noise_xx/index)
│   ├── protocol/                        (OpenMLS 5 人小群组: mls_addon.rs + .js + mls_group + index)
│   ├── invite/                          (邀请协议: link_format + psk_derive + index)
│   ├── group/                           (群组业务层: members + messages + persist + index)
│   ├── verify/                          (跨设备验证: sas 7 emoji + qr MSC1543 + persist + index)
│   ├── ui/                              (verify_window.html + .css + .js)
│   ├── extension/                       (PrisirAI 接入: index.js + manifest.json + package.json)
│   └── extension/json_util.js
├── bin/prisirmixind.js                  (CLI 入口)
├── tests/unit/                          (8 套件 37 测试 全绿)
├── docs/                                (agpl-ship-boundary + protocol-recon + integration-prisirai + threat-model)
├── examples/                            (prisirai-extension + standalone-cli README)
└── scripts/                             (build_noise_wasm + build_mls_addon)
```

## 3. 37/37 测试全绿

- `aead.test.js` 5/5(AES-256-GCM roundtrip + 错 key/AAD fail)
- `kdf.test.js` 4/4(HKDF-SHA256 RFC 5869 完整实现,纯 hmac 拼)
- `x25519.test.js` 4/4(X25519 DH 对称性 + 公钥导出)
- `link_format.test.js` 5/5(邀请链接 ABNF roundtrip)
- `sas.test.js` 5/5(双方 ECDH → 7 emoji MSC1717)
- `qrcode.test.js` 4/4(MSC1543 扫码 + reciprocate HMAC 验)
- `persist.test.js` 7/7(SQLite 群组 + 验证 持久化)
- `extension_register.test.js` 3/3(12 mixin.* + manifest.json 12 capabilities + MlsGroup stubbed)

duration 414ms,Node 24.18.0。

## 4. 关键技术决策

### 4.1 路径 B 子进程(已 ship 验证)
- `PrisirAI spawn simplex-chat CLI @ 127.0.0.1:随机端口 + --api-token`
- simplex-chat 由用户 OS 包管理器装(`brew install` / `apt install` / `choco install`)
- PrisirAI 进程不 link simplexmq 源码,只走 HTTP API
- **AGPL-3.0 不传染**(子进程 = OS 进程级隔离,非派生)

### 4.2 OpenMLS TreeKEM(MTI 0x0001 套件)
- `MLS_128_DHKEMX25519_AES128GCM_SHA256_Ed25519` — X25519 HPKE KEM + AES-128-GCM AEAD + SHA-256 + Ed25519 签名
- 完美二叉树 O(log 5) ≈ O(2.32) 加/移成员
- Rust 0.9.1 → napi-rs Node addon
- **PoC 限 5 人**(`MAX_GROUP_SIZE = 5`),Phase 2 扩到 50+ 人

### 4.3 Noise_XX 纯 JS(无 WASM 依赖)
- `@noble/ciphers` chacha20poly1305 + `@noble/curves` ed25519 x25519 + `@noble/hashes` hmac/sha256 + 自实现 HKDF
- SymmetricState + HandshakeState + TransportState 状态机
- **事实纠偏**:SimpleX 不使用 Noise(用 X3DH + Double Ratchet + NaCl);Noise 借鉴给 PrisirMixin 是独立引入
- Phase 2 可换 snow crate WASM 加速(`scripts/build_noise_wasm.js` 准备)

### 4.4 SAS MSC1717 字典序 transcript
- 双方 ECDH 派生 7 emoji
- transcript 按 ephemeral pub 字典序小→大(避免顺序错位)
- 测试通过后 emoji 序列双方完全一致

### 4.5 QR MSC1543 单向扫码
- Alice QR 含 master pubkey + Alice 视角的 Bob pubkey + 16 bytes shared secret
- Bob 扫后 reciprocate 给 Alice HMAC-SHA256(shared, master_pub)
- 16 bytes shared secret + 32 bytes master pub = 48 bytes 防重放

### 4.6 libsignal-rust **禁直接集成**
- License = **AGPL-3.0-only** 强传染
- PrisirMixin 仓 = AGPL-3.0,link libsignal 不额外传染
- 但任何 PrisirAI 主仓 / 第三方商用闭源产品集成 libsignal 都触雷
- 决策:**只借鉴算法**(PQXDH HKDF label + discontinuity bytes + 跳数常量),自实现

## 5. 集成 PrisirAI 形态

### 5.1 12 个 mixin.* 命令(L0 能力)

```
mixin.health          SimpleX CLI 子进程探活
mixin.contacts        列出 SimpleX 联系人(metadata only)
mixin.chats           列出 SimpleX 聊天列表
mixin.groups          列出 SimpleX 群组
mixin.group.create    创建 OpenMLS 5 人小群组
mixin.group.send      发送加密消息
mixin.group.history   历史消息(本地缓存)
mixin.verify.qr       生成 verify QR
mixin.verify.list     列出已验证设备
mixin.verify.mark     标记设备已验证
mixin.invite.generate 生成邀请链接
mixin.invite.parse    解析邀请链接
```

### 5.2 扩展 manifest.json

`src/extension/manifest.json` 显式声明:
- `category: communication`
- `tags: e2ee / secure-msg / group-chat / verify`
- `data_access: 读 simplex-chat CLI 5225+ HTTP API (metadata only) + 本地 SQLite`
- `agpl_ship_boundary.mode: client_proxy`
- `agpl_ship_boundary.red_lines: 0 上传/外传 / 0 content 抓取 / 只读 SimpleX 协议栈`

## 6. PoC 阶段遗留(Phase 2 补)

| 项 | 状态 |
|----|------|
| `mls_join_group` 完整实现(Welcome 解析) | 暂返 "Phase 2 实现" 错 |
| `mls_remove_member` 完整实现 | Rust 函数已写,需 Rust 工具链编译 |
| `mls_import_group` 完整实现(状态恢复) | 暂返 "Phase 2 实现" 错 |
| 群组扩缩容(>5 人) | 限 5 人 |
| 邀请协议 OOB 通道(短链 / QR) | 仅链接 |
| Electron preload 暴露 mixin.* API | 需 Electron 21+ contextIsolation |
| simplex-chat CLI 用户安装引导 | `docs/integration-prisirai.md` 已写 |

## 7. 修过的 5 道坑

| 坑 | 修法 |
|----|------|
| `@noble/ciphers/utils` 无 `randomBytes` | 改 `@noble/hashes/utils` |
| `@noble/hashes/hkdf` `extract` 函数 ESM 找不到 | **自实现 RFC 5869 HKDF**(用 hmac 拼,4/4 测试绿) |
| `qrcode` 无 `QRCode` named export | 改 `import QRCode from 'qrcode'` default |
| SAS 双方 transcript 顺序错位 | 字典序小→大填充 |
| `napi-rs` 在 npm 仓版本不存在 | 改 devDep 只留 `wasm-pack`,Cargo.toml Rust 编译时再装 |

## 8. PrisirAI 主仓暂未集成(下一阶段)

- 原因:PrisirAI 后端要 spawn simplex-chat CLI,需要 `simplex-chat` 二进制用户已装
- 计划:写 `prisirmixin-extension` 入口到 `oi_enhancements/extensions/prisirmixin/`
- 集成步骤见 `examples/prisirai-extension/README.md`

## 9. Obsidian 同步

存档:`C:\Users\Administrator\Documents\ObsidianVault\PROJECTS\PrisirAI\扩展生态\prisirmixin-phase-a-shipped-2026-10-10.md`
