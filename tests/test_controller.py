"""Scenario tests for the smart_heating controller (proposal roll-out step 3)."""

import pytest

from custom_components.smart_heating.controller import (
    WindowDetector,
    apply_overrides,
    capacity_w,
    radiator_output_w,
    tick,
)
from custom_components.smart_heating.model import (
    Inputs,
    Mode,
    Override,
    Reading,
    SpareHeat,
    State,
)
from custom_components.smart_heating.seed import seed_config

NOW = 1_000_000.0
LONG_AGO = NOW - 86_400


def make_inputs(cfg, temps, *, profile="Office", mode=Mode.AUTO, boiler_on=False, boiler_changed=LONG_AGO,
                open_=(), changed=None, flow=60.0, now=NOW, **kw):
    return Inputs(
        now=now,
        mode=mode,
        profile=profile,
        flow_temp=flow,
        readings={z: Reading(t, now - 30) for z, t in temps.items()},
        boiler_on=boiler_on,
        boiler_changed=boiler_changed,
        radiator_open={r: r in open_ for r in cfg.radiators},
        radiator_changed=changed or {r: LONG_AGO for r in cfg.radiators},
        **kw,
    )


COLD = {"Office": 17.0, "Landing": 14.0, "Main Bedroom": 13.0, "Living Room": 14.0, "Kitchen": 13.0,
        "Bathroom": 13.0, "Guest Bedroom": 13.0}


# ------------------------------------------------------------ physics


def test_radiator_output_matches_proposal_table():
    # Office 60x60 Type 22, 1030 W at ΔT50 -> about 0.70 kW at 60° flow in an 18° room
    assert radiator_output_w(1030, 60, 10, 18) == pytest.approx(700, abs=10)
    # Whole house at 55° ≈ 6.9 kW
    cfg = seed_config()
    assert capacity_w(cfg, set(cfg.radiators), 55, {}) == pytest.approx(6900, abs=100)


def test_shared_radiator_counts_once():
    cfg = seed_config()
    one = capacity_w(cfg, {"Living Room Stairs"}, 60, {})
    assert capacity_w(cfg, {"Living Room Stairs", "Living Room Stairs"}, 60, {}) == one


# ------------------------------------------------------------ the proposal's worked examples


def test_office_buffer_zones_opens_office_and_stairs_only():
    cfg = seed_config()
    d, _ = tick(cfg, make_inputs(cfg, COLD))
    assert d.boiler_on
    assert d.open == {"Office", "Living Room Stairs"}
    assert "buffer: Landing" in d.radiator_reasons["Living Room Stairs"]


def test_office_fill_to_minimum_follows_priority_until_capacity():
    cfg = seed_config()
    cfg.profiles["Office"].spare_heat = SpareHeat.FILL_TO_MINIMUM
    # Rooms at 18° so outputs match the proposal's table exactly
    temps = {z: 18.0 for z in COLD} | {"Office": 17.0}
    for e in cfg.profiles["Office"].priority:
        e.target = 19.0  # all listed zones want heat
    d, _ = tick(cfg, make_inputs(cfg, temps))
    assert d.open == {"Office", "Living Room Stairs", "Main Bedroom", "Living Room Inner",
                      "Living Room Window", "Kitchen"}
    assert "Guest Bedroom" not in d.open and "Bathroom" not in d.open
    assert d.capacity_w >= d.needed_w - 50  # 6.79 kW against 6.8 kW


def test_primary_only_opens_just_the_primary():
    cfg = seed_config()
    cfg.profiles["Office"].spare_heat = SpareHeat.PRIMARY_ONLY
    d, _ = tick(cfg, make_inputs(cfg, COLD))
    assert d.open == {"Office"}


# ------------------------------------------------------------ boiler demand and hysteresis


