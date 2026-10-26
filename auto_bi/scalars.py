"""Canonical scalar types for DecisionOps contracts (DECISIONOPS_ARCHITECTURE §7.1).

Money, ratios and tolerances travel as decimal strings; time as RFC 3339 UTC; durations as
ISO 8601; content addresses as ``sha256:<hex>``. The reason is digest stability: a binary
float, a local-time stamp or a `0.1` / `0.1000` ambiguity would make the same contract hash
differently on another machine, and a hash that depends on the machine cannot gate a
release. Decimal strings are therefore *normalized* on the way in — trailing fractional
zeros are dropped — so two spellings of one number are one contract.

The patterns anchor with ``\\Z``, not ``$``: in Python ``$`` also matches before a trailing
newline, which would let ``"metric://sales/x\\n"`` and ``"sha256:…\\n"`` validate as ids and
digests that look identical in a report but hash differently.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BeforeValidator, StringConstraints

# Plain decimal literal: optional sign, no exponent, no padding, no thousands separators.
_DECIMAL_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
# ISO 8601 duration; at least one component, no fractional parts (§7.1 keeps them exact).
_DURATION_RE = re.compile(
    r"^P(?!\Z)(?:\d+Y)?(?:\d+M)?(?:\d+W)?(?:\d+D)?(?:T(?!\Z)(?:\d+H)?(?:\d+M)?(?:\d+S)?)?\Z"
)
# RFC 3339 `date-time` with the offset `Z`, nothing else: the date in full, `T`, the time to
# the second, a fraction of any length, `Z`. `[0-9]`, because `\d` matches every script's
# digits and `int` reads them.
_TIMESTAMP_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]+)?Z\Z"
)
_NOT_A_TIMESTAMP = "must be an RFC 3339 UTC timestamp such as 2026-08-25T09:00:00Z"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*\Z")

# Names that move: pinning to one of these pins nothing (invariant 11).
_MOVING_POINTERS = frozenset(
    {"head", "main", "master", "latest", "dev", "develop", "trunk", "current", "stable", "tip"}
)

#: Free text that must carry content; surrounding whitespace is not content.
NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _decimal_string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a decimal string, not a binary number")
    if not _DECIMAL_RE.match(value):
        raise ValueError(
            "must be a plain decimal string: optional '-', digits, optional '.', no exponent"
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation:  # pragma: no cover - regex already rejects these
        raise ValueError("must be a decimal string") from None
    if value.startswith("-") and parsed == 0:
        raise ValueError("negative zero is not a canonical decimal value")
    return _normalize_decimal(value)


def _normalize_decimal(value: str) -> str:
    """`0.0000010` and `0.000001` are one number, so they must be one string."""
    if "." not in value:
        return value
    head, _, fraction = value.partition(".")
    fraction = fraction.rstrip("0")
    return f"{head}.{fraction}" if fraction else head


def _duration(value: object) -> str:
    if not isinstance(value, str) or not _DURATION_RE.match(value):
        raise ValueError("must be an ISO 8601 duration such as PT6H or P13W")
    return value


def _utc_timestamp(value: object) -> str:
    """Text of the one spelling of a UTC moment, kept as it is written (§7.1).

    The form is checked by the pattern and the calendar by `datetime` itself: the day must
    exist, the hour be 00-23, the minute and the second 00-59, the year 0001-9999. Nothing is
    left to `datetime.fromisoformat`, which reads forms of ISO 8601 that RFC 3339 has not, stops
    reading a fraction at its sixth digit when an offset follows, and reads `24:00` from Python
    3.14 on: whether a bundle holds a moment would depend on the interpreter that reads it.
    """
    if isinstance(value, date):  # what YAML builds of a moment that is not quoted
        raise ValueError(
            "must be an RFC 3339 UTC timestamp as text, such as '2026-08-25T09:00:00Z' in "
            "quotes: YAML reads an unquoted one as a date"
        )
    if not isinstance(value, str) or (match := _TIMESTAMP_RE.match(value)) is None:
        raise ValueError(_NOT_A_TIMESTAMP)
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    try:
        datetime(year, month, day, hour, minute, second)
    except ValueError:
        raise ValueError(_NOT_A_TIMESTAMP) from None
    return value


def _sha256_digest(value: object) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.match(value):
        raise ValueError("must be a content address of the form sha256:<64 lowercase hex>")
    return value


def _slug(value: object) -> str:
    if not isinstance(value, str) or not _SLUG_RE.match(value):
        raise ValueError("must be a lowercase slug: [a-z0-9] then [a-z0-9._-]")
    return value


def _immutable_pin(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a pinned revision: a tag, commit or version")
    pin = value.strip()
    if pin.lower() in _MOVING_POINTERS:
        raise ValueError(
            f"{pin!r} is a moving pointer, not a pinned revision: use a tag, commit or version"
        )
    return pin


#: ISO 8601 components with a length that does not depend on where in the calendar it is
#: measured. Years and months are deliberately absent: they are 365 or 366 days and 28 to 31
#: days, so a duration built from them has no seconds to compare an age against.
_FIXED_UNITS: dict[str, int] = {
    "W": 604800,
    "D": 86400,
    "H": 3600,
    "M": 60,  # only after the T separator; see `duration_seconds`
    "S": 1,
}


#: The most digits one int() call converts here; Python refuses more than
#: sys.get_int_max_str_digits() (4300 by default, never below 640), so a longer run is split.
_DIGIT_BLOCK = 600


def _whole_number(digits: str) -> int:
    """The exact value of a run of decimal digits however long it is.

    A declared window may legally carry more digits than one int() call converts:
    ``PT`` + 4300 zeros + ``6H`` is six hours. Halving keeps the cost far below
    digit-by-digit accumulation (measured: 100 000 digits in about 0.1 s, a million
    in about 4 s), and no interpreter-wide setting is touched.
    """
    if len(digits) <= _DIGIT_BLOCK:
        return int(digits)
    half = len(digits) // 2
    value: int = _whole_number(digits[:half]) * 10 ** (len(digits) - half) + _whole_number(
        digits[half:]
    )
    return value


def duration_seconds(duration: str) -> int | None:
    """An ISO 8601 duration as seconds, or ``None`` when it has no fixed length.

    Both the freshness of a mart and the age of a recorded reading are compared against a
    window an owner declared, and they must read that declaration the same way — two
    parsers would let one check call `P1M` a month and the other call it a minute. The `M`
    ambiguity is why the parse is positional rather than a regex over the whole string:
    before the `T` separator it is months, after it minutes, and only the second is fixed.
    The count is an exact integer, so no declared window is too long to compare and two
    fixed windows of different length never compare equal.
    """
    body = duration[1:]
    date_part, _, time_part = body.partition("T")
    if "Y" in date_part or "M" in date_part:
        return None
    total = 0
    for part, allowed in ((date_part, "WD"), (time_part, "HMS")):
        number = ""
        for char in part:
            if char.isdigit():
                number += char
                continue
            if char in allowed and number:
                total += _whole_number(number) * _FIXED_UNITS[char]
            number = ""
    return total


DecimalString = Annotated[str, BeforeValidator(_decimal_string)]
Duration = Annotated[str, BeforeValidator(_duration)]
Timestamp = Annotated[str, BeforeValidator(_utc_timestamp)]
Sha256Digest = Annotated[str, BeforeValidator(_sha256_digest)]
Slug = Annotated[str, BeforeValidator(_slug)]
ImmutablePin = Annotated[str, BeforeValidator(_immutable_pin)]

#: Largest integer that survives a JSON round-trip through an IEEE-754 double, and therefore
#: the largest one a canonical contract may carry (see `evidence/canonical.py`).
MAX_SAFE_INT = 2**53 - 1
