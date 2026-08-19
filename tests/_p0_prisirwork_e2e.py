# P0 验收(CW-1 骨架):真跑 Prisir 工坊(PrisirWork),逐项验证红线。
# 断言:无 token→401 / 错 token→401 / 对 token→200 / 白名单外→404
#       /health 免 token 探活 /wallet/status 不可用如实报(不伪造)
#       只监听 127.0.0.1(外部地址连不上)
import json, socket, subprocess, sys, time, urllib.request, urllib.error, os, tempfile

PORT = 12450
TOKEN = "p0test_" + "a" * 56  # 测试 token,固定便于逐个用例带/不带/带错
BASE = f"http://127.0.0.1:{PORT}"

cfg = os.path.join(tempfile.mkdtemp(prefix="oi_p0_"), "work.json")
oi_home = tempfile.mkdtemp(prefix="oi_p0home_")  # F3 隔离 OI_HOME,team 测试不污染真库
audit_file = os.path.join(tempfile.mkdtemp(prefix="oi_p0aud_"), "audit.jsonl")  # F5 隔离审计
env = dict(os.environ, PRISIR_WORK_CONFIG=cfg, PRISIR_WORK_PORT=str(PORT),
           PRISIR_WORK_OI_HOME=oi_home, PRISIR_WORK_AUDIT=audit_file)

# 起 Prisir 工坊(token 由 config 生成;但我们想固定 token 做断言 → 先写配置)
os.makedirs(os.path.dirname(cfg), exist_ok=True)
json.dump({"token": TOKEN, "port": PORT}, open(cfg, "w"))
proc = subprocess.Popen([sys.executable, "-m", "prisir_work", str(PORT)], env=env,
                        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

def req(path, token=None, method="GET", body=None):
    r = urllib.request.Request(BASE + path, method=method)
    if token is not None:
        r.add_header("X-OI-Token", token)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        r.add_header("Content-Type", "application/json")
        r.add_header("Content-Length", str(len(data)))
        r.data = data
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: body2 = json.loads(e.read().decode())
        except Exception: body2 = {}
        return e.code, body2

# 等服务起来
up = False
for _ in range(50):
    try:
        s, b = req("/health"); up = (s == 200);
        if up: break
    except Exception: time.sleep(0.2)
if not up:
    print("P0: FAIL (服务未起来)"); proc.kill(); sys.exit(1)

res = {}
res["health_免token"] = req("/health")                       # 期望 200
res["wallet_无token"] = req("/wallet/status")               # 期望 401
res["wallet_错token"] = req("/wallet/status", token="wrong")# 期望 401
res["wallet_对token"] = req("/wallet/status", token=TOKEN)  # 期望 200 + daemon unavailable(未装 electrum)
res["白名单外"]      = req("/admin/shell", token=TOKEN)      # 期望 404
res["白名单外2"]     = req("/wallet/nonexist", token=TOKEN)  # 未登记 wallet 子路径 → 404(即使带对 token)

# F1 能力门面:search/execute 两入口
res["cap_search_无token"] = req("/cap/search", method="POST", body={"query": ""})                 # 期望 401
res["cap_search_钱包"]    = req("/cap/search", token=TOKEN, method="POST", body={"query": "钱包"}) # 期望 200 命中 wallet.status
res["cap_search_空"]      = req("/cap/search", token=TOKEN, method="POST", body={"query": ""})     # 期望 200 全量
res["cap_exec_无token"]   = req("/cap/execute", method="POST", body={"id": "wallet.status"})       # 期望 401
res["cap_exec_未知能力"]  = req("/cap/execute", token=TOKEN, method="POST", body={"id": "nope.x"}) # 期望 404
res["cap_exec_health"]    = req("/cap/execute", token=TOKEN, method="POST", body={"id": "system.health"})  # 期望 200 经门面跑通
res["cap_exec_wallet"]    = req("/cap/execute", token=TOKEN, method="POST", body={"id": "wallet.status"})  # 期望 200 路由到端点

# F2 wallet 能力接入 + L3 授权门
res["wallet_recv_无token"] = req("/wallet/receive", method="POST", body={})                      # 期望 401
res["wallet_recv"]         = req("/wallet/receive", token=TOKEN, method="POST", body={"memo": "t"})  # 期望 200 daemon unavailable
res["wallet_history"]      = req("/wallet/history", token=TOKEN)                               # 期望 200 daemon unavailable
res["payto_无token"]       = req("/wallet/payto", method="POST", body={"address": "a", "amount": 1})  # 期望 401
res["payto_daemon"]        = req("/wallet/payto", token=TOKEN, method="POST",
                                 body={"address": "tb1qx", "amount": 0.1})                      # 期望 200 daemon unavailable(未到授权门)
res["cap_search_付款"]     = req("/cap/search", token=TOKEN, method="POST", body={"query": "付款"}) # 期望命中 wallet.payto L3
res["cap_exec_payto"]      = req("/cap/execute", token=TOKEN, method="POST",
                                 body={"id": "wallet.payto", "args": {"address": "tb1qx", "amount": 0.1}})  # 期望经门面,L3 标注

# F3 oiagent 团队协作:派单(L1)+ 查状态(L0),OI_HOME 已隔离
res["team_submit_无token"] = req("/team/submit", method="POST", body={"title": "x"})             # 期望 401
res["team_submit"]         = req("/team/submit", token=TOKEN, method="POST",
                                 body={"title": "p0 测试派单", "content": "验证 F3"})            # 期望 200 + task_id
res["team_submit_空题"]    = req("/team/submit", token=TOKEN, method="POST", body={"title": ""}) # 期望 ok=False title_required
res["team_list_无token"]   = req("/team/list", method="POST", body={})                           # 期望 401
res["team_list_ready"]     = req("/team/list", token=TOKEN, method="POST", body={"status": "ready"})  # 期望 200 含刚派的单
res["cap_search_派单"]     = req("/cap/search", token=TOKEN, method="POST", body={"query": "派单"}) # 期望命中 team.submit L1
res["cap_exec_team_list"]  = req("/cap/execute", token=TOKEN, method="POST",
                                 body={"id": "team.list", "args": {"status": "ready"}})          # 期望经门面跑通

# 只监听 127.0.0.1:本机非回环地址应连不上
ext_ip = None
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); ext_ip = s.getsockname()[0]; s.close()
except Exception: pass
loopback_only = None
if ext_ip and not ext_ip.startswith("127."):
    try:
        urllib.request.urlopen(f"http://{ext_ip}:{PORT}/health", timeout=2)
        loopback_only = False  # 能从外部地址连上 = 红线破
    except Exception:
        loopback_only = True

