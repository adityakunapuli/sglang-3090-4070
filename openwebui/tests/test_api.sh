#!/bin/bash
echo "Testing Open WebUI API via Docker network..."

# Create a temporary python script to run inside the network
cat << 'PYEOF' > /tmp/test_api_inner.py
import urllib.request
import json
import ssl
import sys

def test_api():
    url = "http://openwebui:8080/api/models"
    
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Authorization": "Bearer sk-1234"
    })
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            status = response.status
            data = json.loads(response.read().decode())
            print(f"Status Code: {status}")
            print(f"Response: {json.dumps(data, indent=2)[:500]}...")
            if status == 200:
                print("SUCCESS: Connected to Open WebUI API!")
            else:
                print("FAILED: Did not get 200 OK.")
    except urllib.error.HTTPError as e:
        print(f"HTTPError: {e.code} - {e.reason}")
        try:
            print(e.read().decode()[:500])
        except:
            pass
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_api()
PYEOF

docker run --rm --network proxy -v /tmp/test_api_inner.py:/app/test_api.py -w /app python:3.11-slim python test_api.py
