"""F-7:启动预热 — DNS 解析 + TLS 1.3 握手,不发送任何用户数据

跑法:python -m fastlane.prewarm
daemon 集成:后台线程调 prewarm_all(),结果缓存,首次按热键时无握手延迟。
"""
from __future__ import annotations

import asyncio
import socket
import ssl
import time
from typing import Any, Dict, List
from urllib.parse import urlparse

from .adapters import make_ssl_context
from .providers import provider_status
from .providers.factory import _asr_candidates, _llm_candidates, _tts_candidates
from .providers.base import ProviderNotConfigured


def _collect_endpoints() -> List[str]:
    """所有已配置 provider 的 https endpoint(去重,本地 http 跳过)"""
    urls = set()
    for candidates in (_asr_candidates(), _llm_candidates(), _tts_candidates()):
        for _name, builder in candidates:
            try:
                adapter = builder()
            except ProviderNotConfigured:
                continue
            ep = getattr(adapter, "endpoint", "")
            if ep.startswith("https://"):
                urls.add(ep)
    return sorted(urls)


async def _prewarm_one(url: str) -> Dict[str, Any]:
    host = urlparse(url).hostname or ""
    port = urlparse(url).port or 443
    t0 = time.perf_counter()
    result: Dict[str, Any] = {"endpoint": url, "host": host}
    try:
        # DNS
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        result["dns_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        ip = infos[0][4][0]
        # TCP + TLS1.3 握手(即连即断,零应用数据)
        t1 = time.perf_counter()
        ctx = make_ssl_context()
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port, ssl=ctx, server_hostname=host), timeout=10
        )
        tls_obj = writer.get_extra_info("ssl_object")
        result["tls_version"] = tls_obj.version() if tls_obj else "?"
        result["handshake_ms"] = round((time.perf_counter() - t1) * 1000, 1)
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, ssl.SSLError, OSError):
            pass
        result["status"] = "ok"
    except Exception as e:
        result["status"] = "fail"
        result["error"] = f"{type(e).__name__}: {e}"
    return result


async def prewarm_all() -> List[Dict[str, Any]]:
    urls = _collect_endpoints()
    if not urls:
        return []
    return list(await asyncio.gather(*[_prewarm_one(u) for u in urls]))


def main() -> None:
    print("=" * 60)
    print("FastLane 预热(F-7:DNS + TLS1.3 握手,不发送用户数据)")
    print("=" * 60)
    status = provider_status()
    for kind, info in status.items():
        print(f"  {kind.upper()} 链:{' → '.join(info['chain']) or '(空)'}")
        for name, why in info["skipped"].items():
            print(f"    - 跳过 {name}:{why}")
    print()
    results = asyncio.run(prewarm_all())
    if not results:
        print("  没有可预热的云端 endpoint(全本地模式)")
        return
    for r in results:
        if r["status"] == "ok":
            print(f"  ✓ {r['host']:42s} dns={r['dns_ms']}ms tls={r['handshake_ms']}ms ({r['tls_version']})")
        else:
            print(f"  ✗ {r['host']:42s} {r['error']}")


if __name__ == "__main__":
    main()
