// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.

#include "chrome/browser/prisir/agent/agent_store_handler.h"

#include <utility>

#include "base/logging.h"

namespace prisir::agent {

AgentStoreHandler::AgentStoreHandler(AgentStore* store) : store_(store) {}
AgentStoreHandler::~AgentStoreHandler() = default;

void AgentStoreHandler::Bind(
    mojo::PendingReceiver<prisir::agent::mojom::AgentStore> receiver) {
  receiver_.Bind(std::move(receiver));
}

// ── 结构转换 ──────────────────────────────────────────────────────────────

prisir::agent::mojom::ModelConfigPtr AgentStoreHandler::ToMojom(
    const AgentStore::ModelConfig& c) {
  auto m = prisir::agent::mojom::ModelConfig::New();
  m->base_url = c.base_url;
  m->model = c.model;
  m->vision_model = c.vision_model;
  m->persist_history = c.persist_history;
  return m;
}

AgentStore::ChatMessage AgentStoreHandler::FromMojom(
    const prisir::agent::mojom::ChatMessage& m) {
  AgentStore::ChatMessage out;
  out.role = m.role;
  out.text = m.text;
  out.citations = m.citations;
  return out;
}

prisir::agent::mojom::ConversationPtr AgentStoreHandler::ToMojom(
    const AgentStore::Conversation& c) {
  auto m = prisir::agent::mojom::Conversation::New();
  m->id = c.id;
  m->title = c.title;
  m->created_at = c.created_at;  // ISO8601 字符串,mojom 已对齐 string
  m->updated_at = c.updated_at;
  for (const auto& msg : c.messages) {
    auto mm = prisir::agent::mojom::ChatMessage::New();
    mm->role = msg.role;
    mm->text = msg.text;
    mm->citations = msg.citations;
    m->messages.push_back(std::move(mm));
  }
  return m;
}

// ── 模型配置 ──────────────────────────────────────────────────────────────

void AgentStoreHandler::GetModelConfig(GetModelConfigCallback callback) {
  if (!store_) { std::move(callback).Run(ToMojom(AgentStore::ModelConfig())); return; }
  std::move(callback).Run(ToMojom(store_->GetModelConfig()));
}

void AgentStoreHandler::SetModelConfig(
    prisir::agent::mojom::ModelConfigPtr config,
    SetModelConfigCallback callback) {
  if (!store_ || !config) { std::move(callback).Run(false); return; }
  AgentStore::ModelConfig c;
  c.base_url = config->base_url;
  c.model = config->model;
  c.vision_model = config->vision_model;
  c.persist_history = config->persist_history;
  std::move(callback).Run(store_->SetModelConfig(c));
}

// ── apiKey(只进不出,无明文 Get) ─────────────────────────────────────────

void AgentStoreHandler::HasApiKey(HasApiKeyCallback callback) {
  std::move(callback).Run(store_ ? store_->HasApiKey() : false);
}

void AgentStoreHandler::SetApiKey(const std::string& key,
                                  SetApiKeyCallback callback) {
  // 红线:不记 key 明文日志;失败仅返回 false。
  if (!store_) { std::move(callback).Run(false); return; }
  std::move(callback).Run(store_->SetApiKey(key));
}

void AgentStoreHandler::ClearApiKey(ClearApiKeyCallback callback) {
  if (!store_) { std::move(callback).Run(false); return; }
  std::move(callback).Run(store_->ClearApiKey());
}

// ── 会话 CRUD ───────────────────────────────────────────────────────────

void AgentStoreHandler::ListConversations(
    ListConversationsCallback callback) {
  std::vector<prisir::agent::mojom::ConversationSummaryPtr> out;
  if (store_) {
    for (const auto& s : store_->ListConversations()) {
      auto m = prisir::agent::mojom::ConversationSummary::New();
      m->id = s.id;
      m->title = s.title;
      m->updated_at = s.updated_at;  // ISO8601,mojom 已对齐 string
      m->message_count = s.message_count;
      out.push_back(std::move(m));
    }
  }
  std::move(callback).Run(std::move(out));
}

void AgentStoreHandler::GetConversation(const std::string& id,
                                        GetConversationCallback callback) {
  if (!store_) { std::move(callback).Run(nullptr); return; }
  auto conv = store_->GetConversation(id);
  if (!conv) { std::move(callback).Run(nullptr); return; }
  std::move(callback).Run(ToMojom(*conv));
}

void AgentStoreHandler::CreateConversation(
    const std::string& title,
    CreateConversationCallback callback) {
  if (!store_) { std::move(callback).Run(nullptr); return; }
  std::string id = store_->CreateConversation();
  if (id.empty()) { std::move(callback).Run(nullptr); return; }
  auto conv = store_->GetConversation(id);
  if (conv && !title.empty()) store_->RenameConversation(id, title);
  std::move(callback).Run(conv ? ToMojom(*conv) : nullptr);
}

void AgentStoreHandler::AppendMessage(
    const std::string& id,
    prisir::agent::mojom::ChatMessagePtr msg,
    AppendMessageCallback callback) {
  if (!store_ || !msg) { std::move(callback).Run(false); return; }
  std::move(callback).Run(store_->AppendMessage(id, FromMojom(*msg)));
}

void AgentStoreHandler::RenameConversation(
    const std::string& id,
    const std::string& title,
    RenameConversationCallback callback) {
  if (!store_) { std::move(callback).Run(false); return; }
  std::move(callback).Run(store_->RenameConversation(id, title));
}

void AgentStoreHandler::DeleteConversation(
    const std::string& id,
    DeleteConversationCallback callback) {
  if (!store_) { std::move(callback).Run(false); return; }
  std::move(callback).Run(store_->DeleteConversation(id));
}

// ── 迁移(M2b 编排;真实迁移读插件快照需扩展通道,见说明) ──────────────────
//
// 时间戳契约已对齐:mojom 的 Conversation.created_at/updated_at 与
// ConversationSummary.updated_at 均为 ISO8601 string(对齐 chatstore/M2a);
// ChatMessage 无单条时间戳(chatstore 本就不落)。

void AgentStoreHandler::GetMigrationStatus(
    GetMigrationStatusCallback callback) {
  auto status = prisir::agent::mojom::MigrationStatus::New();
  // 真实实现:读 pref prisir.agent.migration_done / migration_count,
  // 检测插件副本是否仍在。此处给骨架,真编译时接 prefs。
  status->migrated = false;
  status->plugin_copy_present = false;
  status->migrated_conversations = 0;
  status->last_error = "";
  std::move(callback).Run(std::move(status));
}

void AgentStoreHandler::RunMigration(RunMigrationCallback callback) {
  // 真实迁移需经扩展通道读插件 chrome.storage.local 快照(见 M2b)。
  // 骨架:返回未迁移状态 + 提示需插件导出通道。真编译时:
  //   1. 经 extension messaging / native messaging 取插件快照
  //   2. 写入 AgentStore(本 handler 的 store_)
  //   3. 逐键校验(对齐 migration_m2b.js 的 threadsEqual/逐键比对)
  //   4. 置 migration_done pref
  auto status = prisir::agent::mojom::MigrationStatus::New();
  status->migrated = false;
  status->plugin_copy_present = false;
  status->migrated_conversations = 0;
  status->last_error = "migration channel to extension not wired (M2b source)";
  LOG(WARNING) << "AgentStoreHandler::RunMigration: " << status->last_error;
  std::move(callback).Run(std::move(status));
}

// ── 长期记忆(存储通路;提炼/敏感过滤/去重/LRU 逻辑在 agent_memory.js) ──────────
//
// 说明:记忆提炼的「业务逻辑」(敏感拦截、同 kind+文本合并、LRU、recall 相关性)
//      已在 agent_memory.js 实现并 15/15 测绿。本 handler 只提供 profile 持久化通路
//      (memory.json 原子读写)。真编译时两种接法:
//        ① handler 内嵌 agent_memory.js 等价 C++ 逻辑,直接服务 WebUI;
//        ② WebUI 侧跑 agent_memory.js,把本 handler 当纯 KV 存储(读全量/写全量)。
//      当前为契约骨架,返回空/未接线,待真编译定接法后补全。

void AgentStoreHandler::ListMemories(ListMemoriesCallback callback) {
  std::vector<prisir::agent::mojom::MemoryItemPtr> out;
  // TODO(真编译):读 profile memory.json → 逐条填 MemoryItem。
  std::move(callback).Run(std::move(out));
}

void AgentStoreHandler::RememberMemory(const std::string& kind,
                                       const std::string& text,
                                       RememberMemoryCallback callback) {
  // TODO(真编译):经 agent_memory 逻辑(敏感过滤/合并/LRU)后写 memory.json。
  std::move(callback).Run(false, "");
}

void AgentStoreHandler::ForgetMemory(const std::string& id,
                                     ForgetMemoryCallback callback) {
  // TODO(真编译):从 memory.json 删该 id,原子写回。
  std::move(callback).Run(false);
}

void AgentStoreHandler::ClearMemories(ClearMemoriesCallback callback) {
  // TODO(真编译):清空 memory.json,返回清除条数。
  std::move(callback).Run(0);
}

}  // namespace prisir::agent
