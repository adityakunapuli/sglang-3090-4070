# google-eta Design: Native HA Integration + Leaflet Card

> Two people, driving ETA, one custom integration.

**Date:** 2026-07-24
**Status:** Design approved (v2 — native HA approach)
**Location:** `/mnt/data/docker/google-eta/`

## Problem

Two people live in the house (Adi + Wife). Both have GPS tracking through Home Assistant. There's no way to see on-screen ETA or route visualization without opening Google Maps externally.

## Goal

Build a proper Home Assistant custom integration that:
- Listens to two person entities
- Computes haversine distance between them
- Calls Google Directions API when >1km apart (30 min cooldown)
- Exposes driving ETA as two HA sensor entities
- Provides a Leaflet-based Lovelace card showing person markers + ETA status

## Architecture

Native HA custom component. No Docker, no exposed ports, no REST API.

```
HAOS (192.168.254.22)
├── custom_components/google_eta/
│   ├── __init__.py        ← Config entry setup, forward sensor platform
│   ├── config_flow.py     ← UI: enter API key + select person entities
│   ├── const.py           ← Constants, attribute keys
│   ├── coordinator.py     ← Person listeners, haversine, Google API, cache
│   └── sensor.py          ← Two sensors: eta_to_wife, eta_to_me
│
├── /config/www/
│   └── google-eta-card.js ← Leaflet card, reads sensor entities via websocket
│
└── Lovelace dashboard
    └── Resource: /local/google-eta-card.js (type: module)
    └── Card in Map view: custom:google-eta-card
```

### Data Flow

1. Person state changes → Coordinator's person listener fires
2. Haversine distance check → if <1km, set sensors to "idle", done
3. Cooldown check → if <30 min since last API call, use cached data
4. Google Directions API call (both directions in parallel) → update sensor attributes
5. Sensors push new state via HA's normal entity system → browser receives via WebSocket
6. Card renders: reads `sensor.eta_to_wife` attributes (duration, distance, coords) → draws Leaflet map

## Components

### Component A: Data Coordinator (`coordinator.py`)

The integration's brain. Owns person listeners, distance check, caching, and API calls.

```python
class ETADataCoordinator:
    def __init__(self, hass, config):
        self._hass: HomeAssistant = hass
        self._api_key = config["api_key"]
        self._person_a = config["person_a"]   # e.g., person.aditya_kunapuli
        self._person_b = config["person_b"]   # e.g., person.mrs_wife
        self._cooldown = config.get("cooldown", 1800)        # 30 min
        self._threshold_m = config.get("threshold_m", 1000)  # 1 km
        self._cache = {ROUTE_A_TO_B: None, ROUTE_B_TO_A: None}
        self._last_call = 0
        self._last_distance = 0
```

**Lifecycle:**
- `async_setup`: Subscribe to person state changes, run initial computation
- `_compute`: Get person coords → haversine → cooldown → Google API → update cache
- `_notify_update`: Tell sensors to pull from coordinator

**Triggers:** Only on person state change (event-driven, no polling).

**Google Directions API Request:**
```
https://maps.googleapis.com/maps/api/directions/json
?origin={latA},{lonA}&destination={latB},{lonB}&mode=driving&key={api_key}
```

**Response stored per direction:**
- `duration`: `{text: "23 min", value: 1380}`
- `distance`: `{text: "14.2 mi", value: 22850}`
- `polyline`: encoded route string

### Component B: Sensors (`sensor.py`)

Two sensor entities, one per direction:
- `sensor.eta_to_wife` — Adi → Wife
- `sensor.eta_to_me` — Wife → Adi

**State:** Duration text (e.g., "23 min") or "idle"

**Attributes:**
```json
{
  "source": "fresh|cached|idle",
  "distance_m": 14127,
  "duration_text": "23 min",
  "distance_text": "12.5 mi",
  "person_a": "Adi",
  "person_b": "Wife",
  "person_a_coords": [34.17, -118.98],
  "person_b_coords": [34.21, -118.84],
  "direction": {
    "duration_text": "23 min",
    "distance_text": "12.5 mi",
    "polyline": "encoded_polyline_string"
  }
}
```

Sensors attach to the coordinator, call `coordinator.get_data()`, and use HA's normal entity update cycle. No custom polling.

### Component C: Lovelace Card (`google-eta-card.js`)

