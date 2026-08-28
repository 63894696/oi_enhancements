// Copyright 2026 Prisir Project. All rights reserved.
// Use of this source code is governed by a BSD-style license.
//
// 订阅解析 + sing-box 配置生成实现(#59)。纯字符串处理,不碰内核/网络。

#include "chrome/browser/prisir/proxy/subscription_parse.h"

#include "base/base64.h"
#include "base/json/json_writer.h"
#include "base/strings/string_number_conversions.h"
#include "base/strings/string_split.h"
#include "base/strings/string_util.h"
#include "base/values.h"
#include "net/base/url_decode.h"
#include "url/gurl.h"
#include "url/url_util.h"

namespace prisir::proxy {

namespace {

// 解析单条 share-link(ss:// vmess:// vless:// trojan:// hysteria2://)→ ProxyNode。
// 只抽展示元数据(tag/protocol/address/port);敏感部分不进返回结构。
prisir::proxy::mojom::ProxyNodePtr ParseShareLink(const std::string& line) {
  if (line.empty()) return nullptr;
  auto scheme_end = line.find("://");
  if (scheme_end == std::string::npos) return nullptr;
  std::string scheme = line.substr(0, scheme_end);
  std::string proto;
  if (scheme == "ss") proto = "ss2022";
  else if (scheme == "vmess") proto = "vmess";
  else if (scheme == "vless") proto = "vless";
  else if (scheme == "trojan") proto = "trojan";
  else if (scheme == "hysteria2" || scheme == "hy2") proto = "hysteria2";
  else if (scheme == "tuic") proto = "tuic";
  else if (scheme == "wireguard" || scheme == "wg") proto = "wireguard";
  else return nullptr;  // 不支持的协议跳过

  auto node = prisir::proxy::mojom::ProxyNode::New();
  node->protocol = proto;
  node->latency_ms = -1;

  // tag:#fragment(URL 解码)作显示名。
  auto hash = line.rfind('#');
  if (hash != std::string::npos) {
    node->tag = net::UnescapeURLComponent(
        line.substr(hash + 1),
        net::UnescapeRule::SPACES | net::UnescapeRule::URL_SPECIAL_CHARS_EXCEPT_PATH_SEPARATORS);
  }
  // address/port:取 userinfo@host:port 段(vmess 是 base64 JSON,单列处理)。
  if (scheme == "vmess") {
    // vmess://<base64-json>,含 add/port/ps。解析展示用。
    std::string payload = line.substr(scheme_end + 3);
    if (hash != std::string::npos) payload = payload.substr(0, payload.size() - (line.size() - hash));
    std::string decoded;
    if (base::Base64Decode(payload, &decoded)) {
      // 粗抽 "add":"x","port":n(不引完整 JSON 解析依赖,够用即可)。
      auto add_pos = decoded.find("\"add\":\"");
      if (add_pos != std::string::npos) {
        auto s = add_pos + 7, e = decoded.find('"', s);
        node->address = decoded.substr(s, e - s);
      }
      auto port_pos = decoded.find("\"port\":");
      if (port_pos != std::string::npos) {
        auto s = port_pos + 7;
        node->port = 0;
        base::StringToInt(decoded.substr(s, decoded.find_first_not_of("0123456789", s) - s), &node->port);
      }
      if (node->tag.empty()) {
        auto ps_pos = decoded.find("\"ps\":\"");
        if (ps_pos != std::string::npos) {
          auto s = ps_pos + 6, e = decoded.find('"', s);
          node->tag = decoded.substr(s, e - s);
        }
      }
    }
  } else {
    // ss/vless/trojan/hysteria2/tuic:userinfo@host:port
    std::string rest = line.substr(scheme_end + 3);
    if (hash != std::string::npos) rest = rest.substr(0, rest.size() - (line.size() - hash));
    auto at = rest.rfind('@');
    std::string hostport = (at != std::string::npos) ? rest.substr(at + 1) : rest;
    // 去掉 ?params
    auto q = hostport.find('?');
    if (q != std::string::npos) hostport = hostport.substr(0, q);
    auto colon = hostport.rfind(':');
    if (colon != std::string::npos) {
      node->address = hostport.substr(0, colon);
      node->port = 0;
      base::StringToInt(hostport.substr(colon + 1), &node->port);
    }
  }
  if (node->address.empty()) return nullptr;
  node->id = node->protocol + "-" + node->address + ":" + base::NumberToString(node->port);
  return node;
}

}  // namespace

std::vector<prisir::proxy::mojom::ProxyNodePtr> ParseSubscriptionBody(
    const std::string& body) {
  std::vector<prisir::proxy::mojom::ProxyNodePtr> out;
  if (body.empty()) return out;
  // 订阅体可能整段 base64;先试解,失败按明文逐行处理。
  std::string decoded;
  std::string text = body;
  base::TrimWhitespaceASCII(text, base::TRIM_ALL, &text);
  if (base::Base64Decode(text, &decoded) && decoded.find("://") != std::string::npos) {
    text = decoded;
  }
  for (const auto& line : base::SplitString(
           text, "\r\n", base::TRIM_WHITESPACE, base::SPLIT_WANT_NONEMPTY)) {
    auto node = ParseShareLink(line);
    if (node) out.push_back(std::move(node));
  }
  return out;
}

std::string BuildSingboxConfigJson(
    const prisir::proxy::mojom::ProxyNodeConfig& cfg,
    int local_port) {
  if (cfg.protocol.empty() || cfg.address.empty() || cfg.port <= 0) return std::string();

  // inbound:本地 SOCKS(只绑 127.0.0.1,不对外)。
  base::Value::Dict inbound;
  inbound.Set("type", "socks");
  inbound.Set("tag", "in");
  inbound.Set("listen", "127.0.0.1");
  inbound.Set("listen_port", local_port);

  // outbound:按协议。敏感字段(uuid/password)从 cfg 进 JSON,不回 Mojo。
  base::Value::Dict outbound;
  outbound.Set("tag", "out");
  outbound.Set("server", cfg.address);
  outbound.Set("server_port", cfg.port);
  const std::string& p = cfg.protocol;
  if (p == "ss2022" || p == "ss") {
    outbound.Set("type", "shadowsocks");
    outbound.Set("method", "2022-blake3-aes-128-gcm");
    outbound.Set("password", cfg.password);
  } else if (p == "vmess") {
    outbound.Set("type", "vmess");
    outbound.Set("uuid", cfg.uuid);
    outbound.Set("security", "auto");
  } else if (p == "vless") {
    outbound.Set("type", "vless");
    outbound.Set("uuid", cfg.uuid);
    // flow/tls 等从 extra_json 并(此处骨架,真编译按 extra 填)。
  } else if (p == "trojan") {
    outbound.Set("type", "trojan");
    outbound.Set("password", cfg.password);
  } else if (p == "hysteria2") {
    outbound.Set("type", "hysteria2");
    outbound.Set("password", cfg.password);
  } else if (p == "tuic") {
    outbound.Set("type", "tuic");
    outbound.Set("uuid", cfg.uuid);
    outbound.Set("password", cfg.password);
  } else if (p == "wireguard") {
    outbound.Set("type", "wireguard");
    // wireguard 字段较多,骨架从 extra_json 并。
  } else {
    return std::string();  // 不支持
  }

  base::Value::Dict root;
  base::Value::List inbounds; inbounds.Append(std::move(inbound));
  base::Value::List outbounds; outbounds.Append(std::move(outbound));
  root.Set("inbounds", std::move(inbounds));
  root.Set("outbounds", std::move(outbounds));
  base::Value::Dict route; route.Set("final", "out");
  root.Set("route", std::move(route));

  std::string json;
  if (!base::JSONWriter::WriteWithOptions(
          root, base::JSONWriter::OPTIONS_PRETTY_PRINT, &json)) {
    return std::string();
  }
  return json;
}

}  // namespace prisir::proxy
