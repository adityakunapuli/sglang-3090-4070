"""Data coordinator for google_eta integration."""

import json
import logging
import math
import time
import urllib.request
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_API_KEY,
    CONF_PERSON_A,
    CONF_PERSON_B,
    CONF_COOLDOWN,
    CONF_THRESHOLD_M,
    DEFAULT_COOLDOWN,
    DEFAULT_THRESHOLD_M,
    DOMAIN,
    ROUTE_A_TO_B,
    ROUTE_B_TO_A,
    SOURCE_FRESH,
    SOURCE_CACHED,
    SOURCE_IDLE,
    ATTR_DISTANCE_M,
    ATTR_SOURCE,
    ATTR_PERSON_A,
    ATTR_PERSON_B,
    ATTR_PERSON_A_COORDS,
    ATTR_PERSON_B_COORDS,
    ATTR_DURATION_TEXT,
    ATTR_DISTANCE_TEXT,
    ATTR_POLYLINE,
)

_LOGGER = logging.getLogger(__name__)


async def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in meters between two lat/lon points."""
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class ETADataCoordinator:
    """Coordinate ETA data for the google_eta integration."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]):
        self._hass = hass
        self._api_key = config[CONF_API_KEY]
        self._person_a = config[CONF_PERSON_A]
        self._person_b = config[CONF_PERSON_B]
        self._cooldown = config.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)
        self._threshold_m = config.get(CONF_THRESHOLD_M, DEFAULT_THRESHOLD_M)

        self._cache = {ROUTE_A_TO_B: None, ROUTE_B_TO_A: None}
        self._last_call = 0.0
        self._last_distance = 0.0
        self._person_a_coords: tuple[float, float] | None = None
        self._person_b_coords: tuple[float, float] | None = None
        self._person_a_state: str | None = None
        self._person_b_state: str | None = None
        self._sensors: list[Any] = []  # Sensors registered for notification

    def register_sensor(self, sensor: Any) -> None:
        """Register a sensor for update notifications."""
        self._sensors.append(sensor)

    def _notify_sensors(self) -> None:
        """Notify all registered sensors of update."""
        data = self._build_data()
        for sensor in self._sensors:
            sensor._update_state(data)
            sensor.async_write_ha_state()

    async def async_setup(self) -> None:
        """Set up person listeners and run initial computation."""
        async_track_state_change_event(
            self._hass,
            [self._person_a, self._person_b],
            self._on_person_change,
        )
        await self._compute()

    def get_data(self) -> dict:
        """Return current ETA data dict."""
        return self._build_data()

    def _build_data(self) -> dict:
        """Build data dict from current state."""
        coords_a = self._person_a_coords
        coords_b = self._person_b_coords
        dist = self._last_distance

        if coords_a and coords_b and dist >= self._threshold_m:
            if time.time() - self._last_call < self._cooldown:
                source = SOURCE_CACHED
            elif self._cache[ROUTE_A_TO_B]:
                source = SOURCE_FRESH
            else:
                source = SOURCE_IDLE
        else:
            source = SOURCE_IDLE

        base = {
            ATTR_SOURCE: source,
            ATTR_DISTANCE_M: round(dist),
            ATTR_PERSON_A: self._get_person_name(self._person_a),
            ATTR_PERSON_B: self._get_person_name(self._person_b),
            ATTR_PERSON_A_COORDS: list(coords_a) if coords_a else None,
            ATTR_PERSON_B_COORDS: list(coords_b) if coords_b else None,
        }

        if self._cache[ROUTE_A_TO_B]:
            dir_ab = {
                ATTR_DURATION_TEXT: self._cache[ROUTE_A_TO_B].get("duration_text"),
                ATTR_DISTANCE_TEXT: self._cache[ROUTE_A_TO_B].get("distance_text"),
                ATTR_POLYLINE: self._cache[ROUTE_A_TO_B].get("polyline"),
            }
        else:
            dir_ab = None

        if self._cache[ROUTE_B_TO_A]:
            dir_ba = {
                ATTR_DURATION_TEXT: self._cache[ROUTE_B_TO_A].get("duration_text"),
                ATTR_DISTANCE_TEXT: self._cache[ROUTE_B_TO_A].get("distance_text"),
                ATTR_POLYLINE: self._cache[ROUTE_B_TO_A].get("polyline"),
            }
        else:
            dir_ba = None

        return {
            **base,
            "dir_ab": dir_ab,
            "dir_ba": dir_ba,
        }

    async def _on_person_change(self, event: Any) -> None:
        """Handle person state change."""
        await self._compute()

    async def _compute(self) -> None:
        """Compute ETA based on current person locations."""
        state_a = self._hass.states.get(self._person_a)
        state_b = self._hass.states.get(self._person_b)

        if not state_a or not state_b:
            self._person_a_state = None
            self._person_b_state = None
            self._person_a_coords = None
            self._person_b_coords = None
            _LOGGER.warning("Person entities not available")
            self._last_distance = 0
            self._notify_sensors()
            return

        self._person_a_state = state_a.state
        self._person_b_state = state_b.state

        lat_a = state_a.attributes.get("latitude")
        lon_a = state_a.attributes.get("longitude")
        lat_b = state_b.attributes.get("latitude")
        lon_b = state_b.attributes.get("longitude")

        try:
            lat_a = float(lat_a) if lat_a else None
            lon_a = float(lon_a) if lon_a else None
            lat_b = float(lat_b) if lat_b else None
            lon_b = float(lon_b) if lon_b else None
        except (TypeError, ValueError):
            lat_a = lon_a = lat_b = lon_b = None

        self._person_a_coords = (lat_a, lon_a) if lat_a and lon_a else None
        self._person_b_coords = (lat_b, lon_b) if lat_b and lon_b else None

        # Idle if at home, unknown, or missing coords
        if self._person_a_state in ("home", "unknown", "not_home") or self._person_b_state in ("home", "unknown", "not_home"):
            _LOGGER.debug("One or both persons at home/unknown — idle")
            self._last_distance = 0
            self._notify_sensors()
            return

        if not self._person_a_coords or not self._person_b_coords:
            _LOGGER.debug("Missing coordinates — idle")
            self._last_distance = 0
            self._notify_sensors()
            return

        distance = await haversine(lat_a, lon_a, lat_b, lon_b)
        self._last_distance = distance

        if distance < self._threshold_m:
            _LOGGER.debug("Distance %.1fm < threshold %.1fm — idle", distance, self._threshold_m)
            self._notify_sensors()
            return

        _LOGGER.debug("Distance %.1fm >= threshold %.1fm", distance, self._threshold_m)

        now = time.time()
        if now - self._last_call < self._cooldown:
            _LOGGER.debug("Cooldown %.1fs remaining", self._cooldown - (now - self._last_call))
            self._cache[ROUTE_A_TO_B] = self._cache.get(ROUTE_A_TO_B)
            self._cache[ROUTE_B_TO_A] = self._cache.get(ROUTE_B_TO_A)
            self._notify_sensors()
            return

        await self._fetch_directions(lat_a, lon_a, lat_b, lon_b)
        self._notify_sensors()

    async def _fetch_directions(self, lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> None:
        """Call Google Directions API for both directions in parallel."""
        _LOGGER.info("Fetching Google Directions (A→B: %.5f,%.5f → B: %.5f,%.5f)", lat_a, lon_a, lat_b, lon_b)

        result_ab = await self._hass.async_add_executor_job(
            self._call_api, lat_a, lon_a, lat_b, lon_b
        )
        result_ba = await self._hass.async_add_executor_job(
            self._call_api, lat_b, lon_b, lat_a, lon_a
        )

        self._cache[ROUTE_A_TO_B] = result_ab.get("route")
        self._cache[ROUTE_B_TO_A] = result_ba.get("route")
        self._last_call = time.time()

        status_ab = result_ab.get("status", "unknown")
        status_ba = result_ba.get("status", "unknown")
        if status_ab != "OK":
            _LOGGER.warning("Google API A→B: %s", status_ab)
        if status_ba != "OK":
            _LOGGER.warning("Google API B→A: %s", status_ba)

    def _call_api(self, origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float) -> dict:
        """Synchronous call to Google Directions API (runs in executor)."""
        url = (
            f"https://maps.googleapis.com/maps/api/directions/json"
            f"?origin={origin_lat},{origin_lon}"
            f"&destination={dest_lat},{dest_lon}"
            f"&mode=driving"
            f"&key={self._api_key}"
        )
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            _LOGGER.error("Google API error: %s", e)
            return {"status": "error", "route": None}

        if data.get("status") != "OK":
            return {"status": data.get("status", "error"), "route": None}

        try:
            route = data["routes"][0]
            leg = route["legs"][0]
            route_data = {
                "duration_text": leg["duration"]["text"],
                "duration_value": leg["duration"]["value"],
                "distance_text": leg["distance"]["text"],
                "distance_value": leg["distance"]["value"],
                "polyline": route["overview_polyline"]["points"],
            }
            return {"status": "OK", "route": route_data}
        except (KeyError, IndexError) as e:
            _LOGGER.error("Parsing Google API response: %s", e)
            return {"status": "error", "route": None}

    def _get_person_name(self, entity_id: str) -> str:
        """Get person display name from entity."""
        state = self._hass.states.get(entity_id)
        if state and state.name:
            return state.name
        return entity_id.split(".", 1)[1] if "." in entity_id else entity_id
