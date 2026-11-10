"""Run exact step-6 reference SELECTs under engine-enforced cost ceilings.

This is deliberately a different port from the catalog/plan ``RunQuery`` seam. Runtime
queries may return an arbitrarily large result, so ClickHouse discards it with ``FORMAT
Null`` and PostgreSQL streams rows one at a time. Only SELECT is accepted, every declared
limit is set on the engine before execution, and the caller receives typed facts rather
than a verdict. The pure release gate remains in :mod:`environment`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from math import ceil
from time import perf_counter_ns
from typing import TYPE_CHECKING, Any

from auto_bi.agent.sql_guard import guard_sql
from auto_bi.postgres import (
    check_postgres_rollback,
    check_postgres_session,
    connect_postgres,
    prepare_postgres_readonly,
)
from auto_bi.verification.environment import CostBound, ExecutionFacts, PlanQuestion
from auto_bi.verification.query_limits import (
    CLICKHOUSE_QUERY_REFUSED,
    POSTGRES_QUERY_REFUSED,
    clickhouse_error_code,
    clickhouse_named_code,
    clickhouse_query_settings,
    postgres_named_state,
    prepare_clickhouse_readonly,
    read_clickhouse_session,
    reviewed_clickhouse_settings,
)

if TYPE_CHECKING:
    from auto_bi.config import Settings


RuntimeCostProbe = Callable[[PlanQuestion], ExecutionFacts]

# An undeclared or very large timeout must not leave the release gate attached forever.
# When this stricter safety cap fires it is explicitly *not* reported as a contract breach.
SAFETY_TIMEOUT_MS = 60_000

_CH_BYTES_EXCEEDED = re.compile(
    r"\b(?:max_bytes_to_read|limit for (?:rows or )?bytes to read(?: or process)?)"
    r"\b[^.\n]*\bexceeded\b",
    re.IGNORECASE,
)


def make_clickhouse_runtime_probe(settings: Settings) -> RuntimeCostProbe:
    """Build a lazy ClickHouse exact-query cost probe."""
    import clickhouse_connect
    from clickhouse_connect.driver.exceptions import ClickHouseError

    client: Any | None = None

    def run(question: PlanQuestion) -> ExecutionFacts:
        nonlocal client
        if client is None:
            client = clickhouse_connect.get_client(
                host=settings.ch_host,
                port=settings.ch_port,
                username=settings.ch_user,
                password=settings.ch_password,
                database=settings.ch_database,
            )
        return _clickhouse_execution(client, question, error_type=ClickHouseError)

    return run


def make_postgres_runtime_probe(settings: Settings) -> RuntimeCostProbe:
    """Build a lazy PostgreSQL-family exact-query timeout probe."""
    from psycopg.errors import QueryCanceled

    connection: Any | None = None

    def run(question: PlanQuestion) -> ExecutionFacts:
        nonlocal connection
        # PostgreSQL exposes no exact scanned-byte counter. Do not open a second connection,
        # much less run a query, when bytes are the only declared runtime bound.
        if question.budgets.timeout_ms is None:
            return _postgres_execution(None, question, cancelled_error=QueryCanceled)
        if connection is None:
            connection = connect_postgres(
                host=settings.gp_host,
                port=settings.gp_port,
                dbname=settings.gp_database,
                user=settings.gp_user,
                password=settings.gp_password or None,
                autocommit=False,
            )
        return _postgres_execution(connection, question, cancelled_error=QueryCanceled)

    return run


def _clickhouse_execution(
    client: Any, question: PlanQuestion, *, error_type: type[Exception]
) -> ExecutionFacts:
    """Execute one SELECT with a server-side null output sink and final query summary."""
    from clickhouse_connect.driver.exceptions import ProgrammingError, StreamFailureError

    guard_sql(question.sql, dialect="clickhouse")
    forbidden = reviewed_clickhouse_settings(question.sql, runtime=True)
    if forbidden:
        return ExecutionFacts(
            metric_ref=question.metric_ref,
            evidence=(
                f"ClickHouse reviewed SETTINGS names protected setting {', '.join(forbidden)}; "
                "no declared runtime bound was measured"
            ),
        )
    timeout_cap_ms, timeout_is_contract = _timeout_cap(question.budgets.timeout_ms)
    query_settings = clickhouse_query_settings(timeout_cap_ms)
    byte_limit = question.budgets.max_scanned_bytes
    if byte_limit is not None:
        # ClickHouse interprets zero as unlimited, while the contract means exactly zero.
        query_settings["max_bytes_to_read"] = max(1, byte_limit)
        query_settings["read_overflow_mode"] = "throw"

    session = read_clickhouse_session(client)
    if session.readonly == "1":
        return ExecutionFacts(
            metric_ref=question.metric_ref,
            evidence=(
                f"ClickHouse session {session.describe()} "
                "cannot accept runtime per-query settings; "
                "no declared runtime bound was measured"
            ),
        )
    prepare_clickhouse_readonly(client, session)
    try:
        # query() appends FORMAT Native itself; command() preserves the explicit Null sink.
        result = client.command(_null_output(question.sql), settings=query_settings)
    except (error_type, StreamFailureError) as exc:
        message = str(exc)
        if isinstance(exc, ProgrammingError):
            for setting in query_settings:
                if message == f"Setting {setting} is unknown or readonly":
                    return ExecutionFacts(
                        metric_ref=question.metric_ref,
                        evidence=(
                            f"ClickHouse runtime setting {setting} is unknown or readonly; "
                            "no declared runtime bound was measured"
                        ),
                    )
            raise
        # Newer drivers carry the server code separately. Older ones include a Code line.
        # A setting name alone does not establish that a query ran or exceeded any limit.
        code = clickhouse_error_code(exc)
        # `43`/`47` are not caught here: a query its own plan accepted that the engine then
        # cannot run is the gate's defect, not a fact (environment_probe.py does not send the
        # exact SELECT of a plan the mart's drift no longer fits).
        refused = clickhouse_named_code(exc, CLICKHOUSE_QUERY_REFUSED)
        if refused is not None:  # environment_probe.py: a refusal is an answer
            return ExecutionFacts(
                metric_ref=question.metric_ref,
                evidence=(
                    f"ClickHouse refused the exact SELECT with {refused}; no declared runtime "
                    "bound was measured"
                ),
            )
        # TOO_MANY_BYTES must concern reading, not e.g. an environment result-size limit.
        if code == 307 and _CH_BYTES_EXCEEDED.search(message):
            if byte_limit is not None:
                return _exceeded(
                    question,
                    CostBound.SCANNED_BYTES,
                    "ClickHouse",
                    engine_limit=max(1, byte_limit),
                )
            return ExecutionFacts(
                metric_ref=question.metric_ref,
                evidence=(
                    "ClickHouse stopped the exact SELECT at an environment byte limit; "
                    "no declared runtime bound was measured"
                ),
            )
        if code == 159:
            if timeout_is_contract:
                return _exceeded(
                    question,
                    CostBound.TIMEOUT,
                    "ClickHouse",
                    engine_limit=timeout_cap_ms,
                )
            return _safety_timeout(question, "ClickHouse")
        raise

    summary = getattr(result, "summary", {}) or {}
    scanned_bytes = _summary_int(summary, "read_bytes")
    elapsed_ns = _summary_int(summary, "elapsed_ns")
    elapsed_ms = None if elapsed_ns is None else ceil(elapsed_ns / 1_000_000)
    query_id = summary.get("query_id") or getattr(result, "query_id", None)
    readings = [
        "exact ClickHouse SELECT completed with FORMAT Null",
        (f"read_bytes={scanned_bytes}" if scanned_bytes is not None else "read_bytes not reported"),
        f"elapsed_ms={elapsed_ms}" if elapsed_ms is not None else "elapsed_ns not reported",
    ]
    if query_id:
        readings.append(f"query_id={query_id}")
    return ExecutionFacts(
        metric_ref=question.metric_ref,
        scanned_bytes=scanned_bytes,
        elapsed_ms=elapsed_ms,
        checked_bounds=_completed_bounds(question, scanned_bytes, elapsed_ms),
        evidence=", ".join(readings),
    )


def _postgres_execution(
    connection: Any, question: PlanQuestion, *, cancelled_error: type[Exception]
) -> ExecutionFacts:
    """Stream one exact SELECT under a transaction-local PostgreSQL statement timeout."""
    timeout = question.budgets.timeout_ms
    if timeout is None:
        return ExecutionFacts(
            metric_ref=question.metric_ref,
            evidence=(
                "PostgreSQL does not report scanned bytes for an exact SELECT; no query was "
                "run because max_scanned_bytes was the only runtime bound"
            ),
        )

    guard_sql(question.sql, dialect="postgres")
    timeout_cap_ms, timeout_is_contract = _timeout_cap(timeout)
    started = perf_counter_ns()
    check_postgres_session(connection)
    outcome = None
    with connection.transaction(force_rollback=True), connection.cursor() as cursor:
        prepare_postgres_readonly(connection, cursor, timeout_cap_ms)
        check_postgres_session(connection)
        try:
            for _row in cursor.stream(question.sql, size=1):
                pass
        except Exception as exc:  # re-raised below unless a refusal or a timeout
            # `42703`/`42883` are not caught here either: environment_probe.py does not send
            # the exact SELECT of a plan the mart's drift no longer fits (see ClickHouse above).
            refused = postgres_named_state(exc, POSTGRES_QUERY_REFUSED)
            if refused is not None:  # environment_probe.py: a refusal is an answer
                outcome = ExecutionFacts(
                    metric_ref=question.metric_ref,
                    evidence=(
                        f"PostgreSQL refused the exact SELECT with {refused}; no declared "
                        "runtime bound was measured"
                    ),
                )
            elif (
                not isinstance(exc, cancelled_error) or "statement timeout" not in str(exc).lower()
            ):
                raise
            elif timeout_is_contract:
                outcome = _exceeded(
                    question,
                    CostBound.TIMEOUT,
                    "PostgreSQL",
                    engine_limit=timeout_cap_ms,
                )
            else:
                outcome = _safety_timeout(question, "PostgreSQL")
    check_postgres_rollback(connection)
    if outcome is not None:
        return outcome

    elapsed_ms = ceil((perf_counter_ns() - started) / 1_000_000)
    evidence = (
        "exact PostgreSQL SELECT streamed to completion under "
        f"statement_timeout={timeout_cap_ms} ms; client elapsed_ms={elapsed_ms}"
    )
    if question.budgets.max_scanned_bytes is not None:
        evidence += "; PostgreSQL does not report scanned bytes for the exact SELECT"
    return ExecutionFacts(
        metric_ref=question.metric_ref,
        elapsed_ms=elapsed_ms,
        checked_bounds=[CostBound.TIMEOUT],
        evidence=evidence,
    )


def _timeout_cap(declared_ms: int | None) -> tuple[int, bool]:
    """The positive engine cap and whether firing it proves the declared timeout exceeded."""
    if declared_ms is not None and declared_ms <= SAFETY_TIMEOUT_MS:
        return max(1, declared_ms), True
    return SAFETY_TIMEOUT_MS, False


def _completed_bounds(
    question: PlanQuestion, scanned_bytes: int | None, elapsed_ms: int | None
) -> list[CostBound]:
    """Bounds proved by completion, excluding zero limits approximated by a positive cap."""
    bounds: list[CostBound] = []
    byte_limit = question.budgets.max_scanned_bytes
    if byte_limit is not None and (byte_limit > 0 or scanned_bytes is not None):
        bounds.append(CostBound.SCANNED_BYTES)
    timeout = question.budgets.timeout_ms
    if timeout is not None and (timeout > 0 or elapsed_ms is not None):
        bounds.append(CostBound.TIMEOUT)
    return bounds


def _exceeded(
    question: PlanQuestion, bound: CostBound, engine: str, *, engine_limit: int
) -> ExecutionFacts:
    enforced_limit = f"{bound.value}={engine_limit}"
    return ExecutionFacts(
        metric_ref=question.metric_ref,
        checked_bounds=[bound],
        exceeded_bound=bound,
        evidence=f"{engine} stopped the exact SELECT while {enforced_limit} was enforced",
    )


def _safety_timeout(question: PlanQuestion, engine: str) -> ExecutionFacts:
    return ExecutionFacts(
        metric_ref=question.metric_ref,
        evidence=(
            f"{engine} hit the {SAFETY_TIMEOUT_MS} ms safety timeout before the declared "
            f"timeout_ms={question.budgets.timeout_ms}; no contract bound was measured"
        ),
    )


def _null_output(sql: str) -> str:
    statement = sql.strip()
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()
    return f"{statement}\nFORMAT Null"


def _summary_int(summary: dict[str, Any], key: str) -> int | None:
    value = summary.get(key)
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
