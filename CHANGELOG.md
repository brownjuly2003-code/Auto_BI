# Changelog

Формат по [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — по [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Changed

- **`contracts.validate` отвечает на правила продукта — dimensions, status, governing
  policies — и на определении метрики в candidate.** Прежде запинненная метрика,
  которую candidate вернул в `draft` или отдал под governing policy, под которой
  продукт не верифицируется, проходила с advisory, хотя `decision validate` по
  контракту candidate отвечает review. Теперь ответ правила, которого нет на
  validated definition, — rule issue: `dimension.candidate_not_allowed` и
  `metric.candidate_retired` — block, `metric.candidate_draft` и
  `policy.candidate_mismatch` — review, `metric.candidate_deprecated` — advisory,
  независимо от того, держит ли base validated definition. Rule issues стоят после
  pin issues, detail называет их коды по каждому pin. `candidate_pins`, validation
  report, `decision validate`, остальные checks и состав run id прежние.
  Verifier 89 → 90, check 65 → 66; historical bundles прежние.

- **`contracts.validate` прежде пропускал другую definition метрики на запинненной
  версии как `contracts.clean`; теперь сравнивает её с validated-контрактом по
  изменениям semantic diff.** Когда base держит validated definition, изменение
  под review остаётся у `semantic.diff` и `impact.products`. Иначе
  `metric_ref.candidate_content_mismatch` — block или advisory по классификации
  diff; запись pin несёт `differences`. Правило версий, validation report,
  `decision validate`, остальные checks и состав run id прежние.
  Verifier 88 → 89, check 64 → 65; historical bundles прежние.

- **Investigation skills отвечают точно по supplied values при любой длине.**
  Прежде цифры после 28-го знака терялись (группа, идущая от
  `10000000000000000000000000000` к `10000000000000000000000000001`, отвечала
  дельтой `0`), ответ зависел от порядка строк и decimal context вызывающего потока;
  значение или окно freshness длиннее лимита преобразования integer/text обрывало
  `decision investigate` с unhandled error. При короткой caller precision freshness
  могла получить ложный `pass`. Теперь суммы и разности вычисляются точно, ranking
  идёт по точным значениям, отношения сохраняют шесть знаков, rounding —
  `HALF_EVEN`; outputs и digests не зависят от caller context, порядка строк или
  длины значения. `data_health` сравнивает точный возраст в целых микросекундах
  с окном и читает окно общим `duration_seconds`. План объявляет версию каждого
  skill, другая версия делает план невалидным, brief отказывается от output другой
  версии. Числовые skills — `skill_version` 2, `data_health` — 1; release-версии
  evidence прежние (verifier 88, check 64).

- **`assets.invalidated` резолвит metric pins каждого выбранного verified asset в candidate snapshot.**
  Другая версия, несколько контрактов или отсутствие метрики блокируют шаг с
  `asset.metric_ref_version_mismatch`, `asset.metric_ref_ambiguous`,
  `asset.metric_ref_absent`, где прежде asset сохранял clean. Findings pins стоят
  после семантического изменения и перед reviewed-запросом; base не сравнивается.
  Payload несёт `metric_pins`, clean detail считает совпавшие pins. Выбор assets,
  `contracts.validate`, `results.reconcile`, `results.provenance`, `decision validate`
  и not-run при непрочитанном контракте прежние.
  Verifier 87 → 88, check 63 → 64; historical bundles прежние.

- **ClickHouse own-grant строка без access type больше не считается отсутствием grant.**
  Missing key, NULL или пустой `access_type` означает неполный ответ: `readonly=1/2`,
  доказанный write grant и неполные enabled roles сохраняют evidence; иначе read-only
  не установлен, `dwh.access` блокируется, reach-вопросы и exact runtime SQL не
  отправляются. Column reach неизвестен при любом `readonly`: `column_reads=null`,
  `security.not_observed`, без ложных unreadable, physical absence и unenforced
  classification. Row-policy facts, typed answers, отказ grants-вопроса, вопросы
  и их порядок, PostgreSQL прежние. Колонка движка — не-nullable `Enum16`
  (ClickHouse 24.8.14.39, вживую): такая строка означает, что ответ доставлен
  не целиком.
  Verifier 86 → 87, check 62 → 63; historical bundles прежние.

- **PostgreSQL-family ядро без row-level security грейдится вместо ошибки на identity-вопросе.**
  Только после undefined column (`42703`) collector спрашивает `server_version_num`;
  ниже `90500` повторяет identity без `BYPASSRLS`, сохраняет правила роли и runtime
  SQL, не читает отсутствующие RLS-каталоги и записывает версию в facts. Clean
  `dwh.security` с покрытием называет ядро; без читаемых витрин его текст прежний.
  Живые факты — ядро PostgreSQL 9.4.26; ответившее на identity ядро, ClickHouse и
  остальные undefined-column случаи прежние.
  Verifier 85 → 86, check 61 → 62; historical bundles прежние.

- **`contracts.validate` резолвит metric pins продукта в candidate snapshot.**
  Ровно один контракт метрики на запинненной версии сохраняет clean; другая версия,
  несколько контрактов или отсутствие блокируют шаг с
  `metric_ref.candidate_version_mismatch`, `metric_ref.candidate_ambiguous`,
  `metric_ref.candidate_absent`. Base и метрики вне pins продукта не сравниваются;
  отсутствующий узел при `gap.contract_invalid` назван `not_compared`, найденный
  грейдится как обычно. Payload сохраняет validation issues и добавляет
  `candidate_pins`; detail считает совпавшие pins. `decision validate` прежний,
  содержимое контрактов одной версии и pins verified assets не сравниваются.
  Verifier 84 → 85, check 60 → 61; historical bundles прежние.

- **Текущая PostgreSQL-family роль без LOGIN больше не блокируется из-за отсутствующей строки в `pg_user`.**
  Identity-вопрос читает superuser-флаг из `pg_roles.rolsuper`, где есть каждая
  роль: прежний view отвечал NULL для NOLOGIN current role (PostgreSQL 16.15,
  вживую). Select-only роль грейдится по своим привилегиям и правам ролей,
  которые session user достигает через `SET ROLE`, и сохраняет eligible exact
  runtime SQL; NOLOGIN superuser получает прежнее superuser evidence и block
  без write-privilege вопросов и runtime SQL. BYPASSRLS, login-роли и правила
  отказа, пустого, частичного и NULL identity-ответа сохраняются.
  Verifier 83 → 84, check 59 → 60; historical bundles прежние.

- **Выданная, но не включённая ClickHouse-роль больше не может скрыть право записи.**
  Роль, выданная пользователю с `SET DEFAULT ROLE NONE`, отсутствует в
  `system.enabled_roles`, поэтому grants-вопрос её не видел, хотя `SET ROLE`
  (или HTTP-параметр `role`) пишет. После чистых собственных grants зонд задаёт
  один вопрос к `system.role_grants` — строка с `user_name` означает роль,
  выданную пользователю напрямую, строка с `role_name` — роль, выданную роли
  и включаемую вместе с ней на любую глубину; уже включённые роли исключены.
  Достижимые роли опрашиваются одним вопросом к `system.grants`: write access
  type даёт `read_only=false` с evidence `SET ROLE reaches …`, `dwh.access` —
  block, exact runtime SQL не исполняется. Отказ или неполный ответ любого из
  двух вопросов не подтверждает read-only; доказанный write grant сохраняет
  приоритет. `readonly=1/2`, отказ `system.grants`, неполные enabled roles и
  собственный write grant сохраняют факты, evidence и вопросы. Имя роли в
  литерале пишется с удвоенными обратной косой чертой и кавычкой: одиночная
  черта называет другую роль или никакую. Пользователю гейта, read-only по
  grants, нужен `SELECT` на `system.role_grants` помимо `system.grants`: без
  него `dwh.access` блокируется. Verifier 82 → 83, check 58 → 59;
  historical bundles прежние.

- **Объявленные join paths метрики сохраняются в canonical snapshot и именуются в semantic diff.**
  Пути отсортированы, одинаковые объявления сохраняются один раз; висячий endpoint —
  `gap.metric_join_dangling`, несколько cardinality одного пути — `gap.metric_join_conflict`,
  оба block. Удаление пути — `metric.join_path_removed` (block), смена cardinality —
  `metric.join_path_changed` (review), добавление — `metric.join_path_added` (advisory).
  Canonical digest каждого native snapshot с метриками перемещается (native projection
  version 2 → 3), поэтому verified assets и result attestations, привязанные к прежнему
  digest, получают `asset.stale_snapshot` / `provenance.other_snapshot` (review) до повторной
  верификации; snapshot без метрик сохраняет digest. Удалённый путь или смена его cardinality
  достигает verified assets метрики как `asset.metric_changed`; модельные joins сохраняют
  свои правила. Verifier 81 → 82, check 57 → 58; historical bundles прежние.

- **Объявленная freshness метрики сохраняется в canonical snapshot и именуется в semantic diff.**
  `max_age` сохраняется как объявлен, quality preconditions отсортированы без повторов.
  Ослабление окна или снятие precondition — `metric.freshness_relaxed` (review),
  ужесточение — `metric.freshness_tightened`, равное окно в другой записи —
  `metric.freshness_restated` (advisory). Canonical digest каждого native snapshot
  с метриками перемещается (native projection version 1 → 2), поэтому verified assets
  и result attestations, привязанные к его прежнему digest, получают
  `asset.stale_snapshot` / `provenance.other_snapshot` (review) до повторной
  верификации; snapshot без метрик сохраняет digest. Ослабленная freshness
  достигает verified assets метрики как `asset.metric_changed`, как и ослабленный
  потолок стоимости. `dwh.freshness` по-прежнему грейдит витрины против окна продукта.
  Окна сравниваются в точных целых секундах: очень длинное или записанное
  тысячами цифр объявленное окно больше не роняет gate в diff, freshness витрин
  и provenance stale-capture. Verifier 80 → 81, check 56 → 57; historical
  bundles прежние.

- **Поставляемый дашборд валидируется против candidate-модели до компиляции BI contract.**
  `bi.contract` использует тот же `validate_spec`, что и build path перед вызовом
  BI, с разрешённым операторским `raw_sql`. Spec с ошибками не компилируется
  и payload'ов не получает; проверка — block с `bi.spec_invalid`, evidence
  называет дашборд и ошибки валидатора в порядке поставки. Валидные дашборды
  сохраняют findings и payload digests, отсутствующий spec — `bi.spec_missing`;
  `impact.products` читает specs как прежде. Verifier 79 → 80, check 55 → 56;
  historical bundles прежние.

- **Роль, достижимая через `SET ROLE`, больше не может скрыть право записи.**
  PostgreSQL-family role probe одним вопросом находит роли, в которые session user
  может переключиться (PostgreSQL 16 — опция `SET`, раньше и в Greenplum/Greengage —
  членство), включая самого session user за role default; superuser-роли остаются
  в вопросе и при наследовании их текущей ролью, потому что членство не передаёт
  атрибут `SUPERUSER`. Одним вопросом на витрину спрашиваются их табличные
  и колоночные write-права. Достижимая пишущая роль, достижимый superuser,
  имя без значения или неотвеченный вопрос не подтверждают read-only:
  `dwh.access` — block с `access.role_writable`, exact runtime SQL
  не исполняется; доказанные grants сохраняют приоритет. Чистое read-only evidence
  называет проверенные достижимые роли или сообщает, что `SET ROLE` других ролей
  не достигает; identity-вопрос с его короткими путями и ClickHouse-коллекция
  без изменений. Verifier 78 → 79, check 54 → 55; historical bundles прежние.

- **Отсутствующая readable-витрина больше не даёт `freshness.fresh`.**
  Если нет существующей читаемой витрины, доступной для датировки, чистый код —
  `freshness.not_run`, и проверка появляется в `What was not done`.
  Detail сообщает, что возраст не сравнивался. Частичное ненулевое покрытие,
  findings, status и verdict прежние. Verifier 77 → 78, check 53 → 54.

- **Отсутствие security-наблюдений больше не даёт `security.unfiltered`.**
  При нуле подтверждений чтения запрошенных колонок и ответов о политиках чистый
  код — `security.not_run`, и проверка появляется в `What was not done`.
  Частичное ненулевое покрытие сохраняет `security.unfiltered`; пустой список
  политик считается ответом. Findings, status, verdict и detail прежние.
  Verifier 76 → 77, check 52 → 53; historical bundles прежние.

- **Отсутствие физических сравнений больше не даёт `physical.as_declared`.**
  При нуле сравнённых объявленных типов и свойств layout чистый результат —
  `physical.not_run`, и проверка появляется в `What was not done`.
  Частичное ненулевое покрытие сохраняет `physical.as_declared`; findings,
  status, verdict и detail прежние. Verifier 75 → 76, check 51 → 52.

- **Непроверенные cost-факты больше не дают `cost.within_budget`.**
  Если все cost-вопросы пропущены, чистый результат — `cost.not_run`,
  и проверка появляется в `What was not done`. Для положительного кода
  требуется фактическое сравнение хотя бы одного объявленного потолка.
  Findings, status, verdict и detail прежние. Verifier 74 → 75, check 50 → 51;
  исторические bundles не переписаны.

- **`dwh.cost` сообщает фактически проверенные потолки.**
  Чистая сводка раздельно считает лимиты строк из планов и лимиты байтов/времени
  из exact-query runtime evidence относительно всех объявленных потолков.
  Недоступные, неатрибутированные и закрытые deny-all вопросы не увеличивают
  покрытие. Неполное и нулевое покрытие названы явно; положительный вывод ограничен
  проверенными лимитами. Findings, summary codes и verdict прежние.
  Verifier 73 → 74, check 50 без изменений; historical bundles прежние.

- **`dwh.security` сообщает фактически наблюдённое покрытие.**
  Чистая сводка считает явные подтверждения чтения запрошенных колонок и ответы
  о row policies по вопросам (витрина, колонка времени). Частичный каталог или
  недоступная часть скоупа больше не подтверждают чтение всех колонок; неполное
  и нулевое покрытие названы явно. Findings, summary codes и verdict прежние.
  Verifier 72 → 73, check 50 без изменений; historical bundles прежние.

- **`dwh.freshness` ограничивает чистую сводку выполненными сравнениями.**
  Покрытие считается по вопросам (витрина, колонка времени): отсутствующие,
  нечитаемые, неатрибутированные и закрытые row security вопросы не подтверждают
  свежесть. Сводка сообщает число сравнений и полное/неполное покрытие; при нуле
  не утверждает свежесть. Findings, summary codes и verdict прежние.
  Verifier 71 → 72, check 50 без изменений; исторические bundles не переписаны.

- **`dwh.physical` сообщает фактическое покрытие сравнения.**
  Чистая сводка считает отвеченные типы колонок и заявленные свойства layout
  по каждому вопросу (витрина, колонка времени). Неатрибутированные, отсутствующие
  и нечитаемые витрины, неизвестные типы и NULL layout не подтверждают совпадение;
  неполное и нулевое покрытие названы явно. Findings, summary codes и verdict прежние.
  Verifier 70 → 71, check 50 без изменений; исторические bundles не переписаны.

- **ClickHouse распознаёт составные write grants и `ALL`.**
  Canonical `ALTER UPDATE`, `ALTER DELETE`, `CREATE TABLE`, `DROP TABLE` и прочие
  `ALTER`/`CREATE`/`DROP` с границей-пробелом теперь блокируют grant-based read-only
  и runtime SQL; `ALL` также включает права записи. Evidence сохраняет точные
  названия. Профили readonly=1/2 и partial revokes прежние. Версии evidence
  69 → 70 / 49 → 50; исторические bundles не переписаны.

- **Неполный ClickHouse enabled-roles ответ не подтверждает эффективный доступ.**
  Отказ, missing/NULL/пустое имя роли без профиля `readonly=1/2` блокируют grant-based
  read-only и runtime SQL. Известные grants сохраняются; column/policy reach остаётся
  неизвестным с `security.not_observed`, без ложного отсутствия скрытой колонки
  или утверждения об отсутствии защиты классификации. Известный пустой список ролей
  и пустой policy catalog различаются с неизвестными. Версии evidence 68 → 69 / 48 → 49;
  исторические bundles не переписаны.

- **Частичные write-privilege ответы PostgreSQL блокируют доступ.**
  Отсутствующее/NULL поле табличных INSERT/UPDATE/DELETE/TRUNCATE или колоночных
  INSERT/UPDATE не подтверждает read-only: `dwh.access` — BLOCK, exact runtime SQL
  не выполняется. Evidence называет неизвестные права и витрину; известные grants
  сохраняют приоритет и не дублируются. SQL-вопросы, identity и ClickHouse прежние.
  `VERIFIER_VERSION` 67 → 68, `CHECK_VERSION` 47 → 48;
  исторические bundles не переписаны.

- **Частичный identity-ответ PostgreSQL блокирует доступ.**
  Отсутствующее/NULL identity-поле или пустое имя не подтверждают read-only:
  `dwh.access` — BLOCK; write-privilege вопросы и exact runtime SELECT не выполняются.
  Известное имя и доказанный superuser/BYPASSRLS сохраняются; обход RLS — false
  только при двух известных отрицательных флагах, иначе null. Evidence называет
  неотвеченные поля. Доказанный superuser сохраняет прежнее writable evidence даже
  при неполной строке. Имя роли и subject refs сохраняют значимые пробелы quoted-имён.
  SQL-вопросы и общий boolean-normalizer не менялись.
  `VERIFIER_VERSION` 66 → 67, `CHECK_VERSION` 46 → 47;
  исторические bundles не переписаны.

- **Неотвеченный identity-вопрос PostgreSQL блокирует доступ.**
  Отказ или пустой ответ о роли не подтверждает read-only: роль — `unknown`,
  superuser и `BYPASSRLS` не установлены, `bypasses_row_security=null`.
  `dwh.access` — BLOCK; write-privilege вопросы и exact runtime SELECT не выполняются.
  Finding сообщает о неподтверждённом read-only, без утверждения, что неизвестная
  роль умеет писать. Ответившие роли и ClickHouse сохраняют свои правила;
  ошибки соединения продолжают пробрасываться. `VERIFIER_VERSION` 65 → 66,
  `CHECK_VERSION` 45 → 46; исторические bundles не переписаны.

- **Неотвеченный табличный write-privilege вопрос PostgreSQL блокирует доступ.**
  Отказ или пустой ответ о INSERT/UPDATE/DELETE/TRUNCATE не подтверждает read-only,
  даже если колоночные INSERT/UPDATE отрицательны. `dwh.access` — BLOCK,
  exact runtime SELECT не выполняется; evidence называет неотвеченные права
  и витрину. Колоночный вопрос продолжается, доказанный grant сохраняет evidence.
  Набор SQL, superuser и ClickHouse сохраняются. `VERIFIER_VERSION` 64 → 65,
  `CHECK_VERSION` 44 → 45; исторические bundles не переписаны.

- **Колоночный write-grant PostgreSQL-роли делает `dwh.access` BLOCK.**
  Probe спрашивает `has_any_column_privilege` для INSERT/UPDATE по всей витрине
  скоупа, включая колонки вне metric-чтений; evidence называет привилегию, колонки
  и витрину. Табличный grant сохраняет одну запись, read-only evidence называет
  обе группы проверенных прав. Exact runtime SQL через такую роль не исполняется;
  неотвеченный колоночный privilege-вопрос также блокирует доступ с evidence о
  неподтверждённом read-only. Superuser и ClickHouse сохраняют свои правила.
  `VERIFIER_VERSION` 63 → 64, `CHECK_VERSION` 43 → 44;
  исторические bundles не переписаны.

- **Нечисловое значение в reconciliation сравнивается по виду.**
  Бесконечности одного знака равны при разных записях и float за пределом binary64;
  float-допуск к ним не применяется. NaN кандидата против числа или бесконечности —
  `value.changed`; ожидаемый NaN, включая decimal `sNaN`, — `value.expected_nan`
  и `insufficient` / REVIEW, если сравнение не доказало различие в другом месте.
  Проверка порядка не скрывается ожидаемым NaN; ячейка любого business key решается
  сопоставлением строки. NULL, идентичность ключей и агрегатные проверки сохраняют
  свои правила. `VERIFIER_VERSION` 62 → 63, `CHECK_VERSION` 42 → 43;
  исторические bundles не переписаны.

- **Float reconciliation сравнивает точную binary64-разность с точным decimal-допуском.**
  Absolute и relative bounds больше не пропускают превышение, скрытое погрешностью
  binary64-арифметики; точное попадание в relative-допуск больше не отвергается
  из-за приближённого отношения.
  Попадание в любой объявленный bound даёт `equivalent`, выход за оба — `value.changed`.
  Absolute-допуск за пределом binary64 больше не делает бесконечность равной конечному
  значению или бесконечности другого знака: прежде такой допуск сам становился бесконечностью.
  `VERIFIER_VERSION` 61 → 62, `CHECK_VERSION` 41 → 42; исторические bundles не переписаны.

- **Environment-чтение отвечает своему вопросу (витрина, event-time колонка).**
  Коллекторы записывают `event_time_column` на всех ветках; access, freshness,
  physical, security и cost, включая runtime probe, используют своё чтение.
  Порядок больше не скрывает stale clock или denied column и не делает свежие часы
  протухшими. Без однозначной атрибуции — `access.reading_unattributed` (review);
  старое чтение без колонки подходит только единственным часам с единственным чтением.
  `freshness.stale` называет часы, evidence facts сохраняют их колонку.
  `VERIFIER_VERSION` 60 → 61, `CHECK_VERSION` 40 → 41; исторические bundles не переписаны.

- **Reconciliation отождествляет integer/decimal ключи и агрегатные группы по числу.**
  Ключ на другом scale теперь находит свою строку вместо `row.missing` / `row.added`;
  две записи одного числа в result дают `insufficient` / `key.not_unique` вместо
  сопоставления по строке и возможного ложного pass. `share_sum` и
  `partition_conservation` объединяют `group_by` на разных scales в одну группу.
  NULL, non-finite, нечисловые значения и ключи другого типа сохраняют записанную форму;
  детали называют ключ так, как его записал свой result; `conflicting totals`
  перечисляет totals числами, без `Decimal(...)`. ResultSet не меняются.
  `VERIFIER_VERSION` 59 → 60, `CHECK_VERSION` 39 → 40; исторические bundles не переписаны.

- **Reconciliation читает колонки supplied result по имени после schema step.**
  Перестановка с ожидаемыми named values, включая перенос business key, больше
  не блокирует эквивалентный результат; changed named values больше не получают
  ложный pass и sealed `result.equivalent` claim. Контрактный порядок строк и
  failed aggregate check используют собственный ключ каждого result; NULL и
  float tolerance остаются у своей колонки. ResultSet не меняются, native capture
  сохраняет порядок asset. `VERIFIER_VERSION` 58 → 59, `CHECK_VERSION` 38 → 39;
  исторические bundles не переписаны.

- **CTE исключает физическую таблицу только внутри объявившего её запроса.**
  Вложенная или соседняя CTE больше не скрывает внешнюю таблицу от governance
  и raw-SQL RBAC и не освобождает bare-имя от denylist SQL guard: пропущенный
  `asset.query_ungoverned` (review) возвращается. Внешний `WITH`, включая обе
  ветки своего `UNION`, сохраняет исключение; whole-AST extraction, сравнение
  имён и прежние same-WITH, recursive и scalar правила сохраняются.
  `VERIFIER_VERSION` 57 → 58, `CHECK_VERSION` 37 → 38.

- **ClickHouse исключает CTE из физических таблиц только по точному регистру.**
  `WITH A … FROM a` больше не скрывает физическую таблицу от governance и
  raw-SQL RBAC и не обходит denylist SQL guard: пропущенный
  `asset.query_ungoverned` (review) возвращается. Точное совпадение имён,
  whole-AST extraction, lexical resolution и PostgreSQL сохраняют правила.
  `VERIFIER_VERSION` 56 → 57, `CHECK_VERSION` 36 → 37.

- **ClickHouse governance учитывает достижимость ключей `JOIN … USING`.**
  Непрочитанная named CTE или scalar `WITH` больше не добавляет ключи в причину
  `asset.query_fields_unchecked`; при соседнем живом JOIN остаются только его
  ключи. Используется общий с chart impact обход гейтов CTE. Общий extractor
  сохраняет whole-AST `join_using_keys`, именованные чтения и PostgreSQL — прежние
  правила. `VERIFIER_VERSION` 55 → 56, `CHECK_VERSION` 35 → 36.

- **ClickHouse governance учитывает достижимость `COLUMNS(...)`.** Селектор в
  непрочитанной named CTE или scalar `WITH` больше не создаёт причину
  `it selects COLUMNS(...)` в `asset.query_fields_unchecked`. Живой селектор
  сохраняет advisory; regex не раскрывается по snapshot-полям. Общий extractor
  сохраняет whole-AST `column_selectors`, а governance использует общий с
  chart impact обход гейтов CTE. `VERIFIER_VERSION` 54 → 55,
  `CHECK_VERSION` 34 → 35.

- **ClickHouse связывает достижимые CTE и scalar `WITH` по точному регистру.**
  Внутреннее `a` больше не скрывает внешнее `A`, а ссылка на `a` не открывает
  непрочитанное соседнее `A`: пропущенный star advisory возвращается, ложный
  исчезает. Chart impact применяет то же правило к звёздам, `COLUMNS`,
  `JOIN … USING`, dictionary- и Join-table-вызовам, включая named CTE в `IN`.
  Точное затенение, self-reference, дубликаты, квалификаторы и `SETTINGS`,
  явные table/field findings и другие причины advisory сохраняют правила.
  `VERIFIER_VERSION` 53 → 54, `CHECK_VERSION` 33 → 34.

- **ClickHouse governance учитывает достижимость звезды.** Звезда в непрочитанной
  named CTE или scalar `WITH` больше не создаёт причину `it selects *` в
  `asset.query_fields_unchecked`. Живые звёзды и `IN <table>` сохраняют advisory;
  обычный `COUNT(*)` остаётся счётчиком строк. Governance использует общий с
  chart impact обход CTE без изменения его алгоритма. Общий extractor,
  именованные чтения, неизвестные таблицы и поля, `COLUMNS`, `JOIN … USING` и
  PostgreSQL сохраняют прежние правила. `VERIFIER_VERSION` 52 → 53,
  `CHECK_VERSION` 32 → 33. Полный offline pytest: 3037 passed, 21 skipped,
  38 deselected; strict mypy, Ruff и Black чистые. Синтетическое Astra xhigh
  review: 0 actionable P1/P2; живой ClickHouse не запускался,
  не human/production acceptance.

- **PostgreSQL CAST сохраняет известное имя выхода при сортировке.**
  `CAST(expr AS type)` и `expr::type`, включая вложенные casts, сохраняют
  сильное имя колонки, `COUNT`/`SUM`/`AVG`/`MIN`/`MAX` или оконного `ROW_NUMBER`
  для целого `ORDER BY`/`DISTINCT ON` и выхода левой ветки set operation.
  Чтения аргумента, `FILTER` и оконных выражений остаются; `AS`, унарный `+`,
  `GROUP BY` и ClickHouse сохраняют границы. Функции, нормализованные sqlglot
  в `Cast`, не наследуют имя аргумента и не прячут чтение сортировки.
  `VERIFIER_VERSION` 51 → 52, `CHECK_VERSION` 31 → 32.

- **PostgreSQL оконный `ROW_NUMBER` даёт неявное имя при сортировке.**
  `ROW_NUMBER() OVER (…)` без alias разрешает целое имя `row_number` в
  `ORDER BY`/`DISTINCT ON` и выход левой ветки set operation без ложного
  field review или sourceless advisory. `PARTITION BY` и внутренний `ORDER BY`
  остаются чтениями; bare `row_number()` и другие оконные функции сохраняют
  прежние правила.
  `VERIFIER_VERSION` 50 → 51, `CHECK_VERSION` 30 → 31.

- **PostgreSQL оконные агрегаты сохраняют неявное имя при сортировке.**
  `COUNT`, `SUM`, `AVG`, `MIN` и `MAX` с `OVER`, включая `FILTER (WHERE …)`
  внутри оконного вызова, разрешают целое имя в `ORDER BY`/`DISTINCT ON`
  и выход левой ветки set operation без ложного field review или sourceless
  advisory. Аргументы, предикат `FILTER`, `PARTITION BY` и внутренний
  `ORDER BY` остаются чтениями.
  `VERIFIER_VERSION` 49 → 50, `CHECK_VERSION` 29 → 30.

- **Пустой ClickHouse `map()` больше не считается шаблоном Superset.**
  В operator SQL исчезает ложный `chart.sql_partial`; настоящий `{{ … }}`
  остаётся частично прочитанным, в том числе рядом с `map()`. Явные чтения
  колонок сохраняются. Меняется chart evidence, грейдинг проверок остаётся
  прежним. `VERIFIER_VERSION` 48 → 49, `CHECK_VERSION` остаётся 29.

- **PostgreSQL агрегаты с `FILTER` сохраняют неявное имя при сортировке.**
  `COUNT`, `SUM`, `AVG`, `MIN` и `MAX` с `FILTER (WHERE …)` разрешают целое
  имя в `ORDER BY`/`DISTINCT ON` и выход левой ветки set operation без ложного
  field review или advisory. Чтения аргумента и предиката сохраняются;
  `AS`, квотированный регистр, унарный `+`, выражения, квалифицированные имена
  и `GROUP BY` остаются на прежних границах. `VERIFIER_VERSION` 47 → 48,
  `CHECK_VERSION` 28 → 29. Выбор среза — синтетическое Astra xhigh review;
  реализация — Luna xhigh. Полный offline pytest: 2937 passed, 21 skipped,
  38 deselected; strict mypy, Ruff и Black чистые. Итоговое синтетическое
  Astra xhigh review: 0 actionable P1/P2; живой PostgreSQL не запускался,
  не human/production acceptance.

- **PostgreSQL `AVG`/`MIN`/`MAX` дают неявные имена при сортировке.**
  Прямые агрегаты без alias разрешают целое имя в `ORDER BY`/`DISTINCT ON`
  и выход левой ветки set operation без ложного field review или advisory.
  Чтения аргументов остаются; квотированный регистр, `AS`, унарный `+`,
  выражения, квалифицированные имена и `GROUP BY` сохраняют границы.
  `VERIFIER_VERSION` 46 → 47, `CHECK_VERSION` 27 → 28.
  Red-green: 18 падений, затем 137 passed. Полный offline pytest:
  2893 passed, 21 skipped, 38 deselected; strict mypy, Ruff и Black чистые.
  Синтетическое Astra xhigh review: 0 actionable P1/P2;
  live PostgreSQL не запускался, не human/production acceptance.

- **PostgreSQL алиас таблицы со списком колонок сохраняет advisory.**
  Ссылки на объявленные выходные имена таблицы или скобочного `JOIN` оставляют
  `asset.query_fields_unchecked`: snapshot не доказывает физический ordinal
  lineage. Ложный field review или чистый результат сменяется advisory;
  соседние физические поля и имена колонок таблицы вне списка проверяются по прежним
  правилам. `ORDER BY` сохраняет приоритет выхода SELECT, `GROUP BY` внутри
  LATERAL — локального выхода. `VERIFIER_VERSION` 45 → 46, `CHECK_VERSION` 26 → 27.

- **PostgreSQL `NATURAL JOIN` сообщает о неявных ключах.**
  Governance оставляет `asset.query_fields_unchecked`: ключи выбираются по
  совпадающим выходным колонкам источников, могут зависеть от физической схемы
  и не выводятся из semantic snapshot. Явные соседние чтения продолжают
  проверяться; вложенные join учитываются.
  `VERIFIER_VERSION` 44 → 45, `CHECK_VERSION` 25 → 26.

- **PostgreSQL `SUM(...)` даёт неявное имя `sum` при сортировке.**
  Целый `ORDER BY`/`DISTINCT ON` и сортировка set operation по левой ветке
  используют выход `SUM`, не создавая ложное чтение поля или advisory.
  Аргументы продолжают читаться; явный `AS`, квотированный регистр и унарный `+`
  сохраняют границы. Проверено на PostgreSQL 16 на Mac.
  `VERIFIER_VERSION` 43 → 44, `CHECK_VERSION` 24 → 25.

- **PostgreSQL `COUNT(...)` даёт неявное имя `count` при сортировке.**
  Целый `ORDER BY`/`DISTINCT ON` и сортировка set operation по левой ветке
  используют выход `COUNT`, не создавая ложное чтение физического поля или
  sourceless advisory. Аргументы `COUNT` сохраняют реальные чтения; явный `AS`
  и унарный `+` сохраняют приоритет; `"Count"(...)` сохраняет регистр выходного
  имени. Проверено на PostgreSQL 16.15 на Mac.
  `VERIFIER_VERSION` 42 → 43, `CHECK_VERSION` 23 → 24 из-за изменившихся
  governance findings.

- **Корневые `INTERSECT` и `EXCEPT` проходят SQL guard.**
  PostgreSQL и ClickHouse читают обе ветки для governance; PostgreSQL допускает
  также скобочную форму с внешними `ORDER BY`, `LIMIT`, `OFFSET`.
  `SELECT INTO` и remote table functions внутри веток отклоняются. Подзапросы в
  общих `LIMIT`/`OFFSET` теперь учитываются и у ранее допустимого `UNION`.
  Mac-проверка подтвердила синтаксис обеих операций в PostgreSQL 16.15 и
  ClickHouse 24.8.14.39. `VERIFIER_VERSION` 41 → 42: operator SQL теперь может
  заменить `chart.opaque_sql` на `reads_field`. `CHECK_VERSION` 22 → 23:
  исправленное чтение `UNION` и отказ на ранее допустимый `SELECT INTO` меняют
  смысл старых CheckResult.

- **Корневой PostgreSQL `SELECT` в скобках проходит guard.**
  `(SELECT …) ORDER BY …` сохраняет чтения из сортировки, а также внешних
  `LIMIT` и `OFFSET` для governance. Неподдержанные и повторные модификаторы
  остаются отказом; корневые `INTERSECT`/`EXCEPT`, `VALUES` и ClickHouse не расширены.
  Evidence versions 41/22 остаются: ранее такой reviewed-запрос не проходил
  границу контракта, поэтому старых CheckResult для него нет. Реализовано Luna xhigh;
  синтетическое Astra xhigh review: 0 actionable P1/P2, не human/production
  acceptance. Полный offline pytest: 2774 passed, 21 skipped, 38 deselected;
  strict mypy, Ruff и Black чистые.

- **Скобочный ClickHouse `ARRAY JOIN` читается как выражение.**
  `ARRAY JOIN (tags AS t)` читает `tags`, но не псевдоколонку `t`;
  `ARRAY JOIN (tags AS t) AS a` объявляет только внешний `a`, поэтому отдельное `t`
  может добавить governance review. Настоящий скобочный SELECT сохраняет свой scope.
  Поиск таблиц и chart impact применяют то же правило при `IN <name>`: валидный
  reviewed SQL больше не обрывает проверку и не превращается в `chart.opaque_sql`.
  `VERIFIER_VERSION` 40 → 41, `CHECK_VERSION` 21 → 22.
  Реализовано как Codex companion Luna xhigh; синтетическое Astra xhigh review:
  0 actionable P1/P2, не human/production acceptance. Целевой прогон T-25:
  303 passed; полный offline pytest: 2751 passed, 21 skipped, 38 deselected.
  Strict mypy: 0 ошибок в 120 модулях, Ruff и Black чистые.

- **Глубокий reviewed SQL получает отказ guard вместо аварии парсера.**
  `_parse_one_select` переводит `RecursionError` от sqlglot в `SQLGuardError`, поэтому
  `guard_sql`, `ReferenceQuery` и загрузка verified assets сообщают о превышении глубины
  вложенности через существующий путь отказа. Читаемые запросы и chart impact не меняются;
  `VERIFIER_VERSION=40`, `CHECK_VERSION=21` остаются без изменений. Реализовано как Codex
  companion Luna xhigh. Целевой прогон T-26: 190 passed, включая docs-as-code;
  полный offline pytest: 2745 passed, 21 skipped, 38 deselected. Strict mypy:
  0 ошибок в 120 модулях, Ruff и Black чистые.

- **PostgreSQL `ORDER BY` и `DISTINCT ON` разрешают имена по правилам движка.**
  Целое голое имя называет выход SELECT с учётом PostgreSQL-сворачивания регистра;
  имя внутри выражения читает input-колонку и может добавить review или advisory.
  `ORDER BY` set operation называет выход левой ветки без ложного sourceless advisory;
  корневые `INTERSECT` и `EXCEPT` guard по-прежнему отклоняет, для них правило действует
  во вложенном запросе.
  Оконные, `WITHIN GROUP` и агрегатные сортировки, остальные формы `GROUP BY`,
  ClickHouse и chart impact сохраняют прежние чтения. `VERIFIER_VERSION` 39 → 40,
  `CHECK_VERSION` 20 → 21.
  Унарный `+` оставляет имя input-выражением и не создаёт имя выхода у неалиасированной
  проекции; сортировка после скобочного запроса читается внутри него, а `(column)`
  сохраняет имя выходного столбца.
  Реализовано `gpt-6-sol` high через MyFlow-кандидат `sol-high`; полный offline pytest:
  2737 passed, 20 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях, Ruff и
  Black чистые. Правила сверены вживую на PostgreSQL 16.15 (одноразовый кластер на Mac);
  ClickHouse 24.8.14.39 форму `(SELECT …) ORDER BY …` отвергает синтаксически.
  Синтетическое ревью `gpt-6-astra` xhigh через MyFlow: проход 1 — три находки (унарный
  `+`, сортировка после скобочного запроса, `(column)`) исправлены, проход 2 — F-T-24-7
  (`+dm.stores.id`) исправлена, проход 3 — 0 findings; не human/production acceptance.

- **PostgreSQL `GROUP BY` разрешает целое имя с учётом выходного столбца SELECT.**
  Локальная FROM-колонка выигрывает; если модель не объявляет её, возможный выходной
  столбец оставляет `asset.query_fields_unchecked` вместо ложного
  `asset.query_ungoverned_field`. Без локального источника остатка нет. Агрегатные и
  оконные выходы, выражения и квалифицированные имена сохраняют прежнее чтение; ClickHouse
  и chart impact не менялись. `VERIFIER_VERSION` 38 → 39, `CHECK_VERSION` 19 → 20.
  Реализовано `gpt-6-sol` high через MyFlow-кандидат `sol-high`; полный offline pytest:
  2684 passed, 20 skipped, 38 deselected; strict mypy: 0 ошибок в 120 модулях, Ruff и
  Black чистые. Правило сверено вживую на PostgreSQL 16.15 (одноразовый кластер на Mac).
  Синтетическое ревью `gpt-6-astra` xhigh через MyFlow: F-T-23-1 (имя `GROUP BY` в LATERAL
  без собственного FROM) исправлена, второй проход — 0 findings; не human/production
  acceptance.

- **`JOIN … USING` в governance reviewed SQL теперь оставляет advisory о ключе.**
  На ClickHouse и PostgreSQL-семействе общий ключ нельзя приписать одной таблице, поэтому
  он получает `asset.query_fields_unchecked`, а явные соседние колонки грейдятся отдельно.
  `JOIN … ON` не менялся. `VERIFIER_VERSION` 37 → 38, `CHECK_VERSION` 18 → 19.
  Реализовано `gpt-5.6-luna` xhigh; финальное синтетическое review `gpt-6-astra` xhigh:
  0 actionable P1/P2, не human/production acceptance. Полный offline pytest: 2655 passed,
  21 skipped, 38 deselected; docs-as-code: 24 passed; strict mypy: 0 ошибок в 120 модулях,
  Ruff и Black чистые.

- **Трёхчастная колонка больше не открывает одноимённый scalar `WITH` при точном
  совпадении с табличным источником.** `dm.sales_daily.revenue` читает единственный
  неалиасированный `FROM`/`JOIN dm.sales_daily` своего SELECT, а не скрытое
  `WITH … AS dm`. Дубликаты, конфликтующие имена и catalog-источники остаются
  conservative. `VERIFIER_VERSION` 36 → 37, `CHECK_VERSION` остаётся 18.
  Синтетическое review `gpt-6-astra` xhigh: 3 P2 закрыты red-green тестами, не
  human/production acceptance. Полный offline pytest: 2647 passed, 21 skipped,
  38 deselected; docs-as-code: 24 passed; strict mypy чистый, Ruff и Black чистые.

- **`t.region` при однозначном явном алиасе именованного табличного источника
  своего SELECT больше не открывает scalar `WITH t`.** `FROM`/`JOIN … AS t`
  направляет колонку к таблице: ложные chart reads и `chart.sql_partial` исчезают,
  а tuple `t.1` и отдельное чтение scalar alias сохраняются.
  `VERIFIER_VERSION` 35 → 36, `CHECK_VERSION` остаётся 18. Итоговое синтетическое
  `gpt-6-astra` xhigh review: 0 actionable findings после закрытия P2; не
  human/production acceptance. Полный offline pytest: 2641 passed, 21 skipped,
  38 deselected; chart-impact suite: 260 passed; docs-as-code: 24 passed; strict mypy:
  0 ошибок в 120 модулях, Ruff и Black чистые.

- **Однозначная ссылка на named CTE связывается с ближайшим объявлением.** Внутренняя
  CTE больше не открывает одноимённую внешнюю и не добавляет ложные chart reads или
  `chart.sql_partial`; `FROM` и bare `IN <name>` следуют одному правилу, а отдельная
  внешняя ссылка сохраняет реальный гэп. Self-reference и одноуровневые дубликаты
  остаются conservative. `VERIFIER_VERSION` 34 → 35, `CHECK_VERSION` остаётся 18.
  Полный offline pytest до трёх дополнительных граничных тестов: 2628 passed, 21 skipped,
  38 deselected; после них chart-impact suite: 179 passed; strict mypy: 0 ошибок в 120
  модулях. Синтетическое ревью `gpt-6-astra` xhigh: дефектов кода нет, один пробел
  покрытия закрыт тестами; не human/production acceptance.

- **Chart impact учитывает достижимость `JOIN … USING`.** Ключ JOIN в непрочитанной
  named CTE или scalar `WITH` больше не создаёт ложный `chart.sql_partial`; открытая
  ссылка сохраняет гэп. `VERIFIER_VERSION` 33 → 34, `CHECK_VERSION` остаётся 18. Полный
  offline pytest: 2623 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120
  модулях, Ruff и Black чистые. Синтетическое ревью `gpt-6-astra` xhigh:
  0 actionable findings, не human/production acceptance.

- **Chart impact учитывает достижимость ClickHouse `COLUMNS('regex')`.** Селектор в
  непрочитанной named CTE или scalar `WITH` больше не создаёт ложный `chart.sql_partial`;
  ссылка из исполняемой части сохраняет гэп. Governance reviewed asset SQL продолжает
  проверять весь AST. `VERIFIER_VERSION` 32 → 33, `CHECK_VERSION` остаётся 18. Полный
  offline pytest: 2618 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120
  модулях, Ruff и Black чистые. Синтетическое ревью `gpt-6-astra` xhigh:
  0 actionable findings, не human/production acceptance.

- **Восстановлен package-wide strict mypy для SQL AST.** Аннотации sqlglot `Expr` и
  `Selectable`, nullable cache/scope и типы коллекций приведены к фактическим контрактам;
  SQL-поведение и версии evidence не менялись. Strict mypy: 0 ошибок в 120 модулях; полный
  offline pytest: 2603 passed, 21 skipped, 38 deselected. Синтетическое ревью `gpt-6-astra`
  xhigh: 0 actionable findings, не human/production acceptance.

- **Offline-тесты ClickHouse alias read-through ограничивают объём работы.** Девять
  проверок с порогом в две секунды заменены бюджетом Python-вызовов парсера и reader'а:
  превышение прерывает вычисление, прежние SQL и семантические assertions сохранены.
  Это убирает зависимость гейта от загрузки хоста; C-операции и elapsed time бюджет не
  измеряет. Версии verifier/check не менялись. Моделированное ревью `gpt-6-astra` xhigh
  (2026-09-26): 0 actionable findings; offline pytest — 2596 passed, 21 skipped,
  38 deselected, Ruff и Black чистые.

- **PostgreSQL `max_scanned_bytes` теперь явно capability-gated.** Контракты native API
  PostgreSQL 14–18 не дают ни per-query scanned-byte counter, ни engine byte ceiling:
  `EXPLAIN (ANALYZE, BUFFERS)` и `pg_stat_get_xact_blocks_fetched` считают обращения к
  блокам, `pg_stat_io` — I/O/cache events, а PostgreSQL 18 `pg_stat_get_backend_io` лишь
  сужает те же I/O-счётчики до backend. Эти величины не переименовываются в scanned bytes;
  до выбора trusted provider/extension сохраняется `cost.not_measured`. Runtime-поведение
  не менялось, поэтому версии verifier/check не повышались.

### Added

- **ClickHouse `COLUMNS('regex')` оставляет честный остаток field governance**
  (`VERIFIER_VERSION` 31 → 32, `CHECK_VERSION` 17 → 18). Селектор выбирает физические
  колонки, которые reviewed SQL не перечисляет, поэтому `assets.invalidated` добавляет
  advisory `asset.query_fields_unchecked` с причиной про `COLUMNS(...)`. Именованные поля
  рядом по-прежнему грейдятся, PostgreSQL-семейство и chart impact не меняются. Правило
  применяется ко всему AST, как и текущая проверка `*`; достижимость непрочитанных CTE и
  scalar `WITH` остаётся отдельной задачей. Выбор среза — синтетическая оценка
  `gpt-6-astra` xhigh: 0 actionable findings, не human/production acceptance. Полный
  offline pytest: 2612 passed, 21 skipped, 38 deselected; strict mypy: 0 ошибок в 120
  модулях, Ruff и Black чистые.

- **Однозначная ссылка на scalar `WITH` связывается с ближайшим объявлением** (`VERIFIER_VERSION`
  30 → 31, `CHECK_VERSION` остаётся 17). Вложенный `WITH 1 AS a` больше не открывает внешнее
  чтение `dictGet`, `joinGet` или `*`, когда внутреннее объявление однозначно его скрывает;
  self-reference, дубликаты scalar-алиасов на одном уровне и shadowing именованных CTE остаются
  conservative и могут пере-сообщать. Выбор среза помечен как synthetic/modelled review
  `gpt-6-astra` xhigh: это не human evidence и не production/design-partner acceptance.

- **`COUNT(*)` не читает колонки, а `COUNT(DISTINCT *)` читает все** (`CHECK_VERSION`
  16 → 17, `VERIFIER_VERSION` 29 → 30). Прямой `COUNT(*)` считает строки, поэтому governance
  снимает advisory `asset.query_fields_unchecked`, когда его единственной причиной была
  звезда. `COUNT(DISTINCT *)` зависит от всех колонок источника там, где движок его принимает:
  chart impact теперь оставляет `chart.sql_partial` для плитки, которая прежде была «не этой
  плиткой» либо была названа только по другим чтениям. Реализовано `gpt-5.6-terra` xhigh
  (Codex companion, MyFlow-кандидат `terra-xhigh`); ревью — `gpt-6-astra` xhigh через MyFlow:
  моделированное ревью, не human evidence и не production/design-partner acceptance.
  Итог ревью — один проход `gpt-6-astra` xhigh через MyFlow (2026-09-25): 0 findings.

- **ClickHouse `SETTINGS` не читается как колонка** (`CHECK_VERSION` 15 → 16,
  `VERIFIER_VERSION` 28 → 29). Имя и значение настройки больше не дают phantom review
  `asset.query_ungoverned_field`, advisory `asset.query_fields_unchecked`, chart
  `reads_field` или `chart.sql_partial` на имени настройки. `additional_table_filters`
  вместо phantom review теперь даёт advisory `asset.query_fields_unchecked` и
  `chart.sql_partial`: это SQL-текст row filter, который reader не разбирает;
  `additional_result_filter` не добавляет чтение таблицы. Реализовано `gpt-5.6-terra` xhigh
  (Codex companion, MyFlow-кандидат `terra-xhigh`); ревью — `gpt-6-astra` xhigh через
  MyFlow: моделированное ревью, не human evidence и не production/design-partner acceptance.
  Итог ревью — один проход `gpt-6-astra` xhigh через MyFlow (2026-09-25): 0 findings.

- **Колонки из `HAVING` и `QUALIFY` читаются как из любой другой клаузы** (`CHECK_VERSION`
  14 → 15, `VERIFIER_VERSION` 27 → 28). `Scope.columns` sqlglot опускал голое `cost` как
  возможный output alias, поэтому reviewed PostgreSQL `HAVING sum(cost) > 0` и ClickHouse
  `QUALIFY row_number() OVER (PARTITION BY cost) = 1` могли пройти как чистые. Теперь
  PostgreSQL читает input текущего query level, а ClickHouse сначала применяет прежнее
  правило видимого alias/read-through; окно в `QUALIFY` включает `PARTITION BY` и `ORDER BY`.
  Направление одно: добавляются `asset.query_ungoverned_field` (review),
  `asset.query_fields_unchecked` (advisory) и chart `reads_field`; ни одна находка не
  снимается. Реализовано `gpt-5.6-terra` xhigh (Codex companion, MyFlow-кандидат
  `terra-xhigh`); ревью — `gpt-6-astra` xhigh через MyFlow: моделированное ревью, не human
  evidence и не production/design-partner acceptance. Итог ревью — один проход
  `gpt-6-astra` xhigh через MyFlow (2026-09-25): одна находка (голое имя над единственным
  подзапросом или CTE) отклонена воспроизведением — её запрос отвергают оба движка, а имя
  читается так же, как в любой другой клаузе.

- **Metric-гэп `chart.metric_unread` задаётся по каждому дашборду** (`VERIFIER_VERSION`
  25 → 26, `CHECK_VERSION` остаётся 13). Резолвер держит дашборды всех продуктов в scope
  одним списком, поэтому изменение, достигающее продукта A, поднимало
  `chart.metric_unread` и на плитке дашборда, который доставляет только продукт B, не
  декларирующий изменённую метрику, — дыра о вопросе, которого этот дашборд не задавал.
  Теперь для каждого точного pinned dashboard ref берётся объединение метрик продуктов в
  scope, которые доставляют этот ref, и решение о `chart.metric_unread` на плитке raw-SQL
  принимается только по пересечению этого объединения с метриками изменения: общий
  дашборд спрашивает объединение всех доставляющих продуктов, смешанное изменение
  фильтруется по каждому дашборду независимо, версии дашборда не сливаются, порядок
  продуктов не влияет. Чтения полей, IR-совпадение `computes_metric` и структурные гэпы
  (`chart.opaque_sql`, `chart.sql_partial`, `chart.dashboard_spec_missing`) остаются всем
  изменением; `chart.metric_unknown` и `chart.formula_untyped` — уровнем изменения.
  Реализовано GLM 5.3 xhigh (изолированный профиль), ревью — synthetic Astra xhigh
  read-only по ворк-ордеру: моделированное ревью, не human evidence и не
  production/design-partner acceptance. Финальный post-implementation review — один проход
  `gpt-6-astra` xhigh через MyFlow (2026-09-24), 0 findings.

- **ClickHouse inline- и ARRAY JOIN-алиасы больше не читаются как поля таблицы**
  (`CHECK_VERSION` 13 → 14, `VERIFIER_VERSION` 26 → 27). Псевдополя вроде
  `dm.sales_daily.a`, `dm.sales_daily.y` и `dm.sales_daily.r` исчезают из governance и
  chart impact: alias виден во всём своём query level, но не в подзапросе; его определение
  грейдится на месте. Верхнеуровневый SELECT-алиас сохраняет приоритет, а без него берётся
  первое inline/ARRAY JOIN-объявление имени. `ARRAY JOIN tags AS a` по-прежнему читает
  `tags`, `ARRAY JOIN tags` и `ARRAY JOIN tags AS tags` не меняются; alias справа от `IN`
  не становится таблицей. Направления оба: уходят ложные `asset.query_ungoverned_field`
  и phantom `reads_field` на алиасе, но inline-определение в HAVING или QUALIFY, которое
  прежняя атрибуция пропускала, теперь грейдится на месте и может назвать реальное поле
  (например, `internal_cost_raw` в `(internal_cost_raw AS y)`). Реализовано
  `gpt-5.6-terra` xhigh (Codex companion, MyFlow-кандидат `terra-xhigh`); ревью —
  `gpt-6-astra` xhigh через MyFlow: моделированное ревью, не human evidence и не
  production/design-partner acceptance. Итог ревью — шесть проходов `gpt-6-astra` xhigh через
  MyFlow (2026-09-24): девять находок, восемь исправлены, одна (порог по wall-clock) отклонена
  с доказательством, последний проход — 0 findings.

- **`x IN <table>` читается как таблица, которую оно называет** (`CHECK_VERSION` 12 → 13,
  `VERIFIER_VERSION` 24 → 25). ClickHouse исполняет `x IN rev` как `x IN (SELECT * FROM
  rev)`, а sqlglot разбирает имя как колонку, и гейт её так и грейдил: `IN rev` давал
  `asset.query_ungoverned_field` на колонку `dm.sales_daily.rev`, которой ни у кого нет, а
  саму таблицу не читал; `IN dm.rev` поднимал только advisory
  `asset.query_fields_unchecked`, и reviewed-число, отфильтрованное через неуправляемую
  таблицу, проходило. Теперь на ClickHouse колонка справа от `[GLOBAL] [NOT] IN`, которая
  не видимый алиас, до scoping переписывается в `IN (SELECT * FROM <name>)` и получает те
  же TableRef и тот же исход звезды, что подзапросная форма. Видимый WITH- или
  SELECT-алиас там остаётся алиасом (по правилу T-13; циклический SELECT-алиас считается
  колонкой и становится таблицей), CTE с этим именем — CTE, как во FROM; литеральные
  списки, подзапросная форма и PostgreSQL-семейство не меняются, функциональная форма
  `in(x, t)` по-прежнему не разбирается. Chart impact читает табличную форму как
  подзапросную: операторская плитка с `IN dm.rev` не получает `reads_field` на
  `dm.rev.revenue` и остаётся `chart.sql_partial` «operator SQL was not fully resolved: it
  selects *»; голое `IN x` открывает именованную CTE `x` в обходе исполняемой части, и
  словарное чтение в ней называется. Версии растут в обе стороны: ложной находки поля для
  `IN rev` больше нет, а неуправляемая таблица справа от `IN` теперь даёт review
  `asset.query_ungoverned` там, где был advisory (`IN dm.rev`) или ничего (`IN rev`).
  Заодно закрыт F-T-13-16: циклическое множество ключуется объявлением и scope, и цикл,
  который есть только во внутреннем scope, больше не делает внешнюю ссылку колонкой.
  Переписывание ищет колонку справа от `IN` до любой сборки; statement с ней строит дерево
  scope и индекс алиасов дважды (риск принят). Остаются названными: видимый scalar WITH-
  или SELECT-алиас справа от `IN` выигрывает у одноимённой CTE, и табличная форма молчит
  там, где подзапросная открыла бы CTE (F-T-14-3); FROM-алиас справа от `IN` читается как
  таблица этого имени, как и в подзапросной форме (F-T-14-1, пере-сообщение). Срез начал
  grok xhigh (`grokw`); оба grok-аккаунта упёрлись в лимиты, и закончил opus-high
  (`claude -p`, claude-opus-5, effort high); ранний выход в `clickhouse_in_table_fields`
  добавил оркестратор после красного полного гейта. Ревью — один проход Fable
  (claude-fable-5-1, effort medium), записанный через MyFlow как внешнее ревью; Opus-ревьюеры
  пропущены, потому что делят движок с автором, GLM-5.3 и Codex упёрлись в rate limit.
  Моделированное ревью: не Astra по ADR 0006, не human evidence, не production или
  design-partner acceptance.

- **ClickHouse-алиас читается насквозь, а не как колонка** (`CHECK_VERSION` 11 → 12,
  `VERIFIER_VERSION` 23 → 24). На ClickHouse голое имя, которое в месте написания —
  видимый алиас, грейдилось как колонка таблицы в scope: валидный reviewed SQL нёс
  `asset.query_ungoverned_field` на имена вроде `floor_value`, `allowed` или `m`, которых
  нет ни у одной таблицы, а колонки внутри expression-form `WITH <expr> AS a` не читались
  вовсе. Теперь `extract_column_refs` следует правилам алиасов ClickHouse: SELECT-алиас
  виден на своём уровне запроса и не виден в подзапросах; WITH-алиас — в объявившем
  запросе и во всех подзапросах и ветках UNION ниже; алиас выигрывает у колонки,
  идентификаторы сравниваются как написаны; внутри определения алиаса X имя X — колонка.
  Ссылка на WITH-выражение читается как его inline-подстановка: колонки грейдятся на месте
  ссылки, вложенный запрос — как тот же подзапрос там. Циклы (невалидный ClickHouse)
  читаются как колонки — в сторону, которая ничего не прячет. Сквозное чтение линейно;
  ссылка за пределом глубины (64) или бюджета раскрытий (10 000) называется unattributed
  под своим именем, а не пропускается. Chart impact: алиас больше не `chart.sql_partial`
  «not attributed to a table», а чтение через expression-form `WITH` даёт `reads_field`.
  Версии растут в обе стороны: меньше находок на именах алиасов и новые — на колонках,
  которые читает выражение алиаса. PostgreSQL-семейство не меняется. Остаётся принятым
  риском: имя, объявленное и WITH-, и SELECT-алиасом на одном уровне, читается как колонка
  (`WITH revenue AS X, X * 2 AS w SELECT w AS X` даёт `asset.query_ungoverned_field` на
  `dm.sales_daily.X`, F-T-13-17) — сторона ложного отказа на statement, который ClickHouse,
  скорее всего, отвергает. Реализовано grok xhigh (`grokw`), ревью — Opus через MyFlow
  (`opus-sub`), три прохода, без второго голоса; GLM-5.3 и Codex 2026-09-18 недоступны:
  моделированное ревью, не Astra по ADR 0006, не human evidence, не production или
  design-partner acceptance.

- **Какая часть operator SQL исполняется, решает один обход гейтов CTE**
  (`VERIFIER_VERSION` 22 → 23, `CHECK_VERSION` остаётся 11). Резолвер решал это дважды и
  по двум правилам: словарный путь открывал scalar `WITH`, когда любое голое имя где угодно
  несло его алиас, включая читателя внутри CTE, которую никто не исполняет; путь звезды
  проверял достижимость отдельно. Теперь один линейный обход разобранного дерева решает
  оба пути — словарный / Join-таблица и звезда, — один раз на SQL. Гейты — именованные CTE
  и ClickHouse scalar `WITH` в обеих формах. Именованная CTE открывается, когда исполняемая
  неквалифицированная ссылка на таблицу её называет; scalar `WITH` — когда исполняемая
  колонка читает алиас (голое имя или обращение к кортежу с любого уровня: `t.1`, `t.a.b`,
  по левому квалификатору). Плитка без словарного или Join-вызова и без звезды обход не
  делает. На тех же входах чтений и гэпов больше: CTE называется без учёта регистра и
  независимо от порядка объявления (forward reference); запрос, вложенный в исполняемую
  часть, исполняется вместе с ней, и именованная CTE, которую он называет, открывается —
  в том числе запрос внутри живого тела scalar `WITH` и подзапрос, до которого sqlglot
  scope не доходит (аргумент `arrayJoin`); алиас, прочитанный только через обращение к
  кортежу с любого уровня, считается прочитанным (для звезды под `WITH <expr> AS alias`
  это совпадает с 22, для звезды под `WITH (SELECT …) AS alias` и для словарного вызова —
  новое); database или catalog qualifier с тем же именем, что у scalar `WITH`, открывает
  его — это больше чтений, чем у 22. Меньше: читатель, который не исполняется, ничего не
  открывает; звезда внутри непрочитанного `WITH <expr> AS alias` больше не
  `chart.sql_partial`; именованная CTE, объявленная во вложенном запросе и никем не
  названная, не исполняется. Срез закрывает оба пункта, которые запись T-10 отложила в
  T-11: алиас, прочитанный из CTE, которую никто не исполняет, больше не делает scalar
  `WITH` достижимым, и звезда внутри `WITH <expr> AS alias` считается только когда
  алиас прочитан. Известные пере-сообщения, все в сторону, которая ничего не прячет:
  shadowing не моделируется; table alias с тем же именем открывает scalar `WITH`;
  database (или catalog) qualifier с тем же именем — тоже. Первое ревью нашло, что
  достижимость по sqlglot scopes прятала чтения, которые HEAD видел: sqlglot не строит
  scope внутри expression-form `WITH`; починка — обход гейтов. Реализовано grok xhigh
  (`grokw`), ревью — Opus через MyFlow (`opus-sub`), без второго голоса; GLM-5.3 и Codex
  2026-09-18 недоступны: моделированное ревью, не Astra по ADR 0006 и не human evidence.

- **`joinGet` читается как `dictGet`, или называется непрочитанным** (`VERIFIER_VERSION`
  21 → 22, `CHECK_VERSION` остаётся 11). `joinGet('dm.rev', 'revenue', key)` читает колонку
  Join-таблицы, а `extract_column_refs` видит только ключ — плитка выглядела прочитанной
  целиком и снималась ответом «не эта плитка» для изменения той самой колонки. Семейство
  Join-таблицы идёт тем же путём, что словарное: `joinGet` / `joinGetOrNull` с литеральными
  таблицей и одной колонкой разворачивается в чтение `<таблица>.<колонка>` и участвует в
  `reads_field` и правиле метрики; любой другой `joinget…` — идентификатор или выражение
  вместо литерала, кортеж колонок (не сигнатура ClickHouse), неизвестное имя — оставляет
  плитку прочитанной частично (`chart.sql_partial`) с клаузой «whose Join table read this
  run could not name». Считаются только вызовы, которые statement исполняет. На тех же
  входах появляется либо чтение, либо ещё одна клауза гэпа. Реализовано grok xhigh
  (`grokw`), ревью — Opus через MyFlow (`opus-sub`), без второго голоса; GLM-5.3 и Codex
  2026-09-18 недоступны: моделированное ревью, не Astra по ADR 0006 и не human evidence.

- **Словарный вызов ClickHouse доведён до закрытого набора и до того, что statement
  исполняет** (`VERIFIER_VERSION` 20 → 21, `CHECK_VERSION` остаётся 11). Четыре дефекта
  первого среза, найденные ревью после него, закрыты: типизированные геттеры —
  именованный закрытый набор (`dictGet<Type>` по целым 8…256, float32/64, decimal32…256,
  date, date32, datetime, datetime64, uuid, ipv4, ipv6, string, bool, с `OrDefault` /
  `OrNull`), поэтому `dictGetDecimal64` и `dictGetDate32` называют чтение вместо гэпа;
  scalar `WITH (SELECT …) AS alias` считается, когда алиас прочитан, а именованная CTE, в том
  числе `WITH x AS ((SELECT …))`, — только через FROM/JOIN; клауза непрочитанного вызова
  говорит то, что правдиво для каждого: «whose dictionary read this run could not name»;
  плитка без `dict…` не строит scope-дерево. На тех же входах гэпов становится меньше, а
  detail словарного `chart.sql_partial` меняется. Открыто и отложено в T-11 (оба
  пере-сообщают, ничего не прячут): алиас, прочитанный из неисполняемой CTE, делает scalar
  `WITH` достижимым, и звезда внутри `WITH <expr> AS alias` считается при любом алиасе.
  Реализовано grok xhigh (`grokw`), ревью — Opus через MyFlow (`opus-sub`, второй голос
  `fable`; GLM-5.3 и Codex 2026-09-18 недоступны): моделированное ревью, не Astra по
  ADR 0006 и не human evidence.

- **Metric-гэп задаётся только там, где вопрос задал продукт в scope**
  (`VERIFIER_VERSION` 19 → 20, `CHECK_VERSION` остаётся 11). Резолвер записывал
  `chart.metric_unread`, `chart.metric_unknown` и `chart.formula_untyped` для каждого
  обходимого изменения, в том числе такого, от которого никто в scope не зависит, и плитка
  попадала в бакет `impact.products` против метрики, которую никто не читает. Теперь
  `resolve_impact` передаёт резолверу, достигает ли изменение продукта в scope, и
  недостигающее изменение этих вопросов не задаёт. Термы по-прежнему разрешаются, так что
  `computes_metric` на таком изменении остаётся evidence; гэпы плитки (`chart.opaque_sql`,
  `chart.sql_partial`, `chart.dashboard_spec_missing`) и разрешение полей не менялись.
  Открыто: на плитках какого продукта гэп вправе быть записан — дашборды всех продуктов в
  scope лежат одним списком. Реализовано grok xhigh (`grokw`), ревью — Opus через MyFlow
  (`opus-sub`, второй голос `fable`; GLM-5.3 и Codex 2026-09-18 недоступны):
  моделированное ревью, не Astra по ADR 0006 и не human evidence.

- **Словарный вызов ClickHouse — чтение, а не молчание** (`VERIFIER_VERSION` 18 → 19,
  `CHECK_VERSION` остаётся 11). `extract_column_refs` видит у `dictGet('dm.rev', 'revenue',
  region)` только ключ, и плитка выглядела прочитанной целиком — резолвер мог снять её
  ответом «не эта плитка» для изменения `dm.rev.revenue`. Теперь литеральные словарь и
  атрибут (или кортеж атрибутов) разворачиваются в чтения `<словарь>.<атрибут>`, которые
  дают `reads_field` и участвуют в правиле метрики как любое атрибутированное чтение; любой
  другой `dict…` — нелитеральный аргумент, `dictHas` / `dictIsIn`, иерархия, неизвестная
  функция — оставляет плитку прочитанной частично (`chart.sql_partial`). Считаются только
  вызовы в scope, который statement исполняет. Реализовано grok xhigh (`grokw`), ревью —
  Opus через MyFlow (`opus-cli`, на части прогонов молча отдавший haiku, затем `opus-sub`;
  второй голос `fable`; GLM-5.3 и Codex 2026-09-18 недоступны): моделированное ревью, не
  Astra по ADR 0006 и не human evidence.

- **Операторский `raw_sql` читается из своего SQL** (`VERIFIER_VERSION` 17 → 18,
  `CHECK_VERSION` остаётся 11). Каждая такая плитка была одной строкой `chart.opaque_sql`:
  гейт говорил, что IR не знает, какие колонки читает плитка, про statement, который говорит
  именно это. Теперь `reads_field` ставится там, где statement сам атрибутирует чтение;
  `computes_metric` из произвольного SQL не выводится, поэтому невыведенная связь с метрикой
  — отдельный гэп `chart.metric_unread`, кроме случая, когда statement прочитан целиком и не
  называет ни одной колонки-терма изменённой метрики. `chart.opaque_sql` — SQL, который не
  разобрали; `chart.sql_partial` — прочитанный частично. Выражение, которое резолвер не
  разворачивает в чтения колонок, оставляет плитку прочитанной частично, а не «не эта плитка»:
  это контракт, а сегодня гейт узнаёт три известных случая (`{{ … }}`, `COLUMNS('re')`,
  `JOIN … USING`); конструкция вне этого набора — дыра детектора, не гарантия. `COUNT(*)`,
  `COUNT(DISTINCT *)` и звезда в scope, который statement не читает, дырами не являются.
  `impact.products` в detail раскладывает плитки по коду гэпа, так что четыре числа делят
  плитки, которых коснулись изменения продукта. Chart-разрешение — evidence, грейдинг не
  меняет.

- **Предшествующие FROM-элементы LATERAL доходят до каждого уровня его тела**
  (`VERIFIER_VERSION` 16 → 17, `CHECK_VERSION` 10 → 11). sqlglot вешает sibling-источники на
  UDTF-scope, а тело может сидеть ниже — set operation или вложенный LATERAL, — и валидный
  PostgreSQL отвергался по колонке, которую объявляет sibling. Обход идёт вверх по предкам и
  останавливается на стене derived table или CTE, которая окружающее не читает. Кандидаты
  остаются плоским множеством: имя, объявленное на двух LATERAL-уровнях, непроверено, а не
  приписано ближайшему. Durable spec и комментарии обеих версий записывают, что имя внутри
  тела LATERAL может сдвинуть грейдинг в любую сторону, `needs_review` включительно, — старый
  bundle нельзя читать сегодняшним правилом.

- **Голое имя разрешается только по источникам, которые его scope читает**
  (`VERIFIER_VERSION` 15 → 16, `CHECK_VERSION` 9 → 10). CTE, которого FROM/JOIN не называет,
  считался источником scope'а, и reviewed-запрос с WITH деградировал в advisory с неверной
  причиной и переставал грейдить необъявленные колонки governed-витрин. Голое имя внутри
  коррелированного подзапроса приписывалось собственной таблице подзапроса, и объявленная
  колонка охватывающего запроса отвергалась как ungoverned. Теперь scope предлагает имя
  только тем источникам, которые читает; имя, написанное там, откуда охватывающий запрос мог
  его поставить, приписывается только когда его объявляет один локальный governed-источник —
  иначе остаётся `asset.query_fields_unchecked` со своей причиной, потому что проверка имя
  наружу не ведёт.

- **Owner-summary объясняет границы reconciliation, attestation, cost и подписи.**
  `result.equivalent` означает совпадение supplied rows, не semantic approval.
  Attestation связывает reviewed query, snapshot и заявленное environment,
  не доказывая исполнение SQL. `dwh.cost` относится к compiled reference SQL,
  а не captured reviewed asset SQL. Подпись подтверждает producer и целостность,
  не исполнение SQL или production sign-off; её проверяют отдельно с trusted key.
  Отчёт sealed до проверки bundle и сам не заявляет, что bundle signed или verified.
  `VERIFIER_VERSION` 14 → 15, `CHECK_VERSION` остаётся 9.

- **Evidence manifest получает opt-in Ed25519 authentication** (`VERIFIER_VERSION` 13 →
  14, `CHECK_VERSION` остаётся 9). `decision verify --signing-key <private.pem>` подписывает
  domain-separated RFC 8785 manifest unencrypted PKCS#8 ключом по явному внешнему пути и
  выставляет `integrity=signed` только после self-verification. `decision evidence
  --trust-key <public.pem>` проверяет подпись против out-of-band pinned SPKI trust root;
  флаг повторяется для overlap-ротации, а signed artifact без подходящего ключа не
  выдаётся. `key_id` — SHA-256 raw Ed25519 public key, signature — canonical unpadded
  base64url. Algorithm/key identity входит в idempotency key, поэтому unsigned, signed и
  rotated-key runs получают разные `run_id`. Старые `content_hashed` bundles остаются
  читаемыми. Private key не записывается в evidence, Git или ошибки; production KMS и
  trusted capture boundary не заявлены ([ADR 0005](docs/adr/0005-ed25519-evidence-trust-root.md)).

- **`dwh.cost` проверяет runtime-границы exact reference-запроса**
  (`VERIFIER_VERSION` 12 → 13, `CHECK_VERSION` 8 → 9). После безопасных plan/access/
  security-проверок ClickHouse выполняет запрос через command API с `FORMAT Null` под
  engine-enforced `max_bytes_to_read` и `max_execution_time`, а PostgreSQL — read-only
  транзакцией с потоковым cursor под transaction-local `statement_timeout`. Ответ не
  материализуется; превышение или отсутствие runtime evidence требует review, успешное
  выполнение подтверждает границу. PostgreSQL не предоставляет честный exact-query
  `scanned_bytes`, поэтому его `max_scanned_bytes` остаётся явным `cost.not_measured`, а
  нулевой лимит без соответствующего counter не выдаётся за доказанный. Для ClickHouse
  используется `command()`, чтобы драйвер не добавлял второй `FORMAT Native` к уже
  заданному `FORMAT Null`.

- **Голая колонка reviewed-запроса приписывается через governed-модель**
  (`VERIFIER_VERSION` 11 → 12, `CHECK_VERSION` 7 → 8). Голое `category` в join'е двух
  таблиц уходило в `asset.query_fields_unchecked` даже тогда, когда snapshot объявлял это
  имя ровно на одной из них: гейт отказывался читать evidence, которое уже держал. Движок
  этот SQL принял — значит имя разрешилось ровно в один источник scope'а; если и модель
  оставляет единственного кандидата, прочитать его — это чтение запроса, а не догадка о
  нём. Условие вывода: все источники scope'а — governed-таблицы. Неуправляемая витрина,
  CTE или подзапрос рядом держат колонки, о которых snapshot не знает, — имя остаётся
  непроверенным; два объявляющих источника (пусть даже это одна таблица, приджойненная к
  себе) оставляют тот же выбор, что был бы у движка, — тоже непроверенным.
  `asset.query_fields_unchecked` разбирает остаток по причинам (несколько объявляющих
  источников; scope, который модель не видит насквозь; квалификатор, которого не объявляет
  ни один охватывающий scope; scope без собственных источников) и считает его по ссылкам, а
  не по именам: разрешённое `category` не закрывает `z.category` рядом.
  Имя, которого не объявляет ни один governed-источник scope'а, называется
  `asset.query_ungoverned_field` (review) вместе с перечнем этих источников. Для `*`
  ничего не изменилось: состав колонок по-прежнему зависит от физической таблицы.

- **Governance reviewed-запроса доведена до поля** (`VERIFIER_VERSION` 10 → 11,
  `CHECK_VERSION` 6 → 7). Проверка витрин отвечала, что число «про эти витрины», но метрика
  определена над полями: запрос по governed-витрине, читающий необъявленную колонку, считал
  число частично из данных, о которых модель ничего не говорит. `assets.invalidated` теперь
  называет и это — `asset.query_ungoverned_field` (review). Колонка приписывается таблице
  только там, где выбора не оставляет сам SQL: квалифицированная разрешается наружу по
  цепочке scope'ов, неквалифицированная — лишь при единственном источнике в scope, а
  проекционный алиас чтением витрины не считается. Что не разрешается без догадки,
  называется, а не пропускается: `asset.query_fields_unchecked` (advisory) перечисляет
  неприписанные имена и отдельно сообщает про `SELECT *`, состав колонок которого зависит
  от физической таблицы, — частичная проверка не должна читаться как чистая. Колонка
  неуправляемой витрины отдельной находкой не становится (один факт — один вес).

- **Reviewed-запрос asset'а сверяется с governed-моделью** (`VERIFIER_VERSION` 9 → 10,
  `CHECK_VERSION` 5 → 6). Provenance-цепочка якорится на reviewed `reference_query`, но
  ничто не проверяло, что сам этот запрос читает витрины, которыми модель управляет:
  attested, сошедшееся число из `dm.sales_daily_v2` проходило молча — ровно тот отказ,
  ради которого продукт существует. `assets.invalidated` теперь отвечает на более широкий
  вопрос — перестал ли asset быть доказательством, — и второй причиной стало
  `asset.query_ungoverned` (review): таблицы reviewed-запроса, которых нет в кандидатном
  snapshot'е, названы поимённо. Сверяются только таблицы: приписать неквалифицированную
  колонку одной из таблиц в scope — догадка, а гейт, догадывающийся о governance,
  перестаёт быть evidence (инвариант 6). Регистр читается по движку asset'а — ClickHouse
  различает `Sales_Daily` и `sales_daily`, PostgreSQL-семейство складывает
  **неквотированный** идентификатор, — поэтому `extract_table_refs` отдаёт квотирование
  вместе с именем, а `extract_table_names` остаётся его тонкой обёрткой для RBAC.
  Заодно `assets.invalidated` перестала прятать причину: summary code теперь называет
  решившую находку (`asset.metric_changed`, `asset.stale_snapshot`,
  `asset.query_ungoverned`), а detail — сам повод с именем asset'а, потому что в
  owner-отчёт попадает только он, а «1 из 1 asset(s)» одинаков для двух разных починок.

- **Происхождение кандидатного числа проверяется** (`VERIFIER_VERSION` 8 → 9). Гейт запрос
  asset'а не исполняет — числа приходят из порта, — поэтому дыра была не в сверке, а рядом с
  ней: число, снятое месяц назад, на другом кластере, запросом, который никто не ревьюил,
  сходится не хуже правильного. Теперь кандидатный результат несёт `ResultAttestation`
  (`origin`, `captured_at`, `environment_ref`, `semantic_digest`, `query_digest`,
  `executed_by`), а новая проверка `results.provenance` сверяет три её утверждения с тем, что
  гейт держит независимо: snapshot — с canonical digest кандидата, среду — с
  `environment_ref` прогона, исполненный запрос — с digest reviewed `reference_query`
  asset'а (новое поле `VerifiedAsset`, `{engine, sql}`, проходящее SELECT-only guard прямо
  при загрузке контракта). Четвёртое — что runner действительно выполнил заявленный запрос —
  непроверяемо без подписи и так и названо. Число без attestation больше не проходит молча:
  `provenance.unattested`. Ни одна находка не блокирует (блокирует расхождение чисел, и это
  уже делает `results.reconcile`), summary code называет решившую находку, а
  `result.equivalent`-claim несёт находки caveat'ами и ссылкой на артефакт provenance —
  чтобы прочитанный вне bundle claim не выглядел чистым. Файл `--results` читается в обеих
  формах, старая (`{asset id: ResultSet}`) остаётся валидной.

