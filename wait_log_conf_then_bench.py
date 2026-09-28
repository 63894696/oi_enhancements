#!/usr/bin/env python3
# wait_log_conf_then_bench.py — 等 log_conf 训完,自动写 bench_log.py 并跑
import os, paramiko, time, json
ALIYUN = "43.106.53.242"
PASS = os.environ.get("ALIYUN_SSH_PASS", "PrisirTrain2026!")
LOG = "/workspace/run_train_log_conf.log"
ADAPTER = "/workspace/qwen3guard-log-conf/adapter/adapter_model.safetensors"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username="root", password=PASS, timeout=15)

print("[1/4] 等待 log_conf 训练完成...")
last_size = -1; last_change = time.time(); start = time.time()
while time.time() - start < 600:
    si, so, se = c.exec_command(f"stat -c '%s' {LOG} 2>/dev/null && tail -1 {LOG} 2>&1")
    out = so.read().decode(errors="replace")
    size = int(out.split()[0]) if out.split() else 0
    tail = out.split("\n")[-1] if "\n" in out else out
    if size != last_size:
        last_size = size; last_change = time.time()
        print(f"  [{int(time.time()-start)}s] log={size}B | {tail[:80]!r}")
    elif time.time() - last_change > 30:
        print(f"  ✅ log stable 30s + tail={tail[:80]!r}")
        break
    time.sleep(8)

# 验证产物
si, so, se = c.exec_command(f"ls -la {ADAPTER} 2>&1")
print(f"\n[2/4] adapter 文件:\n{so.read().decode().strip()}")

# 写 bench_log.py 到 aliyun(在 companion 里)
bench_script = '''
#!/usr/bin/env python3
# bench_log.py — M3.49 log adapter 评测(2026-09-23)
import argparse, json, re, time
from collections import Counter
from pathlib import Path
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

RISK_LABELS = ["safe", "low", "medium", "high", "critical"]
ACTION_LABELS = ["drop", "keep", "review", "alert"]
PROMPT_TEMPLATE = "user\\n{text}\\nassistant\\n"
TARGET_RE = re.compile(
    r"Safety:\\s*(\\w+)(?::(\\d+(?:\\.\\d+)?))?\\s*\\n?\\s*"
    r"Jailbreak:\\s*(\\w+)(?::(\\d+(?:\\.\\d+)?))?\\s*\\n?\\s*"
    r"(?:Action:\\s*(\\w+)(?::(\\d+(?:\\.\\d+)?))?)?",
    re.IGNORECASE | re.MULTILINE,
)

def parse_target(text):
    idx = text.lower().find("safety:")
    if idx < 0: return None, None, None, None, None, None
    seg = text[idx:]
    second = seg.lower().find("safety:", 8)
    if second > 0: seg = seg[:second]
    m = TARGET_RE.search(seg)
    if not m: return None, None, None, None, None, None
    risk = m.group(1).lower().strip()
    rc = float(m.group(2)) if m.group(2) else None
    jb = m.group(3).lower().strip() in ("yes","true","1")
    jc = float(m.group(4)) if m.group(4) else None
    act = m.group(5).lower().strip() if m.group(5) else None
    ac = float(m.group(6)) if m.group(6) else None
    return risk, jb, act, rc, jc, ac

def classify(model, tok, text, device):
    prompt = PROMPT_TEMPLATE.format(text=text)
    inp = tok(prompt, return_tensors="pt").to(device)
    t0 = time.time()
    with torch.inference_mode():
        out = model.generate(**inp, max_new_tokens=40, do_sample=False,
                              pad_token_id=tok.pad_token_id,
                              repetition_penalty=1.2, no_repeat_ngram_size=6)
    dt = (time.time()-t0)*1000
    gen = out[0][inp["input_ids"].shape[1]:]
    raw = tok.decode(gen, skip_special_tokens=True).strip()
    return raw, dt

def split_data(rows, test_frac=0.2, seed=42):
    import random; rng = random.Random(seed)
    idx = list(range(len(rows))); rng.shuffle(idx); cut = int(len(rows)*test_frac)
    test_idx = set(idx[:cut])
    return ([r for i,r in enumerate(rows) if i not in test_idx],
            [r for i,r in enumerate(rows) if i in test_idx])

def eval_model(model, tok, rows, device="cuda"):
    n = len(rows)
    cr = 0; ca = 0; pf = 0; lats = []
    conf_correct = []; conf_wrong = []
    for r in rows:
        raw, dt = classify(model, tok, r["text"], device)
        lats.append(dt)
        p = parse_target(raw)
        if p[0] is None: pf += 1; continue
        if p[0] == r["risk_label"].lower(): cr += 1; (conf_correct if p[3] else conf_correct).append(p[3]) if p[3] else None
        else: (conf_wrong if p[3] else conf_wrong).append(p[3]) if p[3] else None
        if p[2] and p[2] == r["_action"].lower(): ca += 1
    lats.sort(); p50 = lats[len(lats)//2] if lats else 0; p95 = lats[int(len(lats)*0.95)] if lats else 0
    avg = lambda xs: round(sum(xs)/len(xs), 4) if xs else None
    return {"n":n, "risk_accuracy":cr/n, "action_accuracy":ca/n,
            "parse_fail_rate":pf/n,
            "calibration":{"risk_conf_mean_correct":avg(conf_correct),
                            "risk_conf_mean_wrong":avg(conf_wrong),
                            "n_with_conf":len(conf_correct)+len(conf_wrong)},
            "latency_ms":{"p50":round(p50,1), "p95":round(p95,1),
                           "mean":round(sum(lats)/n,1) if n else 0}}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--base-model", required=True)
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--skip-base", action="store_true")
    a = ap.parse_args()
    rows = [json.loads(l) for l in Path(a.data).read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"[1/4] 加载 {len(rows)} 条,切 80/20")
    _, test = split_data(rows)
    print(f"  test {len(test)}")
    print(f"[2/4] 加载 adapter")
    tok = AutoTokenizer.from_pretrained(a.adapter, trust_remote_code=True)
    if tok.pad_token is None: tok.pad_token = tok.eos_token
    base = AutoModelForCausalLM.from_pretrained(a.base_model, trust_remote_code=True,
                                                  torch_dtype=torch.float16, device_map="auto")
    model = PeftModel.from_pretrained(base, a.adapter); model.eval()
    print(f"[3/4] 评测")
    wa = eval_model(model, tok, test)
    print(f"  risk_acc={wa['risk_accuracy']:.3f}  action_acc={wa['action_accuracy']:.3f}  parse_fail={wa['parse_fail_rate']:.3f}")
    cal = wa["calibration"]
    print(f"  calibration: correct={cal['risk_conf_mean_correct']} wrong={cal['risk_conf_mean_wrong']}")
    print(f"  latency p50/p95={wa['latency_ms']['p50']}/{wa['latency_ms']['p95']}ms")
    Path(a.output).write_text(json.dumps({"scenario":"log","samples_total":len(rows),
                                           "test_size":len(test),"with_adapter":wa}, ensure_ascii=False, indent=2))
    print(f"  ✅ {a.output}")

if __name__ == "__main__":
    main()
'''
sftp = c.open_sftp()
sftp.put(__file__.replace('\\', '/'), f'/workspace/companion/wait_log_conf_then_bench.py')
# 写 bench_log.py
with sftp.open('/workspace/companion/bench_log.py', 'w') as f:
    f.write(bench_script)
