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
* a *governed ratio* does not. Decimal division is engine-specific — ClickHouse keeps the
  dividend's scale on `Decimal / Decimal`, which is why the chart path casts to Float64
  (`sqlgen._safe_div`). That cast is a display decision, verified live for dashboards, and a
  governed decimal metric with a declared precision must not inherit it. Without a stand to
  verify the exact form, the gate names the gap instead of emitting SQL it cannot stand
  behind (invariant 6);
* an *external* or *reviewed-SQL* formula is somebody else's definition. Re-deriving it here
  would be inventing a second source of truth for a number the contract says is governed
  elsewhere.

**Every finding is advisory.** Why a metric moved is graded upstream by the semantic diff
and the impact graph; grading it again here would let one change count twice toward a
verdict. This step carries evidence, not judgement.

The aggregate call itself comes from `agent/sqlgen.aggregate_expr` — the same function the
chart path uses, so a newly added aggregation cannot compile one way for a dashboard and
another way for the evidence that verifies it (§8.5).
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field
from sqlglot import exp

from auto_bi.agent.sqlgen import aggregate_expr
from auto_bi.engine import sqlglot_dialect
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.refs import ContractRef
from auto_bi.scalars import NonBlankText, Sha256Digest
from auto_bi.semantic.gateway import MetricNode, SemanticSnapshot, TableNode
from auto_bi.semantic.metric_contract import FormulaKind, MetricTerm
from auto_bi.severity import Severity
from auto_bi.strict import StrictModel

#: Formula kinds whose definition lives outside this contract kernel.
_OPAQUE = frozenset({FormulaKind.EXTERNAL_REF, FormulaKind.REVIEWED_SQL})


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
        # A product pins a version; the snapshot holds whatever version is current. Which
        # pin still resolves is `decision validate`'s question, not this one.
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
    if kind is FormulaKind.TYPED_RATIO:
        findings.append(
            SQLFinding(
                code="sql.ratio_unverified",
                severity=Severity.ADVISORY,
                subject=ref,
                detail=(
                    "decimal division is engine-specific (ClickHouse keeps the dividend's "
                    "scale on Decimal/Decimal, and the chart path answers that with a Float64 "
                    "cast a governed decimal metric must not inherit); no stand has verified "
                    "the exact form, so no query is emitted"
                ),
            )
        )
        return

    term = node.formula.aggregate
    if term is None:  # pragma: no cover - MetricFormula validates this pairing on parse
        return
    if term.table != node.grain.table:
        findings.append(
            SQLFinding(
                code="sql.join_required",
                severity=Severity.ADVISORY,
                subject=ref,
                detail=(
                    f"the aggregate reads {term.table} while the grain is {node.grain.table}; "
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
    sql = _render(node, term, dialect)
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


def _render(node: MetricNode, term: MetricTerm, dialect: str) -> str:
    """`SELECT <grain keys>, <agg>(<column>) AS <metric> FROM <table> GROUP BY <grain keys>`.

    No WHERE and no LIMIT: the metric is defined at its grain, and the window a product
    happens to read it through is the product's, not the metric's. Dimensions are what it
    *may* be sliced by, so they are not in the GROUP BY either.
    """
    keys = [exp.column(key, quoted=True) for key in node.grain.keys]
    alias = ContractRef.parse(node.id).path[-1]
    measure = exp.alias_(
        aggregate_expr(term.agg, exp.column(term.column, quoted=True)), alias, quoted=True
    )
    select = exp.select(*[key.copy() for key in keys], measure).from_(
        exp.to_table(node.grain.table)
    )
    return select.group_by(*[key.copy() for key in keys]).sql(dialect=dialect, identify=True)


__all__ = [
    "CompiledQuery",
    "CompiledQuerySet",
    "SQLFinding",
    "aggregate_expr",
    "compile_metric_queries",
]
