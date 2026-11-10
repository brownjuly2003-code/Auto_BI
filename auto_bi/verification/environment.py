"""Environment checks — the part of §8.2 step 7 that needs a running engine.

The step is split in two on purpose, and this half is pure. It says what has to be *asked*
of the engine (`questions_for`), what an answer looks like (`EnvironmentFacts`), and what
the answers *mean* (`check_access`, `check_freshness`). Collecting them is IO and lives
behind the read-only `RunQuery` seam in `environment_probe.py`. So the gate grades facts
instead of holding a socket, every rule here is testable without an engine, and the facts
the verdict stood on go into evidence as data rather than as a log line.

**Two answers no amount of reading would have settled**, both from stands of the pinned
versions (ClickHouse 24.8.14.39, PostgreSQL 14.24):

* *read-only is not the same question on the two engines.* A ClickHouse user declared in
  `users.xml` holds `GRANT INSERT, ALTER, DROP … ON *.*` even when its profile sets
  `readonly=1`; the profile is what makes the session refuse a write (`Code: 164 READONLY`),
  and reading grants alone would call a correctly configured read-only role writable. On
  PostgreSQL it is the other way round: `transaction_read_only` reads `off` for a
  SELECT-only role, and `has_table_privilege` is the answer. Column-level INSERT/UPDATE
  grants are asked through `has_any_column_privilege` because the table-level question
  answers false for them while the role can write. Roles reachable through `SET ROLE`
  from the session user are asked the same write questions, because a `NOINHERIT`
  membership hides their grants from `current_user` while the session can switch to them
  and write. An unanswered write-privilege question
  or role identity question cannot establish read-only access. Each engine is therefore asked
  its own question, and the fact carries the evidence it came from — never an attempted
  write. A gate that probed by trying to insert would be a gate that writes to a DWH.
* *an empty ClickHouse table still has a max event time.* `max(Date)` over no rows returns
  `1970-01-01`, not NULL, so a freshness rule reading that alone would call an empty mart
  fifty years stale instead of unknown. PostgreSQL returns NULL for the same query. The
  collector reports `rows_seen` beside `max_event_time`, and "no rows" is
  `freshness.unknown` — a check that could not run, not a stale source.

**Freshness is measured against the run's `created_at`, not a wall clock.** The gate takes
its timestamp as an input so the same verification is reproducible (§7.6); reading
`datetime.now()` here would make the age — and the verdict — depend on when the report was
rendered. A UTC moment is the instant it names. A clock with no zone (PostgreSQL's
`timestamp`) and a day are read in the one timezone the metrics declare for the column, a day
as its 00:00 there — the conservative end of the day it stands for — and the detail says what
each was read as. No declared zone, several, or a reading after `created_at` is a review, not
a guess. The probe spells an instant once, in UTC; any other text is unreadable, by its form,
on every interpreter (docs/specs/event-time-readings.md).

The *cost* half keeps plan and runtime evidence separate. Scanned rows are priced from the
plan of each governed query, and the two engines answer that question differently — **both
readings verified on the stands, and both of them able to lie by staying silent**:

* ClickHouse `EXPLAIN ESTIMATE` reports parts, rows and marks and **no bytes at all**. It is
  a pre-scan reading, not a selectivity one: adding `WHERE store_id = 1` left the estimate at
  600 rows on the stand, which is what makes it the right number for "how much will this
  read". It answers with **no rows at all** when the plan touches no MergeTree part — a
  trivial `count()` served from metadata, or a mart on `Memory`, which returned an empty
  estimate while the query really did read its rows. So an empty answer is *not* zero scanned
  rows, and grading it as zero would let every budget pass over a mart on the wrong engine;
* PostgreSQL `EXPLAIN (FORMAT JSON)` — without `ANALYZE`, so nothing is executed — carries
  the reading on the *scan* nodes, not on the plan's root: the stand's aggregate said
  `Plan Rows: 60` above a `Seq Scan` that said 600. Two silences here too. A scan node with a
  `Filter` reports rows *after* it (`WHERE store_id = 1` turned 600 into 30), so that number
  is not what was read; and a mart emptied by row security produces no scan node at all —
  the planner answered `One-Time Filter: "false"` with `Plan Rows: 0`, which as a budget
  reading would be a clean pass over a mart that returns nothing.

Bytes and time are never derived from those plans. The IO side runs the exact step-6 SELECT
only when its mart is readable through a read-only role and row security has not emptied it.
ClickHouse discards output with `FORMAT Null`, enforces `max_bytes_to_read` and
`max_execution_time`, and records `read_bytes`/`elapsed_ns` when the driver exposes its
summary; successful completion under the enforced caps still proves nonzero declared bounds.
PostgreSQL streams and discards rows inside a read-only transaction with
`statement_timeout`. PostgreSQL exposes no honest exact-query scanned-byte counter, so that
one bound remains explicitly unmeasured.

The *physical* half asks a different question: does the engine hold what the candidate
model says it holds? A column the metric reads and the mart does not have is a release that
cannot compute its own number; a type that drifted is worse than missing, because everything
upstream — step 6's exact ratio above all — was reasoned out from the declared one. A layout
that drifted (sorting key, partition key, distribution key) does not break the number, but it
does mean the advisor and the plan were argued from a table that is not there.

Grading, and why:

* a role that can write → **block**. Invariant 3 says the gate reaches the DWH through a
  read-only role; numbers collected through a role that could have changed the mart are not
  the evidence this bundle claims to hold;
* an in-scope grain table that is absent or unreadable → **block**. The metric cannot be
  computed in this environment at all, and a release "verified" against a mart that is not
  there is not verified;
* a source older than the product's declared window → **review**. The change itself is not
  wrong; whether to ship a decision product on stale data is the owner's call, and that is
  the same grading a stale verified asset already gets;
* a window in years or calendar months → **review**, not a guess. Neither has a fixed
  length, so the age cannot be compared to it exactly, and this gate does not pick a
  convention for someone else's SLO;
* a column the metric reads that the engine does not have → **block**; a column whose type
  drifted from the model → **review**; a drifted layout → **review**. Only what the model
  actually claims is compared, and only where the engine reports it: a mart whose engine
  does not expose a distribution policy is not evidence that the declared one is wrong;
* a plan or exact runtime query over its metric's budget → **review**. Missing plan or
  runtime evidence → **review** as well, because a check that could not run is not a pass.
  One metric with no compiled reference query produces one **advisory** finding: step 6 has
  already named that boundary, so repeating it per declared ceiling would weigh it twice.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import NamedTuple
from zoneinfo import ZoneInfo, available_timezones

from pydantic import Field

from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.engine import sqlglot_dialect
from auto_bi.refs import ContractRef
from auto_bi.scalars import Duration, NonBlankText, Timestamp, duration_seconds
from auto_bi.semantic.gateway import MetricNode, SemanticSnapshot, TableNode
from auto_bi.semantic.metric_contract import MetricBudget
from auto_bi.semantic.model import DataClassification
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel
from auto_bi.verification.sql_contract import compile_metric_queries

#: `server_version_num` of PostgreSQL 9.5, the first kernel with row-level security.
POSTGRES_ROW_SECURITY_VERSION = 90500


#: Classifications that §10.1 says a DWH role or a deterministic rewrite has to enforce —
#: not the prompt boundary, and not a document. `internal` is the default of an unset field,
#: so treating it as sensitive would make every mart a finding.
_SENSITIVE = frozenset({DataClassification.CONFIDENTIAL, DataClassification.RESTRICTED})


class LayoutClaim(StrictModel):
    """What the candidate model says the mart's physical layer is."""

    table_engine: str = ""
    sorting_key: list[str] = Field(default_factory=list)
    partition_key: str = ""
    distribution_key: list[str] = Field(default_factory=list)


