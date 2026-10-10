"""One place for numeric input contracts.

Every engine raises a subclass of DomainError (itself a ValueError) for input it can't
model, so callers - the GUI above all - only ever need to catch one family, and nonsense
like NaN, infinities, negative capacitance or a 0 % efficient converter never reaches
the arithmetic.
"""

from __future__ import annotations

import dataclasses
import functools
import math
from numbers import Real


class DomainError(ValueError):
    """Input outside what the model can represent (non-finite, wrong sign, wrong type...)."""


def number(name: str, x, *, gt: float | None = None, ge: float | None = None,
           lt: float | None = None, le: float | None = None, allow_none: bool = False,
           error: type[DomainError] = DomainError) -> float | None:
    """Return x as a float after checking it's a finite real within the given bounds."""
    if x is None:
        if allow_none:
            return None
        raise error(f"{name} is missing")
    if isinstance(x, bool) or not isinstance(x, Real):
        raise error(f"{name} must be a number, not {type(x).__name__} {x!r}")
    v = float(x)
    if not math.isfinite(v):
        raise error(f"{name} must be a finite number (got {x!r})")
    if gt is not None and not v > gt:
        raise error(f"{name} must be greater than {gt:g} (got {v:g})")
    if ge is not None and not v >= ge:
        raise error(f"{name} must be at least {ge:g} (got {v:g})")
    if lt is not None and not v < lt:
        raise error(f"{name} must be less than {lt:g} (got {v:g})")
    if le is not None and not v <= le:
        raise error(f"{name} must be at most {le:g} (got {v:g})")
    return v


def integer(name: str, x, *, ge: int | None = None, le: int | None = None,
            error: type[DomainError] = DomainError) -> int:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or (isinstance(x, float) and not x.is_integer()):
        raise error(f"{name} must be a whole number (got {x!r})")
    v = int(x)
    if ge is not None and v < ge:
        raise error(f"{name} must be at least {ge} (got {v})")
    if le is not None and v > le:
        raise error(f"{name} must be at most {le} (got {v})")
    return v


def text(name: str, x, *, error: type[DomainError] = DomainError, max_len: int = 200) -> str:
    if not isinstance(x, str) or not x.strip():
        raise error(f"{name} must be a non-empty name")
    if len(x) > max_len:
        raise error(f"{name} is longer than {max_len} characters")
    return x


def positive(name: str, x, **kw) -> float:
    return number(name, x, gt=0, **kw)


def non_negative(name: str, x, **kw) -> float:
    return number(name, x, ge=0, **kw)


def finite_all(name: str, values, error: type[DomainError] = DomainError) -> list[float]:
    """Every item a finite real; returns them as floats."""
    return [number(f"{name}[{k}]", v, error=error) for k, v in enumerate(values)]


def reject_json_constant(token: str):
    """json.loads(parse_constant=...) hook: refuse NaN / Infinity in saved files."""
    raise DomainError(f"file contains {token}, which isn't a usable number")


def reciprocal(what: str, x: float) -> float:
    """1/x for a result built from valid inputs, refusing when the inputs are so extreme that x
    underflows to 0 or the answer overflows (e.g. 1e-170 F × 1e-170 H)."""
    try:
        y = 1.0 / x
    except (ZeroDivisionError, OverflowError):
        y = math.inf
    if not math.isfinite(y) or y == 0:
        raise DomainError(f"{what}: these values are too extreme to compute (check the units)")
    return y


def _all_finite(result) -> bool:
    """True unless a float in the result (itself, a tuple/list of them, or a dataclass's fields) is inf/NaN."""
    if isinstance(result, float):
        return math.isfinite(result)
    if isinstance(result, (tuple, list)):
        return all(_all_finite(x) for x in result if isinstance(x, (float, tuple, list)))
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        return all(_all_finite(getattr(result, f.name)) for f in dataclasses.fields(result)
                   if isinstance(getattr(result, f.name), (float, tuple, list)))
    return True


def guard_arithmetic(namespace: dict, module: str, only: tuple[str, ...] | None = None) -> None:
    """Wrap the public functions defined in `module` (or just `only`) so that valid-but-extreme inputs
    (5e-324 F, 1e200 A...) can't escape as ZeroDivisionError / OverflowError or come back as inf/NaN:
    they raise a DomainError with a readable reason instead. Call at the end of a module."""
    def wrap(fn):
        label = fn.__name__.replace("_", " ")

        @functools.wraps(fn)
        def guarded(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
            except (ZeroDivisionError, OverflowError) as exc:
                raise DomainError(f"{label}: these values are too extreme to compute (check the units)") from exc
            if not _all_finite(result):
                raise DomainError(f"{label}: the answer is out of range for these values (check the units)")
            return result
        return guarded

    for name, obj in list(namespace.items()):
        if type(obj).__name__ == "function" and getattr(obj, "__module__", None) == module \
                and not name.startswith("_") and (only is None or name in only):
            namespace[name] = wrap(obj)

