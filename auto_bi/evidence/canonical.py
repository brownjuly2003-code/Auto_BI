"""JSON Canonicalization Scheme (RFC 8785) + SHA-256 content addresses.

Evidence is content-addressed (invariant 9): the same input must produce the same digest on
any machine, or two runs of the same verification cannot be compared. That needs a real
canonicalization — ``json.dumps(sort_keys=True)`` is not JCS: it sorts by code point rather
than UTF-16 code unit, escapes non-ASCII, and delegates number formatting to repr. This
implementation is checked against the official RFC 8785 vector corpus
(``tests/fixtures/jcs``) before being called JCS anywhere (DECISIONOPS_ARCHITECTURE §9.1).

Two layers, deliberately separate:

* ``allow_float=True`` — full JCS, including ES6 number serialization. Used for foreign
  JSON payloads that already contain IEEE-754 numbers, and by the conformance tests.
* the default — the contract boundary. Floats are rejected outright: contract decimals are
  decimal strings (§7.1), so a float in a contract payload means a modelling mistake, and
  silently serializing it would bake a serializer-dependent value into a digest.

Both layers reject what RFC 8785 leaves undefined or unrepresentable: non-finite numbers,
negative zero, integers outside the IEEE-754 safe range, and lone surrogates.
"""

from __future__ import annotations

import hashlib
import math

_MAX_SAFE_INT = 2**53 - 1

# ES6 JSON.stringify escapes exactly these; everything else below 0x20 becomes \u00xx, and
# 0x7f (DEL) and the C1 range stay raw — see the `weird` vector.
_SHORT_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


class CanonicalizationError(ValueError):
    """Value cannot be canonicalized — surfaced, never rounded or dropped."""


def canonical_bytes(value: object, *, allow_float: bool = False) -> bytes:
    """Canonical RFC 8785 UTF-8 bytes of *value*."""
    return "".join(_write(value, allow_float, "$")).encode("utf-8")


def canonical_json(value: object, *, allow_float: bool = False) -> str:
    """Canonical RFC 8785 text of *value* (the UTF-8 decoding of :func:`canonical_bytes`)."""
    return "".join(_write(value, allow_float, "$"))


def digest_bytes(raw: bytes) -> str:
    """Content address of already-canonical bytes."""
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def sha256_digest(value: object, *, allow_float: bool = False) -> str:
    """Content address of the canonical form of *value*."""
    return digest_bytes(canonical_bytes(value, allow_float=allow_float))


def _write(value: object, allow_float: bool, path: str) -> list[str]:
    if value is None:
        return ["null"]
    if isinstance(value, bool):  # before int: bool is an int subclass
        return ["true" if value else "false"]
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INT:
            raise CanonicalizationError(
                f"{path}: {value} is outside the IEEE-754 safe integer range; "
                "store large numbers as decimal strings"
            )
        return [str(value)]
    if isinstance(value, float):
        if not allow_float:
            raise CanonicalizationError(
                f"{path}: binary floats are not allowed in a canonical contract; "
                "use a decimal string"
            )
        return [_es6_number(value, path)]
    if isinstance(value, str):
        return [_quote(value, path)]
    if isinstance(value, list):
        out = ["["]
        for index, item in enumerate(value):
            if index:
                out.append(",")
            out.extend(_write(item, allow_float, f"{path}[{index}]"))
        out.append("]")
        return out
    if isinstance(value, dict):
        return _write_object(value, allow_float, path)
    raise CanonicalizationError(f"{path}: unsupported type {type(value).__name__}")


def _write_object(value: dict[object, object], allow_float: bool, path: str) -> list[str]:
    for key in value:
        if not isinstance(key, str):
            raise CanonicalizationError(
                f"{path}: canonical objects require string keys, got {type(key).__name__}"
            )
    out = ["{"]
    for index, key in enumerate(sorted(value, key=lambda k: _utf16_order(str(k), path))):
        if index:
            out.append(",")
        out.append(_quote(str(key), path))
        out.append(":")
        out.extend(_write(value[key], allow_float, f"{path}.{key}"))
    out.append("}")
    return out


def _utf16_order(key: str, path: str) -> bytes:
    """Sort key: UTF-16BE bytes compare exactly like UTF-16 code units (RFC 8785 §3.2.3)."""
    try:
        return key.encode("utf-16-be")
    except UnicodeEncodeError:
        raise CanonicalizationError(f"{path}: object key contains a lone surrogate") from None


def _quote(value: str, path: str) -> str:
    out = ['"']
    for char in value:
        escape = _SHORT_ESCAPES.get(char)
        if escape is not None:
            out.append(escape)
        elif char < "\x20":
            out.append(f"\\u{ord(char):04x}")
        elif "\ud800" <= char <= "\udfff":
            raise CanonicalizationError(f"{path}: string contains a lone surrogate")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _es6_number(value: float, path: str) -> str:
    """ECMAScript ``Number::toString`` (RFC 8785 §3.2.2.3)."""
    if math.isnan(value) or math.isinf(value):
        raise CanonicalizationError(f"{path}: non-finite numbers have no canonical form")
    if value == 0.0:
        if math.copysign(1.0, value) < 0:
            raise CanonicalizationError(f"{path}: negative zero is not a canonical value")
        return "0"
    if value < 0:
        return "-" + _es6_number(-value, path)

    digits, point = _shortest_digits(value)
    count = len(digits)
    if count <= point <= 21:
        return digits + "0" * (point - count)
    if 0 < point <= 21:
        return f"{digits[:point]}.{digits[point:]}"
    if -6 < point <= 0:
        return "0." + "0" * -point + digits
    exponent = point - 1
    mantissa = digits if count == 1 else f"{digits[0]}.{digits[1:]}"
    return f"{mantissa}e{'+' if exponent >= 0 else '-'}{abs(exponent)}"


def _shortest_digits(value: float) -> tuple[str, int]:
    """Shortest round-trip digits of a positive finite float and its decimal point position.

    ``value == 0.<digits> * 10**point``. CPython's ``repr`` already emits the shortest
    round-tripping decimal (the same digits V8 produces); only the layout differs, and that
    is what :func:`_es6_number` fixes up.
    """
    mantissa, _, exponent_text = repr(value).partition("e")
    exponent = int(exponent_text) if exponent_text else 0
    int_part, _, fraction = mantissa.partition(".")
    raw = int_part + fraction
    stripped = raw.lstrip("0")
    point = len(int_part) - (len(raw) - len(stripped)) + exponent
    return stripped.rstrip("0"), point
