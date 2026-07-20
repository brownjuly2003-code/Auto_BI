"""Shared per-build EXPLAIN evidence (D-2 §3, ARCHITECTURE §3.19).

The SQL guard and the Advisor both plan chart SQL on the DWH, from modules that know
nothing about each other: the guard runs `EXPLAIN <sql>` to prove the statement resolves,
the Advisor runs the engine's evidence statement (`EXPLAIN ESTIMATE` on ClickHouse, plain
`EXPLAIN` on Greenplum) to measure the scan. On the one-shot build paths both plan the
same statement back to back, and the second round trip buys nothing.

This cache removes that duplicate. It is a dumb memo — it never talks to the DWH itself,
so it imports neither the advisor nor the guard, and the layering stays one-way. The
Advisor records what it planned; the guard only READS the record and skips its own EXPLAIN
when this exact statement already planned cleanly.

**Keyed by the exact SQL text, and that is load-bearing.** The two consumers do not always
look at the same query: the Advisor judges the *effective* query (chart filters plus the
dashboard controls that narrow it, P1-2) on the pre-normalization spec, while the guard
validates the *normalized* one (B3 label joins rewrite an FK dimension into a JOIN, B1 adds
top-N). Measured on the current code: identical for all 8 charts of an auto-overview, but
different for an LLM spec with an FK dimension or a control carrying a default. Sharing
evidence between two different statements would hand the Advisor a measurement of a query
the BI never runs — exactly the false positive P1-2 fixed — so a miss must stay a miss.

Skipping the guard's EXPLAIN never weakens invariant 3: `guard_sql` (SELECT-only parse) and
the LIMIT-ed trial run both stay unconditional, and a cache hit means the DWH already
parsed, resolved and permission-checked this very statement.

Lifetime is one build call. Nothing here is valid across builds — an estimate is a
point-in-time measurement, and the cache is deliberately not wired into the long-lived
`serve` Advisor, where the preview and the build are separate requests.

D-2 §5: the guard also records LIMIT-trial *rows* in a parallel store (same exact-SQL key).
Consumers that already paid for that trial — OWN-chart magnitude on Superset/DataLens, CLI
insights — may reuse a *complete* trial (fewer rows than the limit) instead of a second
round trip. A full-limit result is unprovably complete and is never reused for magnitude
(the «млн вместо млрд» trap). SOURCE charts keep their live probe: raw mart trial rows are
not the aggregated magnitude.
"""

from __future__ import annotations

from dataclasses import dataclass

# Same constant the guard uses for its LIMIT trial (`sql_guard.TRIAL_LIMIT`). Duplicated
# here so this module stays free of a reverse import into the guard; both sites must stay
# equal — the completeness rule is `len(rows) < TRIAL_LIMIT`.
TRIAL_LIMIT = 10


@dataclass(frozen=True)
class PlanEntry:
    """Outcome of planning one statement on the DWH."""

    ok: bool  # the engine planned it without raising (=> it resolves; the guard may skip)
    evidence: dict | None  # parsed measurement, None when the engine gave none


@dataclass(frozen=True)
class TrialEntry:
    """LIMIT-trial rows for one statement (D-2 §5), recorded by the SQL guard.

    `complete` is True only when the trial returned fewer rows than the limit — the result
    then IS the full answer set of the statement. A filled limit is unprovably complete
    and must not be reused for magnitude or insights.
    """

    rows: tuple[dict, ...]  # at most TRIAL_LIMIT dict rows as returned by RunQuery
    complete: bool


class PlanCache:
    """One plan (and optional trial) per distinct SQL for the length of a single build."""

    def __init__(self) -> None:
        self._plans: dict[str, PlanEntry] = {}
        # Parallel store: plan entries and trial rows for the same SQL coexist independently
        # (first-record-wins on each side; the guard's trial arrives after the Advisor's plan).
        self._trials: dict[str, TrialEntry] = {}

    def get(self, sql: str) -> PlanEntry | None:
        """The recorded plan for this exact statement, or None if it was never planned."""
        return self._plans.get(sql)

    def record(self, sql: str, *, ok: bool, evidence: dict | None) -> PlanEntry:
        """Remember the outcome of planning `sql`; the first record for a statement wins."""
        entry = self._plans.get(sql)
        if entry is None:
            entry = PlanEntry(ok=ok, evidence=evidence)
            self._plans[sql] = entry
        return entry

    def planned_ok(self, sql: str) -> bool:
        """True when this exact statement already planned cleanly (guard may skip EXPLAIN)."""
        entry = self._plans.get(sql)
        return entry is not None and entry.ok

    def get_trial(self, sql: str) -> TrialEntry | None:
        """LIMIT-trial rows for this exact statement, or None if never recorded."""
        return self._trials.get(sql)

    def record_trial(
        self, sql: str, rows: list[dict], *, trial_limit: int = TRIAL_LIMIT
    ) -> TrialEntry:
        """Remember the LIMIT-trial result for `sql`; the first record for a statement wins.

        Never stores more than `trial_limit` rows. Completeness is
        ``len(capped) < trial_limit`` — a filled limit is truncated / unprovable.
        """
        existing = self._trials.get(sql)
        if existing is not None:
            return existing
        capped = list(rows[:trial_limit])
        entry = TrialEntry(rows=tuple(capped), complete=len(capped) < trial_limit)
        self._trials[sql] = entry
        return entry

    def __len__(self) -> int:
        return len(self._plans)
