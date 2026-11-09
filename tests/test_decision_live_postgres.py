"""`decision capture` and `decision verify --dwh postgres` against a live PostgreSQL.

The PostgreSQL half of `tests/test_decision_live_clickhouse.py`: the same product, the same
mart and the same drifts, asked of a real server. It needs a PostgreSQL of its own: an admin
connection given by `AUTO_BI_LIVE_PG_HOST`, `AUTO_BI_LIVE_PG_PORT`, `AUTO_BI_LIVE_PG_ADMIN_USER`
and `AUTO_BI_LIVE_PG_ADMIN_PASSWORD`. For each test it creates database `decision_live`, its
schema `dm` with the two tables of `scripts/decision_stand_fixture.py` and a read-only gate
role, and drops them at the end. A server that already has that database or one of those
roles is refused, not reused.

Without `AUTO_BI_LIVE_PG_HOST` the file is skipped. The `integration` job of
`.github/workflows/ci.yml` sets it and starts the server from `postgres:16`, the major version
whose answers docs/specs/unanswered-engine.md records.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "scripts" / "decision_stand_fixture.py"
PRODUCT_ID = "decision://sales/weekly_margin"
DATABASE = "decision_live"
GATE_USER = "decision_live_gate_ro"
WRITER_USER = "decision_live_gate_rw"
#: The major version the recorded answers (`42703`, `42883`, `42501`) were observed on.
MAJOR = 16

# As in the ClickHouse file: the mart is dated by day, the newest day DAY, read as its 00:00
# in Europe/Moscow; FRESH_AT is inside the product's PT6H window, STALE_AT 27 hours after it.
DAY = "2026-09-12"
FRESH_AT = "2026-09-12T00:00:00Z"
STALE_AT = "2026-09-13T00:00:00Z"
ROWS = 91
REVIEWED = "msk:9100000.00"
# PostgreSQL reports no scanned bytes for an exact SELECT, so the clean stand declares the two
# ceilings it can measure: rows by the plan, time by the exact SELECT.
WITHIN = ("max_scanned_rows=1000", "timeout_ms=30000")

SCHEMA = (
    "CREATE SCHEMA dm",
    "CREATE TABLE dm.stores (id integer)",
    "INSERT INTO dm.stores VALUES (1)",
    "CREATE TABLE dm.sales_daily (date date, store_id integer, region text, category text, "
    "revenue numeric(18,2), gross_margin numeric(18,2))",
    f"INSERT INTO dm.sales_daily SELECT date '{DAY}' - g, 1, 'msk', 'all', 100000, 45205.40 "
    f"FROM generate_series(0, {ROWS - 1}) g",
    # the plan's estimate is what `max_scanned_rows` is weighed against: exact once analysed
    "ANALYZE dm.sales_daily",
    f"GRANT USAGE ON SCHEMA dm TO {GATE_USER}",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA dm TO {GATE_USER}",
)

EXIT_BY_VERDICT = {"pass": 0, "pass_with_advisory": 0, "needs_review": 3, "block": 4}

#: What every check says of the clean stand; each scenario names only where it differs.
CLEAN = {
    "contracts.validate": ("pass", "contracts.clean"),
    "semantic.gaps": ("pass", "semantic.representable"),
    "semantic.diff": ("pass", "semantic.unchanged"),
    "impact.products": ("pass", "impact.none"),
    "assets.invalidated": ("pass", "assets.valid"),
    "results.reconcile": ("pass", "result.equivalent"),
    "results.provenance": ("pass", "provenance.established"),
    "sql.compile": ("pass", "sql.compiled"),
    "dwh.access": ("pass", "access.read_only"),
    "dwh.freshness": ("pass", "freshness.fresh"),
    "dwh.physical": ("pass", "physical.as_declared"),
    "dwh.security": ("pass", "security.unfiltered"),
    "dwh.cost": ("pass", "cost.within_budget"),
    "bi.contract": ("pass", "bi.compiled"),
    "evidence.bundle": ("pass", "evidence.verified"),
}


@dataclass(frozen=True)
class Server:
    host: str
    port: int
    admin_user: str
    admin_password: str = field(repr=False)  # a failing test prints its fixtures

    def connect(self, database: str) -> Any:
        import psycopg

        return psycopg.connect(
            host=self.host,
            port=self.port,
            user=self.admin_user,
            password=self.admin_password,
            dbname=database,
            autocommit=True,
        )


@dataclass(frozen=True)
class Stand:
    server: Server
    admin: Any = field(repr=False)  # psycopg connection to DATABASE, signed in as the admin
    gate_password: str = field(repr=False)

    def run(self, sql: str) -> None:
        self.admin.execute(sql)

    @property
    def target(self) -> str:
        """How the gate names the environment: host, port and database, never the user."""
        return f"{self.server.host}:{self.server.port}/{DATABASE}"


@dataclass(frozen=True)
class Run:
    verdict: str
    #: check id -> (status, summary code)
    checks: dict[str, tuple[str, str]]
    #: `dwh.*` check id -> codes of its findings, in the order the bundle holds them
    findings: dict[str, list[str]]
    #: `dwh.*` check id -> what each of those findings says, in the same order
    details: dict[str, list[str]]
    #: the facts `dwh.cost` sealed: a plan and, where a runtime bound is declared, an execution
    cost: dict[str, Any]

    def changed(self) -> dict[str, tuple[str, str]]:
        """Each check whose answer differs from the clean stand's; a check gone or new fails."""
        assert set(self.checks) == set(CLEAN), sorted(set(self.checks) ^ set(CLEAN))
        return {key: value for key, value in self.checks.items() if CLEAN[key] != value}

    def codes(self) -> dict[str, list[str]]:
        return {key: codes for key, codes in self.findings.items() if codes}


