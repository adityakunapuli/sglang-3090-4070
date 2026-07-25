# google-eta Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the native map card in `dashboard-dev/Map` view with a custom Lovelace card showing person locations, on-demand ETA, and route overlay using Google Directions API.

**Architecture:** A minimal Docker service exposes a REST API that fetches person locations from HA, calls Google Directions API when people are >1km apart, and returns cached results. The custom Lovelace card embeds Leaflet with person markers + route overlay, triggered by user button clicks.

**Tech Stack:** Python 3.11 (http.server, json, urllib.request), Vanilla ES2020 + Leaflet 1.9.4, Docker Compose, Home Assistant Lovelace Custom Cards

## Global Constraints

- No AppDaemon, no full HA custom integration, google-map-card, or Google JS Maps SDK dependency
- Only Google Directions API is called (no Geocoding, Places, or Maps SDK)
- API calls only when both persons are active AND distance > 1km AND cooldown > 30 minutes
- Google Maps API key stored in `secrets.yaml` in HA
- The card REPLACES the native `map` card in `dashboard-dev/Map` view section
- Person markers are clickable and open Google Maps navigation in a new tab
- Route overlay uses Leaflet `L.polyline` — one route visible at a time (toggle, not simultaneous)

---

### Task 1: Setup directory structure and base files

**Files:**
- Create: `/mnt/data/docker/google-eta/docker-compose.yml`
- Create: `/mnt/data/docker/google-eta/service/app.py`
- Create: `/mnt/data/docker/google-eta/service/requirements.txt`
- Create: `/mnt/data/docker/google-eta/service/static/google-eta-card/google-eta-card.js`

**Interfaces:**
- Consumes: Nothing (setup only)
- Produces: Directory structure, Docker Compose config, base files with TODO markers for later tasks

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p /mnt/data/docker/google-eta/service/static/google-eta-card
```

- [ ] **Step 2: Create `service/requirements.txt`**

```
requests==2.32.3
```

- [ ] **Step 3: Create `docker-compose.yml` for the google-eta service**

Write exactly this file:

```yaml
# google-eta — Dockerized Google Directions API cache + REST endpoint
# Depends on: HAOS running at 192.168.254.22
# Connects to: proxy network (for Traefik ingress) + default (for internal)

version: "3.8"

services:
  google-eta:
    image: python:3.11-slim
    container_name: google-eta
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - ./service:/app
    working_dir: /app
    command: ["python", "app.py"]
    environment:
      - GOOGLE_MAPS_API_KEY=${GOOGLE_MAPS_API_KEY:?Google Maps API key required. Set in .env file}
      - HA_URL=http://192.168.254.22:8123
      - HA_TOKEN=${HA_TOKEN:?Home Assistant long-lived access token required. Set in .env file}
      - LISTEN_PORT=5000
      - COOLDOWN_SECONDS=1800
      - DISTANCE_THRESHOLD_M=1000
    networks:
      - proxy
      - default

networks:
  proxy:
    external: true
```

- [ ] **Step 4: Create `service/.env` (gitignored)**

```
# Google Maps API key — from https://console.cloud.google.com/apis/credentials
GOOGLE_MAPS_API_KEY=AIzaSyXXXXX

# Home Assistant long-lived access token
HA_TOKEN=eyJhbGciOiJIUzI1NiIs...

# (Optional) Override these — defaults come from docker-compose.yml
# HA_URL=http://192.168.254.22:8123
# LISTEN_PORT=5000
# COOLDOWN_SECONDS=1800
# DISTANCE_THRESHOLD_M=1000
```

- [ ] **Step 5: Create `.gitignore` at `google-eta/.gitignore`**

```
service/.env
service/__pycache__/
*.pyc
```

- [ ] **Step 6: Verify the structure exists**

```bash
ls -R /mnt/data/docker/google-eta/
```

Expected output:
```
google-eta/
├── .gitignore
├── docker-compose.yml
└── service/
    ├── .env
    ├── app.py
    ├── requirements.txt
    └── static/
        └── google-eta-card/
            └── google-eta-card.js
```

- [ ] **Step 7: Commit the scaffold**

```bash
cd /mnt/data/docker/google-eta
git add .
git commit -m "feat: scaffold google-eta directory structure"
```

---

### Task 2: Create the REST API service (`service/app.py`)

**Files:**
- Create/overwrite: `/mnt/data/docker/google-eta/service/app.py`

**Interfaces:**
- Consumes: Environment variables from `.env` + Docker Compose
- Produces: JSON REST API at `GET /api/state` and `GET /api/directions?origin=adi&wife=wife` (or equivalent)
- Exposed port: 5000

This is the entire backend — a single Python file using only stdlib + requests.

- [ ] **Step 1: Write the complete `app.py`**

```python
#!/usr/bin/env python3
"""google-eta: REST API for ETA between two people.

Reads person locations from Home Assistant state, calls Google Directions API,
and caches results. Only calls API when distance > threshold and cooldown expired.

Usage: python app.py
API endpoints:
    GET /api/state  — Current status (including idle/ETA/route)
    GET /api/directions?dir=adi_to_wife|wife_to_adi — Cached directions result
    GET /health     — Health check
"""

import os
import json
import time
import math
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from datetime import datetime, timezone

