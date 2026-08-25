# Пилот-репетиция release gate — 2026-08-25

**Что это.** Bootstrap task 8 требует pilot на *реальном* change владельца. Реального change
владелец пока не назвал, поэтому здесь — **репетиция на локальном стенде**: тот же гейт, тот
же живой ClickHouse, тот же отчёт владельцу, но продукт фикстурный. Репетиция отвечает на
вопросы task 8 настолько, насколько на них можно ответить без владельца, и прямо называет,
что она НЕ доказывает.

**Вывод в одну строку.** Гейт против живого движка отрабатывает за **~1.6 с**, отличает
изменение, которое достигло продукта, от изменения, которое его не касается, ловит расхождение
числа против verified asset и не даёт перезаписать запечатанный bundle. **Репетиция нашла и
починила два дефекта гейта** (ниже) — ровно то, ради чего репетицию и гоняют.

## Матрица прогонов

Один граф: два governed-продукта на одной витрине `dm.sales_daily` — `weekly_margin`
(метрика `gross_margin_pct`, ratio) и `weekly_revenue` (метрика `revenue`, aggregate). У
margin есть verified asset `weekly_margin_by_region` с ожидаемым числом, **снятым с самого
стенда** (`0.452054`); гейт этот запрос не выполняет — число записано снаружи и подано ему.

| Прогон | Change | Продукт | Вердикт | Что доказывает |
|---|---|---|---|---|
| `control` | нет | weekly_margin | `needs_review` (3) | на нетронутом стенде тревожит только `freshness.stale`; `results.reconcile` = `result.equivalent` — число сверено |
| `tz-margin` | `time.timezone` `Europe/Moscow → UTC` | weekly_margin | **`block`** (4) | `impact.products` → продукт, дашборд, владелец; asset инвалидирован |
| `tz-revenue` | **тот же** change | weekly_revenue | `needs_review` (3) | `impact.none` («0 of 1 change(s) reach this product»), `assets.valid`, `result.no_expectations` — чужой change не вменяется этому продукту |
| `result-drift` | нет; записанный результат `0.452100` вместо `0.452054` | weekly_margin | **`block`** (4) | `result.not_equivalent`, разница названа: `msk.gross_margin_pct: '0.452054' -> '0.452100'` |

