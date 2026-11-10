"""Reference SQL for a governed metric (DECISIONOPS_ARCHITECTURE §8.2 step 6).

The gate compiles what a metric *means*: its definition at its own declared grain. That is
not a chart query — no window, no filter, no top-N — because those belong to a product and
a run, while this is the metric itself. Two things make it worth carrying in evidence: a
reviewer reading "formula changed" can see the before and after as SQL, and step 7 gets
something concrete to execute once there is a stand to execute it against.

**The boundary is deliberate and narrow.** A query is emitted only where the semantics are
exact offline:

* a *typed aggregate* at its grain compiles — `SUM(col)` over a grouped mart means the same
  thing in ClickHouse and in Postgres;
* a *governed ratio* compiles on the two engine families whose decimal division has been
  checked against a running engine — ClickHouse, and the PostgreSQL family that
  Greenplum/Greengage belong to — each in the form that engine computes exactly at the
  precision the contract declares. Neither form could be derived from the other, so an engine
  the seam does not recognise keeps the gap named instead of receiving SQL the gate cannot
  stand behind (invariant 6);
* an *external* or *reviewed-SQL* formula is somebody else's definition. Re-deriving it here
  would be inventing a second source of truth for a number the contract says is governed
  elsewhere.

**What the ClickHouse stand settled, and could not have been guessed** (24.8.14.39, the
version the local stand pins): plain `Decimal / Decimal` keeps the *dividend's* scale, so a
margin over a Decimal(18,2) numerator would truncate to two digits — the chart path answers
that by casting to Float64 (`sqlgen._safe_div`), a display decision verified live for
dashboards that a governed decimal metric must not inherit. `divideDecimal(num, den, p)`
divides exactly and truncates toward zero at `p` instead, whatever scales the terms carry. It
reads the denominator's values *under* the NULL mask, so `NULLIF(den, 0)` alone still raises
`ILLEGAL_DIVISION` — the zero has to be replaced inside the call and the row nulled by the
guard around it. Its arguments must both be Decimal, so counting and integer terms are cast.
And a group of nothing but NULLs sums to NULL, which ClickHouse refuses to cast to a
non-nullable Decimal — hence the nullable cast for a nullable integer term.

**What the PostgreSQL stand settled** (14.24, the `numeric` implementation Greenplum and
Greengage inherit): `numeric / numeric` is exact, but its *scale* is picked from the operands
to reach about sixteen significant digits, so a large quotient stops short of a deep declared
precision and `TRUNC(a / b, 18)` pads zeros it never computed — `1000000000000.00 / 3.00`
truncated at 18 ends in nine of them. Multiplying the dividend by an exact one that carries
the declared decimals raises its scale without moving its value, and the digits become real;
a cast to `numeric(_, p)` would not do, because it rounds. At precision 0 that multiplier
still carries a decimal: `/` on two integer terms is integer division, and it truncates
before `TRUNC` ever sees the number. Nothing else needs a guard there — this `/` respects the
NULL mask, so `NULLIF(den, 0)` covers a zero denominator and a NULL term nulls its own row —
and `TRUNC` truncates toward zero exactly as `divideDecimal` does, so one governed number
reads the same on both engines.

`unit.precision` is the scale of the result; `null_semantics` is not encoded here, because
SUM's own NULL-skipping already matches both readings except for an all-NULL group, and
deciding that in SQL would change the aggregate path too.

**Every finding is advisory.** Why a metric moved is graded upstream by the semantic diff
and the impact graph; grading it again here would let one change count twice toward a
verdict. This step carries evidence, not judgement.

The aggregate call itself comes from `agent/sqlgen.aggregate_expr` — the same function the
chart path uses, so a newly added aggregation cannot compile one way for a dashboard and
another way for the evidence that verifies it (§8.5).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import Enum, auto
from typing import NamedTuple

from pydantic import Field
from sqlglot import exp

from auto_bi.agent.sqlgen import aggregate_expr
from auto_bi.engine import CLICKHOUSE, GREENPLUM, is_known_engine, sqlglot_dialect
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.refs import ContractRef
from auto_bi.scalars import NonBlankText, Sha256Digest
from auto_bi.semantic.gateway import MetricNode, SemanticSnapshot, TableNode
from auto_bi.semantic.metric_contract import FormulaKind, MetricTerm
from auto_bi.semantic.model import Aggregation
from auto_bi.severity import Severity
from auto_bi.sql_render import SQLRenderRefusal, render_sql
from auto_bi.strict import StrictModel

#: Formula kinds whose definition lives outside this contract kernel.
_OPAQUE = frozenset({FormulaKind.EXTERNAL_REF, FormulaKind.REVIEWED_SQL})

#: The dialects whose decimal division has been checked against a running engine, each
#: with its own exact form below.
_CLICKHOUSE_DIALECT = sqlglot_dialect(CLICKHOUSE)
_POSTGRES_DIALECT = sqlglot_dialect(GREENPLUM)

#: `divideDecimal` takes Decimal arguments only. Widening a counting or integer term is
#: lossless: a SUM of 64-bit integers cannot leave Decimal(38, 0).
_INTEGRAL_DECIMAL = "Decimal(38, 0)"

#: Aggregations that count rows — exact and integral whatever the column holds.
_COUNTING = frozenset({Aggregation.COUNT, Aggregation.COUNT_DISTINCT})

#: Aggregations that carry the column's own type through. AVG is deliberately absent: it
#: returns Float64, and a governed decimal is not defined by a float.
_TYPE_PRESERVING = frozenset({Aggregation.SUM, Aggregation.MIN, Aggregation.MAX})

#: Type wrappers that say nothing about whether a column is exact — kept local to keep the
#: gate free of the introspection package (and its DWH driver) at import time.
_TYPE_WRAPPERS = ("Nullable", "LowCardinality")
_INTEGER_PREFIXES = ("Int", "UInt")

#: Postgres spells its exact types out in full, and matching them by prefix would read
#: `interval` as an integer.
_PG_DECIMAL = frozenset({"numeric", "decimal"})
_PG_INTEGRAL = frozenset({"smallint", "integer", "bigint", "int", "int2", "int4", "int8"})


class _Exactness(Enum):
    """What a declared column type says about the term aggregated from it."""

    #: Aggregates to an exact decimal.
    DECIMAL = auto()
    #: Aggregates to an exact whole number — which one of the two forms has to know about.
    INTEGRAL = auto()


class _RatioTerm(NamedTuple):
    """One side of a ratio: its aggregate, plus what the division needs to know about it."""

    expr: exp.Expression
    #: A counting or integer term — Decimal-cast before it reaches `divideDecimal`.
    integral: bool
    #: The column admits NULL, so its aggregate can be NULL over an all-NULL group.
    nullable: bool


class CompiledQuery(StrictModel):
    """One metric as the query that computes it."""

    metric_ref: NonBlankText
    dialect: NonBlankText
    grain_table: NonBlankText
    grain_keys: list[str] = Field(min_length=1)
    event_time_column: NonBlankText
    sql: NonBlankText
    #: Content address of the SQL text itself — what makes "base vs candidate" a comparison
    #: of two digests rather than a diff of two strings.
    digest: Sha256Digest


class SQLFinding(StrictModel):
    code: str = Field(min_length=1, pattern=r"^sql\.[a-z0-9_]+$")
    severity: Severity
    subject: NonBlankText
    detail: NonBlankText


class CompiledQuerySet(StrictModel):
    queries: list[CompiledQuery] = Field(default_factory=list)
    findings: list[SQLFinding] = Field(default_factory=list)

    def by_metric(self) -> dict[str, CompiledQuery]:
        return {query.metric_ref: query for query in self.queries}


def compile_metric_queries(
    snapshot: SemanticSnapshot, metric_refs: Sequence[str]
) -> CompiledQuerySet:
    """Compile the reference query of every metric in *metric_refs* against *snapshot*."""
    queries: list[CompiledQuery] = []
    findings: list[SQLFinding] = []

    for ref in metric_refs:
        # A product pins a version; the snapshot holds whatever version is current.
        # Whether the pin resolves — in the validated paths and in the candidate snapshot —
        # is graded by the gate's `contracts.validate` step, not here.
        node = snapshot.metric(str(ContractRef.parse(ref).without_version()))
        if node is None:
            findings.append(
                SQLFinding(
                    code="sql.metric_absent",
                    severity=Severity.ADVISORY,
                    subject=ref,
                    detail="this snapshot holds no such metric, so there is nothing to compile",
                )
            )
            continue
        _compile_one(node, snapshot, queries, findings)

    return CompiledQuerySet(queries=queries, findings=findings)


def _compile_one(
    node: MetricNode,
    snapshot: SemanticSnapshot,
    queries: list[CompiledQuery],
    findings: list[SQLFinding],
) -> None:
    ref = f"{node.id}@{node.version}"
    kind = node.formula.kind

    if kind in _OPAQUE:
        findings.append(
            SQLFinding(
                code="sql.opaque_formula",
                severity=Severity.ADVISORY,
                subject=ref,
                detail=(
                    f"a {kind.value} formula is governed outside this kernel; re-deriving it "
                    "here would create a second definition of the same number"
                ),
            )
        )
        return
    terms = _terms_of(node)
    if not terms:  # pragma: no cover - MetricFormula validates the pairing on parse
        return
    off_grain = [(role, term) for role, term in terms if term.table != node.grain.table]
    if off_grain:
        role, term = off_grain[0]
        findings.append(
            SQLFinding(
                code="sql.join_required",
                severity=Severity.ADVISORY,
                subject=ref,
                detail=(
                    f"the {role} reads {term.table} while the grain is {node.grain.table}; "
                    "the join that relates them is not invented here"
                ),
            )
        )
        return

    table = next((item for item in snapshot.graph.tables if item.id == node.grain.table), None)
    if table is None:
        findings.append(
            SQLFinding(
                code="sql.table_missing",
                severity=Severity.ADVISORY,
                subject=ref,
                detail=f"the grain table {node.grain.table} is not in this snapshot's graph",
            )
        )
        return

    dialect = _dialect_of(table, ref, findings)
    if kind is FormulaKind.TYPED_RATIO:
        measure = _ratio_measure(node, snapshot, table, dialect, ref, findings)
        if measure is None:
            return
    else:
        measure = aggregate_expr(terms[0][1].agg, exp.column(terms[0][1].column, quoted=True))
    try:
        sql = _render(node, measure, dialect)
    except SQLRenderRefusal as exc:
        findings.append(
            SQLFinding(
                code="sql.compile_refused",
                severity=Severity.ADVISORY,
                subject=ref,
                detail=str(exc),
            )
        )
        return
    queries.append(
        CompiledQuery(
            metric_ref=ref,
            dialect=dialect,
            grain_table=node.grain.table,
            grain_keys=list(node.grain.keys),
            event_time_column=node.time.event_time_column,
            sql=sql,
            digest=digest_bytes(sql.encode("utf-8")),
        )
    )


def _terms_of(node: MetricNode) -> list[tuple[str, MetricTerm]]:
    """The formula's terms, each under the name a finding would call it by."""
    formula = node.formula
    if formula.kind is FormulaKind.TYPED_RATIO:
        if formula.numerator is None or formula.denominator is None:  # pragma: no cover
            return []
        return [("numerator", formula.numerator), ("denominator", formula.denominator)]
    return [] if formula.aggregate is None else [("aggregate", formula.aggregate)]


