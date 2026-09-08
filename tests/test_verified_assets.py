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
    AssetInvalidation,
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


# --- the reviewed query the provenance chain anchors on (§8.4.1) ---------------


def query(sql: str, engine: str = "clickhouse") -> dict[str, Any]:
    return {"engine": engine, "sql": sql}


def test_a_query_over_governed_marts_is_not_a_finding(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    repo = repository(
        tmp_path / "repo",
        asset(reference_query=query('SELECT "region" FROM "dm"."sales_daily"')),
    )
    assert repo.ungoverned(base) == []


def test_a_query_over_a_mart_the_model_does_not_govern_is_named(tmp_path: Path) -> None:
    """An attested, reconciled number from an ungoverned copy is the failure to catch."""
    base = snap(tmp_path, "base")
    repo = repository(
        tmp_path / "repo",
        asset(reference_query=query('SELECT "region" FROM "dm"."sales_daily_v2"')),
    )
    findings = repo.ungoverned(base)
    assert [item.code for item in findings] == ["asset.query_ungoverned"]
    assert findings[0].severity is Severity.REVIEW
    assert "dm.sales_daily_v2" in findings[0].detail


def test_an_asset_with_no_reviewed_query_has_nothing_to_check(tmp_path: Path) -> None:
    assert repository(tmp_path / "repo", asset()).ungoverned(snap(tmp_path, "base")) == []


def test_a_cte_name_is_not_a_mart(tmp_path: Path) -> None:
    """A WITH alias is a name inside the query, not a table the model must declare."""
    base = snap(tmp_path, "base")
    sql = (
        'WITH "recent" AS (SELECT "region" FROM "dm"."sales_daily") '
        'SELECT "region" FROM "recent"'
    )
    repo = repository(tmp_path / "repo", asset(reference_query=query(sql)))
    assert repo.ungoverned(base) == []


def test_every_ungoverned_mart_is_named_not_only_the_first(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    sql = (
        'SELECT "a"."region" FROM "staging"."sales" AS "a" '
        'JOIN "tmp"."regions" AS "b" ON "a"."region" = "b"."region"'
    )
    repo = repository(tmp_path / "repo", asset(reference_query=query(sql)))
    detail = repo.ungoverned(base)[0].detail
    assert "staging.sales" in detail and "tmp.regions" in detail


def test_clickhouse_reads_case_as_clickhouse_does(tmp_path: Path) -> None:
    """`Sales_Daily` is a different table there, and the model declares `sales_daily`."""
    base = snap(tmp_path, "base")
    repo = repository(
        tmp_path / "repo",
        asset(reference_query=query('SELECT "region" FROM "dm"."Sales_Daily"')),
    )
    assert [item.code for item in repo.ungoverned(base)] == ["asset.query_ungoverned"]


def test_the_postgres_family_folds_an_unquoted_identifier(tmp_path: Path) -> None:
    """Grading ClickHouse's rule there would invent a mismatch the engine does not have."""
    base = snap(tmp_path, "base")
    repo = repository(
        tmp_path / "repo",
        asset(reference_query=query("SELECT region FROM DM.Sales_Daily", engine="greenplum")),
    )
    assert repo.ungoverned(base) == []


def test_a_quoted_postgres_identifier_is_compared_exactly(tmp_path: Path) -> None:
    """`"Sales_Daily"` is a table `sales_daily` is not — folding it would hide a real mart."""
    base = snap(tmp_path, "base")
    repo = repository(
        tmp_path / "repo",
        asset(
            reference_query=query(
                'SELECT region FROM "DM"."Sales_Daily"',
                engine="greenplum",
            )
        ),
    )
    assert [item.code for item in repo.ungoverned(base)] == ["asset.query_ungoverned"]


def test_an_unqualified_name_is_not_read_as_the_governed_mart(tmp_path: Path) -> None:
    """Which schema a bare name resolves to is a session setting the gate does not hold."""
    base = snap(tmp_path, "base")
    repo = repository(
        tmp_path / "repo", asset(reference_query=query("SELECT region FROM sales_daily"))
    )
    findings = repo.ungoverned(base)
    assert [item.code for item in findings] == ["asset.query_ungoverned"]
    assert findings[0].asset == "asset://sales/weekly_margin_by_region@2"


# --- the fields inside those marts --------------------------------------------


def codes(findings: list[AssetInvalidation]) -> list[str]:
    return [item.code for item in findings]


def test_a_query_over_declared_fields_is_not_a_finding(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    sql = 'SELECT "region", sum("revenue") AS "m" FROM "dm"."sales_daily" GROUP BY "region"'
    repo = repository(tmp_path / "repo", asset(reference_query=query(sql)))
    assert repo.ungoverned(base) == []


def test_a_column_the_model_does_not_declare_is_named(tmp_path: Path) -> None:
    """Table-level governance stops at the mart; the number is made of columns."""
    base = snap(tmp_path, "base")
    sql = (
        'SELECT "region", sum("internal_cost_raw") AS "m" FROM "dm"."sales_daily" GROUP BY "region"'
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    assert findings[0].severity is Severity.REVIEW
    assert "dm.sales_daily.internal_cost_raw" in findings[0].detail


def test_a_column_of_an_ungoverned_mart_is_reported_once(tmp_path: Path) -> None:
    """The mart is the fix; naming its columns too would weigh one fact twice."""
    base = snap(tmp_path, "base")
    sql = 'SELECT "region", "ghost" FROM "dm"."sales_daily_v2"'
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned"]


def test_a_column_written_inside_a_subquery_is_still_read_from_the_mart(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    sql = 'SELECT "region" FROM (SELECT "region", "ghost" FROM "dm"."sales_daily") AS "t"'
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    assert "dm.sales_daily.ghost" in findings[0].detail


def test_a_projection_alias_is_not_a_column_of_the_mart(tmp_path: Path) -> None:
    """`ORDER BY m` names the output, not a field the model would have to declare."""
    base = snap(tmp_path, "base")
    sql = (
        'SELECT "region", sum("revenue") AS "m" FROM "dm"."sales_daily" '
        'GROUP BY "region" ORDER BY "m"'
    )
    assert repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base) == []


def test_a_correlated_column_belongs_to_the_scope_that_declares_it(tmp_path: Path) -> None:
    """Resolved outward, as SQL resolves it — not against the subquery's own source."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT region FROM dm.sales_daily AS a "
        "WHERE a.revenue > (SELECT avg(id) FROM dm.stores AS b WHERE b.id = a.store_id)"
    )
    assert repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base) == []


def test_a_bare_column_in_a_join_is_not_guessed_at_but_said(tmp_path: Path) -> None:
    """Either table could own it, so the gate reports a partial check instead of a guess."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, category FROM dm.sales_daily AS a "
        "JOIN dm.stores AS b ON a.store_id = b.id"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert findings[0].severity is Severity.ADVISORY
    assert "category" in findings[0].detail


def test_a_star_says_the_field_check_could_not_run(tmp_path: Path) -> None:
    """`SELECT *` reads whatever the physical table holds, which the SQL does not say."""
    base = snap(tmp_path, "base")
    findings = repository(
        tmp_path / "repo", asset(reference_query=query('SELECT * FROM "dm"."sales_daily"'))
    ).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert "selects *" in findings[0].detail


def test_an_unchecked_field_note_never_hides_a_named_one(tmp_path: Path) -> None:
    """A partial check still reports what it did resolve."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.ghost, category FROM dm.sales_daily AS a "
        "JOIN dm.stores AS b ON a.store_id = b.id"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field", "asset.query_fields_unchecked"]


def test_the_postgres_family_folds_an_unquoted_column(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    sql = "SELECT REGION FROM DM.SALES_DAILY"
    repo = repository(tmp_path / "repo", asset(reference_query=query(sql, engine="greenplum")))
    assert repo.ungoverned(base) == []


def test_a_quoted_column_is_compared_exactly_on_postgres(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    sql = 'SELECT "REGION" FROM dm.sales_daily'
    repo = repository(tmp_path / "repo", asset(reference_query=query(sql, engine="greenplum")))
    findings = repo.ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    assert "dm.sales_daily.REGION" in findings[0].detail


def test_clickhouse_reads_column_case_as_clickhouse_does(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    repo = repository(
        tmp_path / "repo", asset(reference_query=query("SELECT Region FROM dm.sales_daily"))
    )
    assert codes(repo.ungoverned(base)) == ["asset.query_ungoverned_field"]
