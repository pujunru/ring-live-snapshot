"""Ring Live Snapshot: grab a fresh frame from a camera's WebRTC live view on demand."""

from __future__ import annotations

import os

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, CONF_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .annotate import draw_boxes
from .const import (
    ATTR_CONFIDENCE,
    ATTR_CONFIRM,
    ATTR_FILENAME,
    ATTR_MIN_IOU,
    ATTR_MODEL_ID,
    ATTR_OBJECTS,
    ATTR_SETTLE,
    ATTR_SOURCE,
    ATTR_TIMEOUT,
    CONF_ROBOFLOW_API_KEY,
    DEFAULT_CONFIDENCE,
    DEFAULT_MIN_IOU,
    DEFAULT_SETTLE,
    DEFAULT_TIMEOUT,
    DOMAIN,
    SERVICE_CAPTURE,
    SERVICE_DETECT,
    SERVICE_DRAW_BOXES,
)
from .live import LiveCamera, async_get_live_camera
from .roboflow import RoboflowError, async_detect, confirm

PLATFORMS = [Platform.BUTTON, Platform.IMAGE]

# Cameras are set up in the UI; YAML only holds the optional Roboflow key
# (`roboflow_api_key: !secret roboflow_api_key`) so it stays out of traces.
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Optional(CONF_ROBOFLOW_API_KEY): cv.string})}, extra=vol.ALLOW_EXTRA
)

CAPTURE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_domain("camera"),
        vol.Optional(ATTR_FILENAME): cv.string,
        vol.Optional(ATTR_SETTLE, default=DEFAULT_SETTLE): vol.All(vol.Coerce(float), vol.Range(0, 10)),
        vol.Optional(ATTR_TIMEOUT, default=DEFAULT_TIMEOUT): vol.All(vol.Coerce(float), vol.Range(5, 120)),
    }
)

OBJECT_SCHEMA = vol.Schema(
    {
        vol.Required("box_2d"): vol.All(vol.Length(min=4, max=4), [vol.Coerce(float)]),
        vol.Optional("label", default=""): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

DRAW_BOXES_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SOURCE): cv.string,
        vol.Required(ATTR_FILENAME): cv.string,
        vol.Optional(ATTR_OBJECTS, default=[]): vol.All(cv.ensure_list, [OBJECT_SCHEMA]),
    }
)

DETECT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SOURCE): cv.string,
        vol.Required(ATTR_MODEL_ID): cv.string,
        vol.Optional(ATTR_CONFIDENCE, default=DEFAULT_CONFIDENCE): vol.All(vol.Coerce(float), vol.Range(0, 1)),
        vol.Optional(ATTR_FILENAME): cv.string,
        vol.Optional(ATTR_CONFIRM): vol.All(cv.ensure_list, [OBJECT_SCHEMA]),
        vol.Optional(ATTR_MIN_IOU, default=DEFAULT_MIN_IOU): vol.All(vol.Coerce(float), vol.Range(0, 1)),
    }
)

type LiveSnapshotConfigEntry = ConfigEntry[LiveCamera]


def _read(path: str) -> bytes:
    with open(path, "rb") as file:
        return file.read()


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as file:
        file.write(data)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    def check_path(path: str) -> None:
        if not hass.config.is_allowed_path(path):
            raise HomeAssistantError(f"{path} is not in allowlist_external_dirs or /media")

    async def capture(call: ServiceCall) -> ServiceResponse:
        entity_id: str = call.data[ATTR_ENTITY_ID]
        filename: str | None = call.data.get(ATTR_FILENAME)
        if filename:
            check_path(filename)
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

    async def draw(call: ServiceCall) -> ServiceResponse:
        source: str = call.data[ATTR_SOURCE]
        filename: str = call.data[ATTR_FILENAME]
        check_path(source)
        check_path(filename)
        drawn = await hass.async_add_executor_job(draw_boxes, source, filename, call.data[ATTR_OBJECTS])
        return {"filename": filename, "boxes": drawn}

    async def detect(call: ServiceCall) -> ServiceResponse:
        if not (api_key := config.get(DOMAIN, {}).get(CONF_ROBOFLOW_API_KEY)):
            raise HomeAssistantError(f"Set {CONF_ROBOFLOW_API_KEY} under {DOMAIN}: in configuration.yaml")
        source: str = call.data[ATTR_SOURCE]
        filename: str | None = call.data.get(ATTR_FILENAME)
        check_path(source)
        if filename:
            check_path(filename)
        jpeg = await hass.async_add_executor_job(_read, source)
        try:
            objects = await async_detect(
                async_get_clientsession(hass), api_key, call.data[ATTR_MODEL_ID], jpeg, call.data[ATTR_CONFIDENCE]
            )
        except RoboflowError as err:
            raise HomeAssistantError(str(err)) from err
        if filename:
            await hass.async_add_executor_job(draw_boxes, source, filename, objects)
        response = {"objects": objects, "count": len(objects), "filename": filename}
        if ATTR_CONFIRM in call.data:
            response["confirmed"] = confirm(call.data[ATTR_CONFIRM], objects, call.data[ATTR_MIN_IOU])
        return response

    hass.services.async_register(
        DOMAIN, SERVICE_CAPTURE, capture, schema=CAPTURE_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DRAW_BOXES,
        draw,
        schema=DRAW_BOXES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DETECT, detect, schema=DETECT_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LiveSnapshotConfigEntry) -> bool:
    entry.runtime_data = async_get_live_camera(hass, entry.data[CONF_ENTITY_ID])
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LiveSnapshotConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
