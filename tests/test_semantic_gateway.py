"""Native semantic snapshots (DECISIONOPS_ARCHITECTURE §7.4, bootstrap task 4).

A snapshot is what a run is verified against: an immutable, content-addressed projection of
`model.yaml` plus the metric contracts. Two properties carry the weight — the canonical
digest is a function of the graph alone (so two snapshots of the same source are the same
snapshot), and anything the projection cannot represent becomes an explicit
:class:`SemanticGap` instead of quietly disappearing (invariant 6).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from auto_bi.evidence.canonical import digest_bytes, sha256_digest
from auto_bi.semantic.gateway import (
    NativeModelSource,
    SemanticSnapshot,
    SourceKind,
)
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    DataClassification,
    Join,
    Metric,
    Physical,
    SemanticModel,
    Table,
)
from auto_bi.severity import Severity


def model() -> SemanticModel:
    return SemanticModel(
        tables=[
            Table(
                name="dm.sales_daily",
                description="Daily sales",
                grain=["date", "store_id"],
                classification=DataClassification.INTERNAL,
                columns=[
                    Column(name="date", type="Date", role=ColumnRole.TIME),
                    Column(
                        name="store_id",
                        type="UInt32",
                        role=ColumnRole.DIMENSION,
                        fk="dm.stores.id",
                    ),
                    Column(name="region", type="String", role=ColumnRole.DIMENSION),
                    Column(name="category", type="String", role=ColumnRole.DIMENSION),
                    Column(
                        name="revenue",
                        type="Decimal(18, 2)",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                        classification=DataClassification.CONFIDENTIAL,
                    ),
                    Column(
                        name="gross_margin",
                        type="Decimal(18, 2)",
                        role=ColumnRole.MEASURE,
                        agg=Aggregation.SUM,
                    ),
                ],
                physical=Physical(
                    engine="clickhouse",
                    table_engine="MergeTree",
                    sorting_key=["date", "store_id"],
                    partition_key="toYYYYMM(date)",
                ),
            ),
            Table(
                name="dm.stores",
                grain=["id"],
                columns=[Column(name="id", type="UInt32", role=ColumnRole.DIMENSION)],
                physical=Physical(engine="clickhouse"),
            ),
        ],
        joins=[Join(left="dm.sales_daily.store_id", right="dm.stores.id")],
    )


def contract(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "api_version": "auto-bi.io/v2alpha1",
        "kind": "MetricContract",
        "metadata": {
            "id": "metric://sales/gross_margin_pct",
            "version": 3,
            "owner": "commercial-analytics",
            "status": "active",
        },
        "formula": {
            "kind": "typed_ratio",
            "numerator": {"table": "dm.sales_daily", "column": "gross_margin", "agg": "sum"},
            "denominator": {"table": "dm.sales_daily", "column": "revenue", "agg": "sum"},
        },
        "grain": {"table": "dm.sales_daily", "keys": ["date", "store_id"]},
        "dimensions": ["region", "category"],
        "additivity": "non_additive",
        "time": {"event_time_column": "date", "timezone": "Europe/Moscow"},
        "unit": {
            "unit": "ratio",
            "precision": 6,
            "null_semantics": "null_is_missing",
            "zero_semantics": "zero_is_value",
        },
        "freshness": {"max_age": "PT6H"},
        "classification": "internal",
        "policy_refs": ["policy://release/default@1"],
    }
    data.update(overrides)
    return data


def source(
    tmp_path: Path,
    *,
    semantic: SemanticModel | None = None,
    contracts: dict[str, Any] | None = None,
) -> NativeModelSource:
    (semantic or model()).dump(tmp_path / "model.yaml")
    contract_dir = tmp_path / "contracts"
    contract_dir.mkdir(exist_ok=True)
    for name, data in (contracts or {"gross_margin_pct.yaml": contract()}).items():
        payload = data if isinstance(data, str) else yaml.safe_dump(data, allow_unicode=True)
        path = contract_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    return NativeModelSource(tmp_path / "model.yaml", contract_dir)


def gap_codes(snapshot: SemanticSnapshot) -> list[str]:
    return [gap.code for gap in snapshot.gaps]


NONMAPPING_MODEL_DOCUMENTS = [
    pytest.param("", id="an empty file"),
    pytest.param(" \n# comment only\n", id="whitespace and comments only"),
    pytest.param("---\n", id="a document marker"),
    pytest.param("null\n", id="null"),
    pytest.param("~\n", id="tilde null"),
    pytest.param("false\n", id="false"),
    pytest.param("0\n", id="zero"),
    pytest.param("0.0\n", id="zero point zero"),
    pytest.param("[]\n", id="an empty list"),
    pytest.param("''\n", id="an empty YAML string"),
]


@pytest.mark.parametrize("text", NONMAPPING_MODEL_DOCUMENTS)
def test_source_rejects_a_nonmapping_model_document(tmp_path: Path, text: str) -> None:
    path = tmp_path / "model.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValidationError) as caught:
        NativeModelSource(path, None).snapshot()

    assert caught.value.title == SemanticModel.__name__
    assert caught.value.errors()[0]["loc"] == ()


def test_source_accepts_an_explicit_empty_mapping(tmp_path: Path) -> None:
    path = tmp_path / "model.yaml"
    path.write_text("{}\n", encoding="utf-8")

    snapshot = NativeModelSource(path, None).snapshot()

    assert snapshot.graph.tables == []
    assert snapshot.graph.joins == []
    assert snapshot.graph.metrics == []
    assert snapshot.gaps == []


def test_snapshot_projects_fields_joins_and_metrics(tmp_path: Path) -> None:
    snapshot = source(tmp_path).snapshot()
    assert snapshot.source_kind is SourceKind.NATIVE_MODEL_YAML
    assert [field.id for field in snapshot.graph.fields] == [
        "dm.sales_daily.category",
        "dm.sales_daily.date",
        "dm.sales_daily.gross_margin",
        "dm.sales_daily.region",
        "dm.sales_daily.revenue",
        "dm.sales_daily.store_id",
        "dm.stores.id",
    ]
    assert [table.id for table in snapshot.graph.tables] == ["dm.sales_daily", "dm.stores"]
    assert [join.id for join in snapshot.graph.joins] == ["dm.sales_daily.store_id->dm.stores.id"]
    assert [metric.id for metric in snapshot.graph.metrics] == ["metric://sales/gross_margin_pct"]
    assert snapshot.gaps == []


def test_metric_node_records_the_fields_it_depends_on(tmp_path: Path) -> None:
    """The impact chain starts here: a changed field must find its metrics."""
    metric = source(tmp_path).snapshot().graph.metrics[0]
    assert metric.fields == [
        "dm.sales_daily.date",
        "dm.sales_daily.gross_margin",
        "dm.sales_daily.revenue",
        "dm.sales_daily.store_id",
    ]
    assert metric.version == 3
    assert metric.owner == "commercial-analytics"


def test_classification_falls_back_from_column_to_table_to_internal(tmp_path: Path) -> None:
    fields = {field.id: field.classification for field in source(tmp_path).snapshot().graph.fields}
    assert fields["dm.sales_daily.revenue"] is DataClassification.CONFIDENTIAL  # column-level
    assert fields["dm.sales_daily.date"] is DataClassification.INTERNAL  # inherited from table
    assert fields["dm.stores.id"] is DataClassification.INTERNAL  # documented default


def test_canonical_digest_is_a_function_of_the_graph(tmp_path: Path) -> None:
    snapshot = source(tmp_path).snapshot()
    again = source(tmp_path).snapshot(revision="git:abc123")
    assert snapshot.canonical_digest == again.canonical_digest
    assert again.source_revision == "git:abc123"
    assert snapshot.canonical_digest == sha256_digest(snapshot.graph.model_dump(mode="json"))
    assert snapshot.source_digest != snapshot.canonical_digest


def test_canonical_digest_changes_when_the_meaning_changes(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    base = source(tmp_path).snapshot()
    changed_model = model()
    changed_model.tables[0].columns[4].agg = Aggregation.AVG
    candidate = source(tmp_path_factory.mktemp("candidate"), semantic=changed_model).snapshot()
    assert candidate.canonical_digest != base.canonical_digest


def test_snapshot_cannot_misreport_its_own_digest(tmp_path: Path) -> None:
    snapshot = source(tmp_path).snapshot()
    payload = snapshot.model_dump(mode="json")
    payload["canonical_digest"] = sha256_digest({"not": "the graph"})
    with pytest.raises(ValidationError, match="canonical_digest"):
        SemanticSnapshot.model_validate(payload)


def test_raw_sql_metric_becomes_a_gap_without_leaking_the_sql(tmp_path: Path) -> None:
    """model.yaml metrics are opaque SQL: recorded as a gap, never guessed at."""
    semantic = model()
    semantic.metrics = [Metric(name="gmv", sql="sum(revenue * rate)")]
    snapshot = source(tmp_path, semantic=semantic).snapshot()
    gap = next(gap for gap in snapshot.gaps if gap.code == "gap.raw_sql_metric")
    assert gap.severity is Severity.REVIEW
    assert gap.path == "metrics.gmv"
    assert gap.source_value_ref == sha256_digest("sum(revenue * rate)")
    assert "revenue" not in gap.reason


def test_metric_referencing_a_missing_field_is_a_blocking_gap(tmp_path: Path) -> None:
    broken = contract()
    broken["formula"]["numerator"]["column"] = "margin_typo"
    snapshot = source(tmp_path, contracts={"gross_margin_pct.yaml": broken}).snapshot()
    gap = next(gap for gap in snapshot.gaps if gap.code == "gap.metric_field_missing")
    assert gap.severity is Severity.BLOCK
    assert "dm.sales_daily.margin_typo" in gap.reason


def test_dangling_join_is_a_blocking_gap(tmp_path: Path) -> None:
    semantic = model()
    semantic.joins = [Join(left="dm.sales_daily.store_id", right="dm.stores.missing")]
    snapshot = source(tmp_path, semantic=semantic).snapshot()
    assert "gap.dangling_join" in gap_codes(snapshot)
    assert snapshot.graph.joins == []


def test_table_without_physical_facts_is_an_advisory_gap(tmp_path: Path) -> None:
    semantic = model()
    semantic.tables[1].physical = None
    snapshot = source(tmp_path, semantic=semantic).snapshot()
    gap = next(gap for gap in snapshot.gaps if gap.code == "gap.missing_physical")
    assert gap.severity is Severity.ADVISORY
    assert gap.path == "tables.dm.stores.physical"


def test_unparsable_contract_is_a_blocking_gap_not_a_silent_drop(tmp_path: Path) -> None:
    snapshot = source(
        tmp_path,
        contracts={
            "gross_margin_pct.yaml": contract(),
            "broken.yaml": "kind: MetricContract\nmetadata: {id: metric://x/y}\n",
        },
    ).snapshot()
    assert "gap.contract_invalid" in gap_codes(snapshot)
    assert [metric.id for metric in snapshot.graph.metrics] == ["metric://sales/gross_margin_pct"]


def test_json_beside_the_contracts_is_not_read_by_the_snapshot(tmp_path: Path) -> None:
    """Directory discovery of `decision validate` takes JSON; the snapshot reads YAML only."""
    (tmp_path / "plain").mkdir()
    (tmp_path / "beside").mkdir()
    plain = source(tmp_path / "plain").snapshot()
    other = contract(metadata={**contract()["metadata"], "id": "metric://sales/other_pct"})
    beside = source(
        tmp_path / "beside",
        contracts={
            "gross_margin_pct.yaml": contract(),
            "broken.json": "{",
            "other_pct.json": json.dumps(other),
        },
    ).snapshot()
    assert beside.gaps == []
    assert beside.source_digest == plain.source_digest
    assert beside.canonical_digest == plain.canonical_digest


def _other_metric() -> dict[str, Any]:
    return contract(metadata={**contract()["metadata"], "id": "metric://sales/other_pct"})


def test_a_ref_without_two_contracts_of_one_file_name_keeps_its_source_digest(
    tmp_path: Path,
) -> None:
    """The digest verifier 109 gave it: the model and each contract under its file name,
    wherever below the contracts path the contract lies."""
    (tmp_path / "flat").mkdir()
    (tmp_path / "below").mkdir()
    flat = source(
        tmp_path / "flat",
        contracts={"gross_margin_pct.yaml": contract(), "other_pct.yml": _other_metric()},
    ).snapshot()
    below = source(
        tmp_path / "below",
        contracts={"a/gross_margin_pct.yaml": contract(), "b/c/other_pct.yml": _other_metric()},
    ).snapshot()

    root = tmp_path / "flat"
    expected = sha256_digest(
        {
            "model": digest_bytes((root / "model.yaml").read_bytes()),
            "gross_margin_pct.yaml": digest_bytes(
                (root / "contracts" / "gross_margin_pct.yaml").read_bytes()
            ),
            "other_pct.yml": digest_bytes((root / "contracts" / "other_pct.yml").read_bytes()),
        }
    )
    assert flat.source_digest == expected
    assert below.source_digest == expected


def test_every_copy_of_a_contract_file_name_is_in_the_source_digest(tmp_path: Path) -> None:
    """Copies of a contract in two directories are two files the ref was read from, though
    their bytes are alike: one copy, two and three are three sources."""
    digests = []
    for count in (1, 2, 3):
        (tmp_path / str(count)).mkdir()
        copies = {f"{below}/gross_margin_pct.yaml": contract() for below in "abc"[:count]}
        digests.append(source(tmp_path / str(count), contracts=copies).snapshot().source_digest)

    assert len(set(digests)) == 3


def test_three_contracts_of_one_file_name_are_each_in_the_source_digest(tmp_path: Path) -> None:
    """A name of three files keeps the three digests themselves, not a summary of them: the
    digest is the sorted list of all three, and a change to any of them moves it."""
    third = contract(metadata={**contract()["metadata"], "id": "metric://sales/third_pct"})
    frozen = source(
        tmp_path, contracts={"a/m.yaml": contract(), "b/m.yaml": _other_metric(), "c/m.yaml": third}
    )
    files = sorted((tmp_path / "contracts").rglob("m.yaml"))
    model_digest = digest_bytes((tmp_path / "model.yaml").read_bytes())

    def expected() -> str:
        found = sorted(digest_bytes(path.read_bytes()) for path in files)
        return sha256_digest({"model": model_digest, "m.yaml": found})

    before = frozen.snapshot()
    assert len(files) == 3
    assert before.source_digest == expected()

    for path in files:
        path.write_bytes(path.read_bytes() + b"# a comment that changes no meaning\n")
        after = frozen.snapshot()
        assert after.graph == before.graph
        assert after.source_digest == expected()
        assert after.source_digest != before.source_digest
        before = after


def test_contracts_of_one_file_name_are_frozen_whatever_directory_holds_which(
    tmp_path: Path,
) -> None:
    """The source digest does not say where below the contracts path a contract lies: two refs
    that hold the same contracts under the same file names have one source digest, and — the
    contracts being of different metrics and leaving no gap — they are one snapshot."""
    (tmp_path / "one").mkdir()
    (tmp_path / "other").mkdir()
    one = source(
        tmp_path / "one", contracts={"a/m.yaml": contract(), "b/m.yaml": _other_metric()}
    ).snapshot()
    other = source(
        tmp_path / "other", contracts={"a/m.yaml": _other_metric(), "b/m.yaml": contract()}
    ).snapshot()

    assert one.gaps == []
    assert [metric.id for metric in one.graph.metrics] == [
        "metric://sales/gross_margin_pct",
        "metric://sales/other_pct",
    ]
    assert other == one


def test_a_contracts_file_named_model_does_not_take_the_place_of_the_model(
    tmp_path: Path,
) -> None:
    """A contracts path that is one file is read whatever its name, and `model` is the name
    the model's own digest is kept under: the name keeps both digests."""
    model().dump(tmp_path / "model.yaml")
    named = tmp_path / "model"
    named.write_text(yaml.safe_dump(contract(), allow_unicode=True), encoding="utf-8")
    frozen = NativeModelSource(tmp_path / "model.yaml", named)
    before = frozen.snapshot()
    path = tmp_path / "model.yaml"
    path.write_bytes(path.read_bytes() + b"\n# a comment that changes no meaning\n")
    after = frozen.snapshot()

    assert [metric.id for metric in before.graph.metrics] == ["metric://sales/gross_margin_pct"]
    assert after.graph == before.graph
    assert after.canonical_digest == before.canonical_digest
    assert after.source_digest != before.source_digest
    digests = [digest_bytes(path.read_bytes()), digest_bytes(named.read_bytes())]
    assert after.source_digest == sha256_digest({"model": sorted(digests)})


