#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# data_prep_log.py — M3.49 日志分类训练集准备(2026-09-23)
#
# 目的:
#   - 5 类风险等级 + 4 action(alert/review/keep/drop)
#   - 输入:source(nginx/db/kernel/python/systemd/docker)+ level + content
#   - 复用 email/disk_cleanup 的规则架构,但语义切到"运维日志"领域
#
# 与前面 M3.45-3.48 差别:
#   - 数据源:全部合成(规则生成器),本机没现成日志数据集
#   - 训练集可控:每类样本数可控;noise 也用 cross-source mixing
#   - 跟 email 同样的 tier→action 映射,复用 ACTION_MAP 思维
#
# 5 类分级规则:
#   critical — OOM/segfault/panic/database connection failed/restart loop
#   high     — error/fatal/SQL exception/timeout 5xx/permission denied
#   medium   — warn/deprecated/retry/throttling/slow query (>1s)
#   low      — info/notice/status update/listening on port
#   safe     — debug/trace/heartbeat/health check OK / successful auth
#
# 4 action 映射:
#   critical → alert   立即告警
#   high     → review  7 天内人工排查
#   medium   → review  7 天内看(可批量)
#   low      → keep    留档备查
#   safe     → drop    节省存储
#
# 用法:
#   python data_prep_log.py --output data_log.jsonl [--limit 500] [--mix-count 50]
from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path
from string import Template

# ------------------------------------------------------------
# 5 类规则(从 level 字段 + content 关键词联合判定)
# ------------------------------------------------------------

CRITICAL_KEYWORDS = [
    "OutOfMemory", "OOM", "segfault", "panic", "kernel panic",
    "database connection failed", "Connection refused",
    "DatabaseError", "FATAL: terminating", "out of memory",
    "StackOverflow", "NullPointerException", "panic: runtime error",
    "RestarterThreshold exceeded", "restart loop", "no space left",
    "Read-only file system", "device not ready", "I/O error",
    "ConnectionResetError", "BrokenPipeError", "Max retries exceeded",
]

HIGH_KEYWORDS = [
    "ERROR", "Error", "FATAL", "FATAL EXCEPTION", "exception",
    "traceback", "Traceback", "Permission denied", "timeout",
    "timed out", "504 Gateway Timeout", "502 Bad Gateway",
    "500 Internal Server Error", "SQLSTATE", "SQL exception",
    "deadlock", "Deadlock", "Segmentation fault",
    "syntax error", "SyntaxError", "ImportError",
    "ModuleNotFoundError", "AttributeError", "KeyError",
    "TypeError", "ValueError", "RuntimeError",
    "exit code 1", "exit code 2", "non-zero exit",
]

MEDIUM_KEYWORDS = [
    "WARN", "warning", "Warning", "deprecated", "Deprecated",
    "DeprecationWarning", "retry", "Retry", "throttling",
    "Throttling", "slow query", "Slow query", "lagging",
    "reconnect", "Reconnect", "backoff", "Backoff",
    "401 Unauthorized", "403 Forbidden", "rate limit",
    "Slow response", "response time >", "high latency",
    "approaching limit", "queue depth", "fallback to",
]

LOW_KEYWORDS = [
    "INFO", "Info", "info", "notice", "Notice",
    "listening on", "started", "Started", "loaded",
    "Loaded", "initialized", "Initialized", "registered",
    "Registered", "connected to", "Connected to",
    "configuration loaded", "config loaded",
    "Listening on port", "server started",
    "service started", "started successfully",
]

SAFE_KEYWORDS = [
    "DEBUG", "debug", "TRACE", "trace",
    "heartbeat", "Heartbeat", "health check OK",
    "successful auth", "Auth success", "logged in",
    "session created", "session destroyed",
    "ping", "pong", "keep-alive", "Keep-alive",
    "metrics flush", "GC stats", "garbage collection",
    "cache hit", "cache miss", "request completed in 0.",
]

