"""Direct contract tests for the dm_change_request renderer (task 2.5).

`render_dm_change_request` is a first-class deliverable — the document the DM owner
acts on when the advisor finds a request the mart can't serve. It is covered indirectly
through the API, but these lock its markdown shape directly: headings, optional lines,
and the fallbacks for a sparse store row.
"""

from auto_bi.dmcr import DCR_STATUSES, render_dm_change_request


def _full_row() -> dict:
    return {
        "table_name": "dm.sales_daily",
        "created_at": "2026-06-25T10:00:00",
        "severity": "high",
        "rule": "no_filter_large_fact",
        "status": "submitted",
        "session_request": "выручка по менеджерам за год",
        "session_id": "sess-123",
        "narrative": "Витрина агрегирована по дню/магазину; разреза по менеджеру нет.",
    }


def test_full_row_renders_all_sections() -> None:
    md = render_dm_change_request(_full_row())

    assert md.startswith("# Заявка на изменение витрины: `dm.sales_daily`")
    assert "- **Severity:** high" in md
    assert "- **Правило advisor:** no_filter_large_fact" in md
    assert "- **Статус заявки:** submitted" in md
    assert "- **Запрос пользователя:** «выручка по менеджерам за год»" in md
    assert "- **Сессия:** sess-123" in md
    assert "## Обоснование (вердикт advisor)" in md
    assert "Витрина агрегирована по дню/магазину" in md
    assert "## Что просим" in md


def test_minimal_row_uses_defaults() -> None:
    md = render_dm_change_request({})

    # no table -> generic placeholder, status defaults to the lifecycle start
    assert "# Заявка на изменение витрины: `DM`" in md
    assert "- **Статус заявки:** open" in md
    # the two structural sections are always present
    assert "## Обоснование (вердикт advisor)" in md
    assert "## Что просим" in md


def test_missing_narrative_falls_back() -> None:
    md = render_dm_change_request({"table_name": "dm.x", "rule": "r"})

    assert "_нарратив не сохранён — см. правило выше_" in md


def test_optional_lines_omitted_without_request_or_session() -> None:
    md = render_dm_change_request({"table_name": "dm.x", "narrative": "n"})

    assert "Запрос пользователя:" not in md
    assert "- **Сессия:**" not in md


def test_open_is_a_known_status() -> None:
    # the renderer's default status must be a real lifecycle value
    assert "open" in DCR_STATUSES


def _row_with_remediation() -> dict:
    row = _full_row()
    row["remediation"] = (
        '[{"kind": "ch_projection", "summary": "проекция по manager_id",'
        ' "rationale": "таблица отсортирована по date", '
        '"ddl": "ALTER TABLE dm.sales_daily ADD PROJECTION p_by_manager_id (...);"}]'
    )
    return row


def test_remediation_section_renders_ddl() -> None:
    md = render_dm_change_request(_row_with_remediation())

    assert "## Предлагаемое решение (готовый артефакт)" in md
    assert "**проекция по manager_id**" in md
    assert "таблица отсортирована по date" in md
    assert "```sql" in md
    assert "ADD PROJECTION p_by_manager_id" in md
    # the closing ask points at the attached artifact instead of the generic wording
    assert "готовый артефакт-решение приложен выше" in md


def test_no_remediation_keeps_generic_ask() -> None:
    md = render_dm_change_request(_full_row())  # no remediation key

    assert "## Предлагаемое решение" not in md
    assert "новую витрину/колонку" in md  # the generic ask is preserved


def test_malformed_remediation_degrades_without_section() -> None:
    row = _full_row()
    row["remediation"] = "not json at all"
    md = render_dm_change_request(row)

    # advisory-only: a malformed artifact never errors, it just drops the section
    assert "## Предлагаемое решение" not in md
    assert "## Что просим" in md
