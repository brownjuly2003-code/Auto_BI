"""Metric-declared join paths survive canonical projection and directional diff (T-37)."""

from pathlib import Path

import pytest
import yaml

from auto_bi.decision.validate import load_contracts
from auto_bi.evidence.canonical import sha256_digest
from auto_bi.semantic.diff import ChangeKind, diff_snapshots
from auto_bi.semantic.gateway import (
    NATIVE_SOURCE_VERSION,
    NativeModelSource,
    SemanticSnapshot,
    _build_graph,
)
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.severity import Severity
from auto_bi.verification.release_gate import GateInputs, run_gate
from auto_bi.verification.repository import VerifiedAsset, VerifiedAssetRepository
from tests.test_bi_contract_validation import REF, MemoryStore, spec
from tests.test_semantic_gateway import contract, model
from tests.test_verified_assets import asset

METRIC = "metric://sales/gross_margin_pct"
OTHER = "metric://sales/other_pct"
FIXTURES = Path(__file__).parent / "fixtures" / "decision"
J = {"left": "dm.sales_daily.store_id", "right": "dm.stores.id", "type": "many_to_one"}
D = {**J, "left": "dm.sales_daily.date"}
ONE = {**J, "type": "one_to_one"}
REVERSE = {**J, "left": J["right"], "right": J["left"]}
GHOST = {**J, "left": "dm.sales_daily.ghost"}
PATH = "dm.sales_daily.store_id->dm.stores.id"
DATE_PATH = "dm.sales_daily.date->dm.stores.id"


def snapshot(paths=(), *, contracts=None, semantic=None):
    gaps = []
    if contracts is None:
        contracts = [contract(join_paths=list(paths))]
    graph = _build_graph(
        semantic if semantic is not None else model(),
        [MetricContract.model_validate(c) for c in contracts],
        gaps,
    )
    digest = sha256_digest(graph.model_dump(mode="json"))
    return SemanticSnapshot(
        source_kind="native_model_yaml",
        source_version=NATIVE_SOURCE_VERSION,
        source_digest=digest,
        canonical_digest=digest,
        graph=graph,
        gaps=gaps,
    )


def declarations(snap):
    return [(p.id, p.left, p.right, p.type) for p in snap.graph.metrics[0].join_paths]


def change_rows(diff):
    return [
        (c.code, c.kind, c.severity, c.subject, c.impact.metrics, c.impact.fields, c.detail)
        for c in diff.changes
    ]


def metric_change(direction, severity, detail, metric=METRIC):
    return (
        f"metric.join_path_{direction}",
        ChangeKind.MODIFIED,
        severity,
        metric,
        [metric],
        [],
        detail,
    )


def repository(base):
    return VerifiedAssetRepository(
        [
            VerifiedAsset.model_validate(
                asset(
                    semantic_digest=base.canonical_digest,
                    expectation={"kind": "invariants", "invariants": ["margin remains a ratio"]},
                )
            )
        ]
    )


@pytest.mark.parametrize("after", [[], [ONE]], ids=["removed", "retyped"])
def test_path_change_moves_digest_and_carries_declarations(after):
    base, candidate = snapshot([J]), snapshot(after)
    assert base.canonical_digest != candidate.canonical_digest
    assert declarations(base) == [(PATH, J["left"], J["right"], "many_to_one")]
    assert declarations(candidate) == [(PATH, p["left"], p["right"], p["type"]) for p in after]


def test_order_and_repetition_are_canonical():
    base, candidate = snapshot([J, D, J]), snapshot([D, J])
    assert base.canonical_digest == candidate.canonical_digest
    assert (
        declarations(base)
        == declarations(candidate)
        == [
            (DATE_PATH, D["left"], D["right"], "many_to_one"),
            (PATH, J["left"], J["right"], "many_to_one"),
        ]
    )
    assert base.gaps == candidate.gaps == []
    assert base.graph.metrics[0].fields == snapshot().graph.metrics[0].fields


def test_native_projection_version_is_bumped(monkeypatch):
    assert NATIVE_SOURCE_VERSION not in {"1", "2"}
    model_path = FIXTURES / "in-memory-model.yaml"
    read_bytes = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: (
            yaml.safe_dump(model().model_dump(mode="json")).encode("utf-8")
            if path == model_path
            else read_bytes(path)
        ),
    )
    native = NativeModelSource(model_path, FIXTURES / "gross_margin_pct.yaml").snapshot()
    assert native.source_version == NATIVE_SOURCE_VERSION


def test_snapshot_without_metrics_keeps_digest():
    assert snapshot(contracts=[]).canonical_digest == (
        "sha256:d8cad3bada2df8258bcf8f17c86cc4f491fd0a4205c9dc1acd7d751c17c6a0d9"
    )


