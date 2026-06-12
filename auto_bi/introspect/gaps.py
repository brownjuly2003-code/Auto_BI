"""Gaps report: deterministic DM-readiness audit of a semantic model (task 1.10).

Runs after introspection of a real DWH and answers "what is this DM missing for
text-to-dashboard work": undocumented tables/columns, isolated tables (no joins),
entity references without dimension tables, pre-aggregated time grain. Findings are
deterministic — no LLM involved; warn/critical ones double as dm_change_request
candidates for the DM owner.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from pydantic import BaseModel, Field

from auto_bi.semantic.model import ColumnRole, SemanticModel, Table

RunQuery = Callable[[str], list[dict]]

_ENTITY_SUFFIXES = ("_id", "_hk", "_bk")


class GapSeverity(StrEnum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


class GapFinding(BaseModel):
    code: str
    severity: GapSeverity
    table: str = ""
    column: str = ""
    title: str
    detail: str = ""
    dm_change_request: bool = False  # candidate for a structured request to the DM owner


class GapsReport(BaseModel):
    model_tables: list[str] = Field(default_factory=list)
    findings: list[GapFinding] = Field(default_factory=list)

    def by_severity(self, severity: GapSeverity) -> list[GapFinding]:
        return [f for f in self.findings if f.severity == severity]

    def to_markdown(self) -> str:
        lines = ["# Gaps report", ""]
        lines.append(f"Таблиц в модели: {len(self.model_tables)} — " + ", ".join(self.model_tables))
        lines.append("")
        counts = {s: len(self.by_severity(s)) for s in GapSeverity}
        lines.append(
            f"Findings: {counts[GapSeverity.CRITICAL]} critical / "
            f"{counts[GapSeverity.WARN]} warn / {counts[GapSeverity.INFO]} info"
        )
        for severity in (GapSeverity.CRITICAL, GapSeverity.WARN, GapSeverity.INFO):
            found = self.by_severity(severity)
            if not found:
                continue
            lines.append("")
            lines.append(f"## {severity.value}")
            for f in found:
                where = f.table + (f".{f.column}" if f.column else "")
                prefix = f"`{where}` — " if where else ""
                lines.append(f"- **{f.code}**: {prefix}{f.title}")
                if f.detail:
                    lines.append(f"  - {f.detail}")
        dcr = [f for f in self.findings if f.dm_change_request]
        if dcr:
            lines.append("")
            lines.append("## Кандидаты в dm_change_request")
            for f in dcr:
                where = f.table + (f".{f.column}" if f.column else "")
                lines.append(f"- `{where or 'DM'}` ({f.code}): {f.title}")
        lines.append("")
        return "\n".join(lines)


def _entity_stem(column_name: str) -> str | None:
    for suffix in _ENTITY_SUFFIXES:
        if column_name.endswith(suffix) and len(column_name) > len(suffix):
            return column_name.removesuffix(suffix)
    return None


def _check_descriptions(model: SemanticModel, findings: list[GapFinding]) -> None:
    for table in model.tables:
        if not table.description:
            findings.append(
                GapFinding(
                    code="table_no_description",
                    severity=GapSeverity.WARN,
                    table=table.name,
                    title="у таблицы нет описания — grounding опирается только на имя",
                )
            )
        missing = [c.name for c in table.columns if not c.description]
        if missing:
            findings.append(
                GapFinding(
                    code="columns_no_description",
                    severity=GapSeverity.INFO,
                    table=table.name,
                    title=f"без описания {len(missing)} из {len(table.columns)} колонок",
                    detail=", ".join(missing),
                )
            )


def _check_relationships(model: SemanticModel, findings: list[GapFinding]) -> None:
    if len(model.tables) > 1 and not model.joins:
        findings.append(
            GapFinding(
                code="no_relationships",
                severity=GapSeverity.CRITICAL,
                title="ни одной связи между таблицами не обнаружено",
                detail=(
                    "Таблицы изолированы: запрос с полями из разных таблиц невозможен. "
                    "Нужны FK-конвенции (*_id -> справочник) или ручные joins в model.yaml."
                ),
                dm_change_request=True,
            )
        )


def _check_entity_dimensions(model: SemanticModel, findings: list[GapFinding]) -> None:
    table_stems = set()
    for table in model.tables:
        short = table.name.split(".")[-1]
        table_stems.add(short)
        table_stems.update(short.split("_"))
    for table in model.tables:
        for col in table.columns:
            if col.role != ColumnRole.DIMENSION or col.fk:
                continue
            stem = _entity_stem(col.name)
            if not stem or col.name in table.grain:
                continue
            if stem in table_stems or f"{stem}s" in table_stems:
                continue
            findings.append(
                GapFinding(
                    code="entity_without_dimension_table",
                    severity=GapSeverity.WARN,
                    table=table.name,
                    column=col.name,
                    title=f"ссылка на сущность «{stem}» без таблицы-справочника",
                    detail=(
                        f"Разрез по атрибутам «{stem}» невозможен — в DM нет dim-таблицы, "
                        "ключ остаётся непрозрачным идентификатором."
                    ),
                    dm_change_request=True,
                )
            )


def _time_grain(table: Table, column: str, run_query: RunQuery) -> str:
    """Coarsest grain the values actually have: month | week | fine."""
    db, _, tbl = table.name.partition(".")
    target = f"`{db}`.`{tbl}`"
    monthly = run_query(f"SELECT countIf(toDayOfMonth(`{column}`) != 1) AS off FROM {target}")
    if monthly and int(monthly[0]["off"]) == 0:
        return "month"
    weekly = run_query(
        f"SELECT countIf(toDate(`{column}`) != toStartOfWeek(`{column}`)) AS off FROM {target}"
    )
    if weekly and int(weekly[0]["off"]) == 0:
        return "week"
    return "fine"


def _check_time_grain(
    model: SemanticModel, findings: list[GapFinding], run_query: RunQuery | None
) -> None:
    finest: dict[str, str] = {}  # table -> finest grain seen across its time columns
    rank = {"fine": 0, "week": 1, "month": 2}
    for table in model.tables:
        time_cols = [c for c in table.columns if c.role == ColumnRole.TIME]
        if not time_cols:
            continue
        grains = []
        for col in time_cols:
            if run_query is not None and (table.physical is None or table.physical.rows > 0):
                grain = _time_grain(table, col.name, run_query)
            else:  # offline fallback: name heuristics only
                grain = col.name if col.name in ("month", "week") else "fine"
            grains.append(grain)
            if grain != "fine":
                findings.append(
                    GapFinding(
                        code="preaggregated_time_grain",
                        severity=GapSeverity.WARN,
                        table=table.name,
                        column=col.name,
                        title=f"временная колонка агрегирована до «{grain}»",
                        detail=(
                            "Дневная динамика и нестандартные периоды по этой "
                            "таблице невозможны."
                        ),
                    )
                )
        finest[table.name] = min(grains, key=lambda g: rank[g])
    if finest and all(rank[g] > 0 for g in finest.values()):
        findings.append(
            GapFinding(
                code="no_fine_time_grain",
                severity=GapSeverity.CRITICAL,
                title="во всём DM нет таблицы с дневной/событийной грануляцией",
                detail=(
                    "Все таблицы прёагрегированы (месяц/неделя). Любой запрос про дни, "
                    "конкретные даты или intraday-динамику невыполним без новой fact-таблицы."
                ),
                dm_change_request=True,
            )
        )


def find_gaps(model: SemanticModel, run_query: RunQuery | None = None) -> GapsReport:
    findings: list[GapFinding] = []
    _check_descriptions(model, findings)
    _check_relationships(model, findings)
    _check_entity_dimensions(model, findings)
    _check_time_grain(model, findings, run_query)
    severity_rank = {GapSeverity.CRITICAL: 0, GapSeverity.WARN: 1, GapSeverity.INFO: 2}
    findings.sort(key=lambda f: (severity_rank[f.severity], f.code, f.table, f.column))
    return GapsReport(model_tables=[t.name for t in model.tables], findings=findings)