class LayoutFacts(StrictModel):
    """What the engine reports about it. `None` is "not reported", which is not "empty":
    a plain PostgreSQL stand has no distribution policy to answer with, and silence there
    must not read as a contradiction of the model."""

    table_engine: str | None = None
    sorting_key: list[str] | None = None
    partition_key: str | None = None
    distribution_key: list[str] | None = None


class TableQuestion(StrictModel):
    """One mart the product's metrics read, and the column that dates its rows."""

    table: NonBlankText
    event_time_column: NonBlankText
    #: Column -> declared type, for exactly the fields the in-scope metrics read.
    columns: dict[str, str] = Field(default_factory=dict)
    layout: LayoutClaim = Field(default_factory=LayoutClaim)
    #: The business timezones the metrics declare for that column. A day or a clock with no
    #: zone is read in the one zone named here; none, several, or a name the zone database
    #: does not hold leave it unread (docs/specs/event-time-readings.md).
    timezones: list[str] = Field(default_factory=list)
    #: The metrics that read this table at this grain, so a finding names them.
    metric_refs: list[str] = Field(default_factory=list)
    #: Columns among those above that the model declares confidential or restricted, and
    #: the in-scope metrics that declare it of themselves. Both travel because a metric can
    #: be sensitive over columns that are not (a headcount over plain integers), and a
    #: column can be sensitive under a metric that is not.
    sensitive_columns: list[str] = Field(default_factory=list)
    sensitive_metric_refs: list[str] = Field(default_factory=list)

    def declares_sensitive(self) -> bool:
        return bool(self.sensitive_columns or self.sensitive_metric_refs)


class PlanQuestion(StrictModel):
    """One metric's reference query, and the ceiling the metric declares for running it.

    The query is step 6's — the metric at its own grain, no window and no filter — because
    that is the only plan the gate can stand behind; a chart's query belongs to a product
    and a run. `sql` is empty when step 6 compiled none (an opaque formula, or a ratio on an
    engine family whose division was never verified): the metric still declares a bound, so
    it stays in the list and the check says the bound went unmeasured instead of dropping it.
    """

    metric_ref: NonBlankText
    table: NonBlankText
    #: The sqlglot dialect the SQL was compiled for. A plan compiled for another engine is
    #: not asked of this one — the syntax would be somebody else's.
    dialect: str = ""
    sql: str = ""
    budgets: MetricBudget


class EnvironmentQuestions(StrictModel):
    """Everything one environment probe has to answer for this product."""

    #: Declared engines of the in-scope grain tables. More than one means no single
    #: connection can answer for all of them.
    engines: list[str] = Field(default_factory=list)
    tables: list[TableQuestion] = Field(default_factory=list)
    #: Reference queries to price, one per in-scope metric that declares a budget. A metric
    #: with no declared ceiling is not asked about: there would be nothing to compare the
    #: answer to, and the gate does not run queries for readings it cannot grade.
    plans: list[PlanQuestion] = Field(default_factory=list)

    def single_engine(self) -> str | None:
        return self.engines[0] if len(self.engines) == 1 else None


class RoleFacts(StrictModel):
    """Who the gate connected as, and whether that session could write."""

    name: str = Field(min_length=1)
    read_only: bool
    #: What the engine said, in its own terms (`readonly=1`, `no write privilege on …`).
    evidence: NonBlankText
    #: Whether row-level security does not apply to this session at all (a PostgreSQL
    #: superuser or `BYPASSRLS`). `None` when identity was not reported or where the engine
    #: has no such notion: ClickHouse policies do not name a user rather than being bypassed.
    #: `None` also on a kernel that predates row-level security.
    bypasses_row_security: bool | None = None


class RowPolicyFacts(StrictModel):
    """One row-level policy the engine reports on a mart.

    `expression` is the filter as the engine prints it — schema, like a partition key, not
    rows: it is what makes "this reading was filtered" checkable instead of a claim.
    """

    name: NonBlankText
    #: Whether it filters what *this* session reads. A policy naming another role is worth
    #: reporting (the mart is governed) but does not narrow these numbers.
    applies_to_session: bool
    restrictive: bool = False
    expression: str = ""


class TableFacts(StrictModel):
    """What the engine reports about one mart."""

    table: NonBlankText
    #: The clock of the question this reading answers. `None` only in readings made before
    #: readings recorded their event-time column.
    event_time_column: NonBlankText | None = None
    exists: bool
    readable: bool
    #: Rows behind the event-time reading. Zero is why `max_event_time` may be meaningless.
    rows_seen: int = Field(ge=0, default=0)
    #: A day `2026-08-24`, a UTC moment `2026-08-24T09:00:00Z`, or a clock with no zone
    #: `2026-08-24T09:00:00`; any other text is the engine's own, and `dwh.freshness` names it
    #: unreadable. `None` when the engine has nothing to date (no rows, or a NULL max) or
    #: no event-time column left to take `max()` of.
    max_event_time: str | None = None
    #: Column -> type as the engine spells it. `None` means the probe did not ask, or the
    #: engine refused the catalog question: nothing is then compared, nothing is absent.
    columns: dict[str, str] | None = None
    layout: LayoutFacts | None = None
    #: Asked column -> may this session read it. `None` means the engine was not asked.
    #: It is a separate reading from `columns` because the two disagree by design on
    #: ClickHouse, which hides columns the role holds no `SELECT(col)` grant on: without
    #: this, a column that is merely out of reach reads as a column that is not there.
    column_reads: dict[str, bool] | None = None
    #: Whether the engine has row-level security switched on for this mart (PostgreSQL
    #: `relrowsecurity`). `None` on engines with no such switch — ClickHouse has policies
    #: and nothing else, so `row_policies` is the whole answer there.
    #: `None` also on a kernel that predates row-level security.
    row_security_enabled: bool | None = None
    #: Current role holds the mart owner's privileges and FORCE is off. Independent of
    #: the role-wide row-security exemption (superuser or BYPASSRLS); None when unanswered
    #: or on a kernel without row security.
    #: For a view mart this concerns only its own policies, which a view does not have;
    #: the policies of its base tables are not asked about.
    owner_bypasses_row_security: bool | None = None
    #: Policies the engine reports on this mart. `None` is "not reported" — a role that
    #: cannot read the access catalog has not established that there are none.
    #: `[]` on a present mart on a kernel that predates row-level security.
    row_policies: list[RowPolicyFacts] | None = None

    def applying_policies(self) -> list[RowPolicyFacts]:
        if self.row_security_enabled is False or self.owner_bypasses_row_security is True:
            return []
        return [policy for policy in self.row_policies or () if policy.applies_to_session]


