#!/usr/bin/env python3
"""_aliyun_adapter_patch.py — aliyun 上跑 disk_cleanup_conf 评测(用本地路径)"""
import sys, json
sys.path.insert(0, "/workspace/companion")

# 强制覆盖 adapter 路径到 aliyun 本地
import adapter_registry as ar
from pathlib import Path as _P
ar.ADAPTERS["disk_cleanup_conf"] = ar.AdapterSpec(
    name="disk_cleanup_conf",
    schema="disk_cleanup_conf",
    base_model=_P("/workspace/models/Qwen3Guard-Gen-0.6B"),
    adapter_path=_P("/workspace/qwen3guard-disk-cleanup-conf/adapter"),
    description="disk_cleanup_conf on aliyun",
)

from classify_disk_cleanup import classify_one
adapter = ar.get_adapter("disk_cleanup_conf")

paths = [
    r"C:\Windows\Temp\setupapi.dev.log",
    r"C:\Windows\Prefetch\READYBOOST.DAT",
    r"C:\Windows\Logs\CBS\CBS.log",
    r"C:\Windows\WinSxS\Backup\foo.dll",
    r"C:\Windows\SoftwareDistribution\Download\update.cab",
    r"C:\Windows\System32\DriverStore\Temp\bar.inf",
    r"C:\Windows\Installer\$PatchCache$\old.msi",
]

for p in paths:
    r = classify_one(adapter, p)
    print(json.dumps(r, ensure_ascii=False))