- **Impact доходит до чарта, а не до дашборда** (`VERIFIER_VERSION` 7 → 8). `resolve_impact`
  разрешает изменение до конкретной плитки доставляемого дашборда и держит два утверждения
  раздельно: `computes_metric` — measure чарта воспроизводит термы формулы (numerator на мере,
  denominator на её `denominator`; `transform`/`compare` остаются тем же базовым агрегатом),
  `reads_field` — запрос просто называет изменённую колонку. Термы берутся из обоих снапшотов:
  плитка, считавшая вчерашнее определение, — ровно та, которую владелец должен открыть.
  Разрешение — evidence, а не грейдинг: вердикт и `_reaching` не меняются, но плитка, читающая
  колонку без покрывающего metric contract, теперь названа как незадекларированная зависимость.
  Нерешаемое выходит именованным гэпом (`chart.opaque_sql`, `chart.dashboard_spec_missing`,
  `chart.formula_untyped`, `chart.metric_unknown`), а не пустым списком; owner summary
  печатает и плитки, и гэпы.

- **Репетиция пилота release gate на локальном стенде** —
  `docs/DECISIONOPS_PILOT_2026-08-25.md` и
  `docs/pilot/2026-08-25-ch-{control,tz-margin,tz-revenue,result-drift}/` (входы +
  evidence всех четырёх прогонов, перепроверяемые `auto_bi decision evidence`). Против живого
  ClickHouse 24.8.14.39 ролью `gate_ro`, на графе из двух governed-продуктов на одной
  витрине: контроль → `needs_review` только из-за `freshness.stale`; `time.timezone`
  `Europe/Moscow → UTC` → `block` для зависимого продукта и `impact.none` для независимого;
  расхождение записанного результата против verified asset (ожидаемое число снято со стенда)
  → `block` с названной разницей. Гейт целиком ~1.6 с и детерминирован. Записано и то, чего
  репетиция не доказывает — task 8 закрывает владелец.

