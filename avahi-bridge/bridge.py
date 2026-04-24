import docker
import subprocess
import signal
import sys
import socket
import time

client = docker.from_env()
processes = {}

def get_host_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def register_container(container):
    name = container.name
    # Don't register the bridge itself or containers with dots (handled by Cosmos)
    if name == "avahi-bridge" or "." in name:
        return

    hostname = f"{name}.local"
    ip = get_host_ip()
    
    if container.id in processes:
        return

    # Check if already registered by someone else
    try:
        # Very quick check
        subprocess.check_call(['avahi-resolve', '-n', hostname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"Skipping {hostname} - already exists")
        return
    except subprocess.CalledProcessError:
        pass

    print(f"Registering {hostname} -> {ip}")
    # Use -a to publish an address record
    proc = subprocess.Popen(['avahi-publish-address', '-a', hostname, ip])
    processes[container.id] = proc

def unregister_container(container_id):
    if container_id in processes:
        print(f"Unregistering container {container_id[:12]}")
        processes[container_id].terminate()
        del processes[container_id]

def handler(signum, frame):
    print("Shutting down...")
    for proc in processes.values():
        proc.terminate()
    sys.exit(0)

signal.signal(signal.SIGTERM, handler)
signal.signal(signal.SIGINT, handler)

# Initial registration
for container in client.containers.list():
    register_container(container)

# Listen for events
print("Monitoring Docker events...")
for event in client.events(decode=True):
    action = event.get('status')
    container_id = event.get('id')
    
    if action == 'start':
        try:
            container = client.containers.get(container_id)
            register_container(container)
        except Exception:
            pass
    elif action in ['die', 'destroy', 'stop']:
        unregister_container(container_id)
