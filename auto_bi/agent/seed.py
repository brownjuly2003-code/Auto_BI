"""Fields-first seed (task 2.3): drag&drop раскладка полей -> вход того же пайплайна.

The seed is the SECOND entry into the same pipeline (invariant 6 / D8): draft
groups of DM fields render into a textual request that GROUNDING and
PROPOSE_SPEC consume unchanged — there is no separate chart constructor.
Validation is deterministic and happens BEFORE any LLM call: the UI builds the
panel from the semantic model, so an unknown field is protocol misuse, not an
ambiguity to clarify. The layout analysis is deterministic too (D5: code
decides, LLM phrases): it compares the seed with the proposed spec and reports
dropped fields — the LLM never grades its own homework.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from auto_bi.ir.spec import DashboardSpec
from auto_bi.semantic.model import SemanticModel


class SeedGroup(BaseModel):
    label: str = ""
    fields: list[str] = Field(min_length=1)  # fully qualified: "dm.sales_daily.revenue"


class FieldsSeed(BaseModel):
    groups: list[SeedGroup] = Field(min_length=1)
    comment: str = ""


def validate_seed(seed: FieldsSeed, model: SemanticModel) -> list[str]:
    """Deterministic check against the semantic model; errors, not exceptions."""
    known = {f"{t.name}.{c.name}" for t in model.tables for c in t.columns}
    errors: list[str] = []
    for index, group in enumerate(seed.groups, start=1):
        for field in group.fields:
            if field not in known:
                errors.append(f"группа {index}: поле {field!r} не найдено в семантической модели")
    return errors


def seed_tables(seed: FieldsSeed) -> set[str]:
    """Tables the seed references — pinned in context selection (they ARE the request)."""
    return {field.rsplit(".", 1)[0] for group in seed.groups for field in group.fields}


def render_seed_request(seed: FieldsSeed, model: SemanticModel) -> str:
    """Seed -> textual request for GROUNDING / PROPOSE_SPEC.

    Field roles are annotated from the model so the LLM does not have to re-derive
    what is a measure; the instruction block keeps the layout advisory — the user
    sketched chart drafts, not final charts, and the LLM still picks viz types.
    """
    roles = {
        f"{t.name}.{c.name}": (c.role.value + (f", agg {c.agg.value}" if c.agg else ""))
        for t in model.tables
        for c in t.columns
    }
    lines = [
        "Запрос задан раскладкой полей (fields-first): пользователь перетащил поля витрин "
        "в черновые группы будущих чартов.",
        "Каждая группа — черновик ОДНОГО чарта: используй её поля вместе, viz-тип и настройки "
        "подбери сам. Объединять или разбивать группы можно, только если раскладка иначе "
        "не выполнима.",
        "",
    ]
    for index, group in enumerate(seed.groups, start=1):
        title = f" «{group.label}»" if group.label else ""
        fields = ", ".join(f"{f} ({roles.get(f, '?')})" for f in group.fields)
        lines.append(f"Группа {index}{title}: {fields}")
    if seed.comment:
        lines.append("")
        lines.append(f"Комментарий пользователя: {seed.comment}")
    return "\n".join(lines)


def seed_analysis(seed: FieldsSeed, spec: DashboardSpec) -> list[str]:
    """Deterministic layout analysis: what of the seed did NOT survive into the spec.

    Replaces the §3.7 'варианты дашборда' with one spec + honest diff (decision
    2026-06-13, see ARCHITECTURE): the LLM proposes, code reports deviations.
    """
    used: set[str] = set()
    for chart in spec.charts:
        q = chart.query
        for col in (*q.group_columns(), *(m.column for m in q.measures)):
            used.add(f"{q.table}.{col}")
        for qf in q.filters:
            used.add(f"{q.table}.{qf.column}")
    for f in spec.filters:
        used.add(f.column)

    notes: list[str] = []
    for index, group in enumerate(seed.groups, start=1):
        dropped = [f for f in group.fields if f not in used]
        if dropped:
            title = f" «{group.label}»" if group.label else ""
            notes.append(f"поля из группы {index}{title} не вошли в дашборд: {', '.join(dropped)}")
    if len(spec.charts) != len(seed.groups):
        notes.append(f"раскладка из {len(seed.groups)} групп дала {len(spec.charts)} чартов")
    return notes
