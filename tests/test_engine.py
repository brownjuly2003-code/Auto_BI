"""Engine -> sqlglot dialect mapping (Phase 3 seam)."""

from auto_bi.engine import sqlglot_dialect


def test_clickhouse_is_default_and_explicit() -> None:
    assert sqlglot_dialect("clickhouse") == "clickhouse"
    assert sqlglot_dialect(None) == "clickhouse"  # unknown/missing -> v1 engine
    assert sqlglot_dialect("something-else") == "clickhouse"


def test_greenplum_family_maps_to_postgres() -> None:
    assert sqlglot_dialect("greenplum") == "postgres"
    assert sqlglot_dialect("greengage") == "postgres"
    assert sqlglot_dialect("Greengage") == "postgres"  # case-insensitive
    assert sqlglot_dialect("postgres") == "postgres"
