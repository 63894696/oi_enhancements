import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from huggingface_hub import snapshot_download
p = snapshot_download("Qwen/Qwen3Guard-Gen-0.6B", cache_dir=".")
print("downloaded to:", p)