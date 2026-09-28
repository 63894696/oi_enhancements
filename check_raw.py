#!/usr/bin/env python3
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('43.106.53.242', username='root', password='PrisirTrain2026!', timeout=10, banner_timeout=10, auth_timeout=10)

si, so, se = c.exec_command(
    "cd /workspace/companion && python3 -c \"\nimport sys\nsys.path.insert(0, '/workspace/companion')\nfrom pathlib import Path\nfrom dataclasses import replace\nimport adapter_registry\nfor name, aliyun_dir in [('intents','/workspace/qwen3guard-intents/adapter'),\n                          ('intents_conf','/workspace/qwen3guard-intents-conf/adapter')]:\n    old = adapter_registry.ADAPTERS[name]\n    adapter_registry.ADAPTERS[name] = replace(old, adapter_path=Path(aliyun_dir))\nfrom classify_intents import classify_intents\nimport json\nout = classify_intents('帮我打开浏览器', use_conf=True)\nprint('--- raw ---')\nprint(repr(out['raw']))\nprint('--- parsed ---')\nprint(json.dumps(out, ensure_ascii=False, indent=2))\n\" 2>&1 | tail -20",
    timeout=120,
)
print(so.read().decode(errors="replace"))
c.close()