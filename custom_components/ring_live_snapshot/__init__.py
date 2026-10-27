"""Ring Live Snapshot: grab a fresh frame from a camera's WebRTC live view on demand."""

from __future__ import annotations

import os

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, CONF_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_FILENAME,
    ATTR_SETTLE,
    ATTR_TIMEOUT,
    DEFAULT_SETTLE,
    DEFAULT_TIMEOUT,
    DOMAIN,
    SERVICE_CAPTURE,
)
from .live import LiveCamera, async_get_live_camera

PLATFORMS = [Platform.BUTTON, Platform.IMAGE]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CAPTURE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_domain("camera"),
        vol.Optional(ATTR_FILENAME): cv.string,
        vol.Optional(ATTR_SETTLE, default=DEFAULT_SETTLE): vol.All(vol.Coerce(float), vol.Range(0, 10)),
        vol.Optional(ATTR_TIMEOUT, default=DEFAULT_TIMEOUT): vol.All(vol.Coerce(float), vol.Range(5, 120)),
    }
)

type LiveSnapshotConfigEntry = ConfigEntry[LiveCamera]


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as file:
        file.write(data)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def capture(call: ServiceCall) -> ServiceResponse:
        entity_id: str = call.data[ATTR_ENTITY_ID]
        filename: str | None = call.data.get(ATTR_FILENAME)
        if filename and not hass.config.is_allowed_path(filename):
            raise HomeAssistantError(f"{filename} is not in allowlist_external_dirs or /media")
        live = async_get_live_camera(hass, entity_id)
        frame = await live.async_capture(call.data[ATTR_SETTLE], call.data[ATTR_TIMEOUT])
        if filename:
            await hass.async_add_executor_job(_write, filename, frame.jpeg)
        return {
            "entity_id": entity_id,
            "filename": filename,
            "width": frame.width,
            "height": frame.height,
            "seconds": frame.seconds,
            "captured_at": live.captured_at.isoformat(),
        }

    hass.services.async_register(
        DOMAIN, SERVICE_CAPTURE, capture, schema=CAPTURE_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LiveSnapshotConfigEntry) -> bool:
    entry.runtime_data = async_get_live_camera(hass, entry.data[CONF_ENTITY_ID])
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LiveSnapshotConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