class PlanFacts(StrictModel):
    """What the engine's planner said one reference query would read.

    `scanned_rows` is `None` for every reading that is not a scan estimate, and the two
    engines each have a way of answering that silently — see the module docstring. Zero is a
    measurement; absence is not, and the difference is the whole point of the field.
    """

    metric_ref: NonBlankText
    scanned_rows: int | None = Field(default=None, ge=0)
    #: How that number was read, in the engine's own terms — or why there is none.
    evidence: NonBlankText


class CostBound(StrEnum):
    """Runtime cost ceilings an engine can enforce while running the exact SELECT."""

    SCANNED_BYTES = "max_scanned_bytes"
    TIMEOUT = "timeout_ms"


class ExecutionFacts(StrictModel):
    """What running one exact step-6 reference query established about its cost.

    A limit exception often carries no trustworthy final counter. `checked_bounds` therefore
    records the ceilings the engine enforced, while `exceeded_bound` records the one that
    stopped the query. Optional counters remain evidence, never invented substitutes.
    """

    metric_ref: NonBlankText
    scanned_bytes: int | None = Field(default=None, ge=0)
    elapsed_ms: int | None = Field(default=None, ge=0)
    checked_bounds: list[CostBound] = Field(default_factory=list)
    exceeded_bound: CostBound | None = None
    evidence: NonBlankText


class EnvironmentFacts(StrictModel):
    """One read-only look at one environment — the evidence step 7 grades."""

    engine: NonBlankText
    #: Where it looked, never how it got in: host, port and database, no credentials.
    target: NonBlankText
    #: `server_version_num` of a PostgreSQL-family kernel, recorded only when the probe had
    #: to ask it: a kernel before 9.5 has no row-level security. `None` on ClickHouse and
    #: on every kernel that answered the identity question.
    server_version_num: int | None = Field(default=None, ge=0)
    role: RoleFacts
    tables: list[TableFacts] = Field(default_factory=list)
    plans: list[PlanFacts] = Field(default_factory=list)
    executions: list[ExecutionFacts] = Field(default_factory=list)

    def predates_row_security(self) -> bool:
        """Whether a PostgreSQL-family kernel reported a version without row-level security."""
        return (
            self.server_version_num is not None
            and self.server_version_num < POSTGRES_ROW_SECURITY_VERSION
        )

    def table(self, name: str) -> TableFacts | None:
        return next((item for item in self.tables if item.table == name), None)

    def plan(self, metric_ref: str) -> PlanFacts | None:
        return next((item for item in self.plans if item.metric_ref == metric_ref), None)

    def execution(self, metric_ref: str) -> ExecutionFacts | None:
        return next((item for item in self.executions if item.metric_ref == metric_ref), None)


class EnvironmentFinding(StrictModel):
    code: str = Field(
        min_length=1, pattern=r"^(access|freshness|physical|security|cost)\.[a-z0-9_]+$"
    )
    severity: Severity
    subject: str = Field(min_length=1)
    detail: NonBlankText


def reading_for(
    question: TableQuestion | PlanQuestion,
    questions: EnvironmentQuestions,
    readings: list[TableFacts],
) -> TableFacts | None:
    """Resolve a question's reading by its mart and clock, never by collection order.

    A plan belongs to the table question listing its metric. Legacy unlabelled readings
    answer only a single-clock mart with exactly one reading; duplicate exact readings
    answer nothing even when an unlabelled reading is also present.
    """
    if isinstance(question, PlanQuestion):
        owners = [
            item
            for item in questions.tables
            if item.table == question.table and question.metric_ref in item.metric_refs
        ]
        if len(owners) != 1:
            return None
        table_question = owners[0]
    else:
        table_question = question
    candidates = [item for item in readings if item.table == table_question.table]
    exact = [
        item for item in candidates if item.event_time_column == table_question.event_time_column
    ]
    if exact:
        return exact[0] if len(exact) == 1 else None
    clocks = {
        item.event_time_column for item in questions.tables if item.table == table_question.table
    }
    if len(clocks) == 1 and len(candidates) == 1 and candidates[0].event_time_column is None:
        return candidates[0]
    return None


#: The forms of a newest event time `dwh.freshness` reads, and no other: a day, a clock with no
#: zone, and a UTC moment of the one `Timestamp` spelling (docs/specs/event-time-readings.md).
#: `[0-9]`, because `\d` matches every script's digits and `int` reads them.
_EVENT_TIME = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]+))?(Z?))?"
)


class _Reading(NamedTuple):
    """A newest event time read by its form, before any zone is applied."""

    #: The clock it names, with no zone; a day is its 00:00.
    clock: datetime
    #: `day`, `clock` (no zone) or `moment` (UTC, the one `Timestamp` spelling).
    form: str


def questions_for(product: DecisionProductSpec, snapshot: SemanticSnapshot) -> EnvironmentQuestions:
    """What the engine has to be asked about, derived from the product's in-scope metrics.

    Only the grain tables of metrics this product actually reads: an environment check is
    about the numbers behind this decision, not an audit of the warehouse.
    """
    tables: dict[str, TableQuestion] = {}
    engines: set[str] = set()

    for ref in product.scope.metrics:
        node = snapshot.metric(str(ContractRef.parse(ref).without_version()))
        if node is None:  # a pin that no longer resolves is `decision validate`'s finding
            continue
        table = next((item for item in snapshot.graph.tables if item.id == node.grain.table), None)
        if table is not None and table.engine:
            engines.add(table.engine)
        key = f"{node.grain.table}:{node.time.event_time_column}"
        columns = _declared_columns(node, snapshot)
        sensitive = _sensitive_columns(columns, node.grain.table, snapshot)
        metric_sensitive = [ref] if node.classification in _SENSITIVE else []
        question = tables.get(key)
        if question is None:
            tables[key] = TableQuestion(
                table=node.grain.table,
                event_time_column=node.time.event_time_column,
                columns=columns,
                layout=_declared_layout(table),
                timezones=[node.time.timezone],
                metric_refs=[ref],
                sensitive_columns=sensitive,
                sensitive_metric_refs=metric_sensitive,
            )
            continue
        question.columns.update(columns)
        if node.time.timezone not in question.timezones:
            question.timezones.append(node.time.timezone)
        if ref not in question.metric_refs:
            question.metric_refs.append(ref)
        question.sensitive_columns.extend(
            column for column in sensitive if column not in question.sensitive_columns
        )
        question.sensitive_metric_refs.extend(
            item for item in metric_sensitive if item not in question.sensitive_metric_refs
        )

    return EnvironmentQuestions(
        engines=sorted(engines),
        tables=[tables[key] for key in sorted(tables)],
        plans=_plan_questions(product, snapshot),
    )


