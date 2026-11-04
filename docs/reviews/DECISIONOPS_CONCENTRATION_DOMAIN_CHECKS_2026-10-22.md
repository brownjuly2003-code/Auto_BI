# T-81 — отрицательные исходные значения концентрации

Дата: 2026-10-22. Исходный HEAD: `58d4a7a`.
Выбор среза: synthetic Astra xhigh по ADR 0006.
Реализация: Codex; требуемая `gpt-5.6-luna` недоступна.
Модельная оценка не является human/production acceptance.

## Проблема и граница

При dimensions `category, region` строки `A/east=100`, `A/west=-50`, `B/east=50`
давали положительные суммы категорий. Проверка отрицательности выполнялась над
этими суммами, и шаг выпускал concentration result и claims вопреки входной
предпосылке `non_negative_values`.

Теперь на прежнем месте проверяются исходные строки: любая отрицательная величина
даёт существующий `method.negative_value` GAP. Это относится к обоим периодам,
включая компенсацию до нуля и значения меньше точности отображения долей.
Приоритет input/additivity/group gaps сохранён; общего механизма исполнения
декларируемых preconditions не добавлено. Variance и contribution допускают
отрицательные значения как прежде. CLI сохраняет GAP в evidence и завершает
корректное offline investigation с exit 0, без concentration claims.

Skill version остаётся 2: формулы, округление, схема и outputs для допустимых
неотрицательных входов не меняются. Для ранее ошибочно принятого отрицательного
входа новые outputs/brief и их digests меняются; plan и canonical input прежние.
Исторические outputs продолжают читаться. Verifier 98/check 73 не затронуты.

## Проверки

- Red до production-правки: **14 failed, 7 passed**, exit 1. Отказы доказывают
  ошибочный success при положительной/нулевой групповой сумме и в CLI.
- После исправления: **21 passed**, exit 0. Затем добавлены шесть проверок
  приоритета прежних diagnostics.
- Весь investigation-набор: **375 passed in 8.51s**, exit 0, включая 27 новых cases.
- Ruff и Black двух изменённых Python-файлов: exit 0.
- Strict mypy Linux: **122 source files**, exit 0.
- Сравнение с извлечённой из исходного HEAD функцией: четыре payload обычного
  fixture побайтно совпали. Исторический success для отрицательного входа
  по-прежнему читается и рендерится без изменений; его истинность не переоценивается.
- Synthetic Astra xhigh review кода/тестов: **0 открытых P1/P2**. Независимый
  профиль — **27 passed**; повторное сравнение четырёх payload и чтение старого
  brief прошли. Это модельная оценка, не human/production acceptance.
- Docs-as-code/defaults: **32 passed in 0.73s**, exit 0. Второй целевой проход
  Astra проверил согласованность документации: **0 открытых P1/P2**.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q tests/test_investigation_concentration_domain.py --tb=short
$tests = @(rg --files tests -g 'test_investigation*.py')
.venv\Scripts\python.exe -m pytest -q @tests --tb=short
.venv\Scripts\python.exe -m ruff check auto_bi/investigation/skills.py tests/test_investigation_concentration_domain.py
.venv\Scripts\python.exe -m black --check auto_bi/investigation/skills.py tests/test_investigation_concentration_domain.py
uv run --frozen --no-sync --with mypy --with types-PyYAML mypy --platform linux --strict auto_bi
.venv\Scripts\python.exe -m pytest -q tests/test_docs_as_code.py tests/test_docs_defaults.py --tb=short
```

Полный offline suite и live DWH этим срезом не запускались.
