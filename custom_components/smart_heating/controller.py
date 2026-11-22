"""The smart_heating control algorithm.

Pure Python: no Home Assistant imports. `tick()` takes the configuration,
this tick's inputs and the memory from the last tick, and returns a Decision
plus the updated memory. The coordinator applies the decision.

Rules (see the proposal, "Control algorithm"):
- The primary zone alone decides when the boiler runs.
- The priority list decides which other radiators open while it runs, as far
  as the profile's spare-heat mode allows.
- Radiators are open (30°) or closed (5°); a radiator counts once toward
  capacity however many zones want it.
- Any zone under the frost temperature is heated in every mode except Off.
"""

from __future__ import annotations

import copy
from dataclasses import replace

from .model import (
    Config,
    Decision,
    Inputs,
    Mode,
    Notification,
    Override,
    PriorityEntry,
    Profile,
    SpareHeat,
    State,
)


# ---------------------------------------------------------------- physics


def radiator_output_w(rated_w: float, flow: float, drop: float, room: float, exponent: float = 1.3) -> float:
    """Output of one radiator, scaled from its ΔT50 rating."""
    delta = flow - drop / 2 - room
    if delta <= 0:
        return 0.0
    return rated_w * (delta / 50) ** exponent


def capacity_w(config: Config, radiators: set[str], flow: float, room_temps: dict[str, float], faulty: set[str] = frozenset()) -> float:
    """Total output of a set of radiators. Faulty or disabled radiators add nothing."""
    total = 0.0
    for name in radiators:
        rad = config.radiators[name]
        if not rad.enabled or name in faulty:
            continue
        total += radiator_output_w(
            rad.rated_w,
            flow,
            config.settings.drop,
            room_temps.get(name, config.settings.default_room_temp),
            rad.exponent,
        )
    return total


# ---------------------------------------------------------------- helpers


def apply_overrides(profile: Profile, overrides: list[Override], now: float) -> Profile:
    """Return a copy of the profile with live overrides applied."""
    live = [o for o in overrides if (o.expires_at is None or o.expires_at > now) and o.profile in (None, profile.name)]
    if not live:
        return profile
    prof = copy.deepcopy(profile)
    for o in live:
        if o.zone == prof.primary_zone:
            if o.target is not None:
                prof.goal = o.target
            continue
        idx = next((i for i, e in enumerate(prof.priority) if e.zone == o.zone), None)
        if idx is None:
            entry = PriorityEntry(zone=o.zone, target=o.target if o.target is not None else 0.0, buffer=True)
        else:
            entry = prof.priority.pop(idx)
            # A boosted zone always gets heat, whatever the spare-heat mode.
            entry = replace(entry, buffer=True, target=o.target if o.target is not None else entry.target)
        pos = len(prof.priority) if o.position is None else max(0, min(o.position, len(prof.priority)))
        if idx is not None and o.position is None:
            pos = idx
        prof.priority.insert(pos, entry)
    return prof


def _zone_temp(inputs: Inputs, zone: str, stale_s: float) -> float | None:
    r = inputs.readings.get(zone)
    if r is None or r.temp is None or r.updated is None:
        return None
    if inputs.now - r.updated > stale_s:
        return None
    return r.temp


def _room_temps(config: Config, inputs: Inputs) -> dict[str, float]:
    """Temperature each radiator works against: the first zone it belongs to."""
    out: dict[str, float] = {}
    for zone in config.zones.values():
        t = _zone_temp(inputs, zone.name, config.settings.sensor_stale_s)
        for rad in zone.radiators:
            if rad not in out and t is not None:
                out[rad] = t
    return out


def _satisfied(state: State, zone: str, temp: float | None, target: float, hysteresis: float) -> bool:
    """Zone hysteresis: satisfied at target, needs heat again below target - hysteresis."""
    if temp is None:
        return True  # an unknown zone never pulls heat
    was = state.satisfied.get(zone, False)
    now_satisfied = temp >= target if not was else temp >= target - hysteresis
    state.satisfied[zone] = now_satisfied
    return now_satisfied


def _usable(config: Config, inputs: Inputs, names: list[str]) -> list[str]:
    return [n for n in names if config.radiators[n].enabled and n not in inputs.faulty]


# ---------------------------------------------------------------- tick