def _plan_questions(product: DecisionProductSpec, snapshot: SemanticSnapshot) -> list[PlanQuestion]:
    """The reference query of every in-scope metric that declares a cost ceiling.

    Only budgeted metrics: an estimate with nothing to compare it to is a query run on
    somebody's warehouse for a number no rule reads. The queries are step 6's own — asking
    the compiler here rather than re-deriving the SQL keeps one definition of what the
    metric's query *is*, so a plan can never be priced against a form the evidence bundle
    does not hold.
    """
    budgeted = []
    for ref in product.scope.metrics:
        node = snapshot.metric(str(ContractRef.parse(ref).without_version()))
        if node is not None and node.budgets is not None:
            budgeted.append((ref, node))
    if not budgeted:
        return []

    compiled = compile_metric_queries(snapshot, [ref for ref, _ in budgeted]).by_metric()
    questions: list[PlanQuestion] = []
    for ref, node in budgeted:
        assert node.budgets is not None  # filtered above; narrows the type for mypy
        query = compiled.get(f"{node.id}@{node.version}")
        questions.append(
            PlanQuestion(
                metric_ref=ref,
                table=node.grain.table,
                dialect="" if query is None else query.dialect,
                sql="" if query is None else query.sql,
                budgets=node.budgets,
            )
        )
    return questions


def _declared_columns(node: MetricNode, snapshot: SemanticSnapshot) -> dict[str, str]:
    """The fields this metric's value depends on, with the types the model declares.

    `MetricNode.fields` is already exactly that list — formula terms, grain keys and the
    event time — so the check asks about the columns the number is made of, not about every
    column the mart happens to carry.
    """
    declared: dict[str, str] = {}
    for field_id in node.fields:
        table, _, column = field_id.rpartition(".")
        if table != node.grain.table:
            continue  # a term on another table is `sql.join_required`'s finding, not this one
        field = next((item for item in snapshot.graph.fields if item.id == field_id), None)
        if field is not None:
            declared[column] = field.type
    return declared


def _sensitive_columns(
    columns: dict[str, str], table: str, snapshot: SemanticSnapshot
) -> list[str]:
    """Which of the columns this metric reads the model calls confidential or restricted.

    The classification is the model's own claim (§10.1), and it is what makes an unenforced
    restriction checkable: a mart the role reads wide open is not evidence of a control.
    """
    return sorted(
        column
        for column in columns
        if (
            field := next(
                (item for item in snapshot.graph.fields if item.id == f"{table}.{column}"), None
            )
        )
        is not None
        and field.classification in _SENSITIVE
    )


def _declared_layout(table: TableNode | None) -> LayoutClaim:
    if table is None:
        return LayoutClaim()
    return LayoutClaim(
        table_engine=table.table_engine,
        sorting_key=list(table.sorting_key),
        partition_key=table.partition_key,
        distribution_key=list(table.distribution_key),
    )


def check_access(
    questions: EnvironmentQuestions, facts: EnvironmentFacts
) -> list[EnvironmentFinding]:
    """Can this role read what the product needs — and only read it?"""
    findings: list[EnvironmentFinding] = []

    declared = questions.single_engine()
    if declared is None and len(questions.engines) > 1:
        findings.append(
            EnvironmentFinding(
                code="access.engines_mixed",
                severity=Severity.REVIEW,
                subject=facts.target,
                detail=(
                    "the in-scope marts declare more than one engine "
                    f"({', '.join(questions.engines)}); one connection cannot answer for "
                    "all of them, so this environment was only checked against "
                    f"{facts.engine}"
                ),
            )
        )
    elif declared is not None and declared != facts.engine:
        findings.append(
            EnvironmentFinding(
                code="access.environment_mismatch",
                severity=Severity.REVIEW,
                subject=facts.target,
                detail=(
                    f"the probe answered for {facts.engine} while the in-scope marts declare "
                    f"{declared}; the readings below are about a different engine than the "
                    "model reasons over"
                ),
            )
        )

    if not facts.role.read_only:
        findings.append(
            EnvironmentFinding(
                code="access.role_writable",
                severity=Severity.BLOCK,
                subject=facts.role.name,
                detail=(
                    f"the gate reached {facts.target} as {facts.role.name}, "
                    "whose read-only access is not established "
                    f"({facts.role.evidence}); evidence collected through a role that could "
                    "have changed the mart is not the evidence this bundle claims to hold "
                    "(invariant 3)"
                ),
            )
        )

    for question in questions.tables:
        table = reading_for(question, questions, facts.tables)
        readings = [item for item in facts.tables if item.table == question.table]
        if not readings:
            findings.append(
                EnvironmentFinding(
                    code="access.table_not_probed",
                    severity=Severity.REVIEW,
                    subject=question.table,
                    detail=(
                        f"{question.table} is in scope but the probe of {facts.target} "
                        "carries no answer about it"
                    ),
                )
            )
            continue
        if table is None:
            count = sum(item.event_time_column == question.event_time_column for item in readings)
            if count:
                reason = f"{count} readings recorded for it"
            elif any(item.event_time_column is None for item in readings):
                reason = (
                    "a reading that records no column cannot be attributed while the mart "
                    "is dated by several columns or has several readings"
                )
            else:
                reason = "no reading recorded for that column"
            findings.append(
                EnvironmentFinding(
                    code="access.reading_unattributed",
                    severity=Severity.REVIEW,
                    subject=question.table,
                    detail=f"{question.table} has no attributable reading for "
                    f"{question.event_time_column} on {facts.target}: {reason}",
                )
            )
            continue
        if not table.exists:
            findings.append(
                EnvironmentFinding(
                    code="access.table_absent",
                    severity=Severity.BLOCK,
                    subject=question.table,
                    detail=(
                        f"{question.table} does not exist on {facts.target}; the metrics "
                        f"{', '.join(question.metric_refs)} cannot be computed there at all"
                    ),
                )
            )
        elif not table.readable:
            findings.append(
                EnvironmentFinding(
                    code="access.table_unreadable",
                    severity=Severity.BLOCK,
                    subject=question.table,
                    detail=(
                        f"{facts.role.name} cannot read {question.table} on {facts.target}; "
                        "a release verified against a mart this role cannot see is not "
                        "verified"
                    ),
                )
            )

    return findings


