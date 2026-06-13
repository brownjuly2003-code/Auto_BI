"""dm_change_request: структурированная заявка владельцу DM (task 2.5).

The advisor already decided the verdict deterministically and the LLM narrated it
(task 1.7); the store keeps one row per (table, rule) per session. This module only
renders that row into a document the DM owner can act on — no LLM, no new facts.
"""

from __future__ import annotations

from typing import Any

# lifecycle as the DM owner sees it; the store keeps the string as-is
DCR_STATUSES = ("open", "submitted", "accepted", "rejected")


def render_dm_change_request(row: dict[str, Any]) -> str:
    """Markdown заявка from a store row (`Store.dm_change_request`)."""
    table = row.get("table_name") or "DM"
    lines = [
        f"# Заявка на изменение витрины: `{table}`",
        "",
        f"- **Дата:** {row.get('created_at', '')}",
        f"- **Severity:** {row.get('severity', '')}",
        f"- **Правило advisor:** {row.get('rule', '')}",
        f"- **Статус заявки:** {row.get('status', 'open')}",
    ]
    request = row.get("session_request")
    if request:
        lines.append(f"- **Запрос пользователя:** «{request}»")
    if row.get("session_id"):
        lines.append(f"- **Сессия:** {row['session_id']}")
    lines += [
        "",
        "## Обоснование (вердикт advisor)",
        "",
        row.get("narrative") or "_нарратив не сохранён — см. правило выше_",
        "",
        "## Что просим",
        "",
        "Запрос пользователя не предусмотрен текущей витриной (см. правило и "
        "evidence в обосновании). Просим оценить изменение DM: новую витрину/колонку "
        "или другой ключ сортировки/партиционирования под этот класс запросов.",
        "",
    ]
    return "\n".join(lines)