ACTION_MAP: dict[str, str] = {
    "critical": "alert",
    "high": "review",
    "medium": "review",
    "low": "keep",
    "safe": "drop",
}


# ------------------------------------------------------------
# 数据源模板(每源有不同的日志格式)
# ------------------------------------------------------------

SOURCES = ["nginx", "postgresql", "kernel", "python", "systemd", "docker"]

# 各源的 5 类模板(每类 5-10 个变体)
LOG_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "nginx": {
        "critical": [
            "2026/09/23 14:23:01 [crit] 1234#0: *5678 connect() failed (111: Connection refused) while connecting to upstream",
            "2026/09/23 14:23:15 [crit] 1234#0: *5679 SSL_do_handshake() failed (SSL: error:0A000418)",
            "2026/09/23 14:24:02 [emerg] 1234#0: bind() to 0.0.0.0:80 failed (98: Address already in use)",
            "2026/09/23 14:25:00 [alert] 1234#0: 4567 socket() failed (24: Too many open files)",
            "2026/09/23 14:26:00 [crit] worker process exited on signal 9 (SIGKILL)",
        ],
        "high": [
            "2026/09/23 14:23:01 [error] 1234#0: *5678 upstream timed out (110: Connection timed out)",
            "2026/09/23 14:23:15 [error] 1234#0: *5679 open() '/var/log/nginx/foo.log' failed (2: No such file or directory)",
            "2026/09/23 14:24:02 [error] 1234#0: *5680 directory index of '/srv/www/' is forbidden",
            "2026/09/23 14:25:00 [error] 1234#0: *5681 limiting requests, excess: 10.000",
            "2026/09/23 14:26:00 [error] 1234#0: *5682 rewrite or internal redirection cycle",
            "2026/09/23 14:27:00 [error] 1234#0: invalid URL prefix in '/api/'",
        ],
        "medium": [
            "2026/09/23 14:23:01 [warn] 1234#0: *5678 client sent invalid header line",
            "2026/09/23 14:23:15 [warn] conflicting server name 'example.com' on 0.0.0.0:80",
            "2026/09/23 14:24:02 [warn] 1234#0: *5680 client closed connection while waiting for request",
            "2026/09/23 14:25:00 [warn] 1234#0: *5681 upstream server temporarily disabled",
            "2026/09/23 14:26:00 [notice] 1234#0: signal process started",
            "2026/09/23 14:27:00 [warn] 401 response: '/api/v1/users' from 192.168.1.1",
        ],
        "low": [
            "2026/09/23 14:23:01 [notice] 1234#0: using the 'epoll' event method",
            "2026/09/23 14:23:15 [notice] nginx/1.27.0 built by gcc 11.4.0",
            "2026/09/23 14:24:02 [notice] 1234#0: nginx worker process 1235 started",
            "2026/09/23 14:25:00 [info] 1234#0: *5680 client 192.168.1.1 connected",
            "2026/09/23 14:26:00 [info] 1234#0: *5681 client closed keepalive connection",
            "2026/09/23 14:27:00 [info] server started successfully on 0.0.0.0:80",
        ],
        "safe": [
            "2026/09/23 14:23:01 [debug] 1234#0: *5678 http header: 'User-Agent: Mozilla/5.0...'",
            "2026/09/23 14:23:15 [debug] 1234#0: *5679 request line: 'GET /api/v1/health HTTP/1.1'",
            "2026/09/23 14:24:02 [debug] 1234#0: *5680 upstream resolve: '127.0.0.1:8080'",
            "2026/09/23 14:25:00 [debug] connection keepalive enabled",
            "2026/09/23 14:26:00 [debug] metrics flush to /var/log/nginx/metrics: 12KB",
        ],
    },
    "postgresql": {
        "critical": [
            "FATAL:  out of memory\nDETAIL:  Failed on request of size 268435456.",
            "FATAL:  terminating connection due to administrator command",
            "FATAL:  database 'production' does not exist",
            "PANIC:  could not open file 'pg_xact/0000': No such file or directory",
            "FATAL:  password authentication failed for user 'postgres'",
            "FATAL:  could not write to file 'pg_wal/...': No space left on device",
        ],
        "high": [
            "ERROR:  relation 'users' does not exist at character 23",
            "ERROR:  duplicate key value violates unique constraint 'users_pkey'",
            "ERROR:  permission denied for table orders",
            "ERROR:  syntax error at or near 'FROMM'",
            "ERROR:  deadlock detected at character 45",
            "ERROR:  canceling statement due to statement timeout",
            "ERROR:  column 'foo' does not exist",
            "ERROR:  function bar() does not exist",
        ],
        "medium": [
            "WARNING:  there is already a transaction in progress",
            "WARNING:  usage of deprecated feature: NOW()",
            "WARNING:  checkpoints are occurring too frequently (9 seconds apart)",
            "WARNING:  autovacuum launcher started",
            "WARNING:  pg_hba.conf reload requested but not all changes applied",
            "LOG:  checkpoints are occurring too frequently (8 seconds apart)",
        ],
        "low": [
            "LOG:  database system is ready to accept connections",
            "LOG:  autovacuum launcher started",
            "LOG:  database system was shut down at 2026-09-23 14:20:00 UTC",
            "LOG:  MultiXact member wraparound protections are now enabled",
            "LOG:  starting PostgreSQL 15.2",
            "NOTICE:  schema 'public' already exists",
        ],
        "safe": [
            "DEBUG:  autovacuum: processing database 'postgres'",
            "DEBUG:  forked new client backend (pid 12345)",
            "DEBUG:  parsed plan: Seq Scan on users",
            "DEBUG:  checkpoint complete: wrote 24 buffers (0.1%)",
            "LOG:  checkpoint complete: wrote 24 buffers (0.1%); 0 transaction log file added",
        ],
    },
    "kernel": {
        "critical": [
            "kernel: [12345.678] Out of memory: Killed process 1234 (python) total-vm:4294967296kB",
            "kernel: [12345.678] BUG: unable to handle kernel NULL pointer dereference at 0000000000000010",
            "kernel: [12345.678] Kernel panic - not syncing: VFS: Unable to mount root fs",
            "kernel: [12345.678] watchdog: BUG: soft lockup - CPU#3 stuck for 22s",
            "kernel: [12345.678] EXT4-fs error (device sda3): ext4_lookup:",
            "kernel: [12345.678] blk_update_request: I/O error, dev sda, sector 12345",
        ],
        "high": [
            "kernel: [12345.678] usb 1-1: USB disconnect, device number 5",
            "kernel: [12345.678] segfault at 0 ip 0x7f12345678 sp 0x7fff123456",
            "kernel: [12345.678] thermal thermal_zone0: critical temperature reached",
            "kernel: [12345.678] ACPI Error: No handler for Region [ECRM]",
            "kernel: [12345.678] cache: parent cpuset cgroup is empty",
            "kernel: [12345.678] rcu: INFO: rcu_preempt detected stalls on CPUs/tasks",
        ],
        "medium": [
            "kernel: [12345.678] CPU1: Package temperature above threshold",
            "kernel: [12345.678] nvme nvme0: I/O 2345 QID 5 timeout, completion polled",
            "kernel: [12345.678] wlan0: deauthenticated from aa:bb:cc:dd:ee:ff (Reason: 4)",
            "kernel: [12345.678] usb: reset of cred data at 0x123456",
            "kernel: [12345.678] eth0: link down",
            "kernel: [12345.678] ath: Failed to stop TX queue: ([00ff0000])",
        ],
        "low": [
            "kernel: [12345.678] eth0: link up, 1000Mbps, full-duplex",
            "kernel: [12345.678] usb 1-1: new high-speed USB device number 6",
            "kernel: [12345.678] input: AT Translated Set 2 keyboard as /devices/.../input5",
            "kernel: [12345.678] audit: type=1400 audit(1695456789.123:45): apparmor='DENIED'",
            "kernel: [12345.678] ACPI: Wela: Power button pressed",
        ],
        "safe": [
            "kernel: [12345.678] TCP: out of memory -- not on 0.0.0.0:80",
            "kernel: [12345.678] ext4: orphan cleanup on readonly fs",
            "kernel: [12345.678] IPv6: ADDRCONF(NETDEV_UP): eth0: link is not ready",
            "kernel: [12345.678] audit: backlog wait time exceeded",
            "kernel: [12345.678] cache: parent cpuset cgroup is empty",
        ],
    },
    "python": {
        "critical": [
            "Traceback (most recent call last):\n  File '/app/main.py', line 42, in <module>\n    main()\nMemoryError: out of memory",
            "Traceback (most recent call last):\n  File '/app/db.py', line 15, in connect\n    self.conn = psycopg2.connect(**kwargs\npsycopg2.OperationalError: could not connect to server: Connection refused",
            "Traceback (most recent call last):\n  File '/app/worker.py', line 23, in process\nRuntimeError: maximum recursion depth exceeded",
            "Traceback (most recent call last):\n  File '/app/api.py', line 88, in handle\nSystemExit: 1",
            "Traceback (most recent call last):\n  File '/app/io.py', line 12\nOSError: [Errno 28] No space left on device",
        ],
        "high": [
            "Traceback (most recent call last):\n  File '/app/api.py', line 88, in handle\nTypeError: 'NoneType' object is not iterable",
            "Traceback (most recent call last):\n  File '/app/auth.py', line 45\nKeyError: 'user_id'",
            "Traceback (most recent call last):\n  File '/app/utils.py', line 30\nValueError: invalid literal for int() with base 10: 'foo'",
            "Traceback (most recent call last):\n  File '/app/routes.py', line 56\nAttributeError: 'User' object has no attribute 'email'",
            "Traceback (most recent call last):\n  File '/app/db.py', line 78\nsqlalchemy.exc.OperationalError: (psycopg2.OperationalError) could not connect",
        ],
        "medium": [
            "WARNING: ResourceWarning: unclosed file <_io.BufferedReader name='/tmp/foo'>",
            "DeprecationWarning: 'fontname' is deprecated, use 'fontname'",
            "UserWarning: pandas quantile interpolation deprecated",
            "warnings.warn('use of deprecated constant', DeprecationWarning)",
            "FutureWarning: The 'method' keyword is deprecated, use 'method' instead",
            "RuntimeWarning: divide by zero encountered in log",
        ],
        "low": [
            "INFO: Loaded config from /app/config.yaml",
            "INFO: Connected to database: postgres://prod@db:5432",
            "INFO: Server listening on port 8080",
            "INFO: Worker 1 started (bootId=1234)",
            "INFO: Application startup complete",
            "logging.info('User logged in: user_id=42')",
        ],
        "safe": [
            "DEBUG: SQL query: SELECT * FROM users WHERE id=42",
            "DEBUG: cache hit for key 'user:42'",
            "DEBUG: HTTP request: GET /api/v1/health -> 200 in 5ms",
            "DEBUG: gc: gen 0 collected 1234 objects",
            "DEBUG: heartbeat sent (ping_id=1234)",
            "TRACE: __init__ called with args=(1, 2, 3)",
        ],
    },
    "systemd": {
        "critical": [
            "systemd[1]: nginx.service: Main process exited, code=exited, status=1/FAILURE",
            "systemd[1]: postgresql.service: Failed with result 'exit-code'",
            "systemd[1]: docker.service: Scheduled restart job, restart counter is at 5",
            "systemd[1]: NetworkManager.service: Start request repeated too quickly",
            "systemd[1]: sshd.service: Control process exited, code=exited status=255",
        ],
        "high": [
            "systemd[1]: redis-server.service: Failed with result 'exit-code'",
            "systemd[1]: nginx.service: A process of this unit has been killed by the OOM killer",
            "systemd[1]: postgresql.service: Main process exited, code=killed, status=9/KILL",
            "systemd[1]: Failed to start PostgreSQL database server.",
            "systemd[1]: Dependency failed for Multi-User System.",
        ],
        "medium": [
            "systemd[1]: nginx.service: Scheduled restart job, restart counter is at 3",
            "systemd[1]: postgresql.service: SLOW operation, took 12s",
            "systemd[1]: Reloading PostgreSQL database server.",
            "systemd[1]: Stopping target Multi-User System.",
            "systemd[1]: Starting Daily apt download activities...",
        ],
        "low": [
            "systemd[1]: Started Session 42 of user admin",
            "systemd[1]: Reached target Multi-User System.",
            "systemd[1]: Starting Docker Application Container Engine...",
            "systemd[1]: Listening on /run/docker.sock.",
            "systemd[1]: nginx.service: Scheduled restart job, restart counter is at 0",
        ],
        "safe": [
            "systemd[1]: Session 42 logged out. Waiting for processes to finish.",
            "systemd[1]: Removed slice User Slice of UID 1000.",
            "systemd[1]: Deactivated successfully.",
            "systemd[1]: apt-cleanup.timer: Succeeded.",
            "systemd[1]: logrotate.timer: Succeeded.",
        ],
    },
    "docker": {
        "critical": [
            'dockerd: level=fatal msg="Error response from daemon: layer does not exist"',
            'dockerd: level=fatal msg="Error starting daemon: layer does not exist"',
            "dockerd: Error response from daemon: No such image: postgres:latest",
            'dockerd: level=error msg="failed to start container" error="image not known"',
            'dockerd: level=error msg="containerd: failed to start container" exit_code=137',
        ],
        "high": [
            'dockerd: level=error msg="Handler for POST /v1.43/containers/create returned error: No such image"',
            'dockerd: level=error msg="containerd: failed to start container" error="command not found"',
            'dockerd: level=error msg="failed to start healthcheck" error="exec failed"',
            "dockerd: Error response from daemon: Conflict. Container name is already in use",
            'dockerd: level=warning msg="Your kernel does not support cgroup memory" exit_code=1',
        ],
        "medium": [
            'dockerd: level=warn msg="Healthcheck for container foo failed"',
            'dockerd: level=warn msg="Back-off restarting failed container foo"',
            'dockerd: level=warn msg="failed to retrieve network" retry=3',
            'dockerd: level=warn msg="image with no data had my tag"',
            'dockerd: level=warn msg="throttling build" backoff=5s',
        ],
        "low": [
            'dockerd: level=info msg="Container foo started" containerID=abc123',
            'dockerd: level=info msg="Image postgres:15 pulled successfully"',
            'dockerd: level=info msg="Daemon has completed initialization"',
            'dockerd: level=info msg="API listen on /var/run/docker.sock"',
            'dockerd: level=info msg="Loading containers: done."',
        ],
        "safe": [
            'dockerd: level=debug msg="HTTP request" method=GET uri=/v1.43/containers/json',
            'dockerd: level=debug msg="event goroutine" status=ok',
            'dockerd: level=debug msg="Heartbeat received" id=42',
            'dockerd: level=trace msg="conn close: connection reset by peer"',
            'dockerd: level=debug msg="auth successful" user=admin',
        ],
    },
}


