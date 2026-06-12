"""Context selection: fit the semantic model into the LLM prompt budget (task 1.5).

Deterministic, no LLM (the model has not seen the DM yet, so selection cannot ask
it): tables are scored by lexical overlap with the request and greedily rendered
into the character budget, highest score first. On small DMs everything fits and
selection is the identity, so Phase 0 behaviour is unchanged.

Russian/English inflections are handled by a naive prefix stem (первые 5 символов):
«магазинам» и «магазин» совпадают, точность не нужна — ложное включение таблицы
безопасно, ложное исключение чинится бюджетом (таблицы добавляются, пока влезают).
"""

from __future__ import annotations

import logging
import re

from auto_bi.semantic.model import SemanticModel, Table
from auto_bi.semantic.render import render_table

logger = logging.getLogger(__name__)

PROMPT_CHAR_BUDGET = 40_000  # GraceKelly hard limit on the whole prompt

_WORD_RE = re.compile(r"[^0-9a-zа-яё]+")
_MIN_WORD = 3
_STEM_LEN = 5


def _stems(text: str) -> set[str]:
    words = (w for w in _WORD_RE.split(text.lower()) if len(w) >= _MIN_WORD)
    return {w[:_STEM_LEN] for w in words}


def _table_score(table: Table, request_stems: set[str]) -> float:
    """Lexical relevance: weighted stem overlap between the request and the table."""
    score = 0.0
    score += 3.0 * len(request_stems & _stems(table.name))
    score += 2.0 * len(request_stems & _stems(table.description))
    for c in table.columns:
        score += 2.0 * len(request_stems & _stems(c.name))
        score += 1.0 * len(request_stems & _stems(c.description))
        if c.top_values:
            # the user may name a concrete value ("алкоголь", "Москва"), not a column
            score += 2.0 * len(request_stems & _stems(" ".join(c.top_values)))
    return score


def select_context(
    model: SemanticModel,
    request: str,
    *,
    budget_chars: int,
    include_samples: bool = True,
) -> SemanticModel:
    """Sub-model whose rendered text fits budget_chars, most request-relevant first.

    Joins are kept only when both endpoints survive; metrics are small and kept as-is.
    The top-scoring table is always included: if it alone busts the budget even without
    samples, it is still sent (a prompt slightly over budget beats an empty model).
    """
    request_stems = _stems(request)
    ranked = sorted(
        enumerate(model.tables),
        key=lambda item: (-_table_score(item[1], request_stems), item[0]),
    )

    selected: dict[str, Table] = {}
    used = 0
    for _index, table in ranked:
        cost = len(render_table(table, include_samples=include_samples)) + 2  # "\n\n" join
        if used + cost > budget_chars and include_samples and _has_samples(table):
            # drop the samples physically, so every later render stays within cost
            table = _strip_samples(table)
            cost = len(render_table(table)) + 2
        if used + cost > budget_chars and selected:
            continue  # keep scanning: a smaller lower-ranked table may still fit
        selected[table.name] = table
        used += cost

    dropped = [t.name for t in model.tables if t.name not in selected]
    if dropped:
        logger.warning(
            "context selection dropped %d/%d tables over %d-char budget: %s",
            len(dropped),
            len(model.tables),
            budget_chars,
            dropped,
        )

    def _join_survives(join_left: str, join_right: str) -> bool:
        return any(join_left.startswith(f"{n}.") for n in selected) and any(
            join_right.startswith(f"{n}.") for n in selected
        )

    return SemanticModel(
        tables=[selected[t.name] for t in model.tables if t.name in selected],  # original order
        joins=[j for j in model.joins if _join_survives(j.left, j.right)],
        metrics=model.metrics,
    )


def _has_samples(table: Table) -> bool:
    return any(c.top_values for c in table.columns)


def _strip_samples(table: Table) -> Table:
    columns = [c.model_copy(update={"top_values": []}) for c in table.columns]
    return table.model_copy(update={"columns": columns})
