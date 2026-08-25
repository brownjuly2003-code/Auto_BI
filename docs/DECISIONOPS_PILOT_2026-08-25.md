# Пилот-репетиция release gate — 2026-08-25

**Что это.** Bootstrap task 8 требует pilot на *реальном* change владельца. Реального change
владелец пока не назвал, поэтому здесь — **репетиция на локальном стенде**: тот же гейт, тот
же живой ClickHouse, тот же отчёт владельцу, но продукт фикстурный. Репетиция отвечает на
три вопроса task 8 настолько, насколько на них вообще можно ответить без владельца, и прямо
называет, что она НЕ доказывает.

**Вывод в одну строку.** Гейт против живого движка отрабатывает за **~1.9 с**, изменение
таймзоны event time у governed-метрики останавливает релиз с точным адресатом (продукт,
дашборд, владелец), evidence bundle перепроверяется из репозитория. Что осталось за
владельцем — прочитать отчёт по секундомеру и назвать реальный change.

## Что гонялось

| | Run A — контроль | Run B — пилотный change |
|---|---|---|
| Продукт | `decision://sales/weekly_margin@1` (фикстура `scripts/decision_stand_fixture.py`) | тот же |
| Change | нет: base = candidate | `time.timezone` метрики `gross_margin_pct`: `Europe/Moscow → UTC` |
| Стенд | ClickHouse 24.8.14.39, WSL `~/chstand24`, `127.0.0.1:8124/dm`, роль `gate_ro` (`readonly=1`) | тот же |
| Потолок | `budgets.max_scanned_rows = 1000` | тот же |
| Вердикт | `needs_review` (exit 3) — только `dwh.freshness` | **`block`** (exit 4) — `impact.products` |
| Bundle | `run-f7a3fcfc69c98ad1`, `sha256:0b5ba3b0…` | `run-859c5811cf2fc7c7`, `sha256:dc57115a…` |
| В репозитории | `docs/pilot/2026-08-25-ch-control/` | `docs/pilot/2026-08-25-ch-tz-change/` |

Каждый каталог содержит входы (`contracts/`, `base/`, `candidate/`, `dashboards/`) и
`evidence/` — bundle как он записан гейтом. Пароля в bundle нет (проверено grep'ом по
значению из `creds.env`).

Почему именно таймзона: это то изменение, которое аналитик делает «по мелочи» и которое
сдвигает границы дня у недельной маржи — реалистичнее, чем подмена числителя из сьюта.

## Что измерено

**Время гейта.** Два повторных прогона Run B против живого движка: **1945 мс и 1905 мс**
(все 13 проверок, включая пять `dwh.*` с запросами к движку, компиляция BI-контрактов, запись
и перепроверка bundle). Это время машины; время владельца — ниже, оно не измерено.

**Точность impact.** Run B: ровно одно изменение — `metric.time_changed` на
`metric://sales/gross_margin_pct`, классифицировано как `review`, поднято до `block`, потому
что от метрики зависит governed-продукт; адресаты — `decision://sales/weekly_margin@1`,
`dashboard://weekly_margin@7`, владелец `commercial-analytics`. Run A: ноль изменений, ноль
impact. Ложных срабатываний нет, пропусков нет — **но граф здесь тривиальный** (один продукт,
одна метрика, один дашборд), см. «Чего репетиция не доказывает».

**Evidence.** Оба bundle — 19 артефактов, `integrity = content_hashed`; Run B перепроверен
уже из `docs/pilot/…`: `auto_bi decision evidence docs/pilot/2026-08-25-ch-tz-change/evidence`
→ `verdict: pass (0 problem(s))`. Отчёт владельцу — тот же bundle с
`--artifact report-summary-md`.

**Стенд.** Контроль ответил ровно тем, что обещает `DECISIONOPS_CURRENT_STATE.md` для
нетронутого стенда: `access.read_only`, `physical.as_declared`, `security.unfiltered`,
`cost.within_budget` (600 строк против потолка 1000), `freshness.stale` (витрина датирована
2026-08-24, 45 ч против `PT6H`).

## Наблюдения — вопросы владельцу, не баги

1. **Отчёт отвечает «что стоит и кого касается» в первых восьми строках** («What stops the
   release» + «Whose numbers this reaches»). Понятен ли он без чтения таблицы — судить
   владельцу; это и есть замер review time из task 8.
2. **Формулировка freshness про таймзону.** Для `Date`-колонки гейт читает свежесть как начало
   дня в UTC и *сообщает* объявленную таймзону, не применяя её (так задумано, см. docstring
   `verification/environment.py`). Строка «read as 2026-08-24T00:00:00Z while the metric
   declares timezone Europe/Moscow» честна, но владелец может прочитать её как противоречие.
   Переформулировать или оставить — решение по читаемости, не по логике.
3. **`result.no_expectations` в «What was not done».** У фикстурного продукта нет verified
   asset с ожидаемым результатом, поэтому гейт остановил релиз **по семантике, а не по числу**:
   само значение маржи до/после никто не сверял. Для настоящего пилота нужен хотя бы один
   verified asset с записанным ожидаемым результатом — иначе `results.reconcile` молчит и на
   реальном change.

## Чего репетиция не доказывает

- **Что impact точен на нетривиальном графе.** «Изменение достигло продукта A и не достигло
  продукта B» здесь проверить не на чем — продукт один. Реальный пилот должен взять семантику
  минимум с двумя продуктами на общей метрике.
- **Что владелец понял отчёт и за сколько.** Это измеряет только владелец.
- **Что change реальный.** Таймзона выбрана агентом как правдоподобная; task 8 остаётся
  открытым до change, названного владельцем.

## Что нужно от владельца, чтобы закрыть task 8

1. Открыть `docs/pilot/2026-08-25-ch-tz-change/evidence/run-859c5811cf2fc7c7/reports/` (или
   `auto_bi decision evidence … --artifact report-summary-md`), засечь время до ответа «что
   сломалось и у кого» и сказать, понятен ли отчёт.
2. Назвать реальный change (и стенд, если не локальный).
3. Только после этого — решение о certified brief и recurring runs (hard gate §16).

## Воспроизвести

```bash
wsl.exe -d Ubuntu-22.04 -e bash -lc '~/start_stands.sh; exec sleep infinity'   # в фоне

uv run python scripts/decision_stand_fixture.py /tmp/pilot clickhouse max_scanned_rows=1000
cp -r /tmp/pilot /tmp/pilot_tz
sed -i 's/timezone: Europe\/Moscow/timezone: UTC/' /tmp/pilot_tz/candidate/metrics/gross_margin_pct.yaml

AUTO_BI_CH_HOST=127.0.0.1 AUTO_BI_CH_PORT=8124 AUTO_BI_CH_DATABASE=dm \
AUTO_BI_CH_USER=gate_ro AUTO_BI_CH_PASSWORD=…   # CH_GATE_PASSWORD из ~/chstand24/creds.env
uv run auto_bi decision verify /tmp/pilot_tz/contracts --base /tmp/pilot_tz/base \
        --candidate /tmp/pilot_tz/candidate --evidence /tmp/pilot_tz/.evidence \
        --dashboards /tmp/pilot_tz/dashboards --dwh clickhouse --environment pilot-ch \
        --created-at 2026-08-25T21:00:00Z
```

`--created-at` фиксирован, поэтому `run_id` и digest bundle воспроизводятся; свежесть при
этом считается от той же отметки, а не от текущего времени.
