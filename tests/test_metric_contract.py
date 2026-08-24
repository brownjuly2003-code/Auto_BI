"""MetricContract — governed metric definition (DECISIONOPS_ARCHITECTURE 7.3).

The contract is what a release gate diffs, so every field that can silently change a
number — formula, grain, additivity, time zone, null/zero semantics, unit — is explicit
and part of the contract digest. What the gate must not do is guess: an unpinned external
formula, a semi-additive metric without its non-additive axis or a bare SQL string without
review are rejected at parse time, not repaired.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from auto_bi.semantic.metric_contract import FormulaKind, MetricContract, MetricLifecycle

FIXTURE = Path(__file__).parent / "fixtures" / "decision" / "gross_margin_pct.yaml"


def payload(**overrides: Any) -> dict[str, Any]:
    data = deepcopy(yaml.safe_load(FIXTURE.read_text(encoding="utf-8")))
    data.update(overrides)
    return data


def test_fixture_contract_loads() -> None:
    contract = MetricContract.load(FIXTURE)
    assert contract.metadata.id == "metric://sales/gross_margin_pct"
    assert contract.metadata.version == 3
    assert contract.metadata.status is MetricLifecycle.ACTIVE
    assert contract.formula.kind is FormulaKind.TYPED_RATIO
    assert contract.formula.numerator is not None
    assert contract.formula.numerator.column == "gross_margin"
    assert contract.grain.keys == ["date", "store_id", "product_id"]


def test_self_ref_pins_the_domain_version() -> None:
    assert MetricContract.load(FIXTURE).self_ref() == "metric://sales/gross_margin_pct@3"


def test_round_trip_preserves_the_digest() -> None:
    contract = MetricContract.load(FIXTURE)
    again = MetricContract.model_validate(contract.model_dump(mode="json"))
    assert again == contract
    assert again.digest() == contract.digest()
    assert contract.digest().startswith("sha256:")


def test_digest_covers_the_whole_contract_including_description() -> None:
    """The diff classifier decides that a description change is advisory; the digest still
    changes, so an evidence bundle can never point at "the same" contract with new text."""
    base = MetricContract.load(FIXTURE)
    edited = MetricContract.model_validate(
        payload(metadata={**payload()["metadata"], "description": "other text"})
    )
    assert edited.digest() != base.digest()


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        MetricContract.model_validate(payload(owner_team="commercial"))


def test_ratio_formula_requires_both_terms() -> None:
    formula = payload()["formula"]
    del formula["denominator"]
    with pytest.raises(ValidationError, match="denominator"):
        MetricContract.model_validate(payload(formula=formula))


def test_ratio_formula_rejects_a_raw_sql_payload() -> None:
    formula = {**payload()["formula"], "sql": "select 1"}
    with pytest.raises(ValidationError, match="typed_ratio"):
        MetricContract.model_validate(payload(formula=formula))


def test_reviewed_sql_requires_a_recorded_review() -> None:
    formula = {"kind": "reviewed_sql", "sql": "sum(gross_margin) / sum(revenue)"}
    with pytest.raises(ValidationError, match="review"):
        MetricContract.model_validate(payload(formula=formula))

    reviewed = MetricContract.model_validate(
        payload(
            formula={
                **formula,
                "review": {
                    "reviewer": "commercial-analytics",
                    "reviewed_at": "2026-08-21T09:00:00Z",
                    "rationale": "no governed source for the legacy definition yet",
                },
            }
        )
    )
    assert reviewed.formula.kind is FormulaKind.REVIEWED_SQL


def test_external_formula_must_be_version_pinned() -> None:
    """Invariant 11: an external semantic standard is only usable pinned to a revision."""
    with pytest.raises(ValidationError, match="source_revision"):
        MetricContract.model_validate(
            payload(
                formula={
                    "kind": "external_ref",
                    "external": {"source": "dbt", "ref": "metric.shop.gross_margin_pct"},
                }
            )
        )


def test_semi_additive_metric_must_name_its_non_additive_axis() -> None:
    with pytest.raises(ValidationError, match="non_additive_axes"):
        MetricContract.model_validate(payload(additivity="semi_additive"))

    contract = MetricContract.model_validate(
        payload(additivity="semi_additive", non_additive_axes=["date"])
    )
    assert contract.non_additive_axes == ["date"]


def test_non_additive_axes_are_rejected_without_semi_additivity() -> None:
    with pytest.raises(ValidationError, match="semi_additive"):
        MetricContract.model_validate(payload(non_additive_axes=["date"]))


@pytest.mark.parametrize("bad", ["6h", "PT", "", "P13", "6 hours"])
def test_freshness_must_be_an_iso8601_duration(bad: str) -> None:
    with pytest.raises(ValidationError, match="duration"):
        MetricContract.model_validate(payload(freshness={**payload()["freshness"], "max_age": bad}))


def test_grain_keys_must_be_unique_and_non_empty() -> None:
    with pytest.raises(ValidationError, match="unique"):
        MetricContract.model_validate(
            payload(grain={"table": "dm.sales_daily", "keys": ["a", "a"]})
        )
    with pytest.raises(ValidationError, match="at least 1"):
        MetricContract.model_validate(payload(grain={"table": "dm.sales_daily", "keys": []}))


def test_metric_version_starts_at_one() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        MetricContract.model_validate(payload(metadata={**payload()["metadata"], "version": 0}))


def test_metadata_id_must_be_an_unversioned_metric_id() -> None:
    with pytest.raises(ValidationError, match="version"):
        MetricContract.model_validate(
            payload(metadata={**payload()["metadata"], "id": "metric://sales/gross_margin_pct@3"})
        )


def test_policy_refs_must_be_version_pinned() -> None:
    with pytest.raises(ValidationError, match="version"):
        MetricContract.model_validate(payload(policy_refs=["policy://release/default"]))


def test_precision_is_bounded() -> None:
    with pytest.raises(ValidationError, match="less than or equal to"):
        MetricContract.model_validate(payload(unit={**payload()["unit"], "precision": 40}))
