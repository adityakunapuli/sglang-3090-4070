import json,random,sys,time,urllib.request
URL="http://127.0.0.1:8082"; MODEL="default"
target=int(sys.argv[1]) if len(sys.argv)>1 else 250000
rng=random.Random(7)
lines=[]
for i in range(target//9):
    lines.append(f"Entry {i}: the archived value of record {rng.randint(0,10**8)} was noted.")
needle_idx=rng.randint(0,len(lines)-50)
secret=f"SECRET-{rng.randint(10**5,10**6)}"
lines.insert(needle_idx, f"Critical note: the access code is {secret}. Remember it exactly.")
body="\n".join(lines)
prompt=body+"\n\nWhat is the access code? Reply with only the code."
req=urllib.request.Request(URL+"/v1/chat/completions",
  data=json.dumps({"model":MODEL,"messages":[{"role":"user","content":prompt}],
  "max_tokens":300,"temperature":0,"enable_thinking":False}).encode(),
  headers={"Content-Type":"application/json"})
t0=time.time()
with urllib.request.urlopen(req,timeout=1800) as r: d=json.loads(r.read())
el=time.time()-t0
txt=(d["choices"][0]["message"].get("content") or "")
print("prompt_tokens :",d["usage"]["prompt_tokens"])
print("want          :",secret)
print("got           :",repr(txt[:120]))
print("CORRECT       :",secret in txt)
print("wall_s        :",round(el,1))
print("finish        :",d["choices"][0]["finish_reason"])
