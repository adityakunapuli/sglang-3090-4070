# google-eta Design: On-Demand ETA + Route Overlay

> Two people, two directions, one lightweight integration.

**Date:** 2026-07-24
**Status:** Design approved
**Location:** `/mnt/data/docker/google-eta/`

## Problem

Two people live in the house (Adi + Wife/`person.aditya_kunapuli` + `person.mrs_wife`). Both have GPS tracking enabled in Home Assistant. Currently:

- The `map-locations` dashboard uses `google-map-card` (bloated, expensive) which already provides a `travel_panel` with ETA shortcuts.
- The `dashboard-dev` Map view uses the native HA `map` card with a `config-template-card` button that opens Google Maps with the wife's coords.
- No way to see on-screen ETA or route visualization without opening Google Maps externally.

## Goal

Replace the native map card in `dashboard-dev/Map` view with a custom Lovelace card that shows:
- Person location markers (clickable to navigate)
- On-demand ETA between the two people
- Optional driving route overlay on the map (toggle between both directions)

**Only** when the two people are >1km apart (haversine distance check).
**Only** the Google **Directions API** is called (not full Maps JS API, not Geocoding, not Places).

## Architecture

```
google-eta/
├── app/
│   ├── appdaemon.yaml         # AppDaemon integration config
│   └── google_eta.py          # Main AppDaemon app
├── www/
│   └── google-eta-card/
│       └── google-eta-card.js # Custom Lovelace card
└── README.md
```

### Component A: AppDaemon App (`app/google_eta.py`)

**Purpose:** Listen for person location changes, compute haversine distance, call Google Directions API, expose results via internal REST.

**Triggers:**
- `listen_state()` on `person.aditya_kunapuli` AND `person.mrs_wife`
- Duration filter: 30 seconds (person must stay in new state for 30s to trigger — prevents transient zone-crossing spam)

**Filter logic:**
1. Fetch `latitude`/`longitude` from both persons' state attributes
2. Compute haversine distance
3. **If < 1000m:** Return "idle" (no API call, no cooldown restart)
4. **If > 1000m AND cooldown expired (>30min since last API call):** Call Directions API

**Google Directions API Request:**
```
https://maps.googleapis.com/maps/api/directions/json
?origin={latA},{lonA}
&destination={latB},{lonB}
&mode=driving
&key={api_key}
```

**Response stored:**
- `direction` enum: `"adi_to_wife"` or `"wife_to_adi"`
- `duration`: `{ text: "23 min", value: 1380 }` (seconds)
- `distance`: `{ text: "14.2 mi", value: 22850 }` (meters)
- `polyline`: encoded route string from `overview_polyline.points`
- `steps`: array of turn-by-turn instructions (text)
- `timestamp`: Unix epoch of the API call

**Cooldown:** 1800 seconds (30 minutes) — calculated ETA doesn't change enough to justify frequent API calls.

**Idle state:** When distance < 1km, the REST API returns `source: "idle"`.

**REST endpoint:** `GET http://127.0.0.1:5000/api/google-eta` (and public path if HA front-proxied)
- AppDaemon spins up a lightweight `http.server.HTTPServer` (Python stdlib, single file, ~40 lines)
- Listens on localhost:5000, serves JSON responses
- HA's `http` component or external proxy can expose it; the card communicates via `http://host_ip:5000/api/google-eta`
- Query param `?direction=adi_to_wife|wife_to_adi` returns cached result for that specific direction
- If no cached result exists for requested direction, returns `{source: "not_calculated", suggestion: "call_api"}` — card then triggers calculation

**Data persistence:** `self.save_state()` — AppDaemon's built-in state persistence survives restarts.

### Component B: Custom Lovelace Card (`www/google-eta-card/google-eta-card.js`)

**Purpose:** Embedded Leaflet map + route overlay + ETA display. Replaces the native HA `map` card.

**Structure:**

```
┌─────────────────────────────────┐
│                                 │
│         Leaflet Map             │
│   (person markers + routes)     │
│                                 │
│                                 │
├─────────────────────────────────┤
│ Together at home · 0 min        │  ← Idle state
└─────────────────────────────────┘

┌─────────────────────────────────┐
│                                 │
│         Leaflet Map             │
│   (person markers + routes)     │
│                                 │
├─────────────────────────────────┤
│ (23 min, 14.2 mi)               │ ← Idle state
│ [Route to Wife] [Route to Me] [Clear]│
└─────────────────────────────────┘
```

