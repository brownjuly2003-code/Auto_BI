"""Stable ids and version-pinned refs (DECISIONOPS_ARCHITECTURE 7.1).

An id names a contract (`metric://sales/gross_margin_pct`); a ref pins the exact domain
version the run was verified against (`metric://sales/gross_margin_pct@3`). Mixing the two
is what silently re-points a verified claim at a changed metric, so the shapes are typed
apart and the kind is checked at the field.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from auto_bi.refs import ContractRef, RefError, RefKind, parse_ref, stable_id, versioned_ref
from auto_bi.strict import StrictModel


class Holder(StrictModel):
    metric: versioned_ref(RefKind.METRIC)
    product: stable_id(RefKind.DECISION)


def test_parse_versioned_metric_ref() -> None:
    ref = parse_ref("metric://sales/gross_margin_pct@3")
    assert ref.kind is RefKind.METRIC
    assert ref.path == ("sales", "gross_margin_pct")
    assert ref.version == 3
    assert str(ref) == "metric://sales/gross_margin_pct@3"


def test_parse_single_segment_id_without_version() -> None:
    ref = parse_ref("dashboard://weekly_margin")
    assert ref.kind is RefKind.DASHBOARD
    assert ref.path == ("weekly_margin",)
    assert ref.version is None


def test_with_version_derives_a_pinned_ref_from_an_id() -> None:
    assert str(parse_ref("decision://sales/weekly_margin").with_version(7)) == (
        "decision://sales/weekly_margin@7"
    )
    with pytest.raises(RefError, match="start at 1"):
        parse_ref("decision://sales/weekly_margin").with_version(0)


def test_non_string_input_is_rejected() -> None:
    with pytest.raises(RefError, match="must be a string"):
        parse_ref(3)


@pytest.mark.parametrize(
    "raw",
    [
        "metric://Sales/gross_margin_pct",  # uppercase is not a stable id
        "metric://sales//gross_margin_pct",  # empty segment
        "metric://sales/",  # trailing slash
        "metric://",  # no path
        "metric:/sales/x",  # malformed scheme
        "unknown://sales/x",  # unknown kind
        "metric://sales/gross margin",  # space
        "metric://sales/x@0",  # versions are 1-based
        "metric://sales/x@-1",
        "metric://sales/x@01",  # leading zero is not a canonical version
        "metric://sales/x@3@4",
        "metric://sales/x@v3",
        "metric://_sales/x",  # segment starts with a separator
        "",
    ],
)
def test_malformed_refs_are_rejected(raw: str) -> None:
    with pytest.raises(RefError):
        parse_ref(raw)


def test_field_requires_a_version_pin() -> None:
    with pytest.raises(ValidationError, match="version"):
        Holder(metric="metric://sales/gross_margin_pct", product="decision://sales/weekly_margin")


def test_field_rejects_a_version_on_a_stable_id() -> None:
    with pytest.raises(ValidationError, match="version"):
        Holder(
            metric="metric://sales/gross_margin_pct@3",
            product="decision://sales/weekly_margin@1",
        )


def test_field_rejects_the_wrong_kind() -> None:
    with pytest.raises(ValidationError, match="metric"):
        Holder(metric="dashboard://weekly_margin@3", product="decision://sales/weekly_margin")


def test_valid_fields_round_trip_as_plain_strings() -> None:
    holder = Holder(
        metric="metric://sales/gross_margin_pct@3", product="decision://sales/weekly_margin"
    )
    assert holder.model_dump() == {
        "metric": "metric://sales/gross_margin_pct@3",
        "product": "decision://sales/weekly_margin",
    }
    assert ContractRef.parse(holder.metric).version == 3