def check_freshness(
    questions: EnvironmentQuestions,
    facts: EnvironmentFacts,
    *,
    max_age: Duration,
    created_at: Timestamp,
) -> list[EnvironmentFinding]:
    """Is each mart newer than the window the product declares (§7.2 `freshness_max_age`)?"""
    window = duration_seconds(max_age)
    findings: list[EnvironmentFinding] = []

    for question in questions.tables:
        table = reading_for(question, questions, facts.tables)
        if table is None or not table.exists or not table.readable:
            continue  # already a finding of its own in `check_access`
        if denies_all_rows(table, facts.role):
            continue  # `check_security` blocks on it; "no rows" here is that same fact
        if table.rows_seen == 0 or table.max_event_time is None:
            undated = (
                f"{question.table} has no rows to date on {facts.target}, so its "
                f"{question.event_time_column} says nothing about how fresh the source is"
                if table.rows_seen == 0
                else f"{question.table} has {table.rows_seen} row(s) on {facts.target} and no "
                f"newest {question.event_time_column} to date them by — a NULL max, or a "
                "column the engine no longer has"
            )
            findings.append(
                EnvironmentFinding(
                    code="freshness.unknown",
                    severity=Severity.REVIEW,
                    subject=question.table,
                    detail=f"{undated} — the check did not run rather than passing",
                )
            )
            continue
        if window is None:
            findings.append(
                EnvironmentFinding(
                    code="freshness.window_not_comparable",
                    severity=Severity.REVIEW,
                    subject=question.table,
                    detail=(
                        f"the declared window {max_age} is measured in years or calendar "
                        "months, which have no fixed length; an exact age is not compared "
                        "to it here, and the convention is the owner's to declare"
                    ),
                )
            )
            continue
        reported = (
            f"{question.table} on {facts.target} reports {table.max_event_time!r} as its "
            f"newest {question.event_time_column}"
        )
        reading = _read(table.max_event_time)
        if reading is None:
            findings.append(
                EnvironmentFinding(
                    code="freshness.unreadable_event_time",
                    severity=Severity.REVIEW,
                    subject=question.table,
                    detail=(
                        f"{reported}, which is none of the forms this check reads: a day "
                        "YYYY-MM-DD, a clock YYYY-MM-DDTHH:MM:SS with no zone, or a UTC moment "
                        "such as 2026-08-25T09:00:00Z"
                    ),
                )
            )
            continue
        read_as = ""
        if reading.form == "moment":
            instant = reading.clock.replace(tzinfo=UTC)
        else:
            zone, unread = _zone_for(question)
            if zone is None:
                findings.append(
                    EnvironmentFinding(
                        code="freshness.event_time_zone_unknown",
                        severity=Severity.REVIEW,
                        subject=question.table,
                        detail=(
                            f"{reported}, a {reading.form} with no zone; {unread}, so the "
                            "instant it names is not known and its age is not graded"
                        ),
                    )
                )
                continue
            try:
                # `fold=0`: a clock the zone repeats is its first instant, and one it skips
                # takes the offset before the skip; a day's 00:00 is read by the same rule
                # (docs/specs/event-time-readings.md names the days where that is not when
                # the day began).
                instant = reading.clock.replace(tzinfo=zone, fold=0).astimezone(UTC)
            except OverflowError:
                findings.append(
                    EnvironmentFinding(
                        code="freshness.unreadable_event_time",
                        severity=Severity.REVIEW,
                        subject=question.table,
                        detail=(
                            f"{reported}, a {reading.form} that read in {zone.key} falls "
                            "outside the years 0001 to 9999 of UTC, where no instant is read"
                        ),
                    )
                )
                continue
            read_as = (
                f", read in {zone.key} as {_utc_text(instant)}"
                if reading.form == "clock"
                else f", read as 00:00 of that day in {zone.key}, {_utc_text(instant)}"
            )
        now = _run_moment(created_at)
        if instant > now:
            findings.append(
                EnvironmentFinding(
                    code="freshness.event_time_ahead",
                    severity=Severity.REVIEW,
                    subject=question.table,
                    detail=(
                        f"{reported}{read_as}, after the run's created_at {created_at}: a "
                        "source cannot be dated after the run that reads it, so its age is "
                        "not graded"
                    ),
                )
            )
            continue
        age = (now - instant).total_seconds()
        if age > window:
            findings.append(
                EnvironmentFinding(
                    code="freshness.stale",
                    severity=Severity.REVIEW,
                    subject=question.table,
                    detail=(
                        f"{question.table} is dated {table.max_event_time} by "
                        f"{question.event_time_column} ({_hours(age)} before {created_at}"
                        f"{read_as}), older than the {max_age} this product declares"
                    ),
                )
            )

    return findings


def check_physical(
    questions: EnvironmentQuestions, facts: EnvironmentFacts
) -> list[EnvironmentFinding]:
    """Does the engine hold what the candidate model says it holds?"""
    findings: list[EnvironmentFinding] = []

    for question in questions.tables:
        table = reading_for(question, questions, facts.tables)
        if table is None or not table.exists or not table.readable:
            continue  # `check_access` already said so; saying it twice weighs it twice
        findings.extend(_column_findings(question, table, facts))
        findings.extend(_layout_findings(question, table, facts))

    return findings


def _column_findings(
    question: TableQuestion, table: TableFacts, facts: EnvironmentFacts
) -> list[EnvironmentFinding]:
    """Every column the metric's value is made of, as the engine has it."""
    findings: list[EnvironmentFinding] = []
    for column, actual in column_drift(question, table, engine=facts.engine).items():
        declared = question.columns[column]
        if actual is None:
            findings.append(
                EnvironmentFinding(
                    code="physical.column_absent",
                    severity=Severity.BLOCK,
                    subject=f"{question.table}.{column}",
                    detail=(
                        f"{question.table} on {facts.target} has no column {column}, which "
                        f"{', '.join(question.metric_refs)} reads; the metric cannot be "
                        "computed there"
                    ),
                )
            )
        else:
            findings.append(
                EnvironmentFinding(
                    code="physical.type_drift",
                    severity=Severity.REVIEW,
                    subject=f"{question.table}.{column}",
                    detail=(
                        f"{question.table}.{column} is {actual} on {facts.target} while the "
                        f"model declares {declared}; everything upstream — the exact form of "
                        "a governed ratio above all — was reasoned out from the declared type"
                    ),
                )
            )
    return findings


def _layout_findings(
    question: TableQuestion, table: TableFacts, facts: EnvironmentFacts
) -> list[EnvironmentFinding]:
    """Only what the model claims, and only where the engine answered."""
    if table.layout is None:
        return []
    claim, actual = question.layout, table.layout
    drifted = [
        (name, declared, reported)
        for name, declared, reported in (
            ("table engine", claim.table_engine, actual.table_engine),
            ("sorting key", ", ".join(claim.sorting_key), _joined(actual.sorting_key)),
            ("partition key", claim.partition_key, actual.partition_key),
            (
                "distribution key",
                ", ".join(claim.distribution_key),
                _joined(actual.distribution_key),
            ),
        )
        if declared and reported is not None and _normalized(declared) != _normalized(reported)
    ]
    if not drifted:
        return []
    moved = "; ".join(
        f"{name} is {reported or 'unset'} there, {declared} in the model"
        for name, declared, reported in drifted
    )
    return [
        EnvironmentFinding(
            code="physical.layout_drift",
            severity=Severity.REVIEW,
            subject=question.table,
            detail=(
                f"{question.table} on {facts.target} is laid out differently from the model: "
                f"{moved}. The number is unaffected, but the plan and the advisor were argued "
                "from a table that is not there"
            ),
        )
    ]


