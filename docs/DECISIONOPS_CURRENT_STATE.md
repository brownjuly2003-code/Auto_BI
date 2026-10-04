# Auto_BI DecisionOps — текущее состояние

**Обновлено:** 2026-10-04

**Стадия:** release gate bootstrap завершён; offline investigation alpha разрешён;
impact доведён до чарта; происхождение кандидатного числа проверяется вместе с
привязкой его reviewed-запроса к governed-модели; read-only capture reviewed-запроса
реализован и проверен на живых PostgreSQL и ClickHouse; evidence manifest получает
opt-in Ed25519 authentication; единый signed R3 owner-пакет выпущен на Mac;
Astra xhigh review R3/R4 выполнено по ADR 0006; owner-summary поясняет границы evidence;
human/design-partner outcome не измерен и не требуется для продолжения разработки

**Версии evidence:** `VERIFIER_VERSION=89`, `CHECK_VERSION=65`

**Последний срез (2026-10-04):** `contracts.validate` сравнивает validated-контракт
каждой запинненной метрики с определением candidate на запинненной версии по
изменениям semantic diff. Если base держит validated definition, изменение под
review остаётся у `semantic.diff` и `impact.products`. Иначе pin получает
`content_mismatch` с `metric_ref.candidate_content_mismatch`: block при review
или block хотя бы одного отличия, advisory при одних advisory. Запись pin несёт
`differences` в порядке diff. Правило версий, `decision validate`, validation
report, остальные checks и состав run id прежние. Версии evidence 88 → 89 / 64 → 65;
исторические bundles не переписаны.
Выбор — оркестратор: пробел назван альтернативой synthetic Astra xhigh выбора, давшего
T-43, и границей спеки T-40; консультацию после срыва Codex-попытки дал
`claude-fable-5-1` xhigh — не Astra-выбор по ADR 0006, его правило «любая разница
блокирует» отклонено по owner-пакетам. Реализовано `gpt-6.1-sol` medium через MyFlow
`sol-high`: GLM-5.3 был в недельном лимите z.ai. Red на HEAD: 38 failed / 15 passed;
новый модуль: 53 passed. Synthetic Astra xhigh review: 0 findings. Полный offline gate с
duckdb: 4502 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
[Проверки и границы](DECISIONOPS_PIN_CONTENTS_REVIEW_2026-10-04.md).
Human/production acceptance не заявляется.

**Предыдущий срез (2026-10-04):** investigation skills считают supplied decimal strings
точно при любой длине: точная сумма, разность и ranking не теряют цифры после 28-го
знака и не зависят от decimal context вызывающего потока или порядка строк.
Отношения сохраняют шесть знаков, rounding — `HALF_EVEN`; потеря цифр в сумме
больше не становится ложной наблюдаемой дельтой. Длинное значение и длинное окно
freshness не обрывают команду на лимите преобразования integer/text. `data_health`
сравнивает точный возраст в микросекундах с окном из общего `duration_seconds`,
без ложного `pass` при короткой precision. План объявляет версии skills, brief
отказывается от output другой версии. Числовые skills — `skill_version` 2,
`data_health` — 1 с прежним digest при default context. Версии evidence 88 / 64
и исторические bundles прежние. Выбор — synthetic Astra xhigh; точный возраст и
общий парсер окна в `data_health` добавил оркестратор по воспроизведённым отказам.
Реализовано `gpt-6.1-sol` medium через MyFlow `sol-high`: GLM-5.3 был в недельном
лимите z.ai. Red на HEAD: 89 failed / 0 passed (HEAD не принимает шаг версии 2);
на копии HEAD с одними поднятыми версиями skill: 45 failed / 44 passed; новый
модуль: 89 passed. Synthetic Astra xhigh review: 0 findings. Полный offline gate
с duckdb: 4449 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
[Проверки и границы](DECISIONOPS_INVESTIGATION_EXACT_ARITHMETIC_REVIEW_2026-10-04.md).
Human/production acceptance не заявляется.

**Предыдущий срез (2026-10-04):** `assets.invalidated` резолвит каждый metric pin
каждого выбранного verified asset в candidate snapshot. Другая версия, несколько
контрактов или отсутствие метрики блокируют выпуск: `asset.metric_ref_version_mismatch`,
`asset.metric_ref_ambiguous`, `asset.metric_ref_absent`. Findings семантического
изменения, pins и reviewed-запроса сохраняются в этом порядке; base не сравнивается.
Payload несёт `metric_pins`, clean detail считает совпавшие pins. Выбор assets,
`contracts.validate`, `results.reconcile`, `results.provenance`, `decision validate`
и not-run при непрочитанном контракте прежние. Версии evidence 87 → 88 / 63 → 64;
исторические bundles не переписаны. Выбор — synthetic Astra xhigh.
Реализовано `gpt-6.1-sol` medium через MyFlow `sol-high`: GLM-5.3 был в недельном
лимите z.ai. Red на HEAD: 23 failed / 1 passed; новый модуль: 24 passed.
Synthetic Astra xhigh review: 0 findings. Полный offline gate с duckdb:
4360 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
[Проверки и границы](DECISIONOPS_ASSET_METRIC_PINS_REVIEW_2026-10-04.md).
Human/production acceptance не заявляется.

**Предыдущий срез (2026-10-04):** ClickHouse own-grant строка без `access_type`
(missing key, NULL или пустое значение) больше не читается как отсутствие grant.
Это неполный ответ: `readonly=1/2`, доказанный write grant и неполные enabled roles
сохраняют прежнее evidence; иначе read-only не установлен, `dwh.access` блокируется,
reach-вопросы и exact runtime SQL не отправляются. Column reach неизвестен при любом
`readonly`: `column_reads=null`, `dwh.security` — review с `security.not_observed`,
без ложных unreadable, physical absence и unenforced classification. Row-policy
facts, typed answers, отказ grants-вопроса, вопросы и их порядок, PostgreSQL прежние.
Версии evidence 86 → 87 / 62 → 63; исторические bundles не переписаны.
Выбор — альтернатива №1 synthetic Astra xhigh выбора, давшего T-40.
Движок такую строку вернуть не может (не-nullable `Enum16`, ClickHouse 24.8.14.39,
вживую): срез закрывает неполный ответ слоя между движком и коллектором.
Реализовано `gpt-6.1-sol` medium через MyFlow `sol-high`: GLM-5.3 был в недельном
лимите z.ai. Red на HEAD: 15 failed / 8 passed; новый модуль: 23 passed.
Synthetic Astra xhigh review: 0 findings. Полный offline gate с duckdb:
4336 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
[Проверки и границы](DECISIONOPS_UNTYPED_GRANTS_REVIEW_2026-10-04.md).
Human/production acceptance не заявляется.

**Предыдущий срез (2026-10-03):** PostgreSQL-family ядро без row-level security
грейдится по каталогам, которые у него есть. Прежний identity-вопрос падал на
отсутствующем `rolbypassrls` (ядро PostgreSQL 9.4.26, вживую). Только после
undefined column collector спрашивает `server_version_num`; ниже `90500` повторяет
identity без `BYPASSRLS`, сохраняет прежние правила роли и runtime SQL, не спрашивает
RLS-каталоги витрин и записывает версию в facts. Clean `dwh.security` с покрытием
называет ядро, без читаемых витрин его текст прежний; classification без ограничения
сохраняет review. Ответившее на identity ядро,
ClickHouse и остальные undefined-column случаи сохраняют прежние правила.
Границы — собственные каталоги и EXPLAIN Greenplum/Greengage, PostgreSQL 9.5/9.6
без `polpermissive` и ядра старше 9.4. Версии evidence 85 → 86 / 61 → 62;
исторические bundles не переписаны. Выбор — оркестратор по v2-скоупу
Greenplum/Greengage; живые факты сняты на ядре PostgreSQL 9.4.26 и PostgreSQL 16.15
на Mac (одноразовые кластеры, удалены). Реализовано `gpt-6.1-sol` medium через
MyFlow `sol-high`: GLM-5.3 был в недельном лимите z.ai. Red на HEAD: 151 failed /
12 passed; новый модуль: 163 passed. Synthetic Astra xhigh review: 0 findings.
Полный offline gate с duckdb: 4313 passed, 4 skipped, 38 deselected, exit 0;
Black/Ruff/strict mypy чистые.
[Проверки и границы](DECISIONOPS_KERNEL_ROW_SECURITY_REVIEW_2026-10-03.md).
Human/production acceptance не заявляется.

**Предыдущий срез (2026-10-03):** `contracts.validate` резолвит каждый metric pin
продукта в candidate snapshot: ровно один контракт на запинненной версии.
Другая версия, несколько контрактов или отсутствие блокируют шаг с
`metric_ref.candidate_version_mismatch`, `metric_ref.candidate_ambiguous`,
`metric_ref.candidate_absent`. Base и метрики вне pins продукта не сравниваются;
отсутствующий узел при `gap.contract_invalid` назван `not_compared`, а найденный
грейдится как обычно. Payload сохраняет validation issues и добавляет
`candidate_pins`; validation report не меняется. Содержимое контрактов одной
версии и pins verified assets не сравниваются. `decision validate`, diff,
impact и reference SQL сохраняют свои правила. Версии evidence 84 → 85 / 60 → 61;
исторические bundles не переписаны. Выбор — synthetic Astra xhigh; правило
«ровно один контракт» добавил оркестратор по воспроизведённому расхождению diff
и компилятора reference SQL при двух контрактах одного id. Реализовано
`gpt-6.1-sol` medium через MyFlow `sol-high`: GLM-5.3 был в недельном лимите z.ai.
Red на HEAD: 19 failed / 0 passed; новый модуль: 19 passed. Synthetic Astra xhigh
review: 0 findings. Полный offline gate с duckdb: 4150 passed, 4 skipped,
38 deselected, exit 0; Black/Ruff/strict mypy чистые.
[Проверки и границы](DECISIONOPS_METRIC_PINS_REVIEW_2026-10-03.md).
Human/production acceptance не заявляется.

**Предыдущий срез (2026-10-03):** PostgreSQL-family identity-вопрос читает
superuser-флаг текущей роли из `pg_roles.rolsuper`: у current role без LOGIN
нет строки в `pg_user`, поэтому прежний вопрос отвечал NULL и блокировал
select-only роль как unreported (PostgreSQL 16.15, вживую). Теперь NOLOGIN
роль грейдится по своим флагам и write-привилегиям, включая роли, которые
session user достигает через `SET ROLE`: select-only роль сохраняет read-only
и eligible exact runtime SQL, NOLOGIN superuser получает прежнее superuser
evidence и block без privilege-вопросов и runtime SQL. Отказ, пустой,
частичный и NULL identity-ответ сохраняют прежние правила; login-роли
сохраняют факты. Версии evidence 83 → 84 / 59 → 60; исторические bundles
не переписаны. Выбор — альтернатива №2 synthetic Astra xhigh выбора, давшего T-37.
Реализовано `gpt-6.1-sol` medium через MyFlow `sol-high`: GLM-5.3 был в недельном
лимите z.ai. Red на HEAD: 24 failed / 20 passed; новый модуль: 44 passed.
Synthetic Astra xhigh review: 0 findings. Полный offline gate с duckdb:
4131 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
[Проверки и границы](DECISIONOPS_NOLOGIN_IDENTITY_REVIEW_2026-10-03.md).
Human/production acceptance не заявляется.

**Предыдущий срез (2026-10-03):** ClickHouse role probe спрашивает роли, которые
сессия может включить: выданная, но не включённая роль (`SET DEFAULT ROLE NONE`)
отсутствует в `system.enabled_roles`, её write grant невидим собственному
grants-вопросу, а `SET ROLE` (или HTTP-параметр `role`) пишет. Один вопрос к
`system.role_grants` называет выданные пользователю роли (строка с `user_name`)
и роли, выданные ролям (строка с `role_name`), включаемые вместе с ними на любую
глубину; уже включённые роли из охвата исключены. Достижимые роли опрашиваются
одним вопросом к `system.grants`: write access type — `read_only=false` с
evidence `SET ROLE reaches …`, `dwh.access` — block, exact runtime SQL
не исполняется. Отказ или неполный ответ любого из двух вопросов не подтверждает
read-only; доказанный write grant сохраняет приоритет, а полная строка
неполного ответа используется. `readonly=1/2`, отказ `system.grants`, неполные
enabled roles и собственный write grant сохраняют факты, evidence и вопросы,
новых вопросов не шлют; `readonly` переключением роли не снимается (24.8.14.39).
Имя роли в литерале вопроса пишется с удвоенными обратной косой чертой и
кавычкой: одиночная черта называет другую роль или никакую. Достижимые роли
не становятся principals: column reach и policy applicability остаются
сессионными. Пользователю гейта, read-only по grants, нужен `SELECT` на
`system.role_grants` помимо `system.grants` — иначе `dwh.access` блокируется.
Версии evidence 82 → 83 / 58 → 59; исторические bundles
не переписаны. Выбор — альтернатива №1 synthetic Astra xhigh выбора, давшего
T-37. Живые факты сняты на ClickHouse 24.8.14.39 на Mac (одноразовый стенд,
удалён). Реализация — GLM-5.3 xhigh через MyFlow `glm-5.3`; после лимита z.ai
закончил `gpt-6.1-sol` medium через `sol-high`. Synthetic Astra xhigh review:
0 findings. Полный offline gate с duckdb: 4087 passed, 4 skipped, 38 deselected,
exit 0; Black/Ruff/strict mypy чистые. Human/production acceptance не заявляется.
[Проверки и границы](DECISIONOPS_CLICKHOUSE_SET_ROLE_REVIEW_2026-10-03.md).

