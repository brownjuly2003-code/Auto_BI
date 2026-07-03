[
  {
    "severity": "P2",
    "file": "auto_bi/adapters/superset/form_data.py",
    "line": 30,
    "category": "sql-safety/invariant-3",
    "claim": "LLM-controlled measure.label flows UNESCAPED into the Superset adhoc-metric sqlExpression (`f'{agg}(\"{alias}\")'`), and form_data SQL is never passed through the sqlglot SELECT-only guard. The guard (invariant 3) only covers generate_chart_sql output; the chart's metric expression is a second, unguarded SQL path that Superset executes against the DWH connection.",
    "evidence": "_adhoc_metric builds sqlExpression = SUM(\"<alias>\") where alias = measure.label verbatim. Tested: Measure(label='x\") FROM system.numbers --') yields sqlExpression='SUM(\"x\") FROM system.numbers --\")' — the label breaks out of the identifier. sqlgen.py escapes the same alias via sqlglot identify (`AS \"x\"\")...\"`), so the two paths disagree and the form_data side is injectable. measure.label has no charset validation in ir/spec.py or ir/validate.py. A prompt-injected user request could make the model emit a label containing a ClickHouse table function (url()/remote()/s3()) for data exfiltration; the read-only role limits writes but not cross-table reads or outbound table functions.",
    "confidence": "high"
  },
  {
    "severity": "P2",
    "file": "auto_bi/ir/validate.py",
    "line": 52,
    "category": "correctness/clickhouse-dialect",
    "claim": "order_by by a measure's raw COLUMN passes validation but generates invalid ClickHouse SQL, and there is no valid way to sort a bar/line chart by its measure unless the measure has an explicit label. The orderable set is {dimensions, measure.column, measure.label}; it omits the computed measure_alias (agg_column). sqlgen then emits ORDER BY \"<raw column>\", which in a GROUP BY query is neither aggregated nor grouped -> ClickHouse error 215 (NOT_AN_AGGREGATE).",
    "evidence": "Reproduced: spec with dimensions=['city'], measures=[Measure(column='revenue', agg=SUM)] (no label), order_by=[OrderBy(by='revenue')] -> validate_spec returns [] (passes), generate_chart_sql -> 'SELECT \"city\", SUM(\"revenue\") AS \"sum_revenue\" ... GROUP BY \"city\" ORDER BY \"revenue\" DESC' (revenue not grouped). Ordering by the real alias 'sum_revenue' instead is REJECTED by validation: \"order_by 'sum_revenue' is neither a dimension nor a measure\". PROPOSE_SPEC prompt rule 5 explicitly tells the model to order bars by measure desc, so this path is likely to trigger. Fails loudly at the live EXPLAIN (not silent), but blocks the happy path whenever the measure has no label.",
    "confidence": "high"
  },
  {
    "severity": "P2",
    "file": "auto_bi/config.py",
    "line": 31,
    "category": "data-governance/privacy",
    "claim": "The AUTO_BI_SEND_SAMPLES privacy toggle (intended per ARCHITECTURE §4 to gate sending real row values to the LLM) is never read anywhere. The introspector always profiles and writes top_values (actual low-cardinality column values) into semantic/model.yaml, and render_model always embeds them in the LLM prompt, regardless of the flag.",
    "evidence": "Grep for `send_samples` across the repo (excluding .venv/.tmp) matches only config.py:31 (definition) and tests/test_smoke.py:16 (asserts default True). clickhouse.py:159 _fill_top_values runs unconditionally; render.py:38 always appends `[значения: ...]`. The flag is dead config — the documented opt-out has no effect, so real DM data reaches GraceKelly even when an operator sets AUTO_BI_SEND_SAMPLES=false.",
    "confidence": "high"
  },
  {
    "severity": "P3",
    "file": "auto_bi/agent/sqlgen.py",
    "line": 44,
    "category": "edge-case/clickhouse-dialect",
    "claim": "An empty IN filter value list generates `WHERE \"col\" IN ()`, which is a ClickHouse syntax error. QueryFilter.value permits an empty list and validate_spec does not reject it.",
    "evidence": "Tested: QueryFilter(column='store_id', op='in', value=[]) -> 'SELECT ... WHERE \"store_id\" IN () LIMIT 5000'. Caught by the live EXPLAIN so it fails loudly, but it surfaces as an opaque DWH error rather than a validation message the repair loop can act on.",
    "confidence": "high"
  },
  {
    "severity": "P3",
    "file": "auto_bi/adapters/superset/client.py",
    "line": 78,
    "category": "reliability",
    "claim": "No retry/backoff on transient transport failures (5xx, connect timeouts) for the Superset client; GraceKelly client likewise has no retry. Only a single 401-triggered re-login exists, and that re-login does not refresh the CSRF token, so a CSRF expiry on a long-running build is unrecoverable.",
    "evidence": "client.request retries once only on status 401 (line 81), re-calling login() which refetches the access token AND csrf — actually csrf is refreshed inside login(); however 403/CSRF-rejected mutations (not 401) are not retried. gracekelly.py _call wraps httpx in a single try with no retry; httpx.Client default has no transport retries. Task item 5 (timeouts/non-2xx/retries) — non-2xx raises immediately with no backoff for idempotent GETs.",
    "confidence": "medium"
  },
  {
    "severity": "P3",
    "file": "auto_bi/llm/gracekelly.py",
    "line": 97,
    "category": "cost/edge-case",
    "claim": "Two nested repair loops compound: GraceKellyClient.complete retries schema failures up to 1+MAX_REPAIRS=4 times, and propose_spec wraps that in 1+MAX_VALIDATION_ROUNDS=4 model-validation rounds. Worst case is ~16 GraceKelly calls per build, each with reasoning=true, with no global call/cost ceiling and no early-abort when the model returns an identical answer twice.",
    "evidence": "gracekelly.py:97 `for attempt in range(1 + MAX_REPAIRS)` (MAX_REPAIRS=3); propose.py:85 `for round_no in range(1 + MAX_VALIDATION_ROUNDS)` (=3) each calling llm.complete. No detection of repeated identical outputs to break the loop early; no aggregate budget.",
    "confidence": "high"
  },
  {
    "severity": "P3",
    "file": "auto_bi/adapters/superset/adapter.py",
    "line": 144,
    "category": "correctness/edge-case",
    "claim": "Dataset table_name is built from _slug(spec.title)+_slug(chart.id) truncated to 40 chars each. Two charts in one dashboard whose slugs collide after truncation map to the same Superset dataset; the second ensure_dataset PUT-overwrites the first chart's SQL, so both charts render the same (wrong) query.",
    "evidence": "_slug (adapter.py:33) truncates to max_len=40 and strips to \\w+; chart.id uniqueness is enforced by validate_spec but only on the raw id, not the truncated slug. ensure_dataset (line 74-80) treats a name match as the same dataset and PUTs new SQL onto it.",
    "confidence": "medium"
  },
  {
    "severity": "P3",
    "file": "auto_bi/adapters/superset/adapter.py",
    "line": 138,
    "category": "invariant-2/defense-in-depth",
    "claim": "SupersetAdapter.build does not re-validate the spec against the semantic model; it trusts that propose_spec already did. validate_spec is only called inside propose_spec, so any caller that constructs a DashboardSpec and calls adapter.build directly (or a future code path) reaches Superset with an unvalidated spec — invariant 2 is enforced by convention, not at the adapter boundary.",
    "evidence": "pipeline.build_dashboard validates SQL per chart but never calls validate_spec; the only validate_spec call site is propose.py:87. The happy path is safe today because the CLI always routes through propose_spec, but the BI boundary itself has no guard.",
    "confidence": "medium"
  }
]

