"""Per-camera state: one live session at a time, and the last frame captured."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from homeassistant.components.camera.helper import get_camera_from_entity_id
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DEFAULT_SETTLE, DEFAULT_TIMEOUT, DOMAIN
from .grabber import Frame, async_grab_frame


class LiveCamera:
    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.frame: Frame | None = None
        self.captured_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    async def async_capture(self, settle: float = DEFAULT_SETTLE, timeout: float = DEFAULT_TIMEOUT) -> Frame:
        # A battery camera serves one live view at a time; queue overlapping
        # requests instead of opening parallel sessions.
        async with self._lock:
            camera = get_camera_from_entity_id(self.hass, self.entity_id)
            frame = await async_grab_frame(camera, settle=settle, timeout=timeout)
            self.frame = frame
            self.captured_at = dt_util.utcnow()
        for listener in self._listeners:
            listener()
        return frame

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)


@callback
def async_get_live_camera(hass: HomeAssistant, entity_id: str) -> LiveCamera:
    cameras: dict[str, LiveCamera] = hass.data.setdefault(DOMAIN, {})
    if entity_id not in cameras:
        cameras[entity_id] = LiveCamera(hass, entity_id)
    return cameras[entity_id]
