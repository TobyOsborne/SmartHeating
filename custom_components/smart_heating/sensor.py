"""Capacity, reason and per-zone status sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import SmartHeatingEntity, slug

ZONE_STATES = ["heating", "satisfied", "waiting", "idle", "window", "frost", "offline", "boost", "minimum"]


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coord = entry.runtime_data
    entities: list[SensorEntity] = [CapacitySensor(coord), ReasonSensor(coord)]
    entities += [ZoneSensor(coord, name) for name in coord.config.zones]
    async_add_entities(entities)


class CapacitySensor(SmartHeatingEntity, SensorEntity):
    _attr_name = "Open capacity"
    _attr_icon = "mdi:radiator"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_suggested_display_precision = 2

    def __init__(self, coord) -> None:
        super().__init__(coord, "open_capacity")

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.decision
        return round(d.capacity_w / 1000, 2) if d else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.decision
        if not d:
            return {}
        return {"needed_kw": round(d.needed_w / 1000, 2), "shortfall_kw": round(d.shortfall_w / 1000, 2)}


class ReasonSensor(SmartHeatingEntity, SensorEntity):
    _attr_name = "Reason"
    _attr_icon = "mdi:comment-question-outline"

    def __init__(self, coord) -> None:
        super().__init__(coord, "reason")

    @property
    def native_value(self) -> str | None:
        d = self.coordinator.decision
        if not d:
            return None
        prefix = "[dry run] " if self.coordinator.runtime.dry_run else ""
        return (prefix + d.reason)[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        d = c.decision
        return {
            "boiler_wanted": d.boiler_on if d else None,
            "dry_run": c.runtime.dry_run,
            "mode": c.runtime.mode.value,
            "profile": c.runtime.profile,
            "open_radiators": sorted(d.open) if d else [],
            "held_radiators": sorted(d.held) if d else [],
            "faulty_radiators": sorted(c.faulty),
            "overrides": [
                {"zone": o.zone, "target": o.target, "position": o.position, "expires_at": o.expires_at}
                for o in c.runtime.overrides
            ],
            "recent_notifications": c.recent_notifications[:10],
        }


class ZoneSensor(SmartHeatingEntity, SensorEntity):
    _attr_icon = "mdi:home-thermometer-outline"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ZONE_STATES

    def __init__(self, coord, zone: str) -> None:
        super().__init__(coord, f"zone_{slug(zone)}")
        self.zone = zone
        self._attr_name = zone

    @property
    def native_value(self) -> str | None:
        c = self.coordinator
        d = c.decision
        if not d:
            return None
        if any(o.zone == self.zone for o in c.runtime.overrides):
            return "boost"
        return d.zone_states.get(self.zone, "idle")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        zone = c.config.zones.get(self.zone)
        if zone is None:
            return {}
        reading = c._reading(zone.thermometer)
        profile = c.config.profiles.get(c.runtime.profile)
        target = None
        position = None
        if profile:
            if profile.primary_zone == self.zone:
                target, position = profile.goal, "primary"
            else:
                for i, e in enumerate(profile.priority):
                    if e.zone == self.zone:
                        target, position = e.target, i + 1
        return {"temperature": reading.temp, "target": target, "list_position": position,
                "radiators": zone.radiators, "thermometer": zone.thermometer,
                "minimum": zone.minimum,
                "ignored_readings": (f.rejected if (f := c.filters.get(zone.thermometer)) else 0),
                "last_ignored": f.last_rejected if f else None}