# ------------------------------------------------------------
# 分类函数(基于 content 关键词强匹配)
# ------------------------------------------------------------

def _tier_of(content: str, level: str = "") -> str:
    """返 (risk_label, action) — 跟 email 规则器同模式。"""
    # 1. critical(OOM/panic/segfault)
    for kw in CRITICAL_KEYWORDS:
        if kw in content:
            return "critical", ACTION_MAP["critical"]

    # 2. high(error/fatal/exception)
    for kw in HIGH_KEYWORDS:
        if kw in content:
            return "high", ACTION_MAP["high"]

    # 3. medium(warn/deprecated/retry)
    for kw in MEDIUM_KEYWORDS:
        if kw in content:
            return "medium", ACTION_MAP["medium"]

    # 4. low(info/notice)
    for kw in LOW_KEYWORDS:
        if kw in content:
            return "low", ACTION_MAP["low"]

    # 5. safe(debug/trace)
    for kw in SAFE_KEYWORDS:
        if kw in content:
            return "safe", ACTION_MAP["safe"]

    # 兜底
    return "low", ACTION_MAP["low"]


def _build_text(source: str, ts: str, level: str, content: str) -> str:
    """组装跟训练数据一致的 prompt 段。"""
    return (f"日志来源: {source}\n"
            f"时间戳: {ts}\n"
            f"级别: {level}\n"
            f"内容: {content}\n"
            f"问: 这条日志应该如何分类与处理?")


