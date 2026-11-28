"""Profile and mode selects."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import SmartHeatingEntity
from .model import Mode

MODE_LABELS = {Mode.AUTO: "Auto", Mode.MANUAL: "Manual", Mode.OFF: "Off"}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coord = entry.runtime_data
    async_add_entities([ProfileSelect(coord), ModeSelect(coord)])


class ProfileSelect(SmartHeatingEntity, SelectEntity):
    _attr_name = "Profile"
    _attr_icon = "mdi:home-thermometer"

    def __init__(self, coord) -> None:
        super().__init__(coord, "profile")

    @property
    def options(self) -> list[str]:
        return list(self.coordinator.config.profiles)

    @property
    def current_option(self) -> str | None:
        return self.coordinator.runtime.profile

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_profile(option)


class ModeSelect(SmartHeatingEntity, SelectEntity):
    _attr_name = "Mode"
    _attr_icon = "mdi:radiator"
    _attr_options = list(MODE_LABELS.values())

    def __init__(self, coord) -> None:
        super().__init__(coord, "mode")

    @property
    def current_option(self) -> str | None:
        return MODE_LABELS[self.coordinator.runtime.mode]

    async def async_select_option(self, option: str) -> None:
        mode = next(m for m, label in MODE_LABELS.items() if label == option)
        await self.coordinator.async_set_mode(mode)
