---
name: mls-addon-provider-isolation-required
description: OpenMLS 0.9.1 0.9.1 PoC single-process 多虚拟用户必须 per-handle provider + 显式 ratchet tree + merge_pending_commit 顺序固定
metadata:
  type: project
---

# OpenMLS 0.9.1 PoC 多虚拟用户 provider 隔离三铁律

**日期**: 2026-10-10
**范围**: `D:\PrisirMixin\src\protocol\mls_addon.rs` (PrisirMixin Phase 2 PoC)
**状态**: 三铁律都满足后 2-party E2E 仍报 `JoinBuilder::build (Bob): Invalid node signature`
**未决**: 是否能 ship 完整 2-party 测试 (需进 commit_builder 内部调查)

## 三铁律(经实测多轮 fail 锁定)

### 铁律 1: **per-handle provider,不能全局共享**
- `OpenMlsRustCrypto::default()` 每次返新 provider,新 MemoryStorage
- 全局 `Lazy<Mutex<Provider>>` 让 Alice/Bob 写到同一 store → 共享 group_id 触发 `MlsGroup::load` 的 `GroupAlreadyExists` 错
- 必须 `PROVIDERS: HashMap<handle, Provider>`,`mls_create_group` 用 `MlsGroup::builder().with_group_id().replace_old_group().build()`(pub)
- 共享 KeyPackage store 是允许的(不冲突,因为不存 group 状态),但新成员 KP 必须写在 **新成员自己的 provider**(后续 new_from_welcome 在该 provider 上跑)

### 铁律 2: **新成员 KeyPackage 必须写到 Bob 的 provider(不是 Alice 的)**
- `KeyPackage::builder().build(ciphersuite, provider, signer, cw)` 自动 `write_key_package(provider.storage())`
- `StagedWelcome::new_from_welcome(provider, ...)` 内部 `keys_for_welcome` 在 **该 provider** 的 storage 找 KP
- 错把 KP 写到 Alice provider → Bob 端 `No matching key package was found in the key store`

### 铁律 3: **commit_builder → into_welcome 顺序 + merge_pending_commit 在 export_ratchet_tree 前**
- `group.commit_builder().stage_commit(provider)` 不动 `group` 自己的 `public_group`,只把 `group_state` 置 `PendingCommit` 并存 store
- **必须** 紧跟 `group.merge_pending_commit(provider)`,然后才 `group.export_ratchet_tree()` 拿 post-commit 树
- 不 merge 之前 export → 旧树 → `from_ratchet_tree` 找不到 Bob 新 leaf → 错码多种

## 2-party 仍卡死的最后根因

满足三铁律后 `mls_add_member` 仍报:
```
JoinBuilder::build (Bob): Invalid node signature
```

`Invalid node signature` 来自 `treesync/mod.rs:181` (KeyPackage leaf 验签分支) 或 :186 (Update/Commit 验签分支)。

**已排除的可能**:
- ✗ `force_self_update(true)` 多算路径 → 删了 force_self_update 仍报
- ✗ `MlsMessageIn::into_welcome` test-only → 用 `CommitMessageBundle::into_welcome()` pub 路径绕过
- ✗ provider 共享 → 改 per-handle
- ✗ KP 写在 Alice provider → 改 Bob provider
- ✗ 不 merge 就 export tree → merge 后再 export
- ✗ `replace_old_group` flag → 已加
- ✗ `MlsGroup::new_with_group_id` 不带 flag → 改 `MlsGroup::builder().with_group_id().replace_old_group()`

**未排除**:
- ? `commit_builder` 跟 `add_members` 内部区别:前者可能用不同 leaf 生成路径(用新临时 signer?),后者用 `add_members_internal` 的标准路径
- ? `RatchetTreeIn::from(RatchetTree)` 转换时 leaf_node 字节是否变 (canonical test 走 `MlsMessageIn::into_welcome` test-only + 同一导出,无 round-trip 转换问题)
- ? `RatchetTreeIn` 里的 leaf 节点是 `LeafNodeIn` 类型,与 GroupInfo ratchet tree extension 里的 leaf 字节是否一致

## 推荐 Phase 3 解决路径(下一步)

**直接用 `MlsGroup::add_members(provider, signer, &[KeyPackage])` 替换 commit_builder**:
- 拿 `(MlsMessageOut commit, MlsMessageOut welcome, Option<GroupInfo>)`
- `welcome` 是 `MlsMessageOut` 包了 `Welcome`
- `Welcome` struct 提取:test-only 的 `MlsMessageIn::into_welcome` 被 disable,
  替代方案:**从 `MlsMessageOut` 直接构造回 `Welcome` struct** (Welcome 字段 cipher_suite/secrets/encrypted_group_info 都是 pub,需要 `MlsMessageOut` 的反序列化路径,或干脆走字节 round-trip 重新 tls_deserialize)

**或者**:**接受 PoC 不走完整加密握手**,只做单 Alice 群 + 内部 Alice 私钥 mock,展示架构,2-party 跨用户握手留到 Phase 3

## 与本仓库此前 MLS 侦察的差异

- 此前 `ls_rust_crypto` 侦察报告说 OpenMLS 0.9.1 `MlsMessageOut::into_welcome` 是 test-only — **已验证是 0.9.1 仍如此**
- `CommitMessageBundle::into_welcome` 返 `Option<Welcome>` (pub,非 test-only) — **这是绕过 test-only 限制的关键 hook**
- 0.9.1 `MlsMessageIn::into_welcome` / `MlsMessageOut::into_welcome` 都 `#[cfg(any(feature = "test-utils", test))]` — 不能用
- canonical 2-party test (openmls-0.9.1/src/group/mls_group/tests_and_kats/tests/mls_group.rs:90-160) **每用户独立 provider** + **`MlsGroup::add_members` 非 commit_builder** + **手动 `Some(alice_group.export_ratchet_tree().into())` 传 ratchet tree**

## 引用
- canonical test: `D:\PrisirMixin` 参考实现
- 真实 addon: `D:\PrisirMixin\src\protocol\mls_addon.rs`
- PoC Phase A ship memory: `prisirmixin-phase-a-shipped.md`