def check_security(
    questions: EnvironmentQuestions, facts: EnvironmentFacts
) -> list[EnvironmentFinding]:
    """Does this environment enforce what the model calls restricted — and did a restriction
    narrow the numbers this run read?

    Two questions, one check, because they are two halves of one fact: the reach of the role
    the evidence was collected through. A row policy that applies to the session makes every
    reading in the bundle a reading of a subset; a column the metric is made of that the role
    may not select makes the metric uncomputable here; a mart the model calls restricted and
    the engine hands over whole says the restriction is a document rather than a control
    (§10.1 — row/column restrictions belong to the DWH role or to a deterministic rewrite,
    and are proven by negative tests, not by the declaration).
    """
    findings: list[EnvironmentFinding] = []

    for question in questions.tables:
        table = reading_for(question, questions, facts.tables)
        if table is None or not table.exists or not table.readable:
            continue  # `check_access` already said so; saying it twice weighs it twice
        findings.extend(_unobserved_findings(question, table, facts))
        findings.extend(_reach_findings(question, table, facts))
        findings.extend(_row_policy_findings(question, table, facts))
        findings.extend(_classification_findings(question, table, facts))

    return findings


def denies_all_rows(table: TableFacts, role: RoleFacts) -> bool:
    """Whether row security leaves this session no rows at all on this mart.

    PostgreSQL: with RLS switched on a row has to pass at least one *permissive* policy, so a
    mart whose policies are all restrictive — or whose policies name other roles — answers an
    empty set to a `SELECT` that is otherwise allowed (verified on 14.24: `count(*)` = 0 and
    no error, which is exactly why this cannot be left to the reading). ClickHouse has no such
    switch and a user no policy names reads everything (verified on 24.8.14.39, including a
    restrictive-only policy, which filters there instead of denying), so the switch — not the
    policy list — is the condition.
    """
    if (
        table.row_security_enabled is not True
        or role.bypasses_row_security is True
        or table.owner_bypasses_row_security is True
    ):
        return False
    if table.row_policies is None:
        return False
    return not any(
        policy.applies_to_session and not policy.restrictive for policy in table.row_policies
    )


def _unobserved_findings(
    question: TableQuestion, table: TableFacts, facts: EnvironmentFacts
) -> list[EnvironmentFinding]:
    """A role that cannot read the access catalog has not established that it reads it all."""
    missing = [
        text
        for reported, text in (
            (table.column_reads is not None, "which of its columns this role may read"),
            (table.row_policies is not None, "whether a row policy filters what it reads"),
        )
        if not reported
    ]
    if not missing:
        return []
    return [
        EnvironmentFinding(
            code="security.not_observed",
            severity=Severity.REVIEW,
            subject=question.table,
            detail=(
                f"{facts.target} did not answer {' or '.join(missing)} for {question.table}: "
                f"{facts.role.name} cannot read that from this engine's catalog, so these "
                "readings are not established to be of the whole mart — the check did not run "
                "rather than passing"
            ),
        )
    ]


def _reach_findings(
    question: TableQuestion, table: TableFacts, facts: EnvironmentFacts
) -> list[EnvironmentFinding]:
    """Columns the metric's value is made of that this role may not select."""
    reads = table.column_reads
    if reads is None:
        return []
    out_of_reach = sorted(column for column in question.columns if reads.get(column) is False)
    if not out_of_reach:
        return []
    subject = f"{question.table}.{out_of_reach[0]}" if len(out_of_reach) == 1 else question.table
    return [
        EnvironmentFinding(
            code="security.column_unreadable",
            severity=Severity.BLOCK,
            subject=subject,
            detail=(
                f"{facts.role.name} may not read {', '.join(out_of_reach)} on "
                f"{question.table}, which {', '.join(question.metric_refs)} is made of, so the "
                f"metric cannot be computed on {facts.target} through this role. The mart has "
                "the column — this is reach, not a missing column, and the two are one "
                "answer on an engine that hides what it does not grant"
            ),
        )
    ]


def _row_policy_findings(
    question: TableQuestion, table: TableFacts, facts: EnvironmentFacts
) -> list[EnvironmentFinding]:
    """What row-level security did to the numbers this run collected."""
    policies = table.row_policies
    if policies is None:
        return []
    applying = table.applying_policies()

    if (
        policies
        and table.row_security_enabled is not False
        and (facts.role.bypasses_row_security is True or table.owner_bypasses_row_security is True)
    ):
        exemptions = []
        if facts.role.bypasses_row_security is True:
            exemptions.append("the role-wide row-security exemption (SUPERUSER or BYPASSRLS)")
        if table.owner_bypasses_row_security is True:
            exemptions.append("the mart owner's privileges with FORCE ROW LEVEL SECURITY off")
        return [
            EnvironmentFinding(
                code="security.row_security_bypassed",
                severity=Severity.REVIEW,
                subject=question.table,
                detail=(
                    f"{question.table} on {facts.target} reports {_policy_names(policies)}; "
                    f"current role {facts.role.name} holds {' and '.join(exemptions)}; "
                    "the reported policies do not filter what this session read"
                ),
            )
        ]
    if denies_all_rows(table, facts.role):
        named = f" ({_policy_names(policies)})" if policies else ""
        return [
            EnvironmentFinding(
                code="security.row_policy_denies_all",
                severity=Severity.BLOCK,
                subject=question.table,
                detail=(
                    f"row security is on for {question.table} on {facts.target} and no "
                    f"permissive policy admits {facts.role.name}{named}; the mart answers an "
                    f"empty set to this role, so {', '.join(question.metric_refs)} cannot be "
                    "verified here — and an empty answer is not an error the reading would "
                    "have shown"
                ),
            )
        ]
    if applying:
        return [
            EnvironmentFinding(
                code="security.row_filtered",
                severity=Severity.REVIEW,
                subject=question.table,
                detail=(
                    f"{_policy_names(applying)} filters what {facts.role.name} reads from "
                    f"{question.table} on {facts.target}, so every reading taken here — the "
                    "row count, the newest event time, any number reconciled from this mart — "
                    "is about a subset of it and not about the mart"
                ),
            )
        ]
    return []


def _classification_findings(
    question: TableQuestion, table: TableFacts, facts: EnvironmentFacts
) -> list[EnvironmentFinding]:
    """The model calls this restricted; does the engine restrict it for this role?"""
    if (
        not question.declares_sensitive()
        or table.row_policies is None
        or table.column_reads is None
    ):
        return []
    reads = table.column_reads or {}
    if table.applying_policies() or any(
        reads.get(column) is False for column in question.sensitive_columns
    ):
        return []  # something narrowed this role's reach, so the declaration is a control here
    declared = ", ".join(
        [
            *(f"{question.table}.{column}" for column in question.sensitive_columns),
            *question.sensitive_metric_refs,
        ]
    )
    return [
        EnvironmentFinding(
            code="security.unenforced_classification",
            severity=Severity.REVIEW,
            subject=question.table,
            detail=(
                f"the model declares {declared} confidential or restricted, but on "
                f"{facts.target} no row policy applies to {facts.role.name} and it may read "
                "every column of that; the classification is a document here and not a "
                "control the engine enforces (§10.1)"
            ),
        )
    ]