### Fixed

- **`impact.products` грейдил изменения, до этого продукта не доходящие.** `resolve_impact`
  намеренно держит в payload каждое изменение диффа, включая чужие; гейт же грейдил весь
  список, поэтому change чужой метрики поднимал вердикт продукта и попадал в его секцию
  «Whose numbers this reaches». Теперь грейдится и рендерится только достигшее продукта
  (`_reaching`), payload остаётся полным, `semantic.diff` по-прежнему называет change.
- **`assets`/`results` брались независимо от продукта.** Verified asset чужой метрики
  инвалидировался на вердикт этого продукта, а его сверка запечатывала в bundle claim о
  числе, которого продукт не отдаёт. Репозиторий сужается до assets, называющих метрики
  продукта (версии игнорируются с обеих сторон).
- **Одинаковый `run_id` тихо перезаписывал запечатанный bundle.** Записанные результаты не
  входят в idempotency key §7.6 (как и показания движка), поэтому два прогона с разными
  результатами и разными вердиктами делят `run_id`. `BundleWriter.write` теперь отказывается
  писать другой bundle под тем же id (`EvidenceConflictError` → exit 2) до записи первого
  байта; идентичная перезапись остаётся no-op.

### Changed

- **Документация и корень репозитория упорядочены.** Добавлен единый
  `docs/README.md`; короткий DecisionOps current state отделён от подробного
  implementation history; завершённые strategy/bootstrap/execution-документы
  перенесены из корня в `docs/history/`. Локальные pilot и owner-only artifacts
  получили явные ignore-каталоги и не удалялись.

