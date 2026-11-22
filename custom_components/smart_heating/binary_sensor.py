"""Per-radiator open/closed and the boiler decision."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .controller import radiator_output_w
from .entity import SmartHeatingEntity, slug


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coord = entry.runtime_data
    entities: list[BinarySensorEntity] = [BoilerWanted(coord)]
    entities += [RadiatorOpen(coord, name) for name in coord.config.radiators]
    async_add_entities(entities)


class BoilerWanted(SmartHeatingEntity, BinarySensorEntity):
    _attr_name = "Boiler"
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, coord) -> None:
        super().__init__(coord, "boiler")

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.decision
        return d.boiler_on if d else None


class RadiatorOpen(SmartHeatingEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.OPENING
    _attr_icon = "mdi:radiator"

    def __init__(self, coord, radiator: str) -> None:
        super().__init__(coord, f"radiator_{slug(radiator)}")
        self.radiator = radiator
        self._attr_name = f"{radiator} open"

    @property
    def is_on(self) -> bool | None:
        d = self.coordinator.decision
        return self.radiator in d.open if d else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        d = c.decision
        rad = c.config.radiators.get(self.radiator)
        if rad is None:
            return {}
        flow = c._flow_temp()
        room = next((c._reading(z.thermometer).temp for z in c.config.zones.values()
                     if self.radiator in z.radiators), None) or c.config.settings.default_room_temp
        pending = c.pending.get(self.radiator)
        return {
            "reason": d.radiator_reasons.get(self.radiator) if d else None,
            "modelled_kw": round(radiator_output_w(rad.rated_w, flow, c.config.settings.drop, room,
                                                   rad.exponent) / 1000, 2),
            "trv": rad.trv,
            "faulty": self.radiator in c.faulty,
            "awaiting_check": pending.attempts if pending else 0,
        }
