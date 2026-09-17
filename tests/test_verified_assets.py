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
from auto_bi.semantic.model import Column, ColumnRole, Physical, Table
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


def test_a_bare_column_one_governed_source_declares_is_read_not_guessed(tmp_path: Path) -> None:
    """The model leaves one candidate, so attributing it reads the query rather than guesses.

    `category` is declared on `dm.sales_daily` and on nothing else in scope. The engine
    accepted this SQL, so the name resolved to exactly one source there too.
    """
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, category FROM dm.sales_daily AS a "
        "JOIN dm.stores AS b ON a.store_id = b.id"
    )
    assert repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base) == []


def _stores_also_declaring(*columns: str) -> Any:
    """The base model with `dm.stores` declaring more columns — a second candidate each."""
    both = model()
    for column in columns:
        both.tables[1].columns.append(Column(name=column, type="String", role=ColumnRole.DIMENSION))
    return both


def _also_governing_stg_sales_daily(*columns: str) -> Any:
    """The base model plus `stg.sales_daily` — same short name, different mart."""
    extra = model()
    extra.tables.append(
        Table(
            name="stg.sales_daily",
            grain=["date"],
            columns=[
                Column(name=column, type="String", role=ColumnRole.DIMENSION) for column in columns
            ],
            physical=Physical(engine="clickhouse"),
        )
    )
    return extra


def test_a_bare_column_two_governed_sources_declare_is_not_guessed_at_but_said(
    tmp_path: Path,
) -> None:
    """Either table could own it — for the engine too — so the gate says the check was partial."""
    base = snap(tmp_path, "both", semantic=_stores_also_declaring("region"))
    sql = "SELECT region FROM dm.sales_daily AS a JOIN dm.stores AS b ON a.store_id = b.id"
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert findings[0].severity is Severity.ADVISORY
    assert "region" in findings[0].detail


def test_a_bare_column_no_governed_source_declares_names_the_scope(tmp_path: Path) -> None:
    """It is read from somewhere, and the model declares it on nothing in that scope."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, discount FROM dm.sales_daily AS a "
        "JOIN dm.stores AS b ON a.store_id = b.id"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    assert findings[0].severity is Severity.REVIEW
    detail = findings[0].detail
    assert "discount" in detail and "dm.sales_daily" in detail and "dm.stores" in detail


def test_a_bare_column_beside_an_ungoverned_mart_stays_unchecked(tmp_path: Path) -> None:
    """The model cannot see that mart's columns, so it cannot rule it out as the source."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, category FROM dm.sales_daily AS a "
        "JOIN staging.sales AS b ON a.store_id = b.id"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned", "asset.query_fields_unchecked"]
    assert "category" in findings[1].detail


def test_a_bare_column_beside_a_subquery_source_stays_unchecked(tmp_path: Path) -> None:
    """A subquery's columns are not a table's, and the snapshot declares nothing about them."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, category FROM dm.sales_daily AS a "
        "JOIN (SELECT id FROM dm.stores) AS b ON a.store_id = b.id"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert "category" in findings[0].detail


def test_a_bare_column_beside_a_cte_source_stays_unchecked(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    sql = (
        "WITH s AS (SELECT id FROM dm.stores) "
        "SELECT a.region, category FROM dm.sales_daily AS a JOIN s ON a.store_id = s.id"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert "category" in findings[0].detail


def test_a_column_qualified_by_an_unknown_alias_gains_no_candidates(tmp_path: Path) -> None:
    """Nothing in the statement resolves `z`, so the scope's tables are not its candidates."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, z.category FROM dm.sales_daily AS a "
        "JOIN dm.stores AS b ON a.store_id = b.id"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert "category" in findings[0].detail


def test_the_postgres_family_folds_a_bare_column_onto_its_one_candidate(tmp_path: Path) -> None:
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, CATEGORY FROM dm.sales_daily AS a "
        "JOIN dm.stores AS b ON a.store_id = b.id"
    )
    repo = repository(tmp_path / "repo", asset(reference_query=query(sql, engine="greenplum")))
    assert repo.ungoverned(base) == []