- `CHECK_VERSION` и `VERIFIER_VERSION` 1 → 2: смысл `impact.products` изменился, и bundle,
  выданный прежним верификатором, не должен читаться как гранёный сегодняшним правилом.

- **§8.2 шаг 7 — стоимость плана против `budgets` метрики** — `dwh.cost`, последняя из пяти
  проверок шага. Оценивается план reference-запроса шага 6 против потолка, объявленного самой
  метрикой. Спрашивается **планом, никогда прогоном**: `EXPLAIN ESTIMATE` на ClickHouse и
  `EXPLAIN` **без** `ANALYZE` на PG-семействе. Градация:
  - план дороже объявленного потолка → **review**: число остаётся верным, а выпускать ли
    метрику дороже, чем объявил её владелец, — его решение (та же градация, что у протухшего
    источника);
  - объявленный потолок, на который движок не дал оценки, → **review**: проверка не прошла, а
    не прошла успешно;
  - `max_scanned_bytes` и `timeout_ms` → **advisory** `cost.bound_not_estimable`: их не
    отвечает ни один планировщик, а вывести их (гранулы × ширина колонки; стоимость,
    переведённая в миллисекунды) значило бы выдать арифметику гейта за ответ движка. Это
    граница гейта, а не двусмысленность владельца;
  - потолок у метрики, для которой шаг 6 не скомпилировал запрос, → **advisory**: гэп уже
    назван там.

  Чтобы это вообще было чем оценивать, `MetricNode` теперь несёт `budgets` (§7.3 и так
  перечисляет «physical budgets» среди переносимых частей контракта): план компилируется из
  снапшота, и брать запрос оттуда, а потолок — из файла рядом значило бы оценить релиз по
  бюджету другой версии метрики. Поле двигает `canonical_digest`, поэтому сдвинутый потолок
  теперь обязан иметь имя в диффе: `metric.budget_relaxed` (**review** — поднятый или снятый
  потолок пропускает план, который гейт отказал вчера) и `metric.budget_tightened`
  (**advisory** — спрятать этим ничего нельзя, а если он отказывает релизу, `dwh.cost`
  скажет сам).

  Оба движка умеют ответить молчанием, которое читается как ноль, и оба случая
  воспроизведены на стендах: ClickHouse отдаёт **пустую** оценку, когда план не читает ни
  одного парта — тривиальный `count()` из метаданных и витрина на `ENGINE = Memory`, чьи
  строки запрос действительно прочитал; PostgreSQL считает строки скан-узла **после**
  фильтра (`WHERE store_id = 1` — 600 → 30) и вообще не строит скан-узел для витрины,
  опустошённой row security (`One-Time Filter: "false"`). Читается скан-узел, а не корень
  плана: корневой `Aggregate` на стенде сказал `Plan Rows: 60` над `Seq Scan`, сказавшим 600.
  Прогнано вживую на обоих стендах: потолок 500 → `cost.over_budget`, потолок 1000 →
  `cost.within_budget`.

  В гейте больше нет ни одного `check.not_implemented` — конструктор удалён; сам код остался
  в словаре `verification/summary.py`, потому что bundle, записанный прежней версией
  верификатора, всё ещё его несёт.

- **§8.2 шаг 7 — security: что роль реально достаёт** — последняя из четырёх проверок шага,
  `dwh.security`. Она спрашивает про досягаемость роли, через которую собрано evidence:
  сузила ли row-политика прочитанные числа и есть ли у роли доступ к колонкам, из которых
  сделано само число. Градация:
  - колонка метрики, недоступная роли, → **block**: метрику там не посчитать (и это не то же
    самое, что «колонки нет»);
  - включённый row security, при котором роль не проходит ни одной permissive-политики, →
    **block**: витрина отдаёт пустой ответ **и не отдаёт ошибки**;
  - политика, применяющаяся к сессии, → **review**: числа настоящие, но это числа
    подмножества, а выпускать ли продукт на подмножестве — решение владельца;
  - роль, обходящая row security (superuser / `BYPASSRLS`), при живых политиках → **review**:
    гейт прочитал строки, которых потребители не видят;
  - объявленный `confidential`/`restricted`, ничем не ограниченный для этой роли, →
    **review** (§10.1: ограничение исполняет роль или детерминированный rewrite, а не
    документ); `internal` объявлением не считается — это дефолт непроставленного поля;
  - недоступный access-каталог → **review**: «не вижу политику» не значит «её нет».
  Механизмы у движков разные, и это установлено на стендах: ClickHouse исполняет
  `GRANT SELECT(col)` и **прячет** неразрешённые колонки из `system.columns`, поэтому
  досягаемость читается из `system.grants` (включая partial revoke), и применяет политику
  только к тем, кого она называет; PostgreSQL показывает все колонки в `pg_attribute`,
  отвечает `has_column_privilege`, а при `relrowsecurity` требует хотя бы одну
  permissive-политику — иначе витрина пуста. Политики обоих движков вешаются и на роли,
  поэтому членство решают enabled roles сессии.
  Проверено вживую: `security.unfiltered` под `gate_ro` на `dm`; `security.row_filtered` под
  заведённой на время прогона политикой; `security.column_unreadable` (**block**) под
  `gate_sec`, у которого отобрана одна колонка, — при `physical.as_declared` в том же
  прогоне; на PostgreSQL — `security.column_unreadable` + `security.row_filtered` на
  `dm_sec.sales_daily` и `security.row_policy_denies_all` (**block**) на `dm_sec.denied`.

### Fixed

- **Роль с колоночными грантами больше не блокирует релиз по витрине, которую читает.**
  `has_table_privilege(…, 'SELECT')` у такой роли на PostgreSQL 14.24 равен `false`, и гейт
  выносил `access.table_unreadable` (**block**) по витрине, из которой сам только что читал
  строки. Читаемость спрашивается через `has_any_column_privilege`, а какие именно колонки
  вне досягаемости — находка `dwh.security`.
- **Колонка без гранта на ClickHouse больше не называется отсутствующей.** Движок прячет
  неразрешённые колонки из `system.columns`, поэтому «нет колонки» и «нет гранта» выглядели
  одинаково, и `dwh.physical` выносил `physical.column_absent` (**block**) по колонке,
  которая в витрине есть. Physical молчит там, где security говорит «нет гранта».
- **Отказ движка больше не роняет прогон.** Вопросы про доступ — ровно то место, где у
  read-only роли кончаются привилегии; исключение оттуда превращало факт о среде в упавший
  прогон. Отказ (`ACCESS_DENIED`, SQLSTATE 42501/42P01/3F000) читается как «движок не
  ответил», а всё остальное — оборванное соединение прежде всего — по-прежнему поднимается
  наверх: пустой ответ мёртвого соединения не должен читаться как «политик нет».
- **`dwh.freshness` больше не хвалит пустоту.** Витрину, которую опустошил row security,
  роль читает, но датировать её нечем; проверка молчит (block уже сказан), а сводка пишет
  `freshness.not_run` вместо «все витрины внутри окна».

### Added

- **§8.2 шаг 7 — physical: держит ли движок то, что заявляет модель** — третья проверка
  шага, `dwh.physical`. Сравнивается ровно то, что модель утверждает, и только там, где
  движок ответил:
  - колонка, из которой сделано число метрики (`MetricNode.fields` — термы формулы, ключи
    grain, event time), которой на движке нет → **block**: метрику там просто не посчитать;
  - тип, который разъехался с объявленным → **review**. Это хуже отсутствия: точная форма
    governed ratio из шага 6 выведена именно из объявленного типа, и `Float64` вместо
    `Decimal(18, 2)` означает, что гейт рассуждал не про то число, которое вернёт запрос;
  - разъехавшаяся раскладка (`table_engine`, sorting key, partition key, distribution key)
    → **review**: само число не меняется, но план и advisor спорили про таблицу, которой
    нет.
  Типы читаются из каталога каждого движка в том же написании, в каком записана модель:
  `system.columns.type` на ClickHouse и `format_type(atttypid, atttypmod)` на
  PG-семействе — той же функцией, которой типы писал greenplum-интроспектор. Косметика не
  считается дрейфом: `Decimal(18, 2)` и `decimal(18,2)` — один тип.
  **Раскладка репортится только там, где проверена.** ClickHouse отдаёт engine/sorting
  key/partition key из `system.tables`; распределение и уровни партиций Greenplum живут в
  каталогах, которых нет ни на одном доступном стенде (`gp_distribution_policy`,
  `pg_partition`), поэтому PG-путь оставляет раскладку **не отвеченной**, и проверка
  сравнивает ничего вместо того, чтобы выдумать несовпадение.
  Проверено вживую: засеянная витрина совпадает с моделью → `physical.as_declared` на обоих
  стендах; отдельная база `dm_drift`, где `revenue` действительно `Float64`, а ORDER BY
  действительно `(store_id, date)` → `physical.type_drift` + `physical.layout_drift` в одном
  вердикте. Не реализованной у шага 7 осталась только security-половина (row/column policy и
  классификация).

