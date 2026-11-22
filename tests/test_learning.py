"""Learning: matches the Pi's numbers and learns a known cooling curve."""

import math

from custom_components.smart_heating.learning import ZoneLearning, estimate, seed_from_pi
from custom_components.smart_heating.seed import PI_COOLING


def test_seed_reproduces_pi_estimates():
    # Values the Pi's estimate script wrote on 22 Nov (ph, pf, pg, ps, c)
    expected = {
        "Living Room": (36.8, 1.21, 1.11, 0.9, 71),
        "Office": (18.4, 1.35, 1.28, 0.86, 66),
        "Main Bedroom": (44.0, 1.74, 3.23, 1.52, 39),
        "Bathroom": (30.0, 1.35, 1.52, 0.93, 65),
    }
    for zone, (ph, pf, pg, ps, c) in expected.items():
        e = estimate(seed_from_pi(PI_COOLING[zone]).cooling)
        assert (e.half_life_h, e.half_life_factor, e.free_heat, e.free_heat_sd, e.confidence) == (ph, pf, pg, ps, c)


def _simulate(zl: ZoneLearning, hours: int, *, k: float, g: float, start: float, outdoor_fn,
              heating_kw: float = 0.0, warm_per_kw: float = 0.0, t0: float = 0.0) -> float:
    temp = start
    for minute in range(hours * 60):
        now = t0 + minute * 60
        out = outdoor_fn(now)
        heating = heating_kw > 0
        dT = (-k * (temp - (out + g)) + warm_per_kw * heating_kw) / 60
        temp += dT
        zl.observe(now, temp, out, quiet=not heating, heating=heating, delivered_kw=heating_kw)
        if minute % 60 == 0:
            zl.close_hour(now)
    return temp


def test_learns_a_known_cooling_curve():
    zl = ZoneLearning()
    k, g = math.log(2) / 30, 2.0  # 30 h half-life, settles 2° above outdoors
    temp = 19.0
    t0 = 0.0
    for night in range(25):
        # Cool down overnight with a varying outdoor temperature, then reheat
        temp = _simulate(zl, 10, k=k, g=g, start=19.0 - (night % 4), t0=t0,
                         outdoor_fn=lambda t: 4 + 3 * math.sin(t / 20000))
        t0 += 24 * 3600
    e = estimate(zl.cooling)
    assert 24 <= e.half_life_h <= 38
    assert 1.0 <= e.free_heat <= 3.0
    assert e.confidence > 40


def test_heating_hours_are_not_cooling_samples_but_teach_warm_up():
    k, g = math.log(2) / 30, 2.0
    # Learn the cooling first (quiet nights), as happens in the house
    zl = ZoneLearning()
    t0 = 0.0
    for night in range(25):
        _simulate(zl, 10, k=k, g=g, start=19.0 - (night % 4), t0=t0, outdoor_fn=lambda t: 4 + 3 * math.sin(t / 20000))
        t0 += 24 * 3600
    cooling_n = zl.cooling.n
    # Then a heated spell: no new cooling samples, but warm-up samples
    _simulate(zl, 8, k=k, g=g, start=13.0, t0=t0, outdoor_fn=lambda t: 5.0, heating_kw=1.0, warm_per_kw=1.2)
    assert zl.cooling.n == cooling_n  # heated hours add no cooling samples
    assert zl.warmup.n >= 5
    assert 1.0 <= zl.warmup.mean <= 1.4  # about 1.2 °C/h per kW once cooling is added back


def test_round_trips_through_storage():
    zl = seed_from_pi(PI_COOLING["Kitchen"])
    zl.warmup.add(1.1)
    back = ZoneLearning.from_dict(zl.to_dict())
    assert back.cooling == zl.cooling and back.warmup == zl.warmup