Каждый каталог `docs/pilot/2026-08-25-ch-*/` самодостаточен: входы (`contracts/`, `base/`,
`candidate/`, `dashboards/`, `assets/`, `results.yaml`) и `evidence/` — bundle как его записал
гейт. Все четыре перепроверяются из репозитория (`auto_bi decision evidence …` → `pass`).
Пароля в bundle нет (проверено grep'ом по значению из `creds.env`).

Почему таймзона: это изменение, которое аналитик делает «по мелочи», а оно сдвигает границы
дня у недельной маржи — реалистичнее подмены числителя из сьюта.

## Что репетиция нашла — два дефекта, оба починены

**1. `impact.products` грейдил чужие изменения как свои.** `resolve_impact` держит в отчёте
каждое изменение диффа, включая те, от которых проверяемый продукт не зависит (payload обязан
говорить, что видел). Гейт же грейдил **весь список**: прогон `tz-revenue` до починки давал
`impact.products = review` и печатал секцию «Whose numbers this reaches» — про продукт, до
которого этот change не доходит. Теперь грейдится и рендерится только то, что действительно
достигло продукта (`_reaching`), payload по-прежнему полный, а `semantic.diff` по-прежнему
показывает change: это находка снапшота, а не продукта. Формулировка стала «N of M change(s)
reach this product».

**2. `assets`/`results` брались все подряд, независимо от продукта.** Прогон `tz-revenue`
инвалидировал asset чужой метрики и «сверял» его число, запечатывая в bundle продукта claim о
числе, которого этот продукт не отдаёт. Теперь репозиторий сужается до assets, называющих
метрики продукта (версии игнорируются с обеих сторон — резолвится ли пин, спрашивает
`decision validate`).

**Следствие для evidence:** смысл проверки изменился, поэтому по конвенции самого репозитория
подняты `CHECK_VERSION` и `VERIFIER_VERSION` (1 → 2). Bundle, выданный верификатором 1, теперь
не читается как гранёный сегодняшним правилом и не сталкивается по `run_id` с новым.

**3. Побочно найдено и закрыто: одинаковый `run_id` тихо перезаписывал запечатанный bundle.**
Записанные результаты намеренно **не входят** в idempotency key §7.6 (как и показания движка:
это наблюдения момента, иначе каждый прогон был бы новым). Поэтому `control` и `result-drift`
— один `run_id` `run-5a210ec990a382f9` при разных вердиктах (`needs_review` и `block`). В один
и тот же каталог evidence второй прогон молча затирал первый. Теперь `BundleWriter.write`
отказывается (`EvidenceConflictError` → usage-ошибка, exit 2) до того, как записан хоть один
байт; идентичная перезапись по-прежнему no-op. Проверено вживую:

```text
error: run 'run-5a210ec990a382f9' already holds bundle sha256:09bfc5c4…;
refusing to overwrite it with sha256:d8d3d6aa… (same idempotency key, different evidence)
```

## Что измерено

**Время гейта: 1580 / 1541 / 1770 мс** (три повтора `tz-margin` против живого движка) — все
13 проверок, включая пять `dwh.*` с запросами к движку, компиляция BI-контрактов, запись и
перепроверка bundle. Это время машины; время владельца — не измерено, его меряет только
владелец.

**Детерминизм.** Три повтора дают один и тот же `run_id` и побайтово один манифест.
`--created-at` фиксирован, поэтому воспроизводится и свежесть.

**Стенд.** `access.read_only`, `physical.as_declared`, `security.unfiltered`,
`cost.within_budget` (600 строк против потолка 1000) — ровно то, что обещает
`DECISIONOPS_CURRENT_STATE.md` для нетронутого стенда; `freshness.stale`, потому что витрина
датирована 2026-08-24 (45 ч против `PT6H`).

## Наблюдение владельцу — вопрос читаемости, не логики

Для `Date`-колонки гейт читает свежесть как начало дня в UTC и *сообщает* объявленную
таймзону, не применяя её (так задумано, docstring `verification/environment.py`). Строка «read
as 2026-08-24T00:00:00Z while the metric declares timezone Europe/Moscow» честна, но может
прочитаться как противоречие. Переформулировать или оставить — решение по читаемости.

## Чего репетиция не доказывает

- **Что владелец понял отчёт и за сколько.** Это измеряет только владелец — и это тот самый
  «review time», которого требует task 8.
- **Что change реальный.** Оба change выбраны агентом как правдоподобные; task 8 остаётся
  открытым до change, названного владельцем.
- **Что граф достаточно велик.** Два продукта — минимум, на котором «достигло A, не достигло
  B» вообще является утверждением. Настоящая семантика владельца больше.
- **Что число верное.** Гейт сравнивает записанный результат с ожидаемым; он не выполняет
  запрос asset'а и не ручается, что записанное — то, что вернул движок.

## Что нужно от владельца, чтобы закрыть task 8

1. Открыть отчёт (`auto_bi decision evidence docs/pilot/2026-08-25-ch-tz-margin/evidence
   --artifact report-summary-md`), засечь время до ответа «что сломалось и у кого», сказать,
   понятен ли отчёт.
2. Назвать реальный change (и стенд, если не локальный).
3. Только после этого — решение о certified brief и recurring runs (hard gate §16).

## Воспроизвести

```bash
wsl.exe -d Ubuntu-22.04 -e bash -lc '~/start_stands.sh; exec sleep infinity'   # в фоне

uv run python scripts/decision_stand_fixture.py /tmp/graph clickhouse --two-products \
        asset=msk:0.452054 results=msk:0.452054 max_scanned_rows=1000
cp -r /tmp/graph /tmp/graph_tz
sed -i 's/timezone: Europe\/Moscow/timezone: UTC/' /tmp/graph_tz/candidate/metrics/gross_margin_pct.yaml

AUTO_BI_CH_HOST=127.0.0.1 AUTO_BI_CH_PORT=8124 AUTO_BI_CH_DATABASE=dm \
AUTO_BI_CH_USER=gate_ro AUTO_BI_CH_PASSWORD=…   # CH_GATE_PASSWORD из ~/chstand24/creds.env
uv run auto_bi decision verify /tmp/graph_tz/contracts --product decision://sales/weekly_margin \
        --base /tmp/graph_tz/base --candidate /tmp/graph_tz/candidate \
        --evidence /tmp/graph_tz/.evidence --dashboards /tmp/graph_tz/dashboards \
        --assets /tmp/graph_tz/assets --results /tmp/graph_tz/results.yaml \
        --dwh clickhouse --environment pilot-ch --created-at 2026-08-25T21:00:00Z
```

`--product decision://sales/weekly_revenue` на тех же путях даёт прогон `tz-revenue`;
`results=msk:0.452100` в фикстуре — `result-drift`. Каждый прогон пишет в **свой** каталог
evidence: `control` и `result-drift` делят `run_id`, и во второй раз в тот же каталог гейт
теперь откажется писать.
