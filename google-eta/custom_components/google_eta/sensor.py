"""Sensor platform for the google_eta integration."""

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_API_KEY,
    CONF_PERSON_A,
    CONF_PERSON_B,
    CONF_COOLDOWN,
    CONF_THRESHOLD_M,
    SENSOR_A_TO_B,
    SENSOR_B_TO_A,
    SOURCE_IDLE,
    ATTR_SOURCE,
    ATTR_DISTANCE_M,
    ATTR_PERSON_A,
    ATTR_PERSON_B,
    ATTR_PERSON_A_COORDS,
    ATTR_PERSON_B_COORDS,
    ATTR_DURATION_TEXT,
    ATTR_DISTANCE_TEXT,
    ATTR_POLYLINE,
    ATTR_DIRECTION,
)
from .coordinator import ETADataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up google_eta sensors."""
    options = dict(entry.options)
    config: dict = {
        CONF_API_KEY: options[CONF_API_KEY],
        CONF_PERSON_A: options[CONF_PERSON_A],
        CONF_PERSON_B: options[CONF_PERSON_B],
        CONF_COOLDOWN: options.get(CONF_COOLDOWN, 1800),
        CONF_THRESHOLD_M: options.get(CONF_THRESHOLD_M, 1000),
    }

    coordinator = ETADataCoordinator(hass, config)
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}

    def _name_for(val: str) -> str:
        if "." in val:
            return val.split(".", 1)[1]
        return val

    state_a = hass.states.get(options[CONF_PERSON_A])
    state_b = hass.states.get(options[CONF_PERSON_B])
    name_a = (state_a and state_a.name) or _name_for(options[CONF_PERSON_A])
    name_b = (state_b and state_b.name) or _name_for(options[CONF_PERSON_B])

    sensor_a_to_b = EtaSensor(coordinator, name_a, name_b, SENSOR_A_TO_B, "from_a_to_b")
    sensor_b_to_a = EtaSensor(coordinator, name_b, name_a, SENSOR_B_TO_A, "from_b_to_a")

    coordinator.register_sensor(sensor_a_to_b)
    coordinator.register_sensor(sensor_b_to_a)

    async_add_entities([sensor_a_to_b, sensor_b_to_a])

    coordinator._notify_sensors()


class EtaSensor(SensorEntity):
    """Sensor representing a single ETA direction."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: ETADataCoordinator,
        name_a: str,
        name_b: str,
        unique_suffix: str,
        direction_label: str,
    ) -> None:
        self.coordinator = coordinator
        self._name_a = name_a
        self._name_b = name_b
        self._direction_label = direction_label
        self._attr_unique_id = f"{DOMAIN}_{unique_suffix}"
        self._attr_name = f"{name_a} to {name_b} ETA"
        self._attr_native_value = "idle"
        self._attr_extra_state_attributes = {
            "type": "driving_eta",
            ATTR_SOURCE: SOURCE_IDLE,
        }
        self._data: dict | None = None

    @callback
    def _update_state(self, data: dict) -> None:
        """Update sensor from coordinator data."""
        self._data = data
        source = data.get(ATTR_SOURCE, SOURCE_IDLE)
        dist_m = data.get(ATTR_DISTANCE_M, 0)

        if source == SOURCE_IDLE:
            self._attr_native_value = "idle"
            self._attr_extra_state_attributes = {
                "type": "driving_eta",
                ATTR_SOURCE: SOURCE_IDLE,
                ATTR_DISTANCE_M: dist_m or 0,
            }
            return

        if self._direction_label == "from_a_to_b":
            dir_data = data.get("dir_ab")
        else:
            dir_data = data.get("dir_ba")

        duration_text = "?? min"
        distance_text = "??"
        polyline = None

        if dir_data:
            duration_text = dir_data.get(ATTR_DURATION_TEXT) or "?? min"
            distance_text = dir_data.get(ATTR_DISTANCE_TEXT) or "??"
            polyline = dir_data.get(ATTR_POLYLINE)

        self._attr_native_value = duration_text
        self._attr_extra_state_attributes = {
            "type": "driving_eta",
            ATTR_SOURCE: source,
            ATTR_DISTANCE_M: dist_m,
            ATTR_PERSON_A: data.get(ATTR_PERSON_A),
            ATTR_PERSON_B: data.get(ATTR_PERSON_B),
            ATTR_PERSON_A_COORDS: data.get(ATTR_PERSON_A_COORDS),
            ATTR_PERSON_B_COORDS: data.get(ATTR_PERSON_B_COORDS),
            ATTR_DURATION_TEXT: duration_text,
            ATTR_DISTANCE_TEXT: distance_text,
            ATTR_DIRECTION: {
                ATTR_DURATION_TEXT: duration_text,
                ATTR_DISTANCE_TEXT: distance_text,
                ATTR_POLYLINE: polyline,
            },
        }

    @property
    def available(self) -> bool:
        return True
