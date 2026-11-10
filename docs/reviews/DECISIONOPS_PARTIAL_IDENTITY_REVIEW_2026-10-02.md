# PostgreSQL partial role identity — synthetic review

**Дата:** 2026-10-02
**Выбор среза:** `gpt-6-astra`, reasoning `xhigh`, по ADR 0006 на `d488bff`.
**Итоговое review:** `gpt-6-astra`, reasoning `xhigh`: 0 substantive findings.
**Полный offline gate:** 3567 passed, 5 skipped, 38 deselected; exit 0.

Модельная оценка не является human/design-partner acceptance, измерением времени
человека или production acceptance. Контракт:
[environment access](../specs/environment-access.md).

Astra воспроизвела семь частичных/NULL identity-ответов через существующий offline
seam: при отрицательных grants каждый дал `read_only=true`,
`bypasses_row_security=false`, отсутствие access findings и один runtime-call.
Полный отрицательный identity-control сохранил PASS; доказанный superuser без
отдельного BYPASSRLS-ответа сохранил BLOCK. Это дефект обработки ответов и допуска
runtime, а не демонстрация записи в DWH или частоты таких ответов native PostgreSQL.

Выбран контракт: отсутствие/NULL identity-поля или пустое имя не подтверждают
read-only. Известное имя сохраняется, неизвестное называется `unknown`; evidence
называет неотвеченные поля. Write-privilege вопросы и exact runtime не выполняются,
публичный `dwh.access` блокируется через существующий `access.role_writable`.
Обход RLS подтверждается любым положительным флагом superuser/BYPASSRLS, опровергается
только двумя известными отрицательными ответами, иначе остаётся null. Доказанный
superuser сохраняет прежнее writable evidence и true bypass даже при неполной строке.
Полные nonsuperuser-ответы сохраняют правила grants; ошибки соединения пробрасываются.

До production-правки Luna xhigh добавила регрессии и выполнила
`uv run --no-sync pytest -q --tb=short tests/test_postgres_role_identity.py`:
7 failed, 32 passed за 4.96 с, exit 1. Codex независимо прочитал diff и failure log,
исправил scope SELECT-проверки через исполнителя и расширил матрицу для пропущенных
identity-полей и всех четырёх engine alias. Повторный red до production-правки:
40 failed, 40 passed за 14.01 с, exit 1. Отдельное независимое воспроизведение Codex
`-k 'partial_postgres_identity and missing-bypassrls'`: 4 failed, 76 deselected,
exit 1 — неизвестный bypass разрешал runtime при отрицательных grants.

После первоначальной production-правки Codex выполнил
`uv run --no-sync pytest -q --tb=short tests/test_postgres_role_identity.py
tests/test_postgres_column_write_privileges.py tests/test_environment_probe.py
tests/test_environment_checks.py tests/test_environment_question_identity.py
tests/test_decision_verify.py tests/test_runtime_probe.py`: 353 passed за 33.26 с,
exit 0. Полные identity, доказанные superusers, отказ/пустой ответ и ошибки соединения
сохранили контролируемые исходы; ClickHouse regression tests прошли.

Содержательное review выявило существовавшую в baseline нормализацию quoted-имён
через `NonBlankText`: padded name терял пробелы, whitespace-only имя не проходило
валидацию. Для буквального сохранения identity изменены только `RoleFacts.name` и
`EnvironmentFinding.subject` на непустую строку без trim; общий scalar прежний.
Это сохраняет точное имя на всём пути до публичных `subject_refs` без нового encoding.

Luna добавила 16 exact-name controls и получила red: 16 failed за 3.67 с, exit 1.
Codex расширил их до публичного gate и runtime, добавил четыре ClickHouse controls.
Независимый red перед этой поправкой:
`uv run --no-sync pytest -q --tb=short tests/test_postgres_role_identity.py
tests/test_environment_probe.py -k preserves_exact`:
20 failed, 130 deselected за 1.58 с, exit 1. Полные identity с quoted-именами должны
сохранять PASS и runtime; partial — BLOCK без runtime, с точными именем и subject refs.

На окончательной реализации выполнен единственный полный
`uv run --no-sync --with duckdb pytest -q --tb=short`:
3567 passed, 5 skipped, 38 deselected за 161.28 с, exit 0.
Финальное targeted synthetic Astra xhigh review: 0 substantive findings;
withholding, superuser priority, tri-state bypass и fidelity `subject_refs` подтверждены.

`uv run --no-sync ruff check auto_bi tests`: чисто, exit 0.
Black на пяти изменённых Python-файлах: чисто, exit 0.
`uv run --no-sync --with mypy --with types-PyYAML mypy --strict auto_bi`:
no issues in 120 source files, exit 0.

Версии — verifier 67 / check 47; исторические bundles сохраняются.

## Границы

SQL identity-вопрос и общий boolean-normalizer не меняются. `NOINHERIT` / `SET ROLE`,
новая SQL-интроспекция и живой Greengage вне среза. Сохраняется отдельная граница
security-grading при неизвестном bypass; этот срез исправляет role facts, доступ
и runtime withholding.

При выборе также выявлен отдельный ClickHouse-кандидат: отказ
`system.enabled_roles` сейчас выглядит как пустой список и даёт неполный набор
principals для grants и row policies. Его исправление потребует согласованного
среза access, column reach и policy applicability; здесь он не реализуется.
