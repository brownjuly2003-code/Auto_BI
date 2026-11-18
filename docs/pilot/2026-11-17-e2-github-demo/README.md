# E2: гейт на GitHub, демонстрационный pull request (2026-11-17)

Запись пункта E2 [ADR 0013](../../adr/0013-entry-points-to-proven-path.md): публикация,
демонстрационный pull request с двумя прогонами гейта и MCP-сессия с внешним клиентом. Время
GitHub — UTC (2026-11-18 по UTC — тот же вечер 2026-11-17 по местному времени).

## Публикация

- **PR #102** (`decisionops-backlog` → `main`): D1–D6 [ADR 0012](../../adr/0012-post-finish-development.md),
  E1, `0.7.0` без тега, верх README о гейте, [пример](../../../examples/decisionops-gate/README.md).
  Голова — `6cbc7dc`, слит merge-коммитом `f94f5a3` ([ADR 0011](../../adr/0011-public-repository.md)).
- **PR #103:** workflow [`decisionops-gate-demo.yml`](../../../.github/workflows/decisionops-gate-demo.yml).
  Он зовёт Action по SHA `6cbc7dc`, без `dwh` и секретов. Слит `fd4f0da`.
- Перед push — полный `ci_local` (Linux-контейнер, ubuntu 24.04) на дереве обоих PR. Три job
  не достучались с хоста раннера до pypi.org: `dependency-resolution` latest-compatible и
  lowest-direct и сборка образа `demo-image`. Каждая прошла отдельным перезапуском. Мутационный
  гейт — 2946/3044, в пределах baseline; покрытие — 97 %. На GitHub зелёные все проверки: 15 у
  #102, 14 у #103 (smoke образа там не запускался). На `main` после слияния — тоже.

## Демонстрационный pull request #104

PR #104 (`e2-demo-pr`) не сливается. Исходы записаны заранее, до PR — таблица
[примера](../../../examples/decisionops-gate/README.md) (коммит `e7b268c`). Перед каждым push
локальный `decision verify` на тех же входах дал тот же вердикт и те же счётчики. Base
локального прогона — пример из `main`.

| | Прогон 1 | Прогон 2 |
|---|---|---|
| Коммит головы | `527a691`: комментарий YAML в `model.yaml`; `uv.toml` и `.uv-version` 0.13.9 | `32ef762`: `gross_margin_pct` `time.timezone` `Europe/Moscow` → `UTC` |
| `pull_request.base.sha` | `fd4f0da` | `fd4f0da` |
| Workflow run | `44220737628` | `44220933470` |
| Записано заранее | `yaml-comment`: `pass_with_advisory`, 0, `success` | `timezone-change`: `block`, 4, `failure` |
| Вердикт | `pass_with_advisory` | `block` |
| Код выхода гейта | 0 | 4 |
| Статус шага гейта (`fail-on: block`) | `success` | `failure` |
| Статус job | `success` | `failure` |
| Checks block / review / advisory / всего | 0 / 0 / 8 / 15 | 1 / 1 / 8 / 15 |
| Run гейта | `run-fa52eb8bd1956b37` | `run-d57b54f44a9c2240` |
| Evidence digest | `sha256:d0c01d83d7ed821422170b7aa6cccaa670807754f12fc849132253765484aca1` | `sha256:ff5af246a0d8dfdefca3f0efb80045528d5b87dcf2c0ac1df97f89494e69599c` |
| Семантика base | `sha256:d017cca0ab3352e2b139f1a43d3ee3829bd761cd9d0387d8619870f9a09e51e2` | та же |
| Семантика candidate | та же, что base | `sha256:524d638e4aef23edaf16534bb739d80f101f44c28030d638b11b6263cfc71996` |
| Артефакт | `decisionops-pr-104-44220737628-1`, 13 903 B | `decisionops-pr-104-44220933470-1`, 15 008 B |
| Артефакт хранится до | 2026-12-02T02:06:10Z | 2026-12-02T02:07:50Z |
| uv в Action | 0.8.23 | 0.8.23 |

## Шесть условий

1. **Base и candidate.** Шаг «Check out the base» выполнил
   `git worktree add --detach "$RUNNER_TEMP/decisionops-base" "$BASE_SHA"` с
   `BASE_SHA=fd4f0da…` (`pull_request.base.sha`). Вход `base` Action —
   `<runner.temp>/decisionops-base/examples/decisionops-gate/semantic`. Candidate —
   `examples/decisionops-gate/semantic` из checkout `pull_request.head.sha`. Заранее
   подготовленных каталогов нет.
2. **Вердикты.** Прогон без изменения семантики дал записанный заранее `pass_with_advisory`:
   digest семантики base и candidate совпадают. Изменение пилота дало `block`. Причина —
   `metric.time_changed` по `metric://sales/gross_margin_pct`, поднятый из review, потому что
   от метрики зависит governed-продукт.
3. **Один комментарий.** API комментариев PR #104 возвращает один комментарий
   `github-actions[bot]`: id `6966819864`, маркер `auto-bi-decision-gate:cde7d76ca177c4b5`.
   Создан 02:06:09Z прогоном 1, обновлён 02:07:49Z прогоном 2. Тело после каждого прогона —
   [`pr-comment/after-run-1.md`](pr-comment/after-run-1.md) и
   [`pr-comment/after-run-2.md`](pr-comment/after-run-2.md).
