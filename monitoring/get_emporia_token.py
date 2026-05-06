import json
import pyemvue

# Read credentials from vuegraf.json
with open('vuegraf.json', 'r') as f:
    config = json.load(f)
    
email = config['accounts'][0]['email']
password = config['accounts'][0]['password']

vue = pyemvue.PyEmVue()
try:
    print("Logging into Emporia...")
    vue.login(username=email, password=password, token_storage_file='keys.json')
    
    # We need the local API token (not just the cloud token)
    # The local API token isn't natively "given" via simple pyemvue methods unless we check the device
    devices = vue.get_devices()
    for device in devices:
        print(f"Device: {device.device_name}, Model: {device.model}, Serial: {device.device_gid}")
        # Not all devices have local API tokens (e.g. only Vue 2/3)
        # But we can try to extract info from it
        
    print("\nCheck keys.json for cloud tokens.")
    
except Exception as e:
    print(f"Error logging in: {e}")