def test_clickhouse_reads_a_bare_column_case_as_clickhouse_does(tmp_path: Path) -> None:
    """`CATEGORY` is another column there, and no source in scope declares it."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, CATEGORY FROM dm.sales_daily AS a "
        "JOIN dm.stores AS b ON a.store_id = b.id"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    assert "CATEGORY" in findings[0].detail


def test_a_settled_name_does_not_cover_another_reference_of_the_same_name(
    tmp_path: Path,
) -> None:
    """One `category` the model settles says nothing about the `z.category` beside it.

    Keeping the remainder per name rather than per reference let the resolved one stand in
    for the unresolvable one, and the whole query then read as cleanly graded.
    """
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, category, z.category FROM dm.sales_daily AS a "
        "JOIN dm.stores AS b ON a.store_id = b.id"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert "z.category" in findings[0].detail


def test_a_settled_branch_does_not_cover_a_contested_one(tmp_path: Path) -> None:
    """The second branch resolves `region`; the first still leaves two sources declaring it."""
    both = _stores_also_declaring("region")
    both.tables.append(
        Table(
            name="dm.dates",
            grain=["d"],
            columns=[Column(name="d", type="Date", role=ColumnRole.TIME)],
            physical=Physical(engine="clickhouse"),
        )
    )
    base = snap(tmp_path, "both", semantic=both)
    sql = (
        "SELECT region FROM dm.sales_daily AS a JOIN dm.stores AS b ON a.store_id = b.id "
        "UNION ALL "
        "SELECT region FROM dm.sales_daily AS c JOIN dm.dates AS d ON c.date = d.d"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert "region" in findings[0].detail


def test_an_unknown_qualifier_is_not_called_an_unreadable_scope(tmp_path: Path) -> None:
    """Both sources here are governed tables; what nothing declares is `z` itself."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, z.category FROM dm.sales_daily AS a "
        "JOIN dm.stores AS b ON a.store_id = b.id"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    detail = findings[0].detail
    assert "no enclosing scope declares" in detail
    assert "cannot see into" not in detail


def test_a_correlated_scope_with_no_source_of_its_own_names_the_enclosing_query(
    tmp_path: Path,
) -> None:
    """The inner SELECT has no FROM at all — the name comes from the query around it."""
    base = snap(tmp_path, "base")
    sql = "SELECT (SELECT max(revenue)) FROM dm.sales_daily AS a"
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    detail = findings[0].detail
    assert "enclosing query" in detail
    assert "cannot see into" not in detail
    assert "offers no source" not in detail


def test_a_root_derived_table_with_no_from_is_sourceless(tmp_path: Path) -> None:
    """Nothing encloses a root FROM subquery, so an empty FROM is sourceless, not outer."""
    base = snap(tmp_path, "base")
    sql = "SELECT t.x FROM (SELECT discount AS x) AS t"
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    detail = findings[0].detail
    assert "offers no source" in detail
    assert "cannot see into" not in detail
    assert "enclosing query" not in detail


@pytest.mark.parametrize(
    "second",
    ["dm.sales_daily AS b", '"dm"."sales_daily" AS b'],
    ids=["as-written", "quoted"],
)
def test_a_self_join_offers_two_sources_though_they_are_one_table(
    tmp_path: Path, second: str
) -> None:
    """Two sources declare `category`, so no engine resolves it and neither may the gate.

    Counting distinct tables instead of sources made the verdict turn on how the second
    reference was quoted — the same statement, graded two ways.
    """
    base = snap(tmp_path, "base")
    sql = f"SELECT category FROM dm.sales_daily AS a JOIN {second} ON a.date = b.date"
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert "category" in findings[0].detail


def test_a_self_join_names_the_table_it_is_read_from_once(tmp_path: Path) -> None:
    """Two sources are two candidates for attribution, but one mart to go and fix."""
    base = snap(tmp_path, "base")
    sql = "SELECT discount FROM dm.sales_daily AS a JOIN dm.sales_daily AS b ON a.date = b.date"
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    assert "dm.sales_daily, dm.sales_daily" not in findings[0].detail
    assert "discount (read from one of dm.sales_daily)" in findings[0].detail


