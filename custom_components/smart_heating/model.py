"""Data model for smart_heating.

Plain dataclasses with no Home Assistant imports, so the controller and its
tests run anywhere. Times are seconds (float, e.g. time.time()); powers are watts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Mode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    OFF = "off"
    FROST = "frost"


class SpareHeat(str, Enum):
    PRIMARY_ONLY = "primary_only"
    BUFFER_ZONES = "buffer_zones"
    FILL_TO_MINIMUM = "fill_to_minimum"


@dataclass
class Radiator:
    name: str
    trv: str  # climate entity id
    rated_w: float  # output at ΔT50
    exponent: float = 1.3
    enabled: bool = True


@dataclass
class Zone:
    name: str
    thermometer: str  # sensor entity id
    radiators: list[str] = field(default_factory=list)  # radiator names, in order
    hysteresis: float = 0.3
    window_detection: bool = True
    window_drop: float = 0.8  # °C ...
    window_window_s: float = 300  # ... within this many seconds
    damp_alert: bool = True


@dataclass
class PriorityEntry:
    zone: str
    target: float
    buffer: bool = False


@dataclass
class Profile:
    name: str
    primary_zone: str
    goal: float
    priority: list[PriorityEntry] = field(default_factory=list)
    spare_heat: SpareHeat = SpareHeat.BUFFER_ZONES
    runtime_warning_s: float = 2 * 3600


@dataclass
class Override:
    zone: str
    target: float | None = None
    position: int | None = None  # 0 = top of the priority list
    expires_at: float | None = None  # None = until the profile changes
    profile: str | None = None  # profile it was made under


@dataclass
class Settings:
    boiler_min_w: float = 6300
    margin_w: float = 500
    drop: float = 10  # flow-to-return °C
    min_on_s: float = 600
    min_off_s: float = 600
    hold_s: float = 600  # per-radiator minimum time between changes
    frost_temp: float = 7.0
    frost_release: float = 1.0  # frost heating stops at frost_temp + this
    goal_band: float = 0.3  # boiler on below goal - band, off at goal + band
    sensor_stale_s: float = 900
    safety_runtime_s: float = 3 * 3600
    all_satisfied_notify_s: float = 900
    open_temp: float = 30.0
    closed_temp: float = 5.0
    default_room_temp: float = 18.0
    outdoor_sensor: str = "sensor.outdoor_temperature"


@dataclass
class Config:
    radiators: dict[str, Radiator]
    zones: dict[str, Zone]
    profiles: dict[str, Profile]
    settings: Settings = field(default_factory=Settings)


@dataclass
class Reading:
    temp: float | None
    updated: float | None  # when the sensor last reported


@dataclass
class Inputs:
    """Everything the controller needs to know about the world for one tick."""

    now: float
    mode: Mode
    profile: str
    flow_temp: float
    readings: dict[str, Reading]  # by zone name
    boiler_on: bool
    boiler_changed: float  # when the boiler last switched
    radiator_open: dict[str, bool]  # what each TRV was last told
    radiator_changed: dict[str, float]  # when each TRV was last told
    windows: dict[str, bool] = field(default_factory=dict)  # zone -> window open
    faulty: set[str] = field(default_factory=set)  # radiators the actuator flagged
    overrides: list[Override] = field(default_factory=list)


@dataclass
class State:
    """Memory the controller keeps between ticks."""

    satisfied: dict[str, bool] = field(default_factory=dict)  # zone -> satisfied
    frost_active: set[str] = field(default_factory=set)
    all_satisfied_since: float | None = None
    all_satisfied_notified: bool = False
    runtime_warned: bool = False
    safety_warned: bool = False
    offline_notified: bool = False


@dataclass
class Notification:
    kind: str
    message: str
    actions: list[str] = field(default_factory=list)


@dataclass
class Decision:
    boiler_on: bool
    open: set[str]
    radiator_reasons: dict[str, str]
    zone_states: dict[str, str]
    capacity_w: float
    needed_w: float
    reason: str
    notifications: list[Notification] = field(default_factory=list)
    held: set[str] = field(default_factory=set)  # wanted to change but held

    @property
    def shortfall_w(self) -> float:
        return max(0.0, self.needed_w - self.capacity_w)
