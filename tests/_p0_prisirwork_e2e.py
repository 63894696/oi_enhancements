# P0 验收(CW-1 骨架):真跑 Prisir 工坊(PrisirWork),逐项验证红线。
# 断言:无 token→401 / 错 token→401 / 对 token→200 / 白名单外→404
#       /health 免 token 探活 /wallet/status 不可用如实报(不伪造)
#       只监听 127.0.0.1(外部地址连不上)
import json, socket, subprocess, sys, time, urllib.request, urllib.error, os, tempfile

PORT = 12450
TOKEN = "p0test_" + "a" * 56  # 测试 token,固定便于逐个用例带/不带/带错
BASE = f"http://127.0.0.1:{PORT}"

cfg = os.path.join(tempfile.mkdtemp(prefix="oi_p0_"), "coworker.json")
env = dict(os.environ, OI_COWORKER_CONFIG=cfg, OI_COWORKER_PORT=str(PORT))

# 起 Prisir 工坊(token 由 config 生成;但我们想固定 token 做断言 → 先写配置)
os.makedirs(os.path.dirname(cfg), exist_ok=True)
json.dump({"token": TOKEN, "port": PORT}, open(cfg, "w"))
proc = subprocess.Popen([sys.executable, "-m", "oiagent_coworker", str(PORT)], env=env,
                        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

def req(path, token=None, method="GET"):
    r = urllib.request.Request(BASE + path, method=method)
    if token is not None:
        r.add_header("X-OI-Token", token)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: body = json.loads(e.read().decode())
        except Exception: body = {}
        return e.code, body

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
res["白名单外2"]     = req("/wallet/payto", token=TOKEN, method="POST")  # 未登记 → 404(即使带对 token)

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
  "未登记端点(payto)404": res["白名单外2"][0] == 404,
  "只监听 127.0.0.1": (loopback_only is True),
}
print("逐项:", json.dumps(checks, ensure_ascii=False, indent=2))
print("wallet/status 实回:", json.dumps(res["wallet_对token"][1], ensure_ascii=False))
if loopback_only is None:
    print("(未能取得非回环 IP,loopback 项跳过)")
print("P0 验收:", "PASS" if all(v for k, v in checks.items() if v is not None) else "FAIL")