def test_two_contested_names_are_reported_in_the_plural(tmp_path: Path) -> None:
    base = snap(tmp_path, "both", semantic=_stores_also_declaring("region", "category"))
    sql = (
        "SELECT region, category FROM dm.sales_daily AS a "
        "JOIN dm.stores AS b ON a.store_id = b.id"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert "category, region are declared by several" in findings[0].detail


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
        "JOIN (SELECT id FROM dm.stores) AS b ON a.store_id = b.id"
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


# --- the sources a scope actually reads ---------------------------------------


def test_a_cte_nobody_reads_is_not_a_source_in_scope(tmp_path: Path) -> None:
    """Declaring a CTE does not put it in the FROM, so it offers a bare name nothing.

    The same statement without the WITH clause is graded cleanly; a CTE beside it that no
    FROM names cannot make `category` less decidable than it already was.
    """
    base = snap(tmp_path, "base")
    sql = "WITH x AS (SELECT 1 AS one) SELECT category FROM dm.sales_daily AS a"
    assert repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base) == []


def test_a_cte_nobody_reads_does_not_hide_an_undeclared_column(tmp_path: Path) -> None:
    """Counting the unread CTE as a source degraded a real finding into an advisory."""
    base = snap(tmp_path, "base")
    sql = "WITH x AS (SELECT 1 AS one) SELECT discount FROM dm.sales_daily AS a"
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    assert "discount" in findings[0].detail and "dm.sales_daily" in findings[0].detail


def test_a_cte_inherited_into_an_inner_scope_is_not_a_source_there(tmp_path: Path) -> None:
    """sqlglot hands every CTE down into nested scopes; the inner FROM still names one source."""
    base = snap(tmp_path, "base")
    sql = (
        "WITH s AS (SELECT 1 AS one) "
        "SELECT (SELECT max(revenue) FROM dm.sales_daily AS q) AS m FROM dm.sales_daily AS a"
    )
    assert repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base) == []


def test_an_unaliased_self_join_still_offers_two_sources(tmp_path: Path) -> None:
    """Two FROM items are two sources even where sqlglot has to invent the second name."""
    base = snap(tmp_path, "base")
    sql = "SELECT category FROM dm.sales_daily JOIN dm.sales_daily ON 1 = 1"
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert "category" in findings[0].detail


# --- a bare name written where the engine may look outward --------------------


@pytest.mark.parametrize("engine", ["postgresql", "clickhouse"])
def test_a_bare_name_in_an_exists_subquery_may_be_the_enclosing_query(
    tmp_path: Path, engine: str
) -> None:
    """`store_id` is the outer query's declared column, not an undeclared one of `dm.stores`.

    One structural rule for every engine: the gate does not know which server version is
    behind the asset, and calling this a column of `dm.stores` rejects valid reviewed SQL.
    """
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region FROM dm.sales_daily AS a "
        "WHERE EXISTS (SELECT 1 FROM dm.stores AS b WHERE b.id = store_id)"
    )
    findings = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine=engine))
    ).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert findings[0].severity is Severity.ADVISORY
    assert "store_id" in findings[0].detail
    assert "enclosing query" in findings[0].detail


@pytest.mark.parametrize("engine", ["postgresql", "clickhouse"])
def test_a_bare_name_in_a_scalar_subquery_may_be_the_enclosing_query(
    tmp_path: Path, engine: str
) -> None:
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, (SELECT max(b.id) FROM dm.stores AS b WHERE b.id = store_id) AS m "
        "FROM dm.sales_daily AS a"
    )
    findings = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine=engine))
    ).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert findings[0].severity is Severity.ADVISORY
    assert "store_id" in findings[0].detail
    assert "enclosing query" in findings[0].detail