def test_boiler_starts_below_goal_minus_band_and_runs_to_goal_plus_band():
    cfg = seed_config()
    d, _ = tick(cfg, make_inputs(cfg, COLD | {"Office": 17.8}))
    assert not d.boiler_on  # 17.8 is inside the band below 18
    d, _ = tick(cfg, make_inputs(cfg, COLD | {"Office": 17.6}))
    assert d.boiler_on
    d, _ = tick(cfg, make_inputs(cfg, COLD | {"Office": 18.2}, boiler_on=True))
    assert d.boiler_on  # keeps running until 18.3
    d, _ = tick(cfg, make_inputs(cfg, COLD | {"Office": 18.3}, boiler_on=True))
    assert not d.boiler_on


def test_minimum_on_and_off_times():
    cfg = seed_config()
    d, _ = tick(cfg, make_inputs(cfg, COLD | {"Office": 19.0}, boiler_on=True, boiler_changed=NOW - 120))
    assert d.boiler_on  # wants off but has only run 2 min
    d, _ = tick(cfg, make_inputs(cfg, COLD, boiler_on=False, boiler_changed=NOW - 120))
    assert not d.boiler_on  # wants on but was off only 2 min
    # ...and says so, rather than claiming the room is warm enough
    assert "minimum off time (8 min left)" in d.reason
    assert d.zone_states["Office"] == "waiting"


def test_minimum_run_time_is_explained():
    cfg = seed_config()
    d, _ = tick(cfg, make_inputs(cfg, COLD | {"Office": 19.0}, boiler_on=True, boiler_changed=NOW - 120))
    assert d.boiler_on and "minimum run time" in d.reason


def test_satisfied_zone_needs_hysteresis_to_reenter():
    cfg = seed_config()
    state = State()
    d, state = tick(cfg, make_inputs(cfg, COLD | {"Landing": 16.0}), state)
    assert "Living Room Stairs" not in d.open  # Landing at target
    d, state = tick(cfg, make_inputs(cfg, COLD | {"Landing": 15.8}), state)
    assert "Living Room Stairs" not in d.open  # still within hysteresis
    d, state = tick(cfg, make_inputs(cfg, COLD | {"Landing": 15.6}), state)
    assert "Living Room Stairs" in d.open


def test_boiler_off_keeps_only_primary_open():
    cfg = seed_config()
    d, _ = tick(cfg, make_inputs(cfg, COLD | {"Office": 18.5}, open_={"Office", "Living Room Stairs"}))
    assert not d.boiler_on
    assert d.open == {"Office"}


# ------------------------------------------------------------ hold times


def test_radiator_change_is_held_for_ten_minutes():
    cfg = seed_config()
    changed = {r: LONG_AGO for r in cfg.radiators} | {"Living Room Stairs": NOW - 120}
    d, _ = tick(cfg, make_inputs(cfg, COLD, changed=changed))
    assert "Living Room Stairs" not in d.open
    assert "Living Room Stairs" in d.held


def test_off_mode_ignores_hold_times():
    cfg = seed_config()
    changed = {r: NOW - 60 for r in cfg.radiators}
    d, _ = tick(cfg, make_inputs(cfg, COLD, mode=Mode.OFF, open_=set(cfg.radiators), changed=changed,
                                 boiler_on=True))
    assert not d.boiler_on and d.open == set()


# ------------------------------------------------------------ windows, faults, sensors


def test_window_in_buffer_zone_skips_it():
    cfg = seed_config()
    d, _ = tick(cfg, make_inputs(cfg, COLD, windows={"Landing": True}))
    assert d.open == {"Office"}
    assert d.zone_states["Landing"] == "window"


def test_window_in_primary_zone_stops_the_boiler():
    cfg = seed_config()
    d, _ = tick(cfg, make_inputs(cfg, COLD, windows={"Office": True}))
    assert not d.boiler_on
    assert "Office" not in d.open


def test_faulty_trv_left_out_of_plan_and_capacity():
    cfg = seed_config()
    d, _ = tick(cfg, make_inputs(cfg, COLD, faulty={"Living Room Stairs"}))
    assert d.open == {"Office"}