def _ratio_measure(
    node: MetricNode,
    snapshot: SemanticSnapshot,
    table: TableNode,
    dialect: str,
    ref: str,
    findings: list[SQLFinding],
) -> exp.Expression | None:
    """The ratio in the form its engine computes exactly — or nothing, and a named reason.

    The engine is read here, not the dialect: `sqlglot_dialect` falls back to the v1 engine
    for a name it does not know, which is a fair assumption for `SUM(col)` and no assumption
    at all for a form that *is* engine-specific syntax.
    """
    form = _RATIO_FORM.get(dialect) if is_known_engine(table.engine) else None
    if form is None:
        named = table.engine or "an engine the model does not declare"
        findings.append(
            SQLFinding(
                code="sql.ratio_unverified",
                severity=Severity.ADVISORY,
                subject=ref,
                detail=(
                    "decimal division is engine-specific, and only ClickHouse and the "
                    "PostgreSQL family have been verified against a running engine; the "
                    f"ratio is not emitted for {named}"
                ),
            )
        )
        return None

    terms = dict(_terms_of(node))
    numerator = _ratio_term("numerator", terms["numerator"], snapshot, dialect, ref, findings)
    denominator = _ratio_term("denominator", terms["denominator"], snapshot, dialect, ref, findings)
    if numerator is None or denominator is None:
        return None
    return form(numerator, denominator, node.unit.precision)


