"""Verified Asset Repository (DECISIONOPS_ARCHITECTURE §7.5).

A verified asset is a reviewed question with a known-good answer: canonical question and
paraphrases, intent, the metrics it is built on, a reference plan, the expected result (or
the invariants that must hold, or the clarification/refusal a negative case must produce),
the tolerance the comparison runs under, and who reviewed it when.

Three rules keep an asset trustworthy rather than merely present:

* it records the semantic digest it was verified against, so an asset carried over from a
  different snapshot is *stale*, not evidence;
* a semantic change that alters meaning invalidates it (§8.2 step 5). An expectation nobody
  re-checked after a formula change is worse than no expectation — it looks like proof;
* the reviewed query is part of the asset, not a note beside it (`ReferenceQuery`). The
  gate is handed candidate numbers rather than executing them, so the reviewed query's
  digest is the only thing an attestation can be compared against — an asset without one
  can hold an expectation nobody can tie a number to.

Assets are reviewed artifacts in Git. A production interaction only ever enters a redacted
review queue, and an expected result is never fed back into an LLM context (§7.5).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from auto_bi.agent.sql_guard import (
    AmbiguousRef,
    ColumnReads,
    SQLGuardError,
    TableRef,
    extract_column_refs,
    extract_table_refs,
    guard_sql,
)
from auto_bi.decision.spec import RiskTier
from auto_bi.engine import is_known_engine, sqlglot_dialect
from auto_bi.evidence.canonical import sha256_digest
from auto_bi.refs import AssetId, ContractRef, MetricRef
from auto_bi.scalars import MAX_SAFE_INT, NonBlankText, Sha256Digest, Timestamp
from auto_bi.semantic.diff import SemanticDiff
from auto_bi.semantic.gateway import FieldNode, SemanticSnapshot
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel
from auto_bi.verification.reconcile import ResultSet, ResultTolerance

API_VERSION = "auto-bi.io/v2alpha1"

_YAML_SUFFIXES = (".yaml", ".yml")
# Severities that mean "the number may have moved"; an advisory difference does not.
_INVALIDATING = (Severity.REVIEW, Severity.BLOCK)


class AssetIntent(StrEnum):
    METRIC_VALUE = "metric_value"
    BREAKDOWN = "breakdown"
    TREND = "trend"
    COMPARISON = "comparison"
    #: Negative cases: the right answer is a question back, or a refusal.
    CLARIFICATION = "clarification"
    REFUSAL = "refusal"


class ExpectationKind(StrEnum):
    RESULT = "result"
    INVARIANTS = "invariants"
    CLARIFICATION = "clarification"
    REFUSAL = "refusal"


class AssetExpectation(StrictModel):
    kind: ExpectationKind
    result: ResultSet | None = None
    invariants: list[str] = Field(default_factory=list)
    clarification: str = ""
    refusal_reason: str = ""

    @model_validator(mode="after")
    def _check_payload(self) -> AssetExpectation:
        if self.kind is ExpectationKind.RESULT and self.result is None:
            raise ValueError("expectation of kind result requires a `result`")
        if self.kind is ExpectationKind.INVARIANTS and not self.invariants:
            raise ValueError("expectation of kind invariants requires at least one invariant")
        if self.kind is ExpectationKind.CLARIFICATION and not self.clarification.strip():
            raise ValueError("expectation of kind clarification requires the expected question")
        if self.kind is ExpectationKind.REFUSAL and not self.refusal_reason.strip():
            raise ValueError("expectation of kind refusal requires the expected refusal reason")
        return self


class ReferenceQuery(StrictModel):
    """The reviewed SQL that answers this asset's question.

    Free-text `reference_plan` says *how* the answer is meant to be obtained; this says it
    exactly enough to be a content address. That is the whole point of it: a candidate
    result attests to the query that produced it, and without a reviewed query on the asset
    there is nothing for that attestation to be compared against (`provenance.py`).

    The SQL is guarded here, at the contract boundary, rather than wherever it is later
    read: a verified asset carrying an `INSERT` is a broken reviewed artifact, and invariant
    7 does not become optional because nobody has executed it yet. The engine is required
    and must be one the seam knows — `sqlglot_dialect` substitutes the v1 engine for an
    unfamiliar name, and guarding PostgreSQL text with the ClickHouse grammar would either
    reject valid SQL or, worse, accept what it failed to parse as a write.
    """

    engine: NonBlankText
    sql: NonBlankText

    @model_validator(mode="after")
    def _check_sql(self) -> ReferenceQuery:
        if not is_known_engine(self.engine):
            raise ValueError(
                f"unknown engine {self.engine!r}: a reference query must name an engine the "
                "seam knows, so its SQL is guarded with the grammar it was written in"
            )
        try:
            guard_sql(self.sql, dialect=sqlglot_dialect(self.engine))
        except SQLGuardError as exc:
            raise ValueError(f"reference query is not a plain SELECT: {exc}") from None
        return self

    def digest(self) -> str:
        """Content address of the reviewed query — what an attestation is compared against.

        Over the engine as well as the text, because one statement means two things on two
        engines. The canonical form is RFC 8785 over `{"engine": …, "sql": …}` so that a
        capture runner outside this codebase can compute the same digest from the same two
        strings (§9.1).
        """
        return sha256_digest({"engine": self.engine, "sql": self.sql})


class AssetReview(StrictModel):
    reviewer: NonBlankText
    reviewed_at: Timestamp
    rationale: NonBlankText


class AssetMetadata(StrictModel):
    id: AssetId
    version: int = Field(ge=1, le=MAX_SAFE_INT)
    owner: NonBlankText
    risk_tier: RiskTier


class VerifiedAsset(StrictModel):
    api_version: str = Field(default=API_VERSION, pattern=r"^auto-bi\.io/v2alpha1$")
    kind: str = Field(default="VerifiedAsset", pattern=r"^VerifiedAsset$")
    metadata: AssetMetadata
    question: NonBlankText
    paraphrases: list[str] = Field(default_factory=list)
    intent: AssetIntent
    metric_refs: list[MetricRef] = Field(default_factory=list)
    expectation: AssetExpectation
    tolerance: ResultTolerance = Field(default_factory=ResultTolerance)
    reference_plan: str = ""
    #: The reviewed SQL a candidate result must attest to. Optional, because an asset whose
    #: expectation is a clarification or a refusal has no query at all — but an expected
    #: *result* without one leaves the number's origin uncheckable (`provenance.py`).
    reference_query: ReferenceQuery | None = None
    #: The canonical digest of the semantic snapshot this asset was verified against.
    semantic_digest: Sha256Digest
    review: AssetReview

    @model_validator(mode="after")
    def _check_shape(self) -> VerifiedAsset:
        negative = self.intent in (AssetIntent.CLARIFICATION, AssetIntent.REFUSAL)
        if not negative and not self.metric_refs:
            raise ValueError("a positive verified asset must name its metric_refs")
        if self.expectation.kind is ExpectationKind.RESULT and not self.tolerance.business_key:
            raise ValueError(
                "an expected result needs a tolerance.business_key: without one every "
                "comparison is `insufficient` by construction"
            )
        return self

    def metric_ids(self) -> list[str]:
        return [str(ContractRef.parse(ref).without_version()) for ref in self.metric_refs]

    def self_ref(self) -> str:
        return str(ContractRef.parse(self.metadata.id).with_version(self.metadata.version))

    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


class AssetInvalidation(StrictModel):
    asset: NonBlankText
    code: NonBlankText
    severity: Severity
    detail: NonBlankText


class VerifiedAssetRepository:
    """The reviewed corpus a release gate re-checks against."""

    def __init__(self, assets: list[VerifiedAsset]) -> None:
        seen: set[str] = set()
        for item in assets:
            if item.metadata.id in seen:
                raise ValueError(f"duplicate verified asset id: {item.metadata.id}")
            seen.add(item.metadata.id)
        self.assets = sorted(assets, key=lambda item: item.metadata.id)

    @classmethod
    def load(cls, path: str | Path) -> VerifiedAssetRepository:
        root = Path(path)
        files = (
            [root]
            if root.is_file()
            else sorted(
                child
                for child in root.rglob("*")
                if child.suffix in _YAML_SUFFIXES and child.is_file()
            )
        )
        assets = [
            VerifiedAsset.model_validate(yaml.safe_load(file.read_text(encoding="utf-8")) or {})
            for file in files
        ]
        return cls(assets)

    def get(self, asset_id: str) -> VerifiedAsset | None:
        return next((item for item in self.assets if item.metadata.id == asset_id), None)

    def ungoverned(self, snapshot: SemanticSnapshot) -> list[AssetInvalidation]:
        """Assets whose reviewed query reads outside the model this snapshot governs.

        The provenance chain anchors on the reviewed query (§8.4.1): a candidate number is
        tied to the SQL a reviewer signed off. That anchor is worth exactly as much as its
        attachment to the governed model — a reviewed query reading `dm.sales_daily_v2`
        produces an attested, reconciled number computed from a mart nobody governs, and a
        reviewed query reading an undeclared column of a governed mart produces one computed
        partly from data the model says nothing about. Both are what this product exists to
        prevent, and neither is visible to a comparison of two numbers.

        **Only what something decides.** A column is attributed to a table when the
        statement leaves no choice — qualified, or unqualified with a single source in scope,
        where the sources of a scope are the ones its own FROM/JOIN clauses read rather than
        every CTE standing around it. Where the statement alone does not decide, the governed
        model is asked next: the engine accepted this SQL, so a bare name resolved to exactly
        one of the sources in its scope, and when the snapshot declares that name on exactly
        one of them, reading it there describes the query rather than guesses about it. That
        deduction needs the whole scope visible — one ungoverned mart, CTE or subquery beside
        it holds columns the snapshot knows nothing about, and then the name is left alone.
        Two governed sources declaring it — two sources, even where they are one table joined
        to itself — leave the same choice the engine would have had, and picking one is a
        guess a gate must not make (invariant 6).

        A bare name inside a correlated scope is asked the same question of its *local*
        sources only. SQL resolves it at the innermost level that has the column, so one
        local source declaring it settles it there; but where no local source declares it the
        name may belong to the query around it, and calling it an undeclared column of the
        local mart would reject valid reviewed SQL over a column the outer query declares.
        What could not be attributed is *said* rather than skipped
        (`asset.query_fields_unchecked`), per reference and with the reason that applies to
        it, so a partial check never reads as a clean one.

        Case is read the way the asset's engine reads it: ClickHouse distinguishes
        `Sales_Daily` from `sales_daily`, the PostgreSQL family folds an *unquoted*
        identifier to lower case, and grading one engine's rule on the other would either
        miss a real mismatch or invent one. Quoting is part of that question and travels
        with the name: `"Sales_Daily"` on PostgreSQL is a table `sales_daily` is not, so a
        quoted reference is compared exactly on every engine.
        """
        findings: list[AssetInvalidation] = []
        tables = _Names(node.id for node in snapshot.graph.tables)
        columns = {
            table: _Names(node.column for node in nodes)
            for table, nodes in _by_table(snapshot.graph.fields).items()
        }
        for item in self.assets:
            query = item.reference_query
            if query is None:
                continue
            findings.extend(_query_findings(item, query, tables, columns))
        return findings

    def invalidate(self, diff: SemanticDiff) -> list[AssetInvalidation]:
        """Assets a semantic change makes unusable as evidence, worst reason first."""
        invalidations: list[AssetInvalidation] = []
        for item in self.assets:
            if item.semantic_digest != diff.base_digest:
                invalidations.append(
                    AssetInvalidation(
                        asset=item.self_ref(),
                        code="asset.stale_snapshot",
                        severity=Severity.REVIEW,
                        detail=(
                            "verified against another snapshot "
                            f"({item.semantic_digest}), not {diff.base_digest}"
                        ),
                    )
                )
                continue
            invalidation = _invalidation_for(item, diff)
            if invalidation is not None:
                invalidations.append(invalidation)
        return invalidations


class _Names:
    """Declared names, matched the way one engine family matches them.

    Folding is looked up rather than applied: a model that declared both `Sales` and
    `sales` gives one folded key two answers, and choosing either would invent a finding,
    so such a key simply does not fold and the exact comparison stands.
    """

    def __init__(self, declared: Iterable[str]) -> None:
        self.exact = set(declared)
        folded: dict[str, set[str]] = {}
        for name in self.exact:
            folded.setdefault(name.lower(), set()).add(name)
        self.folded = {key: next(iter(names)) for key, names in folded.items() if len(names) == 1}

    def resolve(self, name: str, *, quoted: bool, folds: bool) -> str | None:
        """The declared name this reference means, or ``None`` when nothing declares it."""
        if name in self.exact:
            return name
        if folds and not quoted:
            return self.folded.get(name.lower())
        return None


def _by_table(fields: Iterable[FieldNode]) -> dict[str, list[FieldNode]]:
    grouped: dict[str, list[FieldNode]] = {}
    for node in fields:
        grouped.setdefault(node.table, []).append(node)
    return grouped


def _query_findings(
    item: VerifiedAsset,
    query: ReferenceQuery,
    tables: _Names,
    columns: Mapping[str, _Names],
) -> list[AssetInvalidation]:
    dialect = sqlglot_dialect(query.engine)
    # ClickHouse compares identifiers as written; the PostgreSQL family folds unquoted ones.
    folds = dialect != "clickhouse"

    def governed_table(ref: TableRef) -> str | None:
        return tables.resolve(ref.name, quoted=ref.quoted, folds=folds)

    findings: list[AssetInvalidation] = []
    unknown_tables = sorted(
        ref.name
        for ref in extract_table_refs(query.sql, dialect=dialect)
        if governed_table(ref) is None
    )
    if unknown_tables:
        findings.append(
            _asset_finding(
                item,
                "asset.query_ungoverned",
                Severity.REVIEW,
                f"the reviewed reference_query reads {', '.join(unknown_tables)}, which this "
                "snapshot does not govern: a number from it is not a number about this model",
            )
        )

    reads = extract_column_refs(query.sql, dialect=dialect)

    def declares(table: str, name: str, *, quoted: bool) -> bool:
        declared = columns.get(table)
        if declared is None:
            return False
        return declared.resolve(name, quoted=quoted, folds=folds) is not None

    unknown_fields: set[str] = set()
    for column_ref in reads.attributed:
        table = governed_table(column_ref.table)
        # A column of an ungoverned mart is already covered by the finding above; naming it
        # again would weigh one fact twice and bury the fix that matters.
        if table is None:
            continue
        if not declares(table, column_ref.name, quoted=column_ref.quoted):
            unknown_fields.add(f"{table}.{column_ref.name}")

    # Both sets hold *references*, not names: one `category` the model settles says nothing
    # about another `category` two words later, and keeping only the name would let the
    # resolved one stand in for the unresolvable one — a partially graded query then reads
    # as a clean one, which is exactly what this check exists to prevent.
    settled: set[AmbiguousRef] = set()
    contested: set[AmbiguousRef] = set()
    outward: set[AmbiguousRef] = set()
    for ambiguous_ref in reads.ambiguous:
        scope = (
            _governed_scope(ambiguous_ref.candidates, governed_table)
            if ambiguous_ref.all_tables
            else []
        )
        # Nothing the model can see decides this one: an ungoverned mart, a CTE or a
        # subquery in scope holds columns the snapshot says nothing about, so it can neither
        # rule that source in nor rule it out. An enclosed name with no source of its own
        # is the exception: the engine is certainly reading the query around it, and that
        # is the enclosing-query remainder rather than the sourceless one.
        if not scope:
            if ambiguous_ref.enclosed and not ambiguous_ref.candidates and ambiguous_ref.all_tables:
                outward.add(ambiguous_ref)
            continue
        declaring = [
            table
            for table in scope
            if declares(table, ambiguous_ref.name, quoted=ambiguous_ref.quoted)
        ]
        if len(declaring) == 1:
            # One governed source in scope declares it and the engine accepted the SQL, so
            # the name resolved there — and for a name that could also have been read from
            # the enclosing query, the innermost level holding the column is where SQL says
            # the engine reads it. Reading that is a reading of the query, not a guess.
            settled.add(ambiguous_ref)
        elif declaring:
            contested.add(ambiguous_ref)  # the statement would be ambiguous for the engine too
        elif ambiguous_ref.enclosed:
            # Written where the engine may look outward, and declared on no source of its
            # own scope: it is either the enclosing query's column or an undeclared column
            # of the local table, and nothing here tells the two apart. Calling it the local
            # table's undeclared field rejects valid reviewed SQL, so it stays unchecked.
            outward.add(ambiguous_ref)
        else:
            # Read from somewhere in a scope the model sees whole, and declared nowhere in
            # it: naming it is as solid as naming an undeclared qualified column.
            settled.add(ambiguous_ref)
            unknown_fields.add(
                f"{ambiguous_ref.name} (read from one of {', '.join(sorted(set(scope)))})"
            )

    if unknown_fields:
        findings.append(
            _asset_finding(
                item,
                "asset.query_ungoverned_field",
                Severity.REVIEW,
                f"the reviewed reference_query reads {', '.join(sorted(unknown_fields))}, which "
                "this snapshot does not declare: the number is computed partly from data the "
                "model says nothing about",
            )
        )

    unchecked = _unchecked_reason(reads, settled=settled, contested=contested, outward=outward)
    if unchecked is not None:
        findings.append(
            _asset_finding(item, "asset.query_fields_unchecked", Severity.ADVISORY, unchecked)
        )
    return findings


def _governed_scope(
    candidates: Iterable[TableRef], governed: Callable[[TableRef], str | None]
) -> list[str]:
    """The candidates as declared names — one per source — or nothing at all if any of them
    is ungoverned.

    A single ungoverned mart in scope is enough to spoil the deduction: its columns are
    unknown to the snapshot, so "only this governed table declares the name" says nothing
    about where the engine read it from. Repeats are kept for the same reason the caller
    counts declaring sources rather than tables: a self-join declares the name twice, and
    one entry there would read as a decision the statement does not make.
    """
    names = [governed(candidate) for candidate in candidates]
    if not names or any(name is None for name in names):
        return []
    return sorted(name for name in names if name is not None)


def _unchecked_reason(
    reads: ColumnReads,
    *,
    settled: set[AmbiguousRef],
    contested: set[AmbiguousRef],
    outward: set[AmbiguousRef],
) -> str | None:
    """Why the field-level check could not cover the whole query, if it could not.

    The remainders read very differently to whoever has to act on them, and naming the wrong
    one sends that reader after a fix the query does not need. Several governed sources
    declaring the name is a statement nobody can pin down; a scope holding a mart, CTE or
    subquery the model cannot see into is a hole in what the gate was given; a qualifier no
    enclosing scope declares is a mistake in the statement itself, with no source withheld
    from anyone; a scope with no source of its own withheld nothing either, unless it can
    read the query around it — then the name certainly comes from there, which is a limit of
    this check rather than anything wrong with the SQL. A reference the model *did* settle —
    declared on one source, or reported as declared on none — is not a remainder at all and
    is never repeated here.
    """
    reasons: list[str] = []
    if reads.starred:
        reasons.append(
            "it selects *, so which columns it reads depends on the physical table rather "
            "than on the SQL"
        )
    for setting in sorted(reads.opaque_settings):
        reasons.append(
            f"it sets {setting}, a row filter written as SQL text that this check does not read"
        )
    disputed = sorted({ref.name for ref in contested})
    if disputed:
        reasons.append(
            f"{', '.join(disputed)} {_agrees(disputed)} declared by several of the governed "
            "sources in scope, so nothing says which source each is read from"
        )
    stray: set[str] = set()
    sourceless: set[str] = set()
    enclosing: set[str] = set()
    unseen: set[str] = set()
    for ref in reads.ambiguous - settled - contested:
        if ref.qualifier:
            stray.add(f"{ref.qualifier}.{ref.name}")
        elif ref in outward:
            enclosing.add(ref.name)
        elif not ref.candidates and ref.all_tables:
            sourceless.add(ref.name)
        else:  # candidates the model could not read, or a source that is not a table at all
            unseen.add(ref.name)
    if stray:
        names = sorted(stray)
        reasons.append(
            f"{', '.join(names)} {_agrees(names)} qualified by a source no enclosing scope "
            "declares, so the statement itself resolves nothing"
        )
    if sourceless:
        names = sorted(sourceless)
        reasons.append(
            f"{', '.join(names)} {_agrees(names)} read in a scope that offers no source of its own"
        )
    correlated = sorted(enclosing)
    if correlated:
        reasons.append(
            f"{', '.join(correlated)} may be read from an enclosing query: no source in "
            f"{'its' if len(correlated) == 1 else 'their'} own scope declares "
            f"{'it' if len(correlated) == 1 else 'them'}, and this check does not follow a "
            "name outward"
        )
    opaque = sorted(unseen)
    if opaque:
        reasons.append(
            f"{', '.join(opaque)} could not be attributed to one table without guessing, "
            "and the scope offers a source this snapshot cannot see into"
        )
    if not reasons:
        return None
    return "the field-level check over the reviewed reference_query is partial: " + "; ".join(
        reasons
    )


def _agrees(names: list[str]) -> str:
    """The verb form agreeing with a list rendered as one subject — a detail is read, not parsed."""
    return "is" if len(names) == 1 else "are"


def _asset_finding(
    item: VerifiedAsset, code: str, severity: Severity, detail: str
) -> AssetInvalidation:
    return AssetInvalidation(asset=item.self_ref(), code=code, severity=severity, detail=detail)


def _invalidation_for(item: VerifiedAsset, diff: SemanticDiff) -> AssetInvalidation | None:
    owned = set(item.metric_ids())
    removed: list[str] = []
    changed: list[str] = []
    for change in diff.changes:
        if change.severity not in _INVALIDATING:
            continue
        if not owned.intersection(change.impact.metrics):
            continue
        if change.code == "metric.removed":
            removed.append(change.subject)
        else:
            changed.append(f"{change.code} on {change.subject}")

    if removed:
        return AssetInvalidation(
            asset=item.self_ref(),
            code="asset.metric_removed",
            severity=Severity.BLOCK,
            detail=f"metric gone: {', '.join(sorted(removed))}",
        )
    if changed:
        return AssetInvalidation(
            asset=item.self_ref(),
            code="asset.metric_changed",
            severity=Severity.REVIEW,
            detail=f"re-review required: {', '.join(sorted(changed))}",
        )
    return None
