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

        if user_input is not None:
            try:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Google ETA",
                    data={},
                    options=user_input,
                )
            except Exception as err:
                _LOGGER.error("Config flow error: %s", err)
                errors["base"] = "unknown"

        person_entities = self._get_person_entities()

        if not person_entities:
            errors["base"] = "no_persons"
            person_entities = [("person.aditya_kunapuli", "Adi"), ("person.mrs_wife", "Wife")]

        schema = vol.Schema({
            vol.Required(CONF_API_KEY, default=""): str,
            vol.Required(CONF_PERSON_A, default=person_entities[0][0]): vol.In(person_entities),
            vol.Required(CONF_PERSON_B, default=person_entities[1][0] if len(person_entities) > 1 else person_entities[0][0]): vol.In(person_entities),
            vol.Required(CONF_COOLDOWN, default=DEFAULT_COOLDOWN): vol.All(vol.Coerce(int), vol.Range(min=60, max=86400)),
            vol.Required(CONF_THRESHOLD_M, default=DEFAULT_THRESHOLD_M): vol.All(vol.Coerce(int), vol.Range(min=100, max=100000)),
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    def _get_person_entities(self) -> list[tuple[str, str]]:
        """Collect available person entities."""
        entities = []
        for state in self.hass.states.async_all(PERSON_DOMAIN):
            if not state.entity_id.startswith(f"{PERSON_DOMAIN}."):
                continue
            name = state.name or state.entity_id
            entities.append((state.entity_id, name))
        return entities