4. **Вердикт, код выхода, статус шага.** Шаг «Record the verdict and step status» пишет в job
   summary пять строк: `Verdict`, `Gate exit code`, `Gate step outcome (fail-on: block)`,
   `Evidence digest`, `Artifact name`. Их значения в таблице выше взяты из окружения этого шага
   в логе run. Комментарий называет вердикт и код выхода в каждом прогоне.
5. **Bundle.** Артефакт сохранён в обоих прогонах, при `block` тоже: шаг «Keep the evidence
   bundle» идёт после упавшего гейта. Оба артефакта скачаны `gh run download` и проверены
   `auto_bi decision evidence <run> --expect-digest <digest из комментария>`: exit 0,
   `content_hashed`, verifier 139. Срок хранения — 14 дней, даты в таблице. Bundle прогона 2
   лежит в [`evidence/run-d57b54f44a9c2240/`](evidence/run-d57b54f44a9c2240/reports/summary.md)
   байт в байт, как скачан.
6. **uv не зависит от checkout PR — проверено вживую.** Голова PR несёт `.uv-version` 0.13.9 и
   корневой `uv.toml` с `required-version = "==0.13.9"`. Action прочитал `.uv-version` из
   `$GITHUB_ACTION_PATH` и поставил uv 0.8.23. Затем он выполнил
   `uv run --no-env-file --frozen --no-dev --project "$GITHUB_ACTION_PATH" …` в рабочем каталоге
   job, то есть в checkout головы PR, и оба раза дошёл до вердикта. Обычный CI на том же коммите
   (`Dependency resolution (locked)`) прочитал `.uv-version` из checkout PR и поставил 0.13.9.
   Без `--project` uv 0.8.23 в таком checkout (`pyproject.toml` и этот `uv.toml` в корне) не
   стартует: «Required uv version `==0.13.9` does not match the running version `0.8.23`».
   С `--project` на каталог Action он работает. Это проба 2026-11-17 вне дерева репозитория, на
   Windows.

Прочие проверки PR #104 идут с uv 0.13.9 из головы PR. Гейт они не проверяют и в эту запись не
входят.

## MCP

Сессия `auto_bi decision mcp --evidence <dir>` по stdio, с клиентом вне тестов.

- **Клиент:** MCP Python SDK (`mcp`) 2.3.0, Python 3.12.11, Windows 11 (10.0.26200).
- **Сервер:** `auto_bi` 0.7.0 на `main` `fd4f0da`.
- **Ревизии протокола:** две сессии — stateless 2026-07-28 (`server/discover`) и legacy
  2025-11-25 (`initialize`).
- **Инструменты:** обе сессии перечислили `validate`, `impact`, `verify`, `evidence`, `runs` и
  вызвали каждый на chart-impact пилоте. Входы: `2026-08-25-ch-tz-margin` (contracts, base,
  candidate) и dashboards из `2026-09-12-owner-review-chart-impact`, `created_at`
  `2026-09-12T16:00:00Z`.

Сверка с CLI на тех же входах:

| Инструмент MCP | Сверен с | Итог |
|---|---|---|
| `validate` | `decision validate --json`, exit 0 | равны |
| `verify` | `decision verify --json`, exit 4 (`block`) | равны, кроме пути evidence |
| `evidence` | `decision evidence --json`, exit 0 | равны, кроме пути |
| `evidence` с `artifact` и `expect_digest` | `decision evidence --artifact=check-impact.products --expect-digest …`, exit 0 | payload равен |
| `impact` | артефакт `check-impact.products` из bundle CLI | равны |
| `runs` | `decision runs --json`, exit 0 | равны, кроме пути |

Bundle обеих MCP-сессий байт в байт равен bundle CLI. Дерево —
`cf186325607dcdf8745588ffa470458cd13735a4aea10bdb5f8aea273a7d2399`, manifest —
`sha256:162da218277a8b0974937c3180b34d334e99bcd8aa3cf431dd02653177ca8908`. Ни один ответ не
`isError`. Скрипт сессии — [`mcp/session.py`](mcp/session.py), итог —
`mcp/session-summary.json`. Первый прогон в тот же день, до слияния #102 и #103, дал те же
значения.

## Дефекты первых прогонов

Неверных вердиктов и нарушений границы безопасности не найдено. Одна находка публикации: Action
gitleaks v3.0.0 запускает gitleaks 8.24.3, который не применяет `[[allowlists]]` из
`.gitleaks.toml`. Подставное значение учётных данных из теста D6 поэтому прошло через отпечаток в
`.gitleaksignore` (коммит `6cbc7dc`). Выбор версии сканера — в
[бэклоге](../../DECISIONOPS_BACKLOG.md).

## Проверить

```bash
uv run auto_bi decision evidence docs/pilot/2026-11-17-e2-github-demo/evidence/run-d57b54f44a9c2240 \
  --expect-digest sha256:ff5af246a0d8dfdefca3f0efb80045528d5b87dcf2c0ac1df97f89494e69599c
```

`tests/test_e2_github_demo_record.py` держит три факта: bundle, digest из комментария и
идентичность комментария в двух прогонах.
