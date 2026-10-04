"""Small numeric helpers used across the engine."""

from __future__ import annotations

from collections.abc import Iterable


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def noisy_or(contributions: Iterable[float]) -> float:
    """Combine independent 0..1 "failure pressures" into one 0..1 value.

    1 - prod(1 - c_i). Any single input at 1.0 saturates the result, several
    moderate inputs compound, and the result never exceeds 1.0. Used wherever
    the engine asks "how close is this system to breaking?".
    """
    survive = 1.0
    for c in contributions:
        survive *= 1.0 - clamp(c)
    return 1.0 - survive
