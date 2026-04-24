import urllib.request
import json
import argparse
import sys
import ssl

def print_result(name, url, success, info=""):
    status = "SUCCESS" if success else "FAILED"
    print(f"[{status}] {name} ({url})")
    if info:
        print(f"    -> {info}")

def test_health(base_url, use_ssl):
    url = f"{base_url}/health"
    ctx = ssl.create_default_context() if use_ssl else ssl._create_unverified_context()
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                print_result("Health Check", url, True, f"Status payload: {data}")
            else:
                print_result("Health Check", url, False, f"Status code: {response.status}")
    except Exception as e:
        print_result("Health Check", url, False, str(e))

def test_models(base_url, token, use_ssl):
    url = f"{base_url}/api/models"
    ctx = ssl.create_default_context() if use_ssl else ssl._create_unverified_context()
    
    headers = {
        "Accept": "application/json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
        
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                count = len(data.get("data", []))
                print_result("Models API", url, True, f"Successfully retrieved {count} models via Open WebUI")
            else:
                print_result("Models API", url, False, f"Status code: {response.status}")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print_result("Models API", url, False, "HTTP 401: Unauthorized. Please provide a valid API token generated from your Open WebUI account settings using the --token flag.")
        else:
            print_result("Models API", url, False, f"HTTP {e.code}: {e.reason}")
    except Exception as e:
        print_result("Models API", url, False, str(e))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Open WebUI API Connections")
    parser.add_argument("--url", default="http://openwebui.local", help="The Base URL for Open WebUI (e.g. http://openwebui.local)")
    parser.add_argument("--token", default="", help="Your Open WebUI API JWT Token (generate this from your User Settings -> API in Open WebUI)")
    
    args = parser.parse_args()
    
    print("=======================================")
    print("    Open WebUI API Test Harness        ")
    print("=======================================")
    
    use_ssl = args.url.startswith("https")
    
    test_health(args.url, use_ssl)
    print("-" * 39)
    test_models(args.url, args.token, use_ssl)
    
    if not args.token:
        print("\nNote: The Models API test may fail without an authentication token.")
        print("To authenticate, run: python test_api.py --token \"YOUR_JWT_TOKEN\"")