proc.terminate()
try: proc.wait(timeout=5)
except Exception: proc.kill()

def ok(c): return c == 200
checks = {
  "health 免 token 200": res["health_免token"][0] == 200,
  "wallet 无 token 401": res["wallet_无token"][0] == 401 and res["wallet_无token"][1].get("error") == "unauthorized",
  "wallet 错 token 401": res["wallet_错token"][0] == 401,
  "wallet 对 token 200": res["wallet_对token"][0] == 200,
  "wallet 如实报 unavailable": res["wallet_对token"][1].get("daemon") == "unavailable",
  "白名单外 404": res["白名单外"][0] == 404,
  "白名单外 wallet 子路径 404": res["白名单外2"][0] == 404,
  "只监听 127.0.0.1": (loopback_only is True),
  # F1 能力门面
  "cap/search 无 token 401": res["cap_search_无token"][0] == 401,
  "cap/search 命中钱包能力": res["cap_search_钱包"][0] == 200 and any(
      c.get("id") == "wallet.status" for c in res["cap_search_钱包"][1].get("capabilities", [])),
  "cap/search 空 query 返回全量": res["cap_search_空"][0] == 200 and
      len(res["cap_search_空"][1].get("capabilities", [])) >= 2,
  "cap/execute 无 token 401": res["cap_exec_无token"][0] == 401,
  "cap/execute 未知能力 404": res["cap_exec_未知能力"][0] == 404 and
      res["cap_exec_未知能力"][1].get("error") == "unknown_capability",
  "cap/execute system.health 跑通": res["cap_exec_health"][0] == 200 and
      res["cap_exec_health"][1].get("capability") == "system.health",
  "cap/execute wallet.status 路由到端点": res["cap_exec_wallet"][0] == 200 and
      res["cap_exec_wallet"][1].get("result", {}).get("daemon") == "unavailable",
  # F2 wallet 能力接入 + L3 授权门
  "wallet/receive 无 token 401": res["wallet_recv_无token"][0] == 401,
  "wallet/receive 如实报 unavailable": res["wallet_recv"][0] == 200 and
      res["wallet_recv"][1].get("daemon") == "unavailable",
  "wallet/history 如实报 unavailable": res["wallet_history"][0] == 200 and
      res["wallet_history"][1].get("daemon") == "unavailable",
  "wallet/payto 无 token 401": res["payto_无token"][0] == 401,
  "wallet/payto daemon 不可用如实报(未到授权门)": res["payto_daemon"][0] == 200 and
      res["payto_daemon"][1].get("daemon") == "unavailable",
  "cap/search 付款命中 wallet.payto": res["cap_search_付款"][0] == 200 and any(
      c.get("id") == "wallet.payto" and c.get("risk") == "L3"
      for c in res["cap_search_付款"][1].get("capabilities", [])),
  "cap/execute wallet.payto 带 L3 标注": res["cap_exec_payto"][0] == 200 and
      res["cap_exec_payto"][1].get("capability") == "wallet.payto" and
      res["cap_exec_payto"][1].get("risk") == "L3",
  # F3 oiagent 团队协作
  "team/submit 无 token 401": res["team_submit_无token"][0] == 401,
  "team/submit 派单成功返回 task_id": res["team_submit"][0] == 200 and
      res["team_submit"][1].get("ok") is True and
      isinstance(res["team_submit"][1].get("task_id"), int),
  "team/submit 空题被拒": res["team_submit_空题"][1].get("ok") is False and
      res["team_submit_空题"][1].get("error") == "title_required",
  "team/list 无 token 401": res["team_list_无token"][0] == 401,
  "team/list ready 含刚派的单": res["team_list_ready"][0] == 200 and any(
      t.get("title", "").endswith("p0 测试派单") for t in res["team_list_ready"][1].get("tasks", [])),
  "cap/search 派单命中 team.submit L1": res["cap_search_派单"][0] == 200 and any(
      c.get("id") == "team.submit" and c.get("risk") == "L1"
      for c in res["cap_search_派单"][1].get("capabilities", [])),
  "cap/execute team.list 跑通": res["cap_exec_team_list"][0] == 200 and
      res["cap_exec_team_list"][1].get("capability") == "team.list" and
      res["cap_exec_team_list"][1].get("result", {}).get("ok") is True,
}

