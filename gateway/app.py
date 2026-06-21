import json
import os
import re
import socket
import time
import yaml
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse

try:
    from zoneinfo import ZoneInfo
    PT_TZ = ZoneInfo("America/Los_Angeles")
except ImportError:
    PT_TZ = timezone(timedelta(hours=-8))

def pst_now_str() -> str:
    return datetime.now(PT_TZ).strftime("%Y-%m-%d %H:%M:%S")

class C:
    GRAY = "\033[90m"
    CYAN = "\033[96m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

_dns_cache: dict[str, tuple[str, float]] = {}
_DNS_TTL = 300.0

def resolve_caller(host: str) -> str:
    now = time.time()
    cached = _dns_cache.get(host)
    if cached and (now - cached[1]) < _DNS_TTL:
        return cached[0]
    try:
        name, _, _ = socket.gethostbyaddr(host)
        result = f"{C.BOLD}{C.CYAN}{name}{C.GRAY} ({host})"
        _dns_cache[host] = (result, now)
        return result
    except Exception:
        return f"{C.BOLD}{C.CYAN}{host}"

app = FastAPI()

# Load mapping configurations
CONFIG_PATH = os.getenv("CONFIG_PATH", "config.yaml")
with open(CONFIG_PATH, "r") as f:
    cfg = yaml.safe_load(f)

# API_BASE = cfg.get("api_base", "http://llama-cpp:8080/v1/chat/completions")
API_BASE = cfg.get("api_base", "http://192.168.254.111:8082/v1/chat/completions")
MODEL_MAP = cfg.get("models", {})
client = httpx.AsyncClient(timeout=300.0)

async def get_active_model():
    """Queries llama-swap or llama-cpp for the currently active (ready) model."""
    try:
        # 1. Try querying llama-swap endpoint
        resp = await client.get("http://192.168.254.111:8082/running")
        if resp.status_code == 200:
            data = resp.json()
            for m in data.get("running", []):
                if m.get("state") == "ready":
                    return m.get("model")
    except Exception as e:
        print(f"Error resolving active model from llama-swap: {e}")
        
    try:
        # 2. Fallback to direct llama-cpp v1/models endpoint
        resp = await client.get("http://192.168.254.111:8082/v1/models")
        if resp.status_code == 200:
            data = resp.json()
            models_list = data.get("data", [])
            if models_list:
                return models_list[0].get("id")
    except Exception as e:
        print(f"Error resolving active model from llama-cpp fallback: {e}")

    return None

async def get_upstream_context_length(active_model: str):
    """Dynamically queries the backend to determine the context length of the active model."""
    try:
        # 1. Try querying llama-swap running details and parse cmd
        resp = await client.get("http://192.168.254.111:8082/running")
        if resp.status_code == 200:
            data = resp.json()
            for m in data.get("running", []):
                if m.get("model") == active_model and m.get("state") == "ready":
                    cmd = m.get("cmd", "")
                    match = re.search(r'(?:--ctx-size|-c)\s+(\d+)', cmd)
                    if match:
                        return int(match.group(1))
    except Exception as e:
        print(f"Failed to query context length from llama-swap: {e}")

    try:
        # 2. Try querying llama-cpp props endpoint directly
        resp = await client.get("http://192.168.254.111:8082/props")
        if resp.status_code == 200:
            data = resp.json()
            n_ctx = data.get("default_generation_settings", {}).get("n_ctx")
            if n_ctx:
                return int(n_ctx)
    except Exception as e:
        print(f"Failed to query context length from llama-cpp props: {e}")

    return None

@app.post("/v1/chat/completions")
async def override_and_forward(request: Request):
    start_time = time.time()
    client_host = request.client.host if request.client else "unknown"
    client_port = request.client.port if request.client else 0
    caller_raw = resolve_caller(client_host)
    xff = request.headers.get("x-forwarded-for", "")
    caller = f"{caller_raw}{C.GRAY}:{client_port}{C.RESET}"
    xff_suffix = f" {C.GRAY}x-fwd={xff}{C.RESET}" if xff else ""

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    model_name = body.get("model", "unknown")
    
    if model_name in MODEL_MAP:
        overrides = MODEL_MAP[model_name].copy()

        if overrides.get("model") == "auto":
            active_model = await get_active_model()
            if active_model:
                overrides["model"] = active_model
            else:
                raise HTTPException(status_code=503, detail="No active model available on backend")

        for key in ["max_model_len", "context_length"]:
            overrides.pop(key, None)

        for key, value in overrides.items():
            if isinstance(value, dict) and key in body and isinstance(body[key], dict):
                body[key].update(value)
            else:
                body[key] = value

    if "extra_body" in body and isinstance(body["extra_body"], dict):
        for eb_key, eb_val in body["extra_body"].items():
            if eb_key in body and isinstance(body[eb_key], dict) and isinstance(eb_val, dict):
                body[eb_key].update(eb_val)
            else:
                body[eb_key] = eb_val
        body.pop("extra_body", None)

    if body.get("stream", False) and "stream_options" not in body:
        body["stream_options"] = {"include_usage": True}

    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    req = client.build_request("POST", API_BASE, json=body, headers=headers)
    resp = await client.send(req, stream=True)

    time_to_first_token = None
    usage_data = None

    async def wrapped_stream():
        nonlocal time_to_first_token, usage_data
        async for chunk in resp.aiter_bytes():
            if time_to_first_token is None:
                time_to_first_token = time.time()
            if b'"usage"' in chunk:
                try:
                    raw = chunk.decode("utf-8").strip()
                    if raw.startswith("data: "):
                        raw = raw[6:]
                    if raw != "[DONE]":
                        parsed = json.loads(raw)
                        if parsed.get("usage"):
                            usage_data = parsed["usage"]
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            yield chunk

        elapsed = time.time() - start_time
        ttft = (time_to_first_token - start_time) if time_to_first_token else 0.0
        pt = (usage_data or {}).get("prompt_tokens", 0)
        ct = (usage_data or {}).get("completion_tokens", 0)
        tps = ct / elapsed if elapsed > 0 else 0.0
        pps = pt / ttft if ttft > 0 else 0.0
        print(
            f"{C.GRAY}[{pst_now_str()}]{C.RESET}"
            f" {C.GRAY}caller={C.RESET}{caller}{xff_suffix}"
            f" {C.GRAY}model={C.RESET}{C.BOLD}{C.YELLOW}{body.get('model')}{C.RESET}"
            f" {C.GRAY}tokens={C.RESET}{C.BOLD}{C.GREEN}{pt}{C.GRAY}↗{C.BOLD}{C.GREEN}{ct}{C.RESET}"
            f" {C.GRAY}tok/s={C.RESET}{C.BOLD}{C.GREEN}{tps:.2f}{C.RESET}"
            f" {C.GRAY}pp/s={C.RESET}{C.BOLD}{C.GREEN}{pps:.2f}{C.RESET}"
            f" {C.GRAY}ttft={C.RESET}{C.BOLD}{C.GREEN}{ttft:.2f}s{C.RESET}"
            f" {C.GRAY}total={C.RESET}{C.BOLD}{C.GREEN}{elapsed:.2f}s{C.RESET}"
        )

    if resp.status_code >= 400:
        await resp.aread()
        elapsed = time.time() - start_time
        print(
            f"{C.GRAY}[{pst_now_str()}]{C.RESET}"
            f" {C.GRAY}caller={C.RESET}{caller}{xff_suffix}"
            f" {C.GRAY}model={C.RESET}{C.BOLD}{C.YELLOW}{model_name}{C.RESET}"
            f" {C.BOLD}{C.RED}FAILED{C.RESET}"
            f" {C.GRAY}status={C.RESET}{C.BOLD}{C.RED}{resp.status_code}{C.RESET}"
            f" {C.GRAY}total={C.RESET}{C.BOLD}{C.GREEN}{elapsed:.2f}s{C.RESET}"
        )
        return StreamingResponse(resp.aiter_bytes(), status_code=resp.status_code, headers=dict(resp.headers))

    return StreamingResponse(
        wrapped_stream(),
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() in ["content-type", "cache-control", "connection"]}
    )

@app.get("/v1/models")
async def list_models():
    data = []
    
    # Resolve active model dynamically
    active_model = await get_active_model()
    
    # Resolve upstream context length
    upstream_ctx = None
    if active_model:
        upstream_ctx = await get_upstream_context_length(active_model)
        
    for model_name, model_cfg in MODEL_MAP.items():
        # Fallbacks defined in config.yaml
        config_max_len = model_cfg.get("max_model_len", 8192)
        config_ctx_len = model_cfg.get("context_length", config_max_len)
        
        max_model_len = config_max_len
        context_length = config_ctx_len
        
        # If upstream context length is found, cap the limits dynamically
        if upstream_ctx:
            max_model_len = min(upstream_ctx, config_max_len)
            context_length = min(upstream_ctx, config_ctx_len)
            
        data.append({
            "id": model_name,
            "object": "model",
            "created": 1686935002,
            "owned_by": "gateway",
            "max_model_len": max_model_len,
            "context_length": context_length,
        })
    return {"object": "list", "data": data}

@app.get("/health")
async def health():
    return {"status": "healthy"}