def test_capabilities_are_declared_not_assumed(tmp_path: Path) -> None:
    snapshot = source(tmp_path).snapshot()
    assert "metric_contracts" in snapshot.capabilities
    assert "row_level_policies" not in snapshot.capabilities


def test_missing_model_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        NativeModelSource(tmp_path / "absent.yaml", None).snapshot()


def test_the_graph_carries_the_budget_the_contract_declares(tmp_path: Path) -> None:
    """Step 7 grades a plan compiled from this node against this node's bound; a budget
    read from a file beside the snapshot could belong to another version of the metric."""
    snapshot = source(
        tmp_path, contracts={"m.yaml": contract(budgets={"max_scanned_rows": 1000})}
    ).snapshot()

    node = snapshot.graph.metrics[0]
    assert node.budgets is not None
    assert node.budgets.max_scanned_rows == 1000
    assert (node.budgets.max_scanned_bytes, node.budgets.timeout_ms) == (None, None)


def test_a_metric_that_declares_no_budget_carries_none(tmp_path: Path) -> None:
    assert source(tmp_path).snapshot().graph.metrics[0].budgets is None


def test_a_moved_budget_moves_the_canonical_digest(tmp_path: Path) -> None:
    """It is part of what the metric means, so two snapshots that disagree about it are
    not the same snapshot."""
    plain = source(tmp_path / "plain").snapshot()
    bounded = source(
        tmp_path / "bounded", contracts={"m.yaml": contract(budgets={"max_scanned_rows": 1000})}
    ).snapshot()

    assert plain.canonical_digest != bounded.canonical_digest
