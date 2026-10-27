from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LiveSnapshotConfigEntry
from .entity import LiveSnapshotEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: LiveSnapshotConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([LiveSnapshotImage(hass, entry)])


class LiveSnapshotImage(LiveSnapshotEntity, ImageEntity):
    """The last live frame; its state is the capture time."""

    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, entry: LiveSnapshotConfigEntry) -> None:
        ImageEntity.__init__(self, hass)
        LiveSnapshotEntity.__init__(self, entry, "live_snapshot")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._update_from_live()
        self.async_on_remove(self.live.async_add_listener(self._handle_capture))

    @callback
    def _update_from_live(self) -> None:
        self._attr_image_last_updated = self.live.captured_at
        if self.live.frame:
            self._attr_extra_state_attributes = {
                "width": self.live.frame.width,
                "height": self.live.frame.height,
                "seconds": self.live.frame.seconds,
            }

    @callback
    def _handle_capture(self) -> None:
        self._update_from_live()
        self._cached_image = None
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        return self.live.frame.jpeg if self.live.frame else None
