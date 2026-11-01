"""ClickHouse settings shared by capture, catalog and runtime questions."""

import re
from dataclasses import dataclass
from math import isfinite
from typing import Any

from auto_bi.agent.sql_guard import clickhouse_setting_names

CLICKHOUSE_TIME_SETTINGS = frozenset(
    {
        "max_execution_time",
        "timeout_overflow_mode",
        "max_execution_time_leaf",
        "timeout_overflow_mode_leaf",
    }
)
CLICKHOUSE_OVERFLOW_SETTINGS = (
    "distinct_overflow_mode",
    "group_by_overflow_mode",
    "join_overflow_mode",
    "read_overflow_mode",
    "read_overflow_mode_leaf",
    "result_overflow_mode",
    "set_overflow_mode",
    "sort_overflow_mode",
    "timeout_overflow_mode",
    "timeout_overflow_mode_leaf",
    "transfer_overflow_mode",
)
CLICKHOUSE_CAPTURE_SETTINGS = (
    CLICKHOUSE_TIME_SETTINGS | set(CLICKHOUSE_OVERFLOW_SETTINGS) | {"profile"}
)
CLICKHOUSE_RUNTIME_SETTINGS = CLICKHOUSE_CAPTURE_SETTINGS | {
    "max_bytes_to_read",
    "read_overflow_mode",
    "max_bytes_to_read_leaf",
    "read_overflow_mode_leaf",
}


def reviewed_clickhouse_settings(sql: str, *, runtime: bool = False) -> tuple[str, ...]:
    names = CLICKHOUSE_RUNTIME_SETTINGS if runtime else CLICKHOUSE_CAPTURE_SETTINGS
    return tuple(sorted(clickhouse_setting_names(sql) & names))


@dataclass(frozen=True)
class ClickHouseSession:
    readonly: str
    max_execution_time: float
    timeout_overflow_mode: str
    overflow_modes: tuple[tuple[str, str], ...]

    def describe(self) -> str:
        return (
            f"readonly={self.readonly}, max_execution_time={self.max_execution_time:g} s, "
            f"timeout_overflow_mode={self.timeout_overflow_mode}"
        )


def read_clickhouse_session(client: Any) -> ClickHouseSession:
    from clickhouse_connect.driver.exceptions import OperationalError

    names = (
        "readonly",
        "max_execution_time",
        "timeout_overflow_mode",
        *(name for name in CLICKHOUSE_OVERFLOW_SETTINGS if name != "timeout_overflow_mode"),
    )
    values = client.command("SELECT " + ", ".join(f"getSetting('{name}')" for name in names))
    try:
        if isinstance(values, (str, bytes)) or len(values) != len(names):
            raise ValueError
        mode, seconds, overflow, *other = values
        session = ClickHouseSession(
            str(mode),
            float(seconds),
            str(overflow),
            tuple(zip(names[3:], map(str, other), strict=True)),
        )
    except (TypeError, ValueError):
        raise OperationalError(
            f"ClickHouse session settings answer is invalid: {values!r}"
        ) from None
    if session.readonly not in {"0", "1", "2"}:
        raise OperationalError(f"ClickHouse session readonly answer is invalid: {mode!r}")
    return session


def clickhouse_session_limits(
    session: ClickHouseSession, timeout_ms: int, *, limit_name: str, client: Any
) -> dict[str, Any]:
    from clickhouse_connect.driver.exceptions import OperationalError

    limits = clickhouse_query_settings(timeout_ms)
    if session.readonly == "1":
        failures = []
        if not (
            isfinite(session.max_execution_time)
            and 0 < session.max_execution_time <= timeout_ms / 1000
            and session.timeout_overflow_mode == "throw"
        ):
            failures.append(
                f"ClickHouse {limit_name}={timeout_ms} ms requires a positive session "
                f"max_execution_time <= {timeout_ms / 1000:g} s and timeout_overflow_mode=throw; "
                f"session {session.describe()}"
            )
        failures.extend(
            f"{name}={value}" for name, value in session.overflow_modes if value != "throw"
        )
        rows = client.query(
            "SELECT name, value, changed FROM system.settings WHERE name IN ('limit', 'offset')"
        ).result_rows
        if not isinstance(rows, (tuple, list)):
            raise OperationalError(f"ClickHouse limit/offset answer is invalid: {rows!r}")
        for name in ("limit", "offset"):
            found = [
                row for row in rows if isinstance(row, (tuple, list)) and row and row[0] == name
            ]
            if len(found) != 1 or len(found[0]) != 3:
                failures.append(f"{name} answer is invalid: {found!r}")
                continue
            _, value, changed = found[0]
            if str(value) != "0" or str(changed) != "0":
                failures.append(f"{name}={value}, changed={changed}")
                if str(changed) == "1":
                    failures.append(
                        f"user's own settings set {name} (changed=1), which readonly=1 cannot lift"
                    )
        if len(rows) != 2:
            failures.append(f"limit/offset answer is invalid: {rows!r}")
        if failures:
            raise OperationalError("ClickHouse session limits refused: " + "; ".join(failures))
        return {}
    return limits


def clickhouse_timeout(timeout_ms: int) -> dict[str, Any]:
    if timeout_ms <= 0:
        raise ValueError("max_execution_time must be positive")
    return {"max_execution_time": timeout_ms / 1000, "timeout_overflow_mode": "throw"}


def clickhouse_query_settings(timeout_ms: int) -> dict[str, Any]:
    return {
        **clickhouse_timeout(timeout_ms),
        **dict.fromkeys(CLICKHOUSE_OVERFLOW_SETTINGS, "throw"),
        "limit": 0,
        "offset": 0,
    }


def prepare_clickhouse_readonly(client: Any, session: ClickHouseSession) -> None:
    """Keep mode 1, retain mode 2, or change mode 0 and confirm through the same client."""
    from clickhouse_connect.driver.exceptions import OperationalError

    if session.readonly == "1":
        return
    if session.readonly == "0":
        client.command("SET readonly = 2")
    reported = client.command("SELECT getSetting('readonly')")
    if str(reported) != "2":
        raise OperationalError(
            f"ClickHouse session requires readonly=2; server reported {reported!r}"
        )


def clickhouse_error_code(error: Exception) -> int | None:
    code: int | None = getattr(error, "code", None)
    if code is not None:
        return code
    match = re.search(r"(?:\A|\n|server response: )\s*Code:\s*(\d+)\.", str(error))
    return int(match[1]) if match is not None else None


def clickhouse_timed_out(error: Exception) -> bool:
    return clickhouse_error_code(error) == 159
