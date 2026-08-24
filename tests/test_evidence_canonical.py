"""JCS (RFC 8785) canonicalization and the digest boundary (DECISIONOPS_ARCHITECTURE §9.1).

Two layers are tested apart:

* JCS conformance — the official vector corpus, run with ``allow_float=True`` because it
  contains IEEE-754 numbers and ES6 number serialization is part of the standard;
* the contract boundary — floats, non-finite values, negative zero and unsafe integers are
  rejected by default, because contract decimals are decimal strings (§7.1) and a binary
  float would make a contract digest depend on the serializer rather than the content.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_bi.evidence.canonical import (
    CanonicalizationError,
    canonical_bytes,
    canonical_json,
    digest_bytes,
    sha256_digest,
)
from auto_bi.scalars import MAX_SAFE_INT

VECTORS = Path(__file__).parent / "fixtures" / "jcs"
VECTOR_NAMES = ["arrays", "french", "structures", "unicode", "values", "weird"]


@pytest.mark.parametrize("name", VECTOR_NAMES)
def test_official_rfc8785_vectors(name: str) -> None:
    """Official corpus: sorting, escaping, ES6 numbers and UTF-8 output, byte for byte."""
    parsed = json.loads((VECTORS / f"{name}.input.json").read_bytes().decode("utf-8"))
    expected = (VECTORS / f"{name}.expected.json").read_bytes().rstrip(b"\n")
    assert canonical_bytes(parsed, allow_float=True) == expected


def test_es6_number_serialization_of_negatives() -> None:
    """The official corpus is all-positive; the sign branch needs its own case."""
    assert canonical_json({"a": -1.5, "b": -1e30, "c": -0.002}, allow_float=True) == (
        '{"a":-1.5,"b":-1e+30,"c":-0.002}'
    )


def test_property_order_is_utf16_code_units_not_code_points() -> None:
    """RFC 8785 3.2.3 sorts UTF-16 code units: an astral key sorts before U+FFFD."""
    out = canonical_bytes({"�": 1, "\U0001f600": 2}).decode("utf-8")
    assert out == '{"\U0001f600":2,"�":1}'


def test_output_has_no_insignificant_whitespace_and_keeps_unicode_raw() -> None:
    out = canonical_bytes({"b": ["x", None, True], "a": {"n": 1}, "ю": "щ"})
    assert out == '{"a":{"n":1},"b":["x",null,true],"ю":"щ"}'.encode()


def test_control_characters_use_short_escapes_and_lowercase_hex() -> None:
    out = canonical_json({"k": '\b\t\n\f\r\x00\x1f"\\'})
    assert out == '{"k":"\\b\\t\\n\\f\\r\\u0000\\u001f\\"\\\\"}'


def test_delete_and_c1_controls_are_not_escaped() -> None:
    """ES6 JSON.stringify escapes only < 0x20, quote and backslash (see the weird vector)."""
    assert canonical_json({"k": "\x7f\x80"}) == '{"k":"\x7f\x80"}'


def test_float_is_rejected_at_the_contract_boundary() -> None:
    with pytest.raises(CanonicalizationError, match="decimal string"):
        canonical_bytes({"tolerance": 0.000001})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_is_rejected_even_when_floats_are_allowed(value: float) -> None:
    with pytest.raises(CanonicalizationError, match="non-finite"):
        canonical_bytes({"x": value}, allow_float=True)


def test_both_zeros_canonicalize_to_the_same_token() -> None:
    """RFC 8785 Appendix B pins 0x8000000000000000 to `0`; rejecting -0 is a contract rule
    (`scalars.DecimalString`), not something a JCS implementation may do."""
    assert canonical_json({"x": -0.0}, allow_float=True) == '{"x":0}'
    assert canonical_json({"x": 0.0}, allow_float=True) == '{"x":0}'


def test_integer_outside_the_safe_range_is_rejected_at_the_contract_boundary() -> None:
    assert canonical_bytes({"n": MAX_SAFE_INT}) == b'{"n":9007199254740991}'
    with pytest.raises(CanonicalizationError, match="safe integer"):
        canonical_bytes({"n": MAX_SAFE_INT + 1})


def test_jcs_rounds_a_large_integer_through_the_double_it_becomes() -> None:
    """A JSON number is a double: 2**53+1 has no JSON form of its own (RFC 8785 §3.2.2.3)."""
    assert canonical_json({"n": 9007199254740993}, allow_float=True) == '{"n":9007199254740992}'
    assert canonical_json({"n": 10**300}, allow_float=True) == '{"n":1e+300}'
    with pytest.raises(CanonicalizationError, match="range of an IEEE-754 double"):
        canonical_bytes({"n": 10**400}, allow_float=True)


def test_contract_and_scalar_layers_agree_on_the_safe_range() -> None:
    assert MAX_SAFE_INT == 2**53 - 1
    assert canonical_bytes({"n": MAX_SAFE_INT}) == f'{{"n":{MAX_SAFE_INT}}}'.encode()


def test_non_string_key_is_rejected() -> None:
    with pytest.raises(CanonicalizationError, match="string keys"):
        canonical_bytes({1: "one"})


@pytest.mark.parametrize("value", [{"x"}, b"bytes", (1, 2), object()])
def test_unsupported_types_are_rejected(value: object) -> None:
    with pytest.raises(CanonicalizationError, match="unsupported"):
        canonical_bytes({"x": value})


def test_lone_surrogate_is_rejected() -> None:
    with pytest.raises(CanonicalizationError, match="surrogate"):
        canonical_bytes({"k": "lone\ud800"})


def test_digest_is_stable_across_key_insertion_order() -> None:
    first = sha256_digest({"a": 1, "b": {"y": "2", "x": "1"}})
    second = sha256_digest({"b": {"x": "1", "y": "2"}, "a": 1})
    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64
    assert first == first.lower()


def test_digest_changes_when_content_changes() -> None:
    assert sha256_digest({"a": "1"}) != sha256_digest({"a": "2"})


def test_digest_bytes_matches_canonical_digest_of_the_same_payload() -> None:
    payload = {"a": [1, "2", None]}
    assert digest_bytes(canonical_bytes(payload)) == sha256_digest(payload)


def test_canonical_json_is_the_utf8_text_of_canonical_bytes() -> None:
    payload = {"€": "x"}
    assert canonical_json(payload).encode("utf-8") == canonical_bytes(payload)
