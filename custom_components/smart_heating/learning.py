"""Per-zone cooling model and warm-up rate, ported from the Pi's learner.

Cooling model (same maths as the Pi's "Cooling Model Learn/Estimate" scripts):
  dT/dt = -k (T - (T_out + g))
- k: cooling rate per hour (half-life = ln 2 / k)
- g: free-heat offset, how far above outdoors the zone settles with no heating

Each hourly sample is the 2-hour temperature change across three hourly means
against the indoor-outdoor gap, taken only in quiet hours (boiler off and none
of the zone's radiators open). Samples go into exponentially weighted sums
(L = 0.997 per sample, roughly a four-week memory). A Bayesian grid over
half-life (6 h to ~470 h) and free heat (0 to 8 °C), with gentle priors
(half-life about 48 h, free heat about 3 °C), turns the sums into a best
estimate with uncertainty and a confidence percentage.

Warm-up rate: °C per hour per kW of modelled radiator output, learned from
hours when the zone's radiators were open and the boiler ran the whole hour.

Pure Python, no Home Assistant imports.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

FORGET = 0.997


@dataclass
class CoolingSums:
    n: float = 0.0
    x: float = 0.0
    y: float = 0.0
    xx: float = 0.0
    xy: float = 0.0
    yy: float = 0.0
    last: str = ""  # when the last sample was added

    def add(self, x: float, rate: float, when: str = "") -> None:
        L = FORGET
        self.n = self.n * L + 1
        self.x = self.x * L + x
        self.y = self.y * L + rate
        self.xx = self.xx * L + x * x
        self.xy = self.xy * L + x * rate
        self.yy = self.yy * L + rate * rate
        self.last = when


@dataclass
class CoolingEstimate:
    half_life_h: float  # best estimate
    half_life_factor: float  # × or ÷ this for one standard deviation
    free_heat: float  # °C above outdoors it settles at
    free_heat_sd: float
    confidence: int  # 0-100
    samples: float

    @property
    def k(self) -> float:
        return math.log(2) / self.half_life_h

    def settling_temp(self, outdoor: float) -> float:
        return outdoor + self.free_heat

    def cooling_rate(self, indoor: float, outdoor: float) -> float:
        """°C per hour the zone loses right now with no heating (positive = cooling)."""
        return self.k * (indoor - self.settling_temp(outdoor))


def estimate(s: CoolingSums) -> CoolingEstimate:
    """Bayesian grid estimate, identical to the Pi's estimate script."""
    n = s.n
    if n > 0:
        sxxc = s.xx - s.x * s.x / n
        sxyc = s.xy - s.x * s.y / n
        syyc = s.yy - s.y * s.y / n
    else:
        sxxc = sxyc = syyc = 0.0
    b = sxyc / sxxc if sxxc > 0.01 else 0.0
    s2 = 8 * max((syyc - b * sxyc) / max(n - 2, 1), 0.0004)

    grid = []
    for i in range(26):
        hl = 6 * 1.19 ** i
        k = 0.6931 / hl
        for j in range(21):
            g = j * 0.4
            a = k * g
            bb = -k
            sse = (s.yy - 2 * a * s.y - 2 * bb * s.xy + n * a * a + 2 * a * bb * s.x + bb * bb * s.xx)
            lp = -sse / (2 * s2) - (math.log(hl / 48)) ** 2 / (2 * 0.81) - (g - 3) ** 2 / 12.5
            grid.append((lp, hl, g))
    m = max(lp for lp, _, _ in grid)
    z = l = ll = gs = gg = 0.0
    for lp, hl, g in grid:
        w = math.exp(lp - m)
        z += w
        l += w * math.log(hl)
        ll += w * math.log(hl) ** 2
        gs += w * g
        gg += w * g * g
    ml = l / z
    sdl = max(ll / z - ml * ml, 0) ** 0.5
    mg = gs / z
    sdg = max(gg / z - mg * mg, 0) ** 0.5
    c = round(100 * (1 - min(1, (sdl / 0.9 + sdg / 2.5) / 2)))
    return CoolingEstimate(
        half_life_h=round(math.exp(ml), 1),
        half_life_factor=round(math.exp(sdl), 2),
        free_heat=round(mg, 2),
        free_heat_sd=round(sdg, 2),
        confidence=int(c),
        samples=round(n, 1),
    )


