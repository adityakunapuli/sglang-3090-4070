"""Config flow for google_eta integration."""

import logging

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.components.person import DOMAIN as PERSON_DOMAIN
from homeassistant.data_entry_flow import FlowResult
import voluptuous as vol

from .const import (
    DOMAIN,
    CONF_API_KEY,
    CONF_PERSON_A,
    CONF_PERSON_B,
    CONF_COOLDOWN,
    CONF_THRESHOLD_M,
    DEFAULT_COOLDOWN,
    DEFAULT_THRESHOLD_M,
)

_LOGGER = logging.getLogger(__name__)


class GoogleEtaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}

        person_options = self._get_person_options()

        if not person_options:
            errors["base"] = "no_persons"

        if user_input is not None:
            try:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                resolved = {
                    CONF_API_KEY: user_input[CONF_API_KEY],
                    CONF_PERSON_A: user_input[CONF_PERSON_A],
                    CONF_PERSON_B: user_input[CONF_PERSON_B],
                    CONF_COOLDOWN: user_input[CONF_COOLDOWN],
                    CONF_THRESHOLD_M: user_input[CONF_THRESHOLD_M],
                }
                return self.async_create_entry(
                    title="Google ETA",
                    data={},
                    options=resolved,
                )
            except Exception as err:
                _LOGGER.error("Config flow error: %s", err, exc_info=True)
                errors["base"] = "unknown"

        entity_id_default = person_options[0] if person_options else "person.aditya_kunapuli"
        entity_id_default_b = person_options[1] if len(person_options) > 1 else entity_id_default

        schema = vol.Schema({
            vol.Required(CONF_API_KEY, default=""): str,
            vol.Required(CONF_PERSON_A, default=entity_id_default): vol.In(person_options),
            vol.Required(CONF_PERSON_B, default=entity_id_default_b): vol.In(person_options),
            vol.Required(CONF_COOLDOWN, default=DEFAULT_COOLDOWN): vol.All(vol.Coerce(int), vol.Range(min=60, max=86400)),
            vol.Required(CONF_THRESHOLD_M, default=DEFAULT_THRESHOLD_M): vol.All(vol.Coerce(int), vol.Range(min=100, max=100000)),
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    def _get_person_options(self) -> list[str]:
        """Collect person entity_ids that have GPS coordinates."""
        entity_ids = []
        for state in self.hass.states.async_all(PERSON_DOMAIN):
            if not state.entity_id.startswith(f"{PERSON_DOMAIN}."):
                continue
            lat = state.attributes.get("latitude")
            lon = state.attributes.get("longitude")
            if lat is None or lon is None:
                continue
            try:
                float(lat)
                float(lon)
            except (TypeError, ValueError):
                continue
            entity_ids.append(state.entity_id)
        entity_ids.sort()
        return entity_ids
