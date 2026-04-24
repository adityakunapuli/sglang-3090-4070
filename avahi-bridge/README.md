# Avahi mDNS Bridge

This service automatically registers Docker container names as flat mDNS hostnames (e.g., `container_name.local`) on your network.

## Why is this needed?
Windows (and some other OSs) have native mDNS resolvers that often fail to resolve multi-part subdomains (like `service.home.local`). However, they resolve "flat" names (like `service.local`) perfectly.

Since manual router entries are tedious, this bridge automates the process by "shouting" the container names onto the network.

## How it works
1. **Docker Monitor**: A Python script (`bridge.py`) listens to the Docker socket for container `start` and `stop` events.
2. **Avahi Integration**: When a container starts, the bridge runs `avahi-publish-address` to map the container name to the host's IP address.
3. **Automatic Cleanup**: When a container stops, the mDNS record is automatically withdrawn.

## Current Setup
- **LiteLLM**: Accessible at `http://litellm.local`
- **Llama Swap**: Accessible at `http://llama-swap.local`
- **Home Assistant**: Accessible at `http://homeassistant.local`
- **UniFi**: Accessible at `http://unifi.local`

## Deployment
The bridge runs in `network_mode: host` and requires access to:
- `/var/run/docker.sock`
- `/var/run/dbus/system_bus_socket` (to talk to the host's Avahi daemon)

To start:
```bash
docker compose up -d --build
```
