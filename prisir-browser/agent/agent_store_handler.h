// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.
//
// Prisir 智能体存储 Mojo handler(M2c)— agent_store.mojom 的浏览器侧实现。
//
// 契约:prisir-browser/agent/agent_store.mojom(已定稿,2026-08-14)。
// 后端:prisir-browser/agent/agent_store.{h,cc}(M2a)。
// 定位:NTP WebUI(M4)/ agent 运行时(M3)/ 插件(M5)经 Mojo 调本 handler 读写
//       模型配置 + 会话存档;handler 是唯一经 Mojo 暴露的入口,内部持 AgentStore。
//
// 红线(贯穿,见 mojom 头注):
//   - apiKey 只进不出:提供 HasApiKey/SetApiKey/ClearApiKey,**无 GetApiKey**。
//     页面/插件拿不到明文;本体代发走 AgentStore::GetApiKeyForInternalUse()(不经 Mojo)。
//   - 会话本地:读写都落 profile threads.json。
//   - 持久化开关:AppendMessage/Create 等写操作前查 persist_history,关则仍执行
//     (语义对齐 chatstore:enabled() 由调用层判定;此处诚实执行 + 暴露开关供 UI)。

#ifndef CHROME_BROWSER_PRISIR_AGENT_AGENT_STORE_HANDLER_H_
#define CHROME_BROWSER_PRISIR_AGENT_AGENT_STORE_HANDLER_H_

#include "base/memory/raw_ptr.h"
#include "chrome/browser/prisir/agent/agent_store.h"
#include "chrome/browser/prisir/agent/mojom/agent_store.mojom.h"
#include "mojo/public/cpp/bindings/pending_receiver.h"
#include "mojo/public/cpp/bindings/receiver.h"

namespace prisir::agent {

class AgentStoreHandler : public prisir::agent::mojom::AgentStore {
 public:
  // store:本 profile 的 AgentStore(M2a),不拥有,由 profile-keyed service 持有。
  explicit AgentStoreHandler(AgentStore* store);
  ~AgentStoreHandler() override;

  AgentStoreHandler(const AgentStoreHandler&) = delete;
  AgentStoreHandler& operator=(const AgentStoreHandler&) = delete;

  // 绑定一个 Mojo receiver(WebUI 页面侧请求)。
  void Bind(mojo::PendingReceiver<prisir::agent::mojom::AgentStore> receiver);

  // ── prisir::agent::mojom::AgentStore ──
  void GetModelConfig(GetModelConfigCallback callback) override;
  void SetModelConfig(prisir::agent::mojom::ModelConfigPtr config,
                      SetModelConfigCallback callback) override;

  void HasApiKey(HasApiKeyCallback callback) override;
  void SetApiKey(const std::string& key, SetApiKeyCallback callback) override;
  void ClearApiKey(ClearApiKeyCallback callback) override;

  void ListConversations(ListConversationsCallback callback) override;
  void GetConversation(const std::string& id,
                       GetConversationCallback callback) override;
  void CreateConversation(const std::string& title,
                          CreateConversationCallback callback) override;
  void AppendMessage(const std::string& id,
                     prisir::agent::mojom::ChatMessagePtr msg,
                     AppendMessageCallback callback) override;
  void RenameConversation(const std::string& id,
                          const std::string& title,
                          RenameConversationCallback callback) override;
  void DeleteConversation(const std::string& id,
                          DeleteConversationCallback callback) override;

  void GetMigrationStatus(GetMigrationStatusCallback callback) override;
  void RunMigration(RunMigrationCallback callback) override;

  // ── 长期记忆(存储通路;提炼逻辑在 agent_memory.js) ──
  void ListMemories(ListMemoriesCallback callback) override;
  void RememberMemory(const std::string& kind, const std::string& text,
                      RememberMemoryCallback callback) override;
  void ForgetMemory(const std::string& id, ForgetMemoryCallback callback) override;
  void ClearMemories(ClearMemoriesCallback callback) override;

 private:
  // mojom ↔ M2a 结构转换。
  static prisir::agent::mojom::ModelConfigPtr ToMojom(
      const AgentStore::ModelConfig& c);
  static prisir::agent::mojom::ConversationPtr ToMojom(
      const AgentStore::Conversation& c);
  static AgentStore::ChatMessage FromMojom(
      const prisir::agent::mojom::ChatMessage& m);

  raw_ptr<AgentStore> store_;  // 不拥有
  mojo::Receiver<prisir::agent::mojom::AgentStore> receiver_{this};
};

}  // namespace prisir::agent

#endif  // CHROME_BROWSER_PRISIR_AGENT_AGENT_STORE_HANDLER_H_
