"""A refused runtime setting cannot prove that the exact SELECT exceeded a ceiling."""

from pathlib import Path

import clickhouse_connect
import pytest
from clickhouse_connect.driver.exceptions import ClickHouseError, DatabaseError, ProgrammingError

from auto_bi import cli
from auto_bi.config import Settings
from auto_bi.verification.environment import CostBound
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION, CheckStatus
from auto_bi.verification.runtime_probe import _clickhouse_execution
from tests.test_decision_verify import Case, _check_payload, budgeted_contract, status_of
from tests.test_environment_probe import clickhouse_engine
from tests.test_runtime_probe import FakeClickHouse, question
from tests.test_unanswered_engine import _answered_by

SETTINGS = (
    "max_execution_time",
    "timeout_overflow_mode",
    "max_bytes_to_read",
    "read_overflow_mode",
)


@pytest.mark.parametrize("setting", SETTINGS)
@pytest.mark.parametrize("timeout", [0, 2500, 120_000, None])
def test_a_required_setting_refusal_leaves_every_runtime_bound_unmeasured(
    setting: str, timeout: int | None
) -> None:
    budgets = {"max_scanned_bytes": 50_000}
    if timeout is not None:
        budgets["timeout_ms"] = timeout
    client = FakeClickHouse(error=ProgrammingError(f"Setting {setting} is unknown or readonly"))

    fact = _clickhouse_execution(client, question(**budgets), error_type=ClickHouseError)

    assert fact.checked_bounds == []
    assert fact.exceeded_bound is None
    assert fact.scanned_bytes is None
    assert fact.elapsed_ms is None
    assert setting in fact.evidence
    assert "unknown or readonly" in fact.evidence
    assert "safety timeout" not in fact.evidence
    assert client.command_calls == 1
    assert client.query_calls == 0
    assert set(client.calls[0][1]) == set(SETTINGS)


@pytest.mark.parametrize(
    "error",
    [
        ProgrammingError("Setting max_threads is unknown or readonly"),
        ProgrammingError("Setting max_bytes_to_read is unknown or readonly"),
        DatabaseError("Setting max_execution_time is unknown or readonly"),
        ProgrammingError("invalid max_execution_time value"),
        ProgrammingError("invalid max_bytes_to_read value"),
        DatabaseError("maximum execution time is not available"),
        DatabaseError("limit for bytes to read is not available"),
        DatabaseError("Code: 158. TOO_MANY_ROWS_OR_BYTES: limit for rows to read exceeded"),
        DatabaseError("Code: 62. DB::Exception: syntax error near 'TIMEOUT_EXCEEDED'"),
        DatabaseError("max_execution_time exceeded", code=62),
        DatabaseError("Code: 159. TIMEOUT_EXCEEDED", code=62),
        DatabaseError("Code: 307. TOO_MANY_BYTES: max_result_bytes exceeded", code=307),
        DatabaseError("Code: 158. TOO_MANY_ROWS: max_bytes_to_read exceeded", code=158),
        RuntimeError("Setting max_execution_time is unknown or readonly"),
    ],
)
def test_an_unrelated_failure_does_not_become_cost_evidence(error: Exception) -> None:
    client = FakeClickHouse(error=error)

    with pytest.raises(type(error)) as raised:
        _clickhouse_execution(client, question(timeout_ms=2500), error_type=ClickHouseError)

    assert raised.value is error
    assert client.command_calls == 1


@pytest.mark.parametrize("with_code", [False, True])
@pytest.mark.parametrize(
    ("code", "detail", "bound"),
    [
        (
            307,
            "Limit for rows or bytes to read exceeded, max bytes: 1.00 B, "
            "current bytes: 511.01 KiB: While executing NumbersRange. (TOO_MANY_BYTES)",
            CostBound.SCANNED_BYTES,
        ),
        (
            159,
            "Timeout exceeded: elapsed 0.139029 seconds, maximum: 0.001. (TIMEOUT_EXCEEDED)",
            CostBound.TIMEOUT,
        ),
    ],
)
def test_live_clickhouse_limit_responses_keep_their_exact_bound(
    with_code: bool, code: int, detail: str, bound: CostBound
) -> None:
    message = (
        f"Received ClickHouse exception, code: {code}, server response: Code: {code}. "
        f"DB::Exception: {detail} (version 24.8.14.39 (official build))"
    )
    error = DatabaseError(message, code=code if with_code else None)
    client = FakeClickHouse(error=error)

    fact = _clickhouse_execution(
        client, question(timeout_ms=1, max_scanned_bytes=1), error_type=ClickHouseError
    )

    assert fact.checked_bounds == [bound]
    assert fact.exceeded_bound is bound
    assert client.command_calls == 1


@pytest.mark.parametrize("setting", SETTINGS)
def test_collected_refusal_is_sealed_as_unmeasured_without_losing_catalog_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    case = Case(tmp_path).with_candidate(
        contract=budgeted_contract(max_scanned_bytes=50_000, timeout_ms=2500)
    )
    client = FakeClickHouse(error=ProgrammingError(f"Setting {setting} is unknown or readonly"))
    catalog = clickhouse_engine()
    _answered_by(monkeypatch, "clickhouse", catalog)
    monkeypatch.setattr(cli, "_decision_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(clickhouse_connect, "get_client", lambda **kwargs: client)

    report = case.run(probe=cli._environment_probe("clickhouse"))
    payload = _check_payload(tmp_path, report, "dwh.cost")

    assert status_of(report, "dwh.cost") is CheckStatus.REVIEW
    assert [item["code"] for item in payload["findings"]] == ["cost.not_measured"] * 2
    assert payload["facts"]["role"]["read_only"] is True
    assert payload["facts"]["tables"][0]["exists"] is True
    assert payload["facts"]["tables"][0]["readable"] is True
    assert payload["facts"]["executions"][0]["checked_bounds"] == []
    assert payload["facts"]["executions"][0]["exceeded_bound"] is None
    assert client.command_calls == 1
    assert catalog.asked


def test_cost_evidence_has_a_new_rule_and_bundle_identity() -> None:
    assert int(VERIFIER_VERSION) >= 98
    assert int(CHECK_VERSION) >= 73