# ------------------------------------------------------------
# 多样性采样
# ------------------------------------------------------------

RISK_MIN_TARGETS = {
    "critical": 50,
    "high": 100,
    "medium": 150,
    "low": 120,
    "safe": 80,
}


def _balance_diversity(samples: list[dict], limit: int) -> list[dict]:
    rng = random.Random(42)
    by_risk: dict[str, list[dict]] = {}
    for s in samples:
        by_risk.setdefault(s["risk_label"], []).append(s)

    picked: list[dict] = []
    for risk, items in by_risk.items():
        target = RISK_MIN_TARGETS.get(risk, 50)
        if len(items) <= target:
            picked.extend(items)
            continue
        # 二次抽样:按 source 桶平衡
        sub_buckets: dict[str, list[dict]] = {}
        for it in items:
            sub_buckets.setdefault(it["_source"], []).append(it)
        total = sum(len(v) for v in sub_buckets.values())
        kept: list[dict] = []
        for sub, sub_items in sub_buckets.items():
            share = max(int(target * len(sub_items) / total), 3)
            rng.shuffle(sub_items)
            kept.extend(sub_items[:share])
        picked.extend(kept[:target])

    rng.shuffle(picked)
    return picked[:limit]


# ------------------------------------------------------------
# 主生成函数
# ------------------------------------------------------------
def generate(max_raw: int = 5000, seed: int = 42) -> list[dict]:
    """生成候选样本(每源 5 类 × N 个变体)。"""
    rng = random.Random(seed)
    samples: list[dict] = []

    # 生成时间戳池
    def _random_ts():
            hour = rng.randint(0, 23)
            minute = rng.randint(0, 59)
            second = rng.randint(0, 59)
            return f"2026/09/23 {hour:02d}:{minute:02d}:{second:02d}"

    # level 推断(从 content 中第一行的 [] 标记)
    LEVEL_PAT = re.compile(r"\[(crit|emerg|alert|error|warn|warning|notice|info|debug|trace)\]", re.IGNORECASE)
    KEYWORD_LEVEL = [
        ("FATAL|ERROR|Exception|panic", "error"),
        ("WARN|Warning", "warning"),
        ("INFO|Info", "info"),
        ("DEBUG|debug|TRACE|trace", "debug"),
    ]

    for source in SOURCES:
        for tier, templates in LOG_TEMPLATES[source].items():
            per_tier = max_raw // (len(SOURCES) * 5)
            for _ in range(per_tier):
                content = rng.choice(templates)
                ts = _random_ts()
                # 尝试从 content 抽 level
                m = LEVEL_PAT.search(content)
                if m:
                    level = m.group(1).lower()
                else:
                    # 用关键字 fallback
                    level = "info"
                    for kw_pat, lv in KEYWORD_LEVEL:
                        if re.search(kw_pat, content):
                            level = lv
                            break
                risk, action = _tier_of(content, level)
                samples.append({
                    "_source": source,
                    "_ts": ts,
                    "_level": level,
                    "_content": content,
                    "risk_label": risk,
                    "_action": action,
                })
                if len(samples) >= max_raw:
                    return samples
    return samples