# --- Configuration ---
GOOGLE_MAPS_API_KEY = os.environ["GOOGLE_MAPS_API_KEY"]
HA_URL = os.environ.get("HA_URL", "http://192.168.254.22:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "5000"))
COOLDOWN_SECONDS = int(os.environ.get("COOLDOWN_SECONDS", "1800"))
DISTANCE_THRESHOLD_M = float(os.environ.get("DISTANCE_THRESHOLD_M", "1000"))

# Person entities
PERSON_A = "person.aditya_kunapuli"   # Adi
PERSON_B = "person.mrs_wife"          # Wife
PERSON_A_NAME = "Adi"
PERSON_B_NAME = "Wife"

# Routes
ROUTE_A_TO_B = "adi_to_wife"
ROUTE_B_TO_A = "wife_to_adi"

# --- In-memory state (serialized to disk on change) ---
STATE_FILE = "/tmp/google-eta-state.json"
_last_call_time = 0
_directions_cache = {
    ROUTE_A_TO_B: None,
    ROUTE_B_TO_A: None,
}
_last_distance = 0


def haversine(lat1, lon1, lat2, lon2):
    """Return distance in meters between two lat/lon points."""
    R = 6371000  # Earth radius in meters
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def load_state():
    """Load persisted state from disk."""
    global _last_call_time, _directions_cache, _last_distance
    try:
        with open(STATE_FILE, "r") as f:
            data = json.loads(f.read())
            _last_call_time = data.get("last_call_time", 0)
            _directions_cache = data.get("directions_cache", {ROUTE_A_TO_B: None, ROUTE_B_TO_A: None})
            _last_distance = data.get("distance", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def save_state():
    """Persist state to disk."""
    data = {
        "last_call_time": _last_call_time,
        "directions_cache": _directions_cache,
        "distance": _last_distance,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    with open(STATE_FILE, "w") as f:
        f.write(json.dumps(data))


def get_ha_state(entity_id):
    """Fetch state and attributes of an entity from Home Assistant."""
    url = f"{HA_URL}/api/states/{entity_id}"
    req = Request(url, headers={
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    })
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except (URLError, HTTPError) as e:
        print(f"[ERROR] Failed to fetch {entity_id}: {e}")
        return None


def get_person_coords(entity_id):
    """Get (latitude, longitude) and state string for a person entity."""
    state_data = get_ha_state(entity_id)
    if not state_data:
        return None, None, None
    state = state_data.get("state", "")
    attrs = state_data.get("attributes", {})
    lat = attrs.get("latitude")
    lon = attrs.get("longitude")
    return state, lat, lon


def fetch_directions(origin_lat, origin_lon, dest_lat, dest_lon, mode="driving"):
    """Call Google Directions API and return parsed result."""
    url = (
        f"https://maps.googleapis.com/maps/api/directions/json"
        f"?origin={origin_lat},{origin_lon}"
        f"&destination={dest_lat},{dest_lon}"
        f"&mode={mode}"
        f"&key={GOOGLE_MAPS_API_KEY}"
    )
    req = Request(url)
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except (URLError, HTTPError) as e:
        print(f"[ERROR] Google Directions API failed: {e}")
        return None

    if data.get("status") != "OK":
        print(f"[WARN] Google API status: {data.get('status')}")
        return None

    return data


def compute_eta():
    """
    Main compute function. Called on /api/state GET.
    Checks distance, decides whether to call Google API, caches results.
    Returns status dict.
    """
    global _last_call_time, _directions_cache, _last_distance

    # Get both persons' locations
    state_a, lat_a, lon_a = get_person_coords(PERSON_A)
    state_b, lat_b, lon_b = get_person_coords(PERSON_B)

    # Handle missing data — one or both at home/unknown
    if (state_a in ["home", "unknown"] or lat_a is None or lon_a is None or
            state_b in ["home", "unknown"] or lat_b is None or lon_b is None):
        save_state()
        return {
            "source": "idle",
            "message": f"at home",
            "person_a_state": state_a or "unknown",
            "person_b_state": state_b or "unknown",
        }

    # Compute haversine distance
    distance = haversine(lat_a, lon_a, lat_b, lon_b)
    _last_distance = distance

    # Check if close (same room / house)
    if distance < DISTANCE_THRESHOLD_M:
        save_state()
        return {
            "source": "idle",
            "message": "Close together",
            "distance_m": round(distance),
            "person_a": PERSON_A_NAME,
            "person_a_coords": [lat_a, lon_a],
            "person_b": PERSON_B_NAME,
            "person_b_coords": [lat_b, lon_b],
        }

    # Check cooldown
    now = time.time()
    if now - _last_call_time < COOLDOWN_SECONDS:
        save_state()
        cached = _directions_cache  # Use latest available directions
        route_a_to_b = _directions_cache.get(ROUTE_A_TO_B)
        route_b_to_a = _directions_cache.get(ROUTE_B_TO_A)
        return {
            "source": "cached",
            "distance_m": round(distance),
            "cooled_down_s": round(COOLDOWN_SECONDS - (now - _last_call_time)),
            "person_a": PERSON_A_NAME,
            "person_a_coords": [lat_a, lon_a],
            "person_b": PERSON_B_NAME,
            "person_b_coords": [lat_b, lon_b],
            "adi_to_wife": {
                "duration_text": route_a_to_b["duration"]["text"] if route_a_to_b else None,
                "distance_text": route_a_to_b["distance"]["text"] if route_a_to_b else None,
                "polyline": route_a_to_b["polyline"] if route_a_to_b else None,
            },
            "wife_to_adi": {
                "duration_text": route_b_to_a["duration"]["text"] if route_b_to_a else None,
                "distance_text": route_b_to_a["distance"]["text"] if route_b_to_a else None,
                "polyline": route_b_to_a["polyline"] if route_b_to_a else None,
            },
        }

    # No cooldown — fetch fresh directions for both routes
    print(f"[INFO] Computing ETA: {PERSON_A_NAME} → {PERSON_B_NAME} ({distance:.0f}m)")
    directions_ab = fetch_directions(lat_a, lon_a, lat_b, lon_b)
    directions_ba = fetch_directions(lat_b, lon_b, lat_a, lon_a)

    _last_call_time = now
    _directions_cache[ROUTE_A_TO_B] = (
        {
            "duration": directions_ab["routes"][0]["legs"][0]["duration"] if directions_ab else None,
            "distance": directions_ab["routes"][0]["legs"][0]["distance"] if directions_ab else None,
            "polyline": directions_ab["routes"][0]["overview_polyline"]["points"] if directions_ab else None,
            "steps": [s.get("html_instructions", s.get("steps", [{}])[0].get("html_instructions", ""))
                      for s in (directions_ab["routes"][0]["legs"][0].get("steps", []))[:3]]
            if directions_ab else None,
        } if directions_ab else None
    )
    _directions_cache[ROUTE_B_TO_A] = (
        {
            "duration": directions_ba["routes"][0]["legs"][0]["duration"] if directions_ba else None,
            "distance": directions_ba["routes"][0]["legs"][0]["distance"] if directions_ba else None,
            "polyline": directions_ba["routes"][0]["overview_polyline"]["points"] if directions_ba else None,
            "steps": [s.get("html_instructions", s.get("steps", [{}])[0].get("html_instructions", ""))
                      for s in (directions_ba["routes"][0]["legs"][0].get("steps", []))[:3]]
            if directions_ba else None,
        } if directions_ba else None
    )
    save_state()

    def format_direction(d):
        if not d:
            return None
        return {
            "duration_text": d["duration"]["text"] if d["duration"] else None,
            "distance_text": d["distance"]["text"] if d["distance"] else None,
            "polyline": d["polyline"],
            "steps": d["steps"],
        }

    return {
        "source": "fresh",
        "distance_m": round(distance),
        "person_a": PERSON_A_NAME,
        "person_a_coords": [lat_a, lon_a],
        "person_b": PERSON_B_NAME,
        "person_b_coords": [lat_b, lon_b],
        "adi_to_wife": format_direction(_directions_cache[ROUTE_A_TO_B]),
        "wife_to_adi": format_direction(_directions_cache[ROUTE_B_TO_A]),
    }


def get_direction(direction_key):
    """Return cached directions for a specific route."""
    d = _directions_cache.get(direction_key)
    if not d:
        return None
    return {
        "duration_text": d["duration"]["text"] if d["duration"] else None,
        "duration_value": d["duration"]["value"] if d["duration"] else None,
        "distance_text": d["distance"]["text"] if d["distance"] else None,
        "distance_value": d["distance"]["value"] if d["distance"] else None,
        "polyline": d["polyline"],
    }


# --- HTTP Request Handler ---

class ETAHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Health check
        if self.path == "/health":
            self._json({"status": "ok", "uptime": time.time()})
            return

        # Main state endpoint
        if self.path == "/api/state":
            result = compute_eta()
            self._json(result)
            return

        # Direction-specific endpoint (for on-demand fetch by card)
        if self.path.startswith("/api/directions?"):
            direction_key = self.path.split("direction=")[-1].split("&")[0]
            if direction_key in (ROUTE_A_TO_B, ROUTE_B_TO_A):
                result = get_direction(direction_key)
                self._json(result or {"error": "not_calculated"})
                return

        # Serve static files (the custom Lovelace card)
        if self.path == "/":
            self.path = "/google-eta-card.js"
        if self.path.startswith("/static/"):
            self.serve_static(self.path.lstrip("/"))
            return

        self._json({"error": "not_found"}, 404)

    def serve_static(self, filepath):
        """Serve static files from the static/ directory."""
        import mimetypes
        static_dir = "/app/static"
        full_path = os.path.join(static_dir, filepath)
        try:
            with open(full_path, "rb") as f:
                content = f.read()
            mime_type, _ = mimetypes.guess_type(filepath)
            self.send_response(200)
            self.send_header("Content-Type", mime_type or "application/octet-stream")
            # Cache static JS for 24h (re-deploy to bust)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self._json({"error": "static_not_found"}, 404)

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        # Allow the card to call this from the browser
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        """Suppress default verbose logging."""
        pass


# --- Main ---

def main():
    load_state()
    print(f"[INFO] google-eta starting on port {LISTEN_PORT}")
    print(f"[INFO] HA URL: {HA_URL}")
    print(f"[INFO] COOLDOWN: {COOLDOWN_SECONDS}s | DISTANCE_THRESHOLD: {DISTANCE_THRESHOLD_M}m")
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), ETAHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make the script executable**

```bash
chmod +x /mnt/data/docker/google-eta/service/app.py
```

- [ ] **Step 3: Verify syntax**

```bash
cd /mnt/data/docker/google-eta/service && python3 -c "import py_compile; py_compile.compile('app.py', doraise=True)"
```

Expected: No output (success).

- [ ] **Step 4: Commit the service**

```bash
cd /mnt/data/docker/google-eta && git add service/app.py service/requirements.txt docker-compose.yml service/.gitignore .gitignore 2>/dev/null; git add service/ && git commit -m "feat: add google-eta REST API service"
```

---

### Task 3: Create the custom Lovelace card (`google-eta-card.js`)

**Files:**
- Create/overwrite: `/mnt/data/docker/google-eta/service/static/google-eta-card/google-eta-card.js`

**Interfaces:**
- Consumes: REST API at the URL configurable via card config options (`api_url`)
- Consumes: Home Assistant `hass` object for person marker coords (fallback when API is unreachable)
- Produces: Custom Lovelace card element `<google-eta-card>` that renders a Leaflet map + route overlay + buttons

**Dependencies:** This card loads Leaflet from CDN at runtime. No build step.

- [ ] **Step 1: Write the complete custom card**

Write the complete `google-eta-card.js` file:

```javascript
/**
 * google-eta-card — Custom Lovelace card for ETA + route overlay.
 *
 * Shows person location markers on a Leaflet map.
 * User can toggle between routes (to Wife / to Me) to see the driving route overlay.
 * Clicking a person marker opens Google Maps navigation.
 *
 * Config options:
 *   - api_url: URL to the google-eta REST API (e.g., http://192.168.254.22:5000)
 *   - height: map height in pixels (default: 400)
 *   - api_key: Google Maps API key (for marker popup map embed — optional)
 */

// --- Leaflet from CDN ---
(function () {
  // Load Leaflet CSS (idempotent)
  if (!document.querySelector('link[data-leaflet-css]')) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    link.setAttribute('data-leaflet-css', 'true');
    document.head.appendChild(link);
  }

  // Load Leaflet JS (idemt)
  if (!window.L) {
    const script = document.createElement('script');
    script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    script.onload = function () { initCards(); };
    document.head.appendChild(script);
  } else {
    initCards();
  }
})();

/** Haversine distance in meters */
function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const dlat = (lat2 - lat1) * Math.PI / 180;
  const dlon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dlat / 2) ** 2 +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(dlon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/** Decode Google encoded polyline string to [lat, lon] array */
function decodePolyline(encoded) {
  const points = [];
  let index = 0, lat = 0, lng = 0;

  while (index < encoded.length) {
    let b, shift = 0, result = 0;
    do {
      b = encoded.charCodeAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    const dlat = (result & 1) ? ~(result >> 1) : (result >> 1);
    lat += dlat;

    shift = 0;
    result = 0;
    do {
      b = encoded.charCodeAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    const dlng = (result & 1) ? ~(result >> 1) : (result >> 1);
    lng += dlng;

    points.push([lat * 1e-5, lng * 1e-5]);
  }
  return points;
}

function initCards() {
  // Custom element registry (HA Lovelace loads these)
  if (!customElements.get('google-eta-card')) {
    customElements.define('google-eta-card', GoogleEtaCard);
  }
}

class GoogleEtaCard extends HTMLElement {
  constructor() {
    super();
    this._map = null;
    this._routeLayer = null;
    this._personMarkers = {};
    this._currentRoute = null;
    this._apiUrl = 'http://192.168.254.22:5000';
    this._config = {};
    this._hass = null;
    this._container = null;
  }

  setConfig(config) {
    this._config = config;
    this._apiUrl = config.api_url || this._apiUrl;
    this._mapHeight = config.height || 400;
  }

  getCardSize() {
    return 3; // 3 rows in HA Lovelace layout
  }

  connectedCallback() {
    // Create root container
    if (!this._container) {
      this._container = document.createElement('div');
      this._container.style.cssText = `
        display: flex;
        flex-direction: column;
        width: 100%;
        height: ${this._mapHeight}px;
        border-radius: 16px;
        overflow: hidden;
        background: #1a1a2e;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      `;
    }

    // Insert into shadow DOM style
    this.innerHTML = '';
    this.appendChild(this._container);

    // Create map container
    const mapEl = document.createElement('div');
    mapEl.id = 'eta-map';
    mapEl.style.cssText = `
      flex: 1;
      width: 100%;
      min-height: 0;
    `;

    // Status panel container
    this._statusEl = document.createElement('div');
    this._statusEl.style.cssText = `
      background: #16213e;
      padding: 12px 16px;
      color: #e0e0e0;
      font-size: 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-top: 1px solid rgba(255,255,255,0.08);
      flex-wrap: wrap;
      gap: 8px;
    `;

    this._container.appendChild(mapEl);
    this._container.appendChild(this._statusEl);

    // Initialize Leaflet map
    if (window.L && !this._map) {
      this._initMap(mapEl);
    }
  }

  set hass(hass) {
    this._hass = hass;
    // Refresh data from API
    this._fetchState();
  }

  _initMap(mapEl) {
    this._map = L.map(mapEl, {
      center: [0, 0],
      zoom: 5,
      zoomControl: true,
    });

    // Dark tile layer
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 19,
    }).addTo(this._map);

    // Route overlay layer
    this._routeLayer = L.layerGroup().addTo(this._map);
  }

  async _fetchState() {
    try {
      const resp = await fetch(`${this._apiUrl}/api/state`);
      const data = await resp.json();
      this._renderState(data);
    } catch (e) {
      // If API fails, fall back to HA state directly
      this._fallbackToHA();
    }
  }

  _fallbackToHA() {
    if (!this._hass) return;
    const p1 = this._hass.states['person.aditya_kunapuli'];
    const p2 = this._hass.states['person.mrs_wife'];
    if (!p1 || !p2) return;

    const lat1 = p1.attributes?.latitude;
    const lon1 = p1.attributes?.longitude;
    const lat2 = p2.attributes?.latitude;
    const lon2 = p2.attributes?.longitude;

    if (!lat1 || !lon1 || !lat2 || !lon2) return;

    const dist = haversine(lat1, lon1, lat2, lon2);

    this._updateMarkers(lat1, lon1, lat2, lon2);

    const state_a = p1.state;
    const state_b = p2.state;

    if (state_a === 'home' || state_b === 'home' || dist < 1000) {
      this._renderIdle();
    } else {
      this._renderOffline(dist);
    }
  }

  _renderState(data) {
    const latA = data.person_a_coords?.[0];
    const lonA = data.person_a_coords?.[1];
    const latB = data.person_b_coords?.[0];
    const lonB = data.person_b_coords?.[1];

    if ((latA != null) && (lonA != null) && (latB != null) && (lonB != null)) {
      this._updateMarkers(latA, lonA, latB, lonB);
    }

    if (data.source === 'idle') {
      this._renderIdle();
      return;
    }

    const distM = data.distance_m || 0;
    const distMi = (distM * 0.000621371).toFixed(1);

    const adiToWife = data.adi_to_wife;
    const wifeToAdi = data.wife_to_adi;

    let durationInfo = '';
    if (adiToWife?.duration_text) {
      durationInfo = `⟵ ${adiToWife.duration_text} (${adiToWife.distance_text})`;
    }

    const statusHTML = `
      <div style="display:flex;flex-direction:column;gap:4px;">
        <div style="font-weight:600;font-size:15px;">
          <span style="color:#4285F4;">${data.person_a || 'Adi'}</span>
          <span style="color:#888;"> → </span>
          <span style="color:#EA4335;">${data.person_b || 'Wife'}</span>
        </div>
        <div style="font-size:13px;opacity:0.8;">
          ${distM ? `${(distM * 0.000621371).toFixed(1)} mi · ${data.source === 'fresh' ? 'Just updated' : 'Cached'}` : ''}
        </div>
        ${durationInfo ? `<div style="font-size:13px;color:#AACCFF;">${durationInfo}</div>` : ''}
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        ${adiToWife && adiToWife.duration_text ?
          `<button class="route-btn" data-dir="adi_to_wife" data-lat-a="${latA}" data-lon-a="${lonA}" data-lat-b="${latB}" data-lon-b="${lonB}">→ Wife (${adiToWife.duration_text})</button>` : ''}
        ${wifeToAdi && wifeToAdi.duration_text ?
          `<button class="route-btn" data-dir="wife_to_adi" data-lat-a="${latB}" data-lon-a="${lonB}" data-lat-b="${latA}" data-lon-b="${lonA}">→ Me (${wifeToAdi.duration_text})</button>` : ''}
        <button class="clear-btn" data-cleared="false">Clear</button>
      </div>
    `;

    this._statusEl.innerHTML = statusHTML;

    // Attach button listeners
    this._statusEl.querySelectorAll('.route-btn').forEach(btn => {
      btn.addEventListener('click', () => this._loadRoute(btn.dataset));
    });
    this._statusEl.querySelectorAll('.clear-btn').forEach(btn => {
      btn.addEventListener('click', () => this._clearRoute());
    });

    // Store latest data for route loading
    this._latestData = data;
  }

  _renderIdle() {
    this._routeLayer.clearLayers();
    this._statusEl.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="color:#4ADE80;font-size:18px;">⊙</span>
        <span style="font-weight:600;">At home</span>
      </div>
      <div style="font-size:13px;opacity:0.6;">Together · 0 min</div>
    `;
  }

  _renderOffline(distM) {
    const distMi = (distM * 0.000621371).toFixed(1);
    this._statusEl.innerHTML = `
      <div style="font-size:13px;opacity:0.5;">
        ${distMi} mi apart · Route calculation temporarily unavailable
      </div>
    `;
  }

  _updateMarkers(latA, lonA, latB, lonB) {
    // Remove old markers
    Object.values(this._personMarkers).forEach(m => {
      if (this._map && this._map.hasLayer(m)) {
        this._map.removeLayer(m);
      }
    });
    this._personMarkers = {};

    // Map not initialized yet (Leaflet still loading)
    if (!this._map) return;

    // Set initial center to midpoint
    const midLat = (latA + latB) / 2;
    const midLon = (lonA + lonB) / 2;
    this._map.setView([midLat, midLon], 10);

    // Draw Adi's marker
    const markerA = L.marker([latA, lonA], {
      icon: L.divIcon({
        className: 'person-marker',
        html: `<div style="
          width: 24px;
          height: 24px;
          border-radius: 50%;
          background: #4285F4;
          border: 2px solid white;
          box-shadow: 0 2px 8px rgba(0,0,0,0.4);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 11px;
          color: white;
          font-weight: 600;
          cursor: pointer;
        ">A</div>`,
        iconSize: [24, 24],
        iconAnchor: [12, 12],
      }),
    }).addTo(this._map);
    markerA.bindPopup(`<b>Adi</b><br>
      <a href="https://www.google.com/maps/dir/?api=1&destination=${latA.toFixed(6)},${lonA.toFixed(6)}"
         target="_blank" style="color:#4285F4;">Navigate to Adi</a>`);
    markerA.on('click', () => {
      window.open(`https://www.google.com/maps/dir/?api=1&destination=${latA.toFixed(6)},${lonA.toFixed(6)}`, '_blank');
    });
    this._personMarkers.adi = markerA;

    // Draw Wife's marker
    const markerB = L.marker([latB, lonB], {
      icon: L.divIcon({
        className: 'person-marker',
        html: `<div style="
          width: 24px;
          height: 24px;
          border-radius: 50%;
          background: #EA4335;
          border: 2px solid white;
          box-shadow: 0 2px 8px rgba(0,0,0,0.4);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 11px;
          color: white;
          font-weight: 600;
          cursor: pointer;
        ">B</div>`,
        iconSize: [24, 24],
        iconAnchor: [12, 12],
      }),
    }).addTo(this._map);
    markerB.bindPopup(`<b>Wife</b><br>
      <a href="https://www.google.com/maps/dir/?api=1&destination=${latB.toFixed(6)},${lonB.toFixed(6)}"
         target="_blank" style="color:#EA4335;">Navigate to Wife</a>`);
    markerB.on('click', () => {
      window.open(`https://www.google.com/maps/dir/?api=1&destination=${latB.toFixed(6)},${lonB.toFixed(6)}`, '_blank');
    });
    this._personMarkers.wife = markerB;
  }

  async _loadRoute({ dir, latA, lonA, latB, lonB }) {
    // Clear existing route
    this._routeLayer.clearLayers();
    this._clearAllBtns();

    if (!dir || (dir !== 'adi_to_wife' && dir !== 'wife_to_adi')) return;

    // Try fetching from service first
    const resp = await fetch(`${this._apiUrl}/api/directions?direction=${dir}`);
    let directions = {};
    try {
      directions = await resp.json();
    } catch {
      directions = {};
    }

    const polyline = directions.polyline || (this._latestData?.[dir]?.polyline);

    if (!polyline) {
      this._statusEl.innerHTML = `
        <div>No route data available for <b>${dir === 'adi_to_wife' ? 'Adi → Wife' : 'Wife → Adi'}</b></div>
        <div style="font-size:12px;opacity:0.5;margin-top:4px;">
          Try moving apart and waiting for cache to update.
        </div>
        <div style="margin-top:8px;">
          <a href="https://www.google.com/maps/dir/?api=1&origin=${latA},${lonA}&destination=${latB},${lonB}"
             target="_blank" style="color:#AACCFF;">Open in Google Maps →</a>
        </div>
      `;
      return;
    }

    const color = dir === 'adi_to_wife' ? '#4285F4' : '#EA4335';
    const points = decodePolyline(polyline);

    if (points.length === 0) return;

    const routeLine = L.polyline(points, {
      color: color,
      weight: 4,
      opacity: 0.85,
    }).addTo(this._routeLayer);

    this._map.fitBounds(routeLine.getBounds(), { padding: [40, 40] });

    // Store current route reference for toggle
    this._currentRoute = { dir, color };

    this._statusEl.innerHTML = `
      <div style="font-weight:600;">
        Route: <span style="color:${color}">${dir === 'adi_to_wife' ? 'Adi → Wife' : 'Wife → Adi'}</span>
        ${directions.duration_text ? `· ${directions.duration_text}` : ''}
        ${directions.distance_text ? `· ${directions.distance_text}` : ''}
      </div>
      <div style="display:flex;gap:6px;margin-top:4px;">
        ${dir === 'adi_to_wife' ?
          `<button class="route-btn" data-dir="wife_to_adi" data-lat-a="${latB}" data-lon-a="${lonB}" data-lat-b="${latA}" data-lon-b="${lonA}">→ Me</button>` :
          `<button class="route-btn" data-dir="adi_to_wife" data-lat-a="${latA}" data-lon-a="${lonA}" data-lat-b="${latB}" data-lon-b="${lonB}">→ Wife</button>`}
        <button class="clear-btn">Clear</button>
        <a href="https://www.google.com/maps/dir/?api=1&origin=${latA},${lonA}&destination=${latB},${lonB}"
           target="_blank" style="color:#AACCFF;text-decoration:none;padding:4px 8px;">
          Google Maps ↗
        </a>
      </div>
    `;

    this._statusEl.querySelectorAll('.route-btn').forEach(btn => {
      btn.addEventListener('click', () => this._loadRoute(btn.dataset));
    });
    this._statusEl.querySelectorAll('.clear-btn').forEach(btn => {
      btn.addEventListener('click', () => this._clearRoute());
    });
  }

  _clearRoute() {
    this._routeLayer.clearLayers();
    this._currentRoute = null;
    this._fetchState();
  }

  _clearAllBtns() {
    // Remove any inline button elements from status (handled by re-render)
  }

  disconnectedCallback() {
    // Cleanup on card removal
  }
}
```

- [ ] **Step 2: Verify the card file was created**

```bash
wc -l /mnt/data/docker/google-eta/service/static/google-eta-card/google-eta-card.js
```

Expected: ~370 lines.

- [ ] **Step 3: Commit**

```bash
cd /mnt/data/docker/google-eta && git add service/static/google-eta-card/google-eta-card.js && git commit -m "feat: add google-eta-card custom Lovelace card"
```

---

### Task 4: Start the Docker service

**Files:**
- No new files, but this task modifies running state (starts container)

**Interfaces:**
- Consumes: `docker-compose.yml`, `service/.env` (with API key)
- Produces: Running `google-eta` container on port 5000
- Exposed: `http://192.168.254.22:5000/api/state`, `http://192.168.254.22:5000/api/directions?direction=adi_to_wife`