def tick(config: Config, inputs: Inputs, state: State | None = None) -> tuple[Decision, State]:
    state = copy.deepcopy(state) if state is not None else State()
    s = config.settings
    now = inputs.now
    notes: list[Notification] = []
    reasons: dict[str, str] = {}
    zone_states: dict[str, str] = {}
    temps = {z: _zone_temp(inputs, z, s.sensor_stale_s) for z in config.zones}
    room_temps = _room_temps(config, inputs)
    needed = s.boiler_min_w + s.margin_w

    # --- frost: any zone below frost temperature, every mode except Off
    frost_open: set[str] = set()
    if inputs.mode is not Mode.OFF:
        for z, t in temps.items():
            if t is None:
                continue
            if t < s.frost_temp:
                state.frost_active.add(z)
            elif t >= s.frost_temp + s.frost_release:
                state.frost_active.discard(z)
        for z in state.frost_active:
            for r in _usable(config, inputs, config.zones[z].radiators):
                frost_open.add(r)
                reasons[r] = f"frost protection: {z} below {s.frost_temp:g}°"
            zone_states[z] = "frost"
    else:
        state.frost_active.clear()

    # --- Off: everything closed, boiler off, no hold times
    if inputs.mode is Mode.OFF:
        return _finish(config, inputs, state, False, set(), reasons, zone_states, room_temps, needed,
                       "Heating is off", notes, apply_hold=False)

    # --- Manual: change nothing
    if inputs.mode is Mode.MANUAL:
        current = {r for r, o in inputs.radiator_open.items() if o}
        for r in config.radiators:
            reasons[r] = "manual mode"
        return _finish(config, inputs, state, inputs.boiler_on, current, reasons, zone_states, room_temps, needed,
                       "Manual mode", notes, apply_hold=False, touch_nothing=True)

    # --- Frost mode: only frost heating
    if inputs.mode is Mode.FROST:
        want_on = bool(frost_open)
        want_on = _respect_boiler_times(config, inputs, want_on)
        reason = "Frost protection running" if frost_open else "Frost mode: all zones above frost temperature"
        return _finish(config, inputs, state, want_on, frost_open, reasons, zone_states, room_temps, needed,
                       reason, notes)

    # --- Auto
    profile = config.profiles[inputs.profile]
    profile = apply_overrides(profile, inputs.overrides, now)
    primary = config.zones[profile.primary_zone]
    p_temp = temps[primary.name]

    # Safety: primary thermometer offline -> boiler off (frost heating still allowed)
    if p_temp is None:
        if not state.offline_notified:
            notes.append(Notification("sensor_offline",
                                      f"{primary.name} thermometer offline: heating stopped"))
            state.offline_notified = True
        zone_states[primary.name] = "offline"
        want_on = _respect_boiler_times(config, inputs, bool(frost_open), safety=True)
        return _finish(config, inputs, state, want_on, frost_open, reasons, zone_states, room_temps, needed,
                       f"{primary.name} thermometer offline", notes)
    state.offline_notified = False

    # 1. Boiler demand (primary zone only); an open window in the primary zone stops it
    band = s.goal_band
    window_primary = inputs.windows.get(primary.name, False)
    want_on = (p_temp < profile.goal - band) or (inputs.boiler_on and p_temp < profile.goal + band)
    if window_primary:
        want_on = False
    if frost_open:
        want_on = True
    want_on = _respect_boiler_times(config, inputs, want_on)

    primary_rads = _usable(config, inputs, primary.radiators)
    open_set: set[str] = set(frost_open)

    if window_primary:
        zone_states[primary.name] = "window"
        reason = f"Window open in {primary.name}"
        for r in primary_rads:
            reasons.setdefault(r, f"window open in {primary.name}")
    else:
        open_set |= set(primary_rads)
        for r in primary_rads:
            reasons.setdefault(r, f"primary zone {primary.name}")
        zone_states[primary.name] = "heating" if want_on else "satisfied"
        reason = (f"{primary.name} {p_temp:.1f}° below {profile.goal:g}° goal" if want_on
                  else f"{primary.name} {p_temp:.1f}° at {profile.goal:g}° goal")

    # 2. Choose radiators (only while the boiler runs)
    listed_zones = [e.zone for e in profile.priority]
    all_satisfied = True
    if want_on and profile.spare_heat is not SpareHeat.PRIMARY_ONLY:
        fill = profile.spare_heat is SpareHeat.FILL_TO_MINIMUM
        for entry in profile.priority:
            zone = config.zones[entry.zone]
            if not fill and not entry.buffer:
                zone_states.setdefault(zone.name, "idle")
                continue
            t = temps[zone.name]
            if inputs.windows.get(zone.name, False):
                zone_states[zone.name] = "window"
                continue
            if _satisfied(state, zone.name, t, entry.target, zone.hysteresis):
                zone_states.setdefault(zone.name, "satisfied")
                continue
            all_satisfied = False
            if fill and capacity_w(config, open_set, inputs.flow_temp, room_temps, inputs.faulty) >= needed:
                zone_states.setdefault(zone.name, "waiting")
                continue
            zone_states[zone.name] = "heating"
            for r in _usable(config, inputs, zone.radiators):
                if fill and capacity_w(config, open_set, inputs.flow_temp, room_temps, inputs.faulty) >= needed:
                    break
                if r not in open_set:
                    open_set.add(r)
                    reasons[r] = f"{'overflow' if fill else 'buffer'}: {zone.name} below {entry.target:g}°"
    else:
        for e in profile.priority:
            zone_states.setdefault(e.zone, "idle")
        all_satisfied = False

    # 3. "All overflow rooms at target" (fill mode only)
    if want_on and profile.spare_heat is SpareHeat.FILL_TO_MINIMUM and listed_zones and all_satisfied:
        if state.all_satisfied_since is None:
            state.all_satisfied_since = now
        if now - state.all_satisfied_since >= s.all_satisfied_notify_s and not state.all_satisfied_notified:
            notes.append(Notification("all_satisfied", "All overflow rooms are at target. Turn heating off?",
                                      ["Turn off", "Keep going"]))
            state.all_satisfied_notified = True
    else:
        state.all_satisfied_since = None
        state.all_satisfied_notified = False

    # 4. When the boiler is off, keep only the primary's radiators (and frost) open
    if not want_on:
        open_set = set(frost_open) | (set() if window_primary else set(primary_rads))

    # Run-time warnings
    if inputs.boiler_on and want_on:
        ran = now - inputs.boiler_changed
        if ran >= profile.runtime_warning_s and not state.runtime_warned:
            notes.append(Notification("runtime", f"Heating has been on for {ran / 3600:.0f} h",
                                      ["Turn off", "Keep going"]))
            state.runtime_warned = True
        if ran >= s.safety_runtime_s and not state.safety_warned:
            notes.append(Notification("safety_runtime", f"Boiler has run {ran / 3600:.0f} h in one go",
                                      ["Turn off"]))
            state.safety_warned = True
    else:
        state.runtime_warned = False
        state.safety_warned = False

    return _finish(config, inputs, state, want_on, open_set, reasons, zone_states, room_temps, needed, reason, notes)


