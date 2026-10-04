"""Collecting §8.2 step 7's facts from a running engine — the IO half of `environment.py`.

Catalog and plan questions go through `RunQuery`, the read-only callable introspection, the
SQL guard and the advisor already share. Exact-query cost facts arrive through the separate
bounded callable in `runtime_probe.py`, because an arbitrarily large result must never be
materialised into `RunQuery`'s list. Nothing in this module decides anything — it asks,
normalises and hands typed facts back for the pure rules to grade.

**The gate never probes by writing.** Whether the session could write is read out of the
engine's own answer, because an INSERT "just to see" is a write to somebody's warehouse, and
a gate that mutates the thing it verifies has already failed invariant 3.

Each engine is asked the question that is true *for it*, and both forms were checked on
stands of the pinned versions:

* **ClickHouse 24.8.14.39** — a user declared in `users.xml` holds `GRANT INSERT, ALTER,
  DROP … ON *.*` even when its profile sets `readonly=1`, and it is the profile that makes
  the session refuse (`Code: 164 READONLY`). So the setting decides, and grants are read only
  to catch the other case: `readonly=0` with no write privilege granted is read-only too.
  `max(Date)` over an empty table answers `1970-01-01` rather than NULL, so the row count
  travels with the reading — otherwise an empty mart reads as fifty years stale.
  A role granted to the user but not enabled is absent from `system.enabled_roles`, so
  its grants are asked about through `system.role_grants` after the own grants come back
  clean: `SET ROLE` to such a role (or the HTTP `role` parameter) writes, and a switch
  does not move `readonly` on 24.8.14.39 — neither `SET ROLE` nor the `role` parameter
  applies a role's settings. A role name is an arbitrary string, so in a ClickHouse
  question it is written with every backslash and every single quote doubled: a literal
  that leaves a backslash alone names another role or none.
* **PostgreSQL 14.24** (the `numeric`/privilege behaviour Greenplum and Greengage inherit) —
  `transaction_read_only` is `off` for a SELECT-only role, so the session GUC says nothing;
  `has_table_privilege` per in-scope mart is the answer, and a superuser is writable
  whatever the grants say, because privileges do not apply to it. `max()` over no rows is
  NULL here, which the fact model already allows for.
  `has_table_privilege` alone misses a column-level write grant, which
  `has_any_column_privilege` reports for effective privileges (inherited groups and PUBLIC
  included). The privilege functions answer for `current_user` only, so the roles the
  session user can `SET ROLE` to (including itself behind a role default) are asked the
  same write questions, and a reachable superuser is writable.

Column types come from each engine's own catalog in the spelling the model was written in —
`system.columns.type` on ClickHouse, `format_type(atttypid, atttypmod)` on the Postgres
family, which is the same function the Greenplum introspector wrote those types with, so the
comparison is between two spellings of one thing rather than between two conventions.

**Layout is reported only where it was verified.** ClickHouse answers engine, sorting key and
partition key from `system.tables`, and the stand confirmed the shapes (`MergeTree`,
`date, store_id`, `toYYYYMM(date)`). Greenplum keeps its distribution policy and partition
levels in catalogs no PostgreSQL stand has (`gp_distribution_policy`, `pg_partition`), and no
Greengage stand has been available to pin their columns — so the Postgres path leaves those
facts unreported, and `check_physical` compares nothing rather than guessing at a mismatch.

The security half asks what this role may reach, and here the two engines disagree about
almost every mechanism. All of it was established on the same stands, none of it by reading
documentation:

* **columns.** ClickHouse enforces `GRANT SELECT(col)` and *hides* what it does not grant:
  `system.columns` returned five of six columns to a role granted five, so an out-of-reach
  column and a missing column are the same silence. Reach therefore comes from `system.grants`
  — including partial revokes, which is how `REVOKE SELECT(gross_margin)` after a table-wide
  grant is recorded (`is_partial_revoke = 1`) — and never from what the catalog shows.
  PostgreSQL is the opposite: `pg_attribute` lists every column whatever the grants say, and
  `has_column_privilege` answers per column.
* **readability.** `has_table_privilege(…, 'SELECT')` is **false** for a role holding only
  column grants, while that role reads the mart perfectly well; `has_any_column_privilege` is
  the honest question, and grading the narrower one would have blocked a release over a mart
  the gate had just read.
* **row policies.** ClickHouse applies a policy only to the users and roles it names — a
  session no policy names reads the whole mart, and a restrictive-only policy still filters
  (600 → 30 rows on the stand). PostgreSQL is the reverse: with `relrowsecurity` on, a row
  must pass at least one *permissive* policy, so a mart whose policies are all restrictive —
  or name other roles — answers an empty set with no error at all. That silent empty answer
  is why the switch is read as a fact instead of inferred from a row count.
* **who a policy names.** Both engines attach policies to roles as well as users
  (`apply_to_list` on ClickHouse, `polroles` including `public` on PostgreSQL), so the
  session's enabled roles decide membership, not its user name. A policy attached to a role
  the session holds was verified to filter it.
* **who escapes.** A PostgreSQL superuser or `BYPASSRLS` role is not filtered at all, and the
  fact carries that. A table's *owner* also escapes its policies unless they are `FORCE`d,
  and that case is deliberately not modelled: owning a mart carries `INSERT` on it, so
  `access.role_writable` has already blocked the run before row security matters.

**Scanned rows are asked as a plan, never inferred from a run.** `EXPLAIN ESTIMATE` on
ClickHouse and `EXPLAIN` without `ANALYZE` on the Postgres family answer without executing
anything. Each engine has a way of answering that *looks* like zero and is not, and both were
reproduced on the stands: ClickHouse returns an empty estimate whenever the plan reads no
MergeTree part — a `count()` served from metadata, or a mart on `Memory` whose rows the query
did read — and PostgreSQL reports rows on a filtered scan *after* the filter (600 became 30
for `WHERE store_id = 1`) and no scan node at all for a mart row security has emptied. None
of those becomes a number; each becomes a sentence saying why there is none. Byte/time
ceilings use the optional `run_cost` port only after role, readability, row-security and
dialect checks; its exact SELECT mechanics and safety cap live in `runtime_probe.py`.

**A refusal is an answer.** Asking about access is exactly where a read-only role runs out of
privilege, and an exception there would turn a fact about the environment into a crashed run.
`_ask` returns `None` when the engine refuses a question or the object is not there, and
raises everything else — a dead connection is not an access fact and must not be graded as
one.

**A kernel without row-level security is graded by the catalogs it has.** Greenplum 6 and
Greengage 6 run on the PostgreSQL 9.4 kernel, and `pg_roles.rolbypassrls`, `pg_policy` and
`pg_class.relrowsecurity` came with 9.5. The identity question fails there with an undefined
column (SQLSTATE 42703; PostgreSQL 9.4.26, live), which is not a refusal, so the run crashed
on its first question for every role. The version is asked only after that failure — asking
it first would change what every kernel is asked — and a kernel below 9.5 is then asked its
identity without `BYPASSRLS` and no row-policy catalog for any mart. On every other kernel
the undefined column is still raised: there it is not an access fact.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from datetime import date, datetime
from typing import Any, NamedTuple

from auto_bi.engine import CLICKHOUSE, GREENPLUM, sqlglot_dialect
from auto_bi.introspect.base import RunQuery
from auto_bi.verification.environment import (
    POSTGRES_ROW_SECURITY_VERSION,
    EnvironmentFacts,
    EnvironmentQuestions,
    ExecutionFacts,
    LayoutFacts,
    PlanFacts,
    PlanQuestion,
    RoleFacts,
    RowPolicyFacts,
    TableFacts,
    TableQuestion,
    denies_all_rows,
    reading_for,
)

#: Privileges that make a session capable of changing a mart. `TRUNCATE` is Postgres-only;
#: the ClickHouse path reads its own access types.
_PG_WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")

#: Column privileges exist only for SELECT, INSERT, UPDATE and REFERENCES; of those,
#: INSERT and UPDATE write (PostgreSQL 16.15, live).
_PG_COLUMN_WRITE_PRIVILEGES = ("INSERT", "UPDATE")

#: Which roles the session user can switch into with `SET ROLE` and so could write as,
#: although the current role's own write answers say nothing about them. `SET ROLE` is
#: judged by the session user, not by the current role (PostgreSQL 16.15, live): a
#: session parked on a select-only role default could `SET ROLE writer` although
#: `pg_has_role(current_user, 'writer', 'SET')` was false, and `SET ROLE NONE` returned
#: it to a session user that wrote on its own. The `SET` option of a membership exists
#: from 16; before it — and on Greenplum/Greengage — plain membership is the word, and
#: the server picks it inside the question, so one question serves every version. A
#: membership granted `WITH SET FALSE` answers neither. Roles `current_user` already
#: holds `'USAGE'` on are excluded, because their grants are already in the current
#: role's own answers — except superusers: membership passes privileges on, but not
#: `SUPERUSER`, so an inheriting member answers every own write question false while
#: `SET ROLE` into the group writes (PostgreSQL 16.15, live).
_PG_REACHABLE_ROLES = (
    "SELECT r.rolname AS name, r.rolsuper AS superuser FROM pg_roles r "
    "WHERE (r.rolsuper OR NOT pg_has_role(current_user, r.oid, 'USAGE')) AND "
    "(r.rolname = session_user OR pg_has_role(session_user, r.oid, "
    "CASE WHEN current_setting('server_version_num')::int >= 160000 "
    "THEN 'SET' ELSE 'MEMBER' END)) ORDER BY r.rolname"
)

#: ClickHouse access types that can change data or schema, as `system.grants` spells them.
_CH_WRITE_GRANTS = frozenset(
    {"ALL", "INSERT", "ALTER", "CREATE", "DROP", "TRUNCATE", "OPTIMIZE", "UNDROP TABLE"}
)

#: Which roles the session could still enable. A row with `user_name` is a role granted
#: directly to the user — what `SET ROLE` accepts — and a row with `role_name` is granted
#: to that role, enabled together with it however deep (ClickHouse 24.8.14.39, live).
#: The WHERE keeps the session's own rows beside every role-to-role row, including those
#: of roles this session cannot reach: which half a row is, is decided here, not by the
#: engine — whose access catalog does not join.
_CH_ROLE_GRANTS = (
    "SELECT user_name, role_name, granted_role_name FROM system.role_grants "
    "WHERE user_name = currentUser() OR role_name IS NOT NULL"
)

#: SQLSTATEs of a Postgres refusal: no privilege, no such table, no such schema. Everything
#: else — a dropped connection above all — is not an access fact and is re-raised.
_PG_REFUSALS = frozenset({"42501", "42P01", "3F000"})

#: How the two engines word a refusal when the driver carries no state code. ClickHouse's
#: codes are stable identifiers (`497 ACCESS_DENIED`, `60 UNKNOWN_TABLE`), which is what this
#: matches on; the Postgres wording is the fallback for drivers that lose `sqlstate`.
_REFUSED_TEXT = re.compile(
    r"ACCESS_DENIED|Not enough privileges|UNKNOWN_TABLE|UNKNOWN_DATABASE"
    r"|permission denied|must be owner|relation .* does not exist",
    re.IGNORECASE,
)


class _ChSession(NamedTuple):
    """Who ClickHouse thinks is asking, and what that identity may read.

    Grants are collected once per run rather than per mart: they answer both halves of the
    role question (writable, and which columns are in reach), and asking twice would let the
    two halves disagree. `None` means the role may not read `system.grants` at all.
    """

    user: str
    #: The user and its enabled roles — a policy or a grant naming any of them names it.
    principals: frozenset[str]
    roles_known: bool
    #: The enabled role names alone (`principals` also holds the user name), kept for the
    #: reach question: a role already enabled was named by the grants question.
    enabled_roles: frozenset[str]
    #: The `readonly` setting of the session, as the engine reports it (`None` if unset).
    readonly: str | None
    grants: list[dict[str, Any]] | None


def collect(
    run_query: RunQuery,
    questions: EnvironmentQuestions,
    *,
    engine: str,
    target: str,
    run_cost: Callable[[PlanQuestion], ExecutionFacts] | None = None,
) -> EnvironmentFacts:
    """Ask one engine everything step 7 needs to know about one environment."""
    dialect = sqlglot_dialect(engine)
    postgres = dialect == sqlglot_dialect(GREENPLUM)
    server_version_num: int | None = None
    if postgres:
        identity, server_version_num = _pg_identity(run_query)
        row_security = server_version_num is None
        role = _postgres_role(run_query, questions, identity, row_security=row_security)
        tables = [
            _postgres_table(run_query, item, row_security=row_security) for item in questions.tables
        ]
    else:
        session = _clickhouse_session(run_query)
        role = _clickhouse_role(run_query, session)
        tables = [_clickhouse_table(run_query, item, session) for item in questions.tables]
    plans = [
        _estimate(run_query, item, dialect, postgres=postgres)
        for item in questions.plans
        if item.sql
    ]
    executions = (
        [
            run_cost(item)
            for item in questions.plans
            if _may_execute(item, questions, dialect, role, tables)
        ]
        if run_cost is not None
        else []
    )
    return EnvironmentFacts(
        engine=engine,
        target=target,
        server_version_num=server_version_num,
        role=role,
        tables=tables,
        plans=plans,
        executions=executions,
    )


def _may_execute(
    question: PlanQuestion,
    questions: EnvironmentQuestions,
    dialect: str,
    role: RoleFacts,
    tables: list[TableFacts],
) -> bool:
    """Whether an exact runtime probe is both useful and safe for this question."""
    runtime_bound = (
        question.budgets.max_scanned_bytes is not None or question.budgets.timeout_ms is not None
    )
    table = reading_for(question, questions, tables)
    return bool(
        runtime_bound
        and question.sql
        and (not question.dialect or question.dialect == dialect)
        and role.read_only
        and table is not None
        and table.exists
        and table.readable
        and not denies_all_rows(table, role)
    )


# --- plan estimates -----------------------------------------------------------


def _estimate(
    run_query: RunQuery, question: PlanQuestion, dialect: str, *, postgres: bool
) -> PlanFacts:
    """What this engine says the metric's reference query will read.

    Nothing is executed: `EXPLAIN ESTIMATE` and `EXPLAIN` without `ANALYZE` are both plans,
    which is the only form of "how expensive is this" a read-only gate is allowed to ask.
    """
    if question.dialect and question.dialect != dialect:
        return PlanFacts(
            metric_ref=question.metric_ref,
            evidence=(
                f"the reference query was compiled for {question.dialect} and this probe is "
                f"connected to {dialect}, so its plan was not asked for"
            ),
        )
    reading = (
        _postgres_estimate(run_query, question.sql)
        if postgres
        else _clickhouse_estimate(run_query, question.sql)
    )
    return PlanFacts(metric_ref=question.metric_ref, **reading)


def _clickhouse_estimate(run_query: RunQuery, sql: str) -> dict[str, Any]:
    """`EXPLAIN ESTIMATE` — parts, rows and marks, per table the plan reads.

    An empty answer is the trap: the stand returned one for a `count()` served out of
    metadata *and* for a mart on `Memory` whose rows the query really did read. Both mean
    "no MergeTree part in this plan", which is not a measurement of zero, so neither becomes
    a number here.

    `advisor/explain.estimate_scan` asks the same engine the same thing and is deliberately
    not called: it swallows every exception, because an advisor with no measurement still
    advises. A gate may not, and the difference is load-bearing — a refused question is a
    fact about the environment, while a dropped connection is a broken run, and one function
    cannot both report the first and raise the second.
    """
    rows = _ask(run_query, f"EXPLAIN ESTIMATE {sql}")
    if rows is None:
        return {"evidence": "the engine refused EXPLAIN ESTIMATE on the reference query"}
    if not rows:
        return {
            "evidence": (
                "EXPLAIN ESTIMATE named no part to read: a plan answered from metadata, or a "
                "mart on an engine that has no parts to report — not a reading of zero rows"
            )
        }
    scanned = sum(int(row.get("rows") or 0) for row in rows)
    parts = sum(int(row.get("parts") or 0) for row in rows)
    marks = sum(int(row.get("marks") or 0) for row in rows)
    return {
        "scanned_rows": scanned,
        "evidence": f"EXPLAIN ESTIMATE: {scanned} row(s) in {parts} part(s), {marks} mark(s)",
    }


def _postgres_estimate(run_query: RunQuery, sql: str) -> dict[str, Any]:
    """`EXPLAIN (FORMAT JSON)` — the scan nodes, never the root.

    The root of the stand's plan said `Plan Rows: 60` over a `Seq Scan` that said 600: the
    top of a plan is what comes *out*, and a budget is about what goes in. Two shapes are
    refused rather than reported: a scan carrying a `Filter` (its rows are counted after the
    filter — 600 became 30 on the stand), and a plan with no relation scan at all, which is
    what row security emptying a mart looks like (`One-Time Filter: "false"`).
    """
    rows = _ask(run_query, f"EXPLAIN (FORMAT JSON) {sql}")
    if rows is None:
        return {"evidence": "the engine refused EXPLAIN on the reference query"}
    plan = _plan_tree(rows)
    if plan is None:
        return {"evidence": "EXPLAIN returned no plan to read"}
    scans = list(_scan_nodes(plan))
    filtered = [node for node in scans if "Filter" in node]
    if filtered:
        return {
            "evidence": (
                "the plan filters inside its scan of "
                f"{', '.join(str(node.get('Relation Name')) for node in filtered)}, so its "
                "row estimate counts what survives the filter, not what is read"
            )
        }
    if not scans:
        return {
            "evidence": (
                "the plan scans no relation at all — which is also what a mart emptied by "
                "row security plans as — so there is no reading of what it would read"
            )
        }
    scanned = sum(int(node.get("Plan Rows") or 0) for node in scans)
    named = ", ".join(str(node.get("Relation Name")) for node in scans)
    return {
        "scanned_rows": scanned,
        "evidence": f"EXPLAIN (FORMAT JSON): {scanned} row(s) estimated scanning {named}",
    }


def _plan_tree(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The root plan node out of `EXPLAIN (FORMAT JSON)`'s single-column answer.

    The driver hands the document back already parsed; a driver that returns the raw text
    is read here rather than left to fail on `.get`.
    """
    document = next(iter(rows[0].values()), None) if rows else None
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except ValueError:
            return None
    if isinstance(document, list):
        document = document[0] if document else None
    if not isinstance(document, dict):
        return None
    plan = document.get("Plan")
    return plan if isinstance(plan, dict) else None


