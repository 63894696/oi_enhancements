---
name: prisir-mixin-protocol-recon
description: 5 大密信协议深度侦察合流(2026-10-10)— hs-libsimplex / Matrix / libsignal-rust / MLS / Noise 协议级参考,给密信(prisir-browser/mixin/)做集成优化
metadata:
  type: reference
---

# 密信 5 大协议深度侦察合流(2026-10-10)

**侦察日期**:2026-10-10
**目标产品**:`prisir-browser/mixin/`(密信,Prisir 浏览器内置 `chrome://mixin`)
**目标协议**:**SimpleX 协议**为基座,侦察 5 大协议给优化参考
**关键决策**:**不 ship 任何扩展** — 这是技术侦察报告,产物给密信产品层决策者

---

## 0. TL;DR

5 大协议侦察合流后,**给密信产品层 4 大集成建议**:

1. **群组加密(>10 人)**:借鉴 **Matrix Megolm Sender Key**(O(1) 加密)或 **MLS TreeKEM**(O(log n) 加/移成员),**二选一** — 密信 2.0 大群路线
2. **后量子迁移**:借 **libsignal PQXDH**(X25519 + Kyber1024 hybrid)+ `WhisperText_X25519_SHA-256_CRYPTALS-KYBER-1024` label,hybrid 模式过渡
3. **邀请协议**:借 **Noise_XX 模式** + 1 次性 PSK(预共享密钥),增强 forward secrecy + 抗 harvest-now
4. **跨设备验证**:借 **Matrix SAS 7 emoji + QR 单向扫码**(MSC1543 MSC1717),给密信多设备同步

**关键事实纠偏**:SimpleX **不**用 Noise Protocol Framework(SimpleX 用 X3DH + Double Ratchet + NaCl,术语「noise」指流量 padding)。Noise 借鉴给密信是**独立引入**,与 SimpleX 协议不互通。

---

## 1. 5 大协议对比矩阵

| # | 协议 / 仓 | License | 给密信核心价值 | 集成方式 | 风险等级 |
|---|---|---|---|---|---|
| 1 | **hs-libsimplex** | AGPL-3.0 | SimpleX 协议栈 SSOT;SMP confirmation 4 步握手;PQDR sntrup761;MKSKIPPED 缓存;**群聊是 N 个 1:1 + 应用层 fanout** | 子进程模式(已 ship `simplex-bridge-status`) | 低(Prisir 浏览器 AGPL-3.0 已兼容) |
| 2 | **Matrix / Synapse** | Apache-2.0 | **Megolm Sender Key 群组加密 O(1)**;cross-signing 三层密钥;SAS 7 emoji + QR 单向扫码;sliding sync | 借鉴 client-side E2EE,不用联邦架构 | 低(纯借鉴算法) |
| 3 | **libsignal-rust** | **AGPL-3.0-only** | **PQXDH(Kyber1024 + X25519)+ Triple Ratchet(SPQR)**;Sealed Sender V2 多接收者 KEM;SKDM 群组密钥分发;**跳数常量 MAX_FORWARD_JUMPS=25_000** | **禁止 A/B/C**(传染),**只允许 D 纯客户端代理** | **高**(AGPL-only 强传染) |
| 4 | **MLS RFC 9420** | OpenMLS MIT / mls-rs Apache-2.0 | **TreeKEM 完美二叉树 O(log n) 加/移成员**;7 种 Proposal+Commit 异步友好;**MTI 套件 0x0001**(X25519 + Ed25519 + AES-GCM);**与 SimpleX 协议可双协议并行** | OpenMLS 0.9.1 + napi-rs 给 Node 子进程 | 低(协议 + 实现都宽松 license) |
| 5 | **Noise Protocol** | Spec CC-BY-4.0 / snow MIT | **Noise_XX 邀请握手**;SymmetricState 链式 forward secrecy;**psk2 modifier** 抗 harvest-now;**0-RTT 模式**(IKpsk0 + F modifier);WireGuard 实战验证 | Rust FFI(cdylib + C ABI),不与 SimpleX 协议混 | 低(纯借鉴,无 AGPL 传染) |

---

## 2. 5 大协议深度要点(每协议一页)

### 2.1 hs-libsimplex(密信基座协议)

