"""Analytics Release & Evidence Gate — orchestration only (DECISIONOPS_ARCHITECTURE §8.2).

One decision product, two semantic snapshots, a fixed sequence of typed checks, one
content-addressed evidence bundle, one verdict. Every step is a call into a module that
already exists and is tested on its own; this file decides the order, grades the findings
and writes the evidence. It reads no files and opens no sockets — loading inputs is
`decision/use_cases.py`, and what a live engine had to say arrives as facts a read-only probe
already collected (`environment.py`), never as a connection this module holds.

Two properties this module exists to keep:

* **`operational_status` and `verdict` are different answers** (invariant 8). A run that
  completes and blocks is a working gate; a run that fails is a broken one. They are
  separate fields and a report that mixes them does not validate.
* **A supplied number is not the same as a verified one.** Step 8 takes candidate results
  from a port rather than executing them, so the gate grades *what is known about where
  each number came from* as its own check (`provenance.py`). `results.reconcile` says
  whether two numbers agree; `results.provenance` says whether the second one is about this
  release at all. Neither answer stands in for the other.
* **A step that did not run says so.** §8.2 step 7 needs a running engine, and all five of
  its checks — access, freshness, the physical layer, security and cost — grade *facts* a
  read-only probe collected outside this module (`environment.py`), so a run without a
  configured engine emits them as `check.not_configured` instead of passing, and a check the
  engine answered nothing for says `not_run` rather than clean. Neither may leave a bundle
  that reads as a full verification. Step 6 compiles the reference query of each
  in-scope metric where the semantics are exact offline and names what it will not guess
  (`sql_contract.py`). The same applies downstream of a metric contract that failed to load:
  the diff would call the unparsed contract a *removed metric*, so those checks are emitted
  as `check.not_run` instead of a guess (see `UNTRUSTWORTHY_GAPS` for why that is the only
  gap that stops the sequence).

The evidence bundle carries every input and finding the verdict was computed from. The
`evidence.bundle` check itself is in the report but not in the bundle — it grades the
bundle's own re-verification, and a check cannot address the artifact it validates.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field, model_validator

from auto_bi.decision.impact import ImpactReport, ProductImpact, resolve_impact
from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.decision.validate import ValidationIssue, ValidationReport, product_rule_issues
from auto_bi.evidence.canonical import canonical_bytes, digest_bytes, sha256_digest
from auto_bi.evidence.manifest import BundleWriter, verify_bundle
from auto_bi.evidence.model import ArtifactKind, ClaimType, EvidenceClassification
from auto_bi.evidence.signing import EvidenceSigner
from auto_bi.evidence.store import ArtifactStore
from auto_bi.ir.spec import DashboardSpec, TargetBI
from auto_bi.ir.validate import validate_spec
from auto_bi.refs import ContractRef
from auto_bi.scalars import NonBlankText, Sha256Digest, Slug, Timestamp
from auto_bi.semantic.diff import SemanticChange, SemanticDiff, diff_snapshots, metric_changes
from auto_bi.semantic.gateway import MetricNode, SemanticGap, SemanticSnapshot, metric_node
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.semantic.model import SemanticModel
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel
from auto_bi.verification.bi_contract import (
    BIContractCompiler,
    CompiledBIContract,
    DataLensContractCompiler,
    SupersetContractCompiler,
)
from auto_bi.verification.environment import (
    CostBound,
    EnvironmentFacts,
    EnvironmentFinding,
    EnvironmentQuestions,
    check_access,
    check_cost,
    check_freshness,
    check_physical,
    check_security,
    denies_all_rows,
    questions_for,
    reading_for,
)
from auto_bi.verification.model import Verdict, verdict_for
from auto_bi.verification.provenance import (
    ProvenanceFinding,
    ResultAttestation,
    attest,
)
from auto_bi.verification.reconcile import (
    ComparisonOutcome,
    ComparisonReport,
    ResultSet,
    compare_results,
)
from auto_bi.verification.repository import (
    ExpectationKind,
    VerifiedAsset,
    VerifiedAssetRepository,
)
from auto_bi.verification.sql_contract import compile_metric_queries
from auto_bi.verification.summary import SummaryFacts, render_summary

API_VERSION = "auto-bi.io/v2alpha1"

#: Part of the idempotency key: a different verifier can derive a different verdict or
#: evidence artifact from the same inputs, so its bundles must not be reused (§7.6).
#: 11 — `assets.invalidated` grades a reviewed query's governance down to the field, so the
#: same inputs can now yield `needs_review` where verifier 10 saw a clean asset.
#: 12 — a bare column the statement alone cannot pin is now attributed through the governed
#: model when exactly one source in its scope declares it. Verifier 11 left every such name
#: unchecked, so the same inputs can now yield `needs_review` where it saw only an advisory
#: — and, where the name is declared, a clean asset where it flagged a partial check.
#: 13 — `dwh.cost` now stores typed exact-query execution facts next to plan facts. The same
#: inputs therefore produce a different evidence artifact even when every bound passes.
#: 14 — a run can seal its canonical manifest with an authenticated Ed25519 envelope. The
#: new run identity avoids verifier-13 bundles, and its signing identity also separates
#: unsigned, signed and rotated-key runs under verifier 14.
#: 15 — the owner summary adds a general guide to reading supplied results, provenance,
#: cost measurements and separately verified signatures. The report remains sealed before
#: bundle verification and makes no signature claim of its own.
#: 16 — a reviewed query's bare column is now resolved against the sources its own scope
#: reads and is left unchecked where a correlated scope may have read it from the query
#: around it. The same inputs therefore move against verifier 15 in three directions: a
#: bare name beside an unread CTE is graded where it was only an advisory, a subquery's
#: bare name its own table does not declare is an advisory where it was `needs_review`,
#: and a query verifier 15 graded clean may now carry an advisory, where a subquery's only
#: source is a CTE or derived table whose columns the snapshot cannot see.
#: 17 — a LATERAL body's sibling FROM items are now visible at every nested scope that
#: can read them, so any scope nested in a lateral body, including a subquery written
#: inside it, that verifier 16 reported as `asset.query_ungoverned_field` on the body's
#: own table (or left as `asset.query_fields_unchecked` for a subquery that only the
#: sibling declares) can grade clean as a column of the sibling mart. The same walk
#: also moves inputs the other way: a bare name that governed sources declare at two
#: different lateral levels, and a name read beside an ungoverned mart written at an
#: outer lateral level, now carry `asset.query_fields_unchecked` where verifier 16
#: graded clean. A name written inside a lateral body may therefore move in either
#: direction, an advisory that became `needs_review` included, so an old bundle must not
#: be read against the current rules.
#: 18 — chart resolution now reads an operator `raw_sql` tile from its SQL, so the same
#: inputs name tiles (and `chart.sql_partial` / `chart.metric_unread` holes) verifier 17
#: left as `chart.opaque_sql`. Chart resolution is evidence and does not grade, so
#: CHECK_VERSION does not move.
#: 19 — a ClickHouse dictionary call in an operator tile now names its read as
#: `<dictionary>.<attribute>` when both are literals, and otherwise leaves the tile
#: `chart.sql_partial`, so the same inputs can name a tile or a hole verifier 18 read as
#: "not this tile". Evidence only: CHECK_VERSION does not move.
#: 20 — metric-derived chart gaps (`chart.metric_unread`, `chart.metric_unknown`,
#: `chart.formula_untyped`) are raised only for a change that reaches an in-scope
#: product, so the same inputs can yield fewer gaps than verifier 19. Evidence only:
#: CHECK_VERSION does not move.
#: 21 — a dictionary call (and a projection star) inside `WITH (SELECT …) AS alias`
#: whose alias the statement reads as a column is now in a scope the statement runs,
#: so the same inputs can name a tile or a hole verifier 20 skipped as "not this tile".
#: A typed `dictGet<Type>` call with a literal dictionary and attribute now names a
#: read and carries no gap where verifier 20 left `chart.sql_partial` — fewer gaps on
#: the same inputs, the direction that matters when re-reading an old bundle. The
#: wording of the dictionary `chart.sql_partial` detail also changed, so the same
#: unnameable call produces a different detail string. Evidence only: CHECK_VERSION
#: does not move.
#: 22 — a ClickHouse `joinGet` / `joinGetOrNull` call with a literal Join table and
#: column now names a `reads_field` where verifier 21 answered "not this tile", and a
#: non-literal or non-reader `joinget*` call adds a `chart.sql_partial` clause (a gap
#: where there was none). Evidence only: CHECK_VERSION does not move.
#: 23 — more reads and gaps than 22: a CTE is now named case-insensitively and
#: regardless of declaration order (a forward reference); a query nested anywhere
#: in a running part of the statement runs, and a named CTE it names opens — that
#: covers a query inside a live scalar WITH body and a subquery sqlglot's scope
#: builder does not reach (an argument of `arrayJoin((SELECT groupArray(a) FROM
#: x))`); a scalar alias read only through a tuple access at any depth (`t.1`,
#: `t.a.b`) is read — for a star under an expression-form WITH this matches 22,
#: for a star under a subquery-form WITH and for a dictionary call it is new; a
#: database or catalog qualifier that shares a scalar alias (`dm.sales_daily.revenue`
#: beside `WITH … AS dm`) opens that WITH — more reads than 22, a documented
#: over-report.
#: Fewer reads and gaps than 22: a dead reader opens nothing; an unread
#: expression-form star is not a `chart.sql_partial`; a named CTE declared
#: inside a nested query and never named does not run. Evidence only:
#: CHECK_VERSION does not move.
#: 24 — a ClickHouse alias is not a column read, and an expression-form WITH
#: alias is read through. The same operator SQL can lose a `chart.sql_partial`
#: that named the alias as unattributed, and can gain a `reads_field` for a
#: column written only in the WITH expression. Inside the definition of SELECT
#: alias X, a WITH expression read through from it included, X is the column, so
#: a convert-and-keep-the-name tile now names that column. A name inside a query
#: nested in a referenced WITH expression is now graded (as the same subquery
#: written at the reference) and can raise the unresolved-field advisory. A
#: pathological alias chain past the read-through depth cap or expansion budget
#: is `chart.sql_partial` / `asset.query_fields_unchecked` under the alias's own
#: name. Field-level governance findings move with CHECK_VERSION 12 (fewer
#: `asset.query_ungoverned_field` for alias names; a new one when a referenced
#: WITH expression reads an undeclared column; an advisory for a name inside a
#: nested query of that expression). Both directions.
#: 25 — ClickHouse `x IN <table>` is read as `x IN (SELECT * FROM <table>)`.
#: The same operator SQL loses a false `chart.sql_partial` that named the
#: table as an unattributed column of the tile, and gains a star hole (and a
#: named-CTE open) for the table the right-hand side names. Field-level
#: governance findings move with CHECK_VERSION 13. Both directions.
#: 26 — a raw-SQL tile's `chart.metric_unread` is decided per dashboard, against
#: the changed metrics the in-scope products delivering that exact pinned
#: dashboard ref declare, so the same inputs can carry fewer `chart.metric_unread`
#: gaps than verifier 25: a dashboard delivered only by products that declare
#: none of those metrics no longer carries one. Field reads, the IR
#: `computes_metric` term match and the tile-level gaps keep the whole change.
#: Evidence only: CHECK_VERSION does not move.
#: 27 — ClickHouse inline and ARRAY JOIN aliases are query-level aliases, so the same
#: operator SQL can lose a phantom `reads_field` on the alias. An inline definition in
#: HAVING or QUALIFY that earlier attribution skipped is now graded where written, so it
#: can name a real read (for example `internal_cost_raw` in `(internal_cost_raw AS y)`).
#: ARRAY JOIN still reads its source array. Both directions, so a verifier 26 report does
#: not answer today's chart-read question.
#: 28 — unqualified inputs in HAVING and QUALIFY are reads. The only direction is added
#: evidence: a new REVIEW `asset.query_ungoverned_field`, a new advisory
#: `asset.query_fields_unchecked`, and a new chart `reads_field`; no finding is removed.
#: 29 — ClickHouse SETTINGS names and values are no longer field reads. Their phantom REVIEW
#: `asset.query_ungoverned_field`, advisory `asset.query_fields_unchecked`, chart
#: `reads_field`, and `chart.sql_partial` findings go away. `additional_table_filters` moves
#: from that phantom REVIEW to the advisory `asset.query_fields_unchecked` and a
#: `chart.sql_partial`, because its row-filter SQL is not read. Both directions.
#: 30 — A direct COUNT(*) is a row count, not a column-reading star. Governance drops the
#: advisory `asset.query_fields_unchecked` whose only reason was a COUNT(*) star; chart impact
#: adds `chart.sql_partial` for a tile that counts DISTINCT *, where it resolved to "not this
#: tile" or to its other reads alone.
#: 31 — a nested scalar WITH declaration shadows an outer one in operator SQL, so chart
#: impact drops false `reads_field` and `chart.sql_partial` evidence from an outer alias
#: that only the inner alias was read as. An actual outer read remains visible. Chart
#: evidence only; CHECK_VERSION does not move.
#: 32 — field-level governance now reports a ClickHouse `COLUMNS(...)` selector as an
#: incomplete physical-column read, where verifier 31 could return a false clean result.
#: Named column findings remain alongside the new advisory. The selector's evidence is
#: also carried through the typed `ColumnReads` remainder, so an old bundle does not answer
#: today's field-read question.
#: 33 — operator chart impact ignores `COLUMNS(...)` selectors in unread named CTEs or
#: scalar WITH bodies. A tile that verifier 32 called `chart.sql_partial` may now be read
#: in whole and ruled out for a changed metric. Selectors reached by the statement keep
#: the partial-read gap. CHECK_VERSION does not move: asset governance still scans all AST.
#: 34 — operator chart impact ignores `JOIN ... USING` keys in unread named CTEs or
#: scalar WITH bodies. A tile verifier 33 called `chart.sql_partial` may now be read
#: in whole and ruled out for a changed metric. Reachable JOINs keep the gap;
#: CHECK_VERSION does not move because asset checks are unchanged.
#: 35 — a nested named CTE shadows an outer CTE of the same name for unambiguous
#: operator SQL references. An outer CTE that only the inner reference appeared to
#: open no longer adds false chart reads or partial-read gaps. Evidence only;
#: CHECK_VERSION does not move.
#: 36 — an ordinary two-part column qualified by one explicit named-table alias
#: in its own SELECT no longer opens an equally named scalar WITH in operator SQL.
#: False chart reads and partial-read gaps disappear; CHECK_VERSION does not move.
#: 37 — an ordinary three-part column naming one unaliased named-table source
#: in its own SELECT no longer opens a scalar WITH named like its database.
#: Conflicting source names remain conservative. Evidence only; CHECK_VERSION stays 18.
#: 38 — governance now reports `JOIN ... USING` keys as an advisory remainder because the
#: shared key cannot be attributed to one table. Explicit neighboring column findings stay.
#: 39 — a PostgreSQL-family GROUP BY name that may be a SELECT output column moves REVIEW
#: `asset.query_ungoverned_field` to advisory `asset.query_fields_unchecked`; without local
#: sources it stops being a remainder. Aggregate/window outputs, expressions and qualified
#: names retain input reads; ClickHouse and chart impact are unchanged.
#: 40 — PostgreSQL-family ORDER BY / DISTINCT ON expression names become input reads,
#: potentially adding REVIEW or advisory findings. Case-folded whole output names stop
#: producing phantom input REVIEW; a set operation's ORDER BY output name stops producing
#: a sourceless advisory. A parenthesized query's trailing ORDER BY now grades names it
#: previously ignored and can add findings; GROUP BY +name moves from output-column
#: advisory to input REVIEW. Unary plus on an unaliased projection does not create a
#: plain-column output name. ClickHouse and chart impact are unchanged.
#: 41 — a parenthesized ClickHouse ARRAY JOIN expression no longer creates a phantom
#: column or crashes governance. Its outer AS replaces an inner AS, so a later use of
#: the discarded name becomes a column read and may add review; chart reads can move
#: in either direction. Parenthesized query items keep their query scope.
#: 42 — ClickHouse operator SQL rooted at INTERSECT/EXCEPT can now name field reads where
#: the same text previously left chart.opaque_sql. Set-operation LIMIT/OFFSET subqueries
#: also contribute field reads; SELECT INTO is rejected. Reviewed queries of the new root
#: shape were previously rejected; changes to existing queries are covered by check 23.
#: 43 — PostgreSQL ORDER BY / DISTINCT ON can use an unaliased COUNT output's implicit
#: name, including the left branch of a set operation, instead of a phantom input read.
#: Existing reviewed queries can lose false governance findings; check 24 records that.
#: 44 — PostgreSQL ORDER BY / DISTINCT ON can also use an unaliased SUM output's implicit
#: name, including the left branch of a set operation. Existing reviewed queries can lose
#: a phantom field REVIEW or sourceless advisory; check 25 records that.
#: 45 — PostgreSQL NATURAL JOIN leaves an advisory for keys selected from joined-source
#: output names, potentially dependent on physical schema. Existing queries can gain it;
#: check 26 records the changed meaning of assets.invalidated.
#: 46 — PostgreSQL named-table and parenthesized-JOIN aliases with column lists leave
#: references to declared output names as an advisory: their physical ordinal lineage
#: is not proven by the snapshot.
#: Existing queries can move from a false field REVIEW or clean settlement to the advisory;
#: named-table references outside the declared alias list keep ordinary physical grading.
#: 47 — PostgreSQL ORDER BY / DISTINCT ON can use the implicit output names of direct
#: unaliased AVG, MIN and MAX calls, including the left branch of a set operation.
#: Existing reviewed queries can lose a phantom field REVIEW or sourceless advisory.
#: 48 — PostgreSQL ORDER BY / DISTINCT ON and set-operation output resolution now also
#: recognize FILTER-wrapped unaliased COUNT, SUM, AVG, MIN and MAX outputs, including
#: parenthesized projections. Aggregate arguments and FILTER predicates remain reads;
#: existing reviewed queries can lose a phantom field REVIEW or sourceless advisory.
#: 49 — chart impact distinguishes ClickHouse's empty `map()` constant from a Superset
#: `{{ ... }}` template placeholder, so only the latter leaves operator SQL partially read.
#: 50 — PostgreSQL ORDER BY / DISTINCT ON and set-operation output resolution also
#: recognize Window-wrapped unaliased COUNT, SUM, AVG, MIN and MAX outputs. Window
#: arguments, FILTER predicates, PARTITION BY and inner ORDER BY expressions remain reads.
#: 51 — PostgreSQL ORDER BY / DISTINCT ON and set-operation output resolution also
#: recognize only Window-wrapped unaliased ROW_NUMBER outputs. Its PARTITION BY and inner
#: ORDER BY expressions remain reads; bare row_number() and other window functions do not.
#: 52 — PostgreSQL ORDER BY / DISTINCT ON and set-operation output resolution preserve
#: already known strong output names through CAST and postfix typecasts. Unary plus still
#: prevents the implicit name; argument, FILTER and window input reads remain graded.
#: 53 — ClickHouse reviewed-query governance reports column-reading stars only in reachable
#: query parts, using the same CTE gates as chart impact. Unread named CTE and scalar WITH
#: stars no longer leave the "it selects *" advisory; named reads and other remainders stay.
#: 54 — ClickHouse reachability binds named CTE and scalar WITH references case-sensitively.
#: Differently cased declarations no longer hide an outer read or open an unread sibling;
#: governance star reasons and chart stars, selectors, JOIN keys and getter reads can change.
#: 55 — ClickHouse governance applies CTE reachability to `COLUMNS(...)` selectors as it
#: already does for column-reading stars. The shared extractor stays whole-AST; only the
#: reviewed-query advisory narrows to selectors in query parts that actually run.
#: 56 — ClickHouse governance applies CTE reachability to `JOIN ... USING` keys. The
#: shared extractor stays whole-AST; only the reviewed-query advisory narrows to keys in
#: query parts that actually run.
#: 57 — ClickHouse table extraction and SQL guard compare CTE exemptions case-sensitively.
#: A differently cased declaration no longer hides a physical table from governance or
#: schema-RBAC, or exempts a denylisted bare table. PostgreSQL keeps its existing comparison.
#: 58 — A CTE table exemption requires its declaring query to enclose that table reference.
#: Nested or sibling declarations no longer hide physical tables from governance/schema-RBAC
#: or exempt denylisted bare names. Whole-AST extraction and name comparison stay unchanged.
#: 59 — `results.reconcile` reads supplied columns by name after the schema step.
#: Permuted columns with changed named values move from equivalent (PASS, sealed claim)
#: to different (BLOCK); expected named values or a moved key move from BLOCK to equivalent.
#: Failed candidate aggregate checks name the row by its own key. Historical bundles stay intact.
#: 60 — integer/decimal business keys and aggregate group_by values are identified by number.
#: The same inputs move both ways: rescaled keys previously different (BLOCK, row.missing /
#: row.added) now match; two written forms of one key previously paired by spelling (possibly
#: equivalent, PASS) are now insufficient / key.not_unique (REVIEW). A share_sum or
#: partition_conservation group split by scale (possibly insufficient or different) is now
#: one group. The conflicting-totals detail lists totals as numbers, not `Decimal(...)` reprs.
#: Historical bundles are not rewritten.
#: 61 — Environment readings record their event-time column; every check grades each
#: (mart, event-time column) question from its own reading. A multi-clock mart previously
#: graded from its first reading could pass with a stale clock or a denied column (now
#: freshness.stale REVIEW / security.column_unreadable BLOCK / access.table_unreadable
#: BLOCK), or report a fresh clock stale (that finding is gone). Unattributable readings
#: give access.reading_unattributed REVIEW; freshness.stale names its clock and evidence
#: facts carry event_time_column. Historical bundles are not rewritten.
#: 62 — float tolerance compares the exact binary64 difference with the exact declared
#: decimal. An absolute or relative excess smaller than binary64 rounding now gives
#: value.changed where 61 saw equivalent; an exact relative difference within tolerance
#: now stays equivalent where the rounded ratio rejected it. Grading moves both ways at
#: the rounding boundary. An infinity against a finite value or the opposite infinity was
#: equal under an absolute tolerance beyond the binary64 range, which rounded to infinity;
#: it is now value.changed. Historical bundles are not rewritten.
#: 63 — a non-finite value compares by kind. Same-sign infinities, including different
#: spellings and float overflow, move from value.changed BLOCK to equal. Expected NaN
#: moves from BLOCK to value.expected_nan REVIEW unless another finding proves a
#: difference; decimal sNaN against itself moves from written-form equal to REVIEW.
#: A float key written nan no longer reports its own cell. Candidate NaN against a
#: number, opposite infinities and infinity against a finite value stay value.changed;
#: decimal infinity against itself stays equal. Historical bundles are not rewritten.
#: 64 — the PostgreSQL-family role probe also asks column-level INSERT/UPDATE privileges
#: (has_any_column_privilege) per in-scope mart. A role verifier 63 recorded read-only with
#: only a column-level write grant is now writable: dwh.access blocks with
#: access.role_writable. Read-only role evidence names the column privileges it checked;
#: an unanswered column-write question also blocks and withholds exact runtime SQL.
#: ClickHouse is unchanged. Historical bundles are not rewritten.
#: 65 — an unanswered PostgreSQL-family table-write privilege question no longer
#: establishes read-only access when column-write answers are negative. It blocks
#: dwh.access with access.role_writable and withholds exact runtime SQL; evidence names
#: unreported table privileges. Positive column grants keep their evidence, and the
#: questions sent are unchanged. Historical bundles are not rewritten.
#: 66 — a refused or empty PostgreSQL-family role-identity answer no longer establishes
#: read-only access. The role and row-security bypass remain unknown; write-privilege
#: questions and exact runtime SQL are withheld, and dwh.access blocks with
#: access.role_writable. Its detail describes unestablished read-only access rather than
#: asserting that an unknown role can write. Historical bundles are not rewritten.
#: 67 — a partial PostgreSQL-family role-identity row, NULL identity field or empty name
#: no longer establishes read-only access. Known names and proven row-security bypass are
#: retained; unknown bypass stays null. Write-privilege questions and exact runtime SQL
#: are withheld, and dwh.access blocks. Proven superusers keep their existing evidence
#: even with partial identity rows. Historical bundles are not rewritten.
#: 68 — missing or NULL PostgreSQL-family write-privilege fields no longer establish
#: read-only access. Evidence names unreported privileges and their mart; exact runtime
#: SQL is withheld and dwh.access blocks. Proven grants retain their positive evidence.
#: SQL questions and historical bundles are unchanged.
#: 69 — incomplete ClickHouse enabled roles no longer establish grant-based read-only
#: access or effective column/policy reach. Profiles 1/2 and proven write grants retain
#: their rules. Unknown reach cannot prove classification unenforced or a hidden CH column
#: physically absent. SQL questions, public schemas and historical bundles are unchanged.
#: 70 — ClickHouse ALTER/CREATE/DROP families include canonical space-delimited leaf
#: access types, and canonical ALL includes write capabilities. Positive grants block
#: grant-based read-only and exact runtime SQL,
#: retaining their reported names in evidence. Profiles and partial revokes keep their rules.
#: 71 — physical clean evidence counts only answered column types and declared layout
#: fields on attributable, existing, readable marts. Unobserved facts no longer appear
#: verified. Grading and CHECK_VERSION are unchanged.
#: 72 — freshness clean evidence names the number of compared mart/event-time questions,
#: so a fresh readable subset no longer claims that every in-scope mart was dated.
#: Grading and CHECK_VERSION are unchanged.
#: 73 — security clean evidence counts confirmed requested column reads and observed
#: row-policy reports per mart/event-time question, without extending a partial observation
#: to the whole scope. Grading and CHECK_VERSION are unchanged.
#: 74 — clean cost evidence counts checked plan-row and exact-runtime byte/time ceilings
#: against all declared bounds, without extending partial observations to all queries.
#: Grading and CHECK_VERSION are unchanged.
#: 75 — cost facts for ineligible questions no longer produce cost.within_budget;
#: cost.not_run exposes zero eligible comparisons in the owner's What was not done section.
#: 76 — physical.not_run exposes zero actual column/layout comparisons in the owner summary.
#: 77 — security.not_run exposes zero actual column/policy observations in the owner summary.
#: 78 — absent readable marts no longer produce a fresh outcome without an age comparison.
#: 79 — the PostgreSQL-family role probe also asks which roles the session user can
#: `SET ROLE` to and their table-level and column-level write privileges per in-scope
#: mart; a superuser group stays in that question even when the current role inherits
#: it, because membership does not pass `SUPERUSER` on. A role verifier 78 recorded
#: read-only although a reachable role could write (or a reachable superuser, an
#: inheriting member of a superuser group, or an unanswered reachability question) is
#: now not read-only, and `dwh.access` blocks with `access.role_writable`; the
#: read-only evidence names the reachable roles it checked. ClickHouse is unchanged;
#: historical bundles are not rewritten.
#: 80 — the same inputs now give bi.contract evidence with an invalid list and no BI
#: payloads for delivered specs that do not validate against the candidate model.
#: 81 — snapshots carry metric freshness (native projection version 2), moving the
#: canonical digest of every native snapshot with metrics; a snapshot without metrics keeps
#: its digest. semantic.diff names relaxed freshness as review where it was
#: semantic.unchanged, and tightened or restated freshness as advisory. Fixed windows are
#: compared in exact integer seconds however many digits a component has: windows too long
#: for timedelta or for one int() conversion now grade instead of raising in semantic.diff,
#: dwh.freshness and the provenance stale-capture rule.
#: 82 — snapshots carry each metric's declared join paths (native projection version 3),
#: moving the canonical digest of every native snapshot with metrics; a snapshot without
#: metrics keeps its digest. semantic.diff names a removed metric join path as block and
#: a changed cardinality as review where it was semantic.unchanged, an added path as advisory.
#: semantic.gaps blocks on a metric join path with an endpoint the model does not declare
#: or declared with more than one cardinality.
#: 83 — a ClickHouse role granted to the session user but not enabled is now asked about
#: through system.role_grants and system.grants of the reached roles, so a write grant
#: verifier 82 left unseen on a role `SET ROLE` reaches blocks dwh.access where it passed,
#: and role names are written in those questions with every backslash and quote doubled.
#: 84 — the superuser flag of the current PostgreSQL-family role is read from pg_roles,
#: so a current role without LOGIN is graded by its privileges where verifier 83 blocked
#: as unreported, and a NOLOGIN superuser gets the existing superuser evidence.
#: 85 — contracts.validate resolves every product metric pin in the candidate snapshot,
#: so another version of a pinned metric, several contracts of it or none blocks where
#: verifier 84 passed.
#: 86 — a PostgreSQL-family kernel that predates row-level security (9.4, the kernel of
#: Greenplum 6 and Greengage 6) is graded where verifier 85 raised on the identity
#: question; the sealed environment facts gain server_version_num, so every environment
#: payload differs from verifier 85.
#: 87 — a ClickHouse own-grant row with no access type leaves the grants answer incomplete:
#: the role is not read-only by its grants and column reach is unknown, where verifier 86
#: read the row as no grant.
#: 88 — assets.invalidated resolves every metric pin of every verified asset in the
#: candidate snapshot and seals the graded pins, where verifier 87 read an asset's
#: metrics by id only.
#: 89 — contracts.validate compares the validated contract of every pinned metric with
#: the candidate's definition at the pinned version and seals the difference codes,
#: where verifier 88 compared the version alone.
#: 90 — contracts.validate answers the three validation rules that read a metric contract
#: (dimensions, status, governing policies) on the candidate's definition of every compared
#: pin and seals the rule issues, where verifier 89 sealed the answers on the validated
#: copy alone.
#: 91 — the run identity covers every static input the gate is handed: the validation
#: report, the validated contracts, both snapshots whole, the candidate model, the dashboard
#: specs and the verified assets. Verifier 90 keyed on the product and the two canonical
#: digests, so two runs that differed in any of those shared a run id, whatever each found.
#: 92 — contracts.validate grades the validation issues of the product it verifies and of
#: loading the validated paths, and seals what the rules found for another product of those
#: paths apart, as other_product_issues. Verifier 91 graded every issue of the report, so
#: another product's unresolved pin, undeclared dimension, retired or draft metric or foreign
#: governing policy blocked a release it says nothing of, or sent it to review.
#: 93 — a directory under the validated paths that the filesystem refuses to list is a
#: contract.unreadable issue of the validation report the gate grades and seals, where
#: verifier 92 read it as an empty directory: the same working tree gave a report without
#: the issue and a run that could pass. A directory of a semantic ref or of the verified
#: assets that cannot be listed now ends the run before any bundle, where verifier 92
#: froze the ref and loaded the assets without what the directory holds.
#: 94 — a YAML file that spells a surrogate in an escape is not read. Under the validated
#: paths it is a contract.unreadable issue the gate grades and seals, a metric contract of a
#: semantic ref is a gap.contract_invalid gap, and any other file of a run ends the run
#: before any bundle. Verifier 93 read the string PyYAML builds of the escape: where a
#: schema took it, the run was graded with it or ended in an exception.
#: 95 — a table a model declares with no name is a gap.table_unnamed gap of the snapshot,
#: and neither it nor its columns are in the graph. Verifier 94 kept it as a table whose id
#: is empty text: a run over two refs that held it alike was graded without the gap, and a
#: run over refs that differed in it ended in an exception while they were compared.
VERIFIER_VERSION = "95"

#: Bumped when a check's meaning changes, so an old evidence bundle is not read as if it
#: had been graded by today's rule. Adding a *new* check is not such a change: no existing
#: `CheckResult` is re-graded by it, and the verifier version is what stops a bundle written
#: before it from being reused as a complete verification.
#: 7 — `assets.invalidated` answers a wider question: an asset stops being evidence not
#: only when semantics moved the number under it, but also when its reviewed query reads a
#: mart the model does not govern or a column it does not declare. An earlier `pass` on
#: that check did not ask either question.
#: 8 — `assets.invalidated` asks that field question of more of the query: a bare column the
#: SQL alone cannot attribute is now graded whenever the governed model leaves exactly one
#: source in its scope that declares it. A `pass` under rule 7 only means no column the SQL
#: itself could attribute was undeclared, and an advisory under it may cover a name rule 8
#: grades outright.
#: 9 — byte/time cost ceilings use exact-query runtime facts. Missing evidence is now review,
#: while successful engine-enforced completion can pass a bound rule 8 called unmeasurable.
#: 10 — `assets.invalidated` asks the field question of the sources a scope actually reads,
#: and stops asking it of a bare name a correlated scope may have taken from the enclosing
#: query. An advisory under rule 9 may therefore be a review finding or a clean pass here,
#: a review finding under rule 9 may be an advisory, and a query rule 9 graded clean may
#: now carry an advisory, where a subquery's only source is a CTE or derived table whose
#: columns the snapshot cannot see.
#: 11 — `assets.invalidated` asks the field question of a LATERAL body's sibling sources
#: at every nested level that can read them. A review finding under rule 10 on a
#: set-operation or nested-lateral body, or an advisory on a subquery written inside
#: the body, may therefore be a clean pass here. A query rule 10 graded clean may now
#: carry an advisory, where a bare name is declared by governed sources at two
#: different lateral levels, or is read beside an ungoverned mart written at an outer
#: lateral level. A name written inside a lateral body can move either way under
#: rule 11 — an advisory under rule 10 may be `needs_review` here — so a `pass`
#: written under rule 10 does not answer today's question.
#: 12 — `assets.invalidated` no longer treats a ClickHouse alias as a column of the
#: table in scope, so a reviewed query that reads a number only through an alias of
#: declared columns no longer carries `asset.query_ungoverned_field`. The same
#: change grades columns written in a referenced expression-form `WITH <expr> AS a`
#: at the referencing scope, so a query that previously named the alias as the
#: undeclared field now names the column the expression reads — a new
#: `asset.query_ungoverned_field` where rule 11 was silent, or a clean pass where
#: rule 11 named the alias. Inside the definition of SELECT alias X, a WITH
#: expression read through from it included, X is the column, so convert-and-keep-
#: the-name SQL names the kept column. A name inside a query nested in that
#: expression is graded as the same subquery written at the reference and can raise
#: `asset.query_fields_unchecked`. A reference past the read-through depth cap or
#: expansion budget raises the same advisory under the alias's own name. Both
#: directions, so a `pass` written under rule 11 does not answer today's question.
#: 13 — `assets.invalidated` reads ClickHouse `x IN <table>` as
#: `x IN (SELECT * FROM <table>)`. A false `asset.query_ungoverned_field` for a
#: column nobody has (`dm.sales_daily.rev` from `WHERE region IN rev`) is gone;
#: an ungoverned table on the right of IN that rule 12 raised as an advisory or
#: nothing now carries REVIEW `asset.query_ungoverned`. Both directions, so a
#: `pass` written under rule 12 does not answer today's question.
#: 14 — `assets.invalidated` no longer treats a visible ClickHouse inline or ARRAY JOIN
#: alias as a field of the table in scope. It removes false
#: `asset.query_ungoverned_field` findings for phantom alias fields, but an inline
#: definition in HAVING or QUALIFY that earlier attribution skipped is now graded where
#: written and can name a real field (for example `internal_cost_raw` in
#: `(internal_cost_raw AS y)`). ARRAY JOIN still reads its source array. Both directions,
#: so a `pass` written under rule 13 does not answer today's question.
#: 15 — unqualified inputs in HAVING and QUALIFY are reads. The only direction is added
#: evidence: a new REVIEW `asset.query_ungoverned_field`, a new advisory
#: `asset.query_fields_unchecked`, and a new chart `reads_field`; no finding is removed.
#: 16 — ClickHouse SETTINGS names and values are no longer fields of a reviewed query. This
#: removes their phantom REVIEW `asset.query_ungoverned_field` and advisory
#: `asset.query_fields_unchecked`; `additional_table_filters` instead produces that advisory
#: because its row-filter SQL is opaque. Chart `reads_field` / `chart.sql_partial` changes
#: from the same phantom names are recorded by verifier 29. Both directions.
#: 17 — A direct COUNT(*) is a row count, not a column-reading star. Governance drops the
#: advisory `asset.query_fields_unchecked` whose only reason was a COUNT(*) star; chart impact
#: adds `chart.sql_partial` for a tile that counts DISTINCT *, where it resolved to "not this
#: tile" or to its other reads alone.
#: 18 — A ClickHouse `COLUMNS(...)` selector reads a physical set of columns that the SQL
#: text does not enumerate. Governance adds `asset.query_fields_unchecked` where check
#: version 17 could return clean or report only a named-field review; no existing field
#: finding is removed. PostgreSQL-family parsing keeps its prior result.
#: 19 — `JOIN ... USING` keys are field reads, but their shared SQL form gives no table to
#: attribute. Governance adds an advisory naming those keys on both engine families while
#: continuing to grade explicit neighboring columns.
#: 20 — a PostgreSQL-family GROUP BY name that may be a SELECT output column moves REVIEW
#: `asset.query_ungoverned_field` to advisory `asset.query_fields_unchecked`; without local
#: sources it stops being a remainder. Aggregate/window outputs, expressions and qualified
#: names retain input reads; ClickHouse and chart impact are unchanged.
#: 21 — PostgreSQL-family ORDER BY / DISTINCT ON expression names become input reads,
#: potentially adding REVIEW or advisory findings. Case-folded whole output names stop
#: producing phantom input REVIEW; a set operation's ORDER BY output name stops producing
#: a sourceless advisory. A parenthesized query's trailing ORDER BY now grades names it
#: previously ignored and can add findings; GROUP BY +name moves from output-column
#: advisory to input REVIEW. Unary plus on an unaliased projection does not create a
#: plain-column output name. ClickHouse and chart impact are unchanged.
#: 22 — governance grades parenthesized ClickHouse ARRAY JOIN expressions as their
#: unparenthesized forms, removing phantom field findings and the scope crash. An
#: outer AS discards the inner alias, so its later name may add a field finding.
#: 23 — set-operation LIMIT/OFFSET subqueries now contribute field reads, so an old
#: pass on a UNION or nested set operation may become REVIEW or advisory. SELECT INTO
#: is rejected as a write, including previously accepted SELECT and UNION statements.
#: 24 — PostgreSQL sorting by an unaliased COUNT output no longer creates a phantom
#: field REVIEW or sourceless advisory. Real COUNT argument reads remain graded.
#: 25 — PostgreSQL sorting by an unaliased SUM output no longer creates a phantom
#: field REVIEW or sourceless advisory. Real SUM argument reads remain graded.
#: 26 — PostgreSQL NATURAL JOIN is now a typed partial field-check remainder because
#: matching joined-source output names determine its implicit equality keys.
#: 27 — PostgreSQL table column alias lists are now a typed partial field-check remainder:
#: references to their output names cannot be settled against physical snapshot columns.
#: Adjacent explicit fields and named-table names outside the list retain existing grading.
#: 28 — PostgreSQL sorting by an unaliased AVG, MIN or MAX output no longer creates a
#: phantom field REVIEW or sourceless advisory. Aggregate argument reads remain graded.
#: 29 — PostgreSQL sorting by FILTER-wrapped unaliased COUNT, SUM, AVG, MIN or MAX
#: outputs no longer creates a phantom field REVIEW or sourceless advisory, including
#: DISTINCT ON, set-operation output and parenthesized forms. Arguments and predicates
#: remain graded as field reads.
#: 30 — PostgreSQL sorting by Window-wrapped unaliased COUNT, SUM, AVG, MIN or MAX
#: outputs no longer creates a phantom field REVIEW or sourceless advisory. Window
#: arguments, FILTER predicates, PARTITION BY and inner ORDER BY expressions remain
#: graded as field reads.
#: 31 — PostgreSQL sorting by Window-wrapped unaliased ROW_NUMBER outputs no longer creates
#: a phantom field REVIEW or sourceless advisory. PARTITION BY and inner ORDER BY expressions
#: remain graded as field reads; bare row_number() and other window functions retain input reads.
#: 32 — PostgreSQL sorting by a cast of an already known strong output no longer creates
#: a phantom field REVIEW or ambiguous/sourceless advisory. Cast argument reads remain
#: graded; unary plus, explicit aliases and input sort expressions retain their boundaries.
#: 33 — `assets.invalidated` no longer reports "it selects *" for an unreachable ClickHouse
#: star. Other advisory reasons and explicit table/field findings remain; PostgreSQL keeps
#: its whole-AST star rule. An earlier advisory may disappear from the same reviewed query.
#: 34 — `assets.invalidated` compares ClickHouse CTE and scalar WITH names case-sensitively
#: when deciding which stars are reachable. A missing star advisory can return, or a false
#: one disappear; explicit table/field findings and other advisory reasons keep their rules.
#: 35 — `assets.invalidated` applies the same ClickHouse CTE and scalar WITH reachability to
#: `COLUMNS(...)` selectors. An unread selector no longer leaves a false partial-field
#: advisory; reachable selectors and all other field reasons keep their contract.
#: 36 — `assets.invalidated` applies the same ClickHouse CTE and scalar WITH reachability to
#: `JOIN ... USING` keys. An unread key no longer leaves a false partial-field advisory;
#: reachable keys and all other field reasons keep their contract.
#: 37 — `assets.invalidated` no longer overlooks a physical ClickHouse table whose name
#: differs in case from a CTE declaration. A missing ungoverned-table REVIEW can return;
#: exact-case CTE references and PostgreSQL keep their existing rules.
#: 38 — `assets.invalidated` no longer overlooks physical tables matching CTE declarations
#: owned only by nested or sibling queries. A missing ungoverned-table REVIEW can return;
#: enclosing declarations, whole-AST extraction and existing name comparison keep their rules.
#: 39 — `results.reconcile` reads supplied columns by name after the schema step.
#: The same permuted inputs move both ways: changed named values previously equivalent
#: (PASS, sealed result.equivalent claim) now differ (BLOCK); expected named values or a
#: moved key previously different (BLOCK) now agree. Failed aggregates name the candidate's
#: own row key; historical bundles are not rewritten.
#: 40 — integer/decimal business keys and aggregate group_by values are identified by number.
#: A rescaled key moves from different (BLOCK, row.missing / row.added) to a matched row;
#: duplicate numbers previously paired by written form (possibly equivalent, PASS) move to
#: insufficient / key.not_unique (REVIEW). A share_sum or partition_conservation group split
#: by scale (possibly insufficient or different) now forms one group. Both directions on
#: the same inputs; historical bundles are not rewritten.
#: 41 — Environment readings record their event-time column; every check grades each
#: (mart, event-time column) question from its own reading. A multi-clock mart previously
#: graded from its first reading could pass with a stale clock or a denied column (now
#: freshness.stale REVIEW / security.column_unreadable BLOCK / access.table_unreadable
#: BLOCK), or report a fresh clock stale (that finding is gone). Unattributable readings
#: give access.reading_unattributed REVIEW; freshness.stale names its clock and evidence
#: facts carry event_time_column. Historical bundles are not rewritten.
#: 42 — results.reconcile compares the exact binary64 difference with the exact declared
#: decimal tolerance. An absolute or relative excess hidden by rounding now gives
#: value.changed where 41 saw equivalent; an exact relative difference within tolerance
#: now stays equivalent where the rounded ratio rejected it. Grading moves both ways at
#: the rounding boundary. An infinity against a finite value or the opposite infinity was
#: equal under an absolute tolerance beyond the binary64 range, which rounded to infinity;
#: it is now value.changed. Historical bundles are not rewritten.
#: 43 — results.reconcile compares a non-finite value by kind. Same-sign infinities,
#: including different spellings and float overflow, move from value.changed BLOCK to
#: equal. Expected NaN moves from BLOCK to value.expected_nan REVIEW unless another
#: finding proves a difference; decimal sNaN against itself moves from written-form
#: equal to REVIEW. A float key written nan no longer reports its own cell. Candidate
#: NaN against a number, opposite infinities and infinity against a finite value stay
#: value.changed; decimal infinity against itself stays equal. Historical bundles are
#: not rewritten.
#: 44 — dwh.access blocks with access.role_writable for a PostgreSQL-family role verifier
#: 63 recorded read-only with only a column-level write grant. The role probe also asks
#: column-level INSERT/UPDATE privileges (has_any_column_privilege) per in-scope mart;
#: read-only role evidence names the column privileges it checked. ClickHouse is unchanged.
#: An unanswered column-write question also blocks and withholds exact runtime SQL.
#: Historical bundles are not rewritten.
#: 45 — dwh.access blocks when the PostgreSQL-family table-write privilege question is
#: unanswered even if column-write answers are negative. Read-only access is unestablished,
#: exact runtime SQL is withheld and evidence names the unreported table privileges.
#: Positive grants and ClickHouse are unchanged. Historical bundles are not rewritten.
#: 46 — dwh.access blocks when the PostgreSQL-family role-identity question is unanswered.
#: An unknown superuser or BYPASSRLS answer is not false, and exact runtime SQL is withheld.
#: The finding describes unestablished read-only access for both unanswered and writable
#: roles. Answered identities and ClickHouse grading are unchanged.
#: 47 — dwh.access blocks for partial or NULL PostgreSQL-family role identities even when
#: in-scope grants would be negative. Unknown bypass is not false; proven positive flags
#: and known names are retained. Exact runtime SQL and write-privilege questions are
#: withheld; complete identities and proven superusers keep their rules.
#: 48 — dwh.access blocks for partial or NULL table/column write-privilege answers.
#: Missing fields are unknown rather than negative; complete answers retain their rules.
#: 49 — unknown ClickHouse roles block grant-based access and leave security unobserved;
#: physical absence requires known ClickHouse reach, classification requires known reach.
#: 50 — dwh.access recognizes ClickHouse ALTER/CREATE/DROP leaf write privileges;
#: SQL questions and historical bundles are unchanged.
#: 51 — the clean cost outcome requires an eligible comparison, not just supplied facts.
#: Findings, statuses and verdicts are unchanged.
#: 52 — the clean physical outcome requires an actual declared column/layout comparison.
#: 53 — the clean security outcome requires an actual requested column/policy observation.
#: 54 — the clean freshness outcome requires an existing mart eligible for dating.
#: 55 — the PostgreSQL-family role probe grades a role writable when a role the session
#: user can `SET ROLE` to holds a write privilege on an in-scope mart (or is a superuser,
#: including a superuser group the current role inherits without gaining the attribute,
#: or its reachability or write privileges went unanswered), so `dwh.access` blocks with
#: `access.role_writable` where check 54 recorded read-only; the read-only evidence names
#: the reachable roles it checked. ClickHouse is unchanged.
#: 56 — bi.contract blocks with bi.spec_invalid when a delivered spec does not validate
#: against the candidate model, where it passed or only gave bi.filter_unwired before.
#: 57 — semantic.diff grades relaxed metric freshness as review where it was
#: semantic.unchanged, tightened or restated freshness as advisory; snapshots now carry
#: freshness, moving the canonical digest of every native snapshot with metrics (native
#: projection version 2); a snapshot without metrics keeps its digest. Fixed windows are
#: compared in exact integer seconds however many digits a component has: windows too long
#: for timedelta or for one int() conversion now grade instead of raising in semantic.diff,
#: dwh.freshness and the provenance stale-capture rule.
#: 58 — snapshots carry each metric's declared join paths (native projection version 3),
#: moving the canonical digest of every native snapshot with metrics; a snapshot without
#: metrics keeps its digest. semantic.diff names a removed metric join path as block and
#: a changed cardinality as review where it was semantic.unchanged, an added path as advisory.
#: semantic.gaps blocks on a metric join path with an endpoint the model does not declare
#: or declared with more than one cardinality.
#: 59 — a ClickHouse role granted but not enabled is asked about through system.role_grants
#: and the grants of the roles SET ROLE reaches, so dwh.access blocks with
#: access.role_writable where check 58 recorded read-only over a hidden write grant;
#: role names are written with backslashes doubled.
#: 60 — the current PostgreSQL-family role's superuser flag is read from pg_roles:
#: a role without LOGIN is graded by its privileges where check 59 blocked as unreported,
#: and a NOLOGIN superuser gets the existing superuser evidence in dwh.access.
#: 61 — contracts.validate also grades product metric pins against the candidate snapshot:
#: a pass under rule 60 did not ask that question, and the detail now counts matching pins.
#: 62 — the role and the marts of a PostgreSQL-family kernel that predates row-level
#: security are graded where rule 61 produced no result, and the clean dwh.security detail
#: names that kernel: no row-policy catalog was read there.
#: 63 — dwh.access blocks and dwh.security reports security.not_observed for a ClickHouse
#: own-grants answer carrying an untyped row, where rule 62 passed access and could call
#: a column unreadable.
#: 64 — a pin that names another version, several contracts or no contract of its metric
#: blocks assets.invalidated, where rule 63 passed it as assets.valid.
#: 65 — a candidate definition that differs from the validated contract while the base
#: does not hold the validated definition is a block or an advisory of contracts.validate,
#: by the diff's classification of the differences, where rule 64 passed it as contracts.clean.
#: 66 — a candidate definition that fails a product rule the validated contract satisfies is
#: a block, a review or an advisory of contracts.validate by the rule's own severity, where
#: rule 65 weighed it by the diff's classification alone and a draft or a foreign governing
#: policy passed with an advisory.
#: 67 — an issue the validation rules give for another product of the validated paths is not
#: graded by contracts.validate, where rule 66 blocked, reviewed or advised by it.
#: 68 — contracts.validate blocks on a directory under the validated paths that could not
#: be listed, where rule 67 graded a report that read it as an empty directory.
#: 69 — contracts.validate blocks on a validated file, and semantic.gaps on a metric contract
#: of a ref, that spells a surrogate in an escape, where rule 68 graded the contract PyYAML
#: built of the file.
#: 70 — semantic.gaps blocks on a table a model declares with no name, where rule 69 graded
#: a snapshot that held it as a table.
CHECK_VERSION = "70"

#: Gaps that mean the canonical graph does not represent its source *at all*, so a diff over
#: it would be fiction: a metric contract that failed to parse is simply absent from the
#: candidate graph, and the diff would call that a removed metric. Every other blocking gap
#: (a dangling join, a metric field the model does not declare) describes a truthful graph
#: with a named hole — the diff and the impact over it are still worth reading, and the gap's
#: own `block` severity already stops the release.
UNTRUSTWORTHY_GAPS = frozenset({"gap.contract_invalid"})

_COMPILERS: dict[TargetBI, Callable[[], BIContractCompiler]] = {
    TargetBI.SUPERSET: SupersetContractCompiler,
    TargetBI.DATALENS: DataLensContractCompiler,
}


class RunStatus(StrEnum):
    """Operational lifecycle (§7.6) — never the verdict."""

    COMPLETED = "completed"
    FAILED = "failed"


class CheckStatus(StrEnum):
    PASS = "pass"
    ADVISORY = "advisory"
    REVIEW = "review"
    BLOCK = "block"
    #: The check could not be evaluated — a runtime fault, not a graded outcome.
    ERROR = "error"


class CheckCategory(StrEnum):
    CONTRACT = "contract"
    SEMANTIC = "semantic"
    IMPACT = "impact"
    VERIFICATION = "verification"
    RESULT = "result"
    EXECUTION = "execution"
    DELIVERY = "delivery"
    EVIDENCE = "evidence"


#: Severity order, for picking the finding that decided a check's status. A summary code is
#: the machine identity of an outcome, so it must name that finding rather than whichever one
#: was found first.
_FINDING_RANK = {Severity.ADVISORY: 0, Severity.REVIEW: 1, Severity.BLOCK: 2}


_STATUS_FOR_SEVERITY: dict[Severity, CheckStatus] = {
    Severity.ADVISORY: CheckStatus.ADVISORY,
    Severity.REVIEW: CheckStatus.REVIEW,
    Severity.BLOCK: CheckStatus.BLOCK,
}
_SEVERITY_FOR_STATUS: dict[CheckStatus, Severity] = {
    CheckStatus.ADVISORY: Severity.ADVISORY,
    CheckStatus.REVIEW: Severity.REVIEW,
    CheckStatus.BLOCK: Severity.BLOCK,
}


def status_for(severities: Sequence[Severity]) -> CheckStatus:
    """Worst severity present, as a check status; nothing found is a pass."""
    worst = CheckStatus.PASS
    order = [CheckStatus.PASS, CheckStatus.ADVISORY, CheckStatus.REVIEW, CheckStatus.BLOCK]
    for severity in severities:
        candidate = _STATUS_FOR_SEVERITY[severity]
        if order.index(candidate) > order.index(worst):
            worst = candidate
    return worst


class CheckResult(StrictModel):
    """One typed check (§7.7).

    `summary_code` is the stable machine identity of the outcome; `detail` is the sentence a
    human reads, and the two are not interchangeable. Timing is deliberately absent: a wall
    clock inside a content-addressed artifact makes the same inputs produce a different
    bundle every run, and observability belongs to the trace, not to the evidence (§13).
    """

    check_id: Slug
    check_version: NonBlankText
    category: CheckCategory
    subject_refs: list[str] = Field(default_factory=list)
    status: CheckStatus
    summary_code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    detail: NonBlankText
    evidence_refs: list[Slug] = Field(default_factory=list)

    def severity(self) -> Severity | None:
        return _SEVERITY_FOR_STATUS.get(self.status)


class GateReport(StrictModel):
    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="DecisionGateReport", pattern=r"^DecisionGateReport$")
    run_id: Slug
    product_ref: NonBlankText
    #: §7.6: same inputs, same verifier, same key — the only condition under which a prior
    #: terminal bundle may be reused.
    idempotency_key: Sha256Digest
    base_digest: Sha256Digest
    candidate_digest: Sha256Digest
    operational_status: RunStatus
    verdict: Verdict
    checks: list[CheckResult] = Field(default_factory=list)
    evidence_digest: Sha256Digest
    created_at: Timestamp

    @model_validator(mode="after")
    def _check_consistency(self) -> GateReport:
        if any(check.status is CheckStatus.ERROR for check in self.checks):
            if self.operational_status is not RunStatus.FAILED:
                raise ValueError(
                    "a check that errored means the run failed; a verdict computed from an "
                    "incomplete check set would not be one (invariant 8)"
                )
            return self
        severities = [
            severity for check in self.checks if (severity := check.severity()) is not None
        ]
        expected = verdict_for(severities)
        if self.verdict is not expected:
            raise ValueError(
                f"verdict {self.verdict.value} contradicts the checks (expected {expected.value})"
            )
        return self

    def blocking(self) -> list[CheckResult]:
        return [check for check in self.checks if check.status is CheckStatus.BLOCK]


class RecordedResult(StrictModel):
    """One candidate answer, with what the capturing side says about where it came from.

    The attestation is optional because a result file written before provenance existed is
    still a usable input — it is graded as `provenance.unattested` rather than rejected. It
    is not optional in the sense of "unimportant": an unattested number is exactly the case
    §8.4's reconciliation cannot see through.
    """

    result: ResultSet
    attestation: ResultAttestation | None = None


class ResultSource(Protocol):
    """Where the gate gets the candidate answer to a verified question (§8.2 step 8).

    A live implementation would compile the asset's plan and execute it under the SQL guard.
    The offline gate is handed recorded results instead, which is what lets the whole gate
    run on fixtures in CI — and what keeps `verify` from ever reaching the DWH. Because the
    number arrives rather than being computed here, what it comes with — its attestation —
    is part of the port, not a decoration on it.
    """

    def result_for(self, asset: VerifiedAsset) -> RecordedResult | None: ...


class RecordedResults:
    """Candidate results captured outside the gate, keyed by verified-asset id."""

    def __init__(self, by_asset: Mapping[str, RecordedResult]) -> None:
        self._by_asset = dict(by_asset)

    def result_for(self, asset: VerifiedAsset) -> RecordedResult | None:
        return self._by_asset.get(asset.metadata.id)


class NoResults:
    """No candidate answers available — every comparison is honestly `insufficient`."""

    def result_for(self, asset: VerifiedAsset) -> RecordedResult | None:
        return None


@dataclass(frozen=True)
class GateInputs:
    """Everything §8.1 lists as gate input, already loaded and parsed.

    Every field is part of the run identity (`idempotency_key`) except the two that hold
    what was observed when the run was made: `results` and `environment`.
    """

    product: DecisionProductSpec
    validation: ValidationReport
    base: SemanticSnapshot
    candidate: SemanticSnapshot
    #: The candidate `model.yaml` itself: the BI compilers bind charts to columns, which the
    #: canonical graph deliberately abstracts away.
    candidate_model: SemanticModel
    #: Pinned dashboard ref -> its presentation IR. A ref with no spec is a finding.
    dashboards: Mapping[str, DashboardSpec] = field(default_factory=dict)
    assets: VerifiedAssetRepository = field(default_factory=lambda: VerifiedAssetRepository([]))
    results: ResultSource = field(default_factory=NoResults)
    #: The DWH/BI environment this verdict is about; part of the idempotency key.
    environment_ref: str = ""
    #: What a read-only probe of that environment answered (§8.2 step 7). Absent means the
    #: run had no engine to ask — the checks say so rather than passing. The readings
    #: themselves stay out of the idempotency key: they are observations of a moment, and
    #: keying on them would make every run a new run.
    environment: EnvironmentFacts | None = None
    #: Metric id (without the version) -> the contract the validation report was graded on;
    #: empty means the gate was given nothing to compare a pin's contents with.
    validated_metrics: Mapping[str, MetricContract] = field(default_factory=dict)


def identity_text(value: object) -> str:
    """Exact text of a dumped gate input, written for the run identity alone.

    Evidence is stored as RFC 8785, and that form cannot serve here. The contract layer
    refuses a float, an integer past the safe range and a lone surrogate; the foreign layer
    writes two integers past the safe range as one double. The gate grades inputs that hold
    such values — a dashboard filter, a mart's row count, a synonym — so its key must
    neither stop a run on one nor give two different inputs one text. Integers and floats
    are written in hexadecimal, which has a text for every one of them and none in common;
    strings as ASCII literals, one per sequence of code points — a JSON text would spell a
    character outside the BMP as the two surrogates a string can also hold on their own;
    objects by sorted key.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):  # before int: bool is an int subclass
        return "true" if value else "false"
    if isinstance(value, int):
        return hex(value)
    if isinstance(value, float):
        return value.hex()
    if isinstance(value, str):
        return ascii(value)
    if isinstance(value, list):
        return "[" + ",".join(identity_text(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("a dumped object has string keys")
        members = (f"{key!a}:{identity_text(value[key])}" for key in sorted(value))
        return "{" + ",".join(members) + "}"
    raise TypeError(f"not a dumped JSON value: {type(value).__name__}")


def _static_inputs(inputs: GateInputs) -> dict[str, str]:
    """Content address of every input a run is handed whole, apart from the product.

    Whole, not the part a check reads: the validation report, both snapshots and every
    payload graded from the rest are sealed, so an input that differs is another bundle.
    """
    dumped: dict[str, object] = {
        "validation": inputs.validation.model_dump(mode="json"),
        "validated_metrics": {
            metric_id: contract.model_dump(mode="json")
            for metric_id, contract in inputs.validated_metrics.items()
        },
        "base": inputs.base.model_dump(mode="json"),
        "candidate": inputs.candidate.model_dump(mode="json"),
        "candidate_model": inputs.candidate_model.model_dump(mode="json"),
        "dashboards": {
            ref: spec.model_dump(mode="json") for ref, spec in inputs.dashboards.items()
        },
        "assets": [asset.model_dump(mode="json") for asset in inputs.assets.assets],
    }
    return {
        name: digest_bytes(identity_text(value).encode("ascii")) for name, value in dumped.items()
    }


def idempotency_key(inputs: GateInputs, *, signer: EvidenceSigner | None = None) -> str:
    """§7.6: every static verification input, evidence-signing identity and verifier version.

    What was observed when the run was made — the recorded results, the engine's readings,
    `created_at` — is left out: keying on it would make every run a new one.
    """
    signing_identity = (
        None if signer is None else {"algorithm": signer.algorithm, "key_id": signer.key_id}
    )
    return sha256_digest(
        {
            "product_digest": inputs.product.digest(),
            "base_digest": inputs.base.canonical_digest,
            "candidate_digest": inputs.candidate.canonical_digest,
            "policy": inputs.product.verification.policy,
            "environment_ref": inputs.environment_ref,
            "evidence_signing": signing_identity,
            "verifier_version": VERIFIER_VERSION,
            "inputs": _static_inputs(inputs),
        }
    )


def run_id_for(key: str) -> str:
    """Deterministic run id, so the same verification writes the same bundle path."""
    return f"run-{key.removeprefix('sha256:')[:16]}"


def run_gate(
    inputs: GateInputs,
    *,
    store: ArtifactStore,
    created_at: str,
    signer: EvidenceSigner | None = None,
) -> GateReport:
    """Run §8.2 end to end and return the verdict with its evidence.

    `created_at` is supplied rather than read from a clock: with a fixed timestamp the same
    inputs produce a byte-identical bundle, which is what makes a verification reproducible.
    """
    key = idempotency_key(inputs, signer=signer)
    run_id = run_id_for(key)
    product_ref = inputs.product.self_ref()
    writer = BundleWriter(
        store=store,
        run_id=run_id,
        product_ref=product_ref,
        verifier_version=VERIFIER_VERSION,
        created_at=created_at,
        signer=signer,
    )
    checks: list[CheckResult] = []

    # 1. validate input contracts
    checks.append(_check_contracts(writer, inputs, product_ref))

    # 2-3. freeze snapshots and reject blocking semantic gaps
    gaps_check, untrustworthy = _check_gaps(writer, inputs, product_ref)
    checks.append(gaps_check)

    # 4-5. semantic diff, product impact, verified-asset invalidation
    impact: ImpactReport | None = None
    if untrustworthy:
        reason = (
            "a metric contract did not load, so the snapshot does not represent its source; "
            "a diff would report the parse failure as a removed metric"
        )
        checks.extend(
            _skipped(check_id, category, product_ref, reason)
            for check_id, category in (
                ("semantic.diff", CheckCategory.SEMANTIC),
                ("impact.products", CheckCategory.IMPACT),
                ("assets.invalidated", CheckCategory.VERIFICATION),
                ("results.reconcile", CheckCategory.RESULT),
                ("results.provenance", CheckCategory.RESULT),
                # 6. the same reason: a metric whose contract did not parse is absent from
                # the graph, and compiling would report it as a metric with no definition.
                ("sql.compile", CheckCategory.EXECUTION),
            )
        )
    else:
        diff = diff_snapshots(inputs.base, inputs.candidate)
        checks.append(_check_diff(writer, diff, product_ref))
        impact = resolve_impact(
            diff,
            [inputs.product],
            dashboards=inputs.dashboards,
            # Both sides: the tile that computed the old definition is the one an owner
            # must look at when the definition moves (`decision/impact.py`).
            metrics=[*inputs.base.graph.metrics, *inputs.candidate.graph.metrics],
        )
        checks.append(_check_impact(writer, impact, product_ref))
        checks.append(_check_assets(writer, inputs, diff, product_ref))
        # 8. the numbers themselves, and where they came from. Provenance is graded first
        # only so the reconciliation's claims can cite its artifact; the report keeps the
        # order an owner reads them in — what was compared, then what is known about it.
        findings = _grade_provenance(inputs, created_at)
        provenance, provenance_ref = _check_provenance(writer, inputs, product_ref, findings)
        checks.append(_check_results(writer, inputs, product_ref, findings, provenance_ref))
        checks.append(provenance)
        # 6. reference SQL for both snapshots — compiled, never executed (no DWH here)
        checks.append(_check_sql(writer, inputs, product_ref))

    # 7. ask the engine itself: who we are on it, and how old what it holds is
    checks.extend(_check_environment(writer, inputs, product_ref, created_at))

    # 9. BI compile contracts — payloads only, never a remote create
    checks.append(_check_bi_contracts(writer, inputs, product_ref))

    # 10. assemble the bundle, then re-verify what was written. The owner-facing summary is
    # written last and from the graded checks only: it is derived evidence (§9.2), so it can
    # restate findings but must never be the first place one appears.
    writer.add_report(
        "summary.md",
        render_summary(
            SummaryFacts(
                product_ref=product_ref,
                run_id=run_id,
                base_digest=inputs.base.canonical_digest,
                candidate_digest=inputs.candidate.canonical_digest,
                created_at=created_at,
                verifier_version=VERIFIER_VERSION,
                environment_ref=inputs.environment_ref,
            ),
            checks,
            _reaching(impact) if impact is not None else (),
            impact.chart_gaps if impact is not None else (),
        ),
    )
    written = writer.write()
    problems = verify_bundle(store, run_id, verifier=signer)
    checks.append(
        CheckResult(
            check_id="evidence.bundle",
            check_version=CHECK_VERSION,
            category=CheckCategory.EVIDENCE,
            subject_refs=[product_ref],
            status=status_for([problem.severity for problem in problems]),
            summary_code="evidence.verified" if not problems else "evidence.invalid",
            detail=(
                f"bundle {written.digest} re-verified: "
                f"{len(written.bundle.artifacts)} artifact(s), {len(problems)} problem(s)"
            ),
        )
    )

    severities = [severity for check in checks if (severity := check.severity()) is not None]
    return GateReport(
        run_id=run_id,
        product_ref=product_ref,
        idempotency_key=key,
        base_digest=inputs.base.canonical_digest,
        candidate_digest=inputs.candidate.canonical_digest,
        operational_status=RunStatus.COMPLETED,
        verdict=verdict_for(severities),
        checks=checks,
        evidence_digest=written.digest,
        created_at=created_at,
    )


# --- individual steps ---------------------------------------------------------


def _check_contracts(writer: BundleWriter, inputs: GateInputs, product_ref: str) -> CheckResult:
    """Validate the paths and resolve pins by version, then compare their contents.

    Validation reads only the validated paths, not the snapshot. Snapshot lookups used
    elsewhere drop the version and select the first node, so pins must be graded here.
    The base is read only to ask whether it holds the validated definition; it is not graded.
    The product's rules that read a metric contract are answered on the candidate's
    definition of every compared pin as well: the validation report answers them on the
    validated copy alone.
    The report answers for every product of the validated paths, and the run verifies one:
    what the rules found for another product is sealed apart and not graded.
    """
    report = inputs.validation
    own_issues, other_issues = _issues_by_product(report, product_ref)
    candidate_pins, pin_issues, clauses = _candidate_metric_pins(inputs, product_ref)
    rule_issues, rule_clauses = _candidate_rule_issues(inputs)
    issues = [*own_issues, *pin_issues, *rule_issues]
    severities = [issue.severity for issue in issues]
    payload = report.model_dump(mode="json")
    payload["issues"] = [issue.model_dump(mode="json") for issue in issues]
    payload["other_product_issues"] = [issue.model_dump(mode="json") for issue in other_issues]
    payload["verdict"] = verdict_for(severities).value
    payload["candidate_pins"] = candidate_pins
    ref = writer.add_check("contracts.validate", payload)
    matches = sum(pin["outcome"] == "match" for pin in candidate_pins)
    detail = f"{len(report.metrics)} metric contract(s) resolved, {len(issues)} issue(s)"
    if other_issues:
        products = len({issue.source for issue in other_issues})
        detail += f", {len(other_issues)} issue(s) of {products} other product(s) not graded"
    detail += f"; {matches} of {len(candidate_pins)} metric pin(s) match the candidate snapshot"
    if clauses:
        detail += ": " + "; ".join(clauses)
    for clause in rule_clauses:
        detail += "; " + clause
    return CheckResult(
        check_id="contracts.validate",
        check_version=CHECK_VERSION,
        category=CheckCategory.CONTRACT,
        subject_refs=[product_ref, *inputs.product.scope.metrics],
        status=status_for(severities),
        summary_code="contracts.clean" if not issues else "contracts.issues",
        detail=detail,
        evidence_refs=[ref],
    )


def _issues_by_product(
    report: ValidationReport, product_ref: str
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    """The report's issues this run grades, and those the rules gave for another product.

    The validation rules answer for one product at a time and write its ref as the source of
    what they find, so an issue whose source is another product the report lists says
    nothing of this one. An issue of loading a file carries the file's path and is graded
    on every run: what an unread file would have held is not known. A report that does not
    list this product was not made for it, and all of it is graded.
    """
    others: set[str] = set()
    if product_ref in report.products:
        others = set(report.products) - {product_ref}
    graded = [issue for issue in report.issues if issue.source not in others]
    set_aside = [issue for issue in report.issues if issue.source in others]
    return graded, set_aside


def _candidate_metric_pins(
    inputs: GateInputs, product_ref: str
) -> tuple[list[dict[str, Any]], list[ValidationIssue], list[str]]:
    pins: list[dict[str, Any]] = []
    issues: list[ValidationIssue] = []
    clauses: list[str] = []
    untrustworthy = any(gap.code in UNTRUSTWORTHY_GAPS for gap in inputs.candidate.gaps)
    for ref in inputs.product.scope.metrics:
        parsed = ContractRef.parse(ref)
        metric_id = str(parsed.without_version())
        pinned = parsed.version
        versions = sorted(
            node.version for node in inputs.candidate.graph.metrics if node.id == metric_id
        )
        message = ""
        severity = Severity.BLOCK
        differences: list[SemanticChange] = []
        if len(versions) > 1:
            outcome = "ambiguous"
            held = ", ".join(str(version) for version in versions)
            message = (
                f"the candidate snapshot holds {len(versions)} contracts of {metric_id} "
                f"(versions {held}); the pin to version {pinned} does not resolve to one of them"
            )
            clauses.append(
                f"it holds {len(versions)} contracts of {metric_id} "
                f"(versions {held}), pinned at version {pinned}"
            )
        elif versions:
            if versions[0] == pinned:
                outcome = "match"
                differences = _content_differences(inputs, metric_id, pinned)
                if differences:
                    outcome = "content_mismatch"
                    codes = ", ".join(change.code for change in differences)
                    severity = (
                        Severity.ADVISORY
                        if all(change.severity is Severity.ADVISORY for change in differences)
                        else Severity.BLOCK
                    )
                    message = (
                        f"the candidate snapshot holds another definition of {metric_id} "
                        f"at version {pinned} than the validated contract ({codes}), "
                        "and the base snapshot does not hold the validated definition"
                    )
                    clauses.append(
                        f"it holds another definition of {metric_id} at version {pinned} "
                        f"than the validated contract ({codes})"
                    )
            else:
                outcome = "version_mismatch"
                message = (
                    f"the candidate snapshot holds version {versions[0]} of {metric_id}; "
                    f"the product pins version {pinned}"
                )
                clauses.append(
                    f"it holds version {versions[0]} of {metric_id}, pinned at version {pinned}"
                )
        elif untrustworthy:
            outcome = "not_compared"
            clauses.append(
                f"{ref} was not compared, because a metric contract of the candidate did not load"
            )
        else:
            outcome = "absent"
            message = (
                f"the candidate snapshot holds no metric {metric_id}; "
                f"the product pins version {pinned}"
            )
            clauses.append(f"it holds no {metric_id}, pinned at version {pinned}")
        pins.append({"ref": ref, "candidate_versions": versions, "outcome": outcome})
        if differences:
            pins[-1]["differences"] = [change.code for change in differences]
        if message:
            issues.append(
                ValidationIssue(
                    code=f"metric_ref.candidate_{outcome}",
                    severity=severity,
                    subject=ref,
                    source=product_ref,
                    message=message,
                )
            )
    return pins, issues, clauses


def _candidate_rule_issues(inputs: GateInputs) -> tuple[list[ValidationIssue], list[str]]:
    """The product's rules on the candidate's definition of each pinned metric.

    Answered for a pin whose contents are compared: one candidate node at the pinned version
    and a validated contract at that version. An issue the rules give on the validated
    definition as well is the validation report's own and is not repeated.
    """
    issues: list[ValidationIssue] = []
    clauses: list[str] = []
    for ref in inputs.product.scope.metrics:
        parsed = ContractRef.parse(ref)
        metric_id = str(parsed.without_version())
        contract = inputs.validated_metrics.get(metric_id)
        nodes = [node for node in inputs.candidate.graph.metrics if node.id == metric_id]
        if contract is None or contract.metadata.version != parsed.version:
            continue
        if len(nodes) != 1 or nodes[0].version != parsed.version:
            continue
        own = product_rule_issues(inputs.product, [_defined_as(contract, metric_node(contract))])
        found = [
            issue.model_copy(
                update={
                    "code": issue.code.replace(".", ".candidate_", 1),
                    "message": f"in the candidate snapshot, {issue.message}",
                }
            )
            for issue in product_rule_issues(inputs.product, [_defined_as(contract, nodes[0])])
            if issue not in own
        ]
        if found:
            codes = ", ".join(dict.fromkeys(issue.code for issue in found))
            clauses.append(
                f"the product's rules give {codes} on the candidate's definition of {ref}"
            )
            issues.extend(found)
    return issues, clauses


def _defined_as(contract: MetricContract, node: MetricNode) -> MetricContract:
    """*contract* with what the product's rules read taken from *node*.

    Governing policies are a set: one set written in another order or with a repeat reads
    the same, so the two definitions of a pin are compared in one form.
    """
    return contract.model_copy(
        update={
            "metadata": contract.metadata.model_copy(update={"status": node.status}),
            "dimensions": list(node.dimensions),
            "policy_refs": sorted(set(node.policy_refs)),
        }
    )


def _content_differences(
    inputs: GateInputs, metric_id: str, pinned: int | None
) -> list[SemanticChange]:
    """What the diff names between the validated contract and the candidate's definition.

    Empty when there is nothing to compare (the validated set holds no contract of this id
    at the pinned version), when the two are one definition, and when the base holds the
    validated definition: the difference is then the change under review, which
    `semantic.diff` names and `impact.products` grades.
    """
    contract = inputs.validated_metrics.get(metric_id)
    if contract is None or contract.metadata.version != pinned:
        return []
    validated = metric_node(contract)
    candidate = next(node for node in inputs.candidate.graph.metrics if node.id == metric_id)
    changes = metric_changes(validated, candidate)
    if not changes:
        return []
    base = [node for node in inputs.base.graph.metrics if node.id == metric_id]
    if len(base) == 1 and not metric_changes(validated, base[0]):
        return []
    return changes


def _check_gaps(
    writer: BundleWriter, inputs: GateInputs, product_ref: str
) -> tuple[CheckResult, list[SemanticGap]]:
    """Freeze both snapshots into evidence and grade what they could not represent."""
    for name, snapshot in (("base", inputs.base), ("candidate", inputs.candidate)):
        writer.add_blob(
            f"snapshot-{name}",
            kind=ArtifactKind.SEMANTIC_SNAPSHOT,
            payload=snapshot.model_dump(mode="json"),
            media_type="application/json",
        )
    gaps = [*inputs.base.gaps, *inputs.candidate.gaps]
    blocking = [gap for gap in gaps if gap.severity is Severity.BLOCK]
    untrustworthy = [gap for gap in gaps if gap.code in UNTRUSTWORTHY_GAPS]
    ref = writer.add_check(
        "semantic.gaps",
        {
            "base": [gap.model_dump(mode="json") for gap in inputs.base.gaps],
            "candidate": [gap.model_dump(mode="json") for gap in inputs.candidate.gaps],
        },
    )
    return (
        CheckResult(
            check_id="semantic.gaps",
            check_version=CHECK_VERSION,
            category=CheckCategory.SEMANTIC,
            subject_refs=[product_ref],
            status=status_for([gap.severity for gap in gaps]),
            summary_code="semantic.representable" if not gaps else "semantic.gaps_present",
            detail=(
                f"{len(gaps)} gap(s) across both snapshots, {len(blocking)} blocking"
                if gaps
                else "both snapshots represent their source completely"
            ),
            evidence_refs=[ref],
        ),
        untrustworthy,
    )


def _check_diff(writer: BundleWriter, diff: SemanticDiff, product_ref: str) -> CheckResult:
    ref = writer.add_check("semantic.diff", diff.model_dump(mode="json"))
    return CheckResult(
        check_id="semantic.diff",
        check_version=CHECK_VERSION,
        category=CheckCategory.SEMANTIC,
        subject_refs=[product_ref],
        status=status_for([change.severity for change in diff.changes]),
        summary_code="semantic.unchanged" if not diff.changes else "semantic.changed",
        detail=f"{len(diff.changes)} semantic change(s) between the snapshots",
        evidence_refs=[ref],
    )


def _reaching(impact: ImpactReport) -> list[ProductImpact]:
    """The changes that reach the product under verification.

    `resolve_impact` keeps every change of the diff, including those no in-scope product
    depends on, so the payload says what it saw. The run verifies one product, so a change
    whose `products` is empty did not reach it — grading it here, or listing it under "whose
    numbers this reaches", would report a change to somebody else's metric as this product's.
    The diff check still carries it: that is the snapshot's finding, not the product's.
    """
    return [item for item in impact.impacts if item.products]


def _check_impact(writer: BundleWriter, impact: ImpactReport, product_ref: str) -> CheckResult:
    ref = writer.add_check("impact.products", impact.model_dump(mode="json"))
    reaching = _reaching(impact)
    escalated = [
        item
        for item in reaching
        if item.severity is Severity.BLOCK and item.classified_severity is Severity.REVIEW
    ]
    # Counted over the reaching changes only, for the same reason the section is: a tile
    # named under somebody else's metric is in the payload, not in this product's count.
    tiles = {chart.ref for item in reaching for chart in item.charts}
    # Bucket by code: `chart.metric_unread` is a withheld relation on a tile that may
    # have been read in whole, `chart.sql_partial` is a partial read, `chart.opaque_sql`
    # is unread SQL. Set-difference of every dashboard-scoped gap from `tiles` called
    # the first two "not resolved at all". Metric-id and dashboard-id gaps stay out.
    # Gap subjects are scoped to dashboards of reaching changes, matching `tiles`.
    reaching_dashboards = {dashboard for item in reaching for dashboard in item.dashboards}

    def _tile_gap_subjects(code: str) -> set[str]:
        return {
            gap.subject
            for gap in impact.chart_gaps
            if gap.code == code
            and any(gap.subject.startswith(f"{dashboard}#") for dashboard in reaching_dashboards)
        }

    partial = _tile_gap_subjects("chart.sql_partial")
    opaque = _tile_gap_subjects("chart.opaque_sql")
    metric_unread = _tile_gap_subjects("chart.metric_unread")
    # The four numbers partition the tiles this product's changes touched: a tile is
    # named, or it is not named and then falls under the worst thing missing from it.
    # Without the third bucket a tile read only in part and named by no impact would be
    # in the summary's gap list and in none of these counts.
    unnamed_opaque = opaque - tiles
    unnamed_partial = partial - tiles - unnamed_opaque
    unnamed_metric = metric_unread - tiles - unnamed_opaque - unnamed_partial
    return CheckResult(
        check_id="impact.products",
        check_version=CHECK_VERSION,
        category=CheckCategory.IMPACT,
        subject_refs=[product_ref],
        status=status_for([item.severity for item in reaching]),
        summary_code="impact.none" if not reaching else "impact.reaches_product",
        detail=(
            f"{len(reaching)} of {len(impact.impacts)} change(s) reach this product; "
            f"{len(escalated)} escalated from review to block because it is governed; "
            f"{len(tiles)} delivered chart(s) named ({len(tiles & partial)} read only in part); "
            f"of the tiles not named: {len(unnamed_opaque)} whose SQL could not be read, "
            f"{len(unnamed_partial)} read only in part, "
            f"{len(unnamed_metric)} read in whole but not settled against a changed metric"
        ),
        evidence_refs=[ref],
    )


def _check_assets(
    writer: BundleWriter, inputs: GateInputs, diff: SemanticDiff, product_ref: str
) -> CheckResult:
    # Three ways an asset stops being evidence: a semantic change that moved the number
    # under it, a metric pin that does not resolve in the candidate, and a reviewed query
    # that reads a mart the model does not govern
    # (§8.4.1 anchors provenance on that query, so its attachment is worth checking).
    metric_pins, pin_findings = inputs.assets.resolve_metric_pins(inputs.candidate)
    invalidations = [
        *inputs.assets.invalidate(diff),
        *pin_findings,
        *inputs.assets.ungoverned(inputs.candidate),
    ]
    ref = writer.add_check(
        "assets.invalidated",
        {
            "assets": [item.self_ref() for item in inputs.assets.assets],
            "invalidations": [item.model_dump(mode="json") for item in invalidations],
            "metric_pins": metric_pins,
        },
    )
    worst = max(invalidations, key=lambda item: _FINDING_RANK[item.severity], default=None)
    return CheckResult(
        check_id="assets.invalidated",
        check_version=CHECK_VERSION,
        category=CheckCategory.VERIFICATION,
        subject_refs=[product_ref, *(item.self_ref() for item in inputs.assets.assets)],
        status=status_for([item.severity for item in invalidations]),
        # Same convention as `results.provenance` and the environment checks: the summary
        # code names the reason that decided the status, and the detail is the reason
        # itself. A count cannot carry that here — a moved formula and a query over an
        # ungoverned mart are the same number of invalidated assets and two different fixes.
        summary_code="assets.valid" if worst is None else worst.code,
        detail=(
            f"{len(inputs.assets.assets)} verified asset(s) still stand as evidence"
            + (
                f"; {len(metric_pins)} of {len(metric_pins)} metric pin(s) "
                "match the candidate snapshot"
                if metric_pins
                else ""
            )
            if worst is None
            else "; ".join(f"{item.asset}: {item.detail}" for item in invalidations)
        ),
        evidence_refs=[ref],
    )


def _grade_provenance(inputs: GateInputs, created_at: str) -> dict[str, list[ProvenanceFinding]]:
    """What is known about where each supplied candidate number came from (§8.4).

    Keyed by asset id, and only for assets a result was actually supplied for. An asset with
    no candidate answer is already `insufficient` in the reconciliation, and asking where a
    number that does not exist came from would let one missing result weigh twice.
    """
    graded: dict[str, list[ProvenanceFinding]] = {}
    for asset in inputs.assets.assets:
        if asset.expectation.kind is not ExpectationKind.RESULT:
            continue
        recorded = inputs.results.result_for(asset)
        if recorded is None:
            continue
        query = asset.reference_query
        graded[asset.metadata.id] = attest(
            subject=asset.self_ref(),
            attestation=recorded.attestation,
            reviewed_query_digest=query.digest() if query is not None else None,
            candidate_digest=inputs.candidate.canonical_digest,
            environment_ref=inputs.environment_ref,
            max_age=inputs.product.verification.freshness_max_age,
            created_at=created_at,
        )
    return graded


def _check_provenance(
    writer: BundleWriter,
    inputs: GateInputs,
    product_ref: str,
    graded: Mapping[str, list[ProvenanceFinding]],
) -> tuple[CheckResult, str]:
    """§8.4: can this run show where each candidate number came from?

    Separate from `results.reconcile` because it answers a different question. The
    reconciliation asks whether two numbers agree; this asks whether the second number is
    about this release at all — the same semantics, the same environment, the reviewed
    query. A result captured last month on another cluster reconciles perfectly and proves
    nothing, and only one of the two checks can see that.

    The attestation itself is evidence and goes into the bundle whether or not it is clean:
    a run that could not establish provenance must record what it was told, not only that it
    was unconvinced.
    """
    subjects: list[str] = []
    payload: list[dict[str, Any]] = []
    found: list[ProvenanceFinding] = []
    for asset in inputs.assets.assets:
        findings = graded.get(asset.metadata.id)
        if findings is None:
            continue
        recorded = inputs.results.result_for(asset)
        assert recorded is not None  # `graded` holds exactly the assets with a result
        query = asset.reference_query
        subjects.append(asset.self_ref())
        found.extend(findings)
        payload.append(
            {
                "asset": asset.self_ref(),
                "attestation": (
                    recorded.attestation.model_dump(mode="json")
                    if recorded.attestation is not None
                    else None
                ),
                "reviewed_query_digest": query.digest() if query is not None else None,
                "findings": [finding.model_dump(mode="json") for finding in findings],
            }
        )

    ref = writer.add_check("results.provenance", {"results": payload})
    if not payload:
        return (
            CheckResult(
                check_id="results.provenance",
                check_version=CHECK_VERSION,
                category=CheckCategory.RESULT,
                subject_refs=[product_ref],
                status=CheckStatus.ADVISORY,
                summary_code="provenance.no_results",
                detail="no candidate result was supplied, so there is no origin to establish",
                evidence_refs=[ref],
            ),
            ref,
        )
    established = sum(1 for item in payload if not item["findings"])
    # Same convention as the environment checks: the summary code names the finding that
    # decided the status, and the detail is what an owner has to act on — not a count that
    # sends them into the artifact to learn which of a dozen things went wrong.
    worst = max(found, key=lambda finding: _FINDING_RANK[finding.severity], default=None)
    return (
        CheckResult(
            check_id="results.provenance",
            check_version=CHECK_VERSION,
            category=CheckCategory.RESULT,
            subject_refs=[product_ref, *subjects],
            status=status_for([finding.severity for finding in found]),
            summary_code="provenance.established" if worst is None else worst.code,
            detail=(
                f"{established} of {len(payload)} candidate result(s) traced to the reviewed "
                "query, snapshot and environment of this run"
                if worst is None
                else "; ".join(f"{finding.subject}: {finding.detail}" for finding in found)
            ),
            evidence_refs=[ref],
        ),
        ref,
    )


def _check_results(
    writer: BundleWriter,
    inputs: GateInputs,
    product_ref: str,
    graded: Mapping[str, list[ProvenanceFinding]],
    provenance_ref: str,
) -> CheckResult:
    """Reconcile every asset that declares an expected result (§8.4).

    An asset the source has no answer for is `insufficient`, never a pass: a comparison that
    did not happen is not evidence that nothing moved.

    An equivalent comparison emits a claim, and the claim carries the provenance findings as
    caveats. Grading stays with `results.provenance` — the same fact must not weigh twice in
    the verdict — but a `result.equivalent` observation read on its own, out of the bundle,
    must not be able to hide that nobody could say where its number came from (invariant 5).
    """
    severities: list[Severity] = []
    comparisons: list[dict[str, Any]] = []
    equivalent: list[VerifiedAsset] = []

    for asset in inputs.assets.assets:
        if asset.expectation.kind is not ExpectationKind.RESULT:
            continue
        expected = asset.expectation.result
        assert expected is not None  # AssetExpectation validates this pairing
        recorded = inputs.results.result_for(asset)
        candidate = recorded.result if recorded is not None else None
        if candidate is None:
            severities.append(Severity.REVIEW)
            comparisons.append(
                {
                    "asset": asset.self_ref(),
                    "outcome": ComparisonOutcome.INSUFFICIENT.value,
                    "differences": [
                        {
                            "code": "result.unavailable",
                            "detail": "no candidate result was supplied for this asset",
                        }
                    ],
                }
            )
            continue
        report: ComparisonReport = compare_results(expected, candidate, asset.tolerance)
        comparisons.append({"asset": asset.self_ref(), **report.model_dump(mode="json")})
        if report.outcome is ComparisonOutcome.DIFFERENT:
            severities.append(Severity.BLOCK)
        elif report.outcome is ComparisonOutcome.INSUFFICIENT:
            severities.append(Severity.REVIEW)
        else:
            equivalent.append(asset)

    ref = writer.add_check("results.reconcile", {"comparisons": comparisons})
    for asset in equivalent:
        # A factual claim needs its evidence (invariant 5) — here, the comparison itself and
        # the attestation of the number it compared.
        writer.add_claim(
            claim_id=f"claim-{_slugify(asset.metadata.id)}",
            claim_type=ClaimType.OBSERVATION,
            summary_code="result.equivalent",
            evidence_refs=[ref, provenance_ref],
            caveats=[
                f"verified against snapshot {asset.semantic_digest}",
                *(
                    f"{finding.code}: {finding.detail}"
                    for finding in graded.get(asset.metadata.id, ())
                ),
            ],
        )
    if not comparisons:
        # Nothing was compared, so nothing was proven. Reporting that as a pass is how a
        # governed product ends up with an evidence bundle that verifies none of its numbers.
        return CheckResult(
            check_id="results.reconcile",
            check_version=CHECK_VERSION,
            category=CheckCategory.RESULT,
            subject_refs=[product_ref],
            status=CheckStatus.ADVISORY,
            summary_code="result.no_expectations",
            detail="no verified asset declares an expected result for this product",
            evidence_refs=[ref],
        )
    return CheckResult(
        check_id="results.reconcile",
        check_version=CHECK_VERSION,
        category=CheckCategory.RESULT,
        subject_refs=[product_ref],
        status=status_for(severities),
        summary_code="result.equivalent" if not severities else "result.not_equivalent",
        detail=f"{len(equivalent)} of {len(comparisons)} expected result(s) reconciled",
        evidence_refs=[ref],
    )


def _check_sql(writer: BundleWriter, inputs: GateInputs, product_ref: str) -> CheckResult:
    """§8.2 step 6: the reference query of every in-scope metric, on both snapshots.

    The check reports; it does not grade. *Why* a query moved was already decided by the
    diff and the impact graph, and grading it twice would let one change count twice toward
    a verdict. A moved query is still worth saying out loud on its own: the dialect comes
    from the mart's declared engine, so an engine swap moves the SQL without touching a
    single metric contract — a change nothing else in the sequence would mention.
    """
    refs = list(inputs.product.scope.metrics)
    base = compile_metric_queries(inputs.base, refs)
    candidate = compile_metric_queries(inputs.candidate, refs)
    on_both = base.by_metric().keys() & candidate.by_metric().keys()
    moved = sorted(
        ref for ref in on_both if base.by_metric()[ref].digest != candidate.by_metric()[ref].digest
    )
    findings = [*base.findings, *candidate.findings]

    ref_id = writer.add_check(
        "sql.compile",
        {
            "base": base.model_dump(mode="json"),
            "candidate": candidate.model_dump(mode="json"),
            "moved": moved,
        },
    )
    if not candidate.queries:
        summary_code = "sql.not_compiled"
    elif moved:
        summary_code = "sql.changed"
    elif findings:
        summary_code = "sql.partially_compiled"
    else:
        summary_code = "sql.compiled"
    severities = [finding.severity for finding in findings]
    if moved:
        severities.append(Severity.ADVISORY)
    return CheckResult(
        check_id="sql.compile",
        check_version=CHECK_VERSION,
        category=CheckCategory.EXECUTION,
        subject_refs=[product_ref, *refs],
        status=status_for(severities),
        summary_code=summary_code,
        detail=(
            f"{len(candidate.queries)} of {len(refs)} metric(s) compiled to a reference query; "
            f"{len(moved)} moved between the snapshots, {len(findings)} not compiled offline"
        ),
        evidence_refs=[ref_id],
    )


def _check_bi_contracts(writer: BundleWriter, inputs: GateInputs, product_ref: str) -> CheckResult:
    """Validate every delivered spec against the candidate model before compiling (§8.5).

    As the build path does before any BI call (invariant 2), an invalid spec is named,
    not compiled. Valid specs produce payloads only.
    """
    severities: list[Severity] = []
    compiled: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    missing: list[str] = []

    for delivery in inputs.product.delivery.dashboards:
        spec = inputs.dashboards.get(delivery.ref)
        if spec is None:
            missing.append(delivery.ref)
            severities.append(Severity.REVIEW)
            continue
        errors = validate_spec(spec, inputs.candidate_model)
        if errors:
            invalid.append({"dashboard": delivery.ref, "errors": errors})
            severities.append(Severity.BLOCK)
            continue
        contract = _compile(spec, inputs.candidate_model)
        severities.extend(finding.severity for finding in contract.findings)
        compiled.append({"dashboard": delivery.ref, **contract.model_dump(mode="json")})
        _write_bi_payloads(writer, delivery.ref, contract)

    ref = writer.add_check(
        "bi.contract",
        {"compiled": compiled, "invalid": invalid, "missing": missing},
    )
    return CheckResult(
        check_id="bi.contract",
        check_version=CHECK_VERSION,
        category=CheckCategory.DELIVERY,
        subject_refs=[product_ref, *(item.ref for item in inputs.product.delivery.dashboards)],
        status=status_for(severities),
        summary_code=(
            "bi.spec_invalid" if invalid else "bi.spec_missing" if missing else "bi.compiled"
        ),
        detail=(
            f"{len(compiled)} dashboard(s) compiled without touching the BI"
            + (
                "; not compiled, the spec does not validate against the candidate semantic model: "
                + ", ".join(item["dashboard"] for item in invalid)
                if invalid
                else ""
            )
            + (f"; no spec supplied for {', '.join(missing)}" if missing else "")
        ),
        evidence_refs=[ref],
    )


def _compile(spec: DashboardSpec, model: SemanticModel) -> CompiledBIContract:
    compiler = _COMPILERS[spec.target_bi]()
    return compiler.compile_contract(spec, model)


def _write_bi_payloads(
    writer: BundleWriter, dashboard_ref: str, contract: CompiledBIContract
) -> None:
    """Store each compiled payload under its own content address.

    `allow_float=True`: these are foreign JSON documents (Superset form_data, DataLens
    configs) whose numbers are IEEE-754 by construction — the strict decimal-string rule
    governs our contracts, not theirs.
    """
    prefix = _slugify(dashboard_ref)
    for artifact in contract.artifacts:
        writer.add_blob(
            f"bi-{prefix}-{artifact.artifact_id}",
            kind=ArtifactKind.BI_CONTRACT,
            payload=canonical_bytes(artifact.payload, allow_float=True),
            media_type="application/json",
            classification=EvidenceClassification.INTERNAL,
        )


def _check_environment(
    writer: BundleWriter, inputs: GateInputs, product_ref: str, created_at: str
) -> list[CheckResult]:
    """§8.2 step 7: what a read-only look at the engine says about this release.

    The gate grades facts rather than holding the connection (`environment.py` explains why
    each engine is asked its own question). Without a probe there is nothing to grade, and
    all five checks say `check.not_configured` — an answer, not a pass.
    """
    facts = inputs.environment
    if facts is None:
        reason = (
            "no engine was configured for this run, so the release was not verified against "
            "a live environment (§8.2 step 7)"
        )
        return [
            _not_configured("dwh.access", product_ref, reason),
            _not_configured("dwh.freshness", product_ref, reason),
            _not_configured("dwh.physical", product_ref, reason),
            _not_configured("dwh.security", product_ref, reason),
            _not_configured("dwh.cost", product_ref, reason),
        ]

    questions = questions_for(inputs.product, inputs.candidate)
    access = check_access(questions, facts)
    freshness = check_freshness(
        questions,
        facts,
        max_age=inputs.product.verification.freshness_max_age,
        created_at=created_at,
    )
    return [
        _environment_check(
            writer,
            "dwh.access",
            questions,
            facts,
            access,
            product_ref,
            clean=f"{facts.role.name} reads every in-scope mart on {facts.target}, and only reads",
            summary_clean="access.read_only",
        ),
        _environment_check(
            writer,
            "dwh.freshness",
            questions,
            facts,
            freshness,
            product_ref,
            # Nothing readable to date is not a fresh source: `check_access` already blocked
            # on the mart itself, and calling this half clean would read as a second pass.
            clean=(
                _freshness_clean(questions, facts, inputs.product.verification.freshness_max_age)
                if _dated(questions, facts)
                else f"no in-scope mart could be dated on {facts.target}, so nothing was aged"
            ),
            summary_clean=("freshness.fresh" if _dated(questions, facts) else "freshness.not_run"),
        ),
        _environment_check(
            writer,
            "dwh.physical",
            questions,
            facts,
            check_physical(questions, facts),
            product_ref,
            clean=(
                _physical_clean(questions, facts)
                if _readable(questions, facts)
                else f"no in-scope mart could be read on {facts.target}, so nothing was compared"
            ),
            summary_clean=(
                "physical.as_declared"
                if any(_physical_coverage(questions, facts)[:2])
                else "physical.not_run"
            ),
        ),
        _environment_check(
            writer,
            "dwh.security",
            questions,
            facts,
            check_security(questions, facts),
            product_ref,
            clean=(
                _security_clean(questions, facts)
                if _readable(questions, facts)
                else f"no in-scope mart could be read on {facts.target}, so nothing was asked "
                "about what narrows it"
            ),
            summary_clean=(
                "security.unfiltered"
                if any(_security_coverage(questions, facts)[:2])
                else "security.not_run"
            ),
        ),
        _environment_check(
            writer,
            "dwh.cost",
            questions,
            facts,
            check_cost(questions, facts),
            product_ref,
            clean=_cost_clean(questions, facts),
            summary_clean=_cost_summary(questions, facts),
        ),
    ]


def _cost_summary(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> str:
    """Three different clean answers, because they mean three different things: nothing was
    declared to check, nothing could be measured, or the ceilings held."""
    if not questions.plans:
        return "cost.no_budget_declared"
    return "cost.within_budget" if _priced(questions, facts) else "cost.not_run"


def _priced(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> bool:
    """Whether any plan or exact runtime query was checked against a declared ceiling.

    A product whose metrics declare no budget, and one whose every estimate came back
    silent, must not read the same as one the engine priced — the first has nothing to check,
    and the second is the sentence this report must never contain.
    """
    for question in questions.plans:
        table = reading_for(question, questions, facts.tables)
        if (
            table is None
            or not table.exists
            or not table.readable
            or denies_all_rows(table, facts.role)
            or not question.sql
        ):
            continue
        plan = facts.plan(question.metric_ref)
        if (
            question.budgets.max_scanned_rows is not None
            and plan is not None
            and plan.scanned_rows is not None
        ):
            return True
        execution = facts.execution(question.metric_ref)
        if execution is None:
            continue
        if question.budgets.max_scanned_bytes is not None and (
            execution.scanned_bytes is not None
            or CostBound.SCANNED_BYTES in execution.checked_bounds
            or execution.exceeded_bound is CostBound.SCANNED_BYTES
        ):
            return True
        if question.budgets.timeout_ms is not None and (
            execution.elapsed_ms is not None
            or CostBound.TIMEOUT in execution.checked_bounds
            or execution.exceeded_bound is CostBound.TIMEOUT
        ):
            return True
    return False


def _cost_clean(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> str:
    if not questions.plans:
        return "no in-scope metric declares a cost ceiling, so no query was priced"
    totals = {"max_scanned_rows": 0, "max_scanned_bytes": 0, "timeout_ms": 0}
    checked = dict.fromkeys(totals, 0)
    for question in questions.plans:
        for name in totals:
            totals[name] += getattr(question.budgets, name) is not None
        table = reading_for(question, questions, facts.tables)
        if (
            table is None
            or not table.exists
            or not table.readable
            or denies_all_rows(table, facts.role)
            or not question.sql
        ):
            continue
        plan = facts.plan(question.metric_ref)
        if question.budgets.max_scanned_rows is not None and plan is not None:
            checked["max_scanned_rows"] += plan.scanned_rows is not None
        execution = facts.execution(question.metric_ref)
        if execution is None:
            continue
        for bound, measured in (
            (CostBound.SCANNED_BYTES, execution.scanned_bytes),
            (CostBound.TIMEOUT, execution.elapsed_ms),
        ):
            if getattr(question.budgets, bound.value) is not None:
                checked[bound.value] += (
                    measured is not None
                    or bound in execution.checked_bounds
                    or execution.exceeded_bound is bound
                )
    coverage = "complete" if checked == totals else "incomplete"
    result = (
        f"cost comparison on {facts.target}: {checked['max_scanned_rows']} of "
        f"{totals['max_scanned_rows']} declared scanned-row ceilings checked from plans; "
        f"{checked['max_scanned_bytes']} of {totals['max_scanned_bytes']} declared scanned-byte "
        f"ceilings and {checked['timeout_ms']} of {totals['timeout_ms']} declared timeout "
        f"ceilings checked from exact-query runtime evidence; coverage is {coverage}"
    )
    if not any(checked.values()):
        return f"{result}; no cost comparison was made"
    return f"{result}; checked bounds are within their declared ceilings"


def _physical_coverage(
    questions: EnvironmentQuestions, facts: EnvironmentFacts
) -> tuple[int, int, int, int]:
    """Return compared column/layout counts, then their declared totals, per question."""
    columns_total = columns_compared = layout_total = layout_compared = 0
    for question in questions.tables:
        columns_total += len(question.columns)
        declared_layout = [
            name
            for name, declared in (
                ("table_engine", question.layout.table_engine),
                ("sorting_key", ", ".join(question.layout.sorting_key)),
                ("partition_key", question.layout.partition_key),
                ("distribution_key", ", ".join(question.layout.distribution_key)),
            )
            if declared
        ]
        layout_total += len(declared_layout)
        table = reading_for(question, questions, facts.tables)
        if table is None or not table.exists or not table.readable:
            continue
        if table.columns is not None:
            columns_compared += sum(
                table.columns.get(column) is not None for column in question.columns
            )
        if table.layout is not None:
            layout_compared += sum(
                getattr(table.layout, name) is not None for name in declared_layout
            )
    return columns_compared, layout_compared, columns_total, layout_total


def _physical_clean(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> str:
    columns_compared, layout_compared, columns_total, layout_total = _physical_coverage(
        questions, facts
    )
    coverage = (
        "complete"
        if columns_compared == columns_total and layout_compared == layout_total
        else "incomplete"
    )
    result = (
        f"physical comparison on {facts.target}: {columns_compared} of {columns_total} "
        f"declared column types and {layout_compared} of {layout_total} declared layout "
        f"fields compared; coverage is {coverage}"
    )
    if not columns_compared and not layout_compared:
        return f"{result}; no physical comparison was made"
    return f"{result}; no differences found in the compared facts"


def _security_coverage(
    questions: EnvironmentQuestions, facts: EnvironmentFacts
) -> tuple[int, int, int, int]:
    """Return confirmed column/policy counts, then their requested totals, per question."""
    columns_total = columns_confirmed = policies_observed = 0
    for question in questions.tables:
        columns_total += len(question.columns)
        table = reading_for(question, questions, facts.tables)
        if table is None or not table.exists or not table.readable:
            continue
        if table.column_reads is not None:
            columns_confirmed += sum(
                table.column_reads.get(column) is True for column in question.columns
            )
        policies_observed += table.row_policies is not None
    return columns_confirmed, policies_observed, columns_total, len(questions.tables)


def _security_clean(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> str:
    """Limit clean evidence to the permissions and policy reports actually observed."""
    columns_confirmed, policies_observed, columns_total, policies_total = _security_coverage(
        questions, facts
    )
    coverage = (
        "complete"
        if columns_confirmed == columns_total and policies_observed == policies_total
        else "incomplete"
    )
    result = (
        f"security observation on {facts.target}: {columns_confirmed} of {columns_total} "
        f"requested column reads confirmed and {policies_observed} of {policies_total} "
        "row-policy questions answered per in-scope mart/event-time question; "
        f"coverage is {coverage}"
    )
    if facts.predates_row_security():
        result += (
            f"; {facts.target} reports server_version_num {facts.server_version_num}, "
            "a kernel without row-level security, so no row-policy catalog was read"
        )
    if not columns_confirmed and not policies_observed:
        return f"{result}; no security observation was made"
    return f"{result}; no restrictions found in the observed facts for {facts.role.name}"


def _readable(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> bool:
    """Whether the probe could actually read any mart the product asks about."""
    return any(
        (table := reading_for(item, questions, facts.tables)) is not None and table.readable
        for item in questions.tables
    )


def _freshness_clean(questions: EnvironmentQuestions, facts: EnvironmentFacts, max_age: str) -> str:
    """Used only without freshness findings, so each eligible question's age was compared."""
    compared = sum(
        (table := reading_for(item, questions, facts.tables)) is not None
        and table.exists
        and table.readable
        and not denies_all_rows(table, facts.role)
        for item in questions.tables
    )
    total = len(questions.tables)
    coverage = "complete" if compared == total else "incomplete"
    result = (
        f"freshness comparison on {facts.target}: {compared} of {total} in-scope "
        f"mart/event-time questions compared; coverage is {coverage}"
    )
    if not compared:
        return f"{result}; no freshness comparison was made"
    return f"{result}; compared ages are inside the {max_age} this product declares"


def _dated(questions: EnvironmentQuestions, facts: EnvironmentFacts) -> bool:
    """Whether any mart could be aged at all.

    A mart row security empties is readable and still says nothing about how fresh its source
    is — `check_security` blocks on that, and `check_freshness` stays quiet so one fact is not
    graded twice. Reusing `_readable` here would print "every mart is inside the window" about
    marts nothing was read from, which is the one sentence this report must never contain.
    """
    return any(
        (table := reading_for(item, questions, facts.tables)) is not None
        and table.exists
        and table.readable
        and not denies_all_rows(table, facts.role)
        for item in questions.tables
    )


def _environment_check(
    writer: BundleWriter,
    check_id: str,
    questions: EnvironmentQuestions,
    facts: EnvironmentFacts,
    findings: list[EnvironmentFinding],
    product_ref: str,
    *,
    clean: str,
    summary_clean: str,
) -> CheckResult:
    """One graded look at the environment, with the readings it stood on in the bundle."""
    ref = writer.add_check(
        check_id,
        {
            "questions": questions.model_dump(mode="json"),
            "facts": facts.model_dump(mode="json"),
            "findings": [item.model_dump(mode="json") for item in findings],
        },
    )
    severities = [item.severity for item in findings]
    # The summary code is the machine identity of the outcome, so it names the finding that
    # decided the status — not whichever one happened to be found first.
    worst = max(findings, key=lambda item: _FINDING_RANK[item.severity], default=None)
    summary_code = summary_clean if worst is None else worst.code
    return CheckResult(
        check_id=check_id,
        check_version=CHECK_VERSION,
        category=CheckCategory.EXECUTION,
        subject_refs=[product_ref, *sorted({item.subject for item in findings})],
        status=status_for(severities),
        summary_code=summary_code,
        detail=clean if not findings else "; ".join(item.detail for item in findings),
        evidence_refs=[ref],
    )


def _not_configured(check_id: str, product_ref: str, detail: str) -> CheckResult:
    """A check with an implementation that this run had no engine to point it at."""
    return CheckResult(
        check_id=check_id,
        check_version=CHECK_VERSION,
        category=CheckCategory.EXECUTION,
        subject_refs=[product_ref],
        status=CheckStatus.ADVISORY,
        summary_code="check.not_configured",
        detail=detail,
    )


def _skipped(check_id: str, category: CheckCategory, product_ref: str, reason: str) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        check_version=CHECK_VERSION,
        category=category,
        subject_refs=[product_ref],
        status=CheckStatus.REVIEW,
        summary_code="check.not_run",
        detail=reason,
    )


def _slugify(ref: str) -> str:
    """A ref (`decision://sales/weekly_margin@1`) as an artifact-id slug."""
    out = "".join(char if char.isalnum() else "-" for char in ref.lower())
    return "-".join(part for part in out.split("-") if part) or "ref"
