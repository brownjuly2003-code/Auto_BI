"""Declared metric freshness survives the canonical projection and directional diff."""

import sys
from pathlib import Path

import pytest
import yaml

from auto_bi.decision.validate import load_contracts
from auto_bi.evidence.canonical import sha256_digest
from auto_bi.scalars import duration_seconds
from auto_bi.semantic.diff import ChangeKind, diff_snapshots
from auto_bi.semantic.gateway import (
    NATIVE_SOURCE_VERSION,
    NativeModelSource,
    SemanticSnapshot,
    _build_graph,
)
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.severity import Severity
from auto_bi.verification.environment import check_freshness, questions_for
from auto_bi.verification.release_gate import GateInputs, run_gate
from tests.test_bi_contract_validation import REF, MemoryStore, spec
from tests.test_environment_checks import CREATED_AT, fact, facts, product
from tests.test_result_provenance import attestation, grade
from tests.test_semantic_gateway import contract, model

METRIC = "metric://sales/gross_margin_pct"
LONG_SIX_HOURS = "PT" + "0" * 4300 + "6H"
LONG_DAYS = "P" + "9" * 4301 + "D"
FIXTURES = Path(__file__).parent / "fixtures" / "decision"


def snapshot(max_age="PT6H", preconditions=()):
    gaps = []
    metric = MetricContract.model_validate(
        contract(freshness={"max_age": max_age, "quality_preconditions": list(preconditions)})
    )
    graph = _build_graph(model(), [metric], gaps)
    digest = sha256_digest(graph.model_dump(mode="json"))
    return SemanticSnapshot(
        source_kind="native_model_yaml",
        source_version="1",
        source_digest=digest,
        canonical_digest=digest,
        graph=graph,
        gaps=gaps,
    )


def test_changed_window_moves_digest_and_is_carried():
    base, candidate = snapshot("PT6H"), snapshot("P7D")
    assert base.canonical_digest != candidate.canonical_digest
    assert base.graph.metrics[0].freshness.max_age == "PT6H"
    assert candidate.graph.metrics[0].freshness.max_age == "P7D"


def test_preconditions_are_sorted_unique_and_order_does_not_move_digest():
    base = snapshot(preconditions=["dq.b", "dq.a", "dq.b"])
    candidate = snapshot(preconditions=["dq.a", "dq.b"])
    assert base.graph.metrics[0].freshness.quality_preconditions == ["dq.a", "dq.b"]
    assert candidate.graph.metrics[0].freshness.quality_preconditions == ["dq.a", "dq.b"]
    assert base.canonical_digest == candidate.canonical_digest


def test_native_projection_version_is_bumped(monkeypatch):
    assert NATIVE_SOURCE_VERSION != "1"
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


