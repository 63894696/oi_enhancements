"""llama_local_server — OpenAI-compatible server for MiniCPM-V via llama-cpp-python

API endpoint: http://127.0.0.1:8099/v1/chat/completions
Health check: http://127.0.0.1:8099/health

Usage:
    python llama_local_server.py --model models/MiniCPM-V-4_6-Q4_K_M.gguf --mmproj models/mmproj-model-f16.gguf

Dependencies: llama-cpp-python, fastapi, uvicorn, pydantic
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("llama_local_server")

# Global state
llm: Any = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "minicpm-v4.6"
    messages: List[ChatMessage]
    max_tokens: Optional[int] = Field(default=256, ge=1, le=4096)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    stream: bool = False


app = FastAPI(title="llama-local-server", version="1.0")


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": llm is not None}


@app.post("/v1/chat/completions")
def chat_completion(req: ChatCompletionRequest):
    global llm
    if llm is None:
        raise HTTPException(503, "Model not loaded. Start server with --model and --mmproj")

    try:
        messages_dicts = [
            {"role": m.role, "content": m.content}
            for m in req.messages
        ]

        result = llm.create_chat_completion(
            messages=messages_dicts,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            stream=False,
        )

        # Normalize response format to match VPS llama-server
        choice = result.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content", "")
        reasoning = msg.get("reasoning_content", "")

        # Extract usage info
        usage = result.get("usage", {})

        return {
            "choices": [
                {
                    "finish_reason": choice.get("finish_reason", "stop"),
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "reasoning_content": reasoning,
                    },
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }
    except Exception as e:
        log.error("Generation failed: %s", e, exc_info=True)
        raise HTTPException(500, str(e))


def load_model(model_path: str, mmproj_path: str, n_ctx: int = 1024, n_gpu_layers: int = 0):
    """Load MiniCPM-V model via llama-cpp-python with mtmd support."""
    import llama_cpp

    log.info("Loading model: %s", model_path)
    log.info("Loading mmproj: %s", mmproj_path)

    global llm
    llm = llama_cpp.Llama(
        model_path=str(model_path),
        mmproj=str(mmproj_path),
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        n_threads=4,
        n_threads_batch=4,
        verbose=False,
    )
    log.info("Model loaded successfully")


def main():
    parser = argparse.ArgumentParser(description="MiniCPM-V local server (OpenAI-compatible)")
    parser.add_argument("--port", type=int, default=8099, help="HTTP port (default: 8099)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--model", required=True, help="Path to MiniCPM-V-4_6-Q4_K_M.gguf")
    parser.add_argument("--mmproj", required=True, help="Path to mmproj-model-f16.gguf")
    parser.add_argument("--n-ctx", type=int, default=1024, help="Context size (default: 1024)")
    parser.add_argument("--n-gpu-layers", type=int, default=0,
                        help="GPU offload layers (0=CPU, >0=DirectML if supported)")
    args = parser.parse_args()

    # Validate files
    if not Path(args.model).exists():
        log.error("Model file not found: %s", args.model)
        sys.exit(1)
    if not Path(args.mmproj).exists():
        log.error("mmproj file not found: %s", args.mmproj)
        sys.exit(1)

    # Load model
    load_model(args.model, args.mmproj, args.n_ctx, args.n_gpu_layers)

    # Start server
    log.info("Starting server on %s:%d", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
