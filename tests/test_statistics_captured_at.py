"""The `captured_at` of a model's physical statistics is text, not a `Timestamp`.

Spec: docs/specs/utc-timestamps.md, Known boundaries. The introspectors of the v1 runtime stamp
`YYYY-MM-DDTHH:MM:SS+00:00`, which the `Timestamp` scalar refuses. No code reads the stamp as a
moment, the canonical graph does not carry it, and the bytes of `model.yaml` that hold it are
part of a run's identity like any other byte of the file (docs/specs/run-identity.md); the run
key seals the candidate model whole, the stamp with it.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, tzinfo
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from auto_bi.decision.use_cases import VerifyError
from auto_bi.introspect import clickhouse as clickhouse_introspection
from auto_bi.introspect import greenplum as greenplum_introspection
from auto_bi.introspect.clickhouse import ClickHouseIntrospector
from auto_bi.introspect.greenplum import GreenplumIntrospector
from auto_bi.scalars import Timestamp
from auto_bi.semantic.gateway import NativeModelSource
from auto_bi.semantic.model import Physical, SemanticModel
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import idempotency_key
from auto_bi.yaml_document import load_yaml_document
from tests.test_decision_verify import (
    PRODUCT_ID,
    Case,
    environment,
    probing,
    semantic_model,
    summary_of,
)
from tests.test_introspect_greenplum import fake_run
from tests.test_p1_6_governance import _ch_run_query
from tests.test_run_identity import ROWS, _inputs

#: What an introspector stamps, the `Timestamp` spelling of the same moment, and a word.
STAMPS = ["2026-10-01T12:00:00+00:00", "2026-10-01T12:00:00Z", "yesterday"]


class _Noon(datetime):
    """A clock that reads 2026-10-01 12:00:00.123456 in the zone it is asked for."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return datetime(2026, 10, 1, 12, 0, 0, 123456, tzinfo=tz)


def _physical(model: SemanticModel) -> Physical:
    physical = model.tables[0].physical
    assert physical is not None
    return physical


def _model_with(captured_at: str) -> SemanticModel:
    model = semantic_model()
    _physical(model).captured_at = captured_at
    return model


def _physical_mapping(document: object) -> dict[str, object]:
    assert isinstance(document, dict)
    physical = document["tables"][0]["physical"]
    assert isinstance(physical, dict)
    return physical


def test_both_introspectors_stamp_an_offset_the_timestamp_scalar_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clickhouse_introspection, "datetime", _Noon)
    monkeypatch.setattr(greenplum_introspection, "datetime", _Noon)

    clickhouse = ClickHouseIntrospector(_ch_run_query).introspect("dm").table("dm.returns_daily")
    greenplum = GreenplumIntrospector(fake_run, schema="dm").introspect().tables[0]
    stamps = [
        table.physical.captured_at if table.physical else None for table in (clickhouse, greenplum)
    ]

    assert stamps == ["2026-10-01T12:00:00+00:00"] * 2
    with pytest.raises(ValidationError):
        TypeAdapter(Timestamp).validate_python("2026-10-01T12:00:00+00:00")


@pytest.mark.parametrize("stamp", STAMPS)
def test_a_model_keeps_the_stamp_it_was_given_as_text(tmp_path: Path, stamp: str) -> None:
    path = tmp_path / "model.yaml"
    _model_with(stamp).dump(path)

    assert _physical(SemanticModel.load(path)).captured_at == stamp


def test_a_stamp_moves_the_run_and_no_graded_answer(tmp_path: Path) -> None:
    """Three models that differ only in the stamp: one graph, three sources, three runs."""
    snapshots, reports = [], []
    for number, stamp in enumerate(STAMPS):
        case = Case(tmp_path / str(number)).with_candidate(_model_with(stamp))
        case.with_asset().with_results(ROWS)
        source = NativeModelSource(case.candidate / "model.yaml", case.candidate / "metrics")
        snapshots.append(source.snapshot())
        reports.append(case.run(product_id=PRODUCT_ID, probe=probing(environment())))

    assert len({snapshot.canonical_digest for snapshot in snapshots}) == 1
    assert len({snapshot.source_digest for snapshot in snapshots}) == 3
    assert len({report.run_id for report in reports}) == 3
    graded = [
        [(check.check_id, check.status, check.summary_code) for check in report.checks]
        for report in reports
    ]
    assert graded[0] == graded[1] == graded[2]
    assert [report.verdict for report in reports] == [Verdict.PASS] * 3
    assert summary_of(reports[0], "semantic.diff") == "semantic.unchanged"


def test_the_run_key_seals_the_stamp_with_the_candidate_model(tmp_path: Path) -> None:
    """Both snapshots held, a candidate model that differs only in the stamp is another key:
    the key seals the model whole, not only through the bytes of its file."""
    inputs = _inputs(tmp_path)
    keys = {idempotency_key(inputs)}
    for stamp in STAMPS:
        model = inputs.candidate_model.model_copy(deep=True)
        _physical(model).captured_at = stamp
        keys.add(idempotency_key(dataclasses.replace(inputs, candidate_model=model)))

    assert _physical(inputs.candidate_model).captured_at not in STAMPS
    assert len(keys) == 1 + len(STAMPS)


def test_an_unquoted_stamp_is_a_datetime_to_yaml_and_the_model_is_refused(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path).with_candidate(_model_with("2026-10-01T12:00:00Z"))
    case.with_asset().with_results(ROWS)
    path = case.candidate / "model.yaml"
    raw = path.read_bytes()
    assert raw.count(b"captured_at: '2026-10-01T12:00:00Z'") == 1
    path.write_bytes(raw.replace(b"'2026-10-01T12:00:00Z'", b"2026-10-01T12:00:00Z"))
    read = load_yaml_document(path.read_text(encoding="utf-8"))
    assert type(_physical_mapping(read)["captured_at"]) is datetime

    with pytest.raises(VerifyError) as refused:
        case.run(product_id=PRODUCT_ID)

    assert str(refused.value) == (
        f"{path} is not a valid semantic model: "
        "tables.0.physical.captured_at: Input should be a valid string"
    )
