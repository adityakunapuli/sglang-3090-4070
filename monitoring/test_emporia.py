import json
import urllib.request

try:
    with open('keys.json') as f:
        keys = json.load(f)
    
    token = keys.get('id_token')
    serial = '339373'
    
    req = urllib.request.Request(
        'http://192.168.254.25/api/realTimeData',
        headers={'token': token, 'serial': serial}
    )
    
    with urllib.request.urlopen(req, timeout=5) as response:
        print(f"Status (id_token): {response.status}")
        print(response.read().decode('utf-8'))
        
except Exception as e:
    print(f"Failed with id_token: {e}")
