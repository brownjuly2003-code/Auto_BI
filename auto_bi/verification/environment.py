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
  SELECT-only role, and `has_table_privilege` is the answer. Each engine is therefore asked
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
rendered. A date-only event time is read as that day's start in UTC: the conservative end
of the day it stands for, and the detail says so, because the metric's declared timezone can
move a day boundary by up to fourteen hours.

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

from datetime import UTC, datetime
from enum import StrEnum
from typing import NamedTuple

from pydantic import Field

from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.refs import ContractRef
from auto_bi.scalars import Duration, NonBlankText, Timestamp, duration_seconds
from auto_bi.semantic.gateway import MetricNode, SemanticSnapshot, TableNode
from auto_bi.semantic.metric_contract import MetricBudget
from auto_bi.semantic.model import DataClassification
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel
from auto_bi.verification.sql_contract import compile_metric_queries

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
    #: The business timezone the metrics declare for that column — reported, not applied:
    #: it is what makes a date-only reading an assumption rather than a fact.
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

    name: NonBlankText
    read_only: bool
    #: What the engine said, in its own terms (`readonly=1`, `no write privilege on …`).
    evidence: NonBlankText
    #: Whether row-level security does not apply to this session at all (a PostgreSQL
    #: superuser or `BYPASSRLS`). `None` where the engine has no such notion: ClickHouse
    #: policies simply do not name a user rather than being bypassed by one.
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
    #: ISO 8601 as the engine gave it: `2026-08-24` or `2026-08-24T09:00:00Z`. `None` when
    #: the engine has nothing to date (no rows, or a NULL max).
    max_event_time: str | None = None
    #: Column -> type as the engine spells it. `None` means the probe did not ask.
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
    row_security_enabled: bool | None = None
    #: Policies the engine reports on this mart. `None` is "not reported" — a role that
    #: cannot read the access catalog has not established that there are none.
    row_policies: list[RowPolicyFacts] | None = None

    def applying_policies(self) -> list[RowPolicyFacts]:
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
    role: RoleFacts
    tables: list[TableFacts] = Field(default_factory=list)
    plans: list[PlanFacts] = Field(default_factory=list)
    executions: list[ExecutionFacts] = Field(default_factory=list)

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
    subject: NonBlankText
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


class _Age(NamedTuple):
    seconds: float
    read_as: str
    assumed_start_of_day: bool


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
                    f"the gate reached {facts.target} as {facts.role.name}, which can write "
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
            findings.append(
                EnvironmentFinding(
                    code="freshness.unknown",
                    severity=Severity.REVIEW,
                    subject=question.table,
                    detail=(
                        f"{question.table} has no rows to date on {facts.target}, so its "
                        f"{question.event_time_column} says nothing about how fresh the "
                        "source is — the check did not run rather than passing"
                    ),
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
        age = _age_seconds(table.max_event_time, created_at)
        if age is None:
            findings.append(
                EnvironmentFinding(
                    code="freshness.unreadable_event_time",
                    severity=Severity.REVIEW,
                    subject=question.table,
                    detail=(
                        f"{question.table} reports {table.max_event_time!r} as its newest "
                        f"{question.event_time_column}, which is not a timestamp this check "
                        "can read"
                    ),
                )
            )
            continue
        if age.seconds > window:
            findings.append(
                EnvironmentFinding(
                    code="freshness.stale",
                    severity=Severity.REVIEW,
                    subject=question.table,
                    detail=(
                        f"{question.table} is dated {table.max_event_time} by "
                        f"{question.event_time_column} "
                        f"({_hours(age.seconds)} before {created_at}"
                        f"{_assumption(age, question)}), older than the {max_age} this "
                        "product declares"
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
    if table.columns is None:
        return []
    findings: list[EnvironmentFinding] = []
    reads = table.column_reads or {}
    for column, declared in sorted(question.columns.items()):
        actual = table.columns.get(column)
        if actual is None and reads.get(column) is False:
            # ClickHouse hides columns the role holds no `SELECT(col)` grant on, so absent
            # from the catalog and out of reach look identical here. `check_security` blocks
            # on the reach and knows which one it is; calling it missing would be a guess.
            continue
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
        elif _normalized(actual) != _normalized(declared):
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
    if table.row_security_enabled is not True or role.bypasses_row_security is True:
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

    if policies and facts.role.bypasses_row_security is True:
        return [
            EnvironmentFinding(
                code="security.row_security_bypassed",
                severity=Severity.REVIEW,
                subject=question.table,
                detail=(
                    f"{question.table} on {facts.target} is governed by "
                    f"{_policy_names(policies)}, and none of it reaches {facts.role.name}, "
                    "which bypasses row security; the gate read rows the product's consumers "
                    "do not get, so this is a verification of a wider table than the one that "
                    "gets delivered"
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
    if not question.declares_sensitive() or table.row_policies is None:
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


def _normalized(type_or_key: str) -> str:
    """`Decimal(18, 2)` and `decimal(18,2)` are one type; spacing and case are not content."""
    return "".join(type_or_key.split()).lower()


def _assumption(age: _Age, question: TableQuestion) -> str:
    """Name the reading when a date-only value had to stand for a moment in time."""
    if not age.assumed_start_of_day:
        return ""
    zones = ", ".join(question.timezones) or "unstated"
    return f", read as {age.read_as} while the metric declares timezone {zones}"


def _hours(seconds: float) -> str:
    hours = seconds / 3600
    return f"{hours:.1f}h" if hours < 48 else f"{hours / 24:.1f}d"


def _age_seconds(event_time: str, created_at: Timestamp) -> _Age | None:
    """How old the newest row is at the run's own timestamp."""
    text = event_time.strip()
    date_only = len(text) == 10
    normalized = f"{text}T00:00:00Z" if date_only else text.replace(" ", "T")
    try:
        moment = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    now = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return _Age(
        seconds=(now - moment).total_seconds(),
        read_as=normalized,
        assumed_start_of_day=date_only,
    )


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