def _server() -> Server:
    host = os.environ.get("AUTO_BI_LIVE_PG_HOST", "")
    if not host:
        pytest.skip("AUTO_BI_LIVE_PG_HOST is not set: no PostgreSQL of its own to run against")
    return Server(
        host=host,
        port=int(os.environ.get("AUTO_BI_LIVE_PG_PORT", "5432")),
        admin_user=os.environ.get("AUTO_BI_LIVE_PG_ADMIN_USER", "postgres"),
        admin_password=os.environ.get("AUTO_BI_LIVE_PG_ADMIN_PASSWORD", ""),
    )


def _role(name: str, password: str) -> Any:
    from psycopg import sql

    return sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
        sql.Identifier(name), sql.Literal(password)
    )


@pytest.fixture
def stand() -> Iterator[Stand]:
    server = _server()
    root = server.connect("postgres")
    try:
        version = int(root.execute("SHOW server_version_num").fetchone()[0])
        assert version // 10000 == MAJOR, f"PostgreSQL {version}, the recorded answers are {MAJOR}"
        taken = root.execute(
            "SELECT (SELECT count(*) FROM pg_database WHERE datname = %s) + "
            "(SELECT count(*) FROM pg_roles WHERE rolname IN (%s, %s))",
            (DATABASE, GATE_USER, WRITER_USER),
        ).fetchone()[0]
        if taken:
            pytest.fail(
                f"{server.host}:{server.port} already has database {DATABASE} or role "
                f"{GATE_USER}/{WRITER_USER}: this check builds its own and does not touch "
                "someone else's"
            )
        password = secrets.token_hex(16)
        # Each object is dropped only once this run has made it, so a run that lost a race to
        # another never drops what the other made; the database goes first, as the role's
        # grants inside it would keep `DROP ROLE` from going through.
        root.execute(_role(GATE_USER, password))
        try:
            root.execute(f"CREATE DATABASE {DATABASE}")
            try:
                admin = server.connect(DATABASE)
                try:
                    for statement in SCHEMA:
                        admin.execute(statement)
                    yield Stand(server=server, admin=admin, gate_password=password)
                finally:
                    admin.close()
            finally:
                root.execute(f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)")
        finally:
            # the writer, where a test made it, is checked absent together with the gate role
            root.execute(f"DROP ROLE IF EXISTS {GATE_USER}, {WRITER_USER}")
    finally:
        root.close()


