# Полная offline-проверка после T-83

Дата: 2026-10-22. Проверенный код: `b75edef`.
Среда: нативный Windows, Python 3.13.7; duckdb подключён через uv.

## Результат

**6551 passed, 109 skipped, 38 deselected in 527.35s**, exit 0.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
uv run --frozen --with duckdb pytest -q --tb=short
```

Один полный offline-прогон после T-82/T-83 закрывает пробел между их профильными
проверками и прежним полным baseline после T-81 (6523 passed на `e05703c`).
Production-код, тесты и версии verifier/check 98/73 не менялись.

## Выбор и границы

Synthetic `gpt-6-astra` xhigh review по ADR 0006 подтвердило выбор полной
offline-проверки по текущему состоянию и отчёту T-83. Конкретного обязательного
следующего дефекта в рассмотренных источниках не найдено. Это review выбора
задачи, не утверждение об отсутствии дефектов и не human/production acceptance.
Прогон независимо выполнен Codex; требуемая `gpt-5.6-luna` недоступна.

109 skipped не считаются проверенным поведением; 38 deselected исключены
конфигурацией pytest (`not integration and not e2e`). Live DWH, Mac и контейнеры
не проверялись. Отдельные lint/type-check gates в этом прогоне не выполнялись.
Прежний нестабильный SQLite performance test не отказал; причина прежнего
скачка времени этим не установлена.
