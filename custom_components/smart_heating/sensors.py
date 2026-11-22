"""Sanity filter for room thermometer readings (no Home Assistant imports)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReadingFilter:
    """Hold the last good reading through glitches.

    A reading is ignored when it is outside a plausible indoor range, or when it jumps more
    than ``max_jump`` from the last good reading. A jump that holds steady (within
    ``settle``) for ``confirm_s`` is real (say the sensor was moved) and is accepted.
    An exact 0.0 with no good reading yet is treated as a jump too: Sonoff sensors send it
    while rejoining or updating.
    """

    max_jump: float = 3.0
    settle: float = 1.0
    confirm_s: float = 300
    low: float = -10.0
    high: float = 45.0
    good: tuple[float, float] | None = None  # (temp, when)
    candidate: tuple[float, float] | None = None  # (temp, first seen)
    seen: float | None = None
    rejected: int = 0
    last_rejected: float | None = field(default=None)

    def feed(self, temp: float, when: float) -> tuple[float, float] | None:
        """Return the reading to use, as (temp, when), or None if there is none yet."""
        if when == self.seen:
            return self.good  # same report again: no new information
        self.seen = when
        if not self.low <= temp <= self.high:
            return self._reject(temp)
        suspicious = (temp == 0.0) if self.good is None else abs(temp - self.good[0]) > self.max_jump
        if not suspicious:
            self.good, self.candidate = (temp, when), None
            return self.good
        if self.candidate and abs(temp - self.candidate[0]) <= self.settle:
            if when - self.candidate[1] >= self.confirm_s:
                self.good, self.candidate = (temp, when), None
                return self.good
        else:
            self.candidate = (temp, when)
        return self._reject(temp)

    def _reject(self, temp: float) -> tuple[float, float] | None:
        self.rejected += 1
        self.last_rejected = temp
        return self.good