def _env(stand: Stand, user: str, password: str) -> dict[str, str]:
    """The gate's own settings only: no `AUTO_BI_*` of the caller, the admin's above all."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("AUTO_BI_")}
    env.update(
        AUTO_BI_GP_HOST=stand.server.host,
        AUTO_BI_GP_PORT=str(stand.server.port),
        AUTO_BI_GP_DATABASE=DATABASE,
        AUTO_BI_GP_SCHEMA="dm",
        AUTO_BI_GP_USER=user,
        AUTO_BI_GP_PASSWORD=password,
    )
    return env


def _cli(args: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_bi.cli", "decision", *args],
        env=env,
        cwd=cwd,  # not the checkout: its `.env` must not reach the gate's settings
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _product(
    stand: Stand, tmp_path: Path, budgets: tuple[str, ...] = WITHIN, captured_at: str = FRESH_AT
) -> Path:
    """The fixture's product on PostgreSQL types, its asset's result captured from the stand."""
    root = tmp_path / "product"
    laid = subprocess.run(
        [sys.executable, str(FIXTURE), str(root), "postgres", f"asset={REVIEWED}", *budgets],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert laid.returncode == 0, laid.stderr
    captured = _cli(
        [
            "capture",
            str(root / "assets"),
            "--candidate",
            str(root / "candidate"),
            "--dwh",
            "postgres",
            "--environment",
            "live-postgres",
            "--captured-at",
            captured_at,
            "--output",
            str(root / "results.yaml"),
        ],
        _env(stand, GATE_USER, stand.gate_password),
        tmp_path,
    )
    assert captured.returncode == 0, captured.stdout + captured.stderr
    return root


def _verify_argv(root: Path, evidence: Path, created_at: str) -> list[str]:
    return [
        "verify",
        str(root / "contracts"),
        "--base",
        str(root / "base"),
        "--candidate",
        str(root / "candidate"),
        "--dashboards",
        str(root / "dashboards"),
        "--assets",
        str(root / "assets"),
        "--results",
        str(root / "results.yaml"),
        "--product",
        PRODUCT_ID,
        "--dwh",
        "postgres",
        "--environment",
        "live-postgres",
        "--created-at",
        created_at,
        "--evidence",
        str(evidence),
        "--json",
    ]


def _verify(
    stand: Stand,
    root: Path,
    *,
    created_at: str = FRESH_AT,
    user: str = GATE_USER,
) -> Run:
    evidence = root.parent / "evidence"
    done = _cli(
        _verify_argv(root, evidence, created_at),
        _env(stand, user, stand.gate_password),
        root.parent,
    )
    assert done.returncode in set(EXIT_BY_VERDICT.values()), done.stdout + done.stderr
    report = json.loads(done.stdout)
    assert done.returncode == EXIT_BY_VERDICT[report["verdict"]], report["verdict"]
    [run_dir] = evidence.glob("run-*")
    findings: dict[str, list[str]] = {}
    details: dict[str, list[str]] = {}
    cost: dict[str, Any] = {}
    for path in sorted((run_dir / "checks").glob("check-dwh.*.json")):
        payload = json.loads(path.read_text("utf-8"))
        check_id = path.stem.removeprefix("check-")
        findings[check_id] = [finding["code"] for finding in payload["findings"]]
        details[check_id] = [finding["detail"] for finding in payload["findings"]]
        if check_id == "dwh.cost":
            cost = payload["facts"]
    return Run(
        verdict=report["verdict"],
        checks={c["check_id"]: (c["status"], c["summary_code"]) for c in report["checks"]},
        findings=findings,
        details=details,
        cost=cost,
    )


def _drift(stand: Stand, sql: str) -> None:
    """Change the mart, then analyse it: a rewritten table's estimate is a guess until then."""
    stand.run(sql)
    stand.run("ANALYZE dm.sales_daily")


def test_a_clean_stand_passes_with_every_bound_measured(stand: Stand, tmp_path: Path) -> None:
    run = _verify(stand, _product(stand, tmp_path))

    assert run.verdict == "pass"
    assert run.checks == CLEAN
    assert run.codes() == {}
    [plan] = run.cost["plans"]
    assert plan["scanned_rows"] == ROWS
    [execution] = run.cost["executions"]
    assert execution["checked_bounds"] == ["timeout_ms"]