- **核心架构**:SMP 单向队列 + agent 半双工 + PQDR(sntrup761 后量子)双棘轮
- **HKDF chain**:每条消息派生链 state.CKs + mk;Signal 同款
- **header 加密**:AES-256-GCM + HKs(ratchet header key);header 固定 padding 到 2310 字节(PQ on)或 88 字节(PQ off)
- **MKSKIPPED 缓存**:`<hdr.n, hdr.pn, mk>` 三元组哈希缓存,乱序消息可恢复
- **deniable authentication**:用 X25519 `crypto_box`(非 Ed25519 签名),给「既签名又可否认」强保证
- **群组真相**:`agent-protocol.md` **无 spec 群组协议** — SimpleX 群聊是 N 个 1:1 + 应用层 fanout,O(n²) 消息复杂度
- **FFI 现实**:simplexmq **无 native FFI 导出** — 集成只有 2 条路径:(A)静态 link Haskell lib + 浏览器 native binary(30-60MB,启动卡顿),(B)起 simplex-chat CLI 子进程(已 ship 模式,30MB,启动 0.5s)
- **推荐路径**:**B 子进程模式**,继续走 `simplex-bridge-status` Phase A 模式

### 2.2 Matrix / Synapse

- **核心架构**:去中心化联邦;房间 = state events DAG;E2EE = Olm(1:1 双棘轮)+ Megolm(1:N Sender Key)
- **Megolm 算法**(spec `content/olm-megolm/megolm.md`):
  - 32-bit 计数器 + Ed25519 密钥对 + 4 组 256-bit ratchet 值
  - Ratchet 演进:每 2^8/2^16/2^24 步重种子
  - 消息密钥:`AES_KEY || HMAC_KEY || AES_IV = HKDF(salt=0, IKM=R_i, info="MEGOLM_KEYS", L=80)`
  - **Sender Key 模式**:每个参与者 1 个 outbound session,持有自己的 ratchet + Ed25519 K
  - 加密开销从 O(N) 降到 **O(1)**
  - 密钥轮换:`ROTATION_PERIOD=1 周` 或 `ROTATION_MESSAGES=100` 任一即触发
- **matrix-rust-sdk**:26 crates,核心 `matrix-sdk-crypto` 是 **sans-IO 模式**(无网络 IO)
  - UniFFI 已暴露完整 FFI(Kotlin/Swift/Python/Ruby)
  - 给密信:`OlmMachine` + `BackupRecoveryKey` + `Sas` + `QrCode` + `Device` 即可
- **Megolm 局限**:sender 知道所有密钥(ratchet 可推进/可解密所有历史)→ **与 SimpleX 无 trust 哲学不完全兼容**
- **推荐**:借鉴 Sender Key 思路而非完整集成;**5/50/500 人群临界**:5 人以下维持现状

### 2.3 libsignal-rust(Signal 协议主线)

- **核心架构**:`Double Ratchet + SPQR(Sparse Post-Quantum Ratchet)= Triple Ratchet`,JNI / C FFI / Node-addon 三桥
- **License**:**AGPL-3.0-only**(2026 全面统一,无 MIT/Apache 旁路)
- **PQXDH**(`/rust/protocol/src/pqxdh.rs`):经典 X3DH + **Kyber1024** 后量子混合握手
  - 4 类预密钥:Identity Key(Curve25519 长期)+ Signed Pre-Key(30 天轮换)+ One-time Pre-Key(一次性)+ **Kyber Pre-Key(2024+ 新增,与 SPK 同周期轮换)**
  - AD 构造:`secrets = [0xFF; 32] || DH(IK_A_priv, SPK_B) || E_A_priv * IK_B || E_A_priv * SPK_B || E_A_priv * OPK_B (optional) || Kyber.encaps(KPK_B)→(ss, ct)`
  - HKDF label = `WhisperText_X25519_SHA-256_CRYPTALS-KYBER-1024`(锁协议版本,防 downgrade)
- **Triple Ratchet**:DH Ratchet + SPQR 作为独立 crate `signalapp/SparsePostQuantumRatchet v1.6.0`
- **Sealed Sender V2**(2024+):Manuel Barbosa 2023「Randomness Reuse」+ AES-256-GCM-SIV + 多接收者 KEM
  - 一次随机 m,HKDF 派生所有收件人,只重用 random bytes
  - 标签常量:`Sealed Sender v2: r / K / DH / DH-sender`