def _scan_nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every node in the plan that reads a relation, at any depth."""
    if "Relation Name" in node:
        yield node
    for child in node.get("Plans") or ():
        if isinstance(child, dict):
            yield from _scan_nodes(child)


# --- ClickHouse ---------------------------------------------------------------


def _clickhouse_session(run_query: RunQuery) -> _ChSession:
    """Identity, enabled roles and grants — everything the access questions are answered from."""
    user = _scalar(run_query, "SELECT currentUser() AS value") or "unknown"
    readonly = _scalar(run_query, "SELECT value FROM system.settings WHERE name = 'readonly'")
    answered = _ask(run_query, "SELECT role_name FROM system.enabled_roles")
    roles_known = answered is not None and all(row.get("role_name") for row in answered)
    roles = sorted({str(row.get("role_name")) for row in answered or [] if row.get("role_name")})
    # The roles are named in the grants query rather than sub-selected: a privilege can reach
    # this session through a role as easily as through its user name, and one flat condition
    # is the same question on an engine whose access catalog does not join.
    listed = ", ".join(_ch_literal(role) for role in roles)
    named = f" OR role_name IN ({listed})" if roles else ""
    grants = _ask(
        run_query,
        "SELECT access_type, database, table, column, is_partial_revoke FROM system.grants "
        f"WHERE user_name = currentUser(){named}",
    )
    principals = {user, *roles}
    return _ChSession(
        user=user,
        principals=frozenset(principals),
        roles_known=roles_known,
        enabled_roles=frozenset(roles),
        readonly=readonly,
        grants=grants,
    )


def _clickhouse_role(run_query: RunQuery, session: _ChSession) -> RoleFacts:
    """`readonly` decides; grants are the second chance to be read-only, not the first."""
    setting = session.readonly
    if setting in {"1", "2"}:
        return RoleFacts(
            name=session.user,
            read_only=True,
            evidence=f"readonly={setting}, so the session refuses writes whatever it is granted",
        )
    if session.grants is None:
        # `readonly` does not stop this session and the engine will not say what it granted:
        # the safe reading of an unanswered question is not "read-only" (invariant 3).
        return RoleFacts(
            name=session.user,
            read_only=False,
            evidence=(
                f"readonly={setting or 'unset'} and system.grants is out of reach, so nothing "
                "establishes that this session cannot write"
            ),
        )
    granted = {
        str(row.get("access_type", ""))
        for row in session.grants
        if not _truthy(row.get("is_partial_revoke"))
    }
    writable = sorted(access_type for access_type in granted if _ch_write_access(access_type))
    if writable:
        return RoleFacts(
            name=session.user,
            read_only=False,
            evidence=f"readonly={setting or 'unset'}, granted {', '.join(writable)}",
        )
    if not session.roles_known:
        return RoleFacts(
            name=session.user,
            read_only=False,
            evidence=(
                f"readonly={setting or 'unset'} and system.enabled_roles is unreported or "
                "incomplete, so read-only access is not established"
            ),
        )
    # Which roles the session could still enable. `system.enabled_roles` is the session's
    # own choice, so a granted-but-not-enabled role is absent from it while `SET ROLE` to
    # it writes (ClickHouse 24.8.14.39, live); the collector never switches, it only asks.
    gaps: list[str] = []
    pairs: list[tuple[str, str]] = []
    others = _ch_reached_roles(run_query, session, gaps)
    if others:
        _ch_reached_role_grants(run_query, others, gaps, pairs)
    ro = f"readonly={setting or 'unset'}"
    if pairs:
        return RoleFacts(
            name=session.user,
            read_only=False,
            evidence=(
                f"{ro}, SET ROLE reaches "
                + ", ".join(f"{access} granted to {role}" for access, role in sorted(set(pairs)))
            ),
        )
    if gaps:
        return RoleFacts(
            name=session.user,
            read_only=False,
            evidence=(
                f"{ro}, unreported or incomplete: "
                + "; ".join(gaps)
                + ", so read-only access is not established"
            ),
        )
    if others:
        return RoleFacts(
            name=session.user,
            read_only=True,
            evidence=(
                f"{ro}, no write access type granted, also not to the roles SET ROLE "
                f"reaches ({', '.join(others)})"
            ),
        )
    return RoleFacts(
        name=session.user,
        read_only=True,
        evidence=f"{ro}, no write access type granted, and SET ROLE reaches no other role",
    )


def _ch_reached_roles(run_query: RunQuery, session: _ChSession, gaps: list[str]) -> list[str]:
    """The roles `SET ROLE` could still enable, minus the ones already enabled.

    The transitive closure ends on its own: roles granted to each other are read once,
    and a role-to-role row whose holder is not reached adds nothing — the answer also
    carries the rows of roles this session cannot switch to.
    """
    rows = _ask(run_query, _CH_ROLE_GRANTS)
    if rows is None:
        _note_gap(gaps, "system.role_grants")
        return []
    direct: list[str] = []
    to_roles: dict[str, list[str]] = {}
    for row in rows:
        granted = row.get("granted_role_name")
        if granted is None or str(granted) == "":
            _note_gap(gaps, "system.role_grants")
            continue
        user = row.get("user_name")
        holder = row.get("role_name")
        if user is not None and str(user) != "":
            direct.append(str(granted))
        elif holder is not None and str(holder) != "":
            to_roles.setdefault(str(holder), []).append(str(granted))
        else:
            _note_gap(gaps, "system.role_grants")
    reach: set[str] = set()
    pending = list(direct)
    while pending:
        role = pending.pop()
        if role in reach:
            continue
        reach.add(role)
        pending.extend(to_roles.get(role, ()))
    return sorted(reach - session.enabled_roles)


def _ch_reached_role_grants(
    run_query: RunQuery, roles: list[str], gaps: list[str], pairs: list[tuple[str, str]]
) -> None:
    """What the reached roles hold, in one question naming them all.

    A role with no grants has no row, so absence from the answer is an answer; a refused
    question or an unnamed, untyped row is not (invariant 3). A partial revoke is not a
    grant — the same rule the own grants read with — and a row naming a role the probe
    did not ask about says nothing about this session.
    """
    listed = ", ".join(_ch_literal(role) for role in roles)
    rows = _ask(
        run_query,
        "SELECT role_name, access_type, is_partial_revoke FROM system.grants "
        f"WHERE role_name IN ({listed})",
    )
    gap = f"grants of the roles SET ROLE reaches ({', '.join(roles)})"
    if rows is None:
        _note_gap(gaps, gap)
        return
    wanted = set(roles)
    for row in rows:
        role = row.get("role_name")
        access = row.get("access_type")
        if role is None or str(role) == "" or access is None or str(access) == "":
            _note_gap(gaps, gap)
            continue
        name = str(role)
        if name not in wanted or _truthy(row.get("is_partial_revoke")):
            continue
        if _ch_write_access(str(access)):
            pairs.append((str(access), name))


def _ch_write_access(access_type: str) -> bool:
    """The one write-family rule: exact names and canonical `VERB …` leaf access types."""
    return access_type in _CH_WRITE_GRANTS or access_type.startswith(("ALTER ", "CREATE ", "DROP "))


def _note_gap(gaps: list[str], gap: str) -> None:
    """Record an unanswered half once: the evidence names each gap a single time."""
    if gap not in gaps:
        gaps.append(gap)


def _clickhouse_table(
    run_query: RunQuery, question: TableQuestion, session: _ChSession
) -> TableFacts:
    table = question.table
    database, _, name = table.partition(".")
    present = _ask(
        run_query,
        "SELECT engine, sorting_key, partition_key FROM system.tables WHERE database = "
        f"{_literal(database)} AND name = {_literal(name)}",
    )
    if not present:
        # A mart this role holds no grant on is hidden from `system.tables` too, so "absent"
        # and "out of reach" are one silence here. Both are block; the grants in the bundle
        # are what tells them apart afterwards.
        return TableFacts(
            table=table, event_time_column=question.event_time_column, exists=False, readable=False
        )
    reading = _ask(
        run_query,
        f"SELECT count() AS rows_seen, max({_ident(question.event_time_column)}) AS "
        f"max_event_time FROM {_qualified(table)}",
    )
    reads = _ch_column_reads(session, database, name, question.columns)
    policies = _ch_policies(run_query, session, database, name)
    if not reading:  # the table is there but the role cannot read it
        return TableFacts(
            table=table,
            event_time_column=question.event_time_column,
            exists=True,
            readable=False,
            column_reads=reads,
            row_policies=policies,
        )
    columns = {
        str(row.get("name")): str(row.get("type"))
        for row in _ask(
            run_query,
            "SELECT name, type FROM system.columns WHERE database = "
            f"{_literal(database)} AND table = {_literal(name)}",
        )
        or []
    }
    layout = LayoutFacts(
        table_engine=str(present[0].get("engine") or ""),
        sorting_key=_key_list(present[0].get("sorting_key")),
        partition_key=str(present[0].get("partition_key") or ""),
        # ClickHouse has no distribution policy to report; None is "not reported", and
        # `check_physical` compares only what an engine answered.
        distribution_key=None,
    )
    return _reading(
        table,
        reading[0],
        event_time_column=question.event_time_column,
        columns=columns,
        layout=layout,
        column_reads=reads,
        row_policies=policies,
    )


def _ch_column_reads(
    session: _ChSession, database: str, name: str, columns: dict[str, str]
) -> dict[str, bool] | None:
    """Which of the columns the metrics read this identity holds a `SELECT` on.

    Read from grants rather than from `system.columns`, which shows only what it grants: a
    column withheld and a column absent look the same in the catalog, and they are not the
    same finding.
    """
    if session.grants is None or not session.roles_known:
        return None
    selects = [row for row in session.grants if str(row.get("access_type")) == "SELECT"]
    reads: dict[str, bool] = {}
    for column in columns:
        covering = [row for row in selects if _ch_covers(row, database, name, column)]
        granted = any(not _truthy(row.get("is_partial_revoke")) for row in covering)
        revoked = any(_truthy(row.get("is_partial_revoke")) for row in covering)
        reads[column] = granted and not revoked
    return reads


def _ch_covers(row: dict[str, Any], database: str, name: str, column: str) -> bool:
    """Whether one grant row reaches this column. A NULL is "everything at this level"."""
    for value, wanted in (
        (row.get("database"), database),
        (row.get("table"), name),
        (row.get("column"), column),
    ):
        if value is None:
            return True
        if str(value) != wanted:
            return False
    return True


def _ch_policies(
    run_query: RunQuery, session: _ChSession, database: str, name: str
) -> list[RowPolicyFacts] | None:
    """Row policies on this mart, and which of them name this session."""
    rows = _ask(
        run_query,
        "SELECT short_name, select_filter, is_restrictive, apply_to_all, apply_to_list, "
        f"apply_to_except FROM system.row_policies WHERE database = {_literal(database)} "
        f"AND table = {_literal(name)}",
    )
    if rows is None or (rows and not session.roles_known):
        return None
    policies: list[RowPolicyFacts] = []
    for row in rows:
        listed = session.principals & set(_string_list(row.get("apply_to_list")))
        excepted = session.principals & set(_string_list(row.get("apply_to_except")))
        applies = (
            not excepted if _truthy(row.get("apply_to_all")) else bool(listed) and not excepted
        )
        policies.append(
            RowPolicyFacts(
                name=str(row.get("short_name") or "policy"),
                applies_to_session=applies,
                restrictive=_truthy(row.get("is_restrictive")),
                expression=str(row.get("select_filter") or ""),
            )
        )
    return policies


# --- PostgreSQL family --------------------------------------------------------

_PG_IDENTITY = (
    "SELECT current_user AS name, "
    "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS superuser"
)
_PG_UNDEFINED_BYPASSRLS = re.compile(r'column "?rolbypassrls"? does not exist', re.IGNORECASE)


def _pg_identity(run_query: RunQuery) -> tuple[list[dict[str, Any]] | None, int | None]:
    """The identity answer and, on a kernel that predates row-level security, its version.

    The superuser flag is read from `pg_roles`, which lists every role: a current role
    that cannot log in has no `pg_user` row, so that view answers NULL for it and a
    select-only role would block as unreported (PostgreSQL 16.15, live).

    `rolbypassrls` came with row-level security in PostgreSQL 9.5, so the 9.4 kernel
    answers this question with an undefined column, which `_ask` raises. Only then is the
    version asked. A kernel that reports one below 9.5 is asked its identity without
    `BYPASSRLS`; in every other case the error is raised as it came.
    """
    try:
        return (
            _ask(
                run_query,
                f"{_PG_IDENTITY}, "
                "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypassrls",
            ),
            None,
        )
    except Exception as error:
        if not _pg_undefined_bypassrls(error):
            raise
        version = _pg_kernel_version(run_query)
        if version is None:
            raise
    return _ask(run_query, _PG_IDENTITY), version


def _pg_undefined_bypassrls(error: Exception) -> bool:
    """An undefined column by its state code, or by its wording from a driver with no code."""
    state = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
    if state is not None:
        return str(state) == "42703"
    return bool(_PG_UNDEFINED_BYPASSRLS.search(str(error)))


def _pg_kernel_version(run_query: RunQuery) -> int | None:
    """`server_version_num`, or `None` unless it is that of a kernel before 9.5."""
    rows = _ask(run_query, "SELECT current_setting('server_version_num')::int AS version_num")
    version = rows[0].get("version_num") if rows else None
    if isinstance(version, bool) or not isinstance(version, int):
        return None
    return version if 0 <= version < POSTGRES_ROW_SECURITY_VERSION else None


def _postgres_role(
    run_query: RunQuery,
    questions: EnvironmentQuestions,
    rows: list[dict[str, Any]] | None,
    *,
    row_security: bool,
) -> RoleFacts:
    """A superuser bypasses privileges, so it is writable however the grants read.

    `rows` is the identity answer. Without `row_security` the kernel has no `BYPASSRLS` to
    report and nothing to bypass: the flag is not among the unreported fields and
    `bypasses_row_security` stays unset for every role.
    """
    if not rows:
        return RoleFacts(
            name="unknown",
            read_only=False,
            evidence=(
                "unreported role identity, superuser status and BYPASSRLS, "
                "so read-only access is not established"
                if row_security
                else "unreported role identity and superuser status, "
                "so read-only access is not established"
            ),
            bypasses_row_security=None,
        )
    first = rows[0]
    name = str(first.get("name") or "unknown")
    superuser_answer = first.get("superuser")
    bypass_answer = first.get("bypassrls")
    superuser = _truthy(superuser_answer)
    # A superuser is not subject to row security either, so the two facts share one answer.
    bypasses: bool | None = None
    if row_security and (superuser or _truthy(bypass_answer)):
        bypasses = True
    elif row_security and superuser_answer is not None and bypass_answer is not None:
        bypasses = False
    if superuser:
        return RoleFacts(
            name=name,
            read_only=False,
            evidence="a superuser, whose writes no privilege check can stop",
            bypasses_row_security=bypasses,
        )

    unreported_identity: list[str] = []
    if not first.get("name"):
        unreported_identity.append("role name")
    if superuser_answer is None:
        unreported_identity.append("superuser status")
    if row_security and bypass_answer is None:
        unreported_identity.append("BYPASSRLS")
    if unreported_identity:
        return RoleFacts(
            name=name,
            read_only=False,
            evidence=(
                f"unreported {', '.join(unreported_identity)} in role identity, "
                "so read-only access is not established"
            ),
            bypasses_row_security=bypasses,
        )

    writable: list[str] = []
    unreported: list[str] = []
    for question in questions.tables:
        privileges = ", ".join(
            f"has_table_privilege({_literal(question.table)}, {_literal(privilege)}) AS "
            f"{privilege.lower()}"
            for privilege in _PG_WRITE_PRIVILEGES
        )
        granted = _ask(run_query, f"SELECT {privileges}")
        table_grants = granted[0] if granted else {}
        missing_table = [p for p in _PG_WRITE_PRIVILEGES if table_grants.get(p.lower()) is None]
        if missing_table:
            unreported.append(f"table {'/'.join(missing_table)} privileges on {question.table}")
        for privilege in _PG_WRITE_PRIVILEGES:
            if _truthy(table_grants.get(privilege.lower())):
                writable.append(f"{privilege} on {question.table}")
        column_privileges = ", ".join(
            f"has_any_column_privilege({_literal(question.table)}, {_literal(privilege)}) AS "
            f"column_{privilege.lower()}"
            for privilege in _PG_COLUMN_WRITE_PRIVILEGES
        )
        column_granted = _ask(run_query, f"SELECT {column_privileges}")
        column_grants = column_granted[0] if column_granted else {}
        missing_column = [
            p
            for p in _PG_COLUMN_WRITE_PRIVILEGES
            if column_grants.get(f"column_{p.lower()}") is None
        ]
        if missing_column:
            unreported.append(f"column {'/'.join(missing_column)} privileges on {question.table}")
        for privilege in _PG_COLUMN_WRITE_PRIVILEGES:
            if not _truthy(table_grants.get(privilege.lower())) and _truthy(
                column_grants.get(f"column_{privilege.lower()}")
            ):
                writable.append(f"{privilege} on columns of {question.table}")
    reach_clause = _pg_set_role_write_privileges(run_query, questions, writable, unreported)
    return RoleFacts(
        name=name,
        read_only=not writable and not unreported,
        evidence=(
            f"granted {'; '.join(writable)}"
            if writable
            else (
                f"unreported {'; '.join(unreported)}, so read-only access is not established"
                if unreported
                else (
                    "no INSERT, UPDATE, DELETE or TRUNCATE privilege on any in-scope mart "
                    "and no INSERT or UPDATE privilege on any of their columns" + reach_clause
                )
            )
        ),
        bypasses_row_security=bypasses,
    )


def _pg_set_role_write_privileges(
    run_query: RunQuery, questions: EnvironmentQuestions, writable: list[str], unreported: list[str]
) -> str:
    """Ask the roles `SET ROLE` reaches the same write questions, and report them.

    The privilege functions above answer for `current_user` and what it inherits, so a
    role granted to a `NOINHERIT` member — or the session user parked behind a role
    default — holds write privileges none of those questions can see, while the session
    can switch into it and write (PostgreSQL 16.15, live). The collector never switches;
    it only asks. Returns the reachability clause of the read-only evidence.
    """
    reach = _ask(run_query, _PG_REACHABLE_ROLES)
    if reach is None:
        unreported.append("roles reachable through SET ROLE")
        return ""
    if not reach:
        return "; SET ROLE reaches no other role"
    names: list[str] = []
    unnamed = 0
    superuser_reached = False
    for row in reach:
        value = row.get("name")
        # No strip: a quoted role name may carry spaces that are part of it.
        role = None if value is None or str(value) == "" else str(value)
        if role is None:
            unnamed += 1
        elif role not in names:
            names.append(role)
        flag = row.get("superuser")
        if flag is None:
            unreported.append(f"superuser status of {role or 'unknown'}")
        elif _truthy(flag):
            writable.append(f"SET ROLE to superuser {role or 'unknown'}")
            superuser_reached = True
    if unnamed:
        unreported.append(
            "name of a role reachable through SET ROLE"
            if unnamed == 1
            else f"names of {unnamed} roles reachable through SET ROLE"
        )
    if names and not superuser_reached:
        # One question per distinct mart, however many questions read that mart.
        for mart in dict.fromkeys(question.table for question in questions.tables):
            answer = _ask(run_query, _pg_reachable_privilege_sql(mart, names))
            if answer is None:
                unreported.append(f"write privileges of {', '.join(names)} on {mart}")
                continue
            rows: dict[str, dict[str, Any]] = {}
            for row in answer:
                value = row.get("name")
                # A missing, NULL or empty name stands for no requested role; indexing
                # it by `str(None)` would let the literal role name "None" take such a
                # row's answers.
                if value is None or str(value) == "":
                    continue
                rows.setdefault(str(value), row)
            for role in names:
                answer_row = rows.get(role)
                if answer_row is None:
                    unreported.append(f"write privileges of {role} on {mart}")
                    continue
                missing_table = [
                    privilege
                    for privilege in _PG_WRITE_PRIVILEGES
                    if answer_row.get(privilege.lower()) is None
                ]
                if missing_table:
                    unreported.append(
                        f"table {'/'.join(missing_table)} privileges of {role} on {mart}"
                    )
                granted_table = [
                    privilege
                    for privilege in _PG_WRITE_PRIVILEGES
                    if _truthy(answer_row.get(privilege.lower()))
                ]
                writable.extend(
                    f"{privilege} on {mart} through SET ROLE {role}" for privilege in granted_table
                )
                missing_column = [
                    privilege
                    for privilege in _PG_COLUMN_WRITE_PRIVILEGES
                    if answer_row.get(f"column_{privilege.lower()}") is None
                ]
                if missing_column:
                    unreported.append(
                        f"column {'/'.join(missing_column)} privileges of {role} on {mart}"
                    )
                # A table-level grant already covers the whole mart, so the column
                # question's true answer for it adds nothing — the same deduplication
                # the current role's own answers get.
                writable.extend(
                    f"{privilege} on columns of {mart} through SET ROLE {role}"
                    for privilege in _PG_COLUMN_WRITE_PRIVILEGES
                    if privilege not in granted_table
                    and _truthy(answer_row.get(f"column_{privilege.lower()}"))
                )
    return f", for this role or any role SET ROLE reaches ({', '.join(names)})"


def _pg_reachable_privilege_sql(mart: str, roles: list[str]) -> str:
    """The current role's write questions, asked of other roles by oid in one question."""
    privileges = ", ".join(
        f"has_table_privilege(r.oid, {_literal(mart)}, {_literal(privilege)}) AS "
        f"{privilege.lower()}"
        for privilege in _PG_WRITE_PRIVILEGES
    )
    columns = ", ".join(
        f"has_any_column_privilege(r.oid, {_literal(mart)}, {_literal(privilege)}) AS "
        f"column_{privilege.lower()}"
        for privilege in _PG_COLUMN_WRITE_PRIVILEGES
    )
    listed = ", ".join(_literal(role) for role in roles)
    return (
        f"SELECT r.rolname AS name, {privileges}, {columns} "
        f"FROM pg_roles r WHERE r.rolname IN ({listed}) ORDER BY r.rolname"
    )


