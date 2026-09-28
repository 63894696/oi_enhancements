#!/usr/bin/env python3
"""dl_perf_conf.py — 用 prefetch + 32KB buffer 下载 perf_conf.tgz"""
import paramiko, os, time

HOST = '43.106.53.242'
USER = 'root'
PASS = 'PrisirTrain2026!'
REMOTE = '/workspace/perf_conf.tgz'
LOCAL = 'D:/prisir-train-assets/trained/perf_conf.tgz'

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=15, banner_timeout=15, auth_timeout=15)
sftp = c.open_sftp()
# prefetch + bufsize
sftp.get(REMOTE, LOCAL, callback=lambda so_far, total: print(f'  {so_far}/{total} ({so_far*100//max(1,total)}%)', end='\r', flush=True))
print()
sftp.close()
c.close()
print('OK')