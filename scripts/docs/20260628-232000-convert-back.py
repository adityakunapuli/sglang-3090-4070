#!/usr/bin/env python3
"""
Script to fix the corrupted services.yaml file by restoring properly formatted YAML.
"""

import os

ROOT_DIR = '/mnt/data/docker'
CONFIG_PATH = os.path.join(ROOT_DIR, 'homepage/config/services.yaml')

# Original properly formatted content (simplified)
ORIGINAL_CONTENT = """#- Home Ops:
#    - Home Assistant:
#        icon: home-assistant.png
#        href: http://192.168.254.22:8123
#        ping: http://192.168.254.22:8123
#        widget:
#          type: homeassistant
#          url: http://192.168.254.22:8123
#          key: {{HOMEPAGE_VAR_HA_TOKEN}}
#    - Unifi Controller:
#        icon: unifi.png
#        href: https://192.168.254.22:8443
#        ping: https://192.168.254.22:8443
#        username: admin
#        password: {{HOMEPAGE_VAR_UNIFI_PASSWORD}}
#        tls-skip-verify: true
#    - AdGuard Home:
#        icon: adguard-home.png
#        href: http://192.168.254.111:3001
#        widget:
#          type: adguard
#          url: http://adguardhome:80
- Home Ops:
    - Frigate:
        icon: frigate.png
        href: http://192.168.254.111:5000
        widget:
          type: frigate
          url: http://frigate:5000
    - Unifi Controller:
        icon: unifi.png
        href: https://192.168.254.22:8443
        ping: https://192.168.254.22:8443
        username: admin
        password: {{HOMEPAGE_VAR_UNIFI_PASSWORD}}
        tls-skip-verify: true

#- AI:
#    - OpenClaw:
#        icon: openclaw.png
#        href: http://192.168.254.111:18789

#- Infrastructure:
#    - Grafana:
#        icon: grafana.png
#        href: http://192.168.254.111:3002
#        widget:
#          type: grafana
#          url: http://192.168.254.111:3002
#          showVersion: false

#    - Immich:
#        icon: immich.png
#        href: http://192.168.254.111:2283
#        widget:
#          type: immich
#          url: http://immich_server:2283
#          key: {{HOMEPAGE_VAR_IMMICH_API_KEY}}
#          version: 2
#    - Journiv:
#        icon: mdi-notebook-outline
#        href: http://192.168.254.111:8000
#

#- Paperless Stack:
#    - Paperless:
#        icon: paperless.png
#        href: http://192.168.254.111:8010
#        widget:
#          type: paperlessngx
#          url: http://paperless_web:8000
#          key: {{HOMEPAGE_VAR_PAPERLESS_TOKEN}}
#    - Paperless AI:
#        icon: paperless.png
#        href: http://192.168.254.111:8001
#    - Paperless GPT:
#        icon: paperless.png
#        href: http://192.168.254.111:8002
#

#- Media Automation:
#    - Radarr:
#        icon: radarr.png
#        href: http://192.168.254.111:7878
#        widget:
#          type: radarr
#          url: http://radarr:7878
#          key: {{HOMEPAGE_VAR_RADARR_API_KEY}}
#    - Sonarr:
#        icon: sonarr.png
#        href: http://192.168.254.111:8989
#        widget:
#          type: sonarr
#          url: http://sonarr:8989
#          key: {{HOMEPAGE_VAR_SONARR_API_KEY}}
#    - Prowlarr:
#        icon: prowlarr.png
#        href: http://192.168.254.111:9696
#        widget:
#          type: prowlarr
#          url: http://prowlarr:9696
#          key: {{HOMEPAGE_VAR_PROWLARR_API_KEY}}
#    - Deluge:
#        icon: deluge.png
#        href: http://192.168.254.111:8112
#        widget:
#          type: deluge
#          url: http://delugevpn:8112
#          password: deluge
#    - Flaresolverr:
#        icon: flaresolverr.png
#        href: http://192.168.254.111:8191
#    - Plex:
#        icon: plex.png
#        href: http://192.168.254.111:32400/web
#        widget:
#          type: plex
#          url: http://plex:32400
#          key: {{HOMEPAGE_VAR_PLEX_KEY}}
#    - Audiobookshelf:
#        icon: audiobookshelf.png
#        href: http://192.168.254.111:13378
#        widget:
#          type: audiobookshelf
#          url: http://audiobookshelf:80
#          key: {{HOMEPAGE_VAR_AUDIOBOOKSHELF_API_KEY}}
#
- AI:
    - Open WebUI:
        icon: openwebui.png
        href: http://192.168.254.111:3030
    - Hermes:
        icon: hermesagent.png
        href: http://192.168.254.111:9119
    - Hermes WebUI:
        icon: hermesagent.png
        href: http://192.168.254.111:8787
    - QwenPaw:
        icon: qwen.png
        href: http://192.168.254.111:8088
    - OpenCode:
        icon: opencode.png
        href: http://192.168.254.111:4096
#

#- Infrastructure:
#    - Portainer:
#        icon: portainer.png
#        href: https://192.168.254.111:9443
#        widget:
#          type: portainer
#          url: https://portainer:9443
#          key: {{HOMEPAGE_VAR_PORTAINER_API_KEY}}
#          env: 3
#    - Traefik:
#        icon: traefik.png
#        href: http://192.168.254.111:8081
#        widget:
#          type: traefik
#          url: http://traefik:8080
#    - WUD:
#        icon: whats-up-docker.png
#        href: http://192.168.254.111:3000
#        widget:
#          type: wud
#          url: http://wud:3000
#    - Scrutiny:
#        icon: scrutiny.png
#        href: http://192.168.254.111:8080
#        widget:
#          type: scrutiny
#          url: http://scrutiny:8080
#    - Cosmos:
#        icon: cosmos.svg
#        href: http://192.168.254.111
#

#- System:
#    - OpenRGB:
#        icon: openrgb.png
#        href: http://192.168.254.111:15800
#    - CoolerControl:
#        icon: coolercontrol.svg
#        href: http://192.168.254.111:11987

"""

def restore_services_yaml():
    """Restore the services.yaml file with proper formatting."""
    print("=== Restoring services.yaml ===")
    
    # Clean and restore the file
    with open(CONFIG_PATH, 'w') as f:
        f.write(ORIGINAL_CONTENT)
    
    print(f"Restored {CONFIG_PATH}")
    print(f"File size: {len(ORIGINAL_CONTENT)} bytes")
    
    # Verify the structure
    with open(CONFIG_PATH, 'r') as f:
        lines = f.readlines()
    
    total_lines = len(lines)
    non_comment_lines = sum(1 for line in lines if line.strip() and not line.strip().startswith('#'))
    
    print(f"Total lines: {total_lines}")
    print(f"Non-comment lines: {non_comment_lines}")
    
    return True

def main():
    restore_services_yaml()
    print("\n=== Services.yaml restoration complete ===")

if __name__ == '__main__':
    main()