def _postgres_table(
    run_query: RunQuery, question: TableQuestion, *, row_security: bool
) -> TableFacts:
    table = question.table
    schema, _, name = table.partition(".")
    present = _ask(
        run_query,
        "SELECT tablename FROM pg_tables WHERE schemaname = "
        f"{_literal(schema)} AND tablename = {_literal(name)} "
        "UNION ALL SELECT viewname FROM pg_views WHERE schemaname = "
        f"{_literal(schema)} AND viewname = {_literal(name)}",
    )
    if not present:
        return TableFacts(
            table=table, event_time_column=question.event_time_column, exists=False, readable=False
        )
    reads = _pg_column_reads(run_query, table, question.columns)
    # A kernel that predates row-level security has neither `pg_policy` nor
    # `pg_class.relrowsecurity`: no policy can exist on the mart and there is no switch.
    policies = _pg_policies(run_query, table) if row_security else []
    security = _pg_row_security(run_query, table) if row_security else None
    # Column grants alone make `has_table_privilege(…, 'SELECT')` false while the role reads
    # the mart perfectly well; the narrower question would block a release over a mart the
    # gate is about to read. Which columns are out of reach is `check_security`'s finding.
    readable = _ask(
        run_query, f"SELECT has_any_column_privilege({_literal(table)}, 'SELECT') AS ok"
    )
    if not readable or not _truthy(readable[0].get("ok")):
        return TableFacts(
            table=table,
            event_time_column=question.event_time_column,
            exists=True,
            readable=False,
            column_reads=reads,
            row_security_enabled=security,
            row_policies=policies,
        )
    reading = _ask(
        run_query,
        f"SELECT count(*) AS rows_seen, max({_ident(question.event_time_column)}) AS "
        f"max_event_time FROM {_qualified(table)}",
    )
    if not reading:
        return TableFacts(
            table=table,
            event_time_column=question.event_time_column,
            exists=True,
            readable=False,
            column_reads=reads,
            row_security_enabled=security,
            row_policies=policies,
        )
    columns = {
        str(row.get("name")): str(row.get("type"))
        for row in _ask(
            run_query,
            "SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type "
            "FROM pg_attribute a WHERE a.attrelid = "
            f"to_regclass({_literal(table)}) AND a.attnum > 0 AND NOT a.attisdropped",
        )
        or []
    }
    # Layout stays unreported here: the distribution policy and partition levels live in
    # Greenplum-only catalogs, and no Greengage stand has been available to pin them.
    return _reading(
        table,
        reading[0],
        event_time_column=question.event_time_column,
        columns=columns,
        layout=None,
        column_reads=reads,
        row_security_enabled=security,
        row_policies=policies,
    )