def test_a_stale_mart_needs_review(stand: Stand, tmp_path: Path) -> None:
    run = _verify(stand, _product(stand, tmp_path, captured_at=STALE_AT), created_at=STALE_AT)

    assert run.verdict == "needs_review"
    assert run.changed() == {"dwh.freshness": ("review", "freshness.stale")}
    assert run.codes() == {"dwh.freshness": ["freshness.stale"]}


def test_a_query_over_its_rows_ceiling_needs_review(stand: Stand, tmp_path: Path) -> None:
    run = _verify(stand, _product(stand, tmp_path, budgets=("max_scanned_rows=10",)))

    assert run.verdict == "needs_review"
    assert run.changed() == {"dwh.cost": ("review", "cost.over_budget")}
    assert run.codes() == {"dwh.cost": ["cost.over_budget"]}


def test_a_role_that_can_write_blocks(stand: Stand, tmp_path: Path) -> None:
    """Nor is its exact SELECT run: the runtime ceiling stays unmeasured."""
    root = _product(stand, tmp_path)
    stand.run(_role(WRITER_USER, stand.gate_password))
    stand.run(f"GRANT USAGE ON SCHEMA dm TO {WRITER_USER}")
    stand.run(f"GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA dm TO {WRITER_USER}")

    run = _verify(stand, root, user=WRITER_USER)

    assert run.changed() == {
        "dwh.access": ("block", "access.role_writable"),
        "dwh.cost": ("review", "cost.not_measured"),
    }
    assert run.codes() == {
        "dwh.access": ["access.role_writable"],
        "dwh.cost": ["cost.not_measured"],
    }
    assert run.cost["executions"] == []
    assert run.verdict == "block"


def test_a_column_hidden_from_the_role_blocks(stand: Stand, tmp_path: Path) -> None:
    """The plan and the exact SELECT are both refused, and both refusals are facts."""
    root = _product(stand, tmp_path)
    stand.run(f"REVOKE SELECT ON dm.sales_daily FROM {GATE_USER}")
    stand.run(
        f"GRANT SELECT (date, store_id, region, category, gross_margin) ON dm.sales_daily "
        f"TO {GATE_USER}"
    )

    run = _verify(stand, root)

    assert run.changed() == {
        "dwh.security": ("block", "security.column_unreadable"),
        "dwh.cost": ("review", "cost.not_estimated"),
    }
    assert run.codes() == {
        "dwh.security": ["security.column_unreadable"],
        "dwh.cost": ["cost.not_estimated", "cost.not_measured"],
    }
    [plan] = run.cost["plans"]
    assert plan["evidence"] == "the engine refused EXPLAIN on the reference query"
    [execution] = run.cost["executions"]
    assert execution["checked_bounds"] == []
    assert execution["evidence"] == (
        "PostgreSQL refused the exact SELECT with SQLSTATE 42501 insufficient_privilege; no "
        "declared runtime bound was measured"
    )
    assert run.verdict == "block"


def _unfit(run: Run, unfit: str, drifted: str) -> None:
    """The plan has no number and names the drift; the exact SELECT was not sent at all."""
    [plan] = run.cost["plans"]
    assert plan.get("scanned_rows") is None
    assert plan["evidence"] == (
        f"EXPLAIN answered the reference query with {unfit}, taken to mean the query no "
        f"longer fits the mart as the engine has it ({drifted} drifted from the model)"
    )
    [execution] = run.cost["executions"]
    assert execution["checked_bounds"] == []
    assert execution["evidence"] == (
        f"the exact SELECT was not run: EXPLAIN of the same query answered {unfit}, and a "
        "query the mart no longer fits measures nothing; no declared runtime bound was measured"
    )


UNFIT_COST = ["cost.not_estimated", "cost.not_measured"]


