"""Verified Asset Repository (DECISIONOPS_ARCHITECTURE §7.5, bootstrap task 5).

A verified asset is a reviewed question with a known-good answer: it is what turns "the
query still runs" into "the number is still right". Two properties matter here — an asset
records *what it was verified against* (a semantic digest), and it is invalidated by a
semantic change rather than quietly re-used. An expected result nobody re-checked after a
formula change is worse than no expectation at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from auto_bi.semantic.diff import diff_snapshots
from auto_bi.severity import Severity
from auto_bi.verification.repository import (
    VerifiedAsset,
    VerifiedAssetRepository,
)
from tests.test_semantic_diff import snap
from tests.test_semantic_gateway import contract, model


def asset(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "api_version": "auto-bi.io/v2alpha1",
        "kind": "VerifiedAsset",
        "metadata": {
            "id": "asset://sales/weekly_margin_by_region",
            "version": 2,
            "owner": "commercial-analytics",
            "risk_tier": "medium",
        },
        "question": "Какая валовая маржа по регионам за последние 13 недель?",
        "paraphrases": ["маржа по регионам", "gross margin by region"],
        "intent": "breakdown",
        "metric_refs": ["metric://sales/gross_margin_pct@3"],
        "expectation": {
            "kind": "result",
            "result": {
                "columns": [
                    {"name": "region", "type": "string"},
                    {"name": "margin", "type": "decimal"},
                ],
                "rows": [["north", "0.412"], ["south", "0.318"]],
            },
        },
        "tolerance": {"business_key": ["region"]},
        "reference_plan": "sales_daily_by_region_13w",
        "semantic_digest": "sha256:" + "0" * 64,
        "review": {
            "reviewer": "commercial-analytics",
            "reviewed_at": "2026-08-21T09:00:00Z",
            "rationale": "cross-checked against the finance close",
        },
    }
    data.update(overrides)
    return data


def repository(tmp_path: Path, *assets: dict[str, Any]) -> VerifiedAssetRepository:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for index, data in enumerate(assets or (asset(),)):
        (tmp_path / f"asset_{index}.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
        )
    return VerifiedAssetRepository.load(tmp_path)


def test_asset_loads_and_round_trips() -> None:
    parsed = VerifiedAsset.model_validate(asset())
    assert parsed.self_ref() == "asset://sales/weekly_margin_by_region@2"
    assert parsed.expectation.result is not None
    assert parsed.expectation.result.rows[0] == ["north", "0.412"]
    again = VerifiedAsset.model_validate(parsed.model_dump(mode="json"))
    assert again == parsed
    assert again.digest() == parsed.digest()


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        VerifiedAsset.model_validate(asset(prompt="ask the model nicely"))


def test_expected_result_requires_a_business_key() -> None:
    """Without a key the comparison is `insufficient` by construction — refuse earlier."""
    with pytest.raises(ValidationError, match="business_key"):
        VerifiedAsset.model_validate(asset(tolerance={}))


@pytest.mark.parametrize(
    ("expectation", "match"),
    [
        ({"kind": "result"}, "result"),
        ({"kind": "invariants"}, "invariants"),
        ({"kind": "clarification"}, "clarification"),
        ({"kind": "refusal"}, "refusal"),
    ],
)
def test_expectation_must_carry_its_payload(expectation: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        VerifiedAsset.model_validate(asset(expectation=expectation))


def test_negative_case_records_the_expected_clarification() -> None:
    parsed = VerifiedAsset.model_validate(
        asset(
            intent="clarification",
            metric_refs=[],
            expectation={
                "kind": "clarification",
                "clarification": "За какой период считать маржу?",
            },
            tolerance={},
        )
    )
    assert parsed.expectation.clarification.startswith("За какой")


def test_positive_case_must_name_its_metrics() -> None:
    with pytest.raises(ValidationError, match="metric_refs"):
        VerifiedAsset.model_validate(asset(metric_refs=[]))


def test_repository_indexes_by_id_and_refuses_duplicates(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    assert [item.self_ref() for item in repo.assets] == ["asset://sales/weekly_margin_by_region@2"]
    assert repo.get("asset://sales/weekly_margin_by_region") is not None
    with pytest.raises(ValueError, match="duplicate"):
        repository(tmp_path / "dup", asset(), asset())


def test_meaning_change_invalidates_the_asset(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    repo = repository(tmp_path / "repo", asset(semantic_digest=base.canonical_digest))
    reformulated = contract()
    reformulated["formula"]["denominator"]["column"] = "gross_margin"
    diff = diff_snapshots(base, snap(tmp_path, "candidate", contracts={"m.yaml": reformulated}))
    invalidations = repo.invalidate(diff)
    assert [item.code for item in invalidations] == ["asset.metric_changed"]
    assert invalidations[0].severity is Severity.REVIEW
    assert invalidations[0].asset == "asset://sales/weekly_margin_by_region@2"
    assert "metric.formula_changed" in invalidations[0].detail


def test_label_change_does_not_invalidate(tmp_path: Path) -> None:
    """An advisory difference does not make a verified number wrong."""
    base = snap(tmp_path, "base")
    repo = repository(tmp_path / "repo", asset(semantic_digest=base.canonical_digest))
    relabelled = model()
    relabelled.tables[0].columns[4].description = "Revenue, net of VAT"
    diff = diff_snapshots(base, snap(tmp_path, "candidate", semantic=relabelled))
    assert repo.invalidate(diff) == []


def test_removed_metric_invalidates_with_block(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    repo = repository(tmp_path / "repo", asset(semantic_digest=base.canonical_digest))
    diff = diff_snapshots(base, snap(tmp_path, "candidate", contracts={"note.txt": "gone"}))
    invalidations = repo.invalidate(diff)
    assert [item.code for item in invalidations] == ["asset.metric_removed"]
    assert invalidations[0].severity is Severity.BLOCK


def test_unrelated_asset_survives(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    repo = repository(
        tmp_path / "repo",
        asset(
            metadata={
                "id": "asset://ops/sla",
                "version": 1,
                "owner": "operations",
                "risk_tier": "low",
            },
            metric_refs=["metric://ops/on_time_rate@1"],
            semantic_digest=base.canonical_digest,
        ),
    )
    reformulated = contract()
    reformulated["formula"]["denominator"]["column"] = "gross_margin"
    diff = diff_snapshots(base, snap(tmp_path, "candidate", contracts={"m.yaml": reformulated}))
    assert repo.invalidate(diff) == []


def test_asset_verified_against_another_snapshot_is_stale(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    repo = repository(tmp_path / "repo", asset(semantic_digest=base.canonical_digest))
    other = repository(tmp_path / "repo2")  # digest sha256:000…0
    diff = diff_snapshots(base, snap(tmp_path, "candidate"))
    assert repo.invalidate(diff) == []
    stale = other.invalidate(diff)
    assert [item.code for item in stale] == ["asset.stale_snapshot"]
    assert stale[0].severity is Severity.REVIEW
