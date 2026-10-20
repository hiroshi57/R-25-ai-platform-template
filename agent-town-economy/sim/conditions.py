"""Treatment conditions (2609.11108, section 4).

Each condition only changes world state / shocks; the agent specification is
held fixed. Tourist arrival rate is per sim-hour == per pulse (a pulse is 60
simulated minutes).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class GrantSpec:
    pulse: int
    amount: int
    n_recipients: int


@dataclass
class Condition:
    name: str
    base_rate: float
    # Optional per-pulse rate overrides (used by the transient shock).
    schedule: Dict[int, float] = field(default_factory=dict)
    grant: Optional[GrantSpec] = None

    def rate_at(self, pulse: int) -> float:
        return self.schedule.get(pulse, self.base_rate)


def _shock_schedule(low: float, high: float, start: int, end: int) -> Dict[int, float]:
    # low -> high -> low across the shock window (paper: pulses 84-143).
    sched: Dict[int, float] = {}
    for p in range(start, end + 1):
        sched[p] = high
    return sched


def baseline() -> Condition:
    return Condition("baseline", base_rate=2.0)


def tourism_low() -> Condition:
    return Condition("tourism-low", base_rate=0.5)


def tourism_high() -> Condition:
    return Condition("tourism-high", base_rate=6.0)


def tourism_shock() -> Condition:
    return Condition(
        "tourism-shock",
        base_rate=2.0,
        schedule=_shock_schedule(low=2.0, high=6.0 * 14 / 6, start=84, end=143),
    )


def wealth_grant() -> Condition:
    return Condition(
        "wealth-grant",
        base_rate=2.0,
        grant=GrantSpec(pulse=24, amount=5000, n_recipients=20),
    )


ALL_CONDITIONS = {
    c.name: c
    for c in [baseline(), tourism_low(), tourism_high(), tourism_shock(), wealth_grant()]
}


def get_condition(name: str) -> Condition:
    factory = {
        "baseline": baseline,
        "tourism-low": tourism_low,
        "tourism-high": tourism_high,
        "tourism-shock": tourism_shock,
        "wealth-grant": wealth_grant,
    }
    if name not in factory:
        raise KeyError(f"unknown condition {name!r}; choose from {sorted(factory)}")
    return factory[name]()
