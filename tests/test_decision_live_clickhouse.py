"""`decision capture` and `decision verify --dwh clickhouse` against a live ClickHouse.

The rest of the suite grades step 7 of the gate (DECISIONOPS_ARCHITECTURE §8.2) on facts the
tests type in; this file asks a real server the same questions and checks what comes back. It
needs a ClickHouse of its own: an admin connection given by `AUTO_BI_LIVE_CH_HOST`,
`AUTO_BI_LIVE_CH_PORT`, `AUTO_BI_LIVE_CH_ADMIN_USER` and `AUTO_BI_LIVE_CH_ADMIN_PASSWORD`. For each
test it creates database `dm` with the two tables of `scripts/decision_stand_fixture.py` (the
shapes of `tests/test_decision_verify.py`) and a read-only gate user, and drops both at the end.
A server that already has `dm` or one of those users is refused, not reused.

Without `AUTO_BI_LIVE_CH_HOST` the file is skipped. The `integration` job of
`.github/workflows/ci.yml` sets it and starts the server from the image the compose stand pins.
"""

from __future__ import annotations

import json
import os
import re
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
COMPOSE = REPO_ROOT / "docker-compose.yml"
PRODUCT_ID = "decision://sales/weekly_margin"
GATE_USER = "decision_live_gate_ro"
WRITER_USER = "decision_live_gate_rw"

# The mart is dated by day, the newest day DAY. A day is read as its 00:00 in the metric's
# zone, Europe/Moscow (docs/specs/event-time-readings.md): FRESH_AT is three hours after that,
# inside the product's PT6H window; STALE_AT is a day later, 27 hours after it.
DAY = "2026-09-12"
FRESH_AT = "2026-09-12T00:00:00Z"
STALE_AT = "2026-09-13T00:00:00Z"
ROWS = 91
# What the verified asset was reviewed with: its reference query of the test shapes is
# `sum(revenue)` by region, 91 days of 100000.00 in `msk`.
REVIEWED = "msk:9100000.00"
# Ceilings the clean stand is inside of: 91 rows, a few kilobytes, well under a second.
WITHIN = ("max_scanned_rows=1000", "max_scanned_bytes=1000000", "timeout_ms=30000")

