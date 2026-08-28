// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.
//
// AgentStore 单元测试(M2a 验证)。
// 语义对齐 chatstore.js:LRU 50会话/200条、自动标题、惰性命名、pruneEmpty、原子写。
// 凭证红线:SetApiKey 后文件无明文(以「读文件内容不含 key 字面值」断言)。

#include "chrome/browser/prisir/agent/agent_store.h"

#include "base/files/file_util.h"
#include "base/files/scoped_temp_dir.h"
#include "base/test/task_environment.h"
#include "components/os_crypt/sync/os_crypt_mocker.h"
#include "components/prefs/pref_registry_simple.h"
#include "components/prefs/testing_pref_service.h"
#include "testing/gtest/include/gtest/gtest.h"

namespace prisir::agent {
namespace {

// 测试用 pref 键(与 agent_store_prefs.cc 一致)。
extern const char kPrefBaseURL[];
extern const char kPrefModel[];
extern const char kPrefVisionModel[];
extern const char kPrefPersistHistory[];

class AgentStoreTest : public testing::Test {
 protected:
  void SetUp() override {
    ASSERT_TRUE(temp_dir_.CreateUniqueTempDir());
    OSCryptMocker::SetUp();  // 测试用 mock os_crypt(不依赖真实 DPAPI)
    prefs_.registry()->RegisterStringPref("prisir.agent.base_url", "");
    prefs_.registry()->RegisterStringPref("prisir.agent.model", "");
    prefs_.registry()->RegisterStringPref("prisir.agent.vision_model", "");
    prefs_.registry()->RegisterBooleanPref("prisir.agent.persist_history", true);
    store_ = std::make_unique<AgentStore>(&prefs_, temp_dir_.GetPath());
  }
  void TearDown() override { OSCryptMocker::TearDown(); }

  base::test::TaskEnvironment task_environment_;
  base::ScopedTempDir temp_dir_;
  TestingPrefServiceSimple prefs_;
  std::unique_ptr<AgentStore> store_;
};

// ── 模型配置 ──
TEST_F(AgentStoreTest, ModelConfigRoundTrip) {
  ModelConfig c;
  c.base_url = "https://api.example.com/v1";
  c.model = "text-model";
  c.vision_model = "vision-model";
  c.persist_history = false;
  ASSERT_TRUE(store_->SetModelConfig(c));
  ModelConfig got = store_->GetModelConfig();
  EXPECT_EQ(got.base_url, c.base_url);
  EXPECT_EQ(got.model, c.model);
  EXPECT_EQ(got.vision_model, c.vision_model);
  EXPECT_FALSE(got.persist_history);
}

// ── apiKey:加密落盘 + 文件无明文(红线) ──
TEST_F(AgentStoreTest, ApiKeyEncryptedNoPlaintextOnDisk) {
  const std::string secret = "sk-TESTSECRET-0123456789abcdef";
  EXPECT_FALSE(store_->HasApiKey());
  ASSERT_TRUE(store_->SetApiKey(secret));
  EXPECT_TRUE(store_->HasApiKey());

  // 红线断言:盘上文件不含明文 key 字面值。
  std::string disk;
  ASSERT_TRUE(base::ReadFileToString(store_->GetApiKeyFilePath(), &disk));
  EXPECT_EQ(disk.find(secret), std::string::npos)
      << "apiKey must not appear in plaintext on disk";

  // 内部取用能还原明文(本体代发用)。
  EXPECT_EQ(store_->GetApiKeyForInternalUse(), secret);

  // 清除。
  ASSERT_TRUE(store_->ClearApiKey());
  EXPECT_FALSE(store_->HasApiKey());
}

// ── 会话:创建 + 追加 + 自动标题 ──
TEST_F(AgentStoreTest, CreateAppendAutoTitle) {
  std::string id = store_->CreateConversation();
  ASSERT_FALSE(id.empty());

  ChatMessage m;
  m.role = "user";
  m.text = "帮我总结这篇文章的主要内容";
  ASSERT_TRUE(store_->AppendMessage(id, m));

  auto conv = store_->GetConversation(id);
  ASSERT_TRUE(conv);
  EXPECT_EQ(conv->messages.size(), 1u);
  // 自动标题(首条 user 消息)。
  EXPECT_FALSE(conv->title.empty());
}

// ── LRU:会话数超 50 裁最旧 ──
TEST_F(AgentStoreTest, LruEvictsOldestConversation) {
  std::vector<std::string> ids;
  for (int i = 0; i < AgentStore::kMaxConvs + 3; ++i) {
    ids.push_back(store_->CreateConversation());
  }
  auto list = store_->ListConversations();
  EXPECT_EQ(list.size(), static_cast<size_t>(AgentStore::kMaxConvs));
  // 最早的 3 个应被淘汰。
  for (int i = 0; i < 3; ++i) {
    EXPECT_EQ(store_->GetConversation(ids[i]), nullptr);
  }
}

// ── 单会话消息超 200 裁最旧 ──
TEST_F(AgentStoreTest, MessageCapEvictsOldest) {
  std::string id = store_->CreateConversation();
  ChatMessage m;
  m.role = "user";
  for (int i = 0; i < AgentStore::kMaxMsgs + 10; ++i) {
    m.text = "msg " + base::NumberToString(i);
    ASSERT_TRUE(store_->AppendMessage(id, m));
  }
  auto conv = store_->GetConversation(id);
  ASSERT_TRUE(conv);
  EXPECT_EQ(conv->messages.size(), static_cast<size_t>(AgentStore::kMaxMsgs));
}

// ── pruneEmpty:清 0 消息空会话 ──
TEST_F(AgentStoreTest, PruneEmpty) {
  std::string a = store_->CreateConversation();  // 空
  std::string b = store_->CreateConversation();  // 空
  std::string c = store_->CreateConversation();  // 将填消息
  ChatMessage m; m.role = "user"; m.text = "hi";
  ASSERT_TRUE(store_->AppendMessage(c, m));
  int pruned = store_->PruneEmpty();
  EXPECT_EQ(pruned, 2);
  EXPECT_EQ(store_->GetConversation(a), nullptr);
  EXPECT_NE(store_->GetConversation(c), nullptr);
}

// ── 重命名 / 删除 ──
TEST_F(AgentStoreTest, RenameDelete) {
  std::string id = store_->CreateConversation();
  ASSERT_TRUE(store_->RenameConversation(id, "我的会话"));
  auto conv = store_->GetConversation(id);
  ASSERT_TRUE(conv);
  EXPECT_EQ(conv->title, "我的会话");
  ASSERT_TRUE(store_->DeleteConversation(id));
  EXPECT_EQ(store_->GetConversation(id), nullptr);
}

}  // namespace
}  // namespace prisir::agent
