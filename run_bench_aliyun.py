#!/usr/bin/env python3
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('43.106.53.242', username='root', password='PrisirTrain2026!', timeout=10, banner_timeout=10, auth_timeout=10)

si, so, se = c.exec_command(
    "cd /workspace/companion && python3 -c \"\nimport sys\nsys.path.insert(0, '/workspace/companion')\nfrom pathlib import Path\nfrom dataclasses import replace\nimport adapter_registry\n\nALIYUN_BASE = Path('/workspace/models/Qwen3Guard-Gen-0.6B')\n# Override base model path\nfor name in list(adapter_registry.ADAPTERS.keys()):\n    old = adapter_registry.ADAPTERS[name]\n    adapter_registry.ADAPTERS[name] = replace(old, base_model=ALIYUN_BASE)\n\n# Override intents + intents_conf adapter paths\nfor name, aliyun_dir in [('intents','/workspace/qwen3guard-intents/adapter'),\n                          ('intents_conf','/workspace/qwen3guard-intents-conf/adapter')]:\n    old = adapter_registry.ADAPTERS[name]\n    adapter_registry.ADAPTERS[name] = replace(old, adapter_path=Path(aliyun_dir))\n\nprint('intents base:', adapter_registry.ADAPTERS['intents'].base_model)\nprint('intents_conf adapter:', adapter_registry.ADAPTERS['intents_conf'].adapter_path)\n\nimport bench_intents_local\nfrom bench_intents_local import TEST_CASES\nreport = bench_intents_local.run_bench(TEST_CASES)\nPath('/workspace/bench_intents_local_2026-09-23.json').write_text(\n    __import__('json').dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')\nprint('REPORT WRITTEN')\n\" 2>&1 | tail -65",
    timeout=900,
)
print(so.read().decode(errors="replace"))
c.close()