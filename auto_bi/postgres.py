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
