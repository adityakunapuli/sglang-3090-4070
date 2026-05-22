# Plex Service Management & Recovery

This directory contains the Docker configuration for the Plex Media Server.

## Important: The "Hopeless" Recovery (Nuke & Restore)

If the Plex service becomes "hopeless" (broken identity, authentication loops, or severe plugin crashes), follow this procedure:

1.  **Backup/Nuke:** Stop the container and rename the `Application Support/Plex Media Server` directory in the cache to a backup.
2.  **Selective Restore:** Create a fresh directory and move ONLY the `Metadata` (posters), `Media` (bundles), and `Codecs` folders from the backup to the new directory. **Do NOT move the `Databases` folder** if you want a truly clean identity (though you will lose watch history).
3.  **The Claim Token:**
    *   Go to [plex.tv/claim](https://www.plex.tv/claim) and get a fresh token.
    *   Add `PLEX_CLAIM: <token>` to the `environment` section of `docker-compose.yml`.
    *   `docker compose up -d`.
    *   Once the logs show `**** Server already claimed ****`, remove the token from the compose file.

## Critical Network Settings (`Preferences.xml`)

Plex in Docker often suffers from "Docker Blindness," advertising internal `172.x.x.x` IPs instead of the host IP. This breaks Mobile and TV apps.

If apps can't find the server, ensure `Preferences.xml` contains these surgically injected attributes:

*   **`customConnections`**: `http://192.168.254.111:32400` (Forces the cloud to point to the host IP).
*   **`lanNetworks`**: `192.168.254.0/24` (Ensures local devices aren't treated as 'Remote/WAN').
*   **`allowedNetworks`**: Includes your local subnet to prevent "Not Authorized" lockouts on fresh installs.
*   **`secureConnections`**: Set to `1` (Preferred) for app compatibility.

## Files in this Directory
*   `docker-compose.yml`: Service definition.
*   `Preferences.xml.working_backup`: A known-good snapshot of the identity and network settings as of May 2026.
*   `README.md`: This file.

---
*Note: A working backup of Preferences.xml is stored here as `Preferences.xml.working_backup`. If you lose your config, you can use it as a template, but remember to update the `PlexOnlineToken` if you change accounts.*
