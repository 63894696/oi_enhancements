#!/usr/bin/env python3
"""dl_perf_adapter.py — 分批下载 perf + perf_conf adapter(避免单连接超时)"""
import paramiko, os, sys

HOST = '43.106.53.242'
USER = 'root'
PASS = 'PrisirTrain2026!'
LOCAL_BASE = 'D:/prisir-train-assets/trained'

# (remote_dir, local_dir_name)
DIRS = [
    ('/workspace/qwen3guard-perf', 'perf'),
    ('/workspace/qwen3guard-perf-conf', 'perf_conf'),
]

def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=15, banner_timeout=15, auth_timeout=15)
    return c

def dl_one(c, remote, local):
    """下载单个文件,每次新 sftp session 避免长 session 卡死"""
    sftp = c.open_sftp()
    sftp.get(remote, local)
    sftp.close()

def main():
    os.makedirs(LOCAL_BASE, exist_ok=True)
    c = connect()
    for remote_root, name in DIRS:
        adapter_dir = f'{remote_root}/adapter'
        local_dir = f'{LOCAL_BASE}/{name}/adapter'
        os.makedirs(local_dir, exist_ok=True)
        # 列文件
        sftp = c.open_sftp()
        files = sftp.listdir_attr(adapter_dir)
        sftp.close()
        for f in files:
            rp = f'{adapter_dir}/{f.filename}'
            lp = f'{local_dir}/{f.filename}'
            if os.path.exists(lp) and os.path.getsize(lp) == f.st_size:
                print(f'SKIP {rp} ({f.st_size} bytes)')
                continue
            print(f'GET {rp} -> {lp} ({f.st_size} bytes)', flush=True)
            dl_one(c, rp, lp)
            print(f'  OK {os.path.getsize(lp)} bytes', flush=True)
    c.close()
    print('ALL DOWNLOADED')

if __name__ == "__main__":
    main()