- **跳数常量**:`MAX_FORWARD_JUMPS=25_000` / `MAX_MESSAGE_KEYS=2000` / `MAX_RECEIVER_CHAINS=5` / `MAX_SENDER_KEY_STATES=5` / `MAX_UNACKNOWLEDGED_SESSION_AGE=30 天`
- **AGPL 合规红线**:
  - 静态/动态链接 libsignal 的代码必须以 AGPL-3.0 发布 + 提供源码
  - **纯客户端代理(simplex-bridge-status 模式)不传染** — 因为只跑独立进程,Prisir 浏览器只通过 HTTP/JSON-RPC 调用
  - **禁止 A/B/C 路径**,**只允许 D 纯客户端代理**

### 2.4 MLS RFC 9420(IETF 2023-07 标准)

- **核心架构**:**完美二叉树(TreeKEM)**,每节点持有 HPKE 公钥 + path secret;每次成员变动产生 1 epoch
- **加/移成员复杂度**:**O(log n)** 而非传统 Sender Key 的 O(n)
- **双阶段 Proposal → Commit**:声明意图(Add/Remove/Update)与状态切换分离,异步友好
- **密钥派生**(§8 Epoch 密钥调度图):
  ```
  init_secret[n-1] → commit_secret → KDF.Extract → joiner_secret → psk_secret(0) → epoch_secret → init_secret[n+1]
  ```
  - 8 类 secret label:`sender data` / `encryption` / `exporter` / `external` / `confirm` / `membership` / `resumption` / `authentication`
- **MTI cipher suite** = `MLS_128_DHKEMX25519_AES128GCM_SHA256_Ed25519`(0x0001,X25519 HPKE + Ed25519 签名 + AES-128-GCM + SHA-256)
- **OpenMLS**(`github.com/openmls/openmls`,**★1043**,MIT,RFC v0.9.1 2026-10-07):
  - workspace 8 crates:`openmls` / `openmls_rust_crypto` / `traits` / `delivery-service/ds` / `sqlite-provider` / `libcrux-provider` / `wasm`
  - 支持 ciphersuites:0x0001(必实现) + 0x0002(P-256) + 0x0003(ChaCha20Poly1305)
  - 可编译 wasm32-unknown-unknown(feature="js"),给 Prisir 浏览器 mixin 复用
- **mls-rs**(`github.com/awslabs/mls-rs`,★258,Apache-2.0,v0.56.0 2026-08-19) — server-side 友好
- **应用案例**:Matrix v2(2023-06 公告改 MLS 作为 E2EE 默认)/ Wire / Cisco Webex / Apple Key Transparency(iOS 17)
- **与 SimpleX 关系**:**不冲突,双协议并行** — MLS 在群组密钥层覆盖,SimpleX 在通道层覆盖
- **复杂度门槛**:TreeKEM 实现门槛高,密钥调度 + Secret Tree + Parent Hash + Tree Hash + Transcript Hash 五套 HC 派生联动,调 bug 难

### 2.5 Noise Protocol Framework(2024-04 Rev 34)