def _clickhouse_ratio(
    numerator: _RatioTerm, denominator: _RatioTerm, precision: int
) -> exp.Expression:
    """`divideDecimal(numerator, denominator, precision)`, with the zero the engine forbids
    kept out of the call."""
    zero = exp.Literal.number(0)
    # A denominator of zero — or of nothing at all — leaves the ratio undefined, so the row
    # is NULL. The substitution inside the call is not belt and braces: `divideDecimal` reads
    # the values under the NULL mask, so `NULLIF(den, 0)` on its own still raises.
    undefined = exp.EQ(
        this=exp.func("ifNull", denominator.expr.copy(), zero.copy()), expression=zero.copy()
    )
    # sqlglot types func() as Func, an Expression subclass at runtime (as in `sqlgen`)
    divisor: exp.Expression = exp.func(  # type: ignore[assignment]
        "ifNull", exp.func("nullIf", denominator.expr.copy(), zero.copy()), exp.Literal.number(1)
    )
    if denominator.integral:
        divisor = _as_decimal(divisor, nullable=False)
    dividend = (
        _as_decimal(numerator.expr, nullable=numerator.nullable)
        if numerator.integral
        else numerator.expr
    )
    quotient = exp.Anonymous(
        this="divideDecimal", expressions=[dividend, divisor, exp.Literal.number(precision)]
    )
    return exp.If(this=undefined, true=exp.null(), false=quotient)


