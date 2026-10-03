#!/usr/bin/env python3
"""Spec-decode acceptance probe: is MTP actually proposing acceptable drafts?

Sends a deterministic text generation, then reads avg_spec_accept_length from
the server's internal state. Fixed means >= ~2.5 at 3/1/4; the known-bad PP
state reports exactly 1.00. Run after any boot of the lab container.

Usage: spec_probe.py [url=http://<lab-ip>:8082]
"""
import json, sys, time, urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8082"


def post(path, payload, timeout=300):
    req = urllib.request.Request(
        URL + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


post("/v1/chat/completions", {
    "model": "default",
    "messages": [{"role": "user",
                  "content": "Count from 1 to 200, comma separated, nothing else."}],
    "max_tokens": 600, "temperature": 0, "enable_thinking": False,
})
time.sleep(1)
info = json.loads(urllib.request.urlopen(URL + "/get_server_info", timeout=10).read())
acc = None
for st in info.get("internal_states", []):
    if st.get("avg_spec_accept_length") is not None:
        acc = st["avg_spec_accept_length"]
        break
out = {"avg_accept_length": acc,
       "spec_algorithm": info.get("speculative_algorithm", info.get("server_args", {}).get("speculative_algorithm"))}
print(json.dumps(out))
sys.exit(0 if (acc or 0) >= 2.0 else 1)