@pytest.mark.parametrize("engine", ["postgresql", "clickhouse"])
def test_a_bare_name_in_an_exists_union_branch_may_be_the_enclosing_query(
    tmp_path: Path, engine: str
) -> None:
    """A UNION branch shares the subquery's range: `store_id` is still the outer column."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region FROM dm.sales_daily AS a WHERE EXISTS ("
        "SELECT 1 FROM dm.stores AS b WHERE b.id = store_id "
        "UNION ALL SELECT 1 FROM dm.stores AS c WHERE c.id = store_id)"
    )
    findings = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine=engine))
    ).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert findings[0].severity is Severity.ADVISORY
    assert "store_id" in findings[0].detail
    assert "enclosing query" in findings[0].detail


@pytest.mark.parametrize("engine", ["postgresql", "clickhouse"])
def test_a_bare_name_in_a_scalar_union_branch_may_be_the_enclosing_query(
    tmp_path: Path, engine: str
) -> None:
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, (SELECT max(b.id) FROM dm.stores AS b WHERE b.id = store_id "
        "UNION ALL SELECT 1) AS m FROM dm.sales_daily AS a"
    )
    findings = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine=engine))
    ).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert findings[0].severity is Severity.ADVISORY
    assert "store_id" in findings[0].detail
    assert "enclosing query" in findings[0].detail


def test_a_subquery_bare_name_its_own_source_declares_is_graded_there(tmp_path: Path) -> None:
    """The innermost level that has the column is where the engine reads it."""
    base = snap(tmp_path, "base")
    sql = "SELECT a.region FROM dm.sales_daily AS a WHERE a.store_id IN (SELECT id FROM dm.stores)"
    assert repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base) == []


def test_a_scalar_subquery_bare_name_its_own_source_declares_is_graded_there(
    tmp_path: Path,
) -> None:
    base = snap(tmp_path, "base")
    sql = "SELECT a.region, (SELECT max(id) FROM dm.stores AS b) AS m FROM dm.sales_daily AS a"
    assert repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base) == []


def test_two_local_sources_declaring_a_subquery_bare_name_stay_contested(tmp_path: Path) -> None:
    """Looking outward is one more candidate, not fewer: what two local sources dispute stands."""
    base = snap(tmp_path, "both", semantic=_stores_also_declaring("region"))
    sql = (
        "SELECT a.date FROM dm.sales_daily AS a WHERE EXISTS ("
        "SELECT 1 FROM dm.sales_daily AS s JOIN dm.stores AS t ON s.store_id = t.id "
        "WHERE region = 'north')"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert "region is declared by several" in findings[0].detail


def test_a_root_derived_table_is_not_enclosed(tmp_path: Path) -> None:
    """Nothing encloses a root FROM subquery, so its one source decides as it always did."""
    base = snap(tmp_path, "base")
    sql = "SELECT t.x FROM (SELECT discount AS x FROM dm.sales_daily) AS t"
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    assert "dm.sales_daily.discount" in findings[0].detail


def test_two_marts_sharing_a_short_name_are_two_candidates(tmp_path: Path) -> None:
    """`dm.sales_daily` and `stg.sales_daily` key on the same alias; they are still two sources.

    sqlglot stores the second under `sales_daily_2` while `Scope.references` repeats
    `sales_daily`. Looking the FROM items up by that name collapses both onto the first
    mart and grades a column only the second declares as undeclared.
    """
    base = snap(tmp_path, "stg", semantic=_also_governing_stg_sales_daily("discount"))
    sql = "SELECT discount FROM dm.sales_daily JOIN stg.sales_daily ON 1 = 1"
    assert repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base) == []


def test_two_marts_sharing_a_short_name_neither_declaring_are_both_named(tmp_path: Path) -> None:
    """Identity, not the short name: both FROM items are candidates when neither declares it."""
    base = snap(tmp_path, "stg", semantic=_also_governing_stg_sales_daily())
    sql = "SELECT discount FROM dm.sales_daily JOIN stg.sales_daily ON 1 = 1"
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    detail = findings[0].detail
    assert "discount" in detail
    assert "dm.sales_daily" in detail and "stg.sales_daily" in detail


def test_a_cte_sharing_a_marts_short_name_does_not_swallow_the_mart(tmp_path: Path) -> None:
    """`FROM sales_daily` is the CTE; `FROM dm.sales_daily` beside it is still the mart.

    Looking both FROM items up by the short name in `Scope.sources` collapses them onto
    the CTE, and the bare `discount` then vanishes as a CTE column. HEAD reported the
    name unchecked; grading it clean would hide a partially-checked query.
    """
    base = snap(tmp_path, "base")
    sql = (
        "WITH sales_daily AS (SELECT 1 AS one) "
        "SELECT discount FROM sales_daily JOIN dm.sales_daily ON 1 = 1"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert findings[0].severity is Severity.ADVISORY
    assert "discount" in findings[0].detail


def test_a_cte_joined_to_itself_offers_two_sources(tmp_path: Path) -> None:
    """Two FROM items are two sources even when they name the same CTE."""
    base = snap(tmp_path, "base")
    sql = (
        "WITH x AS (SELECT region, revenue FROM dm.sales_daily) "
        "SELECT region FROM x AS p JOIN x AS q ON 1 = 1"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert findings[0].severity is Severity.ADVISORY
    assert "region" in findings[0].detail


def test_a_derived_table_nested_in_a_subquery_is_not_enclosed(tmp_path: Path) -> None:
    """A FROM-subquery cannot read the query around it, even when it sits inside an IN.

    `discount` is graded as an undeclared column of `dm.sales_daily`. The IN subquery's
    bare `x` may still be unchecked — its only source is the derived table, whose columns
    the snapshot cannot see — but it is not an enclosing-query remainder: the derived
    table does not read outward.
    """
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region FROM dm.sales_daily AS a WHERE a.store_id IN "
        "(SELECT x FROM (SELECT discount AS x FROM dm.sales_daily) AS t)"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert "asset.query_ungoverned_field" in codes(findings)
    assert any("dm.sales_daily.discount" in item.detail for item in findings)
    assert all("enclosing query" not in item.detail for item in findings)


def test_a_union_inside_a_derived_table_is_not_enclosed(tmp_path: Path) -> None:
    """A set-operation branch inside a FROM-subquery still cannot read around it."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT t.x FROM (SELECT discount AS x FROM dm.sales_daily "
        "UNION ALL SELECT discount AS x FROM dm.sales_daily) AS t"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    assert "dm.sales_daily.discount" in findings[0].detail
    assert all("enclosing query" not in item.detail for item in findings)