**Map features:**
- Leaflet.map initialized on card mount
- Person markers: `L.marker([lat, lon]).bindPopup(name)` — clicking opens Google Maps nav
- Route layer: `L.layerGroup()` — cleared and repopulated when user toggles routes
- Fit bounds to route when route is shown

**Person marker click behavior:**
```javascript
marker.on('click', () => {
  const otherPerson = personA === this._personName ? personB : personA
  const url = `https://www.google.com/maps/dir/?api=1&origin=${latA},${lonA}&destination=${otherLat},${otherLon}`
  window.open(url, '_blank')
})
```

**Button interaction:**
- Tapping "Route to Wife" → fetches `GET /api/google-eta?direction=adi_to_wife` → decodes polyline → draws on map
- Tapping "Route to Me" → fetches `GET /api/google-eta?direction=wife_to_adi` → decodes polyline → draws on map
- Tapping "Clear" → clears `routeLayer`, shows buttons again
- Only one route visible at a time (toggle, not simultaneous)

**Route styling:**
- Adi → Wife: `color: '#4285F4'` (blue), `weight: 4`, `opacity: 0.8`
- Wife → Adi: `color: '#EA4335'` (red), `weight: 4`, `opacity: 0.8`
- Route overlay sits on top of person markers (z-order controlled via `addTo()` order)

**Leaflet library:** Loaded from CDN (`https://unpkg.com/leaflet@1.9.4/dist/leaflet.js`, `leaflet.css`). No bundler needed.

**Polyline decoding:** Use `google-polyline` NPM module (or inline decode function). Single utility function < 50 lines.

**No dependency on HA map internals:** Card is fully self-contained. No hooks into HA's native map component.

### Component C: HA Configuration

**secrets.yaml entry:**
```yaml
google_maps_api_key: AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

**Custom UI resource registration** (in HA config or via dashboard setup):
```yaml
resources:
  - url: /local/google-eta-card/google-eta-card.js
    type: module
```

**Card placement in dashboard-dev:**
The card replaces the native `map` card in the Map view section. It takes the full width (columns: "full") of a new section below any existing cards.

**Dashboard section:**
```json
{
  "type": "grid",
  "cards": [{
    "type": "custom:google-eta-card",
    "grid_options": {"columns": "full", "rows": 6}
  }]
}
```

## Constraints

- **No full HA custom integration** — AppDaemon is sufficient for a single script
- **No polling** — event-driven callbacks only
- **No Google Geocoding API** — HA provides lat/lon directly
- **No Google Places API** — not needed
- **No google-map-card dependency** — self-contained Leaflet card
- **Haversine distance check** prevents API calls when both are at home
- **30-minute cooldown** between API calls
- **Directions API only** — cheapest Google Maps API for this use case

## What This Does NOT Do (Out of Scope)

- Live route tracking (just the static calculated route)
- Real-time ETA updates while driving (only recalculated on person state change)
- Multiple route alternatives (just the fastest driving route)
- Walk/transit/cycling modes (driving only)
- Overlap comparison (two routes never shown simultaneously — one-at-a-time toggle)

## Testing Strategy

1. **Test idle state:** Both persons have `home` state → check haversine < 1km → API not called
2. **Test distance filter:** Both persons at known locations > 1km apart → API called, result cached
3. **Test cooldown:** Trigger twice within 30 minutes → second call returns cached result
4. **Test route overlay:** Click "Route to Wife" → decoded polyline renders on Leaflet
5. **Test toggle:** Click "Route to Wife" then "Route to Me" → previous route cleared, new one drawn
6. **Test person click:** Click a person marker → opens Google Maps in new tab with nav directions
7. **Test reload:** Card re-renders after HA refresh → markers positioned correctly

## Files

| File | Responsibility | Lines (est.) |
|------|---------------|--------------|
| `app/google_eta.py` | AppDaemon app: state listeners, haversine, Directions API, REST endpoint | ~250 |
| `app/appdaemon.yaml` | AppDaemon integration registration | ~20 |
| `www/google-eta-card/google-eta-card.js` | Custom Lovelace card: Leaflet init, markers, route overlay, buttons | ~350 |
| `README.md` | Setup instructions and project overview | ~80 |

**Total: ~700 lines of new code.**