def test_primary_thermometer_offline_stops_boiler_and_notifies_once():
    cfg = seed_config()
    inp = make_inputs(cfg, COLD)
    inp.readings["Office"] = Reading(17.0, NOW - 4 * 3600)  # silent for longer than the quiet limit
    d, state = tick(cfg, inp)
    assert not d.boiler_on
    assert [n.kind for n in d.notifications] == ["sensor_offline"]
    d, state = tick(cfg, inp, state)
    assert d.notifications == []


def test_quiet_thermometer_is_fine_while_its_room_is_not_heating():
    cfg = seed_config()
    inp = make_inputs(cfg, COLD | {"Office": 19.0})  # boiler off, Office warm
    inp.readings["Office"] = Reading(19.0, NOW - 2 * 3600)  # unchanged for 2 h: no reports
    d, _ = tick(cfg, inp)
    assert d.zone_states["Office"] != "offline" and d.notifications == []


def test_silent_thermometer_while_heating_counts_from_when_heating_started():
    cfg = seed_config()
    # Boiler came on 20 min ago; the sensor last spoke 2 h ago. Not offline yet.
    inp = make_inputs(cfg, COLD, boiler_on=True, boiler_changed=NOW - 20 * 60, open_=("Office",))
    inp.readings["Office"] = Reading(17.0, NOW - 2 * 3600)
    d, _ = tick(cfg, inp)
    assert d.boiler_on and d.zone_states["Office"] == "heating"
    # 50 min of heating with not a single report: that's a dead sensor.
    inp = make_inputs(cfg, COLD, boiler_on=True, boiler_changed=NOW - 50 * 60, open_=("Office",))
    inp.readings["Office"] = Reading(17.0, NOW - 2 * 3600)
    d, _ = tick(cfg, inp)
    assert not d.boiler_on and d.zone_states["Office"] == "offline"


# ------------------------------------------------------------ frost and modes


def test_frost_heats_cold_zone_in_auto_even_when_primary_satisfied():
    cfg = seed_config()
    d, state = tick(cfg, make_inputs(cfg, COLD | {"Office": 19.0, "Guest Bedroom": 6.5}))
    assert d.boiler_on
    assert d.open == set(cfg.radiators)  # every radiator, so the boiler runs one long burn
    # keeps heating until 8° (7 + 1 release)
    d, state = tick(cfg, make_inputs(cfg, COLD | {"Office": 19.0, "Guest Bedroom": 7.5}, boiler_on=True), state)
    assert "Guest Bedroom" in d.open
    d, state = tick(cfg, make_inputs(cfg, COLD | {"Office": 19.0, "Guest Bedroom": 8.0}, boiler_on=True), state)
    assert "Guest Bedroom" not in d.open


def test_off_mode_still_protects_against_frost():
    cfg = seed_config()
    d, _ = tick(cfg, make_inputs(cfg, COLD, mode=Mode.OFF, boiler_on=True, open_={"Office"}))
    assert not d.boiler_on and d.open == set()  # off straight away, no minimum run
    d, st = tick(cfg, make_inputs(cfg, COLD | {"Bathroom": 6.0}, mode=Mode.OFF))
    assert d.boiler_on and d.open == set(cfg.radiators) and "frost protection" in d.reason  # all open
    # Frost stops once the room is back above frost temperature + release
    d, _ = tick(cfg, make_inputs(cfg, COLD | {"Bathroom": 8.5}, mode=Mode.OFF, boiler_on=True,
                                 open_=set(cfg.radiators)), st)
    assert not d.boiler_on and d.open == set()


def test_manual_mode_still_protects_against_frost():
    cfg = seed_config()
    d, st = tick(cfg, make_inputs(cfg, COLD | {"Bathroom": 6.0}, mode=Mode.MANUAL, open_={"Kitchen"}))
    assert d.boiler_on and d.open == set(cfg.radiators)
    d, _ = tick(cfg, make_inputs(cfg, COLD | {"Bathroom": 8.5}, mode=Mode.MANUAL, boiler_on=True,
                                 open_=set(cfg.radiators)), st)
    assert not d.boiler_on and d.open == {"Kitchen"}  # puts back what it changed, leaves the rest