@pytest.mark.parametrize(
    ("alter", "physical", "unfit", "drifted", "verdict"),
    [
        (
            # `trunc(double precision, integer)` does not exist
            "ALTER COLUMN revenue TYPE double precision",
            ("review", "physical.type_drift"),
            "SQLSTATE 42883 undefined_function",
            "revenue",
            "needs_review",
        ),
        (
            "ALTER COLUMN revenue TYPE text",
            ("review", "physical.type_drift"),
            "SQLSTATE 42883 undefined_function",
            "revenue",
            "needs_review",
        ),
        (
            "RENAME COLUMN revenue TO revenue_rub",
            ("block", "physical.column_absent"),
            "SQLSTATE 42703 undefined_column",
            "revenue",
            "block",
        ),
        (
            "DROP COLUMN gross_margin",
            ("block", "physical.column_absent"),
            "SQLSTATE 42703 undefined_column",
            "gross_margin",
            "block",
        ),
    ],
    ids=["double", "text", "renamed", "dropped"],
)
def test_a_mart_the_query_no_longer_fits_is_reported(
    stand: Stand,
    tmp_path: Path,
    alter: str,
    physical: tuple[str, str],
    unfit: str,
    drifted: str,
    verdict: str,
) -> None:
    """The drift is graded; verifier 130 ended the run on the engine's error with no bundle."""
    root = _product(stand, tmp_path)
    _drift(stand, f"ALTER TABLE dm.sales_daily {alter}")

    run = _verify(stand, root)

    assert run.changed() == {
        "dwh.physical": physical,
        "dwh.cost": ("review", "cost.not_estimated"),
    }
    assert run.codes() == {"dwh.physical": [physical[1]], "dwh.cost": UNFIT_COST}
    _unfit(run, unfit, drifted)
    assert run.verdict == verdict


def test_a_drift_the_query_still_fits_is_priced(stand: Stand, tmp_path: Path) -> None:
    """A wider numeric still takes `sum` and `trunc`: the drift is graded, the cost measured."""
    root = _product(stand, tmp_path)
    _drift(stand, "ALTER TABLE dm.sales_daily ALTER COLUMN revenue TYPE numeric(38,4)")

    run = _verify(stand, root)

    assert run.changed() == {"dwh.physical": ("review", "physical.type_drift")}
    [plan] = run.cost["plans"]
    assert plan["scanned_rows"] == ROWS
    [execution] = run.cost["executions"]
    assert execution["checked_bounds"] == ["timeout_ms"]
    assert run.verdict == "needs_review"


def _undated(run: Run, stand: Stand) -> None:
    assert run.details["dwh.freshness"] == [
        f"dm.sales_daily has {ROWS} row(s) on {stand.target} and no newest date to date them "
        "by — a NULL max, or a column the engine no longer has — the check did not run rather "
        "than passing"
    ]


def test_a_mart_without_its_event_time_column_is_reported(stand: Stand, tmp_path: Path) -> None:
    """`max(date)` of a dropped column used to end the run; its rows are counted, undated."""
    root = _product(stand, tmp_path)
    _drift(stand, "ALTER TABLE dm.sales_daily DROP COLUMN date")

    run = _verify(stand, root)

    assert run.changed() == {
        "dwh.freshness": ("review", "freshness.unknown"),
        "dwh.physical": ("block", "physical.column_absent"),
        "dwh.cost": ("review", "cost.not_estimated"),
    }
    assert run.codes() == {
        "dwh.freshness": ["freshness.unknown"],
        "dwh.physical": ["physical.column_absent"],
        "dwh.cost": UNFIT_COST,
    }
    _undated(run, stand)
    _unfit(run, "SQLSTATE 42703 undefined_column", "date")
    assert run.verdict == "block"


def test_an_event_time_column_max_does_not_take_is_reported(stand: Stand, tmp_path: Path) -> None:
    """`max(jsonb)` does not exist, while the reference query still groups by the column: the
    rows are counted and undated, and the plan is priced as before."""
    root = _product(stand, tmp_path)
    _drift(stand, "ALTER TABLE dm.sales_daily ALTER COLUMN date TYPE jsonb USING to_jsonb(date)")

    run = _verify(stand, root)

    assert run.changed() == {
        "dwh.freshness": ("review", "freshness.unknown"),
        "dwh.physical": ("review", "physical.type_drift"),
    }
    assert run.codes() == {
        "dwh.freshness": ["freshness.unknown"],
        "dwh.physical": ["physical.type_drift"],
    }
    _undated(run, stand)
    [plan] = run.cost["plans"]
    assert plan["scanned_rows"] == ROWS
    assert run.verdict == "needs_review"


