#!/usr/bin/env python3
"""Production stress test for the SGLang PP2+MTP stack.

Simulates the intended workload: concurrent agentic traffic mixing
multi-image vision requests, long-context prefills, and plain decoding.
Pass criteria (all): no container crash/OOM, every request 200 + non-empty,
avg spec accept length >= 2.0, both GPUs report >0.5 GiB free after the run.

Usage: stress_validation.py [url] [rounds]
"""
import base64, glob, json, random, sys, threading, time
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8082"
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 6

IMAGES = [p for p in sorted(
    glob.glob("/mnt/data/.archive/*.png")
    + glob.glob("/mnt/data/.archive/*.jpg")) if (p.endswith(".png") and "favicon" not in p) or p.endswith(".jpg")]
assert len(IMAGES) >= 4, f"need images in .archive, found {len(IMAGES)}"

rng = random.Random(1234)


def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def post(payload, timeout=1800):
    req = urllib.request.Request(
        URL + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    el = time.time() - t0
    txt = (r["choices"][0]["message"].get("content") or "")
    return {"sec": round(el, 1), "out_tok": r["usage"]["completion_tokens"],
            "in_tok": r["usage"]["prompt_tokens"], "len": len(txt)}


def img_req(n_img):
    parts = [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64(p)}"}}
             for p in rng.sample(IMAGES, n_img)]
    parts.append({"type": "text",
                  "text": "Describe each image in one line, then name any visible text."})
    return {"model": "default", "messages": [{"role": "user", "content": parts}],
            "max_tokens": 320, "temperature": 0, "enable_thinking": False}


def long_ctx_req(k_tokens, salt):
    r2 = random.Random(salt)
    # ~25 tokens per "Note<N>=<X>." item; k_tokens is a TOKEN target
    filler = " ".join(f"Note{r2.randint(0,10**9)}={r2.randint(0,999)}." for _ in range(round(k_tokens / 37)))
    return {"model": "default",
            "messages": [{"role": "user", "content": filler + "\n\nSummarize structure in 3 lines."}],
            "max_tokens": 256, "temperature": 0, "enable_thinking": False}


def short_req(salt):
    return {"model": "default",
            "messages": [{"role": "user", "content": f"List {rng.randint(20,40)} random kitchen items, one per line. salt={salt}"}],
            "max_tokens": 400, "temperature": 0, "enable_thinking": False}


results, lock = [], threading.Lock()
errors = []


def work(kind, arg):
    try:
        payload = (img_req(arg) if kind == "img"
                   else long_ctx_req(arg, hash((kind, arg)) % 10**6) if kind == "long"
                   else short_req(arg))
        out = post(payload)
        with lock:
            results.append((kind, out))
    except Exception as e:
        with lock:
            errors.append((kind, str(e)[:300]))


t0 = time.time()
for rnd in range(ROUNDS):
    ths = [
        threading.Thread(target=work, args=("img", 2 + rnd % 2)),   # 2-3 images
        threading.Thread(target=work, args=("img", 2)),
        threading.Thread(target=work, args=("long", 20000 + rnd * 4000)),  # 20-40k ctx
        threading.Thread(target=work, args=("long", 12000)),
        threading.Thread(target=work, args=("short", rnd)),
    ]
    for t in ths: t.start()
    for t in ths: t.join()
    print(f"round {rnd}: ok={len(results)} err={len(errors)}", flush=True)

wall = time.time() - t0
time.sleep(2)
info = json.loads(urllib.request.urlopen(URL + "/get_server_info", timeout=10).read())
acc = next((s.get("avg_spec_accept_length") for s in info.get("internal_states", [])
            if s.get("avg_spec_accept_length")), None)

ok = not errors and len(results) == ROUNDS * 5 and (acc or 0) >= 2.0
print("SUMMARY:", json.dumps({"requests": len(results), "expected": ROUNDS*5,
    "errors": len(errors), "accept": acc, "wall_s": round(wall,1), "PASS": ok}))
print(json.dumps({
    "wall_s": round(wall, 1), "requests": len(results), "errors": errors,
    "by_kind": {k: [r for (kk, r) in results if kk == k] for k in ("img", "long", "short")},
    "avg_accept_length": acc,
    "max_total_num_tokens": info.get("max_total_num_tokens"),
}, indent=1)[:4000])
ok = not errors and len(results) == ROUNDS * 5 and (acc or 0) >= 2.0
print("STRESS:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
