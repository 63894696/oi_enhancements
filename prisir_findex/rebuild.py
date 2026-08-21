# -*- coding: utf-8 -*-
import time, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shell_findex import Findex, DEFAULT_DB

for suf in ['', '-wal', '-shm']:
    p = DEFAULT_DB + suf
    if os.path.exists(p):
        try:
            os.remove(p); print('removed', p)
        except Exception as e:
            print('cannot remove', p, e)

fx = Findex(DEFAULT_DB)
roots = [d for d in ['C:/', 'D:/', 'E:/', 'F:/', 'G:/', 'H:/'] if os.path.exists(d)]
print('roots:', roots)
t0 = time.perf_counter()
r = fx.enable(roots)
dt = time.perf_counter() - t0
print('全盘重建 %s 条 in %.0fs -> %s' % (r.get('scanned'), dt, r))
fx.close()
