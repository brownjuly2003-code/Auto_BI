"""Compact text rendering of the semantic model for LLM prompts (40k budget)."""

from auto_bi.semantic.model import SemanticModel, Table


def render_model(model: SemanticModel, *, include_samples: bool = True) -> str:
    parts = [render_table(t, include_samples=include_samples) for t in model.tables]
    if model.joins:
        joins = "\n".join(f"  {j.left} -> {j.right} ({j.type})" for j in model.joins)
        parts.append(f"Джойны:\n{joins}")
    if model.metrics:
        metrics = "\n".join(
            f"  {m.name} = {m.sql}" + (f" — {m.description}" if m.description else "")
            for m in model.metrics
        )
        parts.append(f"Метрики:\n{metrics}")
    return "\n\n".join(parts)


def render_table(table: Table, *, include_samples: bool = True) -> str:
    header = f"Таблица {table.name}"
    if table.description:
        header += f" — {table.description}"
    if table.synonyms:  # hand-authored vocabulary (X-3), not data samples — never gated
        header += f" [синонимы: {', '.join(table.synonyms)}]"
    if table.physical and table.physical.rows:
        header += f" ({_human_rows(table.physical.rows)} строк)"
    lines = [header]
    if table.grain:
        lines.append(f"  грейн: {', '.join(table.grain)}")
    for c in table.columns:
        col = f"  - {c.name} ({c.type}, {c.role.value}"
        if c.agg:
            col += f", {c.agg.value}"
        if c.additivity:  # e.g. "non_additive": tells the LLM up front that sum is invalid
            col += f", {c.additivity.value}"
        col += ")"
        if c.description:
            col += f": {c.description}"
        if c.synonyms:
            col += f" [синонимы: {', '.join(c.synonyms)}]"
        if c.fk:
            col += f" [fk: {c.fk}]"
        if c.top_values and include_samples:  # gated by AUTO_BI_SEND_SAMPLES (sensitive DMs)
            col += f" [значения: {', '.join(c.top_values[:10])}]"
        lines.append(col)
    return "\n".join(lines)


def _human_rows(rows: int) -> str:
    if rows >= 1_000_000:
        return f"{rows / 1_000_000:.0f}M"
    if rows >= 1_000:
        return f"{rows / 1_000:.0f}K"
    return str(rows)