def test_a_cte_body_inside_exists_is_not_enclosed(tmp_path: Path) -> None:
    """A CTE body is a new range; EXISTS around it does not make `discount` an outer name."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region FROM dm.sales_daily AS a WHERE EXISTS "
        "(WITH c AS (SELECT discount FROM dm.sales_daily) SELECT 1 FROM c)"
    )
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    assert "dm.sales_daily.discount" in findings[0].detail
    assert all("enclosing query" not in item.detail for item in findings)


def test_a_lateral_reads_its_sibling_sources(tmp_path: Path) -> None:
    """A LATERAL body sees the FROM items that precede it.

    sqlglot puts those siblings in the LATERAL node's `lateral_sources`, not in the inner
    SELECT's `references`. Dropping them would treat `category` as an undeclared column of
    `dm.stores` (or as an outer name this check cannot follow) even though the sibling
    mart declares it.
    """
    base = snap(tmp_path, "base")
    sql = (
        "SELECT t.x FROM dm.sales_daily AS a, LATERAL "
        "(SELECT category AS x FROM dm.stores AS b WHERE b.id = a.store_id) AS t"
    )
    assert (
        repository(
            tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
        ).ungoverned(base)
        == []
    )


def test_a_root_lateral_is_not_enclosed(tmp_path: Path) -> None:
    """A LATERAL at the root has no query outside its siblings; the candidate set is complete.

    What a lateral body reads beyond its own FROM are the FROM items written before it, and
    those are already local candidates. Counting the root as enclosed turned a real field
    finding into an advisory whose reason ("enclosing query") is untrue.
    """
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, t.total FROM dm.sales_daily AS a, LATERAL "
        "(SELECT sum(discount) AS total FROM dm.stores AS b WHERE b.id = a.store_id) AS t"
    )
    findings = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
    ).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    assert "discount" in findings[0].detail
    assert all("enclosing query" not in item.detail for item in findings)


def test_a_root_table_function_is_not_enclosed(tmp_path: Path) -> None:
    """unnest at the root is a UDTF whose only readable sources are the FROM siblings.

    `tags` is an undeclared column of `dm.sales_daily`. Marking the UDTF enclosed turned
    that into an enclosing-query advisory, which is untrue: there is no query outside those
    siblings. `t` is the table-function's own output and is not a mart column — that
    remainder is unchanged.
    """
    base = snap(tmp_path, "base")
    sql = "SELECT t FROM dm.sales_daily AS a, unnest(tags) AS t"
    findings = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
    ).ungoverned(base)
    assert "asset.query_ungoverned_field" in codes(findings)
    assert any("dm.sales_daily.tags" in item.detail for item in findings)
    assert all("enclosing query" not in item.detail for item in findings)


def test_a_lateral_inside_exists_stays_enclosed(tmp_path: Path) -> None:
    """A lateral nested in EXISTS still reads that subquery's enclosing query."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region FROM dm.sales_daily AS a WHERE EXISTS ("
        "SELECT 1 FROM dm.stores AS b, LATERAL "
        "(SELECT 1 FROM dm.stores AS c WHERE c.id = store_id) AS t)"
    )
    findings = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
    ).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert findings[0].severity is Severity.ADVISORY
    assert "store_id" in findings[0].detail
    assert "enclosing query" in findings[0].detail


