from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LiveSnapshotConfigEntry
from .entity import LiveSnapshotEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: LiveSnapshotConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([LiveSnapshotButton(entry, "live_snapshot")])


class LiveSnapshotButton(LiveSnapshotEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self.live.async_capture()