- **§8.2 шаг 7 — access и freshness против живого движка** — шаг, который до сих пор
  целиком выходил как `check.not_implemented`, наполовину реализован и проверен на двух
  локальных стендах (ClickHouse 24.8.14.39 и PostgreSQL 14.24, оба в WSL, оба под
  read-only ролью). `auto_bi decision verify … --dwh clickhouse|greengage` спрашивает
  движок, гейт выносит две новые проверки:
  - `dwh.access` — роль, под которой пришёл гейт, умеет только читать, и каждая витрина
    в скоупе продукта на этом движке есть и читается. Роль, которая может писать, — **block**
    (инвариант 3: числа, снятые ролью, способной изменить витрину, не то evidence, за которое
    отвечает bundle); отсутствующая или нечитаемая витрина — тоже block;
  - `dwh.freshness` — возраст самой свежей строки против объявленного продуктом
    `freshness_max_age`, измеренный от `created_at` прогона, а не от стенных часов (иначе
    вердикт зависел бы от времени рендера отчёта). Протухший источник — **review**: сам
    change не сломан, а решение выпускать ли продукт на старых данных — владельца.
  **Гейт никогда не проверяет запись записью.** Read-only определяется ответом самого
  движка: на ClickHouse решает профиль (`readonly=1` заставляет сессию отказать даже
  пользователю, которому `users.xml` выдал `GRANT INSERT … ON *.*` — по грантам корректная
  read-only роль читалась бы как пишущая), на PostgreSQL — `has_table_privilege` по
  витринам скоупа плюс отдельная проверка на суперпользователя (привилегии к нему не
  применяются). Ещё один факт со стенда: `max(Date)` по пустой таблице ClickHouse — это
  `1970-01-01`, а не NULL, поэтому вместе с чтением едет `rows_seen`, и пустая витрина даёт
  `freshness.unknown`, а не «протухла на пятьдесят лет».
  Разделение на чистый слой (`verification/environment.py` — вопросы, факты, правила) и IO
  (`verification/environment_probe.py` поверх того же read-only `RunQuery`, что и
  интроспекция, SQL-guard и advisor) оставляет гейт без сокета: он оценивает факты, и они же
  ложатся в bundle как данные, а не как строчка лога. Прогон без `--dwh` остаётся полностью
  офлайновым и говорит `check.not_configured` — это ответ, а не пропуск. Physical и security
  половины шага 7 по-прежнему `check.not_implemented`.

- **§8.2 шаг 6 — governed ratio компилируется и на PostgreSQL-семействе** — вторая форма
  деления, проверенная на живом движке: PostgreSQL 14.24 (`postgresql-14` в WSL Ubuntu
  22.04) — реализация `numeric`, которую наследуют Greenplum и Greengage. Гейт печатает
  `TRUNC(SUM(num) * 1.000000 / NULLIF(SUM(den), 0), <precision>)`. Множитель — не
  косметика: Postgres выбирает масштаб деления по операндам, целясь примерно в 16 значащих
  цифр, поэтому на большом частном `TRUNC(a / b, 18)` дописывает нули, которых деление не
  считало (`1000000000000.00 / 3.00` при 18 → `…33333333` + девять нулей); умножение
  делимого на точную единицу с объявленным числом знаков поднимает его масштаб, не трогая
  значение, и цифры становятся настоящими — приведение к `numeric(_, p)` вместо этого
  округляет. При `precision` 0 множитель всё равно с десятичным знаком: `/` над двумя
  целочисленными термами — целочисленное деление, и оно усекает раньше, чем `TRUNC` увидит
  число. Больше ничего охранять не нужно: это `/` уважает NULL-маску, поэтому
  `NULLIF(den, 0)` закрывает нулевой знаменатель, а NULL-терм занулевает свою строку сам;
  `TRUNC` усекает к нулю — как и `divideDecimal`, так что одно governed-число читается
  одинаково на обоих движках. Движок теперь читается по имени, а не по диалекту
  (`engine.is_known_engine`): `sqlglot_dialect` подставляет v1-движок незнакомому имени —
  честное допущение для `SUM(col)` и никакое для формы, которая вся состоит из
  engine-specific синтаксиса, поэтому незнакомый движок получает `sql.ratio_unverified`, а
  не ClickHouse-форму. Типы термов читаются в словаре своего семейства (`numeric`/`decimal`
  и `integer`/`bigint`/… — целыми именами: префиксом `interval` прочёлся бы как целое).
  Проверено текстом, который печатает компилятор, против засеянной `dm.sales_daily`:
  300/400 → `0.750000`, нулевой и отсутствующий знаменатель → `NULL`, −200/300 →
  `-0.666666` (те же числа, что на ClickHouse-стенде), большое частное при `precision` 18 →
  `333333333333.333333333333333333` против `333333333333.333333330000000000` без
  множителя; `EXPLAIN` разбирает запрос.

- **§8.2 шаг 6 — governed ratio компилируется на ClickHouse** — форма деления,
  которую «негде было подтвердить», проверена на живом движке того же пина
  (`clickhouse local` из релиза `v24.8.14.39-lts`, в который резолвится
  `clickhouse/clickhouse-server:24.8` из `docker-compose.yml`). Гейт печатает
  `CASE WHEN COALESCE(SUM(den), 0) = 0 THEN NULL ELSE divideDecimal(SUM(num),
  COALESCE(nullIf(SUM(den), 0), 1), <precision>) END`: точное деление с усечением к нулю
  на объявленной `unit.precision` (у неё впервые появился потребитель), нулевой или
  отсутствующий знаменатель → NULL в строке. Охрана вокруг вызова не декоративная —
  `divideDecimal` читает значения знаменателя под NULL-маской, поэтому привычный
  `NULLIF(den, 0)` сам по себе даёт `ILLEGAL_DIVISION`; аргументы обязаны быть Decimal,
  поэтому счётные и целочисленные термы приводятся (nullable — nullable-приведением, иначе
  группа из одних NULL падает на CAST). Вне ClickHouse форма не подтверждена →
  `sql.ratio_unverified` с названным диалектом; неточный терм (AVG, Float-колонка) →
  `sql.ratio_inexact_term`; необъявленная в модели колонка → `sql.term_undeclared`.
  Фикстурный продукт больше не даёт `sql.not_compiled`, и шаг 6 ушёл из раздела «что не
  сделано» в отчёте владельцу.

- **§8.2 шаг 6 — reference SQL метрики** — `auto_bi/verification/sql_contract.py`:
  гейт компилирует определение каждой метрики продукта на её же grain (без окна,
  фильтра и top-N — это принадлежит продукту и прогону, не метрике) для base и
  candidate, кладёт обе стороны в bundle и показывает, какие запросы поехали.
  Граница узкая намеренно: typed aggregate компилируется, governed ratio — нет
  (десятичное деление зависит от движка; chart-путь отвечает на это Float64-приведением,
  которое governed-метрика с объявленной precision наследовать не должна, а стенда для
  подтверждения точной формы нет), external/reviewed-SQL не пере-выводится. Все находки
  advisory — почему метрика поехала, уже оценили diff и impact. Агрегат берётся из
  `agent/sqlgen.aggregate_expr`: та же функция, что у chart-пути, вынесена из
  `_measure_expr`, чтобы новая агрегация не компилировалась по-разному для дашборда и
  для evidence, который его проверяет. `check.not_implemented` теперь остался только у
  шага 7 (исполнение против DWH).

- **`reports/summary.md` — производный отчёт для владельца внутри bundle** —
  `auto_bi/verification/summary.py`: единственный артефакт layout §9.2, написанный
  для человека. Порядок — владельческий, не машинный: что блокирует релиз → чьи
  числа задеты (метрика, дашборд, владелец, и почему review поднялся до block) →
  что решает человек → что прогон НЕ сделал → таблица всех check'ов. Отчёт
  пересказывает уже оценённые findings и не вводит собственных; каждый check
  попадает ровно в один раздел, чтобы одна находка не читалась как две.
  `result.no_expectations` вынесен в «что не сделано», а не оставлен среди
  advisory: гейт, который не сравнил ни одного числа, ничего про числа не
  проверил. `evidence.bundle` в отчёте отсутствует намеренно — эта проверка судит
  артефакт, внутрь которого отчёт запечатан, и решается после запечатывания.
  Читается через `auto_bi decision evidence <path> --artifact report-summary-md`.

- **`auto_bi decision evidence` — чтение написанного evidence bundle** —
  `auto_bi/evidence/inspect.py` (`inspect_bundle` пересчитывает digest манифеста из
  байтов на диске, перепроверяет каждый content address и отдаёт `BundleInspection`
  с вердиктом; `read_artifact` выдаёт один payload) и `locate_evidence` в
  `auto_bi/decision/use_cases.py` (адресом может быть и каталог evidence, и один
  run-каталог внутри; несколько run'ов — команда называет их и просит `--run`, а не
  выбирает сама). Вердикт про целостность, а не про релиз: целый bundle выходит с
  `0`, даже если внутри записан `block`. Два отказа сознательные — `restricted`
  payload не печатается никогда (§10.2), а артефакт, который больше не хешируется в
  свой адрес, не выдаётся вовсе. `--expect-digest` проверяет, что это именно тот
  bundle, под которым публиковался вердикт: без него «bundle проходит проверку»
  означает лишь внутреннюю согласованность каталога.

- **`evidence.run_id_mismatch`** (`verify_bundle`) — скопированный bundle остаётся
  внутренне согласованным (манифест каноничен, артефакты хешируются), врёт только его
  расположение. Review, а не block: читать такой bundle можно, но принадлежность к
  run'у, под которым он лежит, ничем не подтверждена.

- **`auto_bi decision verify` — one-shot Analytics Release & Evidence Gate** —
  `auto_bi/verification/release_gate.py` (последовательность §8.2 для одного
  продукта: validate → snapshots → gaps → diff → impact → инвалидация assets →
  reconcile → BI compile → evidence bundle → повторная верификация bundle) и
  `auto_bi/decision/use_cases.py` (единственный слой, читающий ФС). Exit code
  несёт вердикт (0/3/4), usage-ошибка — 2. `operational_status` и `verdict` —
  разные поля, и отчёт не валидируется, если их смешать (инвариант 8). Шаги
  6–7 (compile SQL, DWH-проверки) объявлены `check.not_implemented`, а не
  пропущены молча; недоступный кандидатный результат — review, не pass. `run_id`
  детерминирован из idempotency key §7.6, поэтому одинаковый вход даёт
  байт-в-байт одинаковый bundle. Сценарии §15.3: четыре breaking seeds (formula,
  grain, event time, removed field) блокируются с точным impact до владельца и
  дашборда, presentation-only change не блокирует, прогон не открывает сокет и не
  зовёт ни один метод BI-клиентов.

- **Pure BI compile contracts** — `auto_bi/verification/bi_contract.py`:
  compile-only порт `BIContractCompiler` (§8.5) и реализации для Superset и
  DataLens, которые компилируют `DashboardSpec` в нативные payload'ы, не трогая BI.
  Второй реализации payload-логики нет — компиляторы зовут те же module-level
  функции, что и `BIAdapter.build()` (`build_form_data`, `build_position_json`,
  `build_native_filter_configuration`, `build_dataset_payload`,
  `build_chart_shared`, `build_selectors`, `build_dashboard_data`), и тест
  сравнивает объекты, а не результаты. Контракт content-addressed: payload'ы
  исключены из dump, digest артефакта считается JCS с `allow_float=True` (чужие
  JSON-документы), digest контракта адресует своё собственное тело. Build-time
  warning'и адаптеров стали типизированными findings: `bi.filter_unwired`,
  `bi.filter_partial_scope`, `bi.viz_degraded`, `bi.viz_unsupported`. Ноль remote
  mutation доказан socket-запретом и mutation-spy на каждом глаголе обоих клиентов.

- **`adapters/superset/derive.py`** — четыре model-производные входа
  `build_form_data` (`metric_labels`, `temporal_alias`, `heatmap_y_pad`,
  `human_label`/`measure_currency`) вынесены из `SupersetAdapter` в чистый модуль,
  чтобы адаптер и compile-контракт считали их одной функцией. Методы адаптера
  остались тонкими делегатами — поведение и v1-тесты не изменились.

- **Evidence writer и artifact store** — `auto_bi/evidence/store.py` (порт
  `ArtifactStore` + файловый адаптер с конфайнментом и отказом на symlink-компоненте)
  и `auto_bi/evidence/manifest.py` (`BundleWriter` по layout §9.2, `read_bundle`,
  `verify_bundle`). Одинаковый вход даёт байт-в-байт одинаковый манифест и digest;
  claim без артефакта падает до записи; restricted-payload не попадает в публичный
  манифест.

- **Verified assets и typed result comparator** — `auto_bi/verification/reconcile.py`
  (сравнение по §8.4 с тремя исходами: `equivalent`/`different`/`insufficient`;
  схема до значений, матчинг по business key, точный decimal, float только с
  объявленным tolerance, различение `NULL`/ноль/отсутствие строки/пустого
  результата, truncated не доказывает эквивалентность) и
  `auto_bi/verification/repository.py` (`VerifiedAsset` + репозиторий с
  инвалидацией по semantic diff: `asset.stale_snapshot`, `asset.metric_changed`,
  `asset.metric_removed`).

- **Ужесточение границ contract kernel** (по итогам adversarial-ревью) — `\Z`
  вместо `$` в рукописных regex (хвостовой `\n` больше не проходит в id, ref,
  digest, decimal и slug); JCS-слой печатает оба нуля как `0` и округляет большие
  целые к double по RFC 8785 §3.2.2.3, а negative zero отклоняет contract-граница;
  `EvidenceBundle.digest()` перевалидирует собранный bundle; целочисленные поля
  ограничены `2**53-1`; path confinement перешёл на whitelist сегментов (NUL,
  NTFS-ADS `name:stream`, хвостовые точки/пробелы, device-имена, bidi-override);
  `source_revision` отвергает `HEAD`/`main`/`latest`; decimal-строки нормализуются,
  `budgets: {}` ≡ отсутствие бюджетов; дубликаты в `scope.metrics`/`dimensions`
  запрещены схемой.

- **Semantic snapshot, diff и impact** — `auto_bi/semantic/gateway.py`
  (`NativeModelSource`: `model.yaml` + metric contracts → content-addressed
  `SemanticSnapshot` с явными `SemanticGap`), `auto_bi/semantic/diff.py`
  (классификация изменений по §8.3: removal и weakened policy → block,
  formula/grain/time/unit/additivity → review, label → advisory) и
  `auto_bi/decision/impact.py` (metric → dashboard → product → owner плюс
  эскалация review→block, когда от метрики зависит governed product).

- **`auto_bi decision validate`** — offline cross-contract проверка Git-tracked
  YAML: продукты резолвятся против metric contracts (`metric_ref.unknown`,
  `metric_ref.version_mismatch`, `metric_ref.duplicate`, `dimension.not_allowed`,
  lifecycle и `policy.mismatch`), без DWH/BI/LLM. Exit codes зафиксированы
  contract-тестом (`0` pass/advisory, `2` runtime-usage error, `3` needs_review,
  `4` block); `--json` печатает отчёт и ничего больше. Verdict считается из
  severity находок в `auto_bi/verification/model.py`.

- **DecisionOps contract kernel** — первый исполняемый слой нового control plane
  (`DECISIONOPS_ARCHITECTURE.md` §7, §9.1): `auto_bi/decision/spec.py`
  (`DecisionProductSpec`), `auto_bi/semantic/metric_contract.py` (`MetricContract`),
  `auto_bi/evidence/model.py` (`EvidenceBundle`/`Claim`/`EvidenceArtifact`) плюс
  shared kernel `auto_bi/refs.py` (stable id vs version-pinned ref) и
  `auto_bi/scalars.py` (decimal-строки, ISO-8601 duration, RFC 3339 UTC, `sha256:`).
  `auto_bi/evidence/canonical.py` — RFC 8785 JCS + SHA-256 content address, проверен
  на официальном векторном корпусе (`tests/fixtures/jcs`), а не на
  `json.dumps(sort_keys=True)`. Аддитивно: `DashboardSpec`, `BIAdapter` и схема Store
  не менялись.

- **Direct Mistral provider** — `AUTO_BI_LLM_PROVIDER=mistral` routes through
  Mistral Chat Completions with standard `MISTRAL_API_KEY` (or the
  `AUTO_BI_MISTRAL_API_KEY` alias), the shared structured-repair and budget
  hooks, token-usage logging, safe HTTP errors, and live-sentinel secret
  routing. Default model: `mistral-large-latest`.

- **Local BYOK runbook** — `docs/LOCAL_BYOK.md`: первый локальный запуск после
  clone со своим `ANTHROPIC_API_KEY` (ClickHouse + Superset через Compose,
  `uv run auto_bi serve`, health/ready, остановка и troubleshooting).

### Security

- **`FilesystemArtifactStore` проверяет и сам run id** — конфайнмент путей внутри
  bundle был укоренён *в* каталоге run'а, поэтому проверял всё, кроме имени самого
  каталога: `run_id` вида `../..` сдвигал корень, и любой путь внутри резолвился
  «правильно» уже совсем не туда. Теперь run id обязан быть одним обычным именем
  (той же формы, что `scalars.Slug`, которой манифест его и объявляет). До появления
  `decision evidence --run` все вызывающие были внутренними, так что известного
  вектора не было — закрыто до того, как значение стало приходить снаружи.

## [0.5.0] - 2026-07-29

### Fixed

- **Closing CI defects** — Superset 4.1.2-compatible exact ownership marker;
  auth response bodies removed from logs; capability-driven Auto-table loading
  deferred until authentication.

- **RR-4 pre-return BI crash window** — schema v9 durably records a
  fingerprint-verified build attempt before `BIAdapter.build`, and server startup
  reconciles it through the owning adapter before the legacy stuck-build reaper.
  Superset cleanup requires exact full-token chart/dashboard metadata (datasets use
  exact names); DataLens removes only exact canonical/`__wip` entries and never shared
  connections. Attempt commit, build row, session and ownership ledger are one success
  transaction; failed cleanup remains retry-blocking.

- **Единый Black gate** — pre-commit Black обновлён с 24.10.0 до 26.5.1,
  совпадающей с проектным `uv`-окружением. Один и тот же файл больше не
  форматируется противоположно локальным gate и commit hook.

- **plan_sol шаг 13 reaudit R1** — SafeError leak-canary pipeline test used pre-step-7
  `adapter.build(spec)` signature; `TypeError` mapped to `internal.error` instead of
  proving `SupersetAPIError` → `bi.http_error` store channel. Fake updated to
  `build(spec, ctx=None)` + contract methods.

### Added

- **Final closure gates** — package-wide `mypy --strict auto_bi` on Python
  3.12/3.13; cumulative Linux mutation evidence 1034/1034 with no weak outcomes;
  closing-SHA CI runtime-evidence artifacts; Settings/architecture/mutation
  docs-as-code ratchets.

- **plan_sol шаг 12 residual: mypy-strict adapter contract boundary** —
  `auto_bi/adapters/base.py` добавлен в строгий CI allowlist на Python 3.12 и
  3.13; shared adapter types, contract validation и lifecycle protocol теперь
  проверяются с `mypy --strict`.

- **plan_sol шаг 12 residual: mypy-strict Store boundary** —
  `auto_bi/store/db.py` добавлен в строгий CI allowlist на Python 3.12 и 3.13;
  durable session/build/artifact persistence теперь проверяется с
  `mypy --strict`.

- **plan_sol шаг 12 residual: mypy-strict pipeline boundary** —
  `auto_bi/agent/pipeline.py` добавлен в строгий CI allowlist на Python 3.12 и
  3.13; compile/build и ownership-cleanup orchestration теперь проверяются с
  `mypy --strict`.

- **plan_sol шаг 12 residual: mypy-strict session boundary** —
  `auto_bi/api/sessions.py` добавлен в строгий CI allowlist на Python 3.12 и
  3.13; session snapshot, restart hydration и registry contracts теперь
  проверяются с `mypy --strict`.

- **Project closure contract** — `docs/PROJECT_CLOSURE.md` фиксирует финальный
  v1 scope, переводит open-ended residual в явные `future` / `retired` /
  `won't-run` и отделяет их от обязательных publish/release/deploy evidence
  gates. `docs/CURRENT_STATE.md` больше не представляет бессрочное
  улучшательство как активный backlog.

- **plan_sol шаг 12 residual: mypy-strict dataset-plan boundary** —
  `auto_bi/agent/dataset_plan.py` добавлен в строгий CI allowlist на Python 3.12
  и 3.13; dataset ownership и native-filter planning теперь проверяются с
  `mypy --strict`.

- **plan_sol шаг 12 residual: mypy-strict query-plan boundary** —
  `auto_bi/agent/query_plan.py` добавлен в строгий CI allowlist на Python 3.12 и
  3.13; compiled query plan и runtime probe contracts теперь проверяются с
  `mypy --strict`.

- **plan_sol шаг 12 residual: mypy-strict SQL guard boundary** —
  `auto_bi/agent/sql_guard.py` добавлен в строгий CI allowlist на Python 3.12 и
  3.13; SELECT-only, complexity и table-access guard теперь одновременно
  защищены mutation и static-typing gates.

- **plan_sol шаг 12 residual: mypy-strict dbt import boundary** —
  `auto_bi/semantic/dbt_import.py` добавлен в строгий CI allowlist на Python
  3.12 и 3.13; dbt artifact mappings получили параметризованный `JsonObject`
  contract без изменения merge policy.

- **plan_sol шаг 12 residual: mypy-strict prompt-data boundary** —
  `auto_bi/semantic/prompt_data.py` добавлен в строгий CI allowlist на Python
  3.12 и 3.13; sample classification и prompt-data policy теперь проверяются с
  `mypy --strict`.

- **plan_sol шаг 12 residual: mypy-strict semantic rendering boundary** —
  `auto_bi/semantic/render.py` добавлен в строгий CI allowlist на Python 3.12 и
  3.13; deterministic prompt rendering теперь проверяется с `mypy --strict`.

- **plan_sol шаг 12 residual: mypy-strict semantic selection boundary** —
  `auto_bi/semantic/select.py` добавлен в строгий CI allowlist на Python 3.12 и
  3.13; deterministic context selection теперь проверяется с `mypy --strict`.

- **plan_sol шаг 12 residual: mypy-strict semantic-model boundary** —
  `auto_bi/semantic/model.py` добавлен в строгий CI allowlist на Python 3.12 и
  3.13; semantic schema и lookup helpers теперь проверяются с `mypy --strict`.