@pytest.mark.parametrize(
    "paths,missing,conflict",
    [
        ([GHOST], "dm.sales_daily.ghost", False),
        ([{**GHOST, "right": "dm.stores.ghost"}], "dm.sales_daily.ghost, dm.stores.ghost", False),
        ([{**GHOST, "right": GHOST["left"]}], "dm.sales_daily.ghost", False),
        ([ONE, J, ONE], None, True),
        ([{**GHOST, "type": "one_to_one"}, GHOST, GHOST], "dm.sales_daily.ghost", True),
    ],
    ids=["dangling", "both-missing", "same-missing", "conflict", "dangling-conflict"],
)
def test_gap_carries_every_declaration_in_order(paths, missing, conflict):
    snap = snapshot(paths)
    left, right = paths[0]["left"], paths[0]["right"]
    path = f"{left}->{right}"
    types = sorted({p["type"] for p in paths})
    assert declarations(snap) == [(path, left, right, t) for t in types]
    expected = []
    if missing:
        expected.append(
            (
                "gap.metric_join_dangling",
                Severity.BLOCK,
                f"metrics.{METRIC}.join_paths.{path}",
                f"join path endpoint is not a declared field: {missing}",
            )
        )
    if conflict:
        expected.append(
            (
                "gap.metric_join_conflict",
                Severity.BLOCK,
                f"metrics.{METRIC}.join_paths.{path}",
                "join path declared with more than one cardinality: many_to_one, one_to_one",
            )
        )
    assert [(g.code, g.severity, g.path, g.reason) for g in snap.gaps] == expected


def test_join_gaps_follow_field_gaps_then_path_order():
    data = contract(
        dimensions=["missing"],
        join_paths=[GHOST, {**GHOST, "type": "one_to_one"}, {**D, "right": "dm.stores.ghost"}],
    )
    snap = snapshot(contracts=[data])
    assert [(g.code, g.path, g.reason) for g in snap.gaps] == [
        (
            "gap.metric_field_missing",
            f"metrics.{METRIC}",
            "metric references a field the model does not declare: dm.sales_daily.missing",
        ),
        (
            "gap.metric_join_dangling",
            f"metrics.{METRIC}.join_paths.dm.sales_daily.date->dm.stores.ghost",
            "join path endpoint is not a declared field: dm.stores.ghost",
        ),
        (
            "gap.metric_join_dangling",
            f"metrics.{METRIC}.join_paths.dm.sales_daily.ghost->dm.stores.id",
            "join path endpoint is not a declared field: dm.sales_daily.ghost",
        ),
        (
            "gap.metric_join_conflict",
            f"metrics.{METRIC}.join_paths.dm.sales_daily.ghost->dm.stores.id",
            "join path declared with more than one cardinality: many_to_one, one_to_one",
        ),
    ]
    assert all(g.severity == Severity.BLOCK for g in snap.gaps)


@pytest.mark.parametrize(
    "before,after,expected",
    [
        (
            [J],
            [],
            [
                metric_change(
                    "removed",
                    Severity.BLOCK,
                    f"join paths no longer declared: {PATH} (many_to_one)",
                )
            ],
        ),
        (
            [J],
            [ONE],
            [
                metric_change(
                    "changed",
                    Severity.REVIEW,
                    f"join path cardinality changed: {PATH} (many_to_one) -> (one_to_one)",
                )
            ],
        ),
        (
            [],
            [J],
            [
                metric_change(
                    "added", Severity.ADVISORY, f"join paths now declared: {PATH} (many_to_one)"
                )
            ],
        ),
        (
            [J, D],
            [],
            [
                metric_change(
                    "removed",
                    Severity.BLOCK,
                    f"join paths no longer declared: {DATE_PATH} (many_to_one); "
                    f"{PATH} (many_to_one)",
                )
            ],
        ),
        (
            [J],
            [REVERSE],
            [
                metric_change(
                    "removed",
                    Severity.BLOCK,
                    f"join paths no longer declared: {PATH} (many_to_one)",
                ),
                metric_change(
                    "added",
                    Severity.ADVISORY,
                    "join paths now declared: dm.stores.id->dm.sales_daily.store_id (many_to_one)",
                ),
            ],
        ),
        ([J], [J], []),
        ([J, D], [D, J], []),
        ([J, D, J], [D, J, D], []),
        (
            [J, D],
            [ONE, REVERSE],
            [
                metric_change(
                    "removed",
                    Severity.BLOCK,
                    f"join paths no longer declared: {DATE_PATH} (many_to_one)",
                ),
                metric_change(
                    "changed",
                    Severity.REVIEW,
                    f"join path cardinality changed: {PATH} (many_to_one) -> (one_to_one)",
                ),
                metric_change(
                    "added",
                    Severity.ADVISORY,
                    "join paths now declared: dm.stores.id->dm.sales_daily.store_id (many_to_one)",
                ),
            ],
        ),
    ],
    ids=[
        "removed",
        "changed",
        "added",
        "two-removed",
        "reverse",
        "unchanged",
        "reordered",
        "repeated",
        "all-directions",
    ],
)
def test_join_path_diff(before, after, expected):
    base, candidate = snapshot(before), snapshot(after)
    assert change_rows(diff_snapshots(base, candidate)) == expected
    assert base.gaps == candidate.gaps == []
    assert (base.canonical_digest != candidate.canonical_digest) == bool(expected)


