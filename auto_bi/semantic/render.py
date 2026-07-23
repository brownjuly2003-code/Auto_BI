"""Compact text rendering of the semantic model for LLM prompts (40k budget).

Free-text fields (description, synonyms, top-values) are untrusted and go through
`prompt_data` sanitization + an envelope preamble (plan_sol step 2 / audit P0-2).
Structural identifiers (table/column names, types, roles, grain, fk) are operator-
authored model shape and are rendered as-is.
"""

from __future__ import annotations

from auto_bi.semantic.model import SemanticModel, Table
from auto_bi.semantic.prompt_data import (
    UNTRUSTED_BLOCK_BEGIN,
    UNTRUSTED_BLOCK_END,
    UNTRUSTED_POLICY,
    samples_for_prompt,
    sanitize_untrusted_text,
)


def untrusted_envelope_overhead() -> int:
    """Fixed char cost of the untrusted envelope (reserved by context selection)."""
    # Matches the f-string layout in render_model when wrap_untrusted=True.
    return len(UNTRUSTED_BLOCK_BEGIN) + 1 + len(UNTRUSTED_POLICY) + 2 + len(UNTRUSTED_BLOCK_END) + 1


def render_model(
    model: SemanticModel,
    *,
    include_samples: bool = False,
    wrap_untrusted: bool = True,
) -> str:
    """Render model text for prompts.

    `wrap_untrusted=False` returns the body only (budget math / tests). LLM call sites
    leave the default True so free-text stays inside the audit P0-2 envelope.
    """
    parts = [render_table(t, include_samples=include_samples) for t in model.tables]
    if model.joins:
        joins = "\n".join(f"  {j.left} -> {j.right} ({j.type})" for j in model.joins)
        parts.append(f"Джойны:\n{joins}")
    if model.metrics:
        metrics = "\n".join(
            f"  {m.name} = {m.sql}"
            + (f" — {sanitize_untrusted_text(m.description)}" if m.description else "")
            for m in model.metrics
        )
        parts.append(f"Метрики:\n{metrics}")
    body = "\n\n".join(parts)
    if not body or not wrap_untrusted:
        return body
    return (
        f"{UNTRUSTED_BLOCK_BEGIN}\n" f"{UNTRUSTED_POLICY}\n\n" f"{body}\n" f"{UNTRUSTED_BLOCK_END}"
    )


def render_table(table: Table, *, include_samples: bool = False) -> str:
    header = f"Таблица {table.name}"
    if table.description:
        header += f" — {sanitize_untrusted_text(table.description)}"
    if table.synonyms:  # hand-authored vocabulary (X-3), not data samples — never gated
        syns = ", ".join(sanitize_untrusted_text(s) for s in table.synonyms)
        header += f" [синонимы: {syns}]"
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
            col += f": {sanitize_untrusted_text(c.description)}"
        if c.synonyms:
            syns = ", ".join(sanitize_untrusted_text(s) for s in c.synonyms)
            col += f" [синонимы: {syns}]"
        if c.fk:
            col += f" [fk: {c.fk}]"
        # Samples: opt-in + classification + sanitize/cap (AUTO_BI_SEND_SAMPLES).
        samples = samples_for_prompt(include_samples=include_samples, table=table, column=c)
        if samples:
            col += f" [значения: {', '.join(samples)}]"
        lines.append(col)
    return "\n".join(lines)


def _human_rows(rows: int) -> str:
    if rows >= 1_000_000:
        return f"{rows / 1_000_000:.0f}M"
    if rows >= 1_000:
        return f"{rows / 1_000:.0f}K"
    return str(rows)