**Предыдущий срез (2026-10-02):** canonical metric node несёт объявленные `join_paths`:
отсортированы, одинаковые объявления один раз; висячий endpoint —
`gap.metric_join_dangling`, путь с несколькими cardinality — `gap.metric_join_conflict`,
оба block. Удалённый путь — `metric.join_path_removed` (block), изменённая cardinality —
`metric.join_path_changed` (review), добавленный путь — `metric.join_path_added` (advisory).
Удаление или смена cardinality достигает verified assets метрики как `asset.metric_changed`;
модельные `joins` сохраняют собственные правила `join.*`, field-level impact прежний.
Native projection version 2 → 3 перемещает canonical digest каждого native snapshot с
метриками; snapshot без метрик сохраняет digest. Прежние digest-привязки получают
`asset.stale_snapshot` / `provenance.other_snapshot` (review) до повторной верификации.
Версии evidence 81 → 82 / 57 → 58; исторические bundles не переписаны.
Выбор — synthetic Astra xhigh; реализация `gpt-6.1-sol` medium через MyFlow `sol-high`.
Synthetic Astra xhigh review: 0 findings. Полный offline gate с duckdb: 4052 passed,
4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
Human/production acceptance не заявляется.
[Проверки и границы](DECISIONOPS_METRIC_JOIN_PATHS_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** canonical metric node несёт объявленную
`freshness`: `max_age` как записан в контракте, quality preconditions отсортированы
без повторов. Native projection version 1 → 2 перемещает canonical digest каждого
native snapshot с метриками; snapshot без метрик сохраняет digest.
`semantic.diff` именует ослабление как `metric.freshness_relaxed` (review),
ужесточение как `metric.freshness_tightened` и равное окно в другой записи как
`metric.freshness_restated` (advisory); смешанная правка именует оба направления.
Ослабление достигает verified assets метрики как `asset.metric_changed`.
Прежние digest-привязки получают `asset.stale_snapshot` / `provenance.other_snapshot`
(review) до повторной верификации. `dwh.freshness` по-прежнему сравнивает витрины
с `freshness_max_age` продукта. Окна сравниваются в точных целых секундах: очень
длинное или записанное тысячами цифр объявленное окно больше не роняет gate в diff,
freshness витрин и provenance stale-capture. Версии evidence 80 → 81 / 56 → 57;
исторические bundles не переписаны. Выбор — альтернатива №2 synthetic Astra xhigh
выбора, давшего T-35; реализация `gpt-6.1-sol` medium через MyFlow `sol-high`.
Synthetic Astra xhigh review: F-T-36-1 и F-T-36-3 воспроизведены и исправлены,
третий проход — 0 новых findings. Полный offline gate с duckdb: 4023 passed,
4 skipped, 38 deselected, exit 0; Black/Ruff/strict mypy чистые.
Human/production acceptance не заявляется.
[Проверки и границы](DECISIONOPS_METRIC_FRESHNESS_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** перед компиляцией поставляемого дашборда
`bi.contract` валидирует spec против candidate-модели тем же `validate_spec`,
что и build path перед вызовом BI; операторский `raw_sql` разрешён.
Spec с ошибками не компилируется и payload'ов не получает: проверка — block
с `bi.spec_invalid`, evidence называет дашборд и ошибки валидатора в порядке
поставки. Валидные дашборды сохраняют findings и payload digests, отсутствие
spec — review с `bi.spec_missing`; `impact.products` читает specs как прежде.
Версии evidence 79 → 80 / 55 → 56; исторические bundles не переписаны.
Выбор — synthetic Astra xhigh, реализация `gpt-6.1-sol` medium через MyFlow
`sol-high`. Synthetic Astra xhigh review: 0 findings. Полный offline gate
с duckdb: 3975 passed, 4 skipped, 38 deselected, exit 0; Black/Ruff/strict
mypy чистые. Human/production acceptance не заявляется.
[Проверки и границы](DECISIONOPS_BI_SPEC_VALIDATION_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** PostgreSQL-family role probe одним вопросом находит
роли, в которые session user может переключиться через `SET ROLE` (PostgreSQL 16 —
опция `SET`, раньше и в Greenplum/Greengage — членство), включая самого session user
за role default; superuser-роли остаются в вопросе и тогда, когда текущая роль их
наследует, — членство не передаёт атрибут `SUPERUSER`. Одним вопросом на витрину
спрашиваются их табличные и колоночные write-права. Достижимая пишущая роль,
достижимый superuser, имя без значения или неотвеченный вопрос не подтверждают
read-only: `dwh.access` — block с `access.role_writable`, exact runtime SQL
не исполняется; доказанные grants сохраняют приоритет. Чистое read-only evidence
называет проверенные достижимые роли или сообщает, что `SET ROLE` других ролей
не достигает; identity-вопрос с его короткими путями, `RoleFacts` и
ClickHouse-коллекция без изменений. Версии evidence 78 → 79 / 54 → 55;
исторические bundles не переписаны. Выбор — оркестратор по границе `SET ROLE`
из review T-33. Реализация — GLM-5.3 xhigh через MyFlow `glm-5.3`; ремонт после
лимита z.ai закончил `gpt-6.1-sol` medium через `sol-high`. Synthetic Astra
xhigh review: F-T-34-1 (наследуемая superuser-группа выпадала из вопроса)
воспроизведена на PostgreSQL 16.15 и исправлена, второй проход — 0 новых
findings. Полный offline gate с duckdb: 3955 passed, 4 skipped, 38 deselected,
exit 0; Black/Ruff/strict mypy чистые. Human/production acceptance
не заявляется.
[Проверки и границы](DECISIONOPS_SET_ROLE_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** отсутствующая readable-витрина не считается
доступной для датировки. Без допустимых вопросов чистый код `dwh.freshness` —
`freshness.not_run`, detail сообщает, что возраст не сравнивался, а проверка
появляется в `What was not done`. Частичное ненулевое покрытие, findings,
status и verdict прежние. Выбор — synthetic Astra xhigh, реализация Codex;
human/production acceptance не заявляется. Профильный набор: 218 passed;
Black/Ruff/strict mypy чистые. Итоговое synthetic Astra xhigh review:
0 substantive findings. Полный offline gate: 3911 passed, 5 skipped,
38 deselected, exit 0.
[Проверки и границы](DECISIONOPS_FRESHNESS_OUTCOME_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** при нуле фактических security-наблюдений чистый
код `dwh.security` — `security.not_run`, поэтому проверка появляется в
`What was not done`. Частичное ненулевое покрытие сохраняет `security.unfiltered`;
пустой список политик считается ответом. Findings, status, verdict и detail прежние.
Выбор — synthetic Astra xhigh, реализация Codex; human/production acceptance
не заявляется. Профильный набор: 224 passed; Black/Ruff/strict mypy чистые.
Итоговое synthetic Astra xhigh review: 0 substantive findings. Полный offline gate:
3899 passed, 5 skipped, 38 deselected, exit 0.
[Проверки и границы](DECISIONOPS_SECURITY_OUTCOME_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** при нуле сравнений объявленных типов и свойств
layout чистый код `dwh.physical` — `physical.not_run`, поэтому проверка появляется
в `What was not done`. Частичное ненулевое покрытие сохраняет `physical.as_declared`;
код и detail используют общий подсчёт. Findings, status, verdict и detail прежние.
Выбор — synthetic Astra xhigh, реализация Codex; human/production acceptance
не заявляется. Профильный набор: 247 passed; Black/Ruff/strict mypy чистые.
Итоговое synthetic Astra xhigh review: 0 substantive findings. Полный offline gate:
3887 passed, 5 skipped, 38 deselected, exit 0.
[Проверки и границы](DECISIONOPS_PHYSICAL_OUTCOME_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** чистый код `dwh.cost` учитывает только вопросы,
которые проверка действительно могла оценить. При нуле допустимых сравнений
код — `cost.not_run`, даже если supplied facts содержат counters или enforced
bounds. Проверка попадает в `What was not done`; частичное ненулевое покрытие
сохраняет `cost.within_budget`. Findings, status, verdict и detail прежние.
Выбор — synthetic Astra xhigh, реализация Codex; human/production acceptance
не заявляется. Полный offline gate: 3871 passed, 5 skipped, 38 deselected,
exit 0; Black/Ruff/strict mypy чистые. Итоговое synthetic Astra xhigh review:
0 substantive findings. [Проверки и границы](DECISIONOPS_COST_OUTCOME_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** чистая сводка `dwh.cost` считает проверенные
потолки строк из планов и байтов/времени из exact-query runtime evidence.
Покрытие соотносится со всеми объявлениями; недоступные, неатрибутированные
и закрытые deny-all вопросы не засчитываются. Неполное и нулевое покрытие
названы явно. Findings, summary codes и verdict прежние. Выбор — synthetic
Astra xhigh, реализация Codex; human/production acceptance не заявляется.
Полный offline gate: 3821 passed, 5 skipped, 38 deselected, exit 0;
docs-as-code: 24 passed; Black/Ruff/strict mypy чистые. Итоговое synthetic
Astra xhigh review: 0 substantive findings.
[Проверки и границы](DECISIONOPS_COST_COVERAGE_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** чистая сводка `dwh.security` сообщает число
подтверждённых прав чтения запрошенных колонок и ответов о row policies.
Частично наблюдённый скоуп больше не описывается как доступ ко всем колонкам;
покрытие считается по вопросам (витрина, колонка времени). Findings, summary
codes, severity и verdict прежние. Выбор среза — synthetic Astra xhigh,
реализация Codex; human/production acceptance не заявляется.
Профильный набор: 306 passed; docs-as-code: 24 passed; Black/Ruff/strict mypy чистые.
Итоговое synthetic Astra xhigh review: 0 substantive findings. Полный offline gate:
3790 passed, 5 skipped, 38 deselected, exit 0.
[Проверки и границы](DECISIONOPS_SECURITY_COVERAGE_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** чистая сводка `dwh.freshness` сообщает число
сравнённых вопросов (витрина, колонка времени) и полноту покрытия. Свежая часть
скоупа больше не описывается как проверка всех витрин. Findings, summary codes,
severity и verdict прежние. Выбор среза и ревью — synthetic Astra xhigh;
реализация Codex, human/production acceptance не заявляется.
Профильный набор: 225 passed; docs-as-code: 24 passed; Black/Ruff/strict mypy чистые.
Итоговое synthetic Astra xhigh review: 0 substantive findings. Полный offline gate:
3763 passed, 5 skipped, 38 deselected, exit 0; timing benchmarks в этом запуске прошли.
[Проверки и границы](DECISIONOPS_FRESHNESS_COVERAGE_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** `dwh.physical` описывает только выполненные сравнения:
число отвеченных типов колонок и заявленных свойств layout по каждому вопросу
(витрина, колонка времени). Неизвестные и недоступные факты больше не объявляются
совпадающими; неполное и нулевое покрытие названы явно. Findings, summary codes,
severity и verdict сохраняются. Срез выбран synthetic Astra xhigh, реализация Codex;
human/production acceptance не заявляется.
Профильный набор: 204 passed; docs-as-code: 24 passed; Black/Ruff/strict mypy чистые.
Итоговое synthetic Astra review: 0 незакрытых substantive findings.
Полный offline gate: 3741 passed, 1 failed — SQLite timing benchmark (9401,3 мс
при лимите 5000 мс); отдельно он прошёл за 1,25 с. Полный gate не объявляется зелёным.
[Проверки и границы](DECISIONOPS_PHYSICAL_COVERAGE_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** canonical ClickHouse grants `ALTER ...`, `CREATE ...`,
`DROP ...` и `ALL` блокируют grant-based read-only и exact runtime SQL. Конкретные
названия вроде `ALTER UPDATE` и `DROP TABLE` подтверждены исходником закреплённой
версии 24.8.14.39; evidence сохраняет точные имена. Профили readonly=1/2, partial
revokes и PostgreSQL сохраняют правила. Выбор — synthetic Astra xhigh, реализация
Codex; human/production acceptance не заявляется.
Полный offline pytest с duckdb: 3723 passed, 5 skipped, 38 deselected, exit 0;
Ruff, Black и strict mypy чистые. Итоговое synthetic Astra review: 0 substantive findings.
[Проверки и границы](DECISIONOPS_WRITE_GRANTS_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** неизвестный или неполный список ClickHouse enabled
roles не подтверждает grant-based read-only. Без профиля `readonly=1/2` доступ
блокируется и exact runtime SQL не выполняется; доказанные write grants сохраняют
evidence. Column reach и применимость непустого policy catalog остаются неизвестными:
`security.not_observed` — REVIEW. Успешный пустой policy catalog сохраняет `[]`.
Неизвестный reach не доказывает отсутствие скрытой ClickHouse-колонки или отсутствие
защиты классификации. SQL и публичные схемы прежние. Выбор — synthetic Astra xhigh,
реализация Codex; human/production acceptance не заявляется.
Полный offline pytest с duckdb: 3679 passed, 5 skipped, 38 deselected, exit 0;
Ruff, Black и strict mypy чистые. Итоговое synthetic Astra review: 0 substantive findings.
[Проверки и границы](DECISIONOPS_ENABLED_ROLES_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** частичные/NULL ответы PostgreSQL-family write-privilege
вопросов не подтверждают read-only. Evidence называет неотвеченные табличные
INSERT/UPDATE/DELETE/TRUNCATE или колоночные INSERT/UPDATE и витрину; `dwh.access`
блокирует доступ, exact runtime SQL не выполняется. Доказанные grants сохраняют
приоритет и не дублируются. SQL, identity и ClickHouse прежние. Выбор и итоговое
synthetic review — Astra xhigh, реализация Codex; human/production acceptance
не заявляется. Полный offline pytest с duckdb: 3645 passed, 5 skipped,
38 deselected; Ruff, Black и strict mypy чистые. Итоговое review: 0 substantive findings.
[Проверки и границы](DECISIONOPS_PARTIAL_PRIVILEGES_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** частичный PostgreSQL-family identity-ответ,
NULL в identity-поле или пустое имя не подтверждают read-only. Известное имя и
доказанный superuser/BYPASSRLS сохраняются; обход RLS — false только при двух
известных отрицательных флагах, иначе null. Неполный identity блокирует доступ
и exact runtime SQL, write-privilege вопросы не выполняются; evidence называет
неотвеченные поля. Доказанный superuser сохраняет прежнее writable evidence
даже при неполной строке. Точные непустые имя и subject refs сохраняют значимые
пробелы quoted-идентификаторов; общий `NonBlankText` не меняется.
SQL-вопросы и общий normalizer не менялись.
Версии 66 → 67 / 46 → 47; исторические bundles не переписаны. Срез выбран
Astra xhigh по ADR 0006; тесты — Luna xhigh, реализация и независимая проверка — Codex.
Red identity: 40 failed / 40 passed; связанная группа до проверки quoted-имён: 353 passed.
Red quoted-name controls: 20 failed / 130 deselected. Итоговый полный offline pytest
с duckdb: 3567 passed, 5 skipped, 38 deselected; Ruff, Black и strict mypy чистые.
Итоговое synthetic Astra xhigh review: 0 substantive findings; не human/production acceptance.
[Synthetic review и границы](DECISIONOPS_PARTIAL_IDENTITY_REVIEW_2026-10-02.md).

**Предыдущий срез (2026-10-02):** отказ или пустой ответ PostgreSQL-family
identity-вопроса блокирует доступ: роль — `unknown`, superuser и `BYPASSRLS` не
установлены, `bypasses_row_security=null`. Write-privilege вопросы и exact runtime
SQL не выполняются. `dwh.access` сохраняет код `access.role_writable`, но detail
описывает неподтверждённый read-only без утверждения, что неизвестная роль пишет.
Ответившие роли и ClickHouse сохраняют правила; ошибки соединения пробрасываются.
Версии 65 → 66 / 45 → 46; исторические bundles не переписаны. Выбор — Astra xhigh
по ADR 0006; реализация Codex. Red: 16 failed / 16 passed; связанная группа:
305 passed. Полный offline pytest с duckdb: 3499 passed, 5 skipped, 38 deselected;
Ruff, Black и strict mypy чистые.
[Итоговое synthetic Astra review](DECISIONOPS_ROLE_IDENTITY_REVIEW_2026-10-02.md):
0 substantive findings; human/production acceptance не заявляется.

**Предыдущий срез (2026-10-02):** отказ или пустой ответ табличного PostgreSQL-family
write-privilege вопроса не подтверждает read-only, даже если колоночные INSERT/UPDATE
отрицательны. `dwh.access` — BLOCK, exact runtime SQL не исполняется; evidence
называет неотвеченные табличные INSERT/UPDATE/DELETE/TRUNCATE и витрину. Колоночный
вопрос продолжается; положительный grant сохраняет evidence. API, набор SQL,
superuser и ClickHouse без изменений. `VERIFIER_VERSION` 64 → 65, `CHECK_VERSION`
44 → 45; исторические bundles не переписаны. Срез выбран Astra xhigh по ADR 0006.
Red: 2 failed / 4 passed; связанная группа — 188 passed. Полный offline pytest
с duckdb — 3467 passed, 5 skipped, 38 deselected; Ruff, Black и strict mypy чистые.
[Synthetic Astra xhigh review](DECISIONOPS_TABLE_PRIVILEGES_REVIEW_2026-10-02.md):
0 substantive findings; не human/production acceptance.

**Предыдущий срез (2026-10-02):** PostgreSQL-family probe проверяет колоночные
INSERT/UPDATE через `has_any_column_privilege` по всей витрине скоупа, включая
колонки вне metric-чтений. Только колоночный write-grant делает роль writable:
`dwh.access` — BLOCK с `access.role_writable`, exact runtime SQL не исполняется.
Табличный grant сохраняет одно evidence без второй колоночной записи; read-only
evidence называет отсутствие табличных INSERT/UPDATE/DELETE/TRUNCATE и колоночных
INSERT/UPDATE. Отказ или пустой ответ колоночного privilege-вопроса также блокирует
доступ: read-only не подтверждён, exact runtime SQL не исполняется. Superuser и
ClickHouse без изменений. `VERIFIER_VERSION` 63 → 64, `CHECK_VERSION` 43 → 44;
исторические bundles не переписаны. Граница `NOINHERIT` / `SET ROLE` в пишущую
группу остаётся вне этого среза (T-33). Полный offline pytest с duckdb:
3461 passed, 5 skipped, 38 deselected; Ruff, Black и strict mypy чистые.
[Synthetic Astra xhigh review](DECISIONOPS_COLUMN_PRIVILEGES_REVIEW_2026-10-02.md):
P1 исправлен, итог 0 substantive findings; не human/production acceptance.

**Предыдущий срез (2026-10-01):** `results.reconcile` сравнивает нечисловые значения
integer/decimal/float по виду: бесконечности одного знака равны при разных записях,
включая float за пределом binary64; допуск к ним не применяется. NaN кандидата против
числа или бесконечности — `value.changed`; ожидаемый NaN, включая decimal `sNaN`,
не решает свою ячейку (`value.expected_nan`, `insufficient` / REVIEW), а доказанное
различие в другом месте сохраняет `different`. Проверка порядка выполняется и при
одних ожидаемых NaN; их ячейки перечисляются после value/order differences и перед
candidate aggregate issues. NULL сохраняет свои коды, ячейка любого business key
решается сопоставлением строки; ключи и агрегатные проверки сохраняют свои правила.
`VERIFIER_VERSION` 62 → 63, `CHECK_VERSION` 42 → 43; исторические bundles не переписаны.
Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`. До правки 58 новых случаев
падали, 77 контролей проходили; после правки все 135 и связанный focused gate прошли:
403 passed. Gate-level тест проверяет REVIEW с `result.not_equivalent` и отсутствие
sealed `result.equivalent` claim. Полный offline pytest: 3424 passed, 20 skipped, 38 deselected;
strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые. Живые факты сняты на Mac
одноразовыми стендами (удалены): ClickHouse 24.8.14.39 даёт `nan` на `0/0` во Float64,
PostgreSQL 16.15 считает `'NaN' = 'NaN'` истиной; политика гейта не следует
SQL-семантике ни одного движка. Срез — альтернатива №2 синтетического Astra xhigh
выбора, давшего T-29; итоговое ревью Astra xhigh: 0 findings. Human/production
acceptance не заявляется.

**Предыдущий срез (2026-10-01):** `results.reconcile` сравнивает точную разность конечных
binary64-значений с точным объявленным decimal-допуском. Absolute проверяет разность,
relative — разность против допуска × max(|a|, |b|), без приближения или деления;
попадание в любой объявленный допуск даёт `equivalent`. Превышение допуска, скрытое
погрешностью binary64, теперь даёт `value.changed`; точное попадание в relative-допуск,
отвергавшееся приближённым отношением, теперь даёт `equivalent`. Перенос float-колонки
сохраняет её допуск; signed zero и разные записи одного binary64-значения равны.
Absolute-допуск за пределом binary64 больше не делает бесконечность равной числу или
бесконечности другого знака: прежде такой допуск сам становился бесконечностью.
`VERIFIER_VERSION` 61 → 62, `CHECK_VERSION` 41 → 42; исторические bundles не переписаны.
Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`. До правки 9 новых случаев
падали, 17 контролей проходили; после правки 26 новых случаев и связанный focused gate
прошли: 268 passed. Полный offline pytest: 3289 passed, 20 skipped, 38 deselected;
strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые. Срез — альтернатива №1
синтетического Astra xhigh выбора, давшего T-29. Итоговое ревью Astra xhigh: проход 1 —
F-T-31-1 (допуск за пределом binary64) отклонена воспроизведением и описана в доках,
проход 2 подтвердил отклонение: 0 actionable P1/P2. Живая БД на этом срезе не
запускалась; human/production acceptance не заявляется.

**Предыдущий срез (2026-10-01):** каждое environment-чтение записывает event-time колонку
своего вопроса (витрина, колонка времени). Access, freshness, physical, security и cost,
включая допуск runtime probe, используют только однозначно атрибутированное чтение;
порядок чтений не скрывает протухшие часы или недоступную колонку и не объявляет свежие
часы протухшими. Неоднозначность — `access.reading_unattributed` (review); старое чтение
без колонки применяется только к единственным часам витрины с единственным чтением.
`freshness.stale` называет колонку времени; bundle сохраняет `event_time_column`.
`VERIFIER_VERSION` 60 → 61, `CHECK_VERSION` 40 → 41; исторические bundles не переписаны.
Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`; новые offline-тесты проверяют
обе семьи движков без изменения SELECT-запросов и gate-level REVIEW/BLOCK. До правки
37 новых случаев падали, 7 контролей проходили; после правки 44 новых случая и
связанный focused gate прошли: 365 passed. Полный offline pytest: 3263 passed, 20 skipped, 38 deselected;
strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые. Выбор среза и итоговое
ревью — синтетические Astra xhigh: 0 actionable P1/P2. Живая БД на этом срезе не
запускалась; human/production acceptance не заявляется.

**Предыдущий срез (2026-10-01):** `results.reconcile` отождествляет integer/decimal
business key и `group_by` проверок `share_sum` / `partition_conservation` по числу.
`0.4120` и `0.412` теперь находят одну строку; две записи одного числа в result
дают `insufficient` / `key.not_unique`, а группа на разных scales больше не делится.
NULL, non-finite, нечисловые значения и ключи других типов сохраняют записанную форму.
Детали используют написание ключа своего result, группа — своей первой строки;
колонки читаются по имени, входные ResultSet не меняются.
`VERIFIER_VERSION` 59 → 60, `CHECK_VERSION` 39 → 40; исторические bundles не переписаны.
Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`. До правки 24 новых случая
падали, 11 контролей проходили; после правки 35 новых случаев и связанный focused gate
прошли: 242 passed. Полный offline pytest: 3219 passed, 20 skipped, 38 deselected;
strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые. Срез — альтернатива №2 того же
синтетического Astra xhigh выбора; итоговое ревью Astra xhigh: 0 actionable P1/P2.
Живая БД на этом срезе не запускалась; human/production acceptance не заявляется.

**Предыдущий срез (2026-10-01):** `results.reconcile` после schema step читает
колонки supplied result по имени: values, business key, контрактный порядок строк
и ключ строки в failed aggregate check. Перестановка с ожидаемыми named values
теперь даёт `equivalent`, включая перенос key; изменённые named values больше
не получают ложный pass и sealed `result.equivalent` claim, а дают `different` / block.
NULL и float tolerance остаются привязаны к своей колонке; входные ResultSet не меняются.
Native capture сохраняет обязательный порядок колонок asset.
`VERIFIER_VERSION` 58 → 59, `CHECK_VERSION` 38 → 39; исторические bundles не переписаны.
Реализовано `gpt-6.1-sol` high через MyFlow `sol-high`. Red на comparator из HEAD:
22 новых теста падали, включая два gate-level; после правки 29 новых регрессий прошли.
Полный offline pytest: 3184 passed, 20 skipped, 38 deselected; strict mypy: 0 ошибок
в 120 модулях, Ruff и Black чистые. Выбор среза и итоговое ревью — синтетические
Astra xhigh; итог: 0 actionable P1/P2. Живая БД на этом срезе не запускалась;
human/production acceptance не заявляется.

**Предыдущий срез (2026-10-01):** исключение bare-ссылки на CTE из физического
списка таблиц и из denylist SQL guard требует, чтобы query-владелец объявления
был предком конкретной ссылки. Вложенная или соседняя CTE больше не скрывает
внешнюю физическую таблицу. `SELECT count(*) FROM a WHERE EXISTS
(WITH a AS (SELECT 1) SELECT 1 FROM a)` даёт `asset.query_ungoverned` (review),
называющий `a`, на ClickHouse и PostgreSQL; raw-SQL RBAC также видит эту таблицу.
Внешний bare `dictionary` с одноимённой вложенной CTE отвергается SQL guard.
Внешний `WITH` сохраняет исключение во вложенных запросах и обеих ветках своего
`UNION`. Whole-AST extraction, включая физические таблицы в непрочитанных CTE,
точный регистр ClickHouse, прежнее сравнение имён PostgreSQL, qualified-ссылки,
same-WITH порядок, self-reference, рекурсия и scalar `WITH` сохраняют правила.
`VERIFIER_VERSION` 57 → 58, `CHECK_VERSION` 37 → 38: пропущенный review возвращается.
Red до production fix: 19 падений, 33 passed; после Luna xhigh fix: 52 passed.
34 новые регрессии покрывают extractor, guard, governance и RBAC, включая
quoted-ссылки, UNION/derived siblings, наследование и ClickHouse `IN` normalization.
Независимый полный offline pytest: 3154 passed, 21 skipped, 38 deselected;
strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
Выбор среза и итоговое ревью — синтетические Astra xhigh; итог: 0 actionable P1/P2.
Живая БД на этом срезе не запускалась; human/production acceptance не заявляется.

**Предыдущий срез (2026-09-30):** governance reviewed-запроса ClickHouse сообщает
ключи `JOIN … USING` только из достижимой части SQL. Непрочитанные named CTE
и scalar `WITH` больше не добавляют эту причину `asset.query_fields_unchecked`;
при соседнем живом JOIN остаётся точный набор его ключей, включая совпадающий
с непрочитанным ключ. Используется общий с chart impact обход гейтов CTE;
общий `ColumnReads.join_using_keys` остаётся whole-AST. Именованные чтения,
неизвестные таблицы/поля, другие причины advisory и PostgreSQL сохраняют правила.
`VERIFIER_VERSION` 55 → 56, `CHECK_VERSION` 35 → 36: прежний advisory может исчезнуть.
Red-green Luna xhigh: 3 падения до правки, затем 20 новых регрессий прошли;
связанный gate исполнителя: 262 passed. Независимый регрессионный модуль:
20 passed; strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
Полный offline pytest: 3102 passed, 21 skipped, 38 deselected.
Выбор среза и итоговое ревью — синтетические Astra xhigh; итог: 0 actionable P1/P2.
Живой ClickHouse на этом срезе не запускался; human/production acceptance не заявляется.

**Предыдущий срез (2026-09-30):** governance reviewed-запроса ClickHouse сообщает
`it selects COLUMNS(...)` только для селектора в достижимой части SQL.
Непрочитанные named CTE и scalar `WITH`, включая цепочки непрочитанных объявлений,
больше не создают эту причину `asset.query_fields_unchecked`; живой селектор
сохраняет advisory. Используется общий с chart impact обход гейтов CTE;
`ColumnReads.column_selectors` остаётся whole-AST, regex не раскрывается по snapshot.
Именованные чтения, неизвестные таблицы/поля, звёзды, `JOIN … USING`, PostgreSQL
и chart matcher сохраняют прежние правила. `VERIFIER_VERSION` 54 → 55,
`CHECK_VERSION` 34 → 35: прежний advisory может исчезнуть.
Red-green исполнителя Luna xhigh: 2 падения до правки, затем 17 новых регрессий
прошли; независимый ClickHouse/governance/chart gate: 242 passed. Полный offline
pytest: 3082 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях,
Ruff и Black чистые. Срез выбран синтетическим Astra xhigh review; итоговое
синтетическое Astra xhigh review: 0 actionable P1/P2. Живой ClickHouse на этом срезе
не запускался; human/production acceptance не заявляется.

**Предыдущий срез (2026-09-30):** общий обход достижимости ClickHouse сопоставляет
named CTE и scalar `WITH` с точным регистром. Внутреннее `a` больше не скрывает
настоящее чтение внешнего `A`, а чтение `a` не открывает непрочитанное соседнее
`A`. Правило едино для звёзд, `COLUMNS`, `JOIN … USING`, dictionary- и
Join-table-вызовов, включая named CTE справа от `IN`. Точное затенение,
self-reference, одноуровневые дубликаты, табличные квалификаторы и `SETTINGS`
сохраняют прежние правила. `VERIFIER_VERSION` 53 → 54, `CHECK_VERSION` 33 → 34:
предупреждения могут добавляться или исчезать. Red-green: 15 падений в новом
модуле и одно в прежнем граничном тесте; после исправления 28 новых регрессий
прошли, связанный ClickHouse/chart gate: 610 passed. Полный offline pytest:
3065 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях,
Ruff и Black чистые. Синтетическое Astra xhigh review: 0 actionable P1/P2.
Живой ClickHouse 24.8.14.39 на Mac подтвердил
различие `A`/`a`, точное затенение и named CTE в `FROM`/`IN`; изолированный
стенд удалён. Это не human/production acceptance.

**Предыдущий срез (2026-09-30):** governance reviewed-запроса ClickHouse сообщает
`it selects *` только для звезды, читающей колонки в достижимой части SQL.
Непрочитанные named CTE и scalar `WITH` больше не создают эту причину
`asset.query_fields_unchecked`; живое чтение алиаса, кортежа, цепочки CTE и
`IN <table>` сохраняет её. Используется общий с chart impact обход гейтов CTE;
общий `ColumnReads.starred` остаётся whole-AST, без дополнительного обхода в
chart matcher. Именованные чтения, неизвестные таблицы и поля, `COLUMNS`,
`JOIN … USING` и PostgreSQL сохраняют прежние правила.
`VERIFIER_VERSION` 52 → 53, `CHECK_VERSION` 32 → 33: прежний advisory может
исчезнуть. Red-green: 4 падения до реализации, затем 17 passed; расширенный
регрессионный модуль: 23 passed, связанный ClickHouse/chart gate: 498 passed.
Полный offline pytest: 3037 passed, 21 skipped, 38 deselected; strict mypy:
0 ошибок в 120 модулях, Ruff и Black чистые. Синтетическое Astra xhigh review:
0 actionable P1/P2. Живой ClickHouse не запускался; это не human/production acceptance.

**Предыдущий срез (2026-09-30):** PostgreSQL `CAST(expr AS type)` и `expr::type`
сохраняют уже известное сильное имя выхода колонки, пяти агрегатов или оконного
`ROW_NUMBER` для целого `ORDER BY`/`DISTINCT ON` и выхода левой ветки set operation.
Вложенные casts и скобки сохраняют имя; `AS` его заменяет, унарный `+` убирает.
Чтения аргумента, `FILTER`, `PARTITION BY` и внутреннего `ORDER BY` остаются;
имена типов, операторов и новых функций не выводятся, `GROUP BY` и ClickHouse
не меняются. Вызовы функций, которые sqlglot нормализует в `Cast`, не наследуют
имя аргумента. `VERIFIER_VERSION` 51 → 52, `CHECK_VERSION` 31 → 32:
меняется governance reviewed-запроса. Red-green подтвердил три ложных чтения
`dm.sales_daily.count` и шесть пропущенных чтений полей сортировки из первого
модельного ревью; после исправления целевой gate: 539 passed, включая 40
CAST-регрессий. Полный offline pytest: 3014 passed, 21 skipped, 38 deselected;
strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые. Живой PostgreSQL
16.15 на Mac подтвердил настоящие casts и унарный `+`; отдельная проба
подтвердила собственное имя пользовательской функции и шесть ожидаемых
SQLSTATE 42703 для сортировки по имени её аргумента. Временные кластеры удалены.
Итоговое синтетическое Astra xhigh review: 0 actionable P1/P2; решение
опирается на AST текущего sqlglot и защищено регрессиями обновления зависимости.
Это не human/production acceptance.

**Предыдущий срез (2026-09-29):** PostgreSQL `ROW_NUMBER() OVER (…)` без явного
alias сохраняет выход `row_number` для целого `ORDER BY`/`DISTINCT ON` и
сортировки set operation по левой ветке. Ложное чтение физического
`row_number` уходит; `PARTITION BY` и внутренний `ORDER BY` сохраняют чтения.
`VERIFIER_VERSION` 50 → 51, `CHECK_VERSION` 30 → 31: прежний reviewed-запрос
мог получить ложный governance finding. Red-green: исходный тест упал на
ложном `dm.sales_daily.row_number`, затем 15 граничных тестов прошли; целевой
gate: 497 passed, полный offline pytest: 2974 passed, 21 skipped,
38 deselected. Strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.
Итоговое синтетическое Astra xhigh review: 0 actionable P1/P2. Живой
PostgreSQL не запускался; это не human/production acceptance.

**Предыдущий срез (2026-09-29):** PostgreSQL `COUNT`, `SUM`, `AVG`, `MIN` и
`MAX` с `OVER`, включая форму с `FILTER (WHERE …)`, сохраняют неявное имя
выхода для целого `ORDER BY`/`DISTINCT ON` и сортировки set operation по
левой ветке. Ложное чтение поля с именем агрегата уходит; аргументы,
предикат `FILTER`, `PARTITION BY` и внутренний `ORDER BY` остаются чтениями.
`VERIFIER_VERSION` 49 → 50, `CHECK_VERSION` 29 → 30: прежний reviewed-запрос
мог получить ложный governance finding. Red-green: исходный тест упал на ложном
`dm.sales_daily.sum`; целевой gate: 482 passed, полный offline pytest:
2959 passed, 21 skipped, 38 deselected. Strict mypy: 0 ошибок в 120 модулях,
Ruff и Black чистые. Итоговое синтетическое Astra xhigh review:
0 actionable P1/P2. Живой PostgreSQL не запускался; это не human/production
acceptance.

**Предыдущий срез (2026-09-29):** пустой ClickHouse `map()` в operator SQL
не является шаблоном Superset `{{ … }}` и не оставляет ложный
`chart.sql_partial`; чтение именованных колонок сохраняется. Настоящий
`{{ filter }}` оставляет гэп и в том же SQL, где встречается `map()`.
`VERIFIER_VERSION` 48 → 49, потому что меняются chart evidence и identity bundle;
`CHECK_VERSION` остаётся 29, так как смысл отдельной проверки не меняется.
Red-green подтвердил ложные `chart.sql_partial` и `chart.metric_unread`; целевой
gate: 161 passed. Полный offline pytest: 2941 passed, 21 skipped, 38 deselected;
strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые. Итоговое синтетическое
Astra xhigh review: 0 actionable P1/P2. Живой ClickHouse не запускался; это не
human/production acceptance.

**Предыдущий срез (2026-09-29):** PostgreSQL `COUNT`, `SUM`, `AVG`, `MIN` и `MAX`
с `FILTER (WHERE …)` сохраняют неявное имя выхода для целого `ORDER BY`/
`DISTINCT ON` и сортировки set operation по левой ветке. Ложное чтение поля с
именем агрегата уходит; аргументы и предикат `FILTER` остаются чтениями.
`AS`, квотированный регистр, унарный `+`, выражения, квалифицированные имена и
`GROUP BY` сохраняют границы. `VERIFIER_VERSION` 47 → 48,
`CHECK_VERSION` 28 → 29: прежний reviewed-запрос мог дать ложный governance
finding. Red-green: исходный тест упал на `dm.sales_daily.sum`, после правки
весь PostgreSQL order-by suite прошёл (181 passed). Срез выбран синтетическим
Astra xhigh review и реализован Luna xhigh. Полный offline pytest:
2937 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях,
Ruff и Black чистые. Итоговое синтетическое Astra xhigh review:
0 actionable P1/P2. Живой PostgreSQL не запускался; это не human/production
acceptance.

**Предыдущий срез (2026-09-29):** прямые PostgreSQL `AVG(...)`, `MIN(...)` и
`MAX(...)` без alias дают неявные выходные имена для целого `ORDER BY`/`DISTINCT ON`
и сортировки set operation по левой ветке. Ложные field review/advisory на имени
функции уходят, чтения аргументов остаются. Квотированный регистр, `AS`, унарный
`+`, выражения, квалифицированные имена и `GROUP BY` сохраняют границы.
`VERIFIER_VERSION` 46 → 47, `CHECK_VERSION` 27 → 28. Red-green: 18 падений до
правки, 137 тестов прошли после; целевой прогон — 372 passed. Полный offline
pytest: 2893 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120
модулях, Ruff и Black чистые. Синтетическое Astra xhigh review: 0 actionable
P1/P2. Живой PostgreSQL на этом срезе не запускался; review не является
human/production acceptance.

**Предыдущий срез (2026-09-29):** PostgreSQL-алиас таблицы со списком выходных
имён (`FROM dm.sales_daily AS s(renamed)`) больше не сопоставляет `s.renamed`
с физической колонкой по имени. Без доказанного ordinal lineage governance
оставляет `asset.query_fields_unchecked` с точной причиной; это относится и к
голому имени, коррелированной ссылке и алиасу над скобочным `JOIN`. Соседние
именованные физические поля продолжают проверяться, а выходное имя SELECT в
`ORDER BY` и локальный выход `GROUP BY` в LATERAL сохраняют приоритет.
`VERIFIER_VERSION` 45 → 46, `CHECK_VERSION` 26 → 27: прежние review и чистый
результат для таких ссылок могут стать advisory. Red-green тесты закрыли
четыре P2 из синтетических Astra xhigh review: противоречивую причину advisory,
ложный остаток у LATERAL `GROUP BY`, голое имя над скобочным `JOIN` и вложенные
скобки. Итоговое review: 0 actionable P1/P2. Полный offline pytest: 2868 passed,
21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях, Ruff и Black
чистые. Это не human/production acceptance.

**Предыдущий срез (2026-09-29):** PostgreSQL-семейство отмечает `NATURAL JOIN`
как частично проверенное чтение: ключи определяются совпадающими выходными
именами источников, которые эта проверка не разворачивает и которые могут
зависеть от физической схемы. `asset.query_fields_unchecked` объясняет остаток
без угадывания имён из snapshot; явные соседние поля проверяются отдельно.
Учитываются вложенные join, а `ON` и текст в литералах/комментариях не создают
остаток. `VERIFIER_VERSION` 44 → 45, `CHECK_VERSION` 25 → 26. Два этапа
red-green тестов закрыли исходный пропуск и неточную причину для производных
источников. Полный offline pytest: 2847 passed, 21 skipped, 38 deselected;
strict mypy: 0 ошибок в 120 модулях. Синтетическое Astra xhigh review:
0 actionable P1/P2 после исправления одного P2. Это не human/production acceptance.

**Предыдущий срез (2026-09-29):** PostgreSQL `SUM(...)` без явного alias даёт
неявное выходное имя `sum` для целого `ORDER BY`/`DISTINCT ON`, в том числе у
левой ветки set operation. Ложное чтение физического поля и sourceless advisory
исчезают; чтения аргументов остаются. Явный `AS`, квотированный регистр и унарный
`+` сохраняют границы; `GROUP BY` не использует агрегатный выход. PostgreSQL 16
на Mac подтвердил обычную и скобочную форму, set operation, `DISTINCT ON` и
отказ для `+SUM`. `VERIFIER_VERSION` 43 → 44, `CHECK_VERSION` 24 → 25:
прежний reviewed-запрос может потерять ложный governance finding. Срез выбран
по синтетическому Astra xhigh review; итоговое review: 0 actionable P1/P2.
Полный offline pytest: 2839 passed, 21 skipped, 38 deselected; strict mypy,
Ruff и Black чистые. Это не human/production acceptance.

**Предыдущий срез (2026-09-29):** PostgreSQL `COUNT(...)` без явного alias даёт
неявное выходное имя `count` для целого `ORDER BY`/`DISTINCT ON`, в том числе у
левой ветки `UNION`/`INTERSECT`/`EXCEPT`. Сортировка больше не называет
несуществующую физическую колонку или sourceless advisory; аргументы `COUNT`
по-прежнему читаются. Явный `AS` заменяет имя, унарный `+` его убирает;
квотированное `"Count"(...)` сохраняет регистр выхода, а `ORDER BY count` читает
input-колонку. `GROUP BY` не получает агрегатный выход. PostgreSQL 16.15 на Mac подтвердил
обычную и скобочную форму, set operation, `DISTINCT ON` и отказ для `+COUNT`.
`VERIFIER_VERSION` 42 → 43 и `CHECK_VERSION` 23 → 24: прежний reviewed-запрос
может потерять ложный governance finding. Срез выбран по синтетическому Astra
xhigh review; повторное ревью: 0 actionable P1/P2. Полный offline pytest:
2830 passed, 21 skipped, 38 deselected; strict mypy, Ruff и Black чистые.
Это не human/production acceptance.

**Предыдущий срез (2026-09-28):** корневые read-only `INTERSECT` и `EXCEPT` проходят
SELECT-only guard на PostgreSQL и ClickHouse. Таблицы и поля обеих веток попадают в
governance; PostgreSQL сохраняет разрешение `ORDER BY` через выход левой ветки и
принимает скобочный корневой set operation с внешними `ORDER BY`/`LIMIT`/`OFFSET`.
`SELECT INTO` и remote table functions отклоняются на всём AST; подзапросы в общих
`LIMIT`/`OFFSET` дают field reads также у ранее допустимого `UNION`;
корневой `VALUES` и скобочный корень ClickHouse остаются отказом. На Mac проверен
синтаксис обеих операций в ClickHouse 24.8.14.39 и PostgreSQL 16.15.
`VERIFIER_VERSION` 41 → 42: прежний ClickHouse operator SQL с таким корнем мог дать
`chart.opaque_sql`, а теперь назвать `reads_field`. `CHECK_VERSION` 22 → 23:
исправленное чтение старого `UNION` и отказ на ранее допустимый `SELECT INTO`
меняют смысл прежних CheckResult.
Синтетическое Astra xhigh review выявило P1 (`SELECT INTO`) и P2 (чтения в
`LIMIT`/`OFFSET`); оба исправлены, это не human/production acceptance.
Полный offline pytest: 2807 passed, 21 skipped, 38 deselected; strict mypy,
Ruff и Black чистые.

**Предыдущий срез (2026-09-28):** корневой PostgreSQL-запрос `(SELECT …)` с внешними
`ORDER BY`, `LIMIT` или `OFFSET` проходит SELECT-only guard. Чтения из внешних
выражений попадают в governance: целое имя выхода не становится входным полем, а
имя внутри выражения и скалярного подзапроса проверяется. Неподдержанные и
повторные модификаторы остаются отказом; корневые `INTERSECT`/`EXCEPT`, `VALUES`
и ClickHouse не расширены. Версии 41/22 сохраняются: ранее такой `ReferenceQuery`
не проходил границу контракта, поэтому старых CheckResult для него нет.
Реализовано Luna xhigh; синтетическое Astra xhigh review: 0 actionable P1/P2,
не human/production acceptance. Полный offline pytest: 2774 passed, 21 skipped,
38 deselected; strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.

**Предыдущий срез (2026-09-28):** скобочный элемент ClickHouse `ARRAY JOIN (tags AS t)`
читается как выражение: `tags` остаётся чтением, а `t` — алиасом. В
`ARRAY JOIN (tags AS t) AS a` внешний `a` заменяет внутренний алиас; последующее `t`
снова грейдится как колонка. Настоящий скобочный SELECT сохраняет свою область.
Нормализация применяется также при поиске таблиц и чтении операторского SQL, где
`IN <name>` раньше мог повторно построить scope на исходной форме. Ложные field review и
chart reads уходят, а запрос с внешним алиасом больше не обрывает governance и не
даёт `chart.opaque_sql`. `VERIFIER_VERSION` 40 → 41, `CHECK_VERSION` 21 → 22.
Реализовано как Codex companion Luna xhigh; синтетическое Astra xhigh review:
0 actionable P1/P2, не human/production acceptance. Целевой прогон T-25:
303 passed; полный offline pytest: 2751 passed, 21 skipped, 38 deselected.
Strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.

**Предыдущий срез (2026-09-28):** SQL guard теперь переводит `RecursionError`, который sqlglot
выбрасывает на запросе глубже доступного parser depth, в обычный `SQLGuardError`: загрузка
`ReferenceQuery` и verified-asset registry получает отказ валидации с указанием глубины вместо
аварии интерпретатора. `guard_sql`, извлечение таблиц и колонок, а также chart impact сохраняют
существующее поведение для читаемых запросов; `VERIFIER_VERSION=40` и `CHECK_VERSION=21`
остаются без изменений. Реализовано как Codex companion Luna xhigh. Целевой прогон T-26:
190 passed, включая docs-as-code; полный offline pytest: 2745 passed, 21 skipped,
38 deselected. Strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.

**Предыдущий срез (2026-09-27):** PostgreSQL-семейство разрешает целое голое имя в
`ORDER BY` и `DISTINCT ON` сначала через выходной столбец SELECT с учётом регистра
идентификатора; имя внутри выражения читает input-колонку. `ORDER BY` над
`UNION`/`INTERSECT`/`EXCEPT` разрешает целое имя через выход левой ветки, не оставляя
ложного sourceless advisory; корневые `INTERSECT` и `EXCEPT` guard по-прежнему
отклоняет, для них правило действует во вложенном запросе. Оконные, `WITHIN GROUP` и
агрегатные сортировки, остальные формы `GROUP BY`, ClickHouse и chart impact сохранили
прежние чтения. `VERIFIER_VERSION` 39 → 40,
`CHECK_VERSION` 20 → 21. Реализовано `gpt-6-sol` high через MyFlow-кандидат `sol-high`;
унарный `+` сохраняет input-чтение и не даёт имени неалиасированной проекции,
сортировка после скобочного запроса относится к его телу, а `(column)` сохраняет
имя выхода. Полный offline pytest: 2737 passed, 20 skipped, 38 deselected; strict mypy:
0 ошибок в 120 модулях, Ruff и Black чистые. Правила сверены вживую на PostgreSQL 16.15
(одноразовый кластер на Mac); ClickHouse 24.8.14.39 форму `(SELECT …) ORDER BY …`
отвергает синтаксически. Синтетическое ревью `gpt-6-astra` xhigh через MyFlow: проход 1
— три находки (унарный `+`, сортировка после скобочного запроса, `(column)`) исправлены,
проход 2 — F-T-24-7 (`+dm.stores.id`) исправлена, проход 3 — 0 findings; не
human/production acceptance.

**Предыдущий срез (2026-09-27):** PostgreSQL-семейство теперь разбирает целое имя в
`GROUP BY` по правилам движка: локальная колонка выигрывает у одноимённого выходного
столбца, а при отсутствии объявления в governed-источниках остаётся advisory
`asset.query_fields_unchecked` вместо ложного `asset.query_ungoverned_field`. Имя
выходного столбца в запросе без источника не оставляет гэпа; выражения, агрегатные и
оконные выходы и квалифицированные имена сохраняют чтение input-колонки. ClickHouse и
chart impact не менялись. `VERIFIER_VERSION` 38 → 39, `CHECK_VERSION` 19 → 20. Реализовано
`gpt-6-sol` high через MyFlow-кандидат `sol-high`; полный offline pytest: 2684 passed, 20
skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые. Правило
сверено вживую на PostgreSQL 16.15 (одноразовый кластер на Mac). Синтетическое ревью
`gpt-6-astra` xhigh через MyFlow: F-T-23-1 (имя `GROUP BY` в LATERAL без собственного
FROM) исправлена, второй проход — 0 findings; не human/production acceptance.

**Предыдущий срез (2026-09-27):** governance reviewed SQL на ClickHouse и
PostgreSQL-семействе называет ключи `JOIN … USING` как advisory
`asset.query_fields_unchecked`: общий ключ нельзя приписать одной таблице, поэтому
частичная проверка больше не выглядит чистой. Явные соседние колонки грейдятся
отдельно; `JOIN … ON` без изменений. `VERIFIER_VERSION` 37 → 38,
`CHECK_VERSION` 18 → 19. Выбор среза — синтетическое `gpt-6-astra` xhigh review,
реализация — `gpt-5.6-luna` xhigh; финальное синтетическое Astra review:
0 actionable P1/P2, не human/production acceptance. Полный offline pytest:
2655 passed, 21 skipped, 38 deselected; docs-as-code: 24 passed; strict mypy:
0 ошибок в 120 модулях, Ruff и Black чистые.

**Предыдущий срез (2026-09-26):** обычная трёхчастная колонка `dm.sales_daily.revenue`
больше не открывает scalar `WITH dm`, если точно совпадает с единственным неалиасированным
именованным источником `FROM`/`JOIN` своего SELECT и имя `dm` не занято другим источником.
Ложные chart reads и `chart.sql_partial` исчезают; несовпадение, дубликаты, конфликтующие
алиасы и catalog-источник остаются консервативными. `VERIFIER_VERSION` 36 → 37,
`CHECK_VERSION` остаётся 18. Синтетическое review — `gpt-6-astra` xhigh,
3 P2 закрыты red-green тестами; не human/production acceptance. Полный offline pytest:
2647 passed, 21 skipped, 38 deselected; docs-as-code: 24 passed; strict mypy:
0 ошибок в 120 модулях, Ruff и Black чистые.

**Предыдущий срез (2026-09-26):** двухчастная колонка `t.column` с однозначным явным
алиасом именованного табличного источника `FROM`/`JOIN … AS t` своего SELECT больше не открывает
одноимённый scalar `WITH t`. Ложные chart reads и `chart.sql_partial` исчезают; настоящий
доступ к `t.1` и отдельное чтение scalar alias остаются. `VERIFIER_VERSION` 35 → 36,
`CHECK_VERSION` остаётся 18. Итоговое синтетическое `gpt-6-astra` xhigh review:
0 actionable findings после закрытия P2; не human/production acceptance.
Полный offline pytest: 2641 passed, 21 skipped,
38 deselected; chart-impact suite: 260 passed; docs-as-code: 24 passed; strict mypy:
0 ошибок в 120 модулях, Ruff и Black чистые.

**Срез verifier 35 (2026-09-26):** однозначная ссылка на named CTE внутри вложенного
`WITH` связывается с ближайшим объявлением. Внутренняя CTE больше не открывает
одноимённую внешнюю и её ложный `chart.sql_partial`; отдельное внешнее чтение
сохраняет гэп. Self-reference и одноуровневые дубликаты остаются консервативными.
`VERIFIER_VERSION` 34 → 35, `CHECK_VERSION` остаётся 18. Полный offline pytest до
добавления трёх граничных тестов: 2628 passed, 21 skipped, 38 deselected; после них
chart-impact suite: 179 passed. Strict mypy: 0 ошибок в 120 модулях. Синтетическое
ревью `gpt-6-astra` xhigh: дефектов кода не найдено, один пробел покрытия закрыт
тестами; не human/production acceptance.

**Срез verifier 34 (2026-09-26):** chart impact учитывает `JOIN … USING` только в
исполняемой части операторского SQL. Ключ JOIN в непрочитанной named CTE или scalar
`WITH` больше не создаёт ложный `chart.sql_partial`; открытая ссылка сохраняет гэп.
`VERIFIER_VERSION` 33 → 34, `CHECK_VERSION` остаётся 18. Полный offline pytest:
2623 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях,
Ruff и Black чистые. Синтетическое ревью `gpt-6-astra` xhigh: 0 actionable
findings, не human/production acceptance.

**Срез verifier 33 (2026-09-26):** chart impact учитывает ClickHouse `COLUMNS('regex')`
только в исполняемой части операторского SQL. Селектор в непрочитанной named CTE или
scalar `WITH` больше не создаёт ложный `chart.sql_partial`, а открытая ссылка сохраняет
гэп. Governance reviewed asset SQL ищет этот селектор во всём AST. `VERIFIER_VERSION`
32 → 33, `CHECK_VERSION` остаётся 18. Полный offline pytest: 2618 passed, 21 skipped,
38 deselected; strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые. Синтетическое
ревью `gpt-6-astra` xhigh: 0 actionable findings, не human/production acceptance.

**Ранее (2026-09-26):** ClickHouse `COLUMNS('regex')` в reviewed asset SQL
оставляет advisory `asset.query_fields_unchecked`: regex выбирает физические колонки,
полный набор которых SQL не перечисляет. Явное соседнее чтение грейдится отдельно;
PostgreSQL-семейство без изменений. `VERIFIER_VERSION` 31 → 32, `CHECK_VERSION` 17 → 18.
Полный offline pytest: 2612 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в
120 модулях. Итоговое синтетическое ревью `gpt-6-astra` xhigh: 0 actionable findings,
не human/production acceptance.

**Более ранний срез (2026-09-26):** восстановлен package-wide strict mypy для SQL AST.
Аннотации `Expr`/`Selectable`, nullable cache/scope и коллекций согласованы с sqlglot без
изменения SQL-поведения или версий evidence. Strict mypy: 0 ошибок в 120 модулях; полный
offline pytest: 2603 passed, 21 skipped, 38 deselected. Синтетическое ревью `gpt-6-astra`
xhigh: 0 actionable findings, не human/production acceptance.

**Ещё ранее (2026-09-26):** однозначная ссылка внутри вложенного scalar `WITH` теперь
связывается с ближайшим объявлением. Внутренний алиас не открывает внешние `dictGet`, `joinGet`
или `*`; self-reference, дубликаты на одном уровне и shadowing именованных CTE остаются
conservative. Итоговое synthetic/modelled review `gpt-6-astra` xhigh — 0 actionable findings;
полный offline pytest: 2603 passed, 21 skipped, 38 deselected. Это не human evidence и не
production/design-partner acceptance.

Четыре исторических tracked pilot-bundle в `docs/pilot/2026-08-25-ch-*` выпущены verifier 2
и такими остаются: `auto_bi decision evidence` по ним проходит (все проверены 2026-09-12,
exit `0`), потому что перепроверка адресует запечатанный bundle и не требует текущей версии.
Mac verifier 7 runs из proxy-review 2026-09-04 в Git не сохранены. Отдельный
[chart-impact overlay](pilot/2026-09-12-owner-review-chart-impact/README.md) выпущен verifier
14 и tracked вместе с evidence: его `operator_table`
(`raw_sql: SELECT region, sum(revenue) FROM dm.sales_daily GROUP BY region`) записан как
`chart.opaque_sql`. Текущий гейт добавляет chart resolution, provenance, governance
reviewed-запроса, runtime cost facts и signing identity; свежий прогон на тех же входах
overlay читает operator SQL и для этой плитки называет `chart.metric_unread` —
statement прочитан целиком, связь с изменённой метрикой не выведена, `computes_metric` не
заявляется, вердикт остаётся `block`, — а detail `impact.products` пишет «1 delivered
chart(s) named (0 read only in part); of the tiles not named: 0 whose SQL could not be
read, 0 read only in part, 1 read in whole but not settled against a changed metric».
У исторического pilot-числа нет attestation, поэтому свежий прогон показывает
`provenance.unattested` (review). Dashboard-only overlay с теми же semantic snapshots и
продуктом сохраняет `run_id`; конкретный bundle различается evidence digest и каталогом. Отдельный
[live R3 package](pilot/2026-09-12-owner-review-live-r3/README.md) связывает эти возможности
в одном Mac-прогоне: candidate capture аттестован, ClickHouse runtime checks исполнены,
manifest подписан и проверен по сохранённому public trust root.

## Коротко

`Auto_BI_new` — локальный additive fork Auto_BI v0.5.0. **Analytics Release &
Evidence Gate** проверяет governed-семантику, product impact и content-addressed
evidence; унаследованный путь «запрос → DashboardSpec → BI» остаётся baseline.

Рабочий путь DecisionOps:

```text
contracts -> validate -> snapshots/diff -> product impact -> result reconciliation
          -> reference SQL -> DWH checks -> pure BI compile -> evidence bundle/report

reviewed asset SQL -> read-only capture -> canonical ResultSet + attestation -> gate input
```

Offline investigation alpha работает отдельным контуром:

```text
InvestigationPlan + CanonicalMetricResult -> четыре pure version-pinned skill
       -> evidence-backed observations/gaps -> deterministic brief artifacts
```

- `auto_bi decision validate` проверяет связанные YAML-контракты.
- `auto_bi decision capture` исполняет reviewed SQL одного positive asset под SELECT-only
  guard, сверяет типы и nullable-контракт с asset'ом и пишет новый gate-ready YAML с
  canonical result и attestation, привязанной к digest кандидатного semantic snapshot'а.
- `auto_bi decision verify` выполняет one-shot gate без удалённой BI-мутации; impact
  доходит до конкретного чарта доставляемого дашборда (`computes_metric` / `reads_field`).
  Плитка с оператором `raw_sql` читается из своего SQL: `reads_field` там, где statement
  сам атрибутирует чтение, `computes_metric` не выводится; нерешаемые плитки выходят
  именованными `chart_gaps` (`chart.opaque_sql` — текст не разобрали, `chart.sql_partial` —
  прочитали частично, `chart.metric_unread` — связь с метрикой не вывели). Словарный вызов
  ClickHouse в исполняемой части statement либо называет своё чтение, либо оставляет
  плитку прочитанной частично: геттер из закрытого набора (`dictGet`, `dictGetOrDefault`,
  `dictGetOrNull`, `dictGetAll` и типизированные `dictGet<Type>` с теми же суффиксами) с
  литеральными словарём и атрибутом читается как `<словарь>.<атрибут>` (кортеж атрибутов —
  по чтению на каждый) и участвует в `reads_field` и правиле метрики как любое
  атрибутированное чтение; любой другой `dict…` — нелитеральный аргумент, `dictHas` /
  `dictIsIn`, иерархия, неизвестная функция — `chart.sql_partial`. То же для семейства
  Join-таблицы: `joinGet` / `joinGetOrNull` с литеральными таблицей и одной колонкой
  читается как `<таблица>.<колонка>`; любой другой `joinget…` — `chart.sql_partial`.
  Какая часть statement исполняется, решает один обход гейтов CTE, общий для словаря,
  Join-таблицы и звезды: именованная CTE открывается, когда исполняемая неквалифицированная
  ссылка на таблицу её называет или когда исполняемое голое `IN <name>`, которое не
  видимый алиас, её называет; scalar `WITH` — когда исполняемая колонка читает алиас
  (голое имя или обращение к кортежу с любого уровня). Плитка без словарного или Join-вызова
  и без звезды обход не делает. На ClickHouse видимый алиас — не колонка таблицы плитки, а
  чтение через expression-form `WITH` даёт `reads_field`; `WHERE region IN dm.rev` читается
  как `IN (SELECT * FROM dm.rev)`, поэтому изменение `dm.rev.revenue` оставляет плитку
  прочитанной частично (`chart.sql_partial`, «it selects *»), а не молчит. Metric-derived гэпы (`chart.metric_unread`,
  `chart.metric_unknown`, `chart.formula_untyped`) записываются только для изменения,
  которое достигает хотя бы одного продукта в scope: вопрос, которого никто в scope не
  задавал, дырой этого отчёта не является. `chart.metric_unread` при этом спрашивается по
  дашборду: гэп ложится на плитку raw-SQL, только когда продукт в scope, доставляющий
  именно этот точный pinned ref, декларирует одну из изменённых метрик — общий дашборд
  спрашивает объединение деклараций всех доставляющих продуктов, смешанное изменение
  фильтруется по каждому дашборду независимо, две версии одного дашборда не сливаются;
  чтения полей, IR-совпадение `computes_metric` и структурные гэпы остаются всем
  изменением.
- `results.provenance` оценивает, откуда пришло кандидатное число: snapshot, среда и
  исполненный запрос сверяются с тем, что гейт держит независимо; число без attestation
  называется `provenance.unattested`. Ни одна находка не блокирует.
- `assets.invalidated` дополнительно сверяет reviewed `reference_query` с кандидатным
  snapshot'ом: неуправляемая витрина — `asset.query_ungoverned`, необъявленная колонка
  governed-витрины (в том числе голое имя, которого не объявляет ни один governed-источник,
  который его scope читает) — `asset.query_ungoverned_field` (обе review). Источники
  scope'а — те, что он читает: непрочитанный CTE колонок не предлагает. Голое имя в
  подзапросе, который может читать охватывающий запрос, приписывается только когда его
  объявляет один локальный governed-источник, иначе остаётся непроверенным; тело LATERAL
  видит предшествующие FROM на каждом уровне до стены derived table/CTE. На ClickHouse
  видимый алиас — не чтение колонки: SELECT-алиас виден на уровне запроса, `WITH <expr>
  AS a` — в нём и во всех подзапросах ниже; ссылка на expression-form WITH читается
  насквозь (колонки из `<expr>` грейдятся в referencing scope; запрос, вложенный в
  `<expr>`, — как тот же подзапрос на месте ссылки, голые имена enclosed; внутри
  определения SELECT-алиаса X, WITH-выражение, прочитанное насквозь из него, включено,
  X — колонка; SELECT-алиас на цикле через SELECT- или WITH-определения — колонка где
  бы на него ни сослались; WITH-ссылка, замыкающая цикл на пути раскрытия, — колонка,
  определения по пути всё равно читаются; ссылка за глубиной/бюджетом раскрытий —
  unattributed под своим именем, обрезанное раскрытие не мемоизируется). На ClickHouse
  `x IN <table>` читается как `x IN (SELECT * FROM <table>)`: ложной колонки
  `dm.sales_daily.rev` больше нет, а неуправляемая таблица справа от `IN` —
  `asset.query_ungoverned` (review). Причина `it selects *` на ClickHouse следует
  достижимости по общим с chart impact гейтам CTE; непрочитанная named CTE или scalar
  `WITH` её не создаёт. Именованные чтения сохраняют прежнюю область проверки.
  PostgreSQL-семейство без изменений. ClickHouse
  `COLUMNS('regex')` оставляет `asset.query_fields_unchecked`: выбранный набор физических
  колонок нельзя доказать по SQL или перечню snapshot-полей. Явные соседние чтения
  грейдятся отдельно. Причина `COLUMNS(...)` следует достижимости по тем же гейтам CTE:
  непрочитанный селектор её не создаёт; общий extractor сохраняет whole-AST остаток.
  `JOIN … USING` на обоих семействах движков тоже оставляет
  advisory с именами ключей: общий ключ нельзя приписать одной таблице. Governance
  собирает этот остаток по всему AST. Неразрешимая без догадки часть запроса —
  `asset.query_fields_unchecked` (advisory).
- `auto_bi decision verify --signing-key <private.pem>` opt-in подписывает canonical
  manifest Ed25519-ключом; private key читается только по явному внешнему пути.
- `auto_bi decision evidence --trust-key <public.pem>` перепроверяет digest каждого
  artifact и подпись против pinned public trust root. Для ротации флаг повторяется.
- `auto_bi decision investigate <plan.yaml> --results <result.yaml> ... --output <dir>`
  запускает только offline investigation skills и требует новый output-каталог.
- Без `--dwh` engine checks дают `check.not_configured`; с `--dwh` работают
  `dwh.access`, `dwh.freshness`, `dwh.physical`, `dwh.security` и `dwh.cost`.
- Exit codes: `0` pass/advisory, `3` needs_review, `4` block, `2` runtime/usage error.

Remote и package identity fork не настроены; `autobi-agent==0.5.0` — baseline.

## Bootstrap и pilot

| Область | Состояние |
|---|---|
| Tasks 1–7 | завершены |
| Task 8: технический pilot | завершён на ClickHouse 24.8.14.39; R3 unified live package tracked и проверен |
| Task 8: proxy-owner review | v7 outcome записан исторически, bundles не tracked; свежий v14 proxy подтвердил scope/actionability, R1/R2 и техническая часть R3 закрыты tracked evidence |
| Task 8: human owner outcome | открыт: реальное время и понятность отчёта не измерены |
| Offline typed investigation alpha | реализован для supplied canonical results; trust `exploratory` |
| Chart-level impact | реализован 2026-09-07; 2026-09-17 операторский `raw_sql` читается из SQL (`reads_field`, никогда `computes_metric`); `chart.opaque_sql` — неразобранный текст, `chart.sql_partial` — частичное чтение, `chart.metric_unread` — невыведенная связь с метрикой; 2026-09-18 словарный вызов ClickHouse и `joinGet` / `joinGetOrNull` называют чтение `<таблица>.<колонка>` или оставляют плитку прочитанной частично, metric-derived гэпы — только для изменения, достигающего продукта в scope; один обход гейтов CTE решает исполняемую часть statement для словаря, Join-таблицы и звезды; ClickHouse-алиас больше не `chart.sql_partial` «not attributed to a table», а чтение через expression-form WITH даёт `reads_field` (verifier 24); `IN <table>` читается как звезда над этой таблицей и открывает именованную CTE (verifier 25); 2026-09-22 `chart.metric_unread` решается по каждому дашборду — объединением метрик продуктов в scope, доставляющих этот точный pinned ref (verifier 26); 2026-09-24 inline- и ARRAY JOIN-алиасы не дают phantom `reads_field`, но ARRAY source остаётся чтением (verifier 27); tracked verifier 14 overlay сохраняет `computes_metric` и `chart.opaque_sql`; грейдинг не меняет |
| Result provenance | реализован 2026-09-07: проверяемая attestation, gate SQL по-прежнему не исполняет |
| Governance reviewed-запроса | реализована 2026-09-07: таблицы и колонки запроса сверяются со snapshot'ом; 2026-09-11 голое имя приписывается, когда модель оставляет одного кандидата; 2026-09-17 имя разрешается только по источникам, которые scope читает; голое имя в коррелированном подзапросе, которого не объявляет ни один локальный governed-источник, остаётся непроверенным, а не ложным `asset.query_ungoverned_field`; тело LATERAL видит предшествующие FROM на каждом уровне до стены derived table/CTE; 2026-09-18 ClickHouse-алиас читается насквозь, а не как колонка (`CHECK_VERSION` 11 → 12); `x IN <table>` читается как `x IN (SELECT * FROM <table>)`: ложной находки поля `IN rev` больше нет, неуправляемая таблица справа от `IN` — review `asset.query_ungoverned` (`CHECK_VERSION` 12 → 13); 2026-09-24 inline- и ARRAY JOIN-алиасы не именуются как поля, но их определения и source array грейдятся (`CHECK_VERSION` 13 → 14) |
| Reviewed result capture | реализован 2026-09-11; live-контракты PostgreSQL и ClickHouse 24.8.14.39 пройдены |
| Evidence authentication | реализована 2026-09-12: opt-in Ed25519, pinned SPKI trust root; pilot-only signer создан вне checkout на Mac, production trust root не назначен |
| Continuous platform | заблокирована до human/design-partner evidence |

Четыре tracked pilot-сценария и найденные дефекты описаны в
[pilot rehearsal](DECISIONOPS_PILOT_2026-08-25.md) и `docs/pilot/`; сохранённые там bundles
выпущены verifier 2 и сохраняют ожидаемые verdict и integrity `pass`. Отдельные Mac verifier 7 runs
описаны в [симуляции owner-gate 2026-09-04](DECISIONOPS_OWNER_GATE_SIMULATION_2026-09-04.md),
но их bundles в Git нет. [Новая proxy-симуляция](DECISIONOPS_OWNER_GATE_SIMULATION_2026-09-12.md)
подтвердила scope/actionability свежих verifier 14 отчётов. Её пробел R2 закрыт отдельным
[tracked chart-impact overlay](pilot/2026-09-12-owner-review-chart-impact/README.md): affected
report содержит один `computes_metric` chart и один именованный `chart.opaque_sql` gap.
Свежий прогон на тех же входах сохраняет `computes_metric` и для `operator_table` называет
`chart.metric_unread`.
[Live R3 package](pilot/2026-09-12-owner-review-live-r3/README.md) добавляет к тому же change
reviewed capture, provenance/governance, все пять live DWH checks и signed evidence.
Это всё ещё техническая подготовка. Task 8 human outcome остаётся открытым,
а [ADR 0004](adr/0004-synthetic-proxy-offline-investigation.md) не разблокирует
scheduler, PostgreSQL, REST/MCP, notifications или actions.

## Карта реализации

| Ответственность | Код |
|---|---|
| Contracts, product/chart impact | `auto_bi/decision/`, `auto_bi/semantic/` |
| Verification, DWH probe, result capture и pure BI compile | `auto_bi/verification/` |
| Provenance кандидатного числа | `auto_bi/verification/provenance.py`, `repository.py` |
| Evidence и owner report | `auto_bi/evidence/`, `auto_bi/verification/summary.py` |
| Typed offline investigation | `auto_bi/investigation/model.py`, `data.py`, `skills.py`, `claims.py`, `application.py` |
| CLI | `auto_bi/cli.py` |

Нормативный дизайн и обязательные инварианты описаны в
[DECISIONOPS_ARCHITECTURE.md](DECISIONOPS_ARCHITECTURE.md), решение об additive fork —
в [ADR 0003](adr/0003-decisionops-additive-control-plane.md).

## Что ещё не реализовано

- Для external/reviewed-SQL формул и неизвестного семейства движков reference SQL
  остаётся явным semantic gap; такая формула не несёт термов, поэтому и до плитки
  не разрешается (`chart.formula_untyped`).
- Chart-разрешение не заменяет lineage: операторский `raw_sql` больше не непрозрачен как
  таковой — `chart.opaque_sql` это SQL, который не разобрали, `chart.sql_partial` — SQL,
  прочитанный частично, `chart.metric_unread` — связь с метрикой, которую резолвер
  отказывается выводить, а `computes_metric` для operator SQL по-прежнему не заявляется.
  Связь «чарт считает метрику» — вывод по точному совпадению термов IR-меры, потому что в
  IR у чарта нет ссылки на метрику, а из произвольного SQL этот срез агрегат не читает.
  Правило скрытого чтения — контракт (неразворачиваемое выражение оставляет плитку
  прочитанной частично); детектор узнаёт именованный набор конструкций — `{{ … }}`,
  `COLUMNS('re')`, `JOIN … USING`, словарное семейство ClickHouse и семейство
  `joinGet` / `joinGetOrNull`. Для chart impact `COLUMNS('re')` учитывается только в
  исполняемой части SQL: непрочитанные named CTE и scalar `WITH` гэпа не создают.
  Ключ `JOIN … USING` следует тем же гейтам исполняемой части.
  Словарный вызов и вызов Join-таблицы — default-deny:
  литеральный геттер называет чтение `<таблица>.<колонка>` (для словаря — ещё кортеж
  атрибутов) и участвует в `reads_field` и правиле метрики; любой другой вызов семейства
  оставляет плитку прочитанной частично. Конструкция вне этого набора — дыра детектора,
  а не гарантия полного чтения. `WHERE region IN rev` / `IN dm.rev` читается как
  `IN (SELECT * FROM <name>)`: плитка выбирает `*` из этой таблицы, поэтому изменение
  колонки той таблицы — `chart.sql_partial` (звезда), а не молчание. Алиас на том же
  месте — не колонка: `WHERE region IN allowed` при `WITH […] AS allowed` не
  приписывает `allowed` таблице плитки и не открывает именованную CTE. Голое имя на
  правой стороне `IN`, которое не видимый алиас, открывает именованную CTE; видимый
  scalar WITH- или SELECT-алиас с тем же именем выигрывает у CTE, и табличная форма тогда
  молчит там, где `IN (SELECT * FROM x)` назвал бы чтение CTE (F-T-14-3).
- Резолвер связывает ссылку на scalar `WITH` или named CTE с ближайшим объявлением, когда
  такое связывание однозначно: вложенное объявление не открывает скрытое внешнее (F-T-11-3),
  в том числе для `dictGet`, `joinGet`, `*` и `JOIN … USING`. Имена ClickHouse
  сравниваются с точным регистром: `a` не затеняет `A` и не открывает одноимённое
  при сворачивании регистра соседнее объявление. Self-reference и дубликаты
  одноимённых объявлений на одном уровне остаются conservative. Обычная двухчастная колонка
  `t.region` при однозначном явном алиасе именованного `FROM`/`JOIN`-источника того же SELECT читает
  таблицу и не открывает scalar `WITH t` (F-T-11-7). Обычная трёхчастная колонка
  `dm.sales_daily.revenue` также не открывает scalar `WITH dm`, когда она точно называет
  единственный неалиасированный источник своего SELECT и `dm` не конфликтует с другим
  именем источника (F-T-11-13). Иначе связывание остаётся консервативным.
- `dwh.cost` проверяет ClickHouse `max_scanned_bytes` и `timeout_ms` exact reference SELECT'ом
  под engine limits без материализации ответа; PostgreSQL так же потоково проверяет
  `timeout_ms`. Native API PostgreSQL 14–18 не отдаёт честный exact-query `scanned_bytes`:
  `EXPLAIN (ANALYZE, BUFFERS)` и transaction counters считают block requests,
  `pg_stat_io`/PostgreSQL 18 `pg_stat_get_backend_io` — I/O и cache events, и ни один из
  них не ставит byte ceiling. Поэтому этот потолок остаётся явным `cost.not_measured`
  (**review**), а не переименованной planner/buffer/I/O-аппроксимацией.
- Gate остаётся pure и сам не выполняет **verified-asset SQL**. Отдельный
  `auto_bi decision capture` теперь исполняет его read-only драйвером, канонизирует ответ и
  формирует attestation; snapshot, среда и запрос затем сверяются с тем, что гейт держит
  независимо. Ed25519 теперь аутентифицирует producer и неизменность итогового bundle, но
  сама не доказывает, что capture runner действительно исполнил заявленный запрос. R3 pilot
  выполняет capture и signing одним runner'ом с ключом вне checkout; production-доказательство
  требует boundary, в которой private key недоступен вызывающей стороне.
- Governance reviewed-запроса покрывает таблицы и колонки. Колонка приписывается
  таблице там, где выбора не оставляет сам SQL, а для голого имени — там, где его не
  оставляет governed-модель: ровно один источник, который scope читает, объявляет это
  имя, и все такие источники — governed-таблицы. На ClickHouse видимый алиас не
  колонка: SELECT-алиас виден на уровне запроса и не виден в подзапросе; scalar
  `WITH` виден в объявившем запросе и ниже; ссылка на `WITH <expr> AS a` читает
  колонки из `<expr>` в referencing scope, а запрос, вложенный в `<expr>`, — как
  тот же подзапрос на месте ссылки (свой FROM, голые имена enclosed); внутри
  определения query-level алиаса X, WITH-выражение, прочитанное насквозь из него,
  включено, X — колонка; query-level алиас на цикле через query-level или WITH-определения —
  колонка где бы на него ни сослались; WITH-ссылка, замыкающая цикл на пути
  раскрытия, — колонка, определения по пути всё равно читаются; обрезанное
  раскрытие не мемоизируется. На ClickHouse `x IN <table>` читается как
  `x IN (SELECT * FROM <table>)`. PostgreSQL-семейство без изменений. Inline-алиас в
  любой клаузе своего уровня и ARRAY JOIN-алиас видны на этом уровне, их определения
  грейдятся на месте, а в подзапрос они не видны. Имя, объявленное и WITH-, и SELECT-алиасом на одном
  уровне, читается как колонка: `WITH revenue AS X, X * 2 AS w SELECT w AS X FROM
  dm.sales_daily` даёт `asset.query_ungoverned_field` на `dm.sales_daily.X` (F-T-13-17,
  риск принят). Видимый scalar WITH-, SELECT-, inline- или ARRAY JOIN-алиас справа от `IN` выигрывает у
  одноимённой CTE, и табличная форма молчит там, где подзапросная открыла бы CTE
  (F-T-14-3). Оба случая требуют одного имени, объявленного дважды, — ClickHouse такой
  statement, скорее всего, отвергает. FROM-алиас справа от `IN` читается как таблица
  этого имени, как и в подзапросной форме: `FROM dm.sales_daily AS rev WHERE region IN
  rev` даёт `asset.query_ungoverned` на `rev` (F-T-14-1) — пере-сообщение, чтения не
  прячет. Голое имя в подзапросе, который может
  читать охватывающий запрос (scalar/IN/EXISTS), остаётся непроверенным, если его не
  объявляет ни один локальный источник: проверка имя наружу не ведёт. `SELECT *` и
  `COUNT(DISTINCT *)` остаются непроверенными — distinct-значение зависит от всех колонок
  источника; один `COUNT(*)` считает строки и колонок не читает. Имя, объявленное
  несколькими источниками (в том числе на двух LATERAL-уровнях), scope с неуправляемой
  витриной, читаемым CTE или подзапросом остаются непроверенными — названными
  (`asset.query_fields_unchecked`), а не молча пропущенными.
  В PostgreSQL-семействе целое имя `GROUP BY`, совпадающее с допустимым выходным
  столбцом, проверяется после локальных источников и оставляет advisory, если ни один
  governed-источник его не объявляет; целое голое имя `ORDER BY` и `DISTINCT ON`
  сначала называет выход SELECT, а имя внутри выражения читает input-колонку.
  `ORDER BY` set operation называет выход левой ветки.
- На ClickHouse `SETTINGS` — настройка, не чтение поля: ни имя, ни значение не участвуют в
  governance или alias read-through на любом query level. `additional_table_filters` хранит
  непрочитанный SQL row filter и поэтому оставляет проверку частичной; `additional_result_filter`
  не добавляет чтение колонки таблицы.
- Источники, дошедшие с охватывающих LATERAL-уровней, — кандидаты рядом с ближайшими, а
  не упорядоченные по уровню, поэтому имя, которое объявляют несколько уровней, остаётся
  непроверенным вместо того, чтобы быть приписанным ближайшему: innermost-first изменил бы
  контракт атрибуции для каждого коррелированного scope и сначала должен быть задан как
  требование.
- Нет измеренного человеком owner outcome: proxy-review не заменяет время и
  подтверждение понятности от живого владельца.
- Production-certified brief, scheduler, PostgreSQL platform runtime, MCP/actions,
  **Ossie**, **MetricFlow** и **column-level lineage** не реализованы; проверка колонок
  reviewed-запроса — это governance («объявлено ли поле»), а не lineage («откуда значение
  пришло через преобразования»). Offline brief
  остаётся `exploratory` и не является human acceptance evidence.

## Быстрая проверка

```bash
uv run pytest tests/test_decision_verify.py tests/test_decision_cli.py \
  tests/test_decision_impact.py tests/test_decision_chart_impact.py \
  tests/test_result_provenance.py tests/test_result_capture.py tests/test_result_probe.py -q
uv run pytest tests/test_investigation_plan.py tests/test_investigation_skills.py \
  tests/test_investigation_brief.py tests/test_investigation_cli.py -q
uv run auto_bi decision --help
uv run auto_bi decision evidence docs/pilot/2026-08-25-ch-tz-margin/evidence \
  --artifact report-summary-md
```

Полный прогон гейта на входах, лежащих в репозитории (не на сохранённом bundle, а с
нуля) — `docs/pilot/2026-08-25-ch-tz-margin/` держит контракты, оба semantic ref'а,
дашборды, asset и записанный результат:

```bash
P=docs/pilot/2026-08-25-ch-tz-margin
uv run auto_bi decision verify $P/contracts --base $P/base --candidate $P/candidate \
  --dashboards $P/dashboards --assets $P/assets --results $P/results.yaml \
  --product decision://sales/weekly_margin --evidence .evidence
uv run auto_bi decision investigate tests/fixtures/investigation/weekly_margin.yaml \
  --results tests/fixtures/investigation/revenue_results.yaml \
  --output .investigation-example
```

Ожидаемый исход — `block`: timezone change доходит до governed-продукта. У pilot-asset'а
нет ни attestation'а, ни `reference_query`, поэтому там же видно `provenance.unattested`
(review) и молчание governance-проверки запроса — так и должно быть для сохранённых
pilot-входов без этих полей; сам tracked historical bundle выпущен verifier 2.

Для owner-review chart resolution используется
[`docs/pilot/2026-09-12-owner-review-chart-impact/`](pilot/2026-09-12-owner-review-chart-impact/README.md).
Его verifier 14 bundle имеет release verdict `block` (exit `4`) и отдельно проходит
integrity inspection (exit `0`); исторические `2026-08-25` dashboard inputs не менялись.

Единый live owner-review пример находится в
[`docs/pilot/2026-09-12-owner-review-live-r3/`](pilot/2026-09-12-owner-review-live-r3/README.md).
Его signed verifier 14 bundle имеет release verdict `block` (exit `4`) и проходит inspection
с pinned public key (`signature_verified=true`, exit `0`).

Шаблон для собственных контрактов — тот же вызов со своими путями:

```bash
uv run auto_bi decision validate <contracts/>
uv run auto_bi decision verify <contracts/> --base <base/> --candidate <candidate/> \
  --dashboards <dashboards/> --evidence .evidence --signing-key <private.pem>
uv run auto_bi decision evidence .evidence --trust-key <public.pem>
```

Без `--signing-key` сохраняется совместимый `integrity=content_hashed`. Signing key —
внешний unencrypted PKCS#8 PEM; pilot-only ключ R3 хранится на Mac вне checkout/evidence с
mode `0600`. Trust root — один или несколько SPKI public PEM. Private key в Git,
документацию, evidence, логи или Windows workspace не помещается; production key не назначен.

`investigate` пишет ровно четыре файла: canonical `plan.json`, canonical
`results.json` с входами и ordered skill outputs, typed `brief.json` и
deterministic `brief.md`. План фиксирует `max_queries=0`; application layer не получает
DWH/BI executor и не импортирует remote adapters. Missing/duplicate metric, budget или
scope stop завершаются кодом `2` до записи output-каталога. Unsupported или ambiguous
аналитический вход остаётся evidence-backed gap без factual claim. План объявляет
версию каждого skill: `data_health` — 1, `period_variance`, `contribution_decomposition`,
`concentration_shift` — 2; другая версия завершает команду кодом `2`. Числовые outputs
точны для значений любой длины и не зависят от decimal context вызывающего потока
или порядка строк.

Флаг `--dwh clickhouse|greenplum` включает пять read-only engine checks. На этой
Windows-машине запрещено поднимать WSL/Docker и аналогичные среды; стенды — только Mac.

## Что делать дальше

Разрешённый срез подписи закрыт [ADR 0005](adr/0005-ed25519-evidence-trust-root.md).
Техническая часть R3 закрыта tracked live package. Выполнено
[Astra xhigh review R3/R4](DECISIONOPS_ASTRA_REVIEW_2026-09-12.md): выбран ограниченный
срез пояснений гарантий в owner-summary реализован Luna xhigh. Раздел
`How to read this evidence` отделяет reconciliation от semantic approval, attestation
от execution proof, cost compiled reference SQL от capture SQL и подпись от sign-off.
Исторические bundles остаются на исходных версиях; grading проверок не менялся.
Закрыто позиционное чтение supplied result в reconciliation: колонки и business key
читаются по имени; native capture сохраняет порядок asset (T-27, verifier 59 / check 39).
Закрыта числовая идентичность integer/decimal business key и aggregate `group_by`;
детали сохраняют написание ключей своего result (T-28, verifier 60 / check 40).
Закрыта атрибуция environment-чтений к паре (витрина, event-time колонка)
(T-29, verifier 61 / check 41).
Закрыта погрешность сравнения float с объявленным допуском: точная binary64-разность
сравнивается с точным decimal-допуском (T-31, verifier 62 / check 42).
Закрыто сравнение нечисловых значений по виду и REVIEW для ожидаемого NaN
(T-32, verifier 63 / check 43).
Закрыто обнаружение колоночных INSERT/UPDATE у PostgreSQL-роли (T-33, verifier 64 / check 44).
Закрыт fail-open неотвеченного табличного write-privilege вопроса
(verifier 65 / check 45): read-only не подтверждён, доступ и runtime SELECT блокируются.
Закрыт fail-open неотвеченного identity-вопроса (verifier 66 / check 46):
роль и row-security bypass остаются неизвестными; доступ и runtime SQL блокируются.
Реализован fail-closed для частичных/NULL identity-строк (verifier 67 / check 47):
известные имя и bypass сохраняются, доступ и runtime SQL блокируются до полного
identity-ответа; доказанный superuser сохраняет прежнее правило.
Закрыто скрытие write-привилегий за `SET ROLE`-достижимой ролью (T-34, verifier 79 / check 55).
Закрыто прохождение невалидного поставляемого spec через `bi.contract` (T-35, verifier 80 / check 56).
Закрыто выпадение объявленной freshness метрики из canonical snapshot и semantic diff (T-36, verifier 81 / check 57).
Закрыто выпадение объявленных join paths метрики из canonical snapshot и semantic diff (T-37, verifier 82 / check 58).
Следующий выявленный Astra-срез закрыт:
[`--artifact` больше не обходит manifest pin](DECISIONOPS_ARTIFACT_PIN_REVIEW_2026-09-12.md).
Неверный `--expect-digest` даёт exit `4` и пустой stdout; правильный сохраняет проверки
подписи, classification и выбранного payload. Версии evidence не менялись.
Также устранено [смешение manifest версий при полной inspection](DECISIONOPS_MANIFEST_SNAPSHOT_REVIEW_2026-09-12.md):
digest, pin, подпись и возвращённый bundle используют одни прочитанные manifest bytes.
Это snapshot manifest в памяти, не атомарный snapshot всего каталога.
Правила закреплены в [AGENTS.md](../AGENTS.md) и
[ADR 0006](adr/0006-modelled-review-development-gate.md).
Зависимость от локального proxy отменена владельцем в `AGENTS.md`; модельные агенты
запускаются без этой проверки. Новый runtime или внешние actions не начинаются.

Закрыто разрешение голого имени по источникам, которые его scope читает: непрочитанный CTE
больше не держит атрибуцию, а имя в коррелированном подзапросе, которого не объявляет ни
один локальный источник, остаётся `asset.query_fields_unchecked`, а не ложным
`asset.query_ungoverned_field`.
Закрыт обход LATERAL: предшествующие FROM-элементы доходят до каждого уровня тела до стены
derived table/CTE; кандидаты с охватывающих уровней сидят рядом с ближайшими, не
упорядоченные по уровню.
Закрыта запись направлений LATERAL: durable spec и комментарии версий записывают, что имя
внутри тела может сдвинуть грейдинг в любую сторону, `needs_review` включительно; три
сценария фиксируют направления, у которых не было покрытия. Поведение на этом срезе не
менялось.
Закрыто чтение operator SQL: операторский `raw_sql` читается из SQL (`reads_field`, никогда
`computes_metric`); `chart.opaque_sql` — неразобранный текст, `chart.sql_partial` —
частичное чтение, `chart.metric_unread` — невыведенная связь с метрикой; `impact.products`
раскладывает плитки по коду гэпа.
Четыре среза реализованы grok xhigh (`grokw`) и ревьюированы Opus (`opus-cli`) через
MyFlow, потому что GLM-5.3 и Codex (Astra/Luna) 2026-09-17 были недоступны: моделированное
ревью, не Astra-review по ADR 0006 и не human evidence.
Закрыто словарное правило: вызов ClickHouse `dict…` в scope, который statement исполняет,
называет чтение `<словарь>.<атрибут>` для геттера из закрытого набора с литеральными
аргументами, а любой другой оставляет плитку прочитанной частично; вызов в непрочитанном CTE
или в scalar `WITH`, чей алиас никто не читает, не считается (срезы T-07 и T-10, verifier
19 и 21).
Закрыт scope metric-гэпа: `chart.metric_unread`, `chart.metric_unknown` и
`chart.formula_untyped` записываются только для изменения, которое достигает продукта в scope
(срез T-08, verifier 20).
Три среза 2026-09-18 реализованы grok xhigh (`grokw`); ревью — Opus через MyFlow:
`opus-cli` (на части прогонов он молча отдавал haiku, как Opus это ревью не засчитывается),
субагент `opus-sub` (opus high) и второй голос `fable`, потому что GLM-5.3 и Codex
2026-09-18 были недоступны: моделированное ревью, не Astra-review по ADR 0006 и не human
evidence.
Закрыто семейство Join-таблицы: `joinGet` / `joinGetOrNull` с литеральными таблицей и одной
колонкой называет чтение `<таблица>.<колонка>`, любой другой `joinget…` оставляет плитку
прочитанной частично с клаузой «whose Join table read this run could not name» (срез T-12,
verifier 22).
Закрыт общий обход гейтов: какая часть operator SQL исполняется, решает один обход
разобранного дерева — именованная CTE открывается, когда исполняемая неквалифицированная
ссылка на таблицу её называет, scalar `WITH` — когда исполняемая колонка читает алиас
(голое имя или обращение к кортежу с любого уровня); словарь, Join-таблица и звезда идут
по одному правилу, вычисленному один раз на SQL; плитка без такого вызова и без звезды
обход не делает (срез T-11, verifier 23). Первое ревью T-11 нашло, что достижимость по
sqlglot scopes прятала чтения, которые HEAD видел: sqlglot не строит scope внутри
expression-form `WITH`; починка — обход гейтов.
Два среза (T-12 и T-11) реализованы grok xhigh (`grokw`); ревью — Opus через MyFlow
(`opus-sub`), без второго голоса; GLM-5.3 и Codex недоступны: моделированное ревью, не
Astra-review по ADR 0006 и не human evidence.
Закрыто чтение ClickHouse-алиаса: видимый SELECT- или WITH-алиас больше не колонка таблицы
в scope, а колонки `WITH <expr> AS a` читаются там, где `a` используется (срез T-13,
verifier 24, check 12). Реализовано grok xhigh (`grokw`); ревью — Opus через MyFlow
(`opus-sub`), три прохода, без второго голоса; GLM-5.3 и Codex недоступны.
Закрыто `x IN <table>`: таблица справа от `IN` читается ровно как `x IN (SELECT * FROM
<table>)` — в governance-проверке и в достижимости chart impact (срез T-14, verifier 25,
check 13). Срез начал grok xhigh (`grokw`); оба grok-аккаунта упёрлись в лимиты, и
закончил opus-high (`claude -p`, claude-opus-5, effort high); ранний выход в
`clickhouse_in_table_fields` добавил оркестратор после красного полного гейта. Ревью —
один проход Fable (claude-fable-5-1, effort medium), записанный через MyFlow как внешнее
ревью: Opus-ревьюеры пропущены, потому что делят движок с автором, GLM-5.3 и Codex упёрлись
в rate limit. Оба среза — моделированное ревью, не Astra-review по ADR 0006, не human
evidence и не production/design-partner acceptance.
Закрыт per-dashboard scope metric-гэпа: `chart.metric_unread` на плитке raw-SQL возникает,
только когда продукт в scope, доставляющий именно этот точный pinned ref дашборда,
декларирует одну из изменённых метрик — объединением деклараций всех доставляющих
продуктов (общий дашборд), с независимой фильтрацией смешанного изменения по каждому
дашборду, без слияния версий дашборда и независимо от порядка продуктов; чтения полей,
IR-совпадение `computes_metric` и структурные гэпы (`chart.opaque_sql`,
`chart.sql_partial`, `chart.dashboard_spec_missing`) остаются всем изменением.
`chart.metric_unknown` и `chart.formula_untyped` — уровнем изменения: их subject —
метрика, не плитка (срез T-17, verifier 26, check 13). Реализовано GLM 5.3 xhigh
(изолированный профиль, попытка продолжена после rate limit); ревью — synthetic Astra
xhigh read-only, записанное оркестратором в ворк-ордере и направившее правку (восстановлен
`reaches_product` на `_match_raw`, фильтрация `computed` перенесена в `charts_for`):
моделированное ревью, не human evidence и не production/design-partner acceptance. Финальный
post-implementation review — один проход `gpt-6-astra` xhigh через MyFlow (2026-09-24),
0 findings.

Закрыт остаток ClickHouse inline- и ARRAY JOIN-алиасов: alias виден на всём своём query
level, но не в подзапросе, верхнеуровневый SELECT-алиас сохраняет приоритет, а ARRAY source
остаётся чтением (срез T-18, verifier 27, check 14). Поэтому `ARRAY JOIN tags AS a` больше
не даёт phantom `dm.sales_daily.a`, однако по-прежнему называет `dm.sales_daily.tags`; тот
же видимый alias справа от `IN` не открывает одноимённую CTE. Направления оба: снимаются
ложные поля алиасов, но inline-определение в HAVING или QUALIFY, которое прежняя атрибуция
пропускала, теперь грейдится на месте и может назвать реальное поле (`internal_cost_raw` в
`(internal_cost_raw AS y)`). Реализовано `gpt-5.6-terra` xhigh (Codex companion,
MyFlow-кандидат `terra-xhigh`); ревью — `gpt-6-astra` xhigh через MyFlow: моделированное
ревью, не human evidence и не production/design-partner acceptance. Итог ревью — шесть
проходов `gpt-6-astra` xhigh через MyFlow (2026-09-24): девять находок, восемь исправлены,
одна (порог по wall-clock) отклонена с доказательством, последний проход — 0 findings.

Закрыто чтение неквалифицированных input-колонок из HAVING и QUALIFY (срез T-19, verifier
28, check 15): PostgreSQL читает input своего query level, а ClickHouse сначала применяет
обычное read-through видимого алиаса и только затем источник. `cost` у единственного
источника теперь даёт `asset.query_ungoverned_field`; в join управляемых таблиц, ни одна из
которых не объявляет `cost`, тот же review называет `cost (read from one of ...)`. Advisory
`asset.query_fields_unchecked` возникает, когда имя объявляют несколько источников или scope
держит источник, внутрь которого snapshot не видит. Raw-SQL плитка получает `reads_field`.
Направление одно: только добавляются чтения и находки. Реализовано `gpt-5.6-terra` xhigh
(Codex companion, MyFlow-кандидат `terra-xhigh`); ревью — `gpt-6-astra` xhigh через MyFlow:
моделированное ревью, не human evidence и не production/design-partner acceptance. Итог ревью
— один проход `gpt-6-astra` xhigh через MyFlow (2026-09-25): одна находка (голое имя над
единственным подзапросом или CTE) отклонена воспроизведением — её запрос отвергают оба
движка, а имя читается так же, как в любой другой клаузе.

Закрыто чтение ClickHouse `SETTINGS` как настроек, а не колонок (срез T-20, verifier 29,
check 16): `max_threads`, `join_use_nulls` и значение-идентификатор вроде `random` больше не
дают phantom field read на любом query level и не открывают видимый alias. Уходят ложные
review `asset.query_ungoverned_field`, advisory `asset.query_fields_unchecked`, chart
`reads_field` и `chart.sql_partial` на имени настройки. `additional_table_filters` вместо
phantom review оставляет advisory `asset.query_fields_unchecked` и `chart.sql_partial`, потому
что его SQL row filter не прочитан; `additional_result_filter` чтения не добавляет. Реализовано
`gpt-5.6-terra` xhigh (Codex companion, MyFlow-кандидат `terra-xhigh`); ревью —
`gpt-6-astra` xhigh через MyFlow: моделированное ревью, не human evidence и не
production/design-partner acceptance. Итог ревью — один проход `gpt-6-astra` xhigh через
MyFlow (2026-09-25): 0 findings.

Закрыта асимметрия чтения `*` (срез T-21, verifier 30, check 17): один `COUNT(*)` считает
строки и больше не даёт advisory `asset.query_fields_unchecked`, а `COUNT(DISTINCT *)` зависит
от всех колонок источника и оставляет операторскую плитку частично прочитанной
(`chart.sql_partial`), а не «не этой плиткой». Реализовано `gpt-5.6-terra` xhigh (Codex
companion, MyFlow-кандидат `terra-xhigh`); ревью — `gpt-6-astra` xhigh через MyFlow:
моделированное ревью, не human evidence и не production/design-partner acceptance. Итог ревью —
один проход `gpt-6-astra` xhigh через MyFlow (2026-09-25): 0 findings.

Закрыто ложное открытие scalar `WITH` через однозначный явный алиас именованного табличного источника
того же SELECT (verifier 36, check 18): `t.region` читает таблицу `AS t`, не скрытое
определение `WITH … AS t`. Настоящий tuple-доступ `t.1` и отдельное чтение alias сохраняются.

Закрыто открытие затенённой named CTE одноимённой ссылкой из вложенного `WITH`
(verifier 35, check 18): chart impact выбирает ближайшее объявление для однозначной
табличной ссылки и `IN <name>`, а отдельное внешнее чтение по-прежнему открывает
внешнюю CTE. Self-reference и одноуровневые дубликаты остаются conservative.

Закрыт ложный `chart.sql_partial` от `JOIN … USING` в непрочитанной named CTE или
scalar `WITH` (verifier 34, check 18): ключ JOIN оставляет гэп только если его часть
statement исполняется. Синтетическое ревью `gpt-6-astra` xhigh: 0 actionable
findings, не human/production acceptance.

Закрыт ложный `chart.sql_partial` от ClickHouse `COLUMNS('regex')` в непрочитанной
named CTE или scalar `WITH` (verifier 33, check 18): в chart impact селектор следует
достижимости исполняемой части statement, а открытый селектор сохраняет гэп. Governance
на этом срезе сохраняло whole-AST область; verifier 55 / check 35 уточняют достижимость
селекторов также для reviewed asset SQL. Синтетическое ревью `gpt-6-astra`
xhigh: 0 actionable findings, не human/production acceptance.

Закрыт false-clean governance ClickHouse `COLUMNS('regex')` (verifier 32, check 18):
`asset.query_fields_unchecked` называет неперечисленные физические колонки, а соседнее
именованное чтение сохраняет собственный review. Проверка видит селектор в выражении и
вложенном запросе; область селектора — весь AST, включая непрочитанную CTE или
scalar `WITH`. PostgreSQL-семейство и chart impact не менялись. Выбор среза —
синтетическая оценка `gpt-6-astra` xhigh, итог — 0 actionable findings, не
human/production acceptance.

Девять offline-тестов ClickHouse alias read-through теперь ограничивают число Python-вызовов
парсера и reader'а вместо двух секунд wall-clock. Бюджет прерывает вычисление при превышении;
прежние SQL и семантические assertions сохранены. Production-код и версии evidence не
менялись. Моделированное ревью `gpt-6-astra` xhigh (2026-09-26): 0 actionable findings;
полный offline pytest — 2596 passed, 21 skipped, 38 deselected, Ruff и Black чистые.

Технически заблокировано до named engine capability (не повторять как локальный research-срез):

1. **PostgreSQL `dwh.cost.max_scanned_bytes`** — native API PostgreSQL 14–18 не даёт
   exact-query scanned-byte counter или byte ceiling. Возобновлять только с конкретным
   trusted provider/extension и его pin/contract; до этого `cost.not_measured` — правильный
   результат.

Отдельно не начинать без нового решения о trusted capture boundary:

1. **Подпись самим capture runner** — manifest signing не доказывает факт исполнения SQL;
   для этого private key должен быть недоступен вызывающей стороне и изолирован внутри
   trusted capture/execution boundary.

Scheduler, PostgreSQL platform runtime, REST/MCP, notifications и actions не начинать
автоматически: следующий срез выбирается после Astra-review. Production acceptance
и human/design-partner evidence модельным review не заявляются.

Журналы закрытых срезов — [typed investigation](history/TYPED_INVESTIGATION_MVP.md) и
[chart-level impact](history/CHART_LEVEL_IMPACT.md).
## Решения для текущего scope

1. Development gate — моделирование owner-review только через Astra xhigh;
   человеческое review для продолжения разработки не требуется, human evidence не заявляется.
2. До публикации fork остаётся без remote и использует текущую локальную package identity.
3. Release evidence поддерживает два честно различимых режима: совместимый
   `integrity=content_hashed` и opt-in `integrity=signed` с Ed25519 и out-of-band SPKI
   trust root по [ADR 0005](adr/0005-ed25519-evidence-trust-root.md).
4. Реализованный alpha-срез ограничен supplied canonical results и локальными
   plan/results/brief artifacts; continuous runtime и внешние actions не начинаются.
5. Дальнейшие срезы выбираются через Astra-review по ADR 0006; synthetic outcome
   не означает production-certified brief или разрешение внешних действий.

## Источники истины

1. Текущий Git status, код и [целевая архитектура](DECISIONOPS_ARCHITECTURE.md).
2. [ADR 0003](adr/0003-decisionops-additive-control-plane.md),
   [ADR 0004](adr/0004-synthetic-proxy-offline-investigation.md),
   [ADR 0005](adr/0005-ed25519-evidence-trust-root.md) и tracked pilot evidence, включая
   [live R3 package](pilot/2026-09-12-owner-review-live-r3/README.md).
3. Для baseline v0.5.0 — [ARCHITECTURE.md](ARCHITECTURE.md) и [CURRENT_STATE.md](CURRENT_STATE.md).