#: The stand's own database only: `pg_attribute` is a catalog of each database, and this one
#: is dropped at the end of the test.
REVOKE_COLUMN_CATALOG = "REVOKE SELECT ON pg_catalog.pg_attribute FROM PUBLIC"


def test_a_column_catalog_the_role_may_not_read_marks_no_column_absent(
    stand: Stand, tmp_path: Path
) -> None:
    """ADR 0010, F1: the gate's role is refused the column list (`42501`). Verifier 132 read
    that as an empty catalog and blocked on every declared column of a mart that holds them
    all; now no column is compared, and which columns the role may read is not observed."""
    root = _product(stand, tmp_path)
    stand.run(REVOKE_COLUMN_CATALOG)

    run = _verify(stand, root)

    assert run.changed() == {
        "dwh.physical": ("pass", "physical.not_run"),
        "dwh.security": ("review", "security.not_observed"),
    }
    assert run.codes() == {"dwh.security": ["security.not_observed"]}
    [plan] = run.cost["plans"]
    assert plan["scanned_rows"] == ROWS
    assert run.verdict == "needs_review"


def test_a_column_gone_behind_a_refused_catalog_ends_the_run(stand: Stand, tmp_path: Path) -> None:
    """Nor does the refused catalog account for the `42703` of a column that is really gone:
    nothing the engine answered says which column drifted, so the state ends the run, as any
    `42703` the catalog does not account for."""
    root = _product(stand, tmp_path)
    stand.run(REVOKE_COLUMN_CATALOG)
    _drift(stand, "ALTER TABLE dm.sales_daily DROP COLUMN gross_margin")
    evidence = root.parent / "evidence"

    done = _cli(
        _verify_argv(root, evidence, FRESH_AT),
        _env(stand, GATE_USER, stand.gate_password),
        root.parent,
    )

    assert done.returncode == 2, done.stdout + done.stderr
    assert done.stdout == ""
    assert "UndefinedColumn" in done.stderr
    assert not evidence.exists()


def test_a_table_catalog_the_role_may_not_read_does_not_hide_the_mart(
    stand: Stand, tmp_path: Path
) -> None:
    """Review r1 of F1: the presence question over `pg_tables` and `pg_views` refused
    (`42501`). Verifier 132 read the refusal as "no row" and blocked `access.table_absent` on a
    mart the role reads; `to_regclass` finds it, and the run is the clean stand's."""
    root = _product(stand, tmp_path)
    stand.run("REVOKE SELECT ON pg_catalog.pg_tables, pg_catalog.pg_views FROM PUBLIC")

    run = _verify(stand, root)

    assert run.changed() == {}
    assert run.codes() == {}
    assert run.verdict == "pass"


def test_a_materialized_view_mart_is_the_mart(stand: Stand, tmp_path: Path) -> None:
    """A mart served as a materialized view is in neither `pg_tables` nor `pg_views`. Verifier
    132 asked only those two and blocked `access.table_absent`; the same rows behind a
    materialized view are the clean stand's run."""
    root = _product(stand, tmp_path)
    stand.run("ALTER TABLE dm.sales_daily RENAME TO sales_daily_rows")
    stand.run("CREATE MATERIALIZED VIEW dm.sales_daily AS SELECT * FROM dm.sales_daily_rows")
    stand.run(f"GRANT SELECT ON dm.sales_daily TO {GATE_USER}")
    stand.run("ANALYZE dm.sales_daily")

    run = _verify(stand, root)

    assert run.changed() == {}
    assert run.codes() == {}
    [plan] = run.cost["plans"]
    assert plan["scanned_rows"] == ROWS
    assert run.verdict == "pass"
