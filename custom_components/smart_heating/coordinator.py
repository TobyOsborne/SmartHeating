"""Runs the controller inside Home Assistant.

Every minute, and shortly after any thermometer, the boiler or the flow
temperature changes, the coordinator gathers the inputs, runs one tick of the
controller and, unless dry run is on, applies the decision.

In dry run nothing is sent to the boiler or the TRVs. The coordinator keeps a
simulated boiler and radiator state instead, so hold times, minimum on/off
times and the decisions shown on the dashboard behave as they would live.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store

from .const import (
    ACTION_IGNORE_WINDOW,
    ACTION_KEEP_GOING,
    ACTION_TURN_OFF,
    MAX_ATTEMPTS,
    SIGNAL_UPDATE,
    TICK_SECONDS,
    STARTUP_GRACE_SECONDS,
    VERIFY_AFTER_SECONDS,
)
from .sensors import ReadingFilter
from .controller import WindowDetector, radiator_output_w, tick
from .learning import ZoneLearning, estimate
from .model import Config, Decision, Inputs, Mode, Notification, Override, Reading, State
from .store import config_to_dict, state_to_dict

_LOGGER = logging.getLogger(__name__)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")


@dataclass
class Pending:
    """A TRV command waiting to be checked."""

    target: float
    due: float
    attempts: int = 1


@dataclass
class Runtime:
    mode: Mode = Mode.AUTO
    profile: str = ""
    dry_run: bool = True
    overrides: list[Override] = field(default_factory=list)


class SmartHeatingCoordinator:
    def __init__(self, hass: HomeAssistant, store: Store, config: Config, runtime: Runtime,
                 boiler: str, flow_source: str, notify: str,
                 learning: dict[str, ZoneLearning] | None = None) -> None:
        self.hass = hass
        self.store = store
        self.config = config
        self.runtime = runtime
        self.boiler = boiler
        self.flow_source = flow_source
        self.notify = notify

        self.state = State()
        self.decision: Decision | None = None
        self.recent_notifications: list[dict[str, Any]] = []

        # What each TRV was last told (live) or would have been told (dry run)
        self.radiator_open: dict[str, bool] = {}
        self.radiator_changed: dict[str, float] = {}
        self.pending: dict[str, Pending] = {}
        self.faulty: set[str] = set()

        # Simulated boiler, used in dry run
        self.sim_boiler_on = False
        self.sim_boiler_changed = 0.0

        self.windows: dict[str, WindowDetector] = {}
        self.filters: dict[str, ReadingFilter] = {}
        self.window_ignored: dict[str, float] = {}

        self.learning: dict[str, ZoneLearning] = learning or {}
        self._last_hour: int | None = None

        self._unsubs: list = []
        self._soon_unsub = None

    # ------------------------------------------------------------ lifecycle

    async def async_start(self) -> None:
        self.started = time.time()
        self._sync_from_trvs()
        self._rebuild_windows()
        self._unsubs.append(async_track_time_interval(
            self.hass, self._handle_interval, timedelta(seconds=TICK_SECONDS)))
        self._track_entities()
        self._unsubs.append(self.hass.bus.async_listen(
            "mobile_app_notification_action", self._handle_notification_action))
        await self.async_tick()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._soon_unsub:
            self._soon_unsub()
            self._soon_unsub = None

    def _track_entities(self) -> None:
        watched = [z.thermometer for z in self.config.zones.values()] + [self.boiler, self.flow_source]
        self._unsubs.append(async_track_state_change_event(self.hass, watched, self._handle_state_change))

    def _sync_from_trvs(self) -> None:
        """Start from what the TRVs are actually set to."""
        for name, rad in self.config.radiators.items():
            st = self.hass.states.get(rad.trv)
            target = st.attributes.get("temperature") if st else None
            self.radiator_open[name] = target is not None and float(target) >= self.config.settings.open_temp - 1
            self.radiator_changed.setdefault(name, 0.0)

    def _rebuild_windows(self) -> None:
        self.windows = {
            z.name: WindowDetector(z.window_drop, z.window_window_s)
            for z in self.config.zones.values() if z.window_detection
        }

    async def async_reload_config(self, config: Config) -> None:
        """Called after the setup panel or a service changes the configuration."""
        self.config = config
        if self.runtime.profile not in config.profiles:
            self.runtime.profile = next(iter(config.profiles))
        self.runtime.overrides = [o for o in self.runtime.overrides if o.zone in config.zones]
        self.faulty &= set(config.radiators)
        self.pending = {k: v for k, v in self.pending.items() if k in config.radiators}
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._sync_from_trvs()
        self._rebuild_windows()
        self._unsubs.append(async_track_time_interval(
            self.hass, self._handle_interval, timedelta(seconds=TICK_SECONDS)))
        self._track_entities()
        self._unsubs.append(self.hass.bus.async_listen(
            "mobile_app_notification_action", self._handle_notification_action))
        await self.async_save()
        await self.async_tick()

    async def async_save(self) -> None:
        data = {
            "config": config_to_dict(self.config),
            "runtime": state_to_dict(self.runtime.mode, self.runtime.profile, self.runtime.dry_run),
            "learning": {name: zl.to_dict() for name, zl in self.learning.items()},
        }
        await self.store.async_save(data)

    # ------------------------------------------------------------ triggers

    async def _handle_interval(self, _now) -> None:
        await self.async_tick()

    @callback
    def _handle_state_change(self, event: Event) -> None:
        entity_id = event.data.get("entity_id")
        new = event.data.get("new_state")
        # Feed window detectors straight away
        for zone in self.config.zones.values():
            if zone.thermometer == entity_id and zone.name in self.windows and new is not None:
                reading = self._reading(entity_id)  # filtered: a glitch must not look like an open window
                if reading.temp is not None:
                    self.windows[zone.name].update(time.time(), reading.temp)
        # Batch bursts of changes into one tick a few seconds later
        if self._soon_unsub is None:
            self._soon_unsub = async_call_later(self.hass, 3, self._handle_soon)

    async def _handle_soon(self, _now) -> None:
        self._soon_unsub = None
        await self.async_tick()

    async def _handle_notification_action(self, event: Event) -> None:
        action = event.data.get("action", "")
        if action == ACTION_TURN_OFF:
            await self.async_set_mode(Mode.OFF)
        elif action == ACTION_KEEP_GOING:
            self.state.all_satisfied_notified = True
        elif action.startswith(ACTION_IGNORE_WINDOW):
            slug = action[len(ACTION_IGNORE_WINDOW):]
            for name, det in self.windows.items():
                if _slug(name) == slug:
                    det.ignore()
                    self.window_ignored[name] = time.time() + 3600
            await self.async_tick()

    # ------------------------------------------------------------ inputs

    def _reading(self, entity_id: str) -> Reading:
        st = self.hass.states.get(entity_id)
        if st is None:
            return Reading(None, None)
        try:
            temp = float(st.state)
        except (TypeError, ValueError):
            temp = None
        reported = (getattr(st, "last_reported", None) or st.last_updated).timestamp()
        if entity_id not in self._thermometers():
            return Reading(temp, reported) if temp is not None else Reading(None, None)
        filt = self.filters.setdefault(entity_id, ReadingFilter())
        if temp is None:
            # unavailable/unknown: keep the last good value; staleness decides when it's offline
            return Reading(*filt.good) if filt.good else Reading(None, None)
        before = filt.rejected
        good = filt.feed(temp, reported)
        if filt.rejected != before:
            _LOGGER.info("Ignoring implausible reading %s from %s (using %s)", temp, entity_id,
                         good[0] if good else "nothing yet")
        return Reading(*good) if good else Reading(None, None)

    def _thermometers(self) -> set[str]:
        return {z.thermometer for z in self.config.zones.values()}

    def _flow_temp(self) -> float:
        st = self.hass.states.get(self.flow_source)
        try:
            return float(st.state) if st else 60.0
        except (TypeError, ValueError):
            return 60.0

    def _boiler(self) -> tuple[bool, float]:
        if self.runtime.dry_run:
            return self.sim_boiler_on, self.sim_boiler_changed
        st = self.hass.states.get(self.boiler)
        if st is None:
            return False, 0.0
        return st.state == STATE_ON, st.last_changed.timestamp()

    def _window_states(self, now: float) -> dict[str, bool]:
        out = {}
        for name, det in self.windows.items():
            if self.window_ignored.get(name, 0) > now:
                out[name] = False
            else:
                out[name] = det.is_open(now)
        return out

    def build_inputs(self, now: float | None = None) -> Inputs:
        now = now if now is not None else time.time()
        self.runtime.overrides = [o for o in self.runtime.overrides
                                  if o.expires_at is None or o.expires_at > now]
        if self.runtime.profile not in self.config.profiles:
            self.runtime.profile = next(iter(self.config.profiles))
        boiler_on, boiler_changed = self._boiler()
        return Inputs(
            now=now,
            mode=self.runtime.mode,
            profile=self.runtime.profile,
            flow_temp=self._flow_temp(),
            readings={z.name: self._reading(z.thermometer) for z in self.config.zones.values()},
            boiler_on=boiler_on,
            boiler_changed=boiler_changed,
            radiator_open=dict(self.radiator_open),
            radiator_changed=dict(self.radiator_changed),
            windows=self._window_states(now),
            faulty=set(self.faulty),
            overrides=list(self.runtime.overrides),
        )

    # ------------------------------------------------------------ tick

    async def async_tick(self) -> None:
        now = time.time()
        inputs = self.build_inputs(now)
        previous_windows = {k for k, v in (self.decision.zone_states.items() if self.decision else [])
                            if v == "window"}
        decision, self.state = tick(self.config, inputs, self.state)
        self.decision = decision

        if self.runtime.mode is not Mode.MANUAL:
            await self._apply(decision, now)
        await self._verify(now)

        for zone, st in decision.zone_states.items():
            if st == "window" and zone not in previous_windows:
                decision.notifications.append(Notification(
                    "window", f"Window open in {zone}?", ["Ignore"]))
        if now - getattr(self, "started", 0) < STARTUP_GRACE_SECONDS:
            # Sensors are still being restored after a restart: don't cry wolf
            if any(n.kind == "sensor_offline" for n in decision.notifications):
                self.state.offline_notified = False
            decision.notifications = [n for n in decision.notifications if n.kind != "sensor_offline"]
        for note in decision.notifications:
            await self._notify(note)

        await self._learn(now)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    # ------------------------------------------------------------ learning

    def _trv_open(self, name: str) -> bool:
        """Real valve state from the TRV's setpoint (learning ignores the dry-run simulation)."""
        st = self.hass.states.get(self.config.radiators[name].trv)
        target = st.attributes.get("temperature") if st else None
        return target is not None and float(target) >= self.config.settings.open_temp - 1

    async def _learn(self, now: float) -> None:
        s = self.config.settings
        outdoor = self._reading(s.outdoor_sensor).temp
        bst = self.hass.states.get(self.boiler)
        boiler_on = bst is not None and bst.state == STATE_ON
        flow = self._flow_temp()
        for zone in self.config.zones.values():
            zl = self.learning.setdefault(zone.name, ZoneLearning())
            temp = self._reading(zone.thermometer).temp
            open_rads = [r for r in zone.radiators if self._trv_open(r)]
            kw = sum(radiator_output_w(self.config.radiators[r].rated_w, flow, s.drop,
                                       temp if temp is not None else s.default_room_temp,
                                       self.config.radiators[r].exponent) for r in open_rads) / 1000
            zl.observe(now, temp, outdoor, quiet=not boiler_on and not open_rads,
                       heating=boiler_on and bool(open_rads), delivered_kw=kw)
        hour = int(now // 3600)
        if self._last_hour is not None and hour != self._last_hour:
            label = time.strftime("%d/%m %H:%M", time.localtime(now))
            for zl in self.learning.values():
                zl.close_hour(now, label)
            await self.async_save()
        self._last_hour = hour

    def learning_summary(self) -> dict[str, dict]:
        out = {}
        outdoor = self._reading(self.config.settings.outdoor_sensor).temp
        for name in self.config.zones:
            zl = self.learning.get(name)
            if zl is None:
                continue
            est = estimate(zl.cooling)
            temp = self._reading(self.config.zones[name].thermometer).temp
            out[name] = {
                "half_life_h": est.half_life_h,
                "half_life_factor": est.half_life_factor,
                "free_heat": est.free_heat,
                "free_heat_sd": est.free_heat_sd,
                "confidence": est.confidence,
                "samples": est.samples,
                "last_sample": zl.cooling.last,
                "settling_temp": round(est.settling_temp(outdoor), 1) if outdoor is not None else None,
                "cooling_now": (round(est.cooling_rate(temp, outdoor), 2)
                                if temp is not None and outdoor is not None else None),
                "warmup_per_kw": round(zl.warmup.mean, 2) if zl.warmup.mean is not None else None,
                "warmup_confidence": zl.warmup.confidence,
                "warmup_samples": round(zl.warmup.n, 1),
            }
        return out

    async def _apply(self, decision: Decision, now: float) -> None:
        s = self.config.settings
        # Boiler
        boiler_on, _ = self._boiler()
        if decision.boiler_on != boiler_on:
            if self.runtime.dry_run:
                self.sim_boiler_on = decision.boiler_on
                self.sim_boiler_changed = now
            else:
                await self.hass.services.async_call(
                    "switch", "turn_on" if decision.boiler_on else "turn_off",
                    {ATTR_ENTITY_ID: self.boiler}, blocking=False)
        # Radiators
        for name, rad in self.config.radiators.items():
            want = name in decision.open
            if want == self.radiator_open.get(name, False):
                continue
            self.radiator_open[name] = want
            self.radiator_changed[name] = now
            if self.runtime.dry_run:
                continue
            target = s.open_temp if want else s.closed_temp
            await self._send_trv(rad.trv, target)
            self.pending[name] = Pending(target=target, due=now + VERIFY_AFTER_SECONDS)

    async def _send_trv(self, entity_id: str, target: float) -> None:
        await self.hass.services.async_call(
            "climate", "set_temperature",
            {ATTR_ENTITY_ID: entity_id, "temperature": target, "hvac_mode": "heat"},
            blocking=False)

    async def _verify(self, now: float) -> None:
        """Check TRVs took their last command; resend, then flag after MAX_ATTEMPTS."""
        for name, p in list(self.pending.items()):
            if now < p.due:
                continue
            rad = self.config.radiators[name]
            st = self.hass.states.get(rad.trv)
            current = st.attributes.get("temperature") if st else None
            if current is not None and abs(float(current) - p.target) < 0.01:
                del self.pending[name]
                self.faulty.discard(name)
                continue
            if p.attempts >= MAX_ATTEMPTS:
                del self.pending[name]
                if name not in self.faulty:
                    self.faulty.add(name)
                    await self._notify(Notification("trv_fault", f"TRV not responding: {name}"))
                continue
            p.attempts += 1
            p.due = now + VERIFY_AFTER_SECONDS
            if not self.runtime.dry_run:
                await self._send_trv(rad.trv, p.target)

    async def _notify(self, note: Notification) -> None:
        entry = {"time": time.time(), "kind": note.kind, "message": note.message,
                 "sent": not self.runtime.dry_run}
        self.recent_notifications = ([entry] + self.recent_notifications)[:20]
        if self.runtime.dry_run or not self.notify:
            _LOGGER.info("Dry run notification: %s", note.message)
            return
        domain, _, service = self.notify.partition(".")
        actions = []
        for title in note.actions:
            if title == "Turn off":
                actions.append({"action": ACTION_TURN_OFF, "title": title})
            elif title == "Keep going":
                actions.append({"action": ACTION_KEEP_GOING, "title": title})
            elif title == "Ignore" and note.kind == "window":
                zone = note.message.removeprefix("Window open in ").rstrip("?")
                actions.append({"action": f"{ACTION_IGNORE_WINDOW}{_slug(zone)}", "title": title})
        data: dict[str, Any] = {"title": "Heating", "message": note.message,
                                "data": {"tag": f"smart_heating_{note.kind}"}}
        if actions:
            data["data"]["actions"] = actions
        try:
            await self.hass.services.async_call(domain, service, data, blocking=False)
        except Exception:  # noqa: BLE001 - a failed notification must never stop heating
            _LOGGER.warning("Could not send notification via %s", self.notify)

    # ------------------------------------------------------------ controls

    async def async_set_mode(self, mode: Mode) -> None:
        self.runtime.mode = mode
        await self.async_save()
        await self.async_tick()

    async def async_set_profile(self, profile: str) -> None:
        if profile not in self.config.profiles:
            raise ValueError(f"Unknown profile {profile}")
        if profile != self.runtime.profile:
            # Overrides made "until the profile changes" end here
            self.runtime.overrides = [o for o in self.runtime.overrides if o.expires_at is not None]
        self.runtime.profile = profile
        await self.async_save()
        await self.async_tick()

    async def async_set_dry_run(self, dry_run: bool) -> None:
        if not dry_run and self.runtime.dry_run:
            # Going live: start from the TRVs' real settings, not the simulation.
            # Simulated change times mean nothing to the real valves, so clear them.
            self.radiator_changed = {}
            self._sync_from_trvs()
        self.runtime.dry_run = dry_run
        await self.async_save()
        await self.async_tick()

    async def async_boost(self, zone: str, target: float | None, position: int | None,
                          minutes: float | None) -> None:
        if zone not in self.config.zones:
            raise ValueError(f"Unknown zone {zone}")
        expires = time.time() + minutes * 60 if minutes else None
        self.runtime.overrides = [o for o in self.runtime.overrides if o.zone != zone]
        self.runtime.overrides.append(Override(zone, target, position, expires, self.runtime.profile))
        await self.async_tick()

    async def async_cancel_boost(self, zone: str | None) -> None:
        self.runtime.overrides = [o for o in self.runtime.overrides if zone is not None and o.zone != zone]
        await self.async_tick()
