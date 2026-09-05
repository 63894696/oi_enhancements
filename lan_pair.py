"""lan_pair — 安卓×Win 联动的局域网配对/令牌/mDNS 模块(P1,2026-08-25)。

定位:prisiragent_web 的局域网远程指挥支撑。设计文档 docs/prisir-android-win-link-2026-08-25.md。

红线落地:
- 不要账号:配对用一次性二维码令牌换持久令牌,无用户 ID。
- 私钥不出原生层/令牌不上云:持久令牌只存本地 profile(lan_token.txt),永不上云。
- 默认本地:模块惰性启用——只有 --lan 时才监听局域网;默认 127.0.0.1 时本机访问不带令牌。

职责:
1. 持久配对令牌:load/create 存 profile;verify 供鉴权中间件调用。
2. 一次性配对 offer:pair/offer 生成一次性令牌(5 分钟有效),pair/confirm 用它换持久令牌。
3. 局域网判定:is_local_client 按来源 IP 分本机(可信)与远程(要令牌)。
4. mDNS/UDP 发现广播:后台线程周期广播 _prisirai 服务(最佳努力,失败不阻塞)。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
from pathlib import Path

_LOG = logging.getLogger("prisirai.lan_pair")

# 持久令牌落点:与对话数据同目录(~/.local/share/prisir/),只存本地、不上云。
_TOKEN_FILE = "lan_token.txt"
# 一次性配对令牌有效期(秒)。
_OFFER_TTL = 300
# mDNS 组播地址/端口。
_MDNS_ADDR = ("224.0.0.251", 5353)
_BROADCAST_INTERVAL = 5.0


class LanPair:
    """配对令牌 + 发现广播。全程线程安全;所有 IO 失败静默(不阻塞对话主功能)。"""

    def __init__(self, data_dir: str, port: int):
        self._dir = Path(data_dir)
        self._port = port
        self._lock = threading.Lock()
        self._token: str | None = None          # 持久令牌(配对后两端持有)
        self._offer: str | None = None          # 一次性配对令牌
        self._offer_exp: float = 0.0
        self._bc_stop = threading.Event()
        self._bc_thread: threading.Thread | None = None

    # ---------- 持久令牌 ----------
    def _token_path(self) -> Path:
        return self._dir / _TOKEN_FILE

    def load_or_create_token(self) -> str:
        """读本地持久令牌;没有则生成并落盘。返回令牌(只供本机 pair/offer 展示)。"""
        with self._lock:
            if self._token:
                return self._token
            try:
                p = self._token_path()
                if p.exists():
                    t = p.read_text(encoding="utf-8").strip()
                    if t:
                        self._token = t
                        return t
            except Exception as e:  # noqa: BLE001
                _LOG.warning("read lan token failed: %s", e)
            # 生成新令牌(48 字节熵,urlsafe)
            t = secrets.token_urlsafe(36)
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                self._token_path().write_text(t, encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                _LOG.warning("write lan token failed: %s", e)
            self._token = t
            return t

    def verify_token(self, tok: str | None) -> bool:
        """鉴权中间件调用:校验请求带的持久令牌。常时间比较防时序侧信道。"""
        if not tok:
            return False
        with self._lock:
            cur = self._token
        if not cur:
            return False
        return secrets.compare_digest(tok.strip(), cur)

    # ---------- 一次性配对 offer ----------
    # 短配对码字符集:大写字母(去易混 O/I/L)+ 数字(去 0/1),共 31 个,人读人输都友好。
    _OFFER_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

    def new_offer(self) -> dict:
        """生成一次性配对令牌(本机调用),6 位短码(字母+数字,不区分大小写)。5 分钟有效。"""
        with self._lock:
            self._offer = "".join(secrets.choice(self._OFFER_ALPHABET) for _ in range(6))
            self._offer_exp = time.time() + _OFFER_TTL
            return {
                "offer": self._offer,
                "port": self._port,
                "expires_in": _OFFER_TTL,
                # 手机端扫码后回 confirm 换持久令牌。
            }

    def confirm_offer(self, offer: str | None) -> str | None:
        """手机回扫:一次性令牌换持久令牌。成功返回持久令牌,失败 None。一次性用后即焚。
        不区分大小写:统一转大写比较,用户输小写也行。"""
        if not offer:
            return None
        with self._lock:
            if not self._offer or time.time() > self._offer_exp:
                return None
            if not secrets.compare_digest(offer.strip().upper(), self._offer):
                return None
            # 用后即焚
            self._offer = None
            self._offer_exp = 0.0
        # 确保持久令牌已生成
        return self.load_or_create_token()

    # ---------- 局域网判定 ----------
    @staticmethod
    def is_local_client(ip: str) -> bool:
        """本机回环(127.0.0.1 / ::1)= 可信,不带令牌;其余一律视为远程。"""
        try:
            return ipaddress.ip_address(ip).is_loopback
        except ValueError:
            return False

    @staticmethod
    def is_lan_client(ip: str) -> bool:
        """回环 ∪ 私网(10/8、172.16/12、192.168/16、链路本地等)= 局域网内,可信任到「生成配对码」粒度。
        配对码本就出示在 PC 屏幕上由人抄进手机,私网内 fetch 它不放大风险;仅拦公网来源。
        MuMu 模拟器经 NAT alias(10.0.2.x)访问,PC 看到的是 VM 网关私网 IP,属此类。"""
        try:
            a = ipaddress.ip_address(ip)
            return a.is_loopback or a.is_private or a.is_link_local
        except ValueError:
            return False

    # ---------- mDNS / UDP 发现广播 ----------
    def start_broadcast(self, tls_fingerprint: str = "") -> None:
        """起后台线程周期广播服务存在。最佳努力:网络受限时静默,不阻塞启动。"""
        if self._bc_thread and self._bc_thread.is_alive():
            return
        self._bc_stop.clear()
        self._bc_thread = threading.Thread(
            target=self._broadcast_loop, args=(tls_fingerprint,), daemon=True)
        self._bc_thread.start()
        _LOG.info("lan broadcast started port=%d", self._port)

    def stop_broadcast(self) -> None:
        self._bc_stop.set()

    def _broadcast_loop(self, tls_fingerprint: str) -> None:
        # 简易 UDP 组播通告(JSON payload)。手机端监听 224.0.0.251:5353 或子网广播。
        payload = json.dumps({
            "service": "_prisirai._tcp.local",
            "name": "PrisirAI",
            "port": self._port,
            "fp": tls_fingerprint,
            "v": 1,
        }).encode("utf-8")
        while not self._bc_stop.is_set():
            for addr in (_MDNS_ADDR, ("255.255.255.255", 5353)):
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
                    s.sendto(payload, addr)
                    s.close()
                except Exception as e:  # noqa: BLE001
                    _LOG.debug("broadcast to %s failed: %s", addr, e)
            self._bc_stop.wait(_BROADCAST_INTERVAL)


# 进程内单例(由 prisiragent_web 在 --lan 时初始化)。
_INSTANCE: LanPair | None = None


def init(data_dir: str, port: int) -> LanPair:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = LanPair(data_dir, port)
        _INSTANCE.load_or_create_token()
    return _INSTANCE


def instance() -> LanPair | None:
    return _INSTANCE