SCHEMA = (
    "CREATE DATABASE dm",
    "CREATE TABLE dm.stores (id UInt32) ENGINE = MergeTree ORDER BY id",
    "INSERT INTO dm.stores VALUES (1)",
    "CREATE TABLE dm.sales_daily (date Date, store_id UInt32, region String, category String, "
    "revenue Decimal(18, 2), gross_margin Decimal(18, 2)) ENGINE = MergeTree "
    "PARTITION BY toYYYYMM(date) ORDER BY (date, store_id)",
    f"INSERT INTO dm.sales_daily SELECT toDate('{DAY}') - toIntervalDay(number), toUInt32(1), "
    f"'msk', 'all', toDecimal64(100000, 2), toDecimal64(45205.40, 2) FROM numbers({ROWS})",
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


@dataclass(frozen=True)
class Stand:
    server: Server
    admin: Any = field(repr=False)  # clickhouse_connect client, signed in as the admin
    gate_password: str = field(repr=False)

    def run(self, sql: str) -> None:
        self.admin.command(sql)


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
    host = os.environ.get("AUTO_BI_LIVE_CH_HOST", "")
    if not host:
        pytest.skip("AUTO_BI_LIVE_CH_HOST is not set: no ClickHouse of its own to run against")
    return Server(
        host=host,
        port=int(os.environ.get("AUTO_BI_LIVE_CH_PORT", "8123")),
        admin_user=os.environ.get("AUTO_BI_LIVE_CH_ADMIN_USER", "default"),
        admin_password=os.environ.get("AUTO_BI_LIVE_CH_ADMIN_PASSWORD", ""),
    )


def _pinned_series() -> str:
    """`24.8` of `clickhouse/clickhouse-server:24.8`, the image the compose stand pins."""
    m = re.search(r"image:\s*clickhouse/clickhouse-server:(\S+)", COMPOSE.read_text("utf-8"))
    assert m, "docker-compose.yml no longer names the clickhouse-server image"
    return m.group(1)


@pytest.fixture
def stand() -> Iterator[Stand]:
    server = _server()
    import clickhouse_connect

    admin = clickhouse_connect.get_client(
        host=server.host,
        port=server.port,
        username=server.admin_user,
        password=server.admin_password,
    )
    try:
        pinned = _pinned_series()
        version = str(admin.command("SELECT version()"))
        assert version.startswith(f"{pinned}."), f"ClickHouse {version}, the stand pins {pinned}"
        taken = admin.command(
            "SELECT count() FROM system.databases WHERE name = 'dm'"
        ) or admin.command(
            f"SELECT count() FROM system.users WHERE name IN ('{GATE_USER}', '{WRITER_USER}')"
        )
        if taken:
            pytest.fail(
                f"{server.host}:{server.port} already has database dm or user {GATE_USER}/"
                f"{WRITER_USER}: this check builds its own and does not touch someone else's"
            )
        password = secrets.token_hex(16)
        try:
            for statement in SCHEMA:
                admin.command(statement)
            # readonly=2, not 1: the session limits a capture sets (max_execution_time) are
            # refused under readonly=1, and so is every runtime bound of `dwh.cost`.
            admin.command(
                f"CREATE USER {GATE_USER} IDENTIFIED WITH sha256_password BY '{password}' "
                "SETTINGS readonly = 2"
            )
            admin.command(f"GRANT SELECT ON dm.* TO {GATE_USER}")
            admin.command(f"GRANT SELECT ON system.* TO {GATE_USER}")
            yield Stand(server=server, admin=admin, gate_password=password)
        finally:
            admin.command("DROP DATABASE IF EXISTS dm SYNC")
            admin.command(f"DROP USER IF EXISTS {GATE_USER}, {WRITER_USER}")
    finally:
        admin.close()


def _env(stand: Stand, user: str, password: str) -> dict[str, str]:
    """The gate's own settings only: no `AUTO_BI_*` of the caller, the admin's above all."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("AUTO_BI_")}
    env.update(
        AUTO_BI_CH_HOST=stand.server.host,
        AUTO_BI_CH_PORT=str(stand.server.port),
        AUTO_BI_CH_DATABASE="dm",
        AUTO_BI_CH_USER=user,
        AUTO_BI_CH_PASSWORD=password,
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
    """The fixture's product, with its asset's result captured from the stand as it is now."""
    root = tmp_path / "product"
    laid = subprocess.run(
        [sys.executable, str(FIXTURE), str(root), "clickhouse", f"asset={REVIEWED}", *budgets],
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
            "clickhouse",
            "--environment",
            "live-clickhouse",
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


def _verify(
    stand: Stand,
    root: Path,
    *,
    created_at: str = FRESH_AT,
    user: str = GATE_USER,
    password: str = "",
) -> Run:
    evidence = root.parent / "evidence"
    done = _cli(
        [
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
            "clickhouse",
            "--environment",
            "live-clickhouse",
            "--created-at",
            created_at,
            "--evidence",
            str(evidence),
            "--json",
        ],
        _env(stand, user, password or stand.gate_password),
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


def test_a_clean_stand_passes_with_every_bound_measured(stand: Stand, tmp_path: Path) -> None:
    run = _verify(stand, _product(stand, tmp_path))

    assert run.verdict == "pass"
    assert run.checks == CLEAN
    assert run.codes() == {}
    [plan] = run.cost["plans"]
    assert plan["scanned_rows"] == ROWS
    [execution] = run.cost["executions"]
    assert execution["checked_bounds"] == ["max_scanned_bytes", "timeout_ms"]


def test_a_stale_mart_needs_review(stand: Stand, tmp_path: Path) -> None:
    run = _verify(stand, _product(stand, tmp_path, captured_at=STALE_AT), created_at=STALE_AT)

    assert run.verdict == "needs_review"
    assert run.changed() == {"dwh.freshness": ("review", "freshness.stale")}
    assert run.codes() == {"dwh.freshness": ["freshness.stale"]}


@pytest.mark.parametrize(
    "ceiling",
    ["max_scanned_rows=10", "max_scanned_bytes=1"],
    ids=["rows-by-plan", "bytes-by-execution"],
)
def test_a_query_over_its_ceiling_needs_review(stand: Stand, tmp_path: Path, ceiling: str) -> None:
    run = _verify(stand, _product(stand, tmp_path, budgets=(ceiling,)))

    assert run.verdict == "needs_review"
    assert run.changed() == {"dwh.cost": ("review", "cost.over_budget")}
    assert run.codes() == {"dwh.cost": ["cost.over_budget"]}


def test_a_role_that_can_write_blocks(stand: Stand, tmp_path: Path) -> None:
    """Nor is its exact SELECT run: a runtime ceiling stays unmeasured."""
    root = _product(stand, tmp_path)
    stand.run(
        f"CREATE USER {WRITER_USER} IDENTIFIED WITH sha256_password BY '{stand.gate_password}'"
    )
    stand.run(f"GRANT SELECT, INSERT ON dm.* TO {WRITER_USER}")
    stand.run(f"GRANT SELECT ON system.* TO {WRITER_USER}")

    run = _verify(stand, root, user=WRITER_USER)

    assert run.changed() == {
        "dwh.access": ("block", "access.role_writable"),
        "dwh.cost": ("review", "cost.not_measured"),
    }
    assert run.codes() == {
        "dwh.access": ["access.role_writable"],
        "dwh.cost": ["cost.not_measured", "cost.not_measured"],
    }
    assert run.cost["executions"] == []
    assert run.verdict == "block"


def test_a_column_hidden_from_the_role_blocks(stand: Stand, tmp_path: Path) -> None:
    """The plan and the exact SELECT are both refused, and both refusals are facts."""
    root = _product(stand, tmp_path)
    stand.run(f"REVOKE SELECT(gross_margin) ON dm.sales_daily FROM {GATE_USER}")

    run = _verify(stand, root)

    assert run.changed() == {
        "dwh.security": ("block", "security.column_unreadable"),
        "dwh.cost": ("review", "cost.not_estimated"),
    }
    assert run.codes() == {
        "dwh.security": ["security.column_unreadable"],
        "dwh.cost": ["cost.not_estimated", "cost.not_measured", "cost.not_measured"],
    }
    [plan] = run.cost["plans"]
    assert plan["evidence"] == "the engine refused EXPLAIN ESTIMATE on the reference query"
    [execution] = run.cost["executions"]
    assert execution["evidence"] == (
        "ClickHouse refused the exact SELECT with Code 497 ACCESS_DENIED; no declared runtime "
        "bound was measured"
    )
    assert run.verdict == "block"


def _unfit(run: Run, unfit: str, drifted: str) -> None:
    """The plan has no number and names the drift; the exact SELECT was not sent at all."""
    [plan] = run.cost["plans"]
    assert plan.get("scanned_rows") is None
    assert plan["evidence"] == (
        f"EXPLAIN ESTIMATE answered the reference query with {unfit}, taken to mean the "
        f"query no longer fits the mart as the engine has it ({drifted} drifted from the "
        "model)"
    )
    [execution] = run.cost["executions"]
    assert execution["checked_bounds"] == []
    assert execution["evidence"] == (
        f"the exact SELECT was not run: EXPLAIN ESTIMATE of the same query answered {unfit}, "
        "and a query the mart no longer fits measures nothing; no declared runtime bound was "
        "measured"
    )


UNFIT_COST = ["cost.not_estimated", "cost.not_measured", "cost.not_measured"]


@pytest.mark.parametrize(
    ("alter", "physical", "unfit", "drifted", "verdict"),
    [
        (
            "MODIFY COLUMN revenue Float64",
            ("review", "physical.type_drift"),
            "Code 43 ILLEGAL_TYPE_OF_ARGUMENT",
            "revenue",
            "needs_review",
        ),
        (
            "MODIFY COLUMN revenue String",
            ("review", "physical.type_drift"),
            "Code 43 ILLEGAL_TYPE_OF_ARGUMENT",
            "revenue",
            "needs_review",
        ),
        (
            "RENAME COLUMN revenue TO revenue_rub",
            ("block", "physical.column_absent"),
            "Code 47 UNKNOWN_IDENTIFIER",
            "revenue",
            "block",
        ),
        (
            "DROP COLUMN gross_margin",
            ("block", "physical.column_absent"),
            "Code 47 UNKNOWN_IDENTIFIER",
            "gross_margin",
            "block",
        ),
    ],
    ids=["float", "string", "renamed", "dropped"],
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
    """The drift is graded; the run used to end on the engine's error with no bundle."""
    root = _product(stand, tmp_path)
    stand.run(f"ALTER TABLE dm.sales_daily {alter}")

    run = _verify(stand, root)

    assert run.changed() == {
        "dwh.physical": physical,
        "dwh.cost": ("review", "cost.not_estimated"),
    }
    assert run.codes() == {"dwh.physical": [physical[1]], "dwh.cost": UNFIT_COST}
    _unfit(run, unfit, drifted)
    assert run.verdict == verdict


def test_a_drift_the_query_still_fits_is_priced(stand: Stand, tmp_path: Path) -> None:
    """A wider Decimal still takes `divideDecimal`: the drift is graded, the cost measured."""
    root = _product(stand, tmp_path)
    stand.run("ALTER TABLE dm.sales_daily MODIFY COLUMN revenue Decimal(38, 4)")

    run = _verify(stand, root)

    assert run.changed() == {"dwh.physical": ("review", "physical.type_drift")}
    [plan] = run.cost["plans"]
    assert plan["scanned_rows"] == ROWS
    [execution] = run.cost["executions"]
    assert execution["checked_bounds"] == ["max_scanned_bytes", "timeout_ms"]
    assert run.verdict == "needs_review"


def test_a_mart_without_its_event_time_column_is_reported(stand: Stand, tmp_path: Path) -> None:
    """`max(date)` of a dropped column used to end the run; its rows are counted, undated."""
    root = _product(stand, tmp_path)
    # A sorting or partition key column cannot be dropped: the mart is laid out again first.
    stand.run("DROP TABLE dm.sales_daily SYNC")
    stand.run(
        "CREATE TABLE dm.sales_daily (date Date, store_id UInt32, region String, "
        "category String, revenue Decimal(18, 2), gross_margin Decimal(18, 2)) "
        "ENGINE = MergeTree ORDER BY store_id"
    )
    stand.run(SCHEMA[-1])
    stand.run("ALTER TABLE dm.sales_daily DROP COLUMN date")

    run = _verify(stand, root)

    assert run.changed() == {
        "dwh.freshness": ("review", "freshness.unknown"),
        "dwh.physical": ("block", "physical.column_absent"),
        "dwh.cost": ("review", "cost.not_estimated"),
    }
    assert run.codes() == {
        "dwh.freshness": ["freshness.unknown"],
        "dwh.physical": ["physical.column_absent", "physical.layout_drift"],
        "dwh.cost": UNFIT_COST,
    }
    assert run.details["dwh.freshness"] == [
        f"dm.sales_daily has {ROWS} row(s) on {stand.server.host}:{stand.server.port}/dm and no "
        "newest "
        "date to date them by — a NULL max, or a column the engine no longer has — the check "
        "did not run rather than passing"
    ]
    _unfit(run, "Code 47 UNKNOWN_IDENTIFIER", "date")
    assert run.verdict == "block"