def test_boost_without_target_on_unlisted_zone_heats_to_goal():
    cfg = seed_config()
    prof = apply_overrides(cfg.profiles["Office"], [Override("Bathroom", expires_at=NOW + 600)], NOW)
    entry = next(e for e in prof.priority if e.zone == "Bathroom")
    assert entry.target == cfg.profiles["Office"].goal


def test_manual_mode_touches_nothing():
    cfg = seed_config()
    d, _ = tick(cfg, make_inputs(cfg, COLD, mode=Mode.MANUAL, boiler_on=True, open_={"Kitchen"}))
    assert d.boiler_on and d.open == {"Kitchen"}
    d, _ = tick(cfg, make_inputs(cfg, COLD, mode=Mode.MANUAL, boiler_on=False, open_={"Kitchen"}))
    assert not d.boiler_on and d.open == {"Kitchen"}


def test_manual_mode_boost_runs_boiler_then_puts_things_back():
    cfg = seed_config()
    boost = [Override("Office", target=20.0, expires_at=NOW + 3600, profile="Office")]
    # Office at 17° boosted to 20°: its radiator opens and the boiler starts; Kitchen (opened by hand) stays
    d, st = tick(cfg, make_inputs(cfg, COLD, mode=Mode.MANUAL, open_={"Kitchen"}, overrides=boost))
    assert d.boiler_on and d.open == {"Kitchen", "Office"}
    assert "boosting Office" in d.reason and d.zone_states["Office"] == "heating"
    # Office reaches 20°: boiler off (it ran long enough) and the radiator it opened closes
    warm = COLD | {"Office": 20.1}
    d, st = tick(cfg, make_inputs(cfg, warm, mode=Mode.MANUAL, boiler_on=True, open_={"Kitchen", "Office"},
                                  overrides=boost), st)
    assert not d.boiler_on and d.open == {"Kitchen"}


def test_manual_mode_boost_respects_boiler_minimums():
    cfg = seed_config()
    boost = [Override("Office", target=20.0, expires_at=NOW + 3600, profile="Office")]
    d, _ = tick(cfg, make_inputs(cfg, COLD, mode=Mode.MANUAL, boiler_changed=NOW - 60, overrides=boost))
    assert not d.boiler_on and "minimum off time" in d.reason
    assert "Office" in d.open  # radiator opens ready


def test_manual_mode_boost_does_not_turn_off_a_boiler_turned_on_by_hand():
    cfg = seed_config()
    boost = [Override("Office", target=20.0, expires_at=NOW + 3600, profile="Office")]
    warm = COLD | {"Office": 20.5}
    d, _ = tick(cfg, make_inputs(cfg, warm, mode=Mode.MANUAL, boiler_on=True, overrides=boost))
    assert d.boiler_on


def test_manual_mode_boost_without_target_uses_profile_goal():
    cfg = seed_config()
    goal = cfg.profiles["Office"].goal
    boost = [Override("Office", expires_at=NOW + 3600, profile="Office")]
    d, _ = tick(cfg, make_inputs(cfg, COLD | {"Office": goal - 1}, mode=Mode.MANUAL, overrides=boost))
    assert d.boiler_on


# ------------------------------------------------------------ overrides and notifications


def test_boost_moves_zone_to_top_with_new_target():
    cfg = seed_config()
    prof = apply_overrides(cfg.profiles["Office"], [Override("Kitchen", target=18.0, position=0,
                                                             expires_at=NOW + 1200)], NOW)
    assert prof.priority[0].zone == "Kitchen" and prof.priority[0].target == 18.0
    assert prof.priority[0].buffer  # a boosted zone always gets heat in buffer mode
    expired = apply_overrides(cfg.profiles["Office"], [Override("Kitchen", 18.0, 0, NOW - 1)], NOW)
    assert expired.priority[0].zone == "Landing"


