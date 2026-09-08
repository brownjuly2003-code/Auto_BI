# Chart-level impact

## Goal

Довести impact-цепочку §8.3 с `dashboard` до `chart`: отчёт должен называть конкретные
чарты, до которых доходит семантическое изменение, и честный typed gap там, где IR
не позволяет это разрешить.

## Tasks

- [x] Red contract tests: chart-ref формат, `computes_metric` vs `reads_field`, typed gaps
      (`raw_sql`, отсутствующий dashboard spec, нетипизированная формула) → Verify: новые
      focused-тесты падают до реализации.
- [x] Реализовать разрешение chart-рефов в `decision/impact.py` (measures на базовой
      таблице, bare/qualified dimension-правило, joins, filters) → Verify: contract tests
      зелёные, unknown/ambiguous вход даёт gap, не догадку.
- [x] Прокинуть dashboards и оба снапшота в `resolve_impact` из release gate → Verify:
      `impact.products` payload содержит chart refs на реальном прогоне.
- [x] Показать чарты и gaps в owner summary → Verify: секция «Whose numbers this reaches»
      называет плитку и относит нерешённые под явный подзаголовок «Charts this run could
      not resolve» — гэп разрешения чарта не проверка, поэтому «What was not done»
      (секция по `CheckResult`) остаётся нетронутой.
- [x] Поднять `VERIFIER_VERSION` (evidence artifact изменился) → Verify: docs-as-code тест
      и документация называют одну и ту же версию.
- [x] Обновить архитектуру и current state → Verify: `docs`-тесты и `git diff --check` зелёные.
- [x] Финальная проверка → Verify: focused decision/verification suite, Ruff, Black.

## Done When

- [x] Каждый chart-ref адресует чарт, который действительно читает изменённое поле или
      считает изменённую метрику; связь типизирована, а не смешана в один список.
- [x] Неразрешимый чарт (`raw_sql`, отсутствующий spec, external/reviewed_sql формула)
      выходит как named gap, а не как «чартов нет».
- [x] Вердикт и грейдинг не меняются: chart-разрешение уточняет адресата, а не оценку.
- [x] Evidence остаётся детерминированным и воспроизводимым.
