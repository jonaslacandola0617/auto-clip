from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any


COMMON_RATES: dict[str, Fraction] = {
    "23.976": Fraction(24000, 1001),
    "24": Fraction(24, 1),
    "25": Fraction(25, 1),
    "29.97": Fraction(30000, 1001),
    "30": Fraction(30, 1),
    "50": Fraction(50, 1),
    "59.94": Fraction(60000, 1001),
    "60": Fraction(60, 1),
}


@dataclass(frozen=True, slots=True)
class Rational:
    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        if self.denominator == 0:
            raise ValueError("denominator must not be zero")
        normalized = Fraction(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", normalized.numerator)
        object.__setattr__(self, "denominator", normalized.denominator)

    @classmethod
    def from_fraction(cls, value: Fraction) -> "Rational":
        return cls(value.numerator, value.denominator)

    @classmethod
    def parse(cls, value: str | dict[str, Any] | "Rational") -> "Rational":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            num, den = value.split("/", 1)
            return cls(int(num), int(den))
        return cls(int(value["numerator"]), int(value["denominator"]))

    def as_fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def to_dict(self) -> dict[str, int]:
        return {"numerator": self.numerator, "denominator": self.denominator}


@dataclass(frozen=True, slots=True)
class MediaTime:
    """Integer ticks in an explicit seconds-per-tick time base."""

    value: int
    time_base: Rational

    @classmethod
    def from_seconds(cls, seconds: Fraction | int | str, time_base: Rational, *, exact: bool = True) -> "MediaTime":
        seconds_fraction = seconds if isinstance(seconds, Fraction) else Fraction(str(seconds))
        ticks = seconds_fraction / time_base.as_fraction()
        if exact and ticks.denominator != 1:
            raise ValueError("seconds are not exactly representable in this time base")
        return cls(ticks.numerator if ticks.denominator == 1 else round(ticks), time_base)

    @classmethod
    def from_frames(cls, frames: int, frame_rate: Rational) -> "MediaTime":
        return cls(frames, Rational(frame_rate.denominator, frame_rate.numerator))

    @property
    def seconds(self) -> Fraction:
        return self.value * self.time_base.as_fraction()

    def to_frames(self, frame_rate: Rational, *, exact: bool = True) -> int:
        frames = self.seconds * frame_rate.as_fraction()
        if exact and frames.denominator != 1:
            raise ValueError("timestamp does not land on an exact frame boundary")
        return frames.numerator // frames.denominator

    def rescale(self, new_time_base: Rational, *, exact: bool = True) -> "MediaTime":
        ticks = self.seconds / new_time_base.as_fraction()
        if exact and ticks.denominator != 1:
            raise ValueError("timestamp cannot be represented exactly in target time base")
        return MediaTime(round(float(ticks)), new_time_base)

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "time_base": self.time_base.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MediaTime":
        return cls(int(data["value"]), Rational.parse(data["time_base"]))


@dataclass(frozen=True, slots=True)
class FrameRateAssessment:
    rate: Rational
    variable_frame_rate: bool
    fallback: str | None = None


def assess_frame_rate(avg_frame_rate: str, real_frame_rate: str, *, tolerance: Fraction = Fraction(1, 1000)) -> FrameRateAssessment:
    avg = Fraction(avg_frame_rate)
    real = Fraction(real_frame_rate)
    delta = abs(avg - real) / max(avg, real)
    vfr = delta > tolerance
    return FrameRateAssessment(
        Rational.from_fraction(avg),
        vfr,
        "timestamp-aware source mapping required; normalize a labeled working proxy before editable export" if vfr else None,
    )