- **plan_sol шаг 12 residual: mypy-strict IR schema boundary** —
  `auto_bi/ir/spec.py` добавлен в строгий CI allowlist на Python 3.12 и 3.13;
  DashboardSpec schema и alias helpers теперь проверяются с `mypy --strict`.

- **plan_sol шаг 12 residual: mypy-strict IR validation boundary** —
  `auto_bi/ir/validate.py` добавлен в строгий CI allowlist на Python 3.12 и 3.13;
  semantic-model/spec validation теперь проверяется с `mypy --strict`.

- **plan_sol шаг 12 residual: SQL guard mutation gate** — `mutmut 3.6.0`
  ограничен `auto_bi/agent/sql_guard.py`; CI отклоняет выжившие, непокрытые,
  пропущенные, suspicious, timed-out и crashed mutants. Evidence run уничтожил
  все 106 сгенерированных мутаций.

- **plan_sol шаг 12 residual: mypy-strict Settings boundary** —
  `auto_bi/config.py` добавлен в строгий CI allowlist на Python 3.12 и 3.13;
  security-sensitive defaults и детектор опечаток `AUTO_BI_*` теперь проверяются
  с `mypy --strict`.

- **plan_sol шаг 12 residual: mypy-strict error boundary** —
  `auto_bi/errors.py` добавлен в строгий CI allowlist на Python 3.12 и 3.13;
  SafeError, redaction и provider mapping теперь проверяются с `mypy --strict`.

- **plan_sol шаг 12 residual: Superset native-filter metamorphic suite** —
  детерминированные перестановки SOURCE/OWN-чартов и mart/joined/unwired-фильтров
  проверяют эквивалентность `participating_chart_ids` и скомпилированных
  `chartsInScope`, а также точное разбиение каждого placement на scope/excluded.

- **plan_sol шаг 12 residual: RBAC property suite + mypy-strict start** —
  `tests/test_property_quality.py`: schema membership, `filter_model_by_schemas`
  join consistency, forbidden-set monotonicity, raw_sql RBAC hatch. CI
  `Mypy strict (boundary modules)` for `auth.py` + `adapters/artifacts.py`
  (`mypy --strict`); allowlist documented in `docs/operations/SLO.md`.

- **plan_sol шаг 8 residual: stable build_token idempotency** —
  `stable_build_token(session_id, spec_id)`; `compile_and_build` reuses an already
  delivered dashboard (`ok` / `delivered_pending`) for the same durable revision
  instead of calling `adapter.build` again. Failed attempts with the same token
  may rebuild. Without a known `spec_id` (or store specs), namespace stays random
  so multi-build orphan/prune tests keep distinct revisions.

- **plan_sol шаг 10 residual: fields drag-drop E2E** —
  Offline browser test `test_fields_drag_drop_seed_to_approve`: HTML5 drop into
  seed groups, click-fallback field add, multi-group, seed submit → propose →
  build (ScriptedLLM, no paid LLM). Step 10 offline E2E matrix complete (5 cases).

- **plan_sol шаг 10 residual: browser UI resume after reload** —
  `sessionStorage` key `auto_bi.session_id`; `resumeSession()` restores chip /
  build panel / approve button from `GET /sessions/{id}`. `SessionState` /
  `SessionSnapshot` carry optional `spec` for IR preview re-render. Offline E2E
  `test_browser_reload_resumes_built_session`. Logout clears stored id. Chat
  transcript not rehydrated in the browser (server Store remains SoT for dialogue).

- **plan_sol шаг 13 (offline adversarial re-audit core)** —
  Evidence doc `docs/operations/REAUDIT_plan_sol_23_07_26.md` (score 8.8/10 offline;
  gates: ruff/mypy/hygiene, security suite, advisor 9+6, golden 37+16, rulesets active,
  restore-drill, docs ratchets). Residual: live GHA/Space/tag, mutmut, full cov run.

- **plan_sol шаг 12 (SLO / recovery core)** —
  `Store.backup_to` + `integrity_check` (online SQLite backup); operator script
  `scripts/store_backup.py` (backup / check / restore-drill); unit restore drill
  `tests/test_store_backup.py`. Offline performance baseline with soft absolute
  ceilings and large/small ratios (`tests/test_perf_baseline.py`); property/
  metamorphic tests for SQL guard, IR validate, normalize idempotence
  (`tests/test_property_quality.py`). Ops: `docs/operations/SLO.md`, DEPLOYMENT §7
  points at the script. Residual: live p50/p95, mutmut score, mypy-strict modules.

- **plan_sol шаг 11 (docs-as-code closed core)** —
  `docs/CURRENT_STATE.md` as the single current-status entry point; generated
  `docs/ENV_REFERENCE.md` from `Settings` (`scripts/generate_env_reference.py`,
  `--check` mode); `tests/test_docs_as_code.py` (env-ref freshness, `.env.example`
  covers every Settings key, internal markdown link resolve, golden/advisor counts
  ↔ 53 fixtures). USER_GUIDE §6 / README / PLAN / ARCHITECTURE and the former root
  plan (now [archived](docs/history/AUTO_BI_IMPLEMENTATION_PLAN.md)) point at
  CURRENT_STATE + ENV_REFERENCE. Follow-up closes the remaining docs
  polish: `ARCHITECTURE.md` is current design, the original delivery narrative
  lives in `ARCHITECTURE_HISTORY.md`, and all Settings keys carry
  `Field(description=...)` metadata used by the generated reference.

- **plan_sol шаг 10 (mostly closed) + шаг 11 start** —
  Step 10: CI `quality-py-latest` (3.13), `windows-cli-smoke`, compatibility matrix
  test, live-stand E2E auth/viewport; **offline browser E2E** job
  `Browser E2E (offline, no stand)` — text path via ScriptedLLM, failed-build retry,
  SSE late-connect replay (`tests/test_web_e2e_offline.py` + helper subprocess).
  No paid LLM. Residual was UI resume + fields DnD; both **closed** offline (see
  Unreleased Added). Residual: live GHA after merge.
  Step 11 start: fixed USER_GUIDE audit falsehoods (session owner-bound, Anthropic
  token usage, work quota on auto, empty DataLens password default); `tests/test_docs_defaults.py`
  (Settings defaults without local `.env`, version, CLI subcommands, `.env.example`);
  PR template docs checklist expanded.

### Changed

- **Final dependency refresh** — coordinated dependency refresh and
  clickhouse-connect 1.6.0 lock/Docker pins.

- **plan_sol шаг 9: replay/live eval trust** — golden fixtures format v2 with
  `prompt_sha256`, `template_version`, `schema_version`, `provider`, `model_id`.
  Replay raises `FixtureStaleError` on contract mismatch (not silent reuse).
  Thresholds split honestly: offline replay **100%**; live clear ≥ 80%.
  CLI: `--llm-mode refresh-fingerprints` (offline stamp). CI: Greenplum advisor
  suite; path-filtered live sentinel workflow (3 cases, budget cap, skip without
  secrets). Procedure: `docs/EVAL_FIXTURES.md`. All 53 fixtures refreshed offline.

- **plan_sol шаг 8: atomic build state (core)** — after `adapter.build` returns,
  build row + session status + ownership ledger commit in **one SQLite transaction**
  (`Store.commit_build_success`). Failure path is atomic too (`commit_build_failure`).
  Ledger/commit fault after BI delivery never marks the build failed: durable
  `builds.status=delivered_pending` + `sessions.status=built_with_cleanup_degraded`
  and the DashboardRef is still returned (audit P1-2 split-brain closed). Schema v8:
  `builds.build_token`. API: immutable `SessionSnapshot` under lock; terminal
  success/failure apply status + `dashboard_url` + SSE together
  (`apply_build_success` / `apply_build_failure`). Startup:
  `reconcile_pending_ledgers` audit traces. Residual: durable outbox *before*
  adapter return; stable build_token idempotency from approve. Tests:
  `tests/test_session_snapshot.py`, store/pipeline fault-injection.

- **plan_sol шаг 7: formal BI adapter contract** — `BuildContext` / `BuildResult` in
  `auto_bi/adapters/base.py`; Protocol requires `build(spec, ctx=None) -> BuildResult`,
  `delete_artifact`, `close`. Pipeline passes namespace+PlanCache via context and
  records ledger from `BuildResult.artifacts` (no getattr). Factory
  `validate_adapter_contract` at `make_adapter`. ADR:
  `docs/adr/0001-bi-adapter-contract.md`. Contract suite:
  `tests/test_adapter_contract.py` (fake third adapter). Deprecated set_*/drain_*
  remain on concrete adapters for unit tests only.

### Security

- **Release supply-chain hardening** — immutable SHA pins for GitHub Actions;
  Gitleaks v3 exact-fingerprint policy; provenance action v4; release-image
  Python compatibility ratchet.

- **plan_sol шаг 6 (partial): reproducible release promotion** —
  (1) Demo/HF: `uv.lock` в Space whitelist; `deploy/hf-demo/Dockerfile` —
  `uv sync --frozen`; `clickhouse-connect==1.6.0` + Superset digest pin в
  demo и `docker/superset/Dockerfile` (согласовано с `uv.lock`).
  (2) `release.yml`: `image-security` = local build → Trivy **до** push →
  image SBOM → push только `:version` → attest; `finalize` (после image +
  pypi + provenance) = `:latest` + GitHub Release (source + image SBOM);
  `release-status` ловит partial. (3) `demo-image.yml` path-triggers на
  demo graph / lock / workflows. (4) CI job `dependency-resolution`
  matrix: locked / latest-compatible / lowest-direct smoke.
  Tests: `tests/test_release_promotion.py`. Residual: live tag dry-run;
  partial PyPI-without-image still possible (wheel unpublish manual).

- **plan_sol шаг 5: GitHub checks scaffolding + live apply** —
  `.github/CODEOWNERS`, PR template, Dependabot groups, stable `gitleaks`
  check-run name, coverage-badge push soft-skip.
  `scripts/apply_github_protection.py` applied (operator gate 2026-07-23):
  main ruleset `19601118` (PR required, 6 status checks, conversation
  resolution, no force-push/delete), tag ruleset `19601122` (`v*`: no
  update/delete/non-ff), Dependabot security updates **enabled**. Payload fix:
  `dismiss_stale_reviews_on_push` (API rejected `dismiss_stale_reviews`).
  Residual: solo `pypi` prevent_self_review=false; open Dependabot PRs not
  bulk-merged; direct push to main is blocked — use PRs.

- **plan_sol шаг 4: Compose loopback + validated profiles** —
  `docker-compose.yml` публикует ClickHouse/Superset только на `127.0.0.1`
  (LAN-соседи больше не видят local-only defaults). Внешняя публикация —
  явный override `docker-compose.publish.yml` с обязательными секретами
  (compose `:?` fail). `AUTO_BI_PROFILE=local|demo|production` валидирует
  комбинации до bind: production требует auth, secure cookie, strict BI
  fingerprint, `SEND_SAMPLES=false`, non-default secrets, resource limits,
  retention; demo — auto-only или text+LLM-ready+quota. Код:
  `auto_bi/deployment_profile.py`; матрица — `tests/test_deployment_profile.py`.
  Док: `docs/DEPLOYMENT.md` §2.

- **plan_sol шаг 3: SafeError + центральная редактиру** — публичные HTTP/SSE,
  durable Store (builds/trace) и structured logs больше не несут raw provider
  bodies, DSN, Authorization/cookies/API keys. Новый `auto_bi/errors.py`:
  `SafeError` (code, public_message, retryable, correlation_id,
  internal_detail, provider_status/class), `redact_secrets`,
  `to_safe_error` / `store_error_text` / `public_error_text`,
  `SecretRedactFilter` на root logging. BI-клиенты (Superset/DataLens) не
  кладут `response.text` в exception message (только HTTP status; body в
  debug-log после redact). `/ready` отдаёт boolean + stable
  code/message/correlation_id без raw health text. Leak-canary:
  `tests/test_safe_error.py` (+ обновлены ready/api/pipeline/machine).

- **plan_sol шаг 2: prompt data boundary / samples opt-in** — default
  `AUTO_BI_SEND_SAMPLES=false` (раньше `true`): clean install не отправляет
  top-N значений DWH внешнему LLM. Classification на table/column
  (`public`/`internal`/`confidential`/`restricted`); samples только при
  explicit opt-in и только для public/internal. Free-text (description,
  synonyms, top-values) санитизируется и рендерится в untrusted-envelope.
  Лимиты: ≤10 values/column, ≤64 chars/value. Интроспектор пишет уже
  sanitized samples в model.yaml. Миграция: `docs/MIGRATION_SEND_SAMPLES.md`.
  Leak-canary: `tests/test_prompt_data.py`.

### Added

- **plan_sol шаг 1: стабильный публичный demo-профиль** — `/health` отдаёт
  `capabilities` (auto_overview / text_session / fields_session / word_edit /
  enrichment / llm_wired) из реальной wiring, не только из флага; UI баннер +
  disable вкладок по capabilities; `AUTO_BI_REQUIRE_LLM_READY` (в
  `start-autobi.sh` forced true при text-mode) не даёт процессу стартовать с
  text-профилем при мёртвом LLM; `deploy/hf-demo/assert_demo_profile.py` —
  post-deploy / CI smoke (flag + capabilities + 403 на text/fields/enrichment);
  demo-image workflow вызывает assert-скрипт.

### Fixed

- **DataLens: LIMIT-drop scope = семантика селекторов DataLens, не роли Superset** —
  `selector_scope_chart_ids` (bare per-chart alias по grain, зеркалит правило
  `build_selectors`) вместо `participating_chart_ids`: OWN-размеченный чарт по
  joined-колонке селектор реально двигает (у DataLens нет shared source-датасета),
  а его SQL держал top-N LIMIT → селектор фильтровал пре-обрезанный top-N.
  Тест согласованности: LIMIT-drop set == scope `build_selectors`.

- **OWN-only дашборд больше не теряет joined-фильтр** (бэклог D-1): binding контрола
  решается один раз на спеку (`dataset_plan.filter_binding_alias`) — source-alias
  (`stores_name`), когда фильтр берёт хоть один source-датасет; bare pre-D-1 alias,
  когда все чарты OWN (контрол биндится к OWN-датасету первого in-scope чарта, чарты
  с колонкой в grain остаются в scope). Превью и Superset-wiring читают binding из
  одной функции (`_filter_target_alias` удалён как вторая аппроксимация). Заодно
  blanket-бейдж «фильтр не влияет» больше не ставится чарту, который контрол реально
  двигает (превью не врёт в обе стороны). Mixed-дашборды не меняются: alias-mismatch
  исключение и честная пометка остаются. Мутационно проверено.

### Added

- **D-2 §5: reuse LIMIT-trial rows for magnitude + CLI insights** — `PlanCache` хранит
  trial-строки guard'а (параллельно plan entries; `complete = len(rows) < TRIAL_LIMIT`).
  OWN-чарты Superset и все чарты DataLens берут magnitude из complete trial вместо
  `/chart/data` / `/api/run`; SOURCE на Superset оставляют probe (после D-1 raw mart
  trial ≠ агрегат). CLI `analyze_spec(..., plans=)` переиспользует trial; `serve
  /insights` без изменений. `compile_and_build` создаёт build-local cache при
  `plans is None` и передаёт его через `set_query_plans`. ARCHITECTURE §3.19.

- **D-1 PR-3: живая приёмка filterable dataset + ARCHITECTURE** — integration-тест
  `tests/test_d1_acceptance.py` (маркер `integration`, деселектнут offline-lane):
  build дашборда KPI + monthly trend + bar по joined `dm.stores.name` + ratio-мера
  через реальный pipeline, затем `POST /api/v1/chart/data` с `time_range` и
  `filters` на `stores_name` (то, что эмитят native-фильтры) — числа сверяются
  с независимыми агрегатами ClickHouse. Soft-collect независимых чеков (period +
  store + 5 declared assumptions rounds 1–2). Offline — payload-shape asserts
  (roles/form_data/nfc/extra_form_data/orderby). CI integration-job гоняет suite
  рядом с contract. Документация: `docs/ARCHITECTURE.md` §3.20.

- **D-1 PR-2: filterable dataset (Superset, вариант A)** — один shared semantic-grain
  датасет на витрину для выразимых чартов (SOURCE), агрегация в form_data adhoc-метриках
  (SUM/AVG/COUNT… по сырой колонке, KPI без «одной строки», `time_grain_sqla` вместо
  toStartOf* в SQL, ratio как `sqlExpression`), scope native-фильтров = чарты на source-
  датасете, qualified-сравнение колонок (фикс `dm.products.name` vs `dm.stores.name`),
  preview-пометка «фильтр не влияет: …» для OWN/fallback-чартов в turn.notes и spec-pane
  UI. Невыразимые (window/compare/raw_sql/bins) остаются на per-chart датасете. Живая
  приёмка и ARCHITECTURE — PR-3 (закрыто).
  **Review fixes (round 2):** (1) один общий `chart_accepts_filter` в `agent/dataset_plan`
  для preview и wiring (joined-label фильтр больше не «не применим» в summary);
  (2) детерминированные уникальные alias joined-ref (`dm.stores.name` → `stores_name`)
  в source SQL / form_data / filter target + loud collision на plan time; OWN в scope
  фильтра только если alias датасета = bound; (3) magnitude-probe для SOURCE считает
  `SUM("revenue")` (и groupby+order desc для tallest point), не `MAX("sum_revenue")`;
  (4) D-1 source-once SQL-gating только для `target_bi=superset`, DataLens — per-chart.

- **Браузерный E2E веб-UI + axe-скан** (D-4): `tests/test_web_e2e.py` — Chromium
  (Playwright) проходит путь пользователя против настоящего `auto_bi serve` в
  demo-профиле (DisabledLLM — без ключа и трат) и живого ClickHouse+Superset:
  Авто-обзор → превью спеки → сборка → SSE-лог → ссылка на дашборд, с axe-core-сканом
  каждого состояния UI. Гоняется в integration-job CI на каждом PR; маркер `e2e`
  деселектнут по умолчанию. Первая же находка скана починена: на странице не было
  `<h1>` — бренд в шапке стал заголовком первого уровня без визуальных изменений.

- **Retention-свип store** (D-3, аудит 2026-07-18): `Store.purge_retention` подрезает по
  возрасту три таблицы, которые росли без ограничений — `llm_calls` (строка на вызов
  провайдера), `trace_events` (строка на шаг пайплайна) и **не-`live`** строки
  `bi_artifacts` (прошлые ревизии от пересборок). До этого свипались только `auth_tokens`.
  Выключено по умолчанию (`AUTO_BI_RETENTION_ENABLED=false`) — удаление операционной
  истории необратимо; сроки настраиваются по таблицам, `0` = хранить вечно. Свип идёт на
  старте `serve` и раз в `AUTO_BI_RETENTION_SWEEP_HOURS`. **Не трогает** `sessions`/
  `messages`/`specs`/`builds` (работа пользователя, не телеметрия) и `live`-строки леджера
  (по ним живёт ownership-cleanup — их удаление осиротило бы реальный дашборд в BI).
- **Prometheus-метрики** (D-3): `GET /api/v1/metrics` в text exposition format, opt-in
  через `AUTO_BI_METRICS_ENABLED`. Билды по статусу и in-flight, слоты сборки, DWH-проходы
  и суммарное время в них, LLM-вызовы/токены/секунды по моделям, `auto_bi_llm_cost_usd_total`
  по той же прайс-таблице, что и бюджетный guard (`llm.budget.cost_usd` — общая функция,
  незалистанная модель тарифицируется в 0), и `auto_bi_store_rows{table}` — по ней видно,
  работает ли retention. Цифры глобальные, поэтому при включённом auth эндпоинт
  **admin-only**; выключенный отвечает 404, а не 403. Рендер — свой (модуль 174 строки), без
  зависимости `prometheus_client` с её глобальным реестром и default-коллекторами.

### Changed

- **Общий план запроса вместо двух EXPLAIN одного стейтмента** (D-2 §3, ядро пункта 21):
  guard и Advisor планировали SQL чарта каждый сам — `EXPLAIN` и `EXPLAIN ESTIMATE` подряд
  по одному и тому же запросу. Теперь на одношаговых путях сборки (`auto_bi build`,
  `build --auto`) обе стороны делят `PlanCache` (`agent/query_plan.py`) на один вызов
  сборки: Advisor записывает результат своего плана, guard читает и пропускает собственный
  `EXPLAIN`, если этот же стейтмент уже спланирован без ошибки. **Замер приёмки на
  8-чартовом auto-overview: 25 → 17 проходов в DWH, 3,1 → 2,1 на чарт.**
  Ключ — **точный текст SQL**: Advisor судит effective query (P1-2) по до-нормализационной
  спеке, а guard — после-нормализационную, и они расходятся, как только срабатывает B3
  label-join или контрол с дефолтом (для auto-overview совпадают все 8 чартов, для
  LLM-спеки с FK-измерением — нет). Промах остаётся промахом: переиспользование evidence
  между разными стейтментами вернуло бы Advisor'у замер запроса, который BI не выполняет.
  Инвариант 3 не тронут — `guard_sql` и LIMIT-прогон безусловны, а попадание в кэш значит,
  что DWH уже разобрал и проверил права на этот самый стейтмент; `ok` выводится
  консервативно (пустой ESTIMATE = промах). API-путь approve кэш не получает (preview и
  build — разные запросы), поведение там прежнее. ARCHITECTURE §3.19.

### Fixed

- **Гонка `Store.close()` с фоновым build-тредом**: store делит одно sqlite3-соединение
  между тредпулом API и daemon-тредом сборки под внутренним локом — кроме `close()`,
  который закрывал соединение без лока. Тред сборки пишет финальный `build_done`-trace
  ПОСЛЕ того, как клиент уже увидел done-событие по SSE, поэтому close на teardown мог
  попасть в середину `execute` и уронить интерпретатор в C-слое sqlite3 (падение
  offline-CI 20.07, exit 139 внутри `add_trace_event`). Теперь `close()` берёт тот же
  лок: незавершённый стейтмент дописывается, опоздавший писатель получает чистый
  `sqlite3.ProgrammingError`, который трейсинг сборки и так глотает. Оба поведения
  закреплены тестами (мутационная проверка: без лока тест падает).
