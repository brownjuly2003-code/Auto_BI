# Auto_BI DecisionOps — текущее состояние

**Обновлено:** 2026-09-12

**Стадия:** release gate bootstrap завершён; offline investigation alpha разрешён;
impact доведён до чарта; происхождение кандидатного числа проверяется вместе с
привязкой его reviewed-запроса к governed-модели; read-only capture reviewed-запроса
реализован и проверен на живых PostgreSQL и ClickHouse; evidence manifest получает
opt-in Ed25519 authentication; единый signed R3 owner-пакет выпущен на Mac;
Astra xhigh review R3/R4 выполнено по ADR 0006; owner-summary поясняет границы evidence;
human/design-partner outcome не измерен и не требуется для продолжения разработки

**Версии evidence:** `VERIFIER_VERSION=15`, `CHECK_VERSION=9`

Четыре исторических tracked pilot-bundle в `docs/pilot/2026-08-25-ch-*` выпущены verifier 2
и такими остаются: `auto_bi decision evidence` по ним проходит (все проверены 2026-09-12,
exit `0`), потому что перепроверка адресует запечатанный bundle и не требует текущей версии.
Mac verifier 7 runs из proxy-review 2026-09-04 в Git не сохранены. Отдельный
[chart-impact overlay](pilot/2026-09-12-owner-review-chart-impact/README.md) выпущен verifier
14 и tracked вместе с evidence. Текущий гейт добавляет chart resolution, provenance,
governance reviewed-запроса, runtime cost facts и signing identity. У исторического
pilot-числа нет attestation, поэтому свежий прогон показывает `provenance.unattested`
(review). Dashboard-only overlay с теми же semantic snapshots и продуктом сохраняет
`run_id`; конкретный bundle различается evidence digest и каталогом. Отдельный
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
InvestigationPlan + CanonicalMetricResult -> четыре pure v1 skill
       -> evidence-backed observations/gaps -> deterministic brief artifacts
```

- `auto_bi decision validate` проверяет связанные YAML-контракты.
- `auto_bi decision capture` исполняет reviewed SQL одного positive asset под SELECT-only
  guard, сверяет типы и nullable-контракт с asset'ом и пишет новый gate-ready YAML с
  canonical result и attestation, привязанной к digest кандидатного semantic snapshot'а.
- `auto_bi decision verify` выполняет one-shot gate без удалённой BI-мутации; impact
  доходит до конкретного чарта доставляемого дашборда (`computes_metric` / `reads_field`),
  а нерешаемые плитки выходят именованными `chart_gaps`.
- `results.provenance` оценивает, откуда пришло кандидатное число: snapshot, среда и
  исполненный запрос сверяются с тем, что гейт держит независимо; число без attestation
  называется `provenance.unattested`. Ни одна находка не блокирует.
- `assets.invalidated` дополнительно сверяет reviewed `reference_query` с кандидатным
  snapshot'ом: неуправляемая витрина — `asset.query_ungoverned`, необъявленная колонка
  governed-витрины (в том числе голое имя, которого не объявляет ни один governed-источник
  его scope'а) — `asset.query_ungoverned_field` (обе review), неразрешимая без
  догадки часть запроса — `asset.query_fields_unchecked` (advisory).
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
| Chart-level impact | реализован 2026-09-07; tracked verifier 14 overlay показывает `computes_metric` и `chart.opaque_sql`, грейдинг не меняет |
| Result provenance | реализован 2026-09-07: проверяемая attestation, gate SQL по-прежнему не исполняет |
| Governance reviewed-запроса | реализована 2026-09-07: таблицы и колонки запроса сверяются со snapshot'ом |
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
- Chart-разрешение не заменяет lineage: плитка на операторском `raw_sql` остаётся
  `chart.opaque_sql`, а связь «чарт считает метрику» — вывод по точному совпадению
  термов, потому что в IR у чарта нет ссылки на метрику.
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
  оставляет governed-модель: ровно один источник scope'а объявляет это имя, и все
  источники scope'а — governed-таблицы. `SELECT *`, имя, объявленное несколькими
  источниками, и scope с неуправляемой витриной, CTE или подзапросом остаются
  непроверенными — названными (`asset.query_fields_unchecked`), а не молча пропущенными.
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
аналитический вход остаётся evidence-backed gap без factual claim.

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
Правила закреплены в [AGENTS.md](../AGENTS.md) и
[ADR 0006](adr/0006-modelled-review-development-gate.md).
Зависимость от локального proxy отменена владельцем в `AGENTS.md`; модельные агенты
запускаются без этой проверки. Новый runtime или внешние actions не начинаются.

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
