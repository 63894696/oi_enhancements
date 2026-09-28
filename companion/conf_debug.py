#!/usr/bin/env python3
"""debug forward 输出"""
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

base = AutoModelForCausalLM.from_pretrained(
    "/workspace/models/Qwen3Guard-Gen-0.6B",
    trust_remote_code=True, torch_dtype=torch.float16, device_map="auto")
tok = AutoTokenizer.from_pretrained(
    "/workspace/qwen3guard-disk-cleanup/adapter", trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
model = PeftModel.from_pretrained(
    base, "/workspace/qwen3guard-disk-cleanup/adapter")
model.eval()

prompt = "user\n文件路径: Windows\\Temp\\abc.log\n扩展名: .log\n大小: 1KB\n年龄: 30 天\n问: 这个 Windows 系统文件是否可以安全清理?\nassistant\n"
target = "Safety: Low\nJailbreak: No\nAction: Delete\n"

p_ids = tok(prompt, return_tensors="pt", add_special_tokens=False)["input_ids"].to("cuda")
t_ids = tok(target, return_tensors="pt", add_special_tokens=False)["input_ids"].to("cuda")
print("prompt shape:", p_ids.shape)
print("target shape:", t_ids.shape)
print("prompt last 5 tokens:", p_ids[0, -5:].tolist())
print("target first 5 tokens:", t_ids[0, :5].tolist())

full = torch.cat([p_ids, t_ids], dim=1)
print("full shape:", full.shape)

with torch.inference_mode():
    out = model(full)
logits = out.logits
print("logits shape:", logits.shape)

p_len = p_ids.shape[1]
# 在 p_len 位置预测 p_len+1 的 token(即 target 第一个 token)
pred_at_p_len = torch.softmax(logits[0, p_len - 1, :], dim=-1)  # 模型用前 p_len 预测 p_len+1
target_first = t_ids[0, 0].item()
print(f"position {p_len-1} predicts token {target_first}: prob={pred_at_p_len[target_first]:.4f}")

# top 5 at p_len-1
top5 = torch.topk(pred_at_p_len, 5)
print("top 5 at p_len-1:", [(t.item(), p.item()) for t, p in zip(top5.indices, top5.values)])

# 现在试 confidence_teacher.py 的逻辑:pos = p_len, logit = logits[0, p_len, :]
# 它预测 p_len+1 那个位置
pred_at_p_len_a = torch.softmax(logits[0, p_len, :], dim=-1)
print(f"position {p_len} predicts token {target_first}: prob={pred_at_p_len_a[target_first]:.4f}")
top5a = torch.topk(pred_at_p_len_a, 5)
print("top 5 at p_len:", [(t.item(), p.item()) for t, p in zip(top5a.indices, top5a.values)])