```
┌─────────────────────────────────┐
│                                 │
│        Leaflet Map              │
│   (dark tiles + person markers) │
│                                 │
├─────────────────────────────────┤
│ ⊙ At home · 58 m apart         │  ← Idle state
└─────────────────────────────────┘

┌─────────────────────────────────┐
│                                 │
│        Leaflet Map              │
│   (dark tiles + person markers) │
│                                 │
├─────────────────────────────────┤
│ Adi → Wife · 12.5 mi           │  ← Active state
│ 23 min                         │
│ → Wife   → Me   Clear          │
└─────────────────────────────────┘
```

**Features:**
- Dark tile layer (CartoDB basemaps, free, no API key needed)
- Custom circle markers: blue "A" for Adi, red "B" for Wife
- Markers clickable → opens Google Maps navigation in new tab
- Status panel below map: idle/ETA state with both directions
- Route buttons toggle driving route overlay (polyline from sensor attributes)
- Leaflet loaded from CDN (`unpkg.com/leaflet@1.9.4`)

**Data source:** Reads from `this.hass.states['sensor.eta_to_wife']` and `this.hass.states['sensor.eta_to_me']` via HA's normal websocket. No REST calls, no external API from the browser.

## Configuration

### Config Flow (via HA UI)

User enters via Settings → Devices & Services → Add Integration:
1. Step 1: Google Maps API Key (secret)
2. Step 2: Select Person A and Person B from entity picker

Options stored in config entry. Reconfigure supported.

### Lovelace Registration

**Resource:**
```yaml
url: /local/google-eta-card.js
type: module
```

**Card in dashboard:**
```yaml
type: custom:google-eta-card
```

## Constraints

- No Docker, no exposed ports, no REST API
- No AppDaemon dependency
- Only Google Directions API (no Geocoding, Places, or Maps SDK)
- No polling — event-driven callbacks only (person state changes)
- Haversine distance check prevents API calls when <1km
- 30-minute cooldown between API calls
- API key stored in HA config entry (never in browser)
- Card hosted in `/config/www/` (served by HA's internal server)

## What This Does NOT Do

- Live route tracking (static calculated route only)
- Real-time ETA updates while driving (recalculates on person state change)
- Multiple route alternatives (fastest driving route only)
- Walk/transit/cycling modes (driving only)
- Overlap comparison (one route overlay at a time)
- Google Geocoding API (HA provides lat/lon directly)

## Deployment

### New files in HA's `/config/`:
```
/config/custom_components/google_eta/
├── __init__.py        ← Config entry setup, forward sensor platform
├── config_flow.py     ← UI: API key + person selection
├── const.py           ← Constants, attribute keys
├── coordinator.py     ← Person listeners, haversine, Google API, cache
└── sensor.py          ← Two sensor entities

/config/www/google-eta-card.js  ← Leaflet custom card
```

### Cleanup of current broken setup:
1. Kill `google-eta` Docker container (`docker compose down`)
2. Delete `docker-compose.yml`, `service/` directory from project
3. Remove old Lovelace resources (v2, v3, inline, external URL)
4. Register new resource: `/local/google-eta-card.js` (type: module)
5. Update `dashboard-dev` Map view to use `custom:google-eta-card`

## Testing Strategy

1. **Idle state:** Both persons home → haversine <1km → sensors show "idle"
2. **Distance filter:** Persons >1km apart → API called, cached, direction data available
3. **Cooldown:** Trigger twice within 30 minutes → second returns cached
4. **Route overlay:** Click "Route to Wife" → polyline renders on Leaflet
5. **Toggle:** Click both routes → previous cleared, new one drawn
6. **Person click:** Click marker → Google Maps navigation opens in new tab
7. **Reload:** Card re-renders after HA refresh → markers positioned correctly

## Files

| File | Responsibility | Lines (est.) |
|---|---|---|
| `__init__.py` | Config entry, forward sensors | ~40 |
| `config_flow.py` | UI: API key + person picker | ~100 |
| `const.py` | Constants, attribute keys | ~40 |
| `coordinator.py` | Person listeners, haversine, Google API, cache | ~200 |
| `sensor.py` | Two sensor entities | ~150 |
| `google-eta-card.js` | Leaflet custom Lovelace card | ~400 |

**Total: ~930 lines of new code.**
