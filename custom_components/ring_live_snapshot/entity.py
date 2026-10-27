"""Shared base: entities sit on the camera's own device page."""

from __future__ import annotations

from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.entity import Entity

from . import LiveSnapshotConfigEntry


class LiveSnapshotEntity(Entity):
    _attr_has_entity_name = True

    def __init__(self, entry: LiveSnapshotConfigEntry, key: str) -> None:
        self.live = entry.runtime_data
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self.device_entry = async_entity_id_to_device(self.live.hass, self.live.entity_id)
        if self.device_entry is None:
            # Camera without a device: fall back to naming after the entry.
            self._attr_has_entity_name = False
            self._attr_name = f"{entry.title} live snapshot"