def check_cost(
    questions: EnvironmentQuestions, facts: EnvironmentFacts
) -> list[EnvironmentFinding]:
    """Does each governed query stay inside the ceilings its metric declares?

    Graded as **review** throughout, and never as block: a plan over budget still computes
    the right number, and whether to ship a metric that costs more than its owner declared
    is that owner's call — the same grading a stale source already gets. Scanned rows stay a
    planner fact. Bytes and time are facts from executing the exact step-6 SELECT under
    engine-enforced limits; a missing runtime measurement is review, never a clean pass.
    """
    findings: list[EnvironmentFinding] = []

    for question in questions.plans:
        table = reading_for(question, questions, facts.tables)
        if table is None or not table.exists or not table.readable:
            continue  # `check_access` already blocked on the mart; the plan is downstream
        if denies_all_rows(table, facts.role):
            continue  # `check_security` blocked on it, and the plan would price an empty read
        if not question.sql:
            findings.append(_reference_query_unavailable(question))
            continue
        findings.extend(_bound_findings(question, facts))
        findings.extend(_runtime_bound_findings(question, facts))

    return findings


def _reference_query_unavailable(question: PlanQuestion) -> EnvironmentFinding:
    declared = ", ".join(
        f"{name}={value}"
        for name, value in question.budgets.model_dump().items()
        if value is not None
    )
    return EnvironmentFinding(
        code="cost.plan_unavailable",
        severity=Severity.ADVISORY,
        subject=question.metric_ref,
        detail=(
            f"{question.metric_ref} declares {declared}, but step 6 compiled no reference "
            "query for it, so neither its plan nor its exact SELECT can be checked"
        ),
    )


def _bound_findings(question: PlanQuestion, facts: EnvironmentFacts) -> list[EnvironmentFinding]:
    """The one bound a planner does answer: how many rows the query is going to read."""
    limit = question.budgets.max_scanned_rows
    if limit is None:
        return []
    plan = facts.plan(question.metric_ref)
    if plan is None or plan.scanned_rows is None:
        reason = "the probe did not answer for it" if plan is None else plan.evidence
        return [
            EnvironmentFinding(
                code="cost.not_estimated",
                severity=Severity.REVIEW,
                subject=question.metric_ref,
                detail=(
                    f"{question.metric_ref} declares a ceiling of {limit} scanned rows and "
                    f"{facts.target} gave no estimate to compare it to ({reason}) — the "
                    "check did not run rather than passing"
                ),
            )
        ]
    if plan.scanned_rows > limit:
        return [
            EnvironmentFinding(
                code="cost.over_budget",
                severity=Severity.REVIEW,
                subject=question.metric_ref,
                detail=(
                    f"the plan for {question.metric_ref} on {facts.target} reads "
                    f"{plan.scanned_rows} rows, over the {limit} its contract declares "
                    f"({plan.evidence})"
                ),
            )
        ]
    return []


def _runtime_bound_findings(
    question: PlanQuestion, facts: EnvironmentFacts
) -> list[EnvironmentFinding]:
    """Grade byte and time ceilings only from the exact query's runtime evidence."""
    execution = facts.execution(question.metric_ref)
    bounds = (
        (
            CostBound.SCANNED_BYTES,
            question.budgets.max_scanned_bytes,
            "scanned bytes",
            None if execution is None else execution.scanned_bytes,
        ),
        (
            CostBound.TIMEOUT,
            question.budgets.timeout_ms,
            "ms",
            None if execution is None else execution.elapsed_ms,
        ),
    )
    findings: list[EnvironmentFinding] = []
    for bound, limit, label, measured in bounds:
        if limit is None:
            continue
        if execution is not None and execution.exceeded_bound is bound:
            findings.append(
                EnvironmentFinding(
                    code="cost.over_budget",
                    severity=Severity.REVIEW,
                    subject=question.metric_ref,
                    detail=(
                        f"{facts.target} stopped the exact reference query for "
                        f"{question.metric_ref} at its {limit} {label} ceiling "
                        f"({execution.evidence})"
                    ),
                )
            )
            continue
        if measured is not None and measured > limit:
            action = "read" if bound is CostBound.SCANNED_BYTES else "ran for"
            findings.append(
                EnvironmentFinding(
                    code="cost.over_budget",
                    severity=Severity.REVIEW,
                    subject=question.metric_ref,
                    detail=(
                        f"the exact query for {question.metric_ref} on {facts.target} {action} "
                        f"{measured} {label}, over the {limit} its contract declares "
                        f"({execution.evidence if execution is not None else ''})"
                    ),
                )
            )
            continue
        if measured is not None or (execution is not None and bound in execution.checked_bounds):
            continue
        reason = "the probe did not answer for it" if execution is None else execution.evidence
        findings.append(
            EnvironmentFinding(
                code="cost.not_measured",
                severity=Severity.REVIEW,
                subject=question.metric_ref,
                detail=(
                    f"{question.metric_ref} declares a ceiling of {limit} {label}, and "
                    f"{facts.target} gave no runtime measurement to compare it to ({reason}) — "
                    "the check did not run rather than passing"
                ),
            )
        )
    return findings


def _policy_names(policies: list[RowPolicyFacts]) -> str:
    """Name the policies and the filters they carry — schema, the way a partition key is."""
    return "; ".join(
        policy.name
        + (" (restrictive)" if policy.restrictive else "")
        + (f" — {policy.expression}" if policy.expression else "")
        for policy in policies
    )


def _joined(value: list[str] | None) -> str | None:
    return None if value is None else ", ".join(value)


def column_drift(
    question: TableQuestion, table: TableFacts, *, engine: str
) -> dict[str, str | None]:
    """Declared column -> the type the engine now holds it as, `None` for one it no longer has.

    Exactly the columns `physical.column_absent` (`None`) and `physical.type_drift` grade, and
    nothing else: environment_probe.py weighs an engine's `47 UNKNOWN_IDENTIFIER` and
    `43 ILLEGAL_TYPE_OF_ARGUMENT` (PostgreSQL: `42703`, `42883`) against the same reading, so
    a drift the probe takes a code for is one this check reports. Empty when the probe did not
    read the catalog. A type is held as declared when it is the same type by another spelling:
    other spacing or case, or on a PostgreSQL kernel another of its names (`_type_key`).
    """
    if table.columns is None:
        return {}
    reads = table.column_reads or {}
    drift: dict[str, str | None] = {}
    for column, declared in sorted(question.columns.items()):
        actual = table.columns.get(column)
        if actual is None and (
            reads.get(column) is False or (engine == "clickhouse" and table.column_reads is None)
        ):
            # ClickHouse hides columns the role holds no `SELECT(col)` grant on, so absent
            # from the catalog does not prove absence with denied or unknown reach.
            # `check_security` reports the denied reach or the unobserved boundary.
            continue
        if actual is None or _type_key(actual, engine) != _type_key(declared, engine):
            drift[column] = actual
    return drift


def _normalized(type_or_key: str) -> str:
    """`Decimal(18, 2)` and `decimal(18,2)` are one type; spacing and case are not content."""
    return "".join(type_or_key.split()).lower()


