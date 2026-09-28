#!/usr/bin/env python3
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('43.106.53.242', username='root', password='PrisirTrain2026!', timeout=10, banner_timeout=10, auth_timeout=10)
sftp = c.open_sftp()
print('listdir /workspace/:', sftp.listdir('/workspace/'))
print('listdir_attr on /workspace/qwen3guard-intents-conf:', sftp.listdir_attr('/workspace/qwen3guard-intents-conf'))
print('listdir_attr on /workspace/qwen3guard-intents-conf/adapter:', sftp.listdir_attr('/workspace/qwen3guard-intents-conf/adapter'))
sftp.close()
c.close()