def _pg_column_reads(
    run_query: RunQuery, table: str, columns: dict[str, str]
) -> dict[str, bool] | None:
    """`has_column_privilege` per column the metrics read, asked by attribute number.

    Only columns the mart actually has come back: asking by name for one it does not have
    raises, and a column that is not there is `check_physical`'s finding, not this one.
    """
    if not columns:
        return {}
    wanted = ", ".join(_literal(column) for column in sorted(columns))
    rows = _ask(
        run_query,
        "SELECT a.attname AS name, has_column_privilege(a.attrelid, a.attnum, 'SELECT') AS ok "
        f"FROM pg_attribute a WHERE a.attrelid = to_regclass({_literal(table)}) "
        f"AND a.attnum > 0 AND NOT a.attisdropped AND a.attname IN ({wanted})",
    )
    if rows is None:
        return None
    return {str(row.get("name")): _truthy(row.get("ok")) for row in rows}


def _pg_policies(run_query: RunQuery, table: str) -> list[RowPolicyFacts] | None:
    """Read-affecting policies on this mart, and whether they name this session.

    `polroles` holds `0` for `PUBLIC`, and membership is a role question rather than a name
    one — `pg_has_role` is what answers it for a policy attached to a group role.
    """
    rows = _ask(
        run_query,
        "SELECT p.polname AS name, p.polpermissive AS permissive, "
        "pg_get_expr(p.polqual, p.polrelid) AS expression, "
        "(0 = ANY(p.polroles) OR EXISTS (SELECT 1 FROM pg_roles r WHERE r.oid = "
        "ANY(p.polroles) AND pg_has_role(current_user, r.oid, 'USAGE'))) AS applies "
        f"FROM pg_policy p WHERE p.polrelid = to_regclass({_literal(table)}) "
        "AND p.polcmd::text IN ('r', '*')",
    )
    if rows is None:
        return None
    return [
        RowPolicyFacts(
            name=str(row.get("name") or "policy"),
            applies_to_session=_truthy(row.get("applies")),
            restrictive=not _truthy(row.get("permissive")),
            expression=str(row.get("expression") or ""),
        )
        for row in rows
    ]