#: PostgreSQL's other names for a type -> the name `format_type` prints, which is what the
#: probe reads from `pg_attribute`: the "Aliases" column of the manual's table of data types,
#: the serial types aside (a column's type is never `serial`), and `dec` and `char varying`,
#: which its grammar reads the same way.
_PG_ALIASES = {
    "int": "integer",
    "int4": "integer",
    "int2": "smallint",
    "int8": "bigint",
    "float4": "real",
    "float8": "double precision",
    "bool": "boolean",
    "dec": "numeric",
    "decimal": "numeric",
    "char": "character",
    "varchar": "character varying",
    "char varying": "character varying",
    "varbit": "bit varying",
}
#: The two names that carry their zone in them.
_PG_ZONED = {"timestamptz": "timestamp", "timetz": "time"}
#: A PostgreSQL type as a model may write it, lowercased with single blanks: a name of one or
#: more words, a modifier, the zone of a time type, array brackets. A quoted or
#: schema-qualified name, or a modifier with parentheses of its own, is not read. Each
#: repeated part matches its text one way only, and the zone, which the name could also take,
#: occurs once: a blank two repeats could take makes a failed match retry every split, 2^n
#: tries for n repeats, and the model's type is free text (`[ ]` was such a blank, before or
#: after an empty bound).
_PG_SPELLING = re.compile(
    r"(?P<name>[a-z][a-z0-9_]*(?: [a-z][a-z0-9_]*)*?)"
    r"(?: ?\((?P<modifier>[^()]*)\))?"
    r"(?P<zone> with(?:out)? time zone)?"
    r"(?P<array>(?: ?\[ ?(?:[0-9]+ ?)?\])*)"
)


def _type_key(spelling: str, engine: str) -> str:
    """What two spellings of one type compare equal by (ADR 0010, F2): spacing and case are not
    content on any engine, and on a PostgreSQL kernel neither is which of its names a model
    writes. ClickHouse's own aliases are not PostgreSQL's — its `INT8` is `Int8`, not `bigint`
    — so they are not applied there."""
    if sqlglot_dialect(engine) == "postgres":
        spelling = _pg_type(spelling)
    return _normalized(spelling)


def _pg_type(spelling: str) -> str:
    """A PostgreSQL type as `format_type` prints it, from any name the engine takes for it:
    `int4` is `integer`, `varchar(20)` is `character varying(20)`, `timestamp` is
    `timestamp without time zone`, `float(24)` is `real`, `numeric(10)` is `numeric(10,0)`,
    `char` is `character(1)`, `bpchar(3)` is `character(3)`, `int4[][]` is `integer[]`
    (dimensions are not part of the type: `format_type` prints one pair of brackets, and the
    count `attndims` keeps is not enforced). A spelling the pattern does not read is
    returned as written, but for spacing and case, which `_normalized` drops anyway."""
    text = " ".join(spelling.lower().split())
    match = _PG_SPELLING.fullmatch(text)
    if match is None:
        return text
    name, modifier, zone, array = match.group("name", "modifier", "zone", "array")
    name = _PG_ALIASES.get(name, name)
    parts = None if modifier is None else [part.strip() for part in modifier.split(",")]
    if name == "bpchar" and parts is not None:
        name = "character"  # with no length `bpchar` is unbounded, not `character(1)`
    if name in _PG_ZONED:
        if zone:
            return text  # `timestamptz without time zone` names no type
        name, zone = _PG_ZONED[name], " with time zone"
    if name in ("timestamp", "time"):
        zone = zone or " without time zone"
    elif name == "float" and (precision := 53 if parts is None else _pg_precision(parts)):
        # `float` is `float8`; `float(p)` is `float4` up to 24 binary digits, `float8` to 53
        name, parts = ("real" if precision <= 24 else "double precision"), None
    elif name == "numeric" and parts is not None and len(parts) == 1:
        parts.append("0")  # a precision with no scale is scale 0
    elif name == "character" and parts is None:
        parts = ["1"]  # SQL: `character` with no length is `character(1)`
    typmod = "" if parts is None else f"({','.join(parts)})"
    return f"{name}{typmod}{zone or ''}{'[]' if array else ''}"


def _pg_precision(parts: list[str]) -> int | None:
    """The binary precision `float(p)` takes, 1 to 53; `None` for any other modifier."""
    if len(parts) != 1 or not (parts[0].isascii() and parts[0].isdecimal()):
        return None
    # `int()` refuses more than 4300 digits, and the model's type is free text
    digits = parts[0].lstrip("0")
    if len(digits) > 2:
        return None
    precision = int(digits or "0")
    return precision if 1 <= precision <= 53 else None


def _hours(seconds: float) -> str:
    hours = seconds / 3600
    return f"{hours:.1f}h" if hours < 48 else f"{hours / 24:.1f}d"


def _zone_for(question: TableQuestion) -> tuple[ZoneInfo | None, str]:
    """The one zone a day or a clock with no zone is read in, or why there is none.

    A name counts only as the zone database lists it (`available_timezones`): `ZoneInfo` on
    Windows also opens `utc`, `Europe\\Moscow` and a name with a blank behind it, which the
    database does not list, so what opens depends on the host's file system.
    """
    zones = list(dict.fromkeys(question.timezones))
    if not zones:
        return None, "the metrics that read it declare no timezone"
    if len(zones) > 1:
        named = ", ".join(repr(zone) for zone in zones)
        return None, f"the metrics that read it declare {len(zones)} timezones, {named}"
    if zones[0] not in available_timezones():
        return None, (
            f"the metrics that read it declare {zones[0]!r}, which the zone database this "
            "gate reads does not hold"
        )
    return ZoneInfo(zones[0]), ""


def _utc_text(instant: datetime) -> str:
    """A UTC instant in the one `Timestamp` spelling."""
    return instant.replace(tzinfo=None).isoformat() + "Z"


def _run_moment(created_at: Timestamp) -> datetime:
    reading = _read(created_at)
    if reading is None or reading.form != "moment":
        raise ValueError(f"created_at must be a Timestamp, not {created_at!r}")
    return reading.clock.replace(tzinfo=UTC)


def _read(text: str) -> _Reading | None:
    """The clock a reading names and its form; `None` for a form not read.

    Decided by the form and the calendar, never by `datetime.fromisoformat`, which reads forms of
    ISO 8601 beyond these and `24:00` from Python 3.14 on (utc-timestamps.md). Digits of a
    fraction after the sixth are not counted.
    """
    match = _EVENT_TIME.fullmatch(text)
    if match is None:
        return None
    year, month, day, hour, minute, second, fraction, utc = match.groups()
    try:
        clock = datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            int((fraction or "")[:6].ljust(6, "0")),
        )
    except ValueError:
        return None
    return _Reading(clock, "moment" if utc else "day" if hour is None else "clock")


__all__ = [
    "EnvironmentFacts",
    "EnvironmentFinding",
    "EnvironmentQuestions",
    "LayoutClaim",
    "LayoutFacts",
    "RoleFacts",
    "RowPolicyFacts",
    "TableFacts",
    "TableQuestion",
    "check_access",
    "check_freshness",
    "check_physical",
    "check_security",
    "denies_all_rows",
    "questions_for",
]