- **Доступность веб-UI** (D-5, п.24): три дефекта, замеренные в браузере, а не на глаз.
  (1) **Контраст**: `--text-3` был `#9aa1ab` = **2,41:1** на `--bg` при требовании WCAG AA
  4,5:1 — и им набраны **26 селекторов** текста 10–15px, включая ярлыки вкладок и заголовки
  панелей; теперь `#636b77` (4,7–5,4:1 на всех трёх фонах). `--text-2` заодно затемнён до
  `#4f5766`, чтобы три уровня текста остались различимы, а не слились в один серый.
  (2) **Метки**: поля логина, запроса к агенту и комментария к раскладке опознавались
  только по `placeholder` — он не метка (исчезает при вводе). Добавлены настоящие `label`
  (визуально скрытые там, где по дизайну нет места), заголовок формы входа стал `h2`.
  (3) **Живые области**: сборка идёт минутами и пишется по SSE, но не объявлялась вообще
  (`aria-*` в `app.js` не было ни одного) — лог сборки получил `aria-live="polite"`,
  результат и ошибка входа — `role="alert"`, чат — `role="log"`, чип сессии — `role="status"`.
  Плюс вкладки режима доведены до паттерна WAI-ARIA: `aria-controls`/`role="tabpanel"`,
  roving `tabindex` и навигация стрелками/Home/End (без неё до вкладок «Полями»/«Авто» с
  клавиатуры было не добраться). Проверено в браузере: 0 нарушений контраста и 0 контролов
  без доступного имени из 20; возврат прежнего цвета немедленно даёт 20 нарушений.
  Закреплено статическим тестом (`tests/test_ui_a11y.py`), который сам проверен на
  заведомо плохом вводе.
- **Lifecycle httpx-клиентов** (D-2 §4, дизайн-док 2026-07-19 — независимая часть, взята
  отдельным PR): адаптеры создаются на каждый билд (`make_adapter` внутри
  `compile_and_build`) и на каждую readiness-пробу (`/ready`), но их HTTP-пулы никогда не
  освобождались — в `serve` пулы копились до сборки мусора, по одному на билд и на каждый
  пинг `/ready` (демо-keepalive пингует его постоянно). Теперь: у обоих адаптеров есть
  `close()` (concrete-хелпер, `BIAdapter`-Protocol не тронут — S4, как
  `drain_build_artifacts`); `compile_and_build` освобождает адаптер в `finally` на любом
  исходе (после леджера/прунинга; сбой самого close логируется и никогда не маскирует
  результат билда); `/ready` ходит через `probe_health` (одноразовый адаптер закрывается
  тут же); `auto_bi prune` закрывает адаптер каждого таргета. `GraceKellyClient` получил
  отсутствовавший `close()`, `AnthropicClient` теперь удерживает SDK-клиент (а не только
  bound `messages.create`) и закрывает его пул; `serve` освобождает LLM-клиент при
  остановке. Закрытие в pipeline и probe_health закреплено мутационно-проверенными тестами.
- `validate_spec` отклоняет коллизию SELECT-алиасов между мерой и размерностью: label меры,
  совпавший с bare-именем колонки-измерения (`label="store_id"` рядом с
  `dimensions=["store_id"]`), давал датасету две колонки под одним именем — какую из них
  возьмёт агрегат BI, не определено, чарт тихо показывал неверные числа. Проверки коллизий
  «внутри мер» и «внутри размерностей» уже были; закрыт третий случай (свободный аудит
  2026-07-19, находка 2).

## [0.4.0] - 2026-07-18

### Added

- Гейт качества тестов (D-4-минимум, аудит 2026-07-18): CI падает при покрытии <90%
  (`--cov-fail-under=90`; фактическое ~95%), warnings в тестах = ошибки
  (`filterwarnings = ["error"]` c одним явным allowlist-исключением на сторонний
  starlette/httpx2-deprecation, который из кода auto_bi не чинится).
- BI-artifact ownership **live-cleanup ВКЛЮЧЁН** (P0-2, критерий 4, надстройка над OFFLINE-леджером
  ниже; live-проверен на 20M-стенде 2026-07-18). Два пути через один движок удаления
  `prune_artifact_rows` (delete-by-id, порядок `chart → dashboard → dataset` — датасет не
  удаляется, пока его читает чарт; `SHARED_BI_KINDS` пропускаются). (1) **Авто-прунинг на
  ребилде:** после успешного билда И записи в леджер `compile_and_build` зовёт
  `_prune_superseded_artifacts` — удаляет BI-артефакты ПРОШЛЫХ ревизий ЭТОЙ сессии по
  `native_id` (селекция `orphan_bi_artifacts`: ключ session/owner/build_token, **НИКОГДА** имя/
  title; только что доставленный дашборд несёт текущий `build_token` и не трогается). Прунинг
  **НИКОГДА не валит билд**: дашборд уже доставлен, любая ошибка логируется, строки остаются
  `live` и повторяются следующим прунингом. Выключатель `AUTO_BI_PRUNE_ON_REBUILD=false`
  (`Settings.prune_on_rebuild`, дефолт `true`, прокинут параметром `prune_orphans` в
  `build_dashboard`/`compile_and_build`). (2) **Операторская команда** `auto_bi prune
  [--session ID] [--dry-run] [--model-path …]` — селекция `Store.stale_bi_artifacts`: живые
  строки билдов, которые НЕ являются последним билдом своей сессии (последний дашборд каждой
  сессии всегда переживает прунинг — удаляются ревизии, не чужие дашборды). `--dry-run` печатает
  кандидатов и не удаляет; недоступный BI-таргет → его строки пропускаются; exit 0 при чистом
  прогоне, 1 если что-то не удалось или было пропущено. `delete_artifact` — новый concrete-хелпер
  обоих адаптеров (Superset `_DELETE_PATHS` / DataLens `_DELETE_SCOPES`; **вне** `BIAdapter`-
  Protocol, как `drain_build_artifacts`/`set_artifact_namespace`): удаляет одну сущность по
  native_id, 404 = уже удалена (норм), любой другой сбой → строка остаётся `live`; shared-kind
  (`database`-connection) отвергается и в SQL-селекции, и самим адаптером. `stale_bi_artifacts`
  добавлен в Store, `mark_bi_artifacts_superseded(ids)` теперь используется обоими путями.
  IR/`BIAdapter`-Protocol/инварианты 1–8 не тронуты (ARCHITECTURE §3.17).
- BI-artifact ownership ledger + orphan-cleanup SELECTION (P0-2, критерий 4, **OFFLINE-слой**):
  таблица Store `bi_artifacts` (`id`, `session_id`, `build_token` = build-namespace = ревизия,
  `target_bi`, `kind` = `database|dataset|chart|dashboard`, `native_id`, `name` — display/debug,
  `owner`, `schema_set` — DWH `schema.table` для RBAC, `status` = `live`, `created_at`). Оба
  адаптера (Superset и DataLens) при `build()` накапливают созданные сущности и отдают их новым
  concrete-методом `drain_build_artifacts()` (**НЕ** в `BIAdapter`-Protocol, как
  `set_artifact_namespace`); оркестратор `compile_and_build` после успешного билда сливает их и
  пишет в леджер с session/owner/build_token (`owner` — из `sessions.owner`, NULL при auth off;
  `schema_set` датасета/чарта — из `query.table`). Выбор кандидатов на чистку —
  `orphan_bi_artifacts(session_id, current_build_token, *, owner=None)`: живые строки сессии с
  `build_token != текущего` (опц. RBAC по `owner`), ключ — **ВЛАДЕНИЕ** (session/build_token),
  **НИКОГДА** не имя/заголовок (два дашборда могут делить техническое имя — та самая ловушка
  `_delete_if_exists`-по-имени DataLens, ради которой леджер и существует).
  `mark_bi_artifacts_superseded(ids)` — шов для будущей чистки, пока не используется. Селекция
  исключает `SHARED_BI_KINDS` (`database`-connection, идемпотентен-по-имени и общий между
  билдами) **по умолчанию** — выдача безопасна для delete-by-id как есть; `include_shared=True`
  даёт полный аудит-вид. Таблица добавлена как always-run `CREATE IF NOT EXISTS` + индексы,
  **БЕЗ** bump'а версии схемы (как budget-индексы `llm_calls`). **Ничего живого не удаляется**
  (осознанный scope): сам продукт BI delete API не вызывает, `_delete_if_exists`/
  `_promote_to_canonical` DataLens не тронуты; полный цикл build → rebuild → orphan → Superset
  DELETE → superseded live-проверен на 20M-стенде 2026-07-18. IR/`BIAdapter`-Protocol/
  инварианты 1–8 не тронуты (ARCHITECTURE §3.5).
- Бюджет LLM на шве клиента (P0-3 item 4): `LLMBudget` (`llm/budget.py`) энфорсит лимиты по
  **вызовам / токенам / стоимости / времени** на **сессию** и на **актора / скользящие 24ч**
  (владелец сессии при auth on; единый глобальный бакет при auth off — предохранитель суммарного
  расхода анонимного демо). Проверка живёт в `complete_with_repair` (хук `on_attempt`) и срабатывает
  **перед каждой** попыткой, поэтому первичный вызов И каждый репэйр списывают бюджет; обойти шов
  нельзя. Fail closed: `BudgetExceeded(LLMError)` до вызова, который пересёк бы лимит, с указанием
  измерения. Токены реальные у Anthropic, оценка `chars/4` у GraceKelly; время — кумулятивная
  латентность; стоимость — таблица цен $/1000 токенов на модель (опционально). Агрегат берётся из
  существующего леджера `llm_calls` (без параллельной таблицы; переживает запросы/рестарты),
  индексы `ix_llm_calls_session`/`ix_llm_calls_created`. Конфиг `AUTO_BI_LLM_BUDGET_*` через
  `make_llm`. **Opt-in, по умолчанию выключен** (как session/work-квоты) — CLI/тесты/локалка не
  затронуты, LLM-демо включает явно (ARCHITECTURE §3.6, DEPLOYMENT §3). IR/`BIAdapter`/инварианты
  1–8 не тронуты.
- Release preflight (P1-7): в `release.yml` добавлен job `preflight`, который выполняется
  ПЕРВЫМ и валит весь релиз, пока не сойдётся всё разом — версия тега (`vX.Y.Z`) ==
  `pyproject.toml [project].version` == `auto_bi.__version__`; в `CHANGELOG.md` есть непустая
  секция `## [<версия>]` (а не только `[Unreleased]`); `uv build` даёт один связный sdist+wheel
  под этой версией; `twine check` проходит; чистая установка колеса в свежее окружение импортится
  и запускает консольный скрипт (`auto_bi --help`). Job'ы `release` (GHCR + GitHub Release) и
  `pypi` (Trusted Publishing) теперь `needs: preflight`, а `pypi` публикует РОВНО те артефакты,
  что собрал и проверил preflight (`upload-artifact`/`download-artifact`, без пересборки) —
  рассинхрон версий отклоняется до любой публикации, частичный релиз невозможен. Проверки
  когерентности и секции changelog вынесены в юнит-тестируемый `scripts/release_preflight.py`
  (`tests/test_release_preflight.py`); быстрый drift-гвард «pyproject == `__version__`» добавлен
  в обычный сьют, чтобы расхождение ловилось на каждом PR, а не только при теге.
- Supply-chain hardening релиза (P1-7 доп., GitHub-native, без внешних сервисов): (1)
  **SLSA build provenance** — новый job `provenance` в `release.yml` (`needs: preflight`)
  скачивает РОВНО те sdist+wheel, что собрал preflight, и подписывает их через
  `actions/attest-build-provenance` (Sigstore + GitHub attestation store); проверка постфактум —
  `gh attestation verify <файл> --repo brownjuly2003-code/Auto_BI`. Права выданы минимально на
  уровне job'а (`id-token: write`, `attestations: write`, `contents: read`), не workflow-wide.
  (2) **PEP 740 аттестации на PyPI** — у `pypa/gh-action-pypi-publish` под trusted publishing
  они включены по умолчанию (v1.11+, `@release/v1` уже трекает такую версию); выставлены
  `attestations: true` явно, чтобы будущий флип дефолта не уронил их тихо. (3) **SBOM** —
  `anchore/sbom-action` генерирует SPDX-JSON по исходному дереву (`pyproject.toml` + `uv.lock`)
  и файл прикладывается к GitHub Release ассетом (через `files:` у `action-gh-release`; событие
  тега = `push`, поэтому собственная выгрузка ассета у экшена не срабатывает — отключена во
  избежание лишнего workflow-артефакта). (4) **Environment-approval** — job `pypi` получил
  полную форму `environment:` с `url` на точную версию пакета на PyPI; включение required
  reviewers для окружения `pypi` (ручной аппрув каждой публикации) — настройка в repo settings,
  файл менять не нужно (как включить — `docs/DEPLOYMENT.md` §2). Аттестации/SBOM исполняются
  только на реальный push тега `vX.Y.Z` — первая живая проверка на следующем релизе.
- Escape hatch `raw_sql` (X-5): ручной SELECT для запросов, которые IR не выражает.
  `ChartQuery.raw_sql` (только `viz=table`) SQL_GEN отдаёт дословно, дальше — тот же
  live-гейт, что у сгенерированного SQL (`guard_sql` SELECT-only → EXPLAIN → LIMIT-прогон)
  → virtual dataset в Superset. Ручной люк (CLI `auto_bi raw --sql-file q.sql`), НЕ
  LLM-генерация — инвариант 1 сохраняется. Ров осознанно ослаблен: advisor слепнет,
  нормализации/форматы не применяются, колонки по модели не проверяются (ARCHITECTURE §3.16).
- Публичное демо: текстовый путь можно включить (`AUTO_BI_DEMO_AUTO_ONLY=false`) — `start-autobi.sh`
  подключает LLM-провайдер (по умолчанию GraceKelly `claude-sonnet-5` через публичный туннель в
  Space secrets) и принудительно включает per-IP session-квоту. По умолчанию демо остаётся
  auto-only (без LLM, нулевой бюджет).

- Semantic governance для rate/non-additive колонок (P1-6): поле `Column.additivity`
  (`additive | semi_additive | non_additive`; `semi_additive` записывается, но в v1 не
  энфорсится). Интроспекторы CH/GP размечают rate-подобные имена (`rate|ratio|pct|percent|share`,
  `price`/`unit_price`) как `agg: avg` + `non_additive`; валидация спеки отклоняет `sum` над
  `non_additive` колонкой (и в denominator ratio-меры) с подсказкой для repair loop;
  enrichment-API отвечает 422 на такой `agg`; autospec для неаддитивной меры без модельного agg
  падает в AVG и не строит share-of-total над ней; маркер рендерится в model_text для LLM.
  Committed-модели исправлены: `price` (model, model_stand) больше не суммируется
  (ARCHITECTURE §3.2).
- `Physical.captured_at` — UTC-штамп снятия статистики интроспектором: замороженные в git
  `rows`/`cardinality` теперь несут дату происхождения (P1-6, ARCHITECTURE §3.2).

### Security

- Контейнер-hardening (C-4, аудит 2026-07-18): продуктовый Dockerfile — non-root
  `USER app` (uid 1000, писабельны только `data/`+`logs/`) + `HEALTHCHECK` на
  `/api/v1/health` (stdlib urllib, без curl); базовые образы обоих Dockerfile
  (python-slim, uv, clickhouse 24.8, superset 4.1.2 — demo тянул `uv:latest`!)
  запинены по digest; все actions в пяти workflow запинены по commit-SHA (`# vX`
  комментарий держит человекочитаемость); CI docker-job теперь ЗАПУСКАЕТ образ
  (smoke: healthy + uid 1000 + user app); release.yml — SLSA-provenance на
  GHCR-образ (`attest-build-provenance`, push-to-registry) и Trivy-скан пушнутого
  образа (fail на HIGH/CRITICAL с доступным фиксом).
- Сканы в CI (C-5, аудит 2026-07-18): (1) job `dependency-audit` — pip-audit по
  залоченному набору зависимостей (`uv export --all-extras` с хэшами, `--require-hashes`;
  локальный прогон: 0 CVE); (2) workflow `codeql.yml` — статанализ python на PR/main/
  еженедельно (паттерн AB_TEST); (3) workflow `gitleaks.yml` — скан секретов на каждый
  push/PR + weekly. Детектор проверен на подсаженных секретах (AWS key + GH PAT → 2 leak);
  полная история репо прогнана локально gitleaks 8.30.1 — 345 коммитов, утечек нет.
  Подавления, если появятся, — только явным `.gitleaks.toml`-allowlist.
- Fingerprint BI-коннекшена при реюзе (C-6, аудит 2026-07-18): `ensure_database` обоих
  адаптеров переиспользует коннекшен ПО ИМЕНИ — стейл-запись (переехавший стенд, другой
  порт/база) молча кормила бы дашборды не тем DWH. Теперь при реюзе host/port(/db) из
  существующей записи (Superset `sqlalchemy_uri` / DataLens `bi/getConnection`) сверяются
  с текущим DWHConfig: mismatch → warning, при `AUTO_BI_BI_CONNECTION_STRICT=true` — отказ.
  Best-effort: нечитаемый fingerprint никогда не ломает билд, только positive-mismatch.
- Лимит одновременных SSE-консьюмеров (C-7, аудит 2026-07-18): `SSEGate` — глобальный cap
  (`AUTO_BI_SSE_MAX_STREAMS`, дефолт 20) и cap на сессию (`AUTO_BI_SSE_MAX_STREAMS_PER_SESSION`,
  дефолт 3; 0 = без лимита); сверх ёмкости `GET .../events` отвечает 429 + `Retry-After`.
  Слот берётся до создания итератора (отбитый консьюмер не съедает билд-события) и
  освобождается в `finally` генератора, включая дисконнект клиента (ARCHITECTURE §3.18).
- Опечатки в `AUTO_BI_*`-переменных больше не молчат (C-2, аудит 2026-07-18): `extra="ignore"`
  у Settings тихо выбрасывал `AUTO_BI_AUTH_ENABLE=true` (auth оставался выключен без следа) —
  `auto_bi serve` на старте сверяет окружение с `Settings.model_fields` (+string-алиасы) и
  пишет warning на каждую неизвестную переменную (`config.warn_unknown_env_settings`).
- Убран дефолт `datalens_password="admin"` (C-8, аудит 2026-07-18): дефолт пустой, signin с
  пустым паролем падает понятной ошибкой «set AUTO_BI_DATALENS_PASSWORD» ДО сетевого вызова
  (симметрично Superset, у которого пароль без дефолта всегда); healthcheck отдаёт её как not-ok.
- `deploy/hf-demo/publish_space.py` — безопасная обвязка (C-3, аудит 2026-07-18): дефолтный
  рабочий каталог = `TemporaryDirectory` с гарантированной очисткой; существующий
  пользовательский путь удаляется ТОЛЬКО при маркере клона этого Space (`.git` + remote
  `huggingface.co/spaces/<SPACE>`) И явном `--force-clean` — чужой каталог не сносится
  (юнит-тесты); токен больше не встраивается в push-URL — аутентификация через inline
  credential helper из env `HF_TOKEN`; новый `--dry-run` (без токена) показывает
  снапшот/дифф и ничего не пушит.
- SQL-guard: denylist табличных функций дополнен (C-1, аудит 2026-07-18) — remote/объектные
  стораджи и lakehouse-форматы (`remoteSecure`, `s3Cluster`/`hdfsCluster`/`urlCluster`/
  `fileCluster`, `azureBlobStorage(+Cluster)`, `gcs`, `oss`, `deltaLake`/`iceberg`/`hudi`
  (+Cluster)) и RBAC-слепые локальные (`merge` — читает все таблицы по regexp правами
  сервисного аккаунта, `dictionary`, `executable`). Закрыта форма AST, где sqlglot парсит
  вызов табличной функции как неквалифицированную таблицу (`dictionary('x')`): таблица без
  схемы с denylist-именем отклоняется, `dm.dictionary` и CTE-алиасы — нет. Заметка о
  кандидате на allowlist — ARCHITECTURE §4.

### Changed

- `explain_high_scan_fraction` предпочитает **живой** знаменатель (P1-6): при доступном
  RunQuery advisor берёт текущий размер таблицы из `system.tables` (кэш на инстанс,
  never-raise) вместо git-замороженного `physical.rows`, который расходится с окружением
  (модель 20M vs демо 1M) и давал ложную долю скана; `evidence.total_rows_source: live|model`
  фиксирует происхождение, фолбэк на модельный `rows` — при недоступном каталоге или live-нуле
  (ARCHITECTURE §3.3).
- Feasibility Advisor судит **effective query**, а не дословный запрос спеки (P1-2):
  фильтры чарта плюс дашбордные контролы, которые реально его сужают (`advisor/effective.py`).
  Контрол засчитывается по тем же условиям, что кодируют оба адаптера — колонка в grain чарта
  и непустой `default`; иначе чарт открывается несуженным и находка честна. До Phase 2
  дашбордные фильтры не компилировались, и чтение одних `query.filters` было верным; с native
  дашборд-фильтрами оно давало ложный `no_filter_on_large_fact` на спеке, где период задан
  только контролом. EXPLAIN тоже считается по effective query — измеряется то, что BI реально
  исполняет (ARCHITECTURE §3.3).
- Advisor запускается во всех entrypoints (P1-2): auto-обзор (`cli build --auto`,
  API `sessions/auto`) и одношаговый `cli build "текст"` раньше шли в сборку мимо него —
  детерминированные находки просто терялись. В путях без диалога вердикты механические
  (`worst_verdicts`, текст самих правил): вердикт по D5 всё равно выносит код, а narration
  стоила бы лишнего вызова провайдера на каждую сборку. `dm_change_request` логируется
  одинаково на всех путях.
- Находка `explain_high_scan_fraction` при многопроходном скане сообщает проходы, а не долю:
  `EXPLAIN ESTIMATE` суммирует все проходы по таблице, поэтому period-compare (текущее окно
  + предыдущее) давал «reads ~146% of dm.sales_daily» — доля выше 100% читается как сломанное
  число. Теперь выше 1 — «29132263 rows … 1.5× its 20000000 rows (more than one pass)»;
  сырое отношение остаётся в `evidence.scan_fraction`.
- Совет этой же находки зависит от формы запроса: скалярному period-compare KPI больше не
  предлагается «сузить период или добавить фильтр» — фильтр у него уже есть, а второй проход
  неустраним (сравнение периодов без него невозможно, и SQL_GEN расширяет внешний скан
  намеренно). Вместо этого — предагрегированный rollup в DM, чтобы сравнение читало бакеты,
  а не сырые строки. Само правило продолжает срабатывать: 29M строк на обновление — реальная
  стоимость, молчание было бы ложным негативом. Признак — `evidence.period_compare`.
- Дефолтная модель GraceKelly — `claude-sonnet-5` (было `claude-sonnet-4-6`).