def test_a_root_lateral_settles_a_sibling_column(tmp_path: Path) -> None:
    """revenue is the sibling mart's column; the candidate set at the root is complete."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, t.total FROM dm.sales_daily AS a, LATERAL "
        "(SELECT sum(revenue) AS total FROM dm.stores AS b WHERE b.id = a.store_id) AS t"
    )
    assert (
        repository(
            tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
        ).ungoverned(base)
        == []
    )


def test_a_union_inside_a_lateral_still_reads_siblings(tmp_path: Path) -> None:
    """A set-operation branch several scopes below the UDTF still sees the sibling mart.

    sqlglot hangs `lateral_sources` on the UDTF; a UNION inserts a SUBQUERY between the
    UDTF and the branch, and the branch is itself a UNION scope. Dropping the siblings
    at those levels grades `category` as an undeclared column of `dm.stores` even though
    the sibling `dm.sales_daily` declares it, and the same statement without the UNION
    is clean.
    """
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, t.x FROM dm.sales_daily AS a, LATERAL "
        "(SELECT category AS x FROM dm.stores AS b "
        "UNION ALL SELECT category AS x FROM dm.stores AS c) AS t"
    )
    assert (
        repository(
            tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
        ).ungoverned(base)
        == []
    )


def test_a_nested_lateral_still_reads_outer_siblings(tmp_path: Path) -> None:
    """A lateral nested in a lateral body still sees the outer lateral's preceding FROM.

    The inner body sits under another UDTF; walking only one parent drops `dm.sales_daily`
    and grades `revenue` as an undeclared column of `dm.stores`.
    """
    base = snap(tmp_path, "base")
    sql = (
        "SELECT t.x FROM dm.sales_daily AS a, LATERAL "
        "(SELECT 1 AS x FROM dm.stores AS b, LATERAL "
        "(SELECT sum(revenue) AS y FROM dm.stores AS d) AS u) AS t"
    )
    assert (
        repository(
            tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
        ).ungoverned(base)
        == []
    )


def test_a_subquery_inside_a_lateral_still_reads_siblings(tmp_path: Path) -> None:
    """A subquery nested in a lateral body still sees the sibling mart.

    The ancestor walk does not stop at SUBQUERY, so the lateral's preceding FROM items
    remain local candidates there. Leaving them out would leave `category` unchecked as
    an enclosing name, even though PostgreSQL resolves it to `dm.sales_daily`.
    """
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, t.x FROM dm.sales_daily AS a, LATERAL "
        "(SELECT 1 AS x FROM dm.stores AS b WHERE b.id IN "
        "(SELECT category FROM dm.stores AS c)) AS t"
    )
    assert (
        repository(
            tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
        ).ungoverned(base)
        == []
    )


def test_a_derived_table_inside_a_lateral_does_not_read_siblings(tmp_path: Path) -> None:
    """A FROM-subquery inside a lateral is a wall: it cannot see the lateral's siblings."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, t.x FROM dm.sales_daily AS a, LATERAL "
        "(SELECT y AS x FROM (SELECT category AS y FROM dm.stores AS b) AS inner_t) AS t"
    )
    findings = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
    ).ungoverned(base)
    assert "asset.query_ungoverned_field" in codes(findings)
    assert any("dm.stores.category" in item.detail for item in findings)
    assert all("sales_daily.category" not in item.detail for item in findings)


