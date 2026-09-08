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
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BeforeValidator, StringConstraints

# Plain decimal literal: optional sign, no exponent, no padding, no thousands separators.
_DECIMAL_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
# ISO 8601 duration; at least one component, no fractional parts (§7.1 keeps them exact).
_DURATION_RE = re.compile(
    r"^P(?!\Z)(?:\d+Y)?(?:\d+M)?(?:\d+W)?(?:\d+D)?(?:T(?!\Z)(?:\d+H)?(?:\d+M)?(?:\d+S)?)?\Z"
)
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
    if not isinstance(value, str) or not value.endswith("Z") or "T" not in value:
        raise ValueError("must be an RFC 3339 UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ValueError("must be an RFC 3339 UTC timestamp ending in Z") from None
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
_FIXED_UNITS: dict[str, timedelta] = {
    "W": timedelta(weeks=1),
    "D": timedelta(days=1),
    "H": timedelta(hours=1),
    "M": timedelta(minutes=1),  # only after the T separator; see `duration_seconds`
    "S": timedelta(seconds=1),
}


def duration_seconds(duration: str) -> float | None:
    """An ISO 8601 duration as seconds, or ``None`` when it has no fixed length.

    Both the freshness of a mart and the age of a recorded reading are compared against a
    window an owner declared, and they must read that declaration the same way — two
    parsers would let one check call `P1M` a month and the other call it a minute. The `M`
    ambiguity is why the parse is positional rather than a regex over the whole string:
    before the `T` separator it is months, after it minutes, and only the second is fixed.
    """
    body = duration[1:]
    date_part, _, time_part = body.partition("T")
    if "Y" in date_part or "M" in date_part:
        return None
    total = timedelta()
    for part, allowed in ((date_part, "WD"), (time_part, "HMS")):
        number = ""
        for char in part:
            if char.isdigit():
                number += char
                continue
            if char in allowed and number:
                total += int(number) * _FIXED_UNITS[char]
            number = ""
    return total.total_seconds()


DecimalString = Annotated[str, BeforeValidator(_decimal_string)]
Duration = Annotated[str, BeforeValidator(_duration)]
Timestamp = Annotated[str, BeforeValidator(_utc_timestamp)]
Sha256Digest = Annotated[str, BeforeValidator(_sha256_digest)]
Slug = Annotated[str, BeforeValidator(_slug)]
ImmutablePin = Annotated[str, BeforeValidator(_immutable_pin)]

#: Largest integer that survives a JSON round-trip through an IEEE-754 double, and therefore
#: the largest one a canonical contract may carry (see `evidence/canonical.py`).
MAX_SAFE_INT = 2**53 - 1