def _cross_source_mix(count: int = 50, seed: int = 43) -> list[dict]:
    """M3.49 v2: cross-source 边界样本。

    level 来自 source A,content 来自 source B,让 _tier_of() 按 content 判 tier,
    制造歧义(让 calibration 信号出现)。
    """
    rng = random.Random(seed)
    pairs = [
        ("nginx", "postgresql"),   # nginx error vs SQL exception
        ("python", "systemd"),     # python traceback vs systemd restart
        ("docker", "nginx"),       # docker error vs nginx access
        ("postgresql", "kernel"),  # SQL error vs kernel panic
        ("systemd", "docker"),     # systemd fail vs docker restart
    ]
    samples: list[dict] = []
    for _ in range(count):
        sA, sB = rng.choice(pairs)
        level_A = "info"  # 占位
        content_B = rng.choice(
            LOG_TEMPLATES[sB][rng.choice(["critical", "high", "low", "safe"])]
        )
        ts = f"2026/09/23 {rng.randint(0,23):02d}:{rng.randint(0,59):02d}:{rng.randint(0,59):02d}"
        risk, action = _tier_of(content_B, level_A)
        samples.append({
            "_source": sA,
            "_ts": ts,
            "_level": level_A,
            "_content": content_B,
            "risk_label": risk,
            "_action": action,
            "_mix": f"{sA}+{sB}",
        })
    return samples