- **核心架构**:「握手模式库 + 加密原语组合 + 双棘轮状态机」三层协议框架
- **12 种握手模式**:
  - NN/KN/NK/KK/NX/KX/XN/XK/IN/**IK**/IX/**XX**
  - **XX**(双方初始未知→互验,3 条消息) = 标准邀请握手
  - **IKpsk2** = WireGuard 实战(A已知B静态密钥 + msg2 注入 PSK 抗 harvest-now)
- **4 个 modifier**:`psk0/psk1/psk2`(预共享密钥注入 N 条消息后) + `F`(0-RTT data) + `S`(被动反身份泄露) + `P`(被动) + `K`(Rev 34+ NoisePQ 引入)
- **SymmetricState 状态机**(spec §5.3):
  - `ck`(chaining key)**单向 HMAC 推进** → forward secrecy 自动
  - `h`(handshake hash)**累加所有握手消息** → transcript binding
- **snow crate**(`github.com/noiseprotocol/noise-rust`,workspace,核心 = `snow` crate):
  - `handshake.rs` + `transport.rs` + `ratchet.rs` + `cipher.rs` + `dh.rs` + `hash.rs` + `params.rs`
  - 纯 Rust,no OpenSSL;**MIT**
- **应用案例**:WireGuard(`Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s`)+ Lightning BOLT #08(`Noise_XK_25519_ChaChaPoly_SHA256`)+ I2P(`Noise_XK_25519_ChaChaPoly_SHA256`)
- **关键事实纠偏**:**SimpleX 不使用 Noise**。SimpleX 用 X3DH + Double Ratchet + NaCl `crypto_box` + Curve448(Signal-style,不是 Noise);post-quantum 用每 ratchet 步 PQ KEM 注入(类似 Apple PQ3,**不是** NoisePQ)
- **给密信借鉴**:Noise_XX 邀请协议 + 1 次性 PSK(psk2 modifier) + 0-RTT 模式(IKpsk0 + F)

---

## 3. 给密信(`prisir-browser/mixin/`)的 4 大集成建议

### 3.1 群组加密(密信 2.0 大群组路线)— **二选一**

**方案 A:Matrix Megolm Sender Key**
- 优势:Apache-2.0 宽松;matrix-rust-sdk UniFFI/wasm FFI 完整;sans-IO 模式易嵌入
- 劣势:与 SimpleX 无 trust 哲学不完全兼容(sender trust 模型)
- 工作量:大,6+ 月全量改造;或 6 周仅大群(>5 人)启用

**方案 B:MLS RFC 9420 TreeKEM**
- 优势:IETF 2023-07 标准,长期可维护;与 SimpleX 协议可双协议并行;OpenMLS 0.9.1 直接用
- 劣势:实现门槛高(密钥调度 + Secret Tree + Parent Hash + Tree Hash + Transcript Hash 五套联动)
- 工作量:OpenMLS Rust 集成 + napi-rs Node addon ~ 4 周;5/50/500 人群 PoC 验证

**推荐**:**B 长期路线 + A 短期试水** — 先 OpenMLS PoC 验证 TreeKEM 关键逻辑,MLS 稳定后大群(>50 人)切换;5 人以下小群维持现状(SimpleX 现有 N 路独立 ratchet 已够用)

### 3.2 后量子迁移(2026+ 必修)— **libsignal PQXDH 路径**

- **必装**:Kyber1024 集成 + SPQR 作为独立 crate
- **HKDF label 钉死**:`WhisperText_X25519_SHA-256_CRYPTALS-KYBER-1024`(锁协议版本,防 downgrade)
- **discontinuity bytes**:`[0xFF; 32]`(防 X3DH↔PQXDH 跨协议攻击)
- **AD 构造**:4 DH + 1 Kyber 共享秘密 + HKDF-SHA256 → (RootKey, ChainKey, InitialPQRKey)
- **混合模式过渡**:即使 Kyber 被量子破解,4 DH 仍安全;反之亦然
- **跳数限制**:`MAX_FORWARD_JUMPS=25_000` / `MAX_MESSAGE_KEYS=2000` 借鉴(防 RAM 爆炸)
- **PQ 路径决策**:5 年内 ship → 走 0x0001 X25519 + AES-GCM;5-10 年 ship → 等 ML-KEM 套件

### 3.3 邀请协议(当前 SMP 4 步握手)→ **Noise_XX + 1 次性 PSK**

- **模式选择**:`Noise_XX_25519_ChaChaPoly_BLAKE2s`(双方初始未知→互验,3 条消息)
- **psk2 modifier**:邀请链接附 256-bit 预共享密钥,抗 harvest-now(对手先存密文待量子破解)
- **0-RTT 模式**(可选):`Noise_IKpsk0 + F modifier` → 减少邀请握手 1 RTT
- **对比 SimpleX confirmation 4 步**:`NEW → OOB 邀请 → Bob 写 SEND → Alice KEY secure`(已自洽)
- **关键**:Noise 是**独立协议**,**不与 SimpleX 互通**;若密信要发消息给 SimpleX 用户,继续走 SimpleX CLI HTTP API(已 ship `simplex-bridge-status`)

### 3.4 跨设备验证(密信多设备同步)→ **Matrix SAS 7 emoji + QR 单向扫码**

- **SAS emoji**(MSC1717):7 个 emoji + 4 个备用(共 256 种组合,匹配概率 ~1.5×10^-18)
- **QR code 单向扫码**(MSC1543):Alice 扫 Bob 的 QR 一次性完成双方验证(单向扫码),QR 编码包含 ephemeral secret 防 MITM
- **Cross-signing 三层密钥**(MSC1756):
  - **Master key**(最高权限,可冒充身份)— 应放 OS secure storage(Win DPAPI / macOS Keychain / Linux libsecret)
  - **Self-signing key**(签自己的设备)— 可换
  - **User-signing key**(签别人的 master)— 可换
- **完整签名链**:Alice master → Alice user-signing → Bob master → Bob self-signing → Bob device
- **给密信**:「扫一次码,信任该用户所有设备」

---

## 4. 集成风险与 AGPL 合规矩阵

### 4.1 License 合规矩阵

| 资产 | License | 集成方式 | 风险 | 备注 |
|---|---|---|---|---|
| **simplexmq** / SimpleX 协议 | AGPL-3.0 | 客户端代理 / 子进程 — **不传染** | 低 | 已 ship `simplex-bridge-status` 验证 |
| **libsignal-rust** | **AGPL-3.0-only** | **禁止 A/B/C**(传染),**只允许 D 纯客户端代理** | **高** | 静态/动态链接强制 AGPL-3.0 源码发布 |
| **OpenMLS** | MIT | 可直接集成 / 可 fork | 低 | 需保留版权 |
| **mls-rs(AWS)** | Apache-2.0 | 可直接集成 / 可 fork | 低 | 需保留 NOTICE + 不用商标 |
| **Matrix Megolm / Olm** | Apache-2.0 | 可直接集成 / 可 fork | 低 | matrix-rust-sdk UniFFI 完整 |
| **Noise Spec** | CC-BY-4.0 | 自由使用 | 无 | 派生规范文档需署名 Trevor Perrin |
| **snow crate** | MIT | 可直接集成 / 可商用闭源 | 低 | 商业友好 |
| **RFC 9420 / RFC 9421** | IETF Trust License | 自由使用 | 无 | 商用无限制 |
| **WireGuard** | GPL-2.0 | **不可直接抄代码** | 高 | 只参考算法描述(whitepaper §5.4) |

### 4.2 集成复杂度

- **FFI 路径**:Haskell runtime(`libHS*` ~500 依赖)+ RTS `-N` 启动,首次 cold start 0.8-3s + GC pause 50-200ms,UX 卡顿
- **推荐**:子进程模式(simplex-bridge-status 模式)已 ship,继续走该模式
- **私钥管理**:私钥不跨进程边界,sqlite DB 整库 dump 模式,`~/.simplex/` 路径用 Prisir `%LOCALAPPDATA%\Prisir\sqlcipher\simplex.db`(防 SQLCipher 文件锁竞争)
- **SQLite lock**:`~/.simplex/sqlcipher.db` 文件锁竞争 — 建议把 simplex-chat 的 SQLCipher 默认目录让出

### 4.3 协议风险

- **SimpleX ≠ Noise**:不可假设 SimpleX 用了 Noise 协议(任务描述错误)
- **NoisePQ 还在演进**:Rev 34 NoisePQ 2024-04 才发布,实战案例不足;hybrid 模式(Kyber+X25519)是当前安全选择
- **BLAKE2b 在 RUSTLS 已有后端**;不要自己再写一份
- **未验证 CipherState 边界**:每次 EncryptAndHash 必须 n++ 自增,nonce 重用会破 ChaCha20-Poly1305 安全
- **PSK 长度 ≥ 32 byte**;**绝不**用小于 16 byte 的 PSK
- **跨设备密钥泄露风险**:Master key 泄露 → 永久身份冒用;必须存 OS secure enclave
- **TreeKEM 调试门槛**:密钥调度 + Secret Tree + Parent Hash + Tree Hash + Transcript Hash 五套联动,调 bug 难
- **Megolm 局限**:sender 知道所有密钥 → 与 SimpleX 无 trust 哲学冲突

---

## 5. 密信产品层决策建议(给用户拍板)

### 5.1 短期(2026 Q4)— 修漏洞 + 不集成

1. **密信安全漏洞审计前置**:用户原话「密信还有安全漏洞要修复目前也不做扩展」,所有扩展 ship 仍拒绝,包括 `simplex-runtime`
2. **子进程存储隔离**:`~/.simplex/` 路径用 Prisir `%LOCALAPPDATA%\Prisir\sqlcipher\`,防 SQLCipher 文件锁竞争
3. **集成路径二选一**:(A) static link libHS — 30-60MB 启动卡顿;(B) 起 simplex-chat 子进程 — 30MB 启动 0.5s,**推荐 B**
4. **Subgroup 群聊结构**:继续「mojom 规划 + 原生代签 + WebCrypto 加解密」 vs 学 SimpleX 协议层 N 个 1:1 → **推荐前者**(后者协议未公开)
5. **deniability 转 crypto_box**:密信 mojom `SignGroupMessage` 现在给 Ed25519,**考虑换成 crypto_box + 短期 X25519** 保「组内否认」语义 → **先不改**,等实务需求
6. **PQ 加密**:SimpleX 默认 PQ-off,只在 mojom 开始时 server 提议 PQ-on → **默认 PQ-off**,仅在用户选择「最大隐私」模式开

### 5.2 中期(2027 Q1-Q2)— 算法借鉴 + 原型验证

1. **PQXDH spike**:在 `prisir-browser/mixin/_crypto/` 用 `x25519-dalek` + `libcrux-ml-kem` 实现 PQXDH 最小 demo,验证 4 DH + 1 Kyber 混合
2. **TreeKEM 试点**:OpenMLS 0.9.1 集成 + napi-rs 暴露给 Node 子进程(JSON-RPC 模式),5 人小群组 PoC 验证
3. **Noise_XX 邀请 spike**:用 snow crate 写最小 Noise_XX 邀请 demo,验证 3 条消息序列化 + PSK 注入
4. **SAS/QR 验证 UI**:借 matrix-sdk-qrcode + matrix-sdk-crypto/verification/sas 5 状态机,做密信跨设备验证 UI

### 5.3 长期(2027 Q3+)— 协议升级 + 服务端协同

1. **服务端升级路线图**:SimpleX SMP server → 加 Sealed Sender V2 模式(可选,服务端也要 AGPL)
2. **群组加密全面升级**:>50 人大群切 MLS TreeKEM,小群维持 SimpleX 双 ratchet
3. **跨设备同步**:cross-signing 三层密钥 + SAS 7 emoji + QR 单向扫码
4. **Signal Backup v2 借鉴**:给密信做端到端加密 backup
5. **PSI 联系人发现**:离线 PSI(用户本地哈希通讯录 → 短哈希集给服务端 → 返回交集),服务端永远拿不到全量通讯录
6. **PNI 拆双号**:密信如果未来想支持 phone-based discovery,可以学 PNI(私号注册 + 公号展示),防 phone number scraping

---

## 6. 关键文件位置(给密信团队参考)

### 6.1 协议 spec / 实现仓

| 资产 | URL | License |
|---|---|---|
| SimpleX 协议总览 | `https://github.com/simplex-chat/simplex-chat/blob/stable/docs/protocol/simplex-chat.md` | AGPL-3.0 |
| SMP 协议 spec | `https://github.com/simplex-chat/simplexmq/blob/master/protocol/simplex-messaging.md` | AGPL-3.0 |
| PQDR spec | `https://github.com/simplex-chat/simplexmq/blob/master/protocol/pqdr.md` | AGPL-3.0 |
| matrix-rust-sdk | `https://github.com/matrix-org/matrix-rust-sdk` | Apache-2.0 |
| Megolm spec | `https://github.com/matrix-org/matrix-spec/blob/main/content/olm-megolm/megolm.md` | Apache-2.0 |
| Cross-signing MSC | `https://github.com/matrix-org/matrix-spec-proposals/blob/main/proposals/1756-cross-signing.md` | Apache-2.0 |
| QR 验证 MSC | `https://github.com/matrix-org/matrix-spec-proposals/blob/main/proposals/1543-qr_code_key_verification.md` | Apache-2.0 |
| SAS MSC | `https://github.com/matrix-org/matrix-spec-proposals/blob/main/proposals/1717-key_verification.md` | Apache-2.0 |
| Sliding sync MSC | `https://github.com/matrix-org/matrix-spec-proposals/blob/main/proposals/4186-simplified-sliding-sync.md` | Apache-2.0 |
| libsignal | `https://github.com/signalapp/libsignal` | AGPL-3.0-only |
| SPQR | `https://github.com/signalapp/SparsePostQuantumRatchet` | AGPL-3.0 |
| OpenMLS | `https://github.com/openmls/openmls` | MIT |
| mls-rs | `https://github.com/awslabs/mls-rs` | Apache-2.0 |
| Noise Spec | `https://noiseprotocol.org/noise.html` | CC-BY-4.0 |
| snow crate | `https://github.com/noiseprotocol/noise-rust` | MIT |
| RFC 9420 | `https://www.rfc-editor.org/rfc/rfc9420.html` | IETF Trust License |
| RFC 9421 | `https://www.rfc-editor.org/rfc/rfc9421.html` | IETF Trust License |

### 6.2 密信本地文件

- **Mojo 契约**:`prisir-browser/mixin/mixin.mojom`(2 人 FFI + 文件 + WebRTC 信令 + DB 口令 + 群聊身份代签)
- **群聊前端**:`prisir-browser/mixin/group.html`(纯 WebCrypto + guohua 主题)
- **2 人前端**:`prisir-browser/mixin/dm.html`(从 `securedm_web.py` `_PAGE` 抽离 1235 行)
- **桥层**:`prisir-browser/mixin/_dm_bridge.js` + `_build_dm.py`(三级降级:原生 Mojo / 本地 HTTP / mock)
- **已 ship 桥接扩展**:`extensions/simplex-bridge-status/`(走 simplex-chat CLI 5225 HTTP API,4 L0)
- **实施方案(2026-08-14)**:`C:\Users\Administrator\Documents\ObsidianVault\handoff\2026-08-14-mixin-into-browser-plan.md`

### 6.3 仓文件锚点

| 关注点 | 路径 |
|---|---|
| Megolm 算法 spec | `matrix-spec/content/olm-megolm/megolm.md` |
| Olm 算法 spec | `matrix-spec/content/olm-megolm/olm.md` |
| Sliding sync | `matrix-spec-proposals/proposals/4186-simplified-sliding-sync.md` |
| libsignal PQXDH | `libsignal/rust/protocol/src/pqxdh.rs:200-238` |
| libsignal Kyber KEM | `libsignal/rust/protocol/src/kem.rs:23KB` |
| libsignal 双棘轮 | `libsignal/rust/protocol/src/double_ratchet.rs:14.8KB` |
| libsignal Sealed Sender | `libsignal/rust/protocol/src/sealed_sender.rs:88KB` |
| libsignal 跳数常量 | `libsignal/rust/protocol/src/consts.rs:38 行` |
| OpenMLS 主 API | `openmls/openmls/src/lib.rs` |
| OpenMLS Rust crypto | `openmls/openmls_rust_crypto/` |
| OpenMLS wasm | `openmls/wasm` (feature="js") |
| snow handshake | `noise-rust/crates/snow/src/handshake.rs` |
| snow transport | `noise-rust/crates/snow/src/transport.rs` |
| snow ratchet | `noise-rust/crates/snow/src/ratchet.rs` |
| SimpleX 协议 SSOT | `simplexmq/protocol/simplex-messaging.md:135` |
| SimpleX PQDR | `simplexmq/protocol/pqdr.md:73-211` |
| SimpleX agent API | `simplexmq/Simplex/Messaging/Agent.hs:33-141`(140 高阶函数) |
| SimpleX e2e 长度表 | `simplexmq/protocol/agent-protocol.md:198-202` |

---

## 7. 决策点(给用户拍板)

### 7.1 短期决策(2026 Q4 必拍)

1. **集成路径**:**A(static link libHS 30-60MB 启动卡顿)vs B(simplex-chat 子进程 30MB 启动 0.5s)**
   - 推荐 B
2. **群聊结构**:继续「mojom 规划 + 原生代签 + WebCrypto 加解密」 vs 学 SimpleX 协议层 N 个 1:1
   - 推荐前者
3. **deniability 转 crypto_box**:**先不改**,等实务需求
4. **PQ 加密默认开关**:**默认 PQ-off**,仅在用户选择「最大隐私」模式开
5. **SQLCipher 文件锁隔离**:`~/.simplex/` → `%LOCALAPPDATA%\Prisir\sqlcipher\simplex.db`

### 7.2 中期决策(2027 Q1-Q2 必拍)

1. **PQXDH 路线**:走 libsignal AGPL-3.0-only 路径 vs 走 OpenMLS MIT 路径 + 自研 PQXDH 适配
   - 推荐后者
2. **群组加密二选一**:Matrix Megolm Sender Key vs MLS TreeKEM
   - 推荐 MLS(长期可维护)
3. **邀请协议**:Noise_XX + 1 次性 PSK vs 继续 SimpleX confirmation 4 步
   - 推荐继续 SimpleX(已自洽)
4. **跨设备验证 UI**:Matrix SAS 7 emoji + QR 单向扫码 vs Signal Sealed Sender V2
   - 推荐 Matrix(开源 + 完整)

### 7.3 长期决策(2027 Q3+ 视需求)

1. **服务端协同 Sealed Sender V2**:服务端改造 + 客户端 AGPL 代理(服务端要 AGPL,客户端代理不传染)
2. **PSI 联系人发现**:离线 PSI(用户本地哈希通讯录 → 短哈希集给服务端 → 返回交集)
3. **PNI 拆双号**:密信如果未来想支持 phone-based discovery
4. **Signal Backup v2 借鉴**:给密信做端到端加密 backup

### 7.4 红线(必须确认)

1. **AGPL-3.0 传染边界**:Prisir 浏览器当前 license 是?(若非 AGPL,**禁止 libsignal A/B/C 路径**)
2. **服务端改造权限**:能否动 SMP server 代码?(若只能动客户端,只能走纯客户端代理 D 路径)
3. **Signal 协议兼容性**:是否需要和 Signal 客户端互操作?(否的话,可以更大胆借鉴)
4. **密信安全漏洞**:用户原话「密信还有安全漏洞要修复」— 修漏洞前置,所有扩展 ship 仍拒绝

---

## 8. 5 大侦察合流结论

**5 大协议侦察合流 = 1 个密信集成参考知识库**,给密信产品层(`prisir-browser/mixin/`)决策用。

**密信当前架构(2026-08-14 拍板)**:
- 群聊:WebCrypto + Ed25519/X25519/AES-GCM,前端纯 JS,原生层只托管身份
- 2 人:libsimplex.dll FFI 链进浏览器原生层,Mojo 桥接(路径 A) — **但侦察发现 Haskell FFI 风险高**(启动 0.8-3s + GC pause 50-200ms)
- 私钥不出原生层,经 `os_crypt` 落本地 profile
- 中继:SimpleX `smp.babelspan.com:5223` + 群聊 VPS relay

**侦察建议**:路径 A → 路径 B(simplex-chat 子进程模式),已被 `simplex-bridge-status` Phase A 验证可行;继续走该模式,密信产品层 2 人视图前端 `prisir-browser/mixin/dm.html` 已就绪,实例侧 MixinHandler 只需按 `_dm_bridge.js` 的 `_native` switch 注入对应 Mojo 方法即可对接。

**群组加密 2.0 路线**:先 OpenMLS PoC 验证 TreeKEM 关键逻辑 → 5/50/500 人群组实测 → 大群(>50 人)切 MLS,小群维持 SimpleX 双 ratchet。

**后量子 2026+ 必修**:走 PQXDH 路径(X25519 + Kyber1024 hybrid),HKDF label 钉死 `WhisperText_X25519_SHA-256_CRYPTALS-KYBER-1024`,discontinuity bytes `[0xFF; 32]` 防 downgrade 攻击。

**AGPL 合规红线**:
- libsignal = AGPL-3.0-only,**禁止 A/B/C 路径**,**只允许 D 纯客户端代理**
- simplexmq = AGPL-3.0,子进程模式不传染
- OpenMLS = MIT,mls-rs = Apache-2.0,可直接集成

**绝不 ship 任何扩展** — 本次侦察产物全部为静态材料 + memo 文本 + 决策点交回用户拍板。

---

## 相关

- 用户原话(2026-10-10):「请将拟定的研究目标逐项推进了解详细,提供给密信优化参考」
- 用户原话(2026-10-10):「直接 ship,关于前面项目的选择还是和之前的筛选标准一样,没有设计给agent操作的就不做扩展。同时把莱茵金属的开源通信项目看是否有能抽给密信做优化的模块,由于密信还有安全漏洞要修复目前也不做扩展。」
- 上游侦察:[[rheinmetall-modulerecon-decision]](无可抽取模块给密信)
- 密信产品层:[[extension-ship-filter]]「密信/含安全漏洞项目暂不 ship」标
- 密信实施方案(2026-08-14):`C:\Users\Administrator\Documents\ObsidianVault\handoff\2026-08-14-mixin-into-browser-plan.md`
- 密信 Mojo 契约:`prisir-browser/mixin/mixin.mojom`
- 密信 2 人前端:`prisir-browser/mixin/dm.html` + `_dm_bridge.js`
- 密信群聊前端:`prisir-browser/mixin/group.html`
- 已 ship 桥接扩展:[[simplex-bridge-status-phase-a-shipped]]
- 同期侦察:hs-libsimplex / Matrix / libsignal-rust / MLS / Noise(5 大协议,5 个后台 agent)
- 决策点(2026-10-10 拍板状态):**未拍板**,等用户回看合流报告