- Запрос своими словами без имени витрины (X-3): поле `synonyms` у таблиц и колонок
  семантической модели — рукописные альтернативные имена («удержание»/«retention»
  у когортной витрины). Синонимы рендерятся в LLM-промпты и скорятся контекст-селекцией
  с весом имени, так что «Покажи удержание клиентов» находит `dm.cohort_retention`
  на любом размере DM. Плюс когортный паттерн в правилах PROPOSE: при наличии
  когортной витрины (форма: время × периодов-с-первого-события × число субъектов)
  дашборд собирается из неё — heatmap-треугольник, bar раннего удержания,
  big_number размера базы.


- **P1-3 Anthropic is a core dependency:** `anthropic` SDK ships with plain
  `pip install autobi-agent` and the production Docker image (default
  `AUTO_BI_LLM_PROVIDER=anthropic`). Optional extra `[anthropic]` kept as a
  no-op alias for older install lines. USER_GUIDE / DEPLOYMENT / ARCHITECTURE
  updated.


- **Дефолтная модель прямого Anthropic-провайдера — `claude-sonnet-5`** (было
  `claude-sonnet-4-6`; аудит 2026-07-18, D-7). Запрос уже строился совместимо: adaptive
  thinking на reasoning-шагах и `disabled` на механических передаются явно на каждом
  вызове, sampling-параметров и `budget_tokens` в запросе нет, thinking-блоки отбрасываются
  при разборе ответа. `anthropic_max_tokens` осознанно остался 16000: вызов
  без стриминга, а SDK отклоняет нестримингованный запрос, чья оценка длительности выходит за
  HTTP-таймаут. Токенизатор новой модели считает тот же текст примерно на 30% дороже —
  прежние оценки бюджета и стоимости пересчитать, а не переносить.
- **Прайс-таблица бюджета покрывает актуальную линейку** (D-7): Opus 4.8 / Sonnet 5 /
  Sonnet 4.6 / Haiku 4.5, USD за 1000 токенов, дефолт `Settings.llm_budget_prices` и
  `.env.example`. Незалистанная модель тарифицируется в 0, поэтому её нужно добавить
  прежде, чем полагаться на лимит стоимости. У Sonnet 5 действует пониженный вводный
  тариф до 2026-08-31 — в таблице стандартный, чтобы гейт скорее переоценивал трату,
  чем недооценивал.

### Removed

- Рабочая кухня вынесена во внутренний репозиторий (аудит 2026-07-18, п.5): 16 файлов
  `docs/history/`, 22 файла `docs/plans/` и пооперационный лог сессий из `CLAUDE.md`.
  Это разборы стендов, машин и ходов отладки — они устаревали быстрее, чем окупалась
  их правка. Поддерживаемые доки (`README`, `ARCHITECTURE`, `PLAN`, `USER_GUIDE`,
  `DEPLOYMENT`, `ONBOARDING_DWH`, `MARKET`, `CHANGELOG`) не тронуты; ссылки на
  вынесенное помечены префиксом `internal/` (конвенция описана в README §Документация).
- Заимствованная витрина-срез из репозитория убрана целиком (аудит 2026-07-18, п.6):
  `semantic/model_x5.yaml`, `docs/gaps_report_marts_x5.md`, стендовые
  `scripts/stand_create_marts.sql` / `scripts/stand_load_bv_mat.sh` и runbook 1.10.
  Продуктового кода не касается: модель ничем не загружалась (golden-сьюты идут на
  `model.yaml` / `model_gp.yaml`), в wheel `semantic/` и не входил.

### Fixed

- **P0-2 BI artifact collisions:** technical dataset (and DataLens entry) names
  include a build/session namespace fingerprint (`adapters/artifacts.py`);
  `compile_and_build` pins it via `set_artifact_namespace` before `build()`.
  Two sessions with the same title/chart ids no longer share or PUT-overwrite
  one virtual dataset; rebuilds get a fresh token so an older dashboard keeps
  its SQL. `BIAdapter` Protocol unchanged (optional concrete helper).
- **P0-3 resource bounds:** fail-closed remote bind (`ALLOW_INSECURE_REMOTE` /
  auth / demo profile required for non-loopback); `MAX_CONCURRENT_BUILDS`
  (default 2) → 503 + Retry-After on approve; work quota on auto/approve/insights
  (forced when `demo_auto_only`). LLM session quota stays separate (O-2).
- **P1-4 DCR / observability RBAC:** list/detail DCR scoped by session owner +
  allowed schemas (foreign → 404); PATCH status is admin-only (own non-admin →
  403). Global `GET /observability/llm` is admin-only; analysts get own-session
  spend only. No cross-user `session_request` leak.
- **P2-1 Anthropic usage:** successful `stop_reason` (`end_turn`/…) stored as
  `status=completed`; usage summary also counts legacy `end_turn` rows as ok.
- **P1-3 install hint:** missing-SDK error names `autobi-agent[anthropic]`
  (not the rejected `auto-bi` distribution).
- **P2-2 seed join-field false drop:** `seed_analysis` keeps already-qualified
  column refs (`dm.stores.city`); only bare columns are prefixed with
  `query.table` — join fields used in the spec are no longer reported as
  «не вошли в дашборд».
- **P1-5 strict models:** IR, semantic model, seed, grounding report, API
  request bodies use `extra="forbid"` (`StrictModel`); typos like `limt` /
  `max_chart` / `tablse` fail validation instead of silently defaulting.
  `AutoSessionRequest.max_charts` bounded to 1..12; request/reply length caps.
- **P1-1 auto-overview period:** период «last 12 months» запекается в SQL WHERE
  каждого чарта обзора (KPI/бары/таблица), а не только в native time-control
  (он покрывал в основном линию динамики). SQL_GEN понимает relative-токены
  `last N days|months|years` как GTE-bound. Серия `yoy_pct` остаётся на полной
  истории (лагу нужны prior-year точки).
- **P0-1 raw_sql:** LLM text/fields/patch больше не может эмитить `raw_sql`
  (schema без поля + `validate_spec(allow_raw_sql=False)`); schema-RBAC ходит
  по AST SELECT, а не только по `query.table`-метке; `guard_sql` режет remote
  table-functions; DataLens+raw явно rejected. CLI `auto_bi raw` без изменений.
- Superset: bar по временнОй оси (когорты по месяцам) держит time-ось с датами
  вместо категориальной с сырыми epoch-ms; числовые категории (`store_id`)
  по-прежнему форсятся категориальными.
- Superset: heatmap больше не метит нулевой ряд ordinal-оси (`months_since=0`)
  как `<NULL>` и не тасует числовые метки алфавитно (upstream #33105/#31318):
  числовая Y-ось малой кардинальности zero-pad'ится adhoc-колонкой
  (`LPAD(CAST(...))`, «00»…«23») — метка нуля и порядок чинятся разом;
  id-подобные оси сохраняют естественные подписи.

## [0.3.1] - 2026-07-10

Первый релиз на PyPI: `pip install autobi-agent`.

### Changed

- **Имя дистрибутива — `autobi-agent`** (было `auto-bi`; импортируемый пакет и консольная
  команда остаются `auto_bi`). PyPI отвергает `auto-bi` как «too similar to an existing
  project»: при сравнении имён разделители схлопываются, поэтому имя конфликтует с уже
  занятым пакетом-заглушкой `autobi`. Trusted publisher зарегистрирован под новым именем.

## [0.3.0] - 2026-07-10

Session-resume, публичное демо (живой Space + режим `AUTO_BI_DEMO_AUTO_ONLY`),
PyPI-конвейер, единый формат KPI-ряда и DataLens-доводки (пресет периода,
RU-единицы, percent-ось).

### Added

- Session-resume после рестарта (X-4): промах in-memory реестра лениво регидрирует
  сессию из Store (schema v7: `sessions.owner/target_bi/pinned`) — фаза диалога,
  текущий spec, счётчик clarify-раундов, статус билда и абсолютная ссылка на дашборд
  переживают рестарт `auto_bi serve` и eviction за `MAX_SESSIONS`; билд, оборванный
  рестартом, воскресает как `failed` и пересобирается повторным approve. RBAC
  сохраняется (владелец и скоуп схем восстанавливаются; legacy-сессии без владельца
  при включённом auth видит только админ). `DELETE /sessions/{id}` теперь ставит
  tombstone (`status='deleted'`), иначе гидрация воскрешала бы удалённую сессию.
- Живое публичное демо: <https://juliome20-auto-bi-demo.hf.space> (Hugging Face
  Space, один контейнер ClickHouse + Superset + Auto_BI + nginx) — анонимный
  auto-overview до готового дашборда в Superset без логина.
- Режим публичного демо (P8): `AUTO_BI_DEMO_AUTO_ONLY=true` открывает только
  детерминированный авто-обзор — text/fields-сессии, правки словами и enrichment
  отвечают 403 (человеческим сообщением), LLM-провайдер не подключается вовсе
  (`DisabledLLM`), UI дизейблит вкладки по флагу в `/health`;
  `AUTO_BI_SUPERSET_PUBLIC_URL` разводит публичную базу ссылок на дашборд и
  внутренний Superset-URL адаптера. Упаковка демо — `deploy/hf-demo/`
  (один контейнер CH+Superset+auto_bi+nginx для HF Space; фронт переведён на
  относительные пути и работает за префиксом `/agent/`), smoke — workflow
  `demo-image.yml`.
- Прокси-wiring per-IP квот (F-2, предпосылка публичного деплоя): `auto_bi serve`
  явно включает uvicorn `proxy_headers` и принимает `AUTO_BI_FORWARDED_ALLOW_IPS`
  (каким прокси доверять `X-Forwarded-For`) — за reverse-proxy login-лимитер и
  LLM-квота считают реальные клиентские IP, а не один общий bucket адреса прокси;
  nginx-пример и compose-схема в DEPLOYMENT дополнены заголовками и гочами.
- Лимитер чистит неактивные ключи (L-4): записи, молчащие дольше
  `max(окно, потолок lockout)`, вычищаются амортизированным сканом — память процесса
  не растёт с каждым уникальным IP при публичной экспозиции; сброс эскалации при этом
  не дешевле, чем отсидеть максимальный lockout.
- Release-конвейер: job `pypi` (X-2) — публикация sdist+wheel на PyPI через Trusted
  Publishing (OIDC, без токена в секретах) на каждый тег `vX.Y.Z`; метаданные пакета
  (license/authors/classifiers/urls) дополнены в `pyproject.toml`, дистрибутив проверен
  `twine check` и установкой в чистое окружение. Первая публикация требует одноразовой
  настройки trusted publisher владельцем (инструкция — комментарий в `release.yml`).
- DataLens: пресет периода на дашборд-селекторе (B5) — `DashboardFilter.default`
  («last 12 months» / ISO-диапазон) компилируется в relative-interval токен
  (`__interval___relative_-12M___relative_+0d`) в `defaultValue`+`defaults` контрола;
  реально сужает ДАННЫЕ чартов в скоупе при открытии, не только бейдж
  (контракт-тест подтверждает сужение через `/api/run` c тем же params-токеном).
- DataLens: русские единицы величин (N2) — крупный рублёвый/счётный KPI рендерится как
  «236 млрд ₽» (замер магнитуды inline-`/api/run`-пробой → масштабирование меры в
  сабселекте датасета → RU-единица postfix'ом, шрифт KPI «s» чтобы влезало), а ось
  значений line/bar/area — тики «8…16» с подписью оси «млрд ₽» вместо SI «8B…16B»
  (зеркало Superset `ru_kpi_scale`/`_axis_scale`).

### Fixed

- Web-UI: ссылка «Дашборд готов» теперь абсолютная (F-1) — адаптеры возвращают
  BI-относительный url, и относительный href резолвился против хоста Auto_BI
  (`:8200` вместо `:8088`) → клик давал 404 при раздельных хостах. Сервер склеивает
  настроенную базу BI (`AUTO_BI_SUPERSET_URL`/`AUTO_BI_DATALENS_URL`) в `dashboard_url`
  и SSE-событие `done` (той же конвенцией, что CLI).
- RU-масштаб оси значений применяется только к одномерным чартам (F-3) — делитель
  тарифицировался по первой мере, но делил ВСЕ метрики чарта, так что на line
  «выручка + число заказов» вторая мера рендерилась в чужих единицах (млрд ₽).
  Мультимерные чарты сохраняют компактный SI-формат (оба адаптера).
- KPI-заголовок в полосе 1–10 единиц масштаба держит один знак после запятой (L-1):
  целочисленное округление теряло до трети величины («1,5 млрд» → «2 млрд»).
  Оба адаптера (Superset `",.1f"`, DataLens `precision: 1`).
- OpenAPI/docs-страница отдаёт версию пакета вместо захардкоженной «0.1.0» (L-3).
- Публичное демо: пересборка дашборда после первого билда падала с 422
  «A database with the same name already exists» — у Public-роли (Gamma-like)
  оставался `can_read on Database`, из-за чего FAB (is_item_public ДО verify_jwt)
  исполнял даже аутентифицированный GET /api/v1/database/ адаптера как анонимный,
  а анонимному DatabaseFilter прячет коннекшены → get-or-create всегда шёл в create.
  `superset_public_role.py` теперь снимает и это чтение; smoke `demo-image.yml`
  гоняет ДВА билда в одном контейнере, чтобы lookup-путь не оставался слепым пятном.
- Демо-режим: `PATCH /api/v1/dm-change-requests/{id}` закрыт 403-гейтом как и
  остальные записи разделяемого состояния (в демо DCR не создаются — defense in depth).
- Superset: KPI-плитки приведены к одному формату и отцентрированы по обеим осям.
  Процентная плитка рендерила «1.5%» одной строкой — длинная строка ужимала кегль,
  а без строки юнита плитка центрировалась иначе соседних; теперь она зеркалит
  плитки с юнитами («1.5» + «%» строкой ниже: метрика ×100 в SQL, формат `.1~f`
  с тримом хвостового нуля). Пропорции шрифтов value/unit запинены на всех
  плитках, центрирование — dashboard-CSS (`KPI_CENTER_CSS`, детерминированный
  шов нативного формата, как position_json).
- DataLens: percent-ось (C1) — доля/ratio-чарты показывали сырые 0..1 на оси значений
  (3 сессии считалось engine-limit). Причина: ось читает `formatting` поля ТОЛЬКО при
  `placeholder.settings.axisFormatMode="by-field"` (реверс datalens-ui 0.3831.0), флаг
  не выставлялся. Плюс горизонтальный bar требует wizard-конвенцию id плейсхолдеров
  (dimension→`y`, measures→`x` при том же позиционном порядке) — иначе формат оси
  ищется на категорийной оси и теряется.

## [0.2.0] - 2026-07-06

Первый версионированный релиз. До него проект жил без тегов/CHANGELOG/GHCR-публикации
(`version = "0.1.0"` с основания репозитория) — этот релиз фиксирует накопленный функционал
Phase 0–4 и последующего hardening-трека и вводит сам процесс релизов.

### Added

- **Сборка дашборда из естественного языка** (текст → уточнения по расхождениям с
  DM → `DashboardSpec` (IR) → сборка) поверх ClickHouse/Greenplum, с engine-aware
  **Feasibility Advisor** (детерминированные вердикты `ok`/`spec_adjustment`/`dm_change_request`).
- **Fields-first режим** (drag&drop полей витрины) и **auto-overview** (курируемый
  дашборд по одной витрине без LLM) — второй и третий вход в тот же пайплайн.
- Аналитическое ядро IR: ratio-меры, произвольный `time_grain`, `yoy`/`pop`/лаг-N,
  `running_share` (Pareto/ABC), `histogram`.
- Два движка DWH (ClickHouse, Greenplum/Greengage) и два BI-адаптера (Apache Superset,
  Yandex DataLens self-hosted) за одним BI-агностичным IR.
- Web UI: чат, превью спецификации, вердикты advisor'а, режим итераций (патч-правки
  словами), заявки владельцу DM (`dm_change_request`), панель наблюдаемости (токены/
  латентность по шагам агента), панель «Что видно» (детерминированные инсайты без LLM).
- Прямой Anthropic Messages API как дефолтный LLM-провайдер (`ANTHROPIC_API_KEY`);
  GraceKelly — документированная опция.
- Auth/RBAC по схемам DWH (opt-in), с security-hardening: secure-cookie, rate-limit на
  login с растущим бэкоффом, токены хранятся как sha256-хэш, периодический purge.
- Ops-hardening: `GET /api/v1/ready` (store + DWH + BI healthcheck), структурные логи
  (`--log-format json`), устойчивая запись оборванных билдов после рестарта процесса.
- CI: офлайн-сьют (ruff/black/mypy/pytest/advisor-eval) + отдельный `integration`-job,
  поднимающий живой ClickHouse+Superset стенд в GitHub Actions на каждый push/PR.
- `docs/DEPLOYMENT.md` — гайд по продакшен-развёртыванию (reverse-proxy/TLS, бэкап
  SQLite, ротация логов, чеклист секретов).
- Релизный конвейер (этот релиз): `docker build` на каждый PR (job `docker` в
  `ci.yml`), публикация образа в GHCR по тегу `vX.Y.Z` (`.github/workflows/release.yml`),
  `auto_bi --version`, поле `version` в `/api/v1/health`, coverage-бейдж, генерируемый CI.
- **Аналитическое ядро в text-first** (S01): ratio/`time_grain`/`yoy`/`pop`/лаг-N/
  `running_share` (Парето)/`histogram` теперь запрашиваются и подтверждаются словами, а не
  только через fields-first spec — `SPEC_RULES` документирует каждый примитив few-shot
  JSON-примерами дословно согласованными с `ir/validate.py`; grounding распознаёт
  производные ratio-меры (обе составляющие есть в модели → matched) и держит аналитические
  обороты («год к году», «Парето», «нарастающим итогом», «распределение») как форму подачи,
  не отдельные сущности. Golden-eval расширен 16 новыми кейсами на сами примитивы
  (`g13`–`g23` + `it4` на CH, `gp_g9`–`gp_g12` на GP).
- **Скалярный period-compare KPI** (`Measure.compare`): плитка `big_number`, значение
  которой = одно число — последний период vs год/период назад (`yoy`/`pop`), как процент
  или абсолютная дельта. Считается условной агрегацией по двум бакетам (без окна) → форма
  скаляра сохраняется; авто-обзор при ≥2 годах истории добавляет плитку «‹мера›, г/г»
  рядом с уровнем главной меры. CH live-verified.
- `auto_bi eval --llm-mode {live,replay,record}` — record/replay-фикстуры для вызовов LLM
  в golden-сьюте (`auto_bi/llm/fixture.py`): сьют можно перегонять офлайн по ранее записанным
  ответам вместо живого провайдера. Фикстуры записаны для всего текущего сьюта (37 CH + 16 GP
  кейсов, 53 файла в `tests/fixtures/golden_llm/`) и подключены как офлайн шаг CI
  (`quality`-job, сразу после advisor-сьюта) — golden-регрессии ловятся на каждый push/PR без
  сети и секретов.
- **Per-IP/per-day лимит на LLM-сессии** (`POST /api/v1/sessions` и `/sessions/{id}/reply`):
  защита LLM-бюджета от неконтролируемого расхода перед публичным демо, тот же
  sliding-window+lockout механизм, что и `LoginRateLimiter`, конфигурируемый
  (`AUTO_BI_SESSION_RATE_ENABLED`/`AUTO_BI_SESSION_RATE_PER_DAY`, выключено по умолчанию —
  без изменений в локальном/dev-поведении) — 429 + `Retry-After`. `POST /sessions/auto`
  (детерминированный auto-overview, без LLM) не гейтуется.
- **Преднастроенный период дашборда (Superset, B5)**: `DashboardFilter.default`
  теперь наполняет `defaultDataMask` — дашборд открывается уже суженным на период
  («Last quarter») или значение фильтра, а не с пустым выбором.

### Changed

- **Читаемые KPI, оси и легенды (Superset)**: крупный рублёвый `big_number` показывается
  как «236 / млрд ₽» (масштаб + русская единица отдельной строкой) вместо d3 SI «236G»;
  **оси line/bar/area** тоже показывают русские единицы («15 … млрд ₽» на заголовке оси
  значений) вместо «15G»; легенды/тултипы/шапки колонок читают человеческое имя меры
  («Выручка») вместо сырого SQL-алиаса («sum_revenue»). SQL и значения не меняются — только
  отображение (percent-оси и средние не масштабируются).
- **Авто-обзор открывается на последних 12 месяцах**: детерминированный auto-overview теперь
  ставит преднастроенный период (`last 12 months`) на дашборд-фильтр времени — свежие данные,
  но полный год в кадре для yoy-KPI; пользователь расширяет до всей истории на дашборде.
  Раньше открывался на всю историю.
- **Человеческие легенды теперь и в DataLens**: поле датасета показывает человеческое имя меры
  («Выручка») в легенде/подписи оси, а не сырой SQL-алиас («sum_revenue») — как в Superset. Имя
  берётся из `measure.label` или короткой формы описания колонки модели; привязка чарта к полю
  идёт по алиасу-источнику, поэтому меняется только отображаемый заголовок.

### Fixed

- **Преднастроенный период реально сужает данные (Superset)**: дашборд-фильтр времени доносил
  `time_range` до чарта, но ECharts-timeseries-запрос не называл временную колонку, поэтому
  Superset не применял диапазон — ряд оставался полным, хотя контрол показывал период и бейдж
  «Applied filters (1)». Timeseries-чарт над временной колонкой теперь ставит `granularity_sqla`,
  и диапазон действительно ре-скоупит ряд (свежая сборка: 24 месяца → 11 под пресетом «last 12
  months»). Контракт-тест теперь проверяет само сужение, а не только round-trip конфигурации.
- **Сортировка баров по мере (Superset)**: структурные бары снова сортируются по значению
  (крупнейший сверху), а не алфавитно. Гуманизация легенды рассинхронила ключ `x_axis_sort`
  (ссылался на алиас, а Superset матчит по метке метрики); плюс для горизонтальных баров
  направление сортировки инвертируется, чтобы крупнейший был сверху, а не снизу.
- **Ровный правый край раскладки** (auto-overview): чарт, оставшийся один в последнем ряду
  (после отсечения детальной таблицы по `max_charts`), растягивается на всю ширину — нет
  «рваного» полупустого ряда.

[Unreleased]: https://github.com/brownjuly2003-code/Auto_BI/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/brownjuly2003-code/Auto_BI/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/brownjuly2003-code/Auto_BI/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/brownjuly2003-code/Auto_BI/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/brownjuly2003-code/Auto_BI/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/brownjuly2003-code/Auto_BI/releases/tag/v0.2.0