@dataclass
class WarmupStats:
    """Exponentially weighted mean of °C/h per kW delivered."""

    n: float = 0.0
    total: float = 0.0
    last: str = ""

    def add(self, rate_per_kw: float, when: str = "") -> None:
        self.n = self.n * FORGET + 1
        self.total = self.total * FORGET + rate_per_kw
        self.last = when

    @property
    def mean(self) -> float | None:
        return self.total / self.n if self.n >= 3 else None

    @property
    def confidence(self) -> int:
        # Rough: 0 % with no runs, about 90 % after 20 heating hours
        return int(round(100 * (1 - math.exp(-self.n / 9)))) if self.n else 0


@dataclass
class HourSlot:
    """One clock hour of observations for one zone."""

    temp_sum: float = 0.0
    out_sum: float = 0.0
    count: int = 0
    quiet: bool = True  # boiler off and no radiator of the zone open all hour
    heating: bool = True  # boiler on and a radiator of the zone open all hour
    kw_sum: float = 0.0  # modelled kW delivered, summed over ticks


@dataclass
class ZoneLearning:
    cooling: CoolingSums = field(default_factory=CoolingSums)
    warmup: WarmupStats = field(default_factory=WarmupStats)
    hours: dict[int, HourSlot] = field(default_factory=dict)  # hour index -> slot (not persisted)

    def to_dict(self) -> dict:
        return {"cooling": asdict(self.cooling), "warmup": asdict(self.warmup)}

    @classmethod
    def from_dict(cls, data: dict) -> "ZoneLearning":
        return cls(
            cooling=CoolingSums(**{k: v for k, v in data.get("cooling", {}).items()
                                   if k in CoolingSums.__dataclass_fields__}),
            warmup=WarmupStats(**{k: v for k, v in data.get("warmup", {}).items()
                                  if k in WarmupStats.__dataclass_fields__}),
        )

    # ------------------------------------------------------------ sampling

    def observe(self, now: float, temp: float | None, outdoor: float | None, quiet: bool,
                heating: bool, delivered_kw: float) -> None:
        """Called every tick. Groups readings into clock hours."""
        if temp is None or outdoor is None:
            return
        hour = int(now // 3600)
        slot = self.hours.setdefault(hour, HourSlot())
        slot.temp_sum += temp
        slot.out_sum += outdoor
        slot.count += 1
        slot.quiet = slot.quiet and quiet
        slot.heating = slot.heating and heating
        slot.kw_sum += delivered_kw if heating else 0.0
        # Forget anything older than four hours
        for h in [h for h in self.hours if h < hour - 4]:
            del self.hours[h]

    def close_hour(self, now: float, label: str = "") -> None:
        """At the start of each hour, learn from the hours just completed."""
        current = int(now // 3600)
        h0, h1, h2 = current - 3, current - 2, current - 1
        slots = [self.hours.get(h) for h in (h0, h1, h2)]
        if all(s is not None and s.count >= 20 for s in slots):
            m = [s.temp_sum / s.count for s in slots]
            out = slots[1].out_sum / slots[1].count
            if all(s.quiet for s in slots):
                rate = (m[2] - m[0]) / 2
                x = m[1] - out
                if -1.5 <= rate <= 0.3:
                    self.cooling.add(x, rate, label)
        # Warm-up: the last hour, if heated the whole hour
        last, before = self.hours.get(h2), self.hours.get(h1)
        if last and before and last.count >= 20 and before.count >= 20 and last.heating:
            kw = last.kw_sum / last.count
            rise = last.temp_sum / last.count - before.temp_sum / before.count
            if kw > 0.1 and -0.5 < rise < 5:
                # Add back what it would have lost by cooling, using the current model
                est = estimate(self.cooling)
                out = last.out_sum / last.count
                loss = est.cooling_rate(before.temp_sum / before.count, out) if self.cooling.n > 5 else 0.0
                self.warmup.add((rise + loss) / kw, label)


def seed_from_pi(raw: dict) -> ZoneLearning:
    """Start from one of the Pi's input_text cooling_model_* JSON values."""
    z = ZoneLearning()
    z.cooling = CoolingSums(n=raw.get("n", 0), x=raw.get("x", 0), y=raw.get("y", 0), xx=raw.get("xx", 0),
                            xy=raw.get("xy", 0), yy=raw.get("yy", 0), last=f"Pi {raw.get('t', '')}".strip())
    return z