def _pg_row_security(run_query: RunQuery, table: str) -> bool | None:
    """Whether row security is switched on for the mart (`relrowsecurity`)."""
    rows = _ask(
        run_query,
        "SELECT c.relrowsecurity AS enabled FROM pg_class c WHERE c.oid = "
        f"to_regclass({_literal(table)})",
    )
    if not rows:
        return None
    return _truthy(rows[0].get("enabled"))


# --- shared -------------------------------------------------------------------


def _reading(
    table: str,
    row: dict[str, Any],
    *,
    event_time_column: str,
    columns: dict[str, str] | None = None,
    layout: LayoutFacts | None = None,
    column_reads: dict[str, bool] | None = None,
    row_security_enabled: bool | None = None,
    row_policies: list[RowPolicyFacts] | None = None,
) -> TableFacts:
    rows_seen = int(row.get("rows_seen") or 0)
    return TableFacts(
        table=table,
        event_time_column=event_time_column,
        exists=True,
        readable=True,
        rows_seen=rows_seen,
        # An empty mart has nothing to date; ClickHouse would still hand back the epoch.
        max_event_time=_moment(row.get("max_event_time")) if rows_seen else None,
        columns=columns,
        layout=layout,
        column_reads=column_reads,
        row_security_enabled=row_security_enabled,
        row_policies=row_policies,
    )