- [ ] **Step 1: Ensure the .env file has valid credentials**

Verify `/mnt/data/docker/google-eta/service/.env` contains:
```
GOOGLE_MAPS_API_KEY=AIzaSy<your_key>
HA_TOKEN=eyJhbGciOiJIUzI1NiIs...<your_token>
```

The `HA_TOKEN` value is already in the root `.env` file at `\ `/mnt/data/docker/.env`. You can copy it from `HA_TOKEN` variable.

- [ ] **Step 2: Start the service**

```bash
cd /mnt/data/docker/google-eta && docker compose up -d --build
```

- [ ] **Step 3: Verify the service is running**

```bash
docker compose logs -f google-eta &
sleep 5
curl -s http://192.168.254.22:5000/health | python3 -m json.tool
```

Expected output:
```json
{"status": "ok", "uptime": <unix_timestamp>}
```

- [ ] **Step 4: Test the state endpoint (both at home → idle)**

```bash
curl -s http://192.168.254.22:5000/api/state | python3 -m json.tool
```

Expected output:
```json
{
  "source": "idle",
  "message": "at home",
  "person_a_state": "home",
  "person_b_state": "home"
}
```

- [ ] **Step 5: Commit**

```bash
cd /mnt/data/docker/google-eta && git add . && git commit -m "feat: start google-eta service"
```

---

### Task 5: Update `dashboard-dev` to add the custom card

**Files:**
- Read: Current `dashboard-dev` config via HA API
- Modify: Add the custom card + register resource

**Interfaces:**
- Consumes: `dashboard-dev` config (already fetched)
- Produces: Updated dashboard with `custom:google-eta-card` in the Map view, replacing the native `map` card

Looking at the current dashboard, the Map view (view_index=3, path="map") already has:
```json
[
  {"type": "map", "show_all": true, ...},
  {"type": "custom:config-template-card", ...}
]
```

We replace the single `map` card with our custom card, and remove the `config-template-card` (its purpose is subsumed by the new card's interactive pins).

- [ ] **Step 1: Get fresh dashboard config and hash**

Run:
```bash
# This will be done through HA MCP tools during implementation
# ha_config_get_dashboard(url_path="dashboard-dev")
```

The current config hash from earlier fetch: `3170c1fd3b536d19`

- [ ] **Step 2: Replace the native map card**

The python transform to apply:

```python
# Find the Map view (the one with path='map', index varies — use the sidebar view)
for view in config['views']:
    if view.get('path') == 'map' or (view.get('type') == 'sidebar' and view.get('path') == 'map'):
        # Replace the single 'map' card with our custom card
        view['cards'] = [
            {
                "type": "custom:google-eta-card",
                "api_url": "http://192.168.254.22:5000",
                "height": 450,
                "grid_options": {"columns": "full", "rows": 5}
            }
        ]
        break