def _postgres_ratio(
    numerator: _RatioTerm, denominator: _RatioTerm, precision: int
) -> exp.Expression:
    """`TRUNC(numerator * <an exact one> / NULLIF(denominator, 0), precision)`.

    Neither term takes the cast its ClickHouse counterpart needs: the multiplier already makes
    the division `numeric`, and `/` reads the NULL mask, so a NULL term nulls its own row.
    """
    quotient = exp.Div(
        this=exp.Mul(this=numerator.expr, expression=_exact_one(precision)),
        expression=exp.Nullif(this=denominator.expr, expression=exp.Literal.number(0)),
        # unsaid, sqlglot casts the dividend to DOUBLE PRECISION for a Postgres target —
        # the float this whole step exists to keep a governed number out of
        typed=True,
    )
    return exp.Anonymous(this="TRUNC", expressions=[quotient, exp.Literal.number(precision)])


def _exact_one(precision: int) -> exp.Expression:
    """`1.000000` at precision 6 — a multiplier that moves a term's scale, not its value.

    Postgres picks the scale of a division from its operands, targeting about sixteen
    significant digits, so over a large quotient `TRUNC(a / b, p)` would pad digits the
    division never computed. Raising the dividend's scale to `p` is what makes them real, and
    a cast to `numeric(_, p)` cannot stand in for it: casting rounds. At precision 0 the
    multiplier keeps one decimal regardless — `/` on two integer terms is integer division.
    """
    return exp.Literal.number(f"1.{'0' * max(precision, 1)}")


#: The exact form of each engine family, keyed by the dialect the seam renders for it.
_RATIO_FORM: dict[str, Callable[[_RatioTerm, _RatioTerm, int], exp.Expression]] = {
    _CLICKHOUSE_DIALECT: _clickhouse_ratio,
    _POSTGRES_DIALECT: _postgres_ratio,
}