def test_path_becomes_conflicting():
    diff = diff_snapshots(snapshot([J]), snapshot([ONE, J]))
    assert change_rows(diff) == [
        metric_change(
            "changed",
            Severity.REVIEW,
            f"join path cardinality changed: {PATH} (many_to_one) -> (many_to_one, one_to_one)",
        ),
        (
            "gap.appeared",
            ChangeKind.ADDED,
            Severity.BLOCK,
            f"metrics.{METRIC}.join_paths.{PATH}",
            [],
            [],
            "gap.metric_join_conflict: join path declared with more than one cardinality: "
            "many_to_one, one_to_one",
        ),
    ]


def test_other_metrics_path_only_impacts_that_metric():
    first = contract(join_paths=[J])
    second = contract(metadata={**contract()["metadata"], "id": OTHER}, join_paths=[J])
    base = snapshot(contracts=[first, second], semantic=model())
    candidate = snapshot(contracts=[first, {**second, "join_paths": []}], semantic=model())
    diff = diff_snapshots(base, candidate)
    assert change_rows(diff) == [
        metric_change(
            "removed",
            Severity.BLOCK,
            f"join paths no longer declared: {PATH} (many_to_one)",
            OTHER,
        )
    ]
    assert repository(base).invalidate(diff) == []


@pytest.mark.parametrize(
    "before,after,code",
    [
        ([J], [], "metric.join_path_removed"),
        ([J], [ONE], "metric.join_path_changed"),
        ([], [J], None),
    ],
    ids=["removed", "changed", "added"],
)
def test_asset_invalidation(before, after, code):
    base, candidate = snapshot(before), snapshot(after)
    invalidated = repository(base).invalidate(diff_snapshots(base, candidate))
    if code is None:
        assert invalidated == []
    else:
        assert len(invalidated) == 1
        finding = invalidated[0]
        assert finding.code == "asset.metric_changed"
        assert finding.severity == Severity.REVIEW
        assert f"{code} on {METRIC}" in finding.detail


def test_model_joins_keep_their_rules():
    candidate_model = model().model_copy(update={"joins": []})
    base, candidate = snapshot([J]), snapshot([J], semantic=candidate_model)
    assert base.gaps == candidate.gaps == []
    assert change_rows(diff_snapshots(base, candidate)) == [
        (
            "join.removed",
            ChangeKind.REMOVED,
            Severity.BLOCK,
            PATH,
            [],
            [],
            "join path is gone: anything that spanned it cannot be re-derived",
        ),
    ]


@pytest.mark.parametrize(
    "before,after,removed",
    [([J], [], True), ([J], [J], False), ([J, D], [D, J, J], False)],
    ids=["removed", "valid-control", "reordered-repeated-control"],
)
def test_gate_names_removed_path_and_preserves_valid_control(before, after, removed):
    loaded = load_contracts([FIXTURES])
    base, candidate = snapshot(before), snapshot(after)
    store = MemoryStore()
    report = run_gate(
        GateInputs(
            product=next(iter(loaded.products.values())),
            validation=loaded.report,
            base=base,
            candidate=candidate,
            candidate_model=model(),
            dashboards={REF: spec()},
            assets=repository(base),
        ),
        store=store,
        created_at="2026-10-02T00:00:00Z",
    )
    checks = {c.check_id: c for c in report.checks}
    assert checks["bi.contract"].status.value == "pass"
    assert checks["bi.contract"].summary_code == "bi.compiled"
    assert checks["semantic.gaps"].status.value == "pass"
    semantic, assets = checks["semantic.diff"], checks["assets.invalidated"]
    if removed:
        assert semantic.status.value == "block"
        assert semantic.summary_code == "semantic.changed"
        assert semantic.detail == "1 semantic change(s) between the snapshots"
        assert assets.status.value == "review"
        assert assets.summary_code == "asset.metric_changed"
        assert f"metric.join_path_removed on {METRIC}" in assets.detail
        assert checks["impact.products"].summary_code != "impact.none"
        assert report.verdict.value == "block"
        assert "0 semantic change(s)" not in store.get(report.run_id, "reports/summary.md").decode(
            "utf-8"
        )
    else:
        assert (semantic.status.value, semantic.summary_code) == ("pass", "semantic.unchanged")
        assert (assets.status.value, assets.summary_code) == ("pass", "assets.valid")
        assert report.verdict.value in {"pass", "pass_with_advisory"}