---

## Resolution — 2026-06-12 (Fable, ветка `phase-0/vertical-slice`)

Все 8 findings закрыты в коде. `ruff` clean, `black` clean, `pytest` 65 passed / 4 deselected (live contract-тесты гейтятся Mac-стендом). Код проекта изменён в этой сессии по явному запросу пользователя («доработать фазу 0 по замечаниям аудита»).

| # | severity | fix | файлы | тест |
|---|---|---|---|---|
| F1 | P2 | `measure.label` экранируется как quoted-identifier (`"` → `""`) перед вставкой в `sqlExpression` адхок-метрики — нельзя выйти из `SUM("…")` | `adapters/superset/form_data.py` | `test_form_data_escapes_malicious_label` |
| F2 | P2 | `measure_alias` вынесен в `ir/spec.py` (единый источник); `order_by` по {column\|label\|alias} мапится на SELECT-алиас (`ORDER BY "sum_revenue"`, не сырая колонка); алиас добавлен в `orderable` валидации | `ir/spec.py`, `agent/sqlgen.py`, `ir/validate.py` | `test_order_by_raw_measure_column_uses_alias`, `test_order_by_computed_alias`, `test_order_by_computed_alias_ok` |
| F3 | P2 | `render_model(include_samples=...)` гейтит `top_values`; проброшено из `settings.send_samples` через `propose`→`pipeline`→`cli`; ARCHITECTURE §4 уточнён (флаг = «любые значения данных в LLM») | `semantic/render.py`, `agent/propose.py`, `agent/pipeline.py`, `cli.py`, `docs/ARCHITECTURE.md` | `test_send_samples_false_strips_values` |
| F4 (empty IN) | P3 | пустой `IN ()` отклоняется валидацией (actionable error для repair loop) + `ValueError` в `sqlgen` (belt-and-suspenders) | `ir/validate.py`, `agent/sqlgen.py` | `test_empty_in_filter_rejected`, `test_empty_in_filter_raises` |
| F5 (retries) | P3 | `httpx.HTTPTransport(retries=2)` на дефолтных клиентах GraceKelly и Superset (только transient connect-фейлы, безопасно для POST) | `llm/gracekelly.py`, `adapters/superset/client.py` | — |
| F6 (loop cost) | P3 | ранний выход из repair-петли при идентичном ответе модели (schema-level в `gracekelly`, model-level в `propose`) | `llm/gracekelly.py`, `agent/propose.py` | `test_complete_aborts_on_identical_answer`, `test_aborts_when_spec_unchanged` |
| F7 (slug collision) | P3 | имя dataset = читаемый slug + 8 hex от sha1(chart_id) → уникальность даже при truncate-коллизии slug | `adapters/superset/adapter.py` | `test_dataset_names_unique_even_when_slugs_collide` |
| F8 (re-validate) | P3 | defensive `validate_spec` на границе сборки в `pipeline.build_dashboard` (no-op на happy path, защищает BI-boundary от невалидного spec) | `agent/pipeline.py` | (покрыто существующими propose-тестами) |

Примечание по F8: концептуально правильнее было бы валидировать в самом `SupersetAdapter`, но адаптер не держит `SemanticModel`, а добавлять его в конструктор — интерфейсная правка (близко к S4). Выбрана защита на оркестрационной границе `pipeline`, где модель уже доступна — без изменения `BIAdapter`-протокола.

Примечание по F3: ARCHITECTURE §4 ранее гейтил флагом только «сэмплы строк» (фича Phase 1+), top-N значения слались всегда. Поскольку фича сэмплов ещё не реализована, флаг был мёртв, а для «чувствительных DM» реальные значения (top_values) всё равно утекали в LLM. Семантика флага расширена до «любые значения данных»; формулировка §4 обновлена. Это не входит в инварианты 1–8, но если владелец дизайна предпочитает прежнюю трактовку — откатывается одним коммитом.
