"""Costs, forecast and flow advice."""

from custom_components.smart_heating.energy import (
    DEFAULT_BURN_KW,
    EnergyLedger,
    suggest_flow,
)


def _day(ledger, date, hours, outdoor, gas=None):
    ledger.add_tracked(date, 24 * 3600)
    ledger.add_runtime(date, hours * 3600)
    for _ in range(1440):
        ledger.add_outdoor(date, outdoor)
    if gas is not None:
        ledger.set_gas(date, gas, None)


def test_gas_model_learns_base_and_burn_rate():
    led = EnergyLedger()
    for i, (h, out) in enumerate([(1, 12), (2, 10), (3, 8), (4, 6), (5, 4)]):
        _day(led, f"2026-11-0{i + 1}", h, out, gas=4.0 + 6.5 * h)
    base, burn, n, learned = led.gas_model()
    assert learned and n == 5
    assert round(base, 2) == 4.0 and round(burn, 2) == 6.5


def test_gas_model_falls_back_until_enough_varied_days():
    led = EnergyLedger()
    _day(led, "2026-11-01", 2, 8, gas=20)
    base, burn, n, learned = led.gas_model()
    assert not learned and burn == DEFAULT_BURN_KW


def test_forecast_uses_degree_days_and_prices():
    led = EnergyLedger()
    for i, out in enumerate([5.5, 7.5, 9.5, 11.5]):
        hdd = 15.5 - out
        _day(led, f"2026-11-0{i + 1}", 0.4 * hdd, out, gas=3 + 7 * 0.4 * hdd)
    f = led.forecast([("2026-11-10", 5.5), ("2026-11-11", 15.5)], unit_rate=0.077, standing=0.1683)
    assert f["status"] == "ok"
    cold, mild = f["days"]
    assert cold["hours"] == 4.0 and mild["hours"] == 0.0
    assert cold["gas_kwh"] == 31.0  # 3 + 7 × 4
    assert cold["cost"] == round(31 * 0.077 + 0.1683, 2)
    assert f["total"] == round(cold["cost"] + mild["cost"], 2)


def test_forecast_says_learning_at_first():
    f = EnergyLedger().forecast([("2026-11-10", 6.0)], 0.077, 0.17)
    assert f["status"] == "learning" and f["total"] is None


def test_ledger_round_trips_and_keeps_sixty_days():
    led = EnergyLedger()
    for i in range(70):
        led.add_runtime(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", 60)
    assert len(led.days) == 60
    again = EnergyLedger.from_dict(led.to_dict())
    assert again.to_dict() == led.to_dict()


LIVING_ROOM = [(2060, 1.3), (1400, 1.3), (1380, 1.3)]


def test_flow_advice_waits_for_warmup_learning():
    out = suggest_flow(half_life_h=36.8, free_heat=1.1, warmup_per_kw=None, warmup_confidence=0,
                       goal=18, outdoor=7, radiators=LIVING_ROOM, drop=10, current_flow=60)
    assert out["status"] == "learning" and out["suggested"] is None


def test_flow_advice_finds_lowest_flow_that_holds_the_room():
    # k = ln2/36.8 h, C = 1/0.2 = 5 kWh/°C -> H ≈ 0.094 kW/°C; need ≈ 0.94 kW at 7° outside
    out = suggest_flow(half_life_h=36.8, free_heat=1.1, warmup_per_kw=0.2, warmup_confidence=80,
                       goal=18, outdoor=7, radiators=LIVING_ROOM, drop=10, current_flow=60)
    assert out["status"] == "ok"
    assert 0.9 < out["need_kw"] < 1.0
    assert out["suggested"] == 45  # 41° gives 1.28 kW ≥ 1.3 × 0.94, rounded up to 45
    assert "condense" in out["reason"]
    cold = suggest_flow(half_life_h=36.8, free_heat=1.1, warmup_per_kw=0.05, warmup_confidence=80,
                        goal=18, outdoor=-3, radiators=LIVING_ROOM, drop=10, current_flow=60)
    assert cold["suggested"] > 60


def test_part_recorded_day_is_not_taken_as_a_hot_water_day():
    # Recording started mid-evening, after the old system had done that day's heating
    led = EnergyLedger()
    led.add_tracked("2026-11-22", 3 * 3600)
    led.add_outdoor("2026-11-22", 7)
    led.set_gas("2026-11-22", 21.8, 1.68)
    base, burn, n, learned = led.gas_model()
    assert n == 0 and base == 3.0 and not learned