def test_boost_in_buffer_mode_opens_boosted_zone():
    cfg = seed_config()
    d, _ = tick(cfg, make_inputs(cfg, COLD, overrides=[Override("Kitchen", 18.0, 0, NOW + 1200)]))
    assert "Kitchen" in d.open


def test_all_overflow_satisfied_notifies_after_fifteen_minutes():
    cfg = seed_config()
    cfg.profiles["Office"].spare_heat = SpareHeat.FILL_TO_MINIMUM
    warm = {z: 20.0 for z in COLD} | {"Office": 17.0}
    _, state = tick(cfg, make_inputs(cfg, warm))
    inp = make_inputs(cfg, warm, boiler_on=True, boiler_changed=NOW, now=NOW + 901)
    d, state = tick(cfg, inp, state)
    assert [n.kind for n in d.notifications] == ["all_satisfied"]


def test_runtime_warning_after_two_hours():
    cfg = seed_config()
    d, _ = tick(cfg, make_inputs(cfg, COLD, boiler_on=True, boiler_changed=NOW - 2 * 3600 - 1))
    assert "runtime" in [n.kind for n in d.notifications]


# ------------------------------------------------------------ window detection


def test_window_detector_flags_fast_drop_and_clears_after_hold():
    w = WindowDetector()
    assert not w.update(0, 18.0)
    assert not w.update(120, 17.6)
    assert w.update(240, 17.1)  # 0.9° in 4 min
    assert w.is_open(240 + 1199)
    assert not w.is_open(240 + 1201)


# ------------------------------------------------------------ room minimums


def _with_minimums(cfg, **mins):
    for name, m in mins.items():
        cfg.zones[name.replace("_", " ")].minimum = m
    return cfg


def test_rooms_below_minimum_open_whenever_the_boiler_runs_in_any_profile():
    cfg = _with_minimums(seed_config(), Kitchen=14, Bathroom=14, Main_Bedroom=14, Guest_Bedroom=13)
    temps = COLD | {"Kitchen": 12.0, "Bathroom": 13.5}
    # Office profile, primary only: normally just the Office radiator
    cfg.profiles["Office"].spare_heat = SpareHeat.PRIMARY_ONLY
    d, _ = tick(cfg, make_inputs(cfg, temps))
    assert d.boiler_on
    assert {"Office", "Kitchen", "Bathroom", "Main Bedroom"} <= d.open
    assert "Guest Bedroom" not in d.open  # 13.0 is not below a 13° minimum
    assert d.zone_states["Kitchen"] == "minimum"
    assert d.radiator_reasons["Kitchen"] == "minimum: Kitchen 12.0° below its 14° minimum"


def test_minimums_never_start_the_boiler():
    cfg = _with_minimums(seed_config(), Kitchen=14)
    d, _ = tick(cfg, make_inputs(cfg, COLD | {"Office": 19.0, "Kitchen": 10.0}))
    assert not d.boiler_on
    assert "Kitchen" not in d.open


def test_minimum_uses_hysteresis_and_skips_open_windows():
    cfg = _with_minimums(seed_config(), Kitchen=14)
    d, st = tick(cfg, make_inputs(cfg, COLD | {"Kitchen": 13.0}))
    assert "Kitchen" in d.open
    d, st = tick(cfg, make_inputs(cfg, COLD | {"Kitchen": 14.0}, open_=d.open), st)
    assert "Kitchen" not in d.open  # reached its minimum
    d, st = tick(cfg, make_inputs(cfg, COLD | {"Kitchen": 13.8}, open_=d.open), st)
    assert "Kitchen" not in d.open  # within hysteresis
    d, _ = tick(cfg, make_inputs(cfg, COLD | {"Kitchen": 12.0}, windows={"Kitchen": True}), st)
    assert "Kitchen" not in d.open
