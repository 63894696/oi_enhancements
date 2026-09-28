#!/usr/bin/env python3
"""_probe_data.py — 看 tempfile_conf 数据格式"""
import json

with open('/workspace/data_tempfile_conf.jsonl') as f:
    for i, line in enumerate(f):
        if i >= 3:
            break
        d = json.loads(line)
        print(f"--- sample {i} ---")
        print(f"keys: {list(d.keys())}")
        print(f"risk: {d.get('risk_label')} action: {d.get('_action')}")
        print(f"_conf: {d.get('_conf')}")
        tgt = d.get('target', '')
        if not tgt:
            print(f"text: {d.get('text','')[:100]}")
        else:
            print(f"target: {tgt[:200]}")