#!/usr/bin/env python3
"""sync_to_aliyun.py — 同步本地训练数据 + perf schema 补丁到 aliyun T4"""
import sys
from pathlib import Path
import paramiko

HOST = '43.106.53.242'
USER = 'root'
PASS = 'PrisirTrain2026!'

LOCAL_FILES = [
    # 训练数据
    ('companion/data/data_perf.jsonl', '/workspace/data_perf.jsonl'),
    ('companion/data_prep_perf.py', '/workspace/companion/data_prep_perf.py'),
    ('companion/data_prep_perf_ndis.py', '/workspace/companion/data_prep_perf_ndis.py'),
    ('companion/perf_collector.py', '/workspace/companion/perf_collector.py'),
    # schema 补丁
    ('companion/train_step1.py', '/workspace/companion/train_step1.py'),
    ('companion/confidence_teacher.py', '/workspace/companion/confidence_teacher.py'),
]


def main():
    sftp_files = sys.argv[1:] if len(sys.argv) > 1 else [l for l, _ in LOCAL_FILES]
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=15, banner_timeout=15, auth_timeout=15)
    sftp = c.open_sftp()
    for f in sftp_files:
        # f = input
        if f in dict(LOCAL_FILES):
            local, remote = dict(LOCAL_FILES)[f], None
            # lookup by remote
            for l_, r_ in LOCAL_FILES:
                if l_ == f:
                    local = l_
                    remote = r_
                    break
        else:
            # direct path
            remote = f'/workspace/companion/{Path(f).name}'
            local = f
        print(f'PUT {local} -> {remote}')
        sftp.put(local, remote)
    sftp.close()
    print('SYNC OK')
    c.close()


if __name__ == "__main__":
    main()