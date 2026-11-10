"""Engine -> sqlglot dialect mapping (Phase 3 seam)."""

from auto_bi.engine import is_known_engine, sqlglot_dialect


def test_clickhouse_is_default_and_explicit() -> None:
    assert sqlglot_dialect("clickhouse") == "clickhouse"
    assert sqlglot_dialect(None) == "clickhouse"  # unknown/missing -> v1 engine
    assert sqlglot_dialect("something-else") == "clickhouse"


def test_greenplum_family_maps_to_postgres() -> None:
    assert sqlglot_dialect("greenplum") == "postgres"
    assert sqlglot_dialect("greengage") == "postgres"
    assert sqlglot_dialect("Greengage") == "postgres"  # case-insensitive
    assert sqlglot_dialect("postgres") == "postgres"


def test_a_fallback_dialect_is_not_an_answer_about_the_engine() -> None:
    """`sqlglot_dialect` answers "render for which dialect" and falls back to the v1 engine;
    a caller emitting engine-specific syntax needs to know that was a fallback."""
    assert is_known_engine("clickhouse")
    assert is_known_engine("Greengage")  # case-insensitive, like the dialect lookup
    assert not is_known_engine("duckdb")
    assert not is_known_engine(None)
    assert not is_known_engine("")