@pytest.mark.parametrize(
    "before,after,dropped,added,expected",
    [
        ("PT6H", "P7D", [], [], [("relaxed", Severity.REVIEW, "max_age PT6H -> P7D")]),
        ("P7D", "PT6H", [], [], [("tightened", Severity.ADVISORY, "max_age P7D -> PT6H")]),
        (
            "PT6H",
            LONG_SIX_HOURS,
            [],
            [],
            [("restated", Severity.ADVISORY, f"max_age PT6H -> {LONG_SIX_HOURS}, the same length")],
        ),
        (
            "PT6H",
            LONG_DAYS,
            [],
            [],
            [("relaxed", Severity.REVIEW, f"max_age PT6H -> {LONG_DAYS}")],
        ),
        (
            "PT6H",
            "PT360M",
            [],
            [],
            [("restated", Severity.ADVISORY, "max_age PT6H -> PT360M, the same length")],
        ),
        (
            "P1M",
            "P30D",
            [],
            [],
            [("relaxed", Severity.REVIEW, "max_age P1M -> P30D, which cannot be ordered")],
        ),
        (
            "PT6H",
            "PT6H",
            ["dq.orders_loaded"],
            [],
            [("relaxed", Severity.REVIEW, "quality precondition dropped: dq.orders_loaded")],
        ),
        (
            "PT6H",
            "PT6H",
            [],
            ["dq.orders_loaded"],
            [("tightened", Severity.ADVISORY, "quality precondition added: dq.orders_loaded")],
        ),
        (
            "P7D",
            "PT6H",
            ["dq.orders_loaded"],
            [],
            [
                ("relaxed", Severity.REVIEW, "quality precondition dropped: dq.orders_loaded"),
                ("tightened", Severity.ADVISORY, "max_age P7D -> PT6H"),
            ],
        ),
        (
            "PT6H",
            "P7D",
            ["dq.z", "dq.a"],
            ["dq.y", "dq.b"],
            [
                (
                    "relaxed",
                    Severity.REVIEW,
                    "max_age PT6H -> P7D; quality precondition dropped: dq.a; "
                    "quality precondition dropped: dq.z",
                ),
                (
                    "tightened",
                    Severity.ADVISORY,
                    "quality precondition added: dq.b; quality precondition added: dq.y",
                ),
            ],
        ),
        (
            "PT6H",
            "PT360M",
            ["dq.orders_loaded"],
            ["dq.checked"],
            [
                ("relaxed", Severity.REVIEW, "quality precondition dropped: dq.orders_loaded"),
                ("tightened", Severity.ADVISORY, "quality precondition added: dq.checked"),
                ("restated", Severity.ADVISORY, "max_age PT6H -> PT360M, the same length"),
            ],
        ),
        ("PT6H", "PT6H", [], [], []),
        (
            "PT6H",
            "P1000000000D",
            [],
            [],
            [("relaxed", Severity.REVIEW, "max_age PT6H -> P1000000000D")],
        ),
        (
            "P1000000000D",
            "PT6H",
            [],
            [],
            [("tightened", Severity.ADVISORY, "max_age P1000000000D -> PT6H")],
        ),
        (
            "P1000000000000000000D",
            "P1000000000000000001D",
            [],
            [],
            [
                (
                    "relaxed",
                    Severity.REVIEW,
                    "max_age P1000000000000000000D -> P1000000000000000001D",
                )
            ],
        ),
        (
            "P1000000000000000001D",
            "P1000000000000000000D",
            [],
            [],
            [
                (
                    "tightened",
                    Severity.ADVISORY,
                    "max_age P1000000000000000001D -> P1000000000000000000D",
                )
            ],
        ),
        (
            "P7000000000000000000D",
            "P1000000000000000000W",
            [],
            [],
            [
                (
                    "restated",
                    Severity.ADVISORY,
                    "max_age P7000000000000000000D -> P1000000000000000000W, the same length",
                )
            ],
        ),
    ],
    ids=[
        "longer",
        "shorter",
        "many-digits-restated",
        "many-digits-relaxed",
        "restated",
        "unordered",
        "dropped",
        "added",
        "mixed",
        "sorted-moves",
        "three-directions",
        "unchanged",
        "very-long-relaxed",
        "very-long-tightened",
        "exact-relaxed",
        "exact-tightened",
        "very-long-restated",
    ],
)
def test_freshness_diff(before, after, dropped, added, expected):
    base, candidate = snapshot(before, dropped), snapshot(after, added)
    changes = diff_snapshots(base, candidate).changes
    assert [
        (c.code, c.kind, c.severity, c.subject, c.impact.metrics, c.impact.fields, c.detail)
        for c in changes
    ] == [
        (
            f"metric.freshness_{direction}",
            ChangeKind.MODIFIED,
            severity,
            METRIC,
            [METRIC],
            [],
            f"freshness {direction}: {detail}",
        )
        for direction, severity, detail in expected
    ]
    if expected:
        assert base.canonical_digest != candidate.canonical_digest
    else:
        assert base.canonical_digest == candidate.canonical_digest


@pytest.mark.parametrize(
    "max_age",
    ["P7D", "PT6H", "P1000000000D", LONG_DAYS],
    ids=["relaxed", "valid-control", "very-long-relaxed", "many-digits-relaxed"],
)
def test_gate_names_freshness_and_preserves_valid_control(max_age):
    loaded = load_contracts([FIXTURES])
    report = run_gate(
        GateInputs(
            product=next(iter(loaded.products.values())),
            validation=loaded.report,
            base=snapshot(),
            candidate=snapshot(max_age),
            candidate_model=model(),
            dashboards={REF: spec()},
        ),
        store=MemoryStore(),
        created_at="2026-10-02T00:00:00Z",
    )
    bi = next(c for c in report.checks if c.check_id == "bi.contract")
    assert bi.status.value == "pass"
    assert bi.summary_code == "bi.compiled"
    check = next(c for c in report.checks if c.check_id == "semantic.diff")
    if max_age != "PT6H":
        assert check.status.value == "review"
        assert check.summary_code == "semantic.changed"
        assert report.verdict.value not in {"pass", "pass_with_advisory"}
    else:
        assert check.status.value == "pass"
        assert check.summary_code == "semantic.unchanged"
        assert report.verdict.value in {"pass", "pass_with_advisory"}


