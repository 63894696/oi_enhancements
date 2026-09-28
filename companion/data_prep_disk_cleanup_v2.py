#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_prep_disk_cleanup_v2.py — M3.66+ 扩 disk_cleanup 数据(2026-09-24)

补强 disk_cleanup 数据集的三大弱项:
  - safe 3→100(Prefetch 自动清理/系统定期删除)
  - high 10→80(DriverStore\Temp / 残留中间产物)
  - critical 30→80(CBS.log / 系统组件备份 / DISM 跟踪日志)

合成策略:基于 Windows 系统路径段 + 文件名特征,
让 AgentJev head 学到"系统目录下的关键路径"特征,而非具体文件名。

输出 schema:text + risk_label + _action,与原 jsonl 兼容。
"""
from __future__ import annotations
import json
import random
from pathlib import Path


# safe:系统定期清理(Prefetch / 旧日志)
SAFE_PATH_TEMPLATES = [
    "C:/Windows/Prefetch/{app}-{hash}.pf",
    "C:/Windows/Prefetch/{app}-{hash}.pf.bak",
    "C:/Windows/Temp/{app}-{hash}.tmp",
    "C:/Windows/Temp/{app}-{hash}.log",
    "C:/Windows/Logs/{subsystem}/{date}.log",
    "C:/Windows/Logs/{subsystem}/{date}.old",
    "C:/Windows/ServiceProfiles/LocalService/AppData/Local/Temp/{app}-{hash}.tmp",
    "C:/Windows/ServiceProfiles/NetworkService/AppData/Local/Temp/{app}-{hash}.tmp",
]

# low:DISM 报告 / LogFiles 文本
LOW_PATH_TEMPLATES = [
    "C:/Windows/Logs/DISM/dism.log",
    "C:/Windows/Logs/DISM/dism_{date}.log",
    "C:/Windows/Logs/CBS/CBS_{date}.log",
    "C:/Windows/Logs/DPX/{date}.log",
    "C:/Windows/System32/LogFiles/Http/httperr{date}.log",
    "C:/Windows/System32/LogFiles/Scm/scm-{date}.log",
    "C:/Windows/System32/winevt/Logs/Application-{date}.evtx",
    "C:/Windows/System32/winevt/Logs/System-{date}.evtx",
]

# medium:SoftwareDistribution\Download / Installer 缓存
MEDIUM_PATH_TEMPLATES = [
    "C:/Windows/SoftwareDistribution/Download/Install/{package}_{hash}.tmp",
    "C:/Windows/SoftwareDistribution/Download/Install/{package}.exe",
    "C:/Windows/Installer/$PatchCache$/{package}_{hash}.cab",
    "C:/Windows/Installer/MSI{temp}.tmp",
    "C:/Windows/System32/catroot2/{{cat_root_hash}}.cat",
    "C:/Windows/System32/catroot2/{{cat_root_hash}}.db",
    "C:/Windows/Logs/WindowsUpdate/WindowsUpdate_{date}.log",
]

# high:DriverStore\Temp / 残留中间产物
HIGH_PATH_TEMPLATES = [
    "C:/Windows/System32/DriverStore/Temp/{{driver_hash}}_{{os_ver}}/driver.inf",
    "C:/Windows/System32/DriverStore/Temp/{{driver_hash}}/setupapi.dev.log",
    "C:/Windows/System32/DriverStore/FileRepository/{driver}/{driver_inf}.inf",
    "C:/Windows/.old/{dir}/{file}",
    "C:/Windows/WinSxS/Backup/{file}",
    "C:/Windows/Installer/{msi_hash}.msi",
    "C:/Windows/Installer/{msi_hash}.msp",
    "C:/Windows/Installer/Properties/{ts}.ini",
]

# critical:CBS.log / DISM 跟踪 / 系统组件备份
CRITICAL_PATH_TEMPLATES = [
    "C:/Windows/Logs/CBS/CBS.log",
    "C:/Windows/Logs/CBS/CbsPersist_{date}.log",
    "C:/Windows/Logs/CBS/CBS_{date}.cab",
    "C:/Windows/Logs/DISM/dism_{date}.log.bak",
    "C:/Windows/Logs/DPX/{date}.log.bak",
    "C:/Windows/WinSxS/Backup/pending.xml",
    "C:/Windows/WinSxS/Backup/{component_hash}.manifest",
    "C:/Windows/WinSxS/Backup/{component_hash}.mum",
    "C:/Windows/System32/catroot2/catroot{{hash}}.cat",  # 关键根证书索引
    "C:/Windows/Boot/PCAT/bootcat.dat",  # 启动目录索引
    "C:/Windows/System32/config/{random}.dat.LOG1",  # 注册表 hive 备份
    "C:/Windows/System32/config/{random}.dat.LOG2",
    "C:/Windows/System32/sru/SRUDB.dat",  # 资源监控数据库
]


APPS = [
    "chrome", "firefox", "msedge", "office", "word", "excel",
    "powershell", "cmd", "explorer", "svchost", "csrss",
    "dwm", "winlogon", "lsass", "services",
]
SUBSYSTEMS = ["CBS", "DISM", "DPX", "WindowsUpdate", "WER", "Setup"]
PACKAGE_PREFIXES = ["Windows10.0-KB", "Windows10.0-KB64", "Package_for_KB", "SSU"]
DRIVERS = ["oem0", "oem10", "oem100", "prnbr002", "prnbr003"]
MSI_HASHES = [f"{i:08X}" for i in range(0x10000, 0x10020)]
COMPONENT_HASHES = [f"{i:08X}" for i in range(0x20000, 0x20020)]
CAT_ROOT_HASHES = [f"{{F750E6C3-38EE-{i:04X}-A4A4-{i:04X}-{i:04X}}}" for i in range(10)]
DRIVER_HASHES = [f"{i:08X}.{j:08X}" for i in range(0x30000, 0x30005) for j in range(0x40000000, 0x40000003)]
RANDOM_HASHES = [f"{{00000000-0000-{i:04X}-{i:04X}-{i:04X}}}" for i in range(10)]
DATES = [f"202509{i:02d}" for i in range(1, 30)] + [f"202510{i:02d}" for i in range(1, 5)]


def synth_path(template: str, rng: random.Random) -> str:
    return template.format(
        app=rng.choice(APPS),
        hash=f"{rng.randint(0x100000, 0xffffff):06x}",
        subsystem=rng.choice(SUBSYSTEMS),
        date=rng.choice(DATES),
        package=rng.choice(PACKAGE_PREFIXES) + str(rng.randint(4000000, 6000000)),
        temp=f"{rng.randint(0,999):03d}",
        driver=rng.choice(DRIVERS),
        driver_inf=rng.choice(["prnms001", "netrtwlane01", "audioendpoint", "intcaudio"]),
        msi_hash=rng.choice(MSI_HASHES),
        component_hash=rng.choice(COMPONENT_HASHES),
        cat_root_hash=rng.choice(CAT_ROOT_HASHES),
        random=rng.choice(RANDOM_HASHES),
        dir=rng.choice(["Users", "Windows", "Program Files", "System32"]),
        file=rng.choice(["setupapi.log", "driver.inf", "manifest.xml"]),
        ts=f"{rng.randint(10000000, 99999999)}",
    )


def gen_samples(target_per_class: dict, rng: random.Random) -> list[dict]:
    samples = []
    templates_map = {
        "safe": SAFE_PATH_TEMPLATES,
        "low": LOW_PATH_TEMPLATES,
        "medium": MEDIUM_PATH_TEMPLATES,
        "high": HIGH_PATH_TEMPLATES,
        "critical": CRITICAL_PATH_TEMPLATES,
    }
    action_map = {
        "safe": "delete",
        "low": "delete",
        "medium": "delete",
        "high": "review",
        "critical": "keep",
    }
    for label, target in target_per_class.items():
        for i in range(target):
            t = rng.choice(templates_map[label])
            path = synth_path(t, rng)
            samples.append({
                "id": f"disk_cleanup_v2.{label}.{i:04d}",
                "text": f"文件路径: {path}",
                "risk_label": label,
                "_action": action_map[label],
            })
    return samples


def main() -> int:
    rng = random.Random(42)
    # 目标:safe 100 + high 80 + critical 80 = 260 合成
    # low/medium 不扩(已 80/80 平衡)
    target = {
        "safe": 100,
        "low": 30,
        "medium": 30,
        "high": 80,
        "critical": 80,
    }
    synth = gen_samples(target, rng)
    rng.shuffle(synth)

    out_path = Path(__file__).parent / "data" / "data_disk_cleanup_v2.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for s in synth:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    label_counts = {}
    for s in synth:
        label_counts[s["risk_label"]] = label_counts.get(s["risk_label"], 0) + 1
    print(f"✅ disk_cleanup v2: {len(synth)} samples → {out_path}")
    print(f"   分布: {label_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())