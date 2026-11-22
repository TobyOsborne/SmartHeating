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


# The Pi's cooling-model sums on 22 Nov 2026 (input_text.cooling_model_*), so the
# learning carries on from where the Pi left off instead of starting at zero.
PI_COOLING = {
    "Living Room": {"n": 112.49, "x": 864.219, "y": -15.9195, "xx": 7195.83, "xy": -123.6118, "yy": 2.87347, "t": "22/11 16:05"},
    "Kitchen": {"n": 111.83, "x": 860.676, "y": -16.1402, "xx": 7221.9, "xy": -131.3425, "yy": 3.22405, "t": "22/11 16:05"},
    "Landing": {"n": 112.49, "x": 728.968, "y": -13.9123, "xx": 5310.45, "xy": -90.9948, "yy": 2.37634, "t": "22/11 16:05"},
    "Bathroom": {"n": 85.78, "x": 440.095, "y": -7.855, "xx": 2482.59, "xy": -45.6026, "yy": 1.18448, "t": "22/11 16:05"},
    "Main Bedroom": {"n": 87.26, "x": 561.18, "y": -4.9171, "xx": 3860.34, "xy": -39.2601, "yy": 1.19671, "t": "22/11 18:05"},
    "Guest Bedroom": {"n": 87.26, "x": 466.266, "y": -7.5267, "xx": 2727.35, "xy": -45.9012, "yy": 1.53701, "t": "22/11 18:05"},
    "Office": {"n": 86.52, "x": 415.912, "y": -13.3586, "xx": 2288.83, "xy": -75.9102, "yy": 3.62743, "t": "22/11 18:05"},
}
