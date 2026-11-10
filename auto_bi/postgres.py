"""PostgreSQL connection settings shared by introspection and gate probes."""

from __future__ import annotations

from typing import Any


def check_postgres_session(connection: Any) -> None:
    """Require exactly UTF8/on from reported settings, without SQL; close on refusal.

    Gate callers check before each explicit execute or stream and transaction start.
    Capture also checks after reading its result, inside its transaction. Transaction
    exit has no separate check; a change already restored before a check is not detected.
    """
    import psycopg

    try:
        for setting, expected in (
            ("client_encoding", "UTF8"),
            ("standard_conforming_strings", "on"),
        ):
            reported = connection.info.parameter_status(setting)
            if reported != expected:
                raise psycopg.OperationalError(
                    f"PostgreSQL session requires {setting}={expected!r}; "
                    f"server reported {reported!r}"
                )
    except Exception:
        connection.close()
        raise


def connect_postgres(**kwargs: Any) -> Any:
    """Request UTF8 and standard strings; close and refuse an unconfirmed session."""
    import psycopg

    connection = psycopg.connect(
        client_encoding="UTF8",
        options="-c standard_conforming_strings=on",
        **kwargs,
    )
    check_postgres_session(connection)
    return connection


def set_postgres_timeout(connection: Any, cursor: Any, timeout_ms: int, *, local: bool) -> None:
    """Set a positive statement timeout after checking the string settings."""
    if timeout_ms <= 0:
        raise ValueError("statement_timeout must be positive")
    check_postgres_session(connection)
    scope = "LOCAL " if local else ""
    cursor.execute(f"SET {scope}statement_timeout = {timeout_ms}")


def prepare_postgres_readonly(connection: Any, cursor: Any, timeout_ms: int) -> None:
    """Require an active read-only transaction before sending the reviewed text."""
    import psycopg

    check_postgres_session(connection)
    cursor.execute("SET TRANSACTION READ ONLY")
    set_postgres_timeout(connection, cursor, timeout_ms, local=True)
    check_postgres_session(connection)
    cursor.execute("SHOW transaction_read_only")
    row = cursor.fetchone()
    reported = row[0] if row else None
    if reported != "on":
        connection.close()
        raise psycopg.OperationalError(
            f"PostgreSQL session requires transaction_read_only='on'; server reported {reported!r}"
        )


def check_postgres_rollback(connection: Any, *, nested: bool = False) -> None:
    """Refuse a non-idle outer transaction or failed savepoint rollback."""
    import psycopg
    from psycopg.pq import TransactionStatus

    expected = TransactionStatus.INTRANS if nested else TransactionStatus.IDLE
    reported = connection.info.transaction_status
    if reported != expected:
        connection.close()
        raise psycopg.OperationalError(
            f"PostgreSQL rollback requires transaction status {expected.name}; "
            f"server reported {reported!r}"
        )