def _respect_boiler_times(config: Config, inputs: Inputs, want_on: bool, safety: bool = False) -> bool:
    """Hold the boiler in its current state until its minimum on/off time has passed."""
    s = config.settings
    since = inputs.now - inputs.boiler_changed
    if inputs.boiler_on and not want_on and since < s.min_on_s and not safety:
        return True
    if not inputs.boiler_on and want_on and since < s.min_off_s:
        return False
    return want_on


def _finish(config, inputs, state, boiler_on, wanted, reasons, zone_states, room_temps, needed, reason, notes,
            apply_hold=True, touch_nothing=False) -> tuple[Decision, State]:
    s = config.settings
    final = set()
    held = set()
    for name in config.radiators:
        is_open = inputs.radiator_open.get(name, False)
        want = name in wanted
        if touch_nothing:
            if is_open:
                final.add(name)
            continue
        if want != is_open and apply_hold:
            changed = inputs.radiator_changed.get(name)
            if changed is not None and inputs.now - changed < s.hold_s:
                held.add(name)
                want = is_open
                reasons[name] = f"held {'open' if is_open else 'closed'}: changed under {s.hold_s / 60:g} min ago"
        if want:
            final.add(name)
        reasons.setdefault(name, "not needed")
    cap = capacity_w(config, final, inputs.flow_temp, room_temps, inputs.faulty)
    decision = Decision(
        boiler_on=boiler_on,
        open=final,
        radiator_reasons=reasons,
        zone_states=zone_states,
        capacity_w=cap,
        needed_w=needed,
        reason=reason,
        notifications=notes,
        held=held,
    )
    return decision, state


# ---------------------------------------------------------------- window detection


class WindowDetector:
    """Flags a zone when its temperature falls `drop`° within `window_s` seconds."""

    def __init__(self, drop: float = 0.8, window_s: float = 300, hold_s: float = 1200):
        self.drop = drop
        self.window_s = window_s
        self.hold_s = hold_s
        self._history: list[tuple[float, float]] = []
        self._open_until: float | None = None

    def update(self, now: float, temp: float) -> bool:
        self._history.append((now, temp))
        self._history = [(t, v) for t, v in self._history if now - t <= self.window_s]
        peak = max(v for _, v in self._history)
        if peak - temp >= self.drop:
            self._open_until = now + self.hold_s
        return self.is_open(now)

    def is_open(self, now: float) -> bool:
        return self._open_until is not None and now < self._open_until

    def ignore(self) -> None:
        self._open_until = None
        self._history.clear()
