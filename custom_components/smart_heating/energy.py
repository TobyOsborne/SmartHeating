"""Running costs, cost forecast and flow temperature advice (no Home Assistant imports).

Costs
-----
Every day the ledger records how long the boiler ran and the mean outdoor temperature. When
the smart meter's "last full day" gas figure arrives (a day or so late), it is filed against
that day. From those days two simple fits are learned:

* gas used per day = base (hot water, cooking) + burn rate × boiler hours
* boiler hours per day = hours per degree-day × heating degree-days (base 15.5°)

Today's cost is estimated from the boiler hours so far. The 7-day forecast runs the weather
forecast's daily temperatures through both fits.

Flow temperature
----------------
Each room's learned cooling half-life (k) and warm-up rate (°C/h per kW, the inverse of its
heat capacity C) give its heat loss H = k × C in kW per °C. Holding the primary room at its
goal needs H × (goal − outdoor − free heat). The suggested flow is the lowest that lets that
room's radiators give this with a margin to warm up: lower flow means a cooler return and a
boiler that condenses more.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .controller import radiator_output_w

HDD_BASE = 15.5
DEFAULT_BURN_KW = 7.0  # gas kW while firing, until learned (i30 at low fire, ~90 % efficient)
DEFAULT_BASE_KWH = 3.0  # hot water etc. per day, until learned
MIN_DAYS = 4
FULL_DAY_S = 20 * 3600  # a day only teaches the models if it was recorded for at least this long
KEEP_DAYS = 60
FLOW_MARGIN = 1.3  # radiators must give 30 % more than the loss, so the room can warm up
FLOW_MIN, FLOW_MAX = 40, 75


@dataclass
class DayRecord:
    hours: float = 0.0  # boiler on-time
    out_sum: float = 0.0
    out_n: int = 0
    gas_kwh: float | None = None  # from the smart meter, when it arrives
    cost: float | None = None
    tracked_s: float = 0.0  # how much of the day was actually recorded
    runs: int = 0  # times the boiler came on

    @property
    def full(self) -> bool:
        return self.tracked_s >= FULL_DAY_S

    @property
    def out_mean(self) -> float | None:
        return self.out_sum / self.out_n if self.out_n else None

    @property
    def hdd(self) -> float | None:
        m = self.out_mean
        return max(0.0, HDD_BASE - m) if m is not None else None


@dataclass
class EnergyLedger:
    days: dict[str, DayRecord] = field(default_factory=dict)  # "YYYY-MM-DD" -> record

    # ---------------------------------------------------------------- recording

    def day(self, date: str) -> DayRecord:
        rec = self.days.get(date)
        if rec is None:
            rec = self.days[date] = DayRecord()
            for old in sorted(self.days)[:-KEEP_DAYS]:
                del self.days[old]
        return rec

    def add_runtime(self, date: str, seconds: float) -> None:
        self.day(date).hours += seconds / 3600

    def add_run(self, date: str) -> None:
        self.day(date).runs += 1

    def add_tracked(self, date: str, seconds: float) -> None:
        self.day(date).tracked_s += seconds

    def add_outdoor(self, date: str, temp: float) -> None:
        rec = self.day(date)
        rec.out_sum += temp
        rec.out_n += 1

    def set_gas(self, date: str, kwh: float, cost: float | None) -> None:
        rec = self.day(date)
        rec.gas_kwh, rec.cost = kwh, cost

    # ---------------------------------------------------------------- models

    def gas_model(self) -> tuple[float, float, int, bool]:
        """(base kWh/day, burn kW, days used, learned?) from days with both meter and runtime."""
        # Only days recorded from start to finish: a part-recorded day under-counts boiler
        # hours, which would push its gas into the hot-water base.
        pts = [(r.hours, r.gas_kwh) for r in self.days.values() if r.gas_kwh is not None and r.full]
        n = len(pts)
        if n >= MIN_DAYS:
            mx = sum(p[0] for p in pts) / n
            my = sum(p[1] for p in pts) / n
            sxx = sum((p[0] - mx) ** 2 for p in pts)
            if sxx > 1.0:  # needs days with different amounts of heating
                rate = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx
                base = my - rate * mx
                if 2.0 <= rate <= 30.0 and base >= 0:
                    return base, rate, n, True
        base = DEFAULT_BASE_KWH
        quiet = [p[1] for p in pts if p[0] < 0.2]
        if quiet:
            base = sum(quiet) / len(quiet)
        return base, DEFAULT_BURN_KW, n, False

    def hours_model(self) -> tuple[float | None, int]:
        """Boiler hours per heating degree-day, from complete days that needed heat."""
        pts = [(r.hdd, r.hours) for r in self.days.values() if r.hdd and r.full]
        n = len(pts)
        if n < MIN_DAYS:
            return None, n
        return sum(h * d for d, h in pts) / sum(d * d for d, _ in pts), n

    def estimate_day(self, hours: float, unit_rate: float | None, standing: float | None) -> dict:
        base, rate, _, _ = self.gas_model()
        kwh = base + rate * hours
        cost = kwh * unit_rate + (standing or 0) if unit_rate is not None else None
        return {"hours": round(hours, 2), "gas_kwh": round(kwh, 1),
                "cost": round(cost, 2) if cost is not None else None}

    def forecast(self, daily_temps: list[tuple[str, float]], unit_rate: float | None,
                 standing: float | None) -> dict:
        per_hdd, n_days = self.hours_model()
        _, _, n_gas, gas_learned = self.gas_model()
        if per_hdd is None:
            return {"status": "learning", "days_recorded": n_days, "needed": MIN_DAYS,
                    "total": None, "days": []}
        days = []
        for date, temp in daily_temps:
            hours = min(24.0, per_hdd * max(0.0, HDD_BASE - temp))
            days.append({"date": date, "outdoor": round(temp, 1), **self.estimate_day(hours, unit_rate, standing)})
        costs = [d["cost"] for d in days if d["cost"] is not None]
        return {"status": "ok" if gas_learned else "rough", "days_recorded": n_days,
                "gas_days": n_gas, "hours_per_degree_day": round(per_hdd, 3),
                "total": round(sum(costs), 2) if costs else None, "days": days}

    # ---------------------------------------------------------------- storage

    def to_dict(self) -> dict:
        return {d: asdict(r) for d, r in self.days.items()}

    @classmethod
    def from_dict(cls, data: dict) -> "EnergyLedger":
        names = set(DayRecord.__dataclass_fields__)
        return cls({d: DayRecord(**{k: v for k, v in r.items() if k in names}) for d, r in data.items()})


def suggest_flow(*, half_life_h: float, free_heat: float, warmup_per_kw: float | None,
                 warmup_confidence: int, goal: float, outdoor: float | None,
                 radiators: list[tuple[float, float]], drop: float, current_flow: float) -> dict:
    """Lowest flow temperature at which the primary room's radiators can hold its goal."""
    if outdoor is None:
        return {"status": "no_outdoor", "suggested": None,
                "reason": "No outdoor temperature, so no advice yet."}
    if warmup_per_kw is None or warmup_per_kw <= 0 or warmup_confidence < 40 or not radiators:
        return {"status": "learning", "suggested": None,
                "reason": "Still learning how fast this room warms up. Advice appears once warm-up "
                          "confidence passes 40% (usually a few days of heating)."}
    k = math.log(2) / half_life_h  # per hour
    capacity = 1 / warmup_per_kw  # kWh per °C
    loss_per_deg = k * capacity  # kW per °C
    need_kw = max(0.0, loss_per_deg * (goal - outdoor - free_heat))

    def output_kw(flow: float) -> float:
        return sum(radiator_output_w(w, flow, drop, goal, n) for w, n in radiators) / 1000

    flow = None
    for f in range(FLOW_MIN, FLOW_MAX + 1):
        if output_kw(f) >= need_kw * FLOW_MARGIN:
            flow = f
            break
    suggested = FLOW_MAX if flow is None else int(math.ceil(flow / 5) * 5)
    now_kw = output_kw(current_flow)
    if flow is None:
        reason = (f"Holding {goal:g}° at {outdoor:.1f}° outside needs about {need_kw:.1f} kW; "
                  f"even {FLOW_MAX}° flow only gives {output_kw(FLOW_MAX):.1f} kW from this room's radiators.")
    else:
        reason = (f"Holding {goal:g}° at {outdoor:.1f}° outside needs about {need_kw:.1f} kW. "
                  f"This room's radiators give that, with room to warm up, at {suggested}° flow "
                  f"({output_kw(suggested):.1f} kW). At today's {current_flow:g}° they give {now_kw:.1f} kW.")
    if suggested < current_flow:
        reason += " Turning the boiler's heating dial down makes it condense more, so it uses less gas."
    elif suggested > current_flow:
        reason += " The current setting will struggle to keep up in this weather."
    return {"status": "ok", "suggested": suggested, "need_kw": round(need_kw, 2),
            "loss_kw_per_deg": round(loss_per_deg, 3), "output_now_kw": round(now_kw, 2),
            "reason": reason}
