"""Dry-run switch: on = decide and display only, send nothing."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import SmartHeatingEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([DryRunSwitch(entry.runtime_data)])


class DryRunSwitch(SmartHeatingEntity, SwitchEntity):
    _attr_name = "Dry run"
    _attr_icon = "mdi:test-tube"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coord) -> None:
        super().__init__(coord, "dry_run")

    @property
    def is_on(self) -> bool:
        return self.coordinator.runtime.dry_run

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_dry_run(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_dry_run(False)
