// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.

#ifndef CHROME_BROWSER_PRISIR_AGENT_AGENT_STORE_PREFS_H_
#define CHROME_BROWSER_PRISIR_AGENT_AGENT_STORE_PREFS_H_

class PrefRegistrySimple;

namespace prisir::agent {

// 登记 AgentStore 相关 pref 默认值。在 profile pref 注册处调用
// (chrome/browser/prefs/browser_prefs.cc 的 RegisterProfilePrefs,见 README 挂接)。
void RegisterAgentStorePrefs(PrefRegistrySimple* registry);

}  // namespace prisir::agent

#endif  // CHROME_BROWSER_PRISIR_AGENT_AGENT_STORE_PREFS_H_
