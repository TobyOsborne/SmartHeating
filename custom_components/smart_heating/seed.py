"""Starting configuration for Toby's house, from the proposal.

Radiator ratings are typical Type 22 catalogue figures (ΔT50). Entity ids are
the X1's Zigbee2MQTT names. Everything here is editable later from the setup
panel; this only seeds the store on first run.
"""

from .model import Config, PriorityEntry, Profile, Radiator, Settings, SpareHeat, Zone


def seed_config() -> Config:
    radiators = {
        r.name: r
        for r in [
            Radiator("Living Room Inner", "climate.trv_living_room", 2060),
            Radiator("Living Room Window", "climate.trv_living_room_window", 1400),
            Radiator("Living Room Stairs", "climate.trv_living_room_stairs", 1380),
            Radiator("Kitchen", "climate.trv_kitchen", 2060),
            Radiator("Main Bedroom", "climate.trv_bedroom", 2060),
            Radiator("Guest Bedroom", "climate.trv_guest_bedroom", 1380),
            Radiator("Office", "climate.trv_office", 1030),
            Radiator("Bathroom", "climate.trv_bathroom", 1030),
        ]
    }
    zones = {
        z.name: z
        for z in [
            Zone("Living Room", "sensor.thermostat_living_room_temperature",
                 ["Living Room Inner", "Living Room Stairs", "Living Room Window"]),
            Zone("Kitchen", "sensor.thermostat_kitchen_temperature", ["Kitchen"]),
            Zone("Bathroom", "sensor.thermostat_bathroom_temperature", ["Bathroom"]),
            # The landing has no radiator of its own; the stairs radiator warms it.
            Zone("Landing", "sensor.thermostat_landing_temperature", ["Living Room Stairs"]),
            Zone("Main Bedroom", "sensor.thermostat_bedroom_temperature", ["Main Bedroom"]),
            Zone("Guest Bedroom", "sensor.thermostat_guest_bedroom_temperature", ["Guest Bedroom"]),
            Zone("Office", "sensor.thermostat_office_temperature", ["Office"]),
        ]
    }
    profiles = {
        p.name: p
        for p in [
            Profile("Office", "Office", 18.0, [
                PriorityEntry("Landing", 16.0, buffer=True),
                PriorityEntry("Main Bedroom", 15.0),
                PriorityEntry("Living Room", 16.0),
                PriorityEntry("Kitchen", 15.0),
            ]),
            Profile("Living Room", "Living Room", 18.0, [
                PriorityEntry("Landing", 16.0, buffer=True),
                PriorityEntry("Kitchen", 15.0),
            ]),
            # Bathroom and Kitchen share a wall, so each buffers the other.
            Profile("Kitchen", "Kitchen", 17.0, [
                PriorityEntry("Bathroom", 15.0, buffer=True),
                PriorityEntry("Living Room", 16.0),
            ]),
            Profile("Bathroom", "Bathroom", 18.0, [
                PriorityEntry("Kitchen", 15.0, buffer=True),
            ]),
            Profile("Whole House", "Living Room", 18.0, [
                PriorityEntry("Kitchen", 16.0),
                PriorityEntry("Landing", 16.0),
                PriorityEntry("Main Bedroom", 15.0),
                PriorityEntry("Office", 15.0),
                PriorityEntry("Guest Bedroom", 14.0),
                PriorityEntry("Bathroom", 15.0),
            ], spare_heat=SpareHeat.FILL_TO_MINIMUM),
        ]
    }
    return Config(radiators=radiators, zones=zones, profiles=profiles, settings=Settings())