# F5 审计:L1+ 端点已留痕,L0 只读不记,且审计行不含口令/token
audit_events = []
if os.path.exists(audit_file):
    for line in open(audit_file, encoding="utf-8"):
        line = line.strip()
        if line:
            audit_events.append(json.loads(line))
audited_eps = {e.get("endpoint") for e in audit_events}
audit_blob = json.dumps(audit_events, ensure_ascii=False)
checks["F5 审计:L1 team/submit 已留痕"] = "/team/submit" in audited_eps
checks["F5 审计:L3 wallet/payto 已留痕"] = "/wallet/payto" in audited_eps
checks["F5 审计:L0 只读不留痕(health/status)"] = "/health" not in audited_eps and "/wallet/status" not in audited_eps
checks["F5 审计:不含 token"] = TOKEN not in audit_blob
checks["F5 审计:事件含 risk+ok+ts"] = all(
    ("risk" in e and "ok" in e and "ts" in e) for e in audit_events) and len(audit_events) > 0
print("逐项:", json.dumps(checks, ensure_ascii=False, indent=2))
print("wallet/status 实回:", json.dumps(res["wallet_对token"][1], ensure_ascii=False))
if loopback_only is None:
    print("(未能取得非回环 IP,loopback 项跳过)")

# F2 mainnet 守卫:换 OI_ELECTRUM_NET=mainnet 起第二个实例,payto 必须被拒
PORT2 = 12451
cfg2 = os.path.join(tempfile.mkdtemp(prefix="oi_p0m_"), "work.json")
json.dump({"token": TOKEN, "port": PORT2}, open(cfg2, "w"))
env2 = dict(os.environ, PRISIR_WORK_CONFIG=cfg2, PRISIR_WORK_PORT=str(PORT2),
            OI_ELECTRUM_NET="mainnet")
proc2 = subprocess.Popen([sys.executable, "-m", "prisir_work", str(PORT2)], env=env2,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
up2 = False
for _ in range(50):
    try:
        r = urllib.request.Request(f"http://127.0.0.1:{PORT2}/health")
        with urllib.request.urlopen(r, timeout=2) as resp:
            up2 = (resp.status == 200)
        if up2: break
    except Exception: time.sleep(0.2)
mainnet_blocked = False
if up2:
    try:
        rq = urllib.request.Request(f"http://127.0.0.1:{PORT2}/wallet/payto", method="POST")
        rq.add_header("X-OI-Token", TOKEN); rq.add_header("Content-Type", "application/json")
        data = json.dumps({"address": "1abc", "amount": 0.1}).encode(); rq.data = data
        with urllib.request.urlopen(rq, timeout=5) as resp:
            b = json.loads(resp.read().decode())
        mainnet_blocked = (b.get("ok") is False and b.get("error") == "mainnet_forbidden")
    except Exception as e:
        print("(mainnet 守卫验证异常:", e, ")")
proc2.terminate()
try: proc2.wait(timeout=5)
except Exception: proc2.kill()
checks["mainnet 守卫:OI_ELECTRUM_NET=mainnet 拒绝付款"] = mainnet_blocked
print("mainnet 守卫:", "PASS" if mainnet_blocked else "FAIL")

print("P0 验收:", "PASS" if all(v for k, v in checks.items() if v is not None) else "FAIL")
