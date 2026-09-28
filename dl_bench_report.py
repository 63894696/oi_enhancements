#!/usr/bin/env python3
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('43.106.53.242', username='root', password='PrisirTrain2026!', timeout=10, banner_timeout=10, auth_timeout=10)
sftp = c.open_sftp()
sftp.get("/workspace/bench_intents_local_2026-09-23.json",
         "C:/Users/Administrator/oi_enhancements/companion/reports/bench_intents_local_2026-09-23.json")
sftp.close()
print("REPORT DOWNLOADED")
c.close()