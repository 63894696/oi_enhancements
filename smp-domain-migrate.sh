#!/bin/bash
# smp-domain-migrate.sh — smp-server 域名化:192.220.14.165 → smp.babelspan.com
#
# 前置(用户已在 Cloudflare 完成):smp.babelspan.com A 记录 → 192.220.14.165,灰云(DNS only)。
# 本脚本在 VPS 上执行(root)。幂等:重复跑安全。改前备份,改后验证。
#
# 关键安全点:
#   - fingerprint 来自自签 CA(ca.crt/ca.key),本脚本【不换 CA】→ fingerprint 不变 →
#     已建立的老连接身份不受影响(但旧队列地址仍是 IP,需 "Change receiving address" 或重建)。
#   - 只重签 server 证书(CN=域名)+ 改 ini host + 重启。
set -euo pipefail

DOMAIN="smp.babelspan.com"
IP="192.220.14.165"
CONF_DIR="/etc/opt/simplex"
INI="$CONF_DIR/smp-server.ini"
TS="$(date +%Y%m%d-%H%M%S)"

echo "== [0] 前置校验:DNS 是否已灰云生效 =="
RESOLVED="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
if [[ "$RESOLVED" != "$IP" ]]; then
  echo "FAIL: $DOMAIN 未解析到 $IP(当前: ${RESOLVED:-<none>})"
  echo "  → 先在 Cloudflare 加 A 记录 $DOMAIN → $IP,Proxy=DNS only(灰云),生效后再跑本脚本。"
  exit 1
fi
echo "  OK: $DOMAIN → $RESOLVED"

echo "== [1] 备份现有 ini + 证书 =="
cp -a "$INI" "$INI.bak-domain-$TS"
[[ -f "$CONF_DIR/server.crt" ]] && cp -a "$CONF_DIR/server.crt" "$CONF_DIR/server.crt.bak-$TS"
echo "  备份: $INI.bak-domain-$TS"

echo "== [2] 记录旧 fingerprint(必须不变) =="
OLD_FP="$(cat "$CONF_DIR/fingerprint")"
echo "  旧 fingerprint: $OLD_FP"

echo "== [3] 用同一 CA 重签 server 证书(CN=$DOMAIN) =="
# 复用 smp-server 自带的 openssl 配置,只把 CN 换成域名。CA 不动 → fingerprint 不变。
cd "$CONF_DIR"
# 生成新 CSR(CN=域名)
openssl req -new -key server.key -out "server.csr.domain" -subj "/CN=$DOMAIN"
# 用 CA 签 server 证书(沿用 openssl_ca.conf 的扩展,若存在)
if [[ -f openssl_server.conf ]]; then
  openssl x509 -req -in "server.csr.domain" -CA ca.crt -CAkey ca.key \
    -CAcreateserial -out "server.crt.new" -days 3650 -extensions v3_server \
    -extfile openssl_server.conf 2>/dev/null || \
  openssl x509 -req -in "server.csr.domain" -CA ca.crt -CAkey ca.key \
    -CAcreateserial -out "server.crt.new" -days 3650
else
  openssl x509 -req -in "server.csr.domain" -CA ca.crt -CAkey ca.key \
    -CAcreateserial -out "server.crt.new" -days 3650
fi
mv "server.crt.new" server.crt
rm -f "server.csr.domain"
echo "  新 server.crt CN: $(openssl x509 -in server.crt -noout -subject)"

echo "== [4] 改 ini [TRANSPORT] host = $DOMAIN =="
sed -i -E "s|^host\s*=.*|host = $DOMAIN|" "$INI"
grep -E "^host" "$INI"

echo "== [5] 重启 smp-server =="
systemctl restart smp-server
sleep 2
systemctl is-active --quiet smp-server && echo "  smp-server active" || { echo "  FAIL: smp-server 未起"; journalctl -u smp-server -n 20 --no-pager; exit 1; }

echo "== [6] 验证 fingerprint 不变 =="
NEW_FP="$(cat "$CONF_DIR/fingerprint")"
if [[ "$NEW_FP" != "$OLD_FP" ]]; then
  echo "FAIL: fingerprint 变了! $OLD_FP → $NEW_FP (CA 被动过,需回滚)"
  exit 1
fi
echo "  OK fingerprint 不变: $NEW_FP"

echo "== [7] 验证 TLS 证书 CN + 监听 =="
echo | openssl s_client -connect "$DOMAIN:5223" -servername "$DOMAIN" 2>/dev/null | openssl x509 -noout -subject -fingerprint 2>/dev/null || echo "  (openssl s_client 验证可选)"
ss -tlnp | grep 5223 && echo "  OK 5223 listening"

echo
echo "== 完成 =="
echo "服务器地址(客户端配置用,新邀请链接将印此域名):"
echo "  smp://$NEW_FP@$DOMAIN:5223"
echo "旧连接(IP 队列)需重建或在客户端 'Change receiving address'。"