print(f"\n[3/4] bench_log.py 上传 OK")

# 跑 bench
print(f"\n[4/4] 跑 bench_log.py")
bench_cmd = ('cd /workspace/companion && python3 bench_log.py '
             '--data /workspace/data_log_conf.jsonl '
             '--base-model /workspace/models/Qwen3Guard-Gen-0.6B '
             '--adapter /workspace/qwen3guard-log-conf/adapter '
             '--output /workspace/bench_log_conf.json --skip-base 2>&1')
si, so, se = c.exec_command(bench_cmd, timeout=600)
print(so.read().decode(errors='replace')[-1500:])

# 读 bench json
si, so, se = c.exec_command('cat /workspace/bench_log_conf.json 2>&1')
report = so.read().decode(errors='replace')
try:
    j = json.loads(report)
    wa = j.get("with_adapter", {})
    cal = wa.get("calibration", {})
    print(f"\n[bench summary]:")
    print(f"  risk_acc={wa.get('risk_accuracy'):.3f}")
    print(f"  action_acc={wa.get('action_accuracy'):.3f}")
    print(f"  parse_fail={wa.get('parse_fail_rate'):.3f}")
    rc = cal.get('risk_conf_mean_correct') or 0
    rw = cal.get('risk_conf_mean_wrong') or 0
    print(f"  risk_conf correct={rc}  wrong={rw}  delta={rc-rw:.4f}")
    print(f"  latency p50/p95={wa.get('latency_ms',{}).get('p50')}/{wa.get('latency_ms',{}).get('p95')}ms")
except Exception as e:
    print(f"parse err: {e}")
    print(report[:2000])

sftp.close(); c.close()