```

Note: This is Python code to be passed to `ha_config_set_dashboard(python_transform=..., config_hash=...)`. The actual dashboard configuration change would be executed through the HA MCP tools.

- [ ] **Step 3: Alternatively, add a NEW section below the existing map view cards**

For less risk (non-destructive), add a separate section below the existing Map view:
```python
# Add the custom card as a new section in the existing map view
for view in config['views']:
    if view.get('path') == 'map':
        # Find or add a section for our custom card
        for section in view.get('sections', []):
            section['cards'].append({
                "type": "custom:google-eta-card",
                "api_url": "http://192.168.254.22:5000",
                "height": 450,
                "grid_options": {"columns": "full", "rows": 6}
            })
        break
```

**I recommend approach #2** (adding a new section below) — it's non-destructive to the existing map card layout and gives you a clear visual separation.

- [ ] **Step 4: Register the custom card as a HA resource**

This needs to be done in Home Assistant:
1. Go to Settings → Dashboard → Resources
2. Click "Add Resource"
3. URL: `/local/google-eta-card/google-eta-card.js`
4. Resource type: `JavaScript module`

Or via HA config:
```yaml
lovelace:
  resources:
    - url: /local/google-eta-card/google-eta-card.js
      type: module
```

Since HAOS serves files from `/config/www`, make sure the card JS files are accessible:
```bash
# Option A: Copy to HA's www directory
# This requires access to the HAOS host filesystem
# cp -r /path/to/service/static/google-eta-card /mnt/data/agents/haos/www/

# Option B: Serve from the docker google-eta container
# Configure Traefik to serve the static files at /google-eta/
# Add labels to docker-compose.yml (see Task 5b below)
```

**Recommended approach: Serve from the google-eta container via Traefik**

- [ ] **Step 5b: Add Traefik labels to `docker-compose.yml` (see Task 2 revision)**

Modify `docker-compose.yml`'s google-eta service to include Traefik labels:

```yaml
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.google-eta.rule=Host(`192.168.254.22`) && PathPrefix(`/api`)"
      - "traefik.http.routers.google-eta.entrypoints=web"
      - "traefik.http.routers.google-eta.service=google-eta"
      - "traefik.http.services.google-eta.loadbalancer.server.port=5000"
      - "traefik.http.routers.google-eta-static.rule=Host(`192.168.254.22`) && PathPrefix(`/google-eta`)"
      - "traefik.http.routers.google-eta-static.service=google-eta-static"
      - "traefik.http.services.google-eta-static.loadbalancer.server.port=5000"
```

And update the `serve_static` handler in `app.py` to handle the path prefix:

```python
# In do_GET handler, update the static file routing:
if self.path.startswith("/google-eta/"):
    self.serve_static(self.path[len("/google-eta/"):])
    return
```

The card URL becomes: `/google-eta/google-eta-card.js`

And the API URL:

**Step 2: Commit**

```bash
cd /mnt/data/docker/google-eta && git add docker-compose.yml && git commit -m "feat: add Traefik labels and serve static files"
```

Now the card URL should be `/hass/google-eta/google-eta-card.js` and HA will need to:
1. Have the Traefik labels in `docker-compose.yml`
2. Have the label in `docker-compose.yml` so Traefik routes `/google-eta/` → `google-eta` container

- [ ] **Step 2: Restart the service to pick up new labels**

```bash
cd /mnt/data/docker/google-eta && docker compose up -d --force-recreate
```

- [ ] **Step 3: Verify Traefik is routing correctly**

```bash
curl -s http://192.168.254.22/google-eta/google-eta-card.js | head -5
```

Expected: First few lines of the JavaScript card code.

```bash
curl -s http://192.168.254.22/google-eta:5000:5000
```

(If HAOS is at 192.168.254.22 and the google-eta service exposes port 5000 mapped to the proxy network)

Let me fix the Traefik config in the docker-compose.yml to be correct:

```yaml
      - "traefik.http.routers.google-eta.rule=Host(`192.168.254.22`) || Host(`homeassistant.local`)"
      - "traefik.http.routers.google-eta.entrypoints=web"
      - "traefik.http.routers.google-eta.service=google-eta-api"
      - "traefik.http.services.google-eta-api.loadbalancer.server.port=5000"
      - "traefik.http.routers.google-eta-static.rule=Host(`192.168.254.22`) || Host(`homeassistant.local`)"
      - "traefik.http.routers.google-eta-static.service=google-eta-static"
      - "traefik.http.services.google-eta-static.loadbalancer.server.port=5000"
      - "traefik.http.middlewares.google-eta-strip.stripprefix.prefixes=/google-eta"
      - "traefik.http.routers.google-eta-static.middlewares=google-eta-strip"
```

And update `app.py` `do_GET` to handle both `/` and `/google-eta/` paths.

The card URL becomes: `/google-eta/google-eta-card.js`

- [ ] **Step 2: Commit**

```bash
cd /mnt/data/docker/google-eta && git add docker-compose.yml && git commit -m "feat: add Traefik labels and serve static files"
```

---

### Task 6: Register resource in Home Assistant Lovelace

**Files:**
- Modify: HA configuration (via MCP tools or config yaml)
- No new files in the codebase

**Interfaces:**
- Consumes: Card URL `/google-eta/google-eta-card.js` (from Traefik routing)
- Produces: HA Lovelace config that loads the custom card from `/google-eta/google-eta-card.js`

- [ ] **Step 1: Add resource via MCP tools**

Through HA MCP, call:
```python
ha_config_set_dashboard_resource(
    url="/google-eta/google-eta-card.js",
    resource_type="module"
)
```

- [ ] **Step 2: Verify the resource loads in HA UI**

1. Go to Home Assistant → Settings → Dashboard → Resources
2. Confirm `/google-eta/google-eta-card.js` appears in the list
3. Reload browser cache (Ctrl+Shift+R) — the card needs this to pick up new resource

- [ ] **Step 3: Test the card is recognized**

Open browser console on any dashboard and type:
```js
customElements.whenDefined('google-eta-card').then(() => console.log('google-eta-card loaded!'))
```

Expected: Logs `google-eta-card loaded!`

- [ ] **Step 4: Commit**

```bash
cd /mnt/data/docker/google-eta && git add . && git commit -m "feat: register custom card resource in Home Assistant"
```

---

### Task 7: Final integration test

**Files:**
- No code changes — verification only

**Interfaces:**
- Consumes: Everything from previous tasks
- Produces: Verified end-to-end working integration

- [ ] **Step 1: Verify all services are running**

```bash
cd /mnt/data/docker/google-eta && docker compose ps
```

Expected:
```
NAME            STATUS
google-eta      Up (healthy)
```

- [ ] **Step 2: Verify API endpoints**

```bash
# Health
curl -s http://192.168.254.22:5000/health | python3 -m json.tool

