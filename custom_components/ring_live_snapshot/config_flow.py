"""Config flow: pick a camera that offers a WebRTC live view."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.camera.helper import get_camera_from_entity_id
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .const import DOMAIN
from .grabber import supports_webrtc

SCHEMA = vol.Schema({vol.Required(CONF_ENTITY_ID): EntitySelector(EntitySelectorConfig(domain="camera"))})


class LiveSnapshotConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input[CONF_ENTITY_ID]
            await self.async_set_unique_id(entity_id)
            self._abort_if_unique_id_configured()
            try:
                camera = get_camera_from_entity_id(self.hass, entity_id)
            except HomeAssistantError:
                errors["base"] = "camera_unavailable"
            else:
                if supports_webrtc(camera):
                    state = self.hass.states.get(entity_id)
                    title = state.name if state else entity_id
                    return self.async_create_entry(title=title, data={CONF_ENTITY_ID: entity_id})
                errors["base"] = "no_webrtc"
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)