@pytest.mark.parametrize(
    "duration,expected",
    [
        ("PT6H", 21600),
        ("PT360M", 21600),
        ("P13W", 7862400),
        ("P1DT1S", 86401),
        ("PT1M", 60),
        ("P1000000000D", 86400000000000),
        ("P1M", None),
        ("P1Y", None),
        ("P1Y2DT3H", None),
    ],
)
def test_duration_seconds_is_exact_and_preserves_valid_windows(duration, expected):
    result = duration_seconds(duration)
    assert result == expected
    if expected is not None:
        assert type(result) is int


def test_very_long_window_accepts_a_fresh_readable_mart():
    questions = questions_for(product(), snapshot())
    assert questions.tables
    assert (
        check_freshness(
            questions,
            facts(fact(max_event_time="2026-08-25T06:30:00Z")),
            max_age="P1000000000D",
            created_at=CREATED_AT,
        )
        == []
    )


def test_very_long_window_accepts_a_capture_one_hour_before_run():
    assert grade(attestation(captured_at="2026-08-25T08:00:00Z"), max_age="P1000000000D") == []


@pytest.mark.parametrize(
    "duration,expected",
    [
        (LONG_SIX_HOURS, 21600),
        (LONG_DAYS, (10**4301 - 1) * 86400),
        ("P1" + "0" * 5000 + "WT" + "0" * 5000 + "1S", 10**5000 * 604800 + 1),
    ],
    ids=["leading-zeros", "large-days", "multiple-components"],
)
def test_duration_seconds_many_digits(duration, expected):
    result = duration_seconds(duration)
    assert result == expected
    assert type(result) is int


def test_duration_seconds_with_minimum_integer_string_limit():
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        assert duration_seconds("PT" + "0" * 700 + "6H") == 21600
        result = duration_seconds("P" + "9" * 700 + "D")
        assert result == (10**700 - 1) * 86400
        assert type(result) is int
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize("digits", [599, 600, 601, 1200, 1201])
def test_duration_seconds_digit_block_edges(digits):
    for duration, expected in [
        ("P" + "9" * digits + "D", (10**digits - 1) * 86400),
        ("P1" + "0" * (digits - 1) + "D", 10 ** (digits - 1) * 86400),
    ]:
        result = duration_seconds(duration)
        assert result == expected
        assert type(result) is int


@pytest.mark.parametrize(
    "event_time,expected_codes",
    [("2026-08-25T02:00:00Z", ["freshness.stale"]), ("2026-08-25T06:30:00Z", [])],
    ids=["stale", "fresh"],
)
def test_many_digit_window_preserves_mart_freshness(event_time, expected_codes):
    questions = questions_for(product(), snapshot())
    observed = facts(fact(max_event_time=event_time))
    ordinary = check_freshness(questions, observed, max_age="PT6H", created_at=CREATED_AT)
    long = check_freshness(questions, observed, max_age=LONG_SIX_HOURS, created_at=CREATED_AT)
    assert [f.code for f in ordinary] == expected_codes
    assert [(f.code, f.severity) for f in long] == [(f.code, f.severity) for f in ordinary]
    if not expected_codes:
        assert check_freshness(questions, observed, max_age=LONG_DAYS, created_at=CREATED_AT) == []


@pytest.mark.parametrize(
    "captured_at,expected",
    [
        ("2026-08-25T02:00:00Z", [("provenance.stale_capture", Severity.REVIEW)]),
        ("2026-08-25T08:00:00Z", []),
    ],
    ids=["stale", "fresh"],
)
def test_many_digit_window_preserves_capture_freshness(captured_at, expected):
    captured = attestation(captured_at=captured_at)
    assert grade(captured, max_age="PT6H") == expected
    assert grade(captured, max_age=LONG_SIX_HOURS) == expected
