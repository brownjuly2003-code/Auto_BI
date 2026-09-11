"""Filesystem use case for capturing one reviewed verified-asset result."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from auto_bi.decision.use_cases import CaptureRequest, VerifyError, capture_result
from auto_bi.verification.release_gate import RecordedResult
from auto_bi.verification.result_probe import QueryColumn, QueryResult
from tests.test_decision_verify import (
    ASSET_ID,
    CREATED_AT,
    ENVIRONMENT_REF,
    REFERENCE_SQL,
    Case,
    _write_yaml,
    verified_asset,
)


def _query_result() -> QueryResult:
    return QueryResult(
        columns=(
            QueryColumn("region", "String", nullable=False),
            QueryColumn("gross_margin_pct", "Decimal(18, 6)", nullable=False),
        ),
        rows=(("Москва", Decimal("0.412000")),),
    )


def _request(
    case: Case,
    output: Path,
    query_factory: Any,
    **overrides: Any,
) -> CaptureRequest:
    values = {
        "assets": case.assets,
        "candidate": case.candidate,
        "output": output,
        "engine": "clickhouse",
        "environment_ref": ENVIRONMENT_REF,
        "executed_by": "auto_bi_ro",
        "captured_at": CREATED_AT,
        "query_factory": query_factory,
        "asset_id": ASSET_ID,
        **overrides,
    }
    return CaptureRequest(**values)


def test_capture_writes_a_stable_gate_ready_result_for_the_selected_asset(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path).with_candidate().with_asset()
    output = tmp_path / "captures" / "results.yaml"
    executed: list[str] = []
    factory_calls = 0

    def query_factory() -> Any:
        nonlocal factory_calls
        factory_calls += 1

        def run(sql: str) -> QueryResult:
            executed.append(sql)
            return _query_result()

        return run

    report = capture_result(_request(case, output, query_factory))

    assert factory_calls == 1
    assert executed == [REFERENCE_SQL]
    assert report.asset_id == ASSET_ID
    assert report.output == output
    assert report.row_count == 1
    raw = output.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r" not in raw
    assert raw.startswith(f"{ASSET_ID}:\n  result:\n".encode())
    assert "Москва".encode() in raw

    payload = yaml.safe_load(raw.decode("utf-8"))
    recorded = RecordedResult.model_validate(payload[ASSET_ID])
    assert recorded.result.rows == [["Москва", "0.412000"]]
    assert recorded.attestation is not None
    assert recorded.attestation.semantic_digest == case.candidate_digest
    assert recorded.attestation.environment_ref == ENVIRONMENT_REF
    assert recorded.attestation.executed_by == "auto_bi_ro"


def test_capture_refuses_to_overwrite_before_opening_a_dwh_connection(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate().with_asset()
    output = tmp_path / "results.yaml"
    output.write_text("owned-by-user\n", encoding="utf-8")
    factory_called = False

    def query_factory() -> Any:
        nonlocal factory_called
        factory_called = True
        return lambda _sql: _query_result()

    with pytest.raises(VerifyError, match=r"already exists.*refusing to overwrite"):
        capture_result(_request(case, output, query_factory))

    assert not factory_called
    assert output.read_text(encoding="utf-8") == "owned-by-user\n"


def test_capture_requires_an_asset_choice_when_the_repository_is_ambiguous(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path).with_candidate().with_asset()
    second = verified_asset(case.base_digest)
    second["metadata"]["id"] = "asset://sales/weekly_margin_by_category"
    _write_yaml(case.assets / "weekly_margin_by_category.yaml", second)
    factory_called = False

    def query_factory() -> Any:
        nonlocal factory_called
        factory_called = True
        return lambda _sql: _query_result()

    with pytest.raises(VerifyError, match=r"holds 2 verified assets.*--asset"):
        capture_result(
            _request(
                case,
                tmp_path / "results.yaml",
                query_factory,
                asset_id="",
            )
        )

    assert not factory_called


def test_capture_rejects_a_driver_from_another_engine_family_before_connecting(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path).with_candidate().with_asset()
    factory_called = False

    def query_factory() -> Any:
        nonlocal factory_called
        factory_called = True
        return lambda _sql: _query_result()

    with pytest.raises(VerifyError, match=r"uses clickhouse.*requested postgres"):
        capture_result(
            _request(
                case,
                tmp_path / "results.yaml",
                query_factory,
                engine="postgres",
            )
        )

    assert not factory_called
