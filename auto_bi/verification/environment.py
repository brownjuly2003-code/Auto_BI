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
  convention for someone else's SLO.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from pydantic import Field

from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.refs import ContractRef
from auto_bi.scalars import Duration, NonBlankText, Timestamp
from auto_bi.semantic.gateway import SemanticSnapshot
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel

#: Duration components with a fixed length in seconds. Years and calendar months are absent
#: on purpose: `P1M` is 28 to 31 days, and an exact age cannot be compared to an inexact
#: window without inventing a convention.
_FIXED_UNITS: dict[str, timedelta] = {
    "W": timedelta(weeks=1),
    "D": timedelta(days=1),
    "H": timedelta(hours=1),
    "M": timedelta(minutes=1),  # only after the T separator; see `_window_seconds`
    "S": timedelta(seconds=1),
}


class TableQuestion(StrictModel):
    """One mart the product's metrics read, and the column that dates its rows."""

    table: NonBlankText
    event_time_column: NonBlankText
    #: The business timezone the metrics declare for that column — reported, not applied:
    #: it is what makes a date-only reading an assumption rather than a fact.
    timezones: list[str] = Field(default_factory=list)
    #: The metrics that read this table at this grain, so a finding names them.
    metric_refs: list[str] = Field(default_factory=list)


class EnvironmentQuestions(StrictModel):
    """Everything one environment probe has to answer for this product."""

    #: Declared engines of the in-scope grain tables. More than one means no single
    #: connection can answer for all of them.
    engines: list[str] = Field(default_factory=list)
    tables: list[TableQuestion] = Field(default_factory=list)

    def single_engine(self) -> str | None:
        return self.engines[0] if len(self.engines) == 1 else None


class RoleFacts(StrictModel):
    """Who the gate connected as, and whether that session could write."""

    name: NonBlankText
    read_only: bool
    #: What the engine said, in its own terms (`readonly=1`, `no write privilege on …`).
    evidence: NonBlankText


class TableFacts(StrictModel):
    """What the engine reports about one mart."""

    table: NonBlankText
    exists: bool
    readable: bool
    #: Rows behind the event-time reading. Zero is why `max_event_time` may be meaningless.
    rows_seen: int = Field(ge=0, default=0)
    #: ISO 8601 as the engine gave it: `2026-08-24` or `2026-08-24T09:00:00Z`. `None` when
    #: the engine has nothing to date (no rows, or a NULL max).
    max_event_time: str | None = None


class EnvironmentFacts(StrictModel):
    """One read-only look at one environment — the evidence step 7 grades."""

    engine: NonBlankText
    #: Where it looked, never how it got in: host, port and database, no credentials.
    target: NonBlankText
    role: RoleFacts
    tables: list[TableFacts] = Field(default_factory=list)

    def table(self, name: str) -> TableFacts | None:
        return next((item for item in self.tables if item.table == name), None)


class EnvironmentFinding(StrictModel):
    code: str = Field(min_length=1, pattern=r"^(access|freshness)\.[a-z0-9_]+$")
    severity: Severity
    subject: NonBlankText
    detail: NonBlankText


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
        question = tables.get(key)
        if question is None:
            tables[key] = TableQuestion(
                table=node.grain.table,
                event_time_column=node.time.event_time_column,
                timezones=[node.time.timezone],
                metric_refs=[ref],
            )
            continue
        if node.time.timezone not in question.timezones:
            question.timezones.append(node.time.timezone)
        if ref not in question.metric_refs:
            question.metric_refs.append(ref)

    return EnvironmentQuestions(
        engines=sorted(engines),
        tables=[tables[key] for key in sorted(tables)],
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
        table = facts.table(question.table)
        if table is None:
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
    window = _window_seconds(max_age)
    findings: list[EnvironmentFinding] = []

    for question in questions.tables:
        table = facts.table(question.table)
        if table is None or not table.exists or not table.readable:
            continue  # already a finding of its own in `check_access`
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
                        f"{question.table} is dated {table.max_event_time} "
                        f"({_hours(age.seconds)} before {created_at}"
                        f"{_assumption(age, question)}), older than the {max_age} this "
                        "product declares"
                    ),
                )
            )

    return findings


def _assumption(age: _Age, question: TableQuestion) -> str:
    """Name the reading when a date-only value had to stand for a moment in time."""
    if not age.assumed_start_of_day:
        return ""
    zones = ", ".join(question.timezones) or "unstated"
    return f", read as {age.read_as} while the metric declares timezone {zones}"


def _hours(seconds: float) -> str:
    hours = seconds / 3600
    return f"{hours:.1f}h" if hours < 48 else f"{hours / 24:.1f}d"


def _window_seconds(duration: Duration) -> float | None:
    """ISO 8601 duration as seconds, or None when it is not a fixed length."""
    body = duration[1:]
    date_part, _, time_part = body.partition("T")
    if "Y" in date_part or "M" in date_part:
        return None
    total = timedelta()
    for part, allowed in ((date_part, "WD"), (time_part, "HMS")):
        number = ""
        for char in part:
            if char.isdigit():
                number += char
                continue
            if char in allowed and number:
                total += int(number) * _FIXED_UNITS[char]
            number = ""
    return total.total_seconds()


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
    "RoleFacts",
    "TableFacts",
    "TableQuestion",
    "check_access",
    "check_freshness",
    "questions_for",
]