def _key_list(value: object) -> list[str]:
    """`date, store_id` as ClickHouse prints a sorting key -> the columns it names."""
    if value is None:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _string_list(value: object) -> list[str]:
    """An `Array(String)` column as the client hands it over — or as text, from a raw driver."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [part.strip().strip("'") for part in str(value).strip("[]").split(",") if part.strip()]


def _truthy(value: object) -> bool:
    """Engine booleans arrive as `True`, `1` or `'1'` depending on the driver and the column."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "t", "true"}


def _moment(value: object) -> str | None:
    """Engine value as ISO 8601 — a date stays a date, a datetime keeps its offset."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _scalar(run_query: RunQuery, sql: str) -> str | None:
    rows = _ask(run_query, sql)
    if not rows:
        return None
    value = next(iter(rows[0].values()), None)
    return None if value is None else str(value)


def _ask(run_query: RunQuery, sql: str) -> list[dict[str, Any]] | None:
    """One question, or `None` if the engine refused it.

    A refusal is a fact about the environment — that is the half of step 7 this module
    collects — so it must not end the run. Anything else (a dropped connection, a syntax
    error of ours) is not an access fact and is raised for the caller to fail on.
    """
    try:
        return list(run_query(sql))
    except Exception as error:  # re-raised below unless the engine simply refused
        if _refused(error):
            return None
        raise


def _refused(error: Exception) -> bool:
    """Whether the engine said "not for you" or "no such object" rather than falling over."""
    state = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
    if state is not None:
        return str(state) in _PG_REFUSALS
    return bool(_REFUSED_TEXT.search(str(error)))


def _literal(value: str) -> str:
    """A single-quoted literal with quotes doubled — these are catalog lookups, not user SQL."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _ch_literal(value: str) -> str:
    """A ClickHouse string literal: every backslash doubled first, then every quote.

    A role name is an arbitrary string, and a backslash left alone escapes the next
    character: `role_name IN ('wri\\ter')` named no role although `wri\\ter` held
    `ALTER UPDATE`, and `'tail\\'` was a syntax error (ClickHouse 24.8.14.39, live).
    """
    escaped = value.replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier: {name!r}")
    return f'"{name}"'


def _qualified(table: str) -> str:
    return ".".join(_ident(part) for part in table.split("."))


__all__ = ["CLICKHOUSE", "collect"]