def _ratio_term(
    role: str,
    term: MetricTerm,
    snapshot: SemanticSnapshot,
    dialect: str,
    ref: str,
    findings: list[SQLFinding],
) -> _RatioTerm | None:
    """One side of the ratio — only where the model says its aggregate is an exact number."""
    field_id = f"{term.table}.{term.column}"
    field = next((item for item in snapshot.graph.fields if item.id == field_id), None)
    if field is None:
        findings.append(
            SQLFinding(
                code="sql.term_undeclared",
                severity=Severity.ADVISORY,
                subject=ref,
                detail=(
                    f"the {role} reads {field_id}, which this snapshot's model does not "
                    "declare; without its type an exact term cannot be told from a float one"
                ),
            )
        )
        return None

    aggregate = aggregate_expr(term.agg, exp.column(term.column, quoted=True))
    if term.agg in _COUNTING:  # a row count is exact and integral whatever it reads
        return _RatioTerm(expr=aggregate, integral=True, nullable=False)

    exact = _exactness(dialect, field.type) if term.agg in _TYPE_PRESERVING else None
    if exact is not None:
        return _RatioTerm(
            expr=aggregate,
            integral=exact is _Exactness.INTEGRAL,
            nullable="Nullable(" in field.type,
        )

    reason = (
        "AVG returns a float whatever it reads"
        if term.agg is Aggregation.AVG
        else f"a {_base_type(dialect, field.type)} column does not aggregate to an exact decimal"
    )
    findings.append(
        SQLFinding(
            code="sql.ratio_inexact_term",
            severity=Severity.ADVISORY,
            subject=ref,
            detail=(
                f"the {role} is {term.agg.value}({field_id}) — {reason}; a governed decimal "
                "metric is not defined by a float (invariant 6)"
            ),
        )
    )
    return None


def _as_decimal(inner: exp.Expression, *, nullable: bool) -> exp.Expression:
    """An integral aggregate as a Decimal, since `divideDecimal` takes nothing else."""
    kind = f"Nullable({_INTEGRAL_DECIMAL})" if nullable else _INTEGRAL_DECIMAL
    return exp.cast(inner, kind, dialect=_CLICKHOUSE_DIALECT)


def _exactness(dialect: str, column_type: str) -> _Exactness | None:
    """Whether the model's declared type aggregates to an exact number, and to which kind.

    The two engine families name their types differently enough that one table of prefixes
    would answer wrongly for the other, so each is read in its own vocabulary.
    """
    base = _base_type(dialect, column_type)
    if dialect == _POSTGRES_DIALECT:
        if base in _PG_DECIMAL:
            return _Exactness.DECIMAL
        return _Exactness.INTEGRAL if base in _PG_INTEGRAL else None
    if base.startswith("Decimal"):
        return _Exactness.DECIMAL
    return _Exactness.INTEGRAL if base.startswith(_INTEGER_PREFIXES) else None


def _base_type(dialect: str, column_type: str) -> str:
    """The declared type without what says nothing about exactness: `Nullable(LowCardinality(
    String))` -> `String`, `NUMERIC(18,2)` -> `numeric`."""
    if dialect == _POSTGRES_DIALECT:
        return column_type.split("(", 1)[0].strip().lower()
    inner = column_type
    for wrapper in _TYPE_WRAPPERS:
        prefix, suffix = f"{wrapper}(", ")"
        if inner.startswith(prefix) and inner.endswith(suffix):
            inner = inner[len(prefix) : -len(suffix)]
    return inner


def _dialect_of(table: TableNode, ref: str, findings: list[SQLFinding]) -> str:
    """The engine's dialect — and a named assumption when the table declares none."""
    dialect = sqlglot_dialect(table.engine)
    if not table.engine:
        findings.append(
            SQLFinding(
                code="sql.engine_undeclared",
                severity=Severity.ADVISORY,
                subject=ref,
                detail=(
                    f"{table.id} declares no engine; the query below is rendered for "
                    f"{dialect}, which is an assumption and not a fact from the model"
                ),
            )
        )
    return dialect


def _render(node: MetricNode, measure: exp.Expression, dialect: str) -> str:
    """`SELECT <grain keys>, <measure> AS <metric> FROM <table> GROUP BY <grain keys>`.

    No WHERE and no LIMIT: the metric is defined at its grain, and the window a product
    happens to read it through is the product's, not the metric's. Dimensions are what it
    *may* be sliced by, so they are not in the GROUP BY either.
    """
    keys = [exp.column(key, quoted=True) for key in node.grain.keys]
    alias = ContractRef.parse(node.id).path[-1]
    aliased = exp.alias_(measure, alias, quoted=True)
    select = exp.select(*[key.copy() for key in keys], aliased).from_(
        exp.to_table(node.grain.table)
    )
    return render_sql(select.group_by(*[key.copy() for key in keys]), dialect=dialect)


__all__ = [
    "CompiledQuery",
    "CompiledQuerySet",
    "SQLFinding",
    "aggregate_expr",
    "compile_metric_queries",
]