def test_a_cte_body_inside_a_lateral_does_not_read_siblings(tmp_path: Path) -> None:
    """A CTE body inside a lateral is a wall: it cannot see the lateral's siblings."""
    base = snap(tmp_path, "base")
    sql = (
        "SELECT a.region, t.x FROM dm.sales_daily AS a, LATERAL "
        "(WITH c AS (SELECT category AS y FROM dm.stores AS b) SELECT y AS x FROM c) AS t"
    )
    findings = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
    ).ungoverned(base)
    assert "asset.query_ungoverned_field" in codes(findings)
    assert any("dm.stores.category" in item.detail for item in findings)
    assert all("sales_daily.category" not in item.detail for item in findings)


def test_a_name_declared_at_two_lateral_levels_stays_unchecked(tmp_path: Path) -> None:
    """Siblings walked in from an enclosing lateral sit beside the inner body's own FROM.

    PostgreSQL would resolve `revenue` to the nearer `b`, but the candidate set is not
    ordered by level, so two governed sources declaring the name leave it unchecked.
    """
    base = snap(tmp_path, "base")
    sql = (
        "SELECT t.x FROM dm.sales_daily AS a, LATERAL "
        "(SELECT 1 AS x FROM dm.sales_daily AS b, LATERAL "
        "(SELECT sum(revenue) AS y FROM dm.stores AS d) AS u) AS t"
    )
    findings = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
    ).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert findings[0].severity is Severity.ADVISORY
    assert "revenue" in findings[0].detail
    assert "several of the governed sources" in findings[0].detail


def test_a_union_in_a_lateral_with_the_column_on_both_levels_stays_unchecked(
    tmp_path: Path,
) -> None:
    """A set-operation branch seeing both its own FROM and the enclosing lateral's sibling.

    Both levels name the same governed table — `a` and `b` are both `dm.sales_daily`; that
    is the point. Flattening them contests `category` even though the field question has
    one answer whichever level wins, and the one-level rule attributed the name to `b`
    alone.
    """
    base = snap(tmp_path, "base")
    sql = (
        "SELECT t.x FROM dm.sales_daily AS a, LATERAL "
        "(SELECT category AS x FROM dm.sales_daily AS b UNION ALL SELECT 'z' AS x) AS t"
    )
    findings = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
    ).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert findings[0].severity is Severity.ADVISORY
    assert "category" in findings[0].detail
    assert "several of the governed sources" in findings[0].detail


def test_an_ungoverned_outer_lateral_sibling_leaves_an_inner_name_unchecked(
    tmp_path: Path,
) -> None:
    """An ungoverned mart two lateral levels out is still a candidate of the inner body.

    `d` declares `revenue`, but the outer ungoverned mart is a candidate too, so the
    name stays unchecked beside the ungoverned-mart finding.
    """
    base = snap(tmp_path, "base")
    sql = (
        "SELECT t.x FROM dm.other AS a, LATERAL "
        "(SELECT 1 AS x FROM dm.stores AS b, LATERAL "
        "(SELECT sum(revenue) AS y FROM dm.sales_daily AS d) AS u) AS t"
    )
    findings = repository(
        tmp_path / "repo", asset(reference_query=query(sql, engine="postgresql"))
    ).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned", "asset.query_fields_unchecked"]
    assert "dm.other" in findings[0].detail
    assert "revenue" in findings[1].detail
    assert "cannot see" in findings[1].detail


def test_an_enclosed_name_with_no_local_source_cites_the_enclosing_query(
    tmp_path: Path,
) -> None:
    """A correlated subquery with no FROM of its own is reading the query around it."""
    base = snap(tmp_path, "base")
    sql = "SELECT a.region, (SELECT store_id) AS m FROM dm.sales_daily AS a"
    findings = repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base)
    assert codes(findings) == ["asset.query_fields_unchecked"]
    assert findings[0].severity is Severity.ADVISORY
    assert "store_id" in findings[0].detail
    assert "enclosing query" in findings[0].detail
    assert "offers no source" not in findings[0].detail
