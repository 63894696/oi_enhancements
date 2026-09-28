#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_prep_tempfile_v2.py — M3.66+ 扩 tempfile 数据(2026-09-24)

目的:补强现有 tempfile 数据集(290 条)的两大弱项:
  - critical 20→120(证书/密钥边界 + dev project context,容易跟 high 混)
  - safe 30→120(临时文件/锁文件/构建产物,~pycache__/node_modules 误报)
  - 其它类 80→160(略补)

合成策略:不依赖全盘扫描,基于已知路径模式 + 训练模板变异,
让 AgentJev head 学到"路径段+扩展名组合"特征,而不是记忆具体路径。

输出 schema:text + risk_label,与 data_tempfile.jsonl 完全兼容。
"""
from __future__ import annotations
import json
import random
from pathlib import Path


# ---------- 路径模板 ----------
# critical:密钥/凭证/隐私
CRITICAL_PATH_TEMPLATES = [
    "{base}/{project}/.env",
    "{base}/{project}/.env.local",
    "{base}/{project}/.env.production",
    "{base}/{project}/secrets.yml",
    "{base}/{project}/secrets.yaml",
    "{base}/{project}/credentials.json",
    "{base}/{project}/service-account.json",
    "{base}/{project}/.aws/credentials",
    "{base}/{project}/.ssh/id_rsa",
    "{base}/{project}/.ssh/id_ed25519",
    "{base}/{project}/.ssh/known_hosts",
    "{base}/{project}/.ssh/config",
    "{base}/{project}/.kube/config",
    "{base}/{project}/.netrc",
    "{base}/{project}/.npmrc",
    "{base}/{project}/.pypirc",
    "{base}/{project}/certs/server.pem",
    "{base}/{project}/certs/server.key",
    "{base}/{project}/certs/client.pfx",
    "{base}/{project}/certs/ca-bundle.pem",
    "{base}/{project}/ssl/private.key",
    "{base}/{project}/ssl/cert.pem",
    "{base}/{project}/keystore.jks",
    "{base}/{project}/ssl/keystore.p12",
    "{base}/{project}/dist/server.pem",
    "{base}/{project}/build/server.key",
    "{base}/{project}/node_modules/some-pkg/private.key",  # 误报位
    "{base}/{project}/__pycache__/secrets.json",
    "{base}/{project}/target/release/server.pem",
    "{base}/{project}/.venv/lib/secrets.yml",
    # Windows 路径
    "C:/Users/{user}/Documents/{project}/.env",
    "C:/Users/{user}/AppData/Roaming/{project}/credentials.json",
    "C:/ProgramData/{project}/config/server.pem",
    "D:/projects/{project}/.aws/credentials",
    "D:/work/{project}/.ssh/id_rsa",
]

# safe:临时/锁/构建缓存
SAFE_PATH_TEMPLATES = [
    "{base}/{project}/~$document.docx",
    "{base}/{project}/.~$lockfile.lck",
    "{base}/{project}/build/output.tmp",
    "{base}/{project}/dist/cache.bak",
    "{base}/{project}/session.swp",
    "{base}/{project}/session.swo",
    "{base}/{project}/file.old",
    "{base}/{project}/file.orig",
    "{base}/{project}/file.rej",
    "{base}/{project}/debug.log",
    "{base}/{project}/access.log",
    "{base}/{project}/error.log",
    "{base}/{project}/test.tmp",
    "{base}/{project}/scratch.bak",
    "{base}/{project}/.DS_Store",
    "{base}/{project}/Thumbs.db",
    "{base}/{project}/Desktop.ini",
    "{base}/{project}/__pycache__/module.cpython-312.pyc",
    "{base}/{project}/.pytest_cache/v/cache/lastfailed",
    "{base}/{project}/.mypy_cache/3.12/data.json",
    "{base}/{project}/.ruff_cache/.gitignore",
    "{base}/{project}/htmlcov/index.html",
    "{base}/{project}/.coverage",
    "{base}/{project}/coverage.xml",
    "{base}/{project}/.nyc_output/processinfo.json",
]

# low:cache/build 目录
LOW_PATH_TEMPLATES = [
    "{base}/{project}/__pycache__/module.pyc",
    "{base}/{project}/.pytest_cache/README.md",
    "{base}/{project}/.mypy_cache/data.json",
    "{base}/{project}/.ruff_cache/CACHEDIR.TAG",
    "{base}/{project}/.tox/{env}/log/{test}.log",
    "{base}/{project}/dist/{pkg}-{ver}.tar.gz",
    "{base}/{project}/build/lib/{module}.py",
    "{base}/{project}/.eggs/{pkg}-{ver}.egg-info/PKG-INFO",
    "{base}/{project}/htmlcov/{module}_py.html",
    "{base}/{project}/.coverage.{hash}",
    "{base}/{project}/coverage/lcov.info",
    "{base}/{project}/.nyc_output/out.json",
    "{base}/{project}/target/doc/{module}.html",
]

# medium:node_modules / venv / IDE
MEDIUM_PATH_TEMPLATES = [
    "{base}/{project}/node_modules/{pkg}/package.json",
    "{base}/{project}/node_modules/{pkg}/index.js",
    "{base}/{project}/node_modules/.cache/{hash}",
    "{base}/{project}/target/release/{binary}.exe",
    "{base}/{project}/target/debug/{binary}",
    "{base}/{project}/.venv/Lib/site-packages/{pkg}/{module}.py",
    "{base}/{project}/venv/bin/{cmd}",
    "{base}/{project}/.cargo/registry/cache/{crate}-{ver}.crate",
    "{base}/{project}/.gradle/caches/{hash}/output.bin",
    "{base}/{project}/.idea/workspace.xml",
    "{base}/{project}/.idea/modules.xml",
    "{base}/{project}/.vscode/settings.json",
]

# high:源码/配置
HIGH_PATH_TEMPLATES = [
    "{base}/{project}/src/{module}.py",
    "{base}/{project}/src/{module}.ts",
    "{base}/{project}/src/{module}.tsx",
    "{base}/{project}/src/{module}.js",
    "{base}/{project}/src/{module}.jsx",
    "{base}/{project}/src/{module}.go",
    "{base}/{project}/src/{module}.rs",
    "{base}/{project}/src/{module}.java",
    "{base}/{project}/src/{module}.kt",
    "{base}/{project}/src/{module}.c",
    "{base}/{project}/src/{module}.cpp",
    "{base}/{project}/tests/test_{module}.py",
    "{base}/{project}/README.md",
    "{base}/{project}/CHANGELOG.md",
    "{base}/{project}/config.yaml",
    "{base}/{project}/config.yml",
    "{base}/{project}/settings.toml",
    "{base}/{project}/package.json",
    "{base}/{project}/Cargo.toml",
    "{base}/{project}/pyproject.toml",
    "{base}/{project}/pom.xml",
    "{base}/{project}/build.gradle",
    "{base}/{project}/.gitignore",
    "{base}/{project}/.editorconfig",
]


PROJECTS = [
    "myapp", "webapp", "backend", "frontend", "service", "api",
    "dashboard", "tool", "scripts", "tools", "lib", "core",
    "agent", "model", "data", "etl", "ingest", "train",
    "billing", "auth", "user", "admin", "chat", "search",
]
BASES = [
    "C:/Users/admin/projects",
    "C:/Users/admin/Documents",
    "C:/Users/admin/code",
    "D:/projects",
    "D:/work",
    "D:/code",
    "D:/repos",
    "/home/admin/projects",
    "/home/admin/code",
    "/var/www",
]
USERS = ["admin", "Administrator", "dev", "user", "alice", "bob"]

PKGS = ["react", "lodash", "axios", "express", "next", "vue", "webpack", "babel"]
ENVS = ["py312", "py311", "py310", "cp39"]
TESTS = ["test_foo", "test_bar", "test_baz"]
MODULES = ["main", "utils", "config", "models", "views", "auth", "billing", "chat"]


def synth_path(template: str, rng: random.Random) -> str:
    return template.format(
        base=rng.choice(BASES),
        project=rng.choice(PROJECTS),
        user=rng.choice(USERS),
        pkg=rng.choice(PKGS),
        env=rng.choice(ENVS),
        test=rng.choice(TESTS),
        module=rng.choice(MODULES),
        hash=f"{rng.randint(0x100000, 0xffffff):06x}",
        binary="app",
        ver=f"{rng.randint(0,5)}.{rng.randint(0,20)}.{rng.randint(0,99)}",
        cmd="activate",
        crate="serde",
    )


def gen_samples(target_per_class: dict, total_cap: int, rng: random.Random) -> list[dict]:
    """每类生成 target_per_class 条,total_cap 上限"""
    samples = []
    templates_map = {
        "critical": CRITICAL_PATH_TEMPLATES,
        "safe": SAFE_PATH_TEMPLATES,
        "low": LOW_PATH_TEMPLATES,
        "medium": MEDIUM_PATH_TEMPLATES,
        "high": HIGH_PATH_TEMPLATES,
    }
    action_map = {
        "critical": "keep",  # 永远不删
        "safe": "delete",
        "low": "delete",
        "medium": "review",
        "high": "review",
    }
    for label, target in target_per_class.items():
        templates = templates_map[label]
        for i in range(target):
            t = rng.choice(templates)
            path = synth_path(t, rng)
            samples.append({
                "id": f"tempfile_v2.{label}.{i:04d}",
                "text": f"文件路径: {path}",
                "risk_label": label,
                "_action": action_map[label],
            })
            if len(samples) >= total_cap:
                return samples
    return samples


def main() -> int:
    rng = random.Random(42)
    # 目标:critical 120 + safe 120 + low 80 + medium 80 + high 80 = 480 合成
    # 加上现有 290 = 770
    target = {
        "critical": 120,
        "safe": 120,
        "low": 80,
        "medium": 80,
        "high": 80,
    }
    synth = gen_samples(target, total_cap=1000, rng=rng)
    rng.shuffle(synth)

    # 拼接到现有 jsonl
    out_path = Path(__file__).parent / "data" / "data_tempfile_v2.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for s in synth:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    label_counts = {}
    for s in synth:
        label_counts[s["risk_label"]] = label_counts.get(s["risk_label"], 0) + 1
    print(f"✅ tempfile v2: {len(synth)} samples → {out_path}")
    print(f"   分布: {label_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())