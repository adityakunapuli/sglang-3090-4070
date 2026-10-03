#!/usr/bin/env python3
"""
Benchmark harness for SGLang PP2 on Qwen3.8-27B (hybrid GDN + vision + MTP).

Methodology notes (these matter -- two traps found in the literature):
 1) NEVER compute decode tok/s from a single server log line. SGLang's
    "Decode batch" gen-throughput = tokens / wall time since the PREVIOUS log
    line. The first line after a long prefill charges the whole prefill to the
    decode denominator, so a 50k prefill (~15s) reports ~6-13 tok/s for a
    request that is actually decoding fine. Always discard the first decode
    line per request, or compute client-side as (wall - TTFT).
 2) Temperature 0 does NOT give byte-identical output across runs on this
    stack (batching / atomics). Correctness is judged on the KEY ANSWER, never
    on full-text diff.

Usage: bench.py --ctx 32768 --label smoke
"""
import argparse, base64, io, json, os, statistics, sys, threading, time
import urllib.request, urllib.error

URL = os.environ.get("SGL_URL", "http://127.0.0.1:8082")
MODEL = os.environ.get("SGL_MODEL", "default")


def post(path, payload, timeout=600):
    req = urllib.request.Request(
        URL + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_healthy(timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(URL + "/health", timeout=5) as r:
                if r.status == 200:
                    return time.time() - t0
        except Exception:
            pass
        time.sleep(5)
    raise SystemExit("TIMEOUT waiting for /health")


def make_image(w=512, h=512, seed=0):
    """Tiny PNG with a solid colour + border. Real image bytes for the VL path."""
    import struct, zlib

    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    raw = b""
    for y in range(h):
        raw += b"\x00"
        for x in range(w):
            edge = x < 8 or y < 8 or x >= w - 8 or y >= h - 8
            raw += bytes((255, 0, 0) if edge else ((x * 255) // w, (y * 255) // h, 128))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


# ---- correctness gates -----------------------------------------------------
# NOTE: expected values verified by hand. 2718*4319 = 11,739,042 (the model got
# this right; an earlier revision of this file asserted 11,735,042 and produced
# a false FAIL). Also note max_tokens must exceed reasoning length: this model
# emits ~257 reasoning tokens even with enable_thinking=false unless the server
# sets default_chat_template_kwargs.
GATES = [
    ("arith", "What is 2718 * 4319? Reply with only the number.", "11739042"),
    ("logic", "If all Bloops are Razzies and all Razzies are Lazzies, is every Bloop a Lazzie? Reply only yes or no.", "yes"),
    ("code", "What does this print? `print(sum(range(10)))` Reply with only the number.", "45"),
]


def run_gate(name, prompt, expect, max_tokens=600):
    t0 = time.time()
    try:
        r = post("/v1/chat/completions", {
            "model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0, "enable_thinking": False,
        })
    except Exception as e:
        return {"gate": name, "ok": False, "err": str(e)[:200]}
    txt = (r["choices"][0]["message"].get("content") or "").strip()
    return {
        "gate": name, "ok": expect.lower() in txt.lower()[:120],
        "want": expect, "got": txt[:120].replace("\n", " "),
        "ttft": round(r.get("usage", {}).get("_ttft", 0), 3),
        "sec": round(time.time() - t0, 2),
    }


def decode_test(prompt_tok_target, max_tokens=256, label="", salt=0):
    """Client-side decode tps: (wall - TTFT) isolates decode from prefill.

    The filler is salted per call. An unsalted constant prefix makes the second
    and later runs hit the radix prefix cache, which silently inflates apparent
    prefill throughput (observed: a "60k" test reporting 43,000 tok/s because it
    was really a cache hit on the previous run's identical filler).
    """
    import random
    rng = random.Random(1000 + salt)
    # Each fact is ~12 tokens, so scale the fact count down accordingly.
    n_facts = max(20, prompt_tok_target // 12)
    filler = " ".join(
        f"Fact{rng.randint(0, 10**9)} is {rng.randint(0, 999)}."
        for _ in range(n_facts)
    )
    prompt = filler + "\n\nCount to twenty, one number per line."
    t0 = time.time()
    first = None
    n = 0
    req = urllib.request.Request(
        URL + "/v1/chat/completions",
        data=json.dumps({
            "model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0, "enable_thinking": False,
            "stream": True,
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=900) as resp:
        for line in resp:
            line = line.decode(errors="ignore").strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                break
            try:
                d = json.loads(body)
            except Exception:
                continue
            delta = d["choices"][0].get("delta", {}).get("content")
            if delta:
                n += 1
                if first is None:
                    first = time.time()
    total = time.time() - t0
    ttft = (first - t0) if first else total
    dec = n / (total - ttft) if total > ttft else 0.0
    usage = None
    return {
        "label": label, "prompt_tok_approx": prompt_tok_target, "gen": n,
        "ttft_s": round(ttft, 3), "total_s": round(total, 2),
        "decode_tps": round(dec, 1),
        "prefill_tok_per_s": round(prompt_tok_target / ttft, 1) if ttft > 0 else None,
    }


def concurrency_test(n, prompt_words=600, max_tokens=128, salt=0):
    import random
    rng = random.Random(5000 + salt)
    filler = " ".join(f"Note{rng.randint(0,10**9)}={rng.randint(0,999)}." for _ in range(prompt_words))
    msg = [{"role": "user", "content": filler + "\n\nName three primary colours."}]
    out = [None] * n
    barrier = threading.Barrier(n)

    def one(i):
        try:
            barrier.wait(timeout=120)
            t0 = time.time()
            r = post("/v1/chat/completions", {
                "model": MODEL, "messages": msg, "max_tokens": max_tokens,
                "temperature": 0, "enable_thinking": False,
            })
            out[i] = r["usage"]["completion_tokens"] / (time.time() - t0)
        except Exception as e:
            out[i] = {"err": str(e)[:150]}

    ths = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    t_start = time.time()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    wall = time.time() - t_start
    ok = [v for v in out if isinstance(v, float)]
    return {
        "concurrency": n, "ok": len(ok), "failed": n - len(ok),
        "wall_s": round(wall, 2),
        "aggregate_tps": round(sum(ok), 1) if ok else 0.0,
    }


def vision_test():
    b64 = make_image()
    try:
        r = post("/v1/chat/completions", {
            "model": MODEL, "max_tokens": 80, "temperature": 0, "enable_thinking": False,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}},
                {"type": "text", "text": "What colour dominates the border of this image? One word."},
            ]}],
        })
        txt = (r["choices"][0]["message"].get("content") or "")
        return {"vision_ok": True, "got": txt[:100].replace("\n", " "),
                "prompt_tokens": r.get("usage", {}).get("prompt_tokens")}
    except urllib.error.HTTPError as e:
        return {"vision_ok": False, "err": f"HTTP {e.code}: {e.read()[:200].decode(errors='ignore')}"}
    except Exception as e:
        return {"vision_ok": False, "err": str(e)[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--ctx", type=int, default=0, help="skip if server ctx differs")
    ap.add_argument("--skip-vision", action="store_true")
    ap.add_argument("--skip-conc", action="store_true")
    ap.add_argument("--conc-levels", default="1,4,8")
    ap.add_argument("--decode-targets", default="2000,20000")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    res = {"label": args.label, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        with urllib.request.urlopen(URL + "/get_server_info", timeout=10) as r:
            info = json.loads(r.read())
        res["server"] = {
            "context_length": info.get("server_args", {}).get("context_length"),
            "pp_size": info.get("server_args", {}).get("pp_size"),
            "max_running_requests": info.get("server_args", {}).get("max_running_requests"),
            "max_mamba_cache_size": info.get("max_mamba_cache_size"),
            "max_total_num_tokens": info.get("max_total_num_tokens"),
            "kv_cache_size": info.get("kv_cache_size"),
        }
    except Exception as e:
        res["server"] = {"err": str(e)[:200]}

    res["gates"] = [run_gate(*g) for g in GATES]
    if not args.skip_vision:
        res["vision"] = vision_test()
    res["decode"] = [decode_test(int(t), label=f"~{t}tok", salt=i)
                     for i, t in enumerate(args.decode_targets.split(",")) if t.strip()]
    if not args.skip_conc:
        levels = [x for x in args.conc_levels.split(",") if x.strip()]
        res["concurrency"] = [concurrency_test(int(c), salt=i)
                              for i, c in enumerate(levels)]

    txt = json.dumps(res, indent=2)
    print(txt)
    if args.out:
        with open(args.out, "w") as f:
            f.write(txt + "\n")
    ok = all(g.get("ok") for g in res["gates"])
    print("\n=== GATES:", "PASS" if ok else "FAIL", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())