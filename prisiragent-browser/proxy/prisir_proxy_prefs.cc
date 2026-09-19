// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.

#include "chrome/browser/prisir/proxy/prisir_proxy_prefs.h"

#include "components/prefs/pref_registry_simple.h"

namespace prisir::proxy {

// pref 键(非敏感)。密钥/订阅 token 落 os_crypt 文件,绝不在此登记。
const char kSelectedNodeId[] = "prisir.proxy.selected_node_id";
const char kLinkEnabled[] = "prisir.proxy.link_enabled";
const char kLocalInboundPort[] = "prisir.proxy.local_inbound_port";

void RegisterPrisirProxyPrefs(PrefRegistrySimple* registry) {
  registry->RegisterStringPref(kSelectedNodeId, "");
  registry->RegisterBooleanPref(kLinkEnabled, false);
  // 0 = 启动时随机挑空闲端口(避免固定端口冲突/被探测)。
  registry->RegisterIntegerPref(kLocalInboundPort, 0);
}

}  // namespace prisir::proxy