# ------------------------------------------------------------
# 主入口
# ------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="M3.49 日志分类训练集准备")
    ap.add_argument("--output", default="data_log.jsonl")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--max-raw", type=int, default=5000)
    ap.add_argument("--mix-count", type=int, default=0,
                    help="M3.49 v2: cross-source mixing 边界样本数(默认 0 不开)")
    args = ap.parse_args()

    print(f"[1/5] 规则生成(最多 {args.max_raw} 条)...")
    t0 = time.time()
    samples = generate(max_raw=args.max_raw)
    print(f"  + 候选 {len(samples)} 条,耗时 {time.time()-t0:.1f}s")

    if args.mix_count > 0:
        print(f"[2/5] cross-source mixing 加 {args.mix_count} 条边界样本...")
        mix = _cross_source_mix(count=args.mix_count)
        samples.extend(mix)
        print(f"  + 混合后 {len(samples)} 条")
        by_tier_mix: dict[str, int] = {}
        for s in mix:
            by_tier_mix[s["risk_label"]] = by_tier_mix.get(s["risk_label"], 0) + 1
        print(f"  by_tier(mix): {by_tier_mix}")
    else:
        print("[2/5] cross-source mixing 关闭(默认)")

    by_tier: dict[str, int] = {}
    for s in samples:
        by_tier[s["risk_label"]] = by_tier.get(s["risk_label"], 0) + 1
    print(f"  by_tier(总): {by_tier}")

    print(f"[3/5] 多样性平衡(各 tier cap 到 RISK_MIN_TARGETS,总 ≤ {args.limit})...")
    balanced = _balance_diversity(samples, args.limit)
    print(f"  + {len(balanced)} 条入训练集")

    print(f"[4/5] 写盘: {args.output}")
    out = Path(args.output)
    final_by_tier: dict[str, int] = {}
    final_by_action: dict[str, int] = {}
    with out.open("w", encoding="utf-8") as f:
        for s in balanced:
            row = {
                "text": _build_text(s["_source"], s["_ts"], s["_level"], s["_content"]),
                "risk_label": s["risk_label"],
                "jailbreak_label": False,
                "_action": s["_action"],
                "_source": s["_source"],
                "_ts": s["_ts"],
                "_level": s["_level"],
                "_content": s["_content"][:300],
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            final_by_tier[s["risk_label"]] = final_by_tier.get(s["risk_label"], 0) + 1
            final_by_action[s["_action"]] = final_by_action.get(s["_action"], 0) + 1
    print(f"\n  by_tier: {final_by_tier}")
    print(f"  by_action: {final_by_action}")
    print(f"  ✅ 写盘: {args.output} ({out.stat().st_size//1024} KB, {len(balanced)} 条)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())