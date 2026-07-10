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
from collections.abc import Iterable

from auto_bi.semantic.model import Column, SemanticModel, Table
from auto_bi.semantic.render import render_model, render_table

logger = logging.getLogger(__name__)

PROMPT_CHAR_BUDGET = 40_000  # GraceKelly hard limit on the whole prompt

_WORD_RE = re.compile(r"[^0-9a-zа-яё]+")
_MIN_WORD = 3
_STEM_LEN = 5


def _stems(text: str) -> set[str]:
    words = (w for w in _WORD_RE.split(text.lower()) if len(w) >= _MIN_WORD)
    return {w[:_STEM_LEN] for w in words}


def _column_score(column: Column, request_stems: set[str]) -> float:
    score = 0.0
    score += 2.0 * len(request_stems & _stems(column.name))
    # synonyms are alternate names, so they weigh like the name: «удержание» must pull
    # the retention column exactly as its physical name would (X-3)
    score += 2.0 * len(request_stems & _stems(" ".join(column.synonyms)))
    score += 1.0 * len(request_stems & _stems(column.description))
    if column.top_values:
        # the user may name a concrete value ("алкоголь", "Москва"), not a column
        score += 2.0 * len(request_stems & _stems(" ".join(column.top_values)))
    return score


def _table_score(table: Table, request_stems: set[str]) -> float:
    """Lexical relevance: weighted stem overlap between the request and the table."""
    score = 0.0
    score += 3.0 * len(request_stems & _stems(table.name))
    score += 3.0 * len(request_stems & _stems(" ".join(table.synonyms)))  # alternate names
    score += 2.0 * len(request_stems & _stems(table.description))
    for c in table.columns:
        score += _column_score(c, request_stems)
    return score


def select_context(
    model: SemanticModel,
    request: str,
    *,
    budget_chars: int,
    include_samples: bool = True,
    pinned: Iterable[str] = (),
) -> SemanticModel:
    """Sub-model whose rendered text fits budget_chars, most request-relevant first.

    Joins are kept only when both endpoints survive; metrics are small and kept as-is,
    but both are charged against the budget up front so the rendered model never busts
    the prompt limit. `pinned` tables (e.g. tables of an existing spec in patch_spec)
    are always included regardless of score. A mandatory table (pinned, or the
    top-scoring one when nothing is pinned) that alone busts the budget loses samples
    first, then its least request-relevant columns — never the whole table.
    """
    request_stems = _stems(request)
    ranked = sorted(
        enumerate(model.tables),
        key=lambda item: (-_table_score(item[1], request_stems), item[0]),
    )

    # joins/metrics render into the same prompt: reserve their (upper-bound) cost
    overhead = 0
    if model.joins or model.metrics:
        extras = SemanticModel(tables=[], joins=model.joins, metrics=model.metrics)
        overhead = len(render_model(extras, include_samples=include_samples)) + 2
    budget = budget_chars - overhead

    pinned_names = set(pinned)
    mandatory = [t for t in model.tables if t.name in pinned_names]
    if not mandatory and ranked:
        mandatory = [ranked[0][1]]  # top table always included (empty model is worse)

    selected: dict[str, Table] = {}
    used = 0
    for table in mandatory:
        table, cost = _fit_mandatory(table, budget - used, request_stems, include_samples)
        selected[table.name] = table
        used += cost

    for _index, table in ranked:
        if table.name in selected:
            continue
        cost = len(render_table(table, include_samples=include_samples)) + 2  # "\n\n" join
        if used + cost > budget and include_samples and _has_samples(table):
            # drop the samples physically, so every later render stays within cost
            table = _strip_samples(table)
            cost = len(render_table(table)) + 2
        if used + cost > budget:
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


def _fit_mandatory(
    table: Table, budget: int, request_stems: set[str], include_samples: bool
) -> tuple[Table, int]:
    """A table that must be sent: degrade (samples -> columns) until it fits.

    Trimming keeps the most request-relevant columns, so in patch_spec the columns an
    existing spec references survive (they appear in the scoring text). This guarantees
    the rendered prompt stays under the GraceKelly hard limit instead of raising
    LLMError at call time.
    """
    cost = len(render_table(table, include_samples=include_samples)) + 2
    if cost <= budget:
        return table, cost
    if include_samples and _has_samples(table):
        table = _strip_samples(table)
        cost = len(render_table(table)) + 2
        if cost <= budget:
            return table, cost

    by_relevance = sorted(
        enumerate(table.columns),
        key=lambda item: (-_column_score(item[1], request_stems), item[0]),
    )
    kept_indexes: list[int] = []
    for index, _column in by_relevance:
        candidate = table.model_copy(
            update={"columns": [table.columns[i] for i in sorted([*kept_indexes, index])]}
        )
        if len(render_table(candidate)) + 2 > budget:
            continue  # a shorter lower-relevance column may still fit
        kept_indexes.append(index)
    trimmed = table.model_copy(update={"columns": [table.columns[i] for i in sorted(kept_indexes)]})
    logger.warning(
        "mandatory table %s over budget: trimmed to %d/%d most relevant columns",
        table.name,
        len(kept_indexes),
        len(table.columns),
    )
    return trimmed, len(render_table(trimmed)) + 2


def _has_samples(table: Table) -> bool:
    return any(c.top_values for c in table.columns)


def _strip_samples(table: Table) -> Table:
    columns = [c.model_copy(update={"top_values": []}) for c in table.columns]
    return table.model_copy(update={"columns": columns})
