// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.
//
// Prisir 网络链路订阅解析 + sing-box 配置生成(#59,纯字符串,不碰内核)。
//
// 契约:prisir-browser/proxy/prisir_proxy.mojom(2026-08-21)。
// 定位:
//   - ParseSubscriptionBody:机场订阅体(base64 编码的 ss://vmess://vless://trojan://
//     hysteria2:// 节点列表)→ 解析成 ProxyNode 数组(展示元数据,不含敏感字段)。
//   - BuildSingboxConfigJson:由选中节点敏感配置 + 本地 inbound 端口,生成 sing-box
//     运行 JSON(inbound 本地 SOCKS + outbound 选中节点)。敏感字段只在此进 JSON,
//     写完即落 singbox.json 由内核子进程读,不经 Mojo 回页面。

#ifndef CHROME_BROWSER_PRISIR_PROXY_SUBSCRIPTION_PARSE_H_
#define CHROME_BROWSER_PRISIR_PROXY_SUBSCRIPTION_PARSE_H_

#include <string>
#include <vector>

#include "chrome/browser/prisir/proxy/mojom/prisir_proxy.mojom.h"

namespace prisir::proxy {

// 解析机场订阅体(base64 节点列表)→ ProxyNode 数组(展示元数据)。
// body 为原始订阅内容(可能整段 base64,或逐行 share-link);解析失败的行跳过。
std::vector<prisir::proxy::mojom::ProxyNodePtr> ParseSubscriptionBody(
    const std::string& body);

// 生成 sing-box 运行 JSON:本地 SOCKS inbound(127.0.0.1:local_port)+ 按 cfg.protocol
// 的 outbound。cfg 含敏感字段(uuid/password/extra),只进本 JSON,不回 Mojo。
// 返回空串表示协议不支持/配置非法。
std::string BuildSingboxConfigJson(
    const prisir::proxy::mojom::ProxyNodeConfig& cfg,
    int local_port);

}  // namespace prisir::proxy

#endif  // CHROME_BROWSER_PRISIR_PROXY_SUBSCRIPTION_PARSE_H_