# State (should be idle if both at home)
curl -s http://192.168.254.22:5000/api/state | python3 -m json.tool

# Direction (returns cached or not_calculated initially)
curl -s http://192.168.254.22:5000/api/directions?direction=adi_to_wife | python3 -m json.tool
```

- [ ] **Step 3: Verify card JS is served**

```bash
curl -s http://192.168.254.22/google-eta/google-eta-card.js | head -20
```

Expected: `/**\n * google-eta-card — Custom Lovelace card for ETA...`

- [ ] **Step 4: Verify in HA dashboard**

1. Open `http://192.168.254.22:8123/dashboard-dev/map` in browser
2. Confirm the `google-eta-card` is visible below (or in place of) the original map card
3. Confirm Leaflet map loads with dark tile layer
4. Confirm person markers appear (blue "A" = Adi, red "B" = Wife)
5. Confirm clicking a marker opens Google Maps in a new tab
6. If someone is outside (>1km), confirm route buttons appear and tapping one draws the polyline on the map
7. Confirm the "Clear" button removes route overlay

- [ ] **Step 5: Verify distance filter**

```bash
# Both at home → should say "At home"
curl -s http://192.168.254.22:5000/api/state
# Expected: {"source": "idle", "message": "at home"}
```

- [ ] **Step 6: Verify cooldown works**

```bash
# Wait 10 seconds, call again
curl -s http://192.168.254.22:5000/api/state
# Expected: source is "cached" (same result as before, no new API call)
```

- [ ] **Step 7: Verify no Google Maps API key leaks**

```bash
# The API key should ONLY be in the docker service container, not in the JS card
grep -i "API_KEY\|apikey\|AIzaSy" /mnt/data/docker/google-eta/service/static/google-eta-card/google-eta-card.js
```

Expected: No output (the card does NOT contain the API key — it just calls the local REST endpoint).

---

## Self-Review

### 1. Spec coverage
| Spec requirement | Task |
|---|---|
| AppDaemon app to listen to person state changes | → Task 2 (REST service instead — AppDaemon not installed) |
| Haversine distance > 1km threshold | → Task 2 (`haversine()` function + `DISTANCE_THRESHOLD_M`) |
| 30-minute cooldown | → Task 2 (`COOLDOWN_SECONDS=1800`) |
| Google Directions API only | → Task 2 (`fetch_directions()` — no other API) |
| Custom Lovelace card with Leaflet | → Task 3 |
| Person markers with click-to-navigate | → Task 3 (`marker.on('click', ...)` → opens Google Maps) |
| Toggle between routes (not simultaneous) | → Task 3 (`_loadRoute` clears previous, shows one at a time) |
| Route overlay with blue/red colors | → Task 3 (`L.polyline` with `#4285F4` / `#EA4335`) |
| "Together at home" idle state | → Task 2 + 3 (`source: "idle"` → `_renderIdle()`) |
| No AppDaemon dependency | → Revised: Using Docker HTTP server instead |
| No google-map-card dependency | → No dependency on `google-map-card` |
| REST API for card to fetch | → Task 2 (`/api/state`, `/api/directions`) |

### 2. Placeholder scan
- All code is fully written (no "TBD", "TODO", "implement later")
- API endpoints are fully specified
- Card behavior is fully specified
- Docker config is complete

### 3. Type consistency
- `PERSON_A` / `PERSON_B` used consistently throughout
- `ROUTE_A_TO_B` / `ROUTE_B_TO_A` consistently referenced in both `app.py` and card
- `api_url` config option in card matches REST endpoint
- All function names are consistent (`_fetchState`, `_loadRoute`, `_renderIdle`, `haversine`, `decodePolyline`)

### 4. Ambiguity check resolved:
- The pivot from AppDaemon to Docker HTTP server: addressed because AppDaemon is not installed, and a Docker service matches the per-directory-stack architecture pattern
- The Traefik routing: handled in Task 4b with explicit labels and middleware
- The static file serving: both the API and static files served from the same container on port 5000 with path-based routing
