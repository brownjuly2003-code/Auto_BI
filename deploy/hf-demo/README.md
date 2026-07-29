# Публичное демо на Hugging Face Space (P8)

Один контейнер: ClickHouse (синтетический демо-DM) + Superset + Auto_BI + nginx.
Space отдаёт один порт (7860), nginx маршрутизирует:

- `/agent/*` → auto_bi (:8200, префикс срезается — фронт работает на относительных путях);
- всё остальное → Superset (:8088, ему нужен корень: `/superset/*`, `/static/assets`, его `/api/v1/*`);
- `/` → 302 на `/agent/` (демо начинается с агента, не с логин-страницы Superset).

Режим по умолчанию — `AUTO_BI_DEMO_AUTO_ONLY=true`: доступен только детерминированный
авто-обзор (без LLM, без ключей, ноль расходов); text/fields и enrichment отвечают 403,
вкладки в UI задизейблены, `/health` отдаёт `capabilities` с `text_session=false` /
`llm_wired=false`. Зритель строит авто-дашборд и открывает его в Superset анонимно
(`PUBLIC_ROLE_LIKE="Gamma"` + `all_datasource_access` для Public — см.
`superset_public_role.py`; адаптер создаёт дашборды `published: true`).

Чтобы открыть text/fields, задайте в Space secrets `AUTO_BI_DEMO_AUTO_ONLY=false` **и**
рабочий `AUTO_BI_GRACEKELLY_URL` (или Anthropic). `start-autobi.sh` тогда включает
`AUTO_BI_REQUIRE_LLM_READY=true`: процесс **не стартует**, если LLM-probe не проходит —
Space не должен рекламировать text-режим при мёртвом туннеле. Если туннель ненадёжен,
**удалите** secret `AUTO_BI_DEMO_AUTO_ONLY` (не ставьте `false`) — дефолт снова auto-only.

Всё эфемерно by design: диск Space не персистентен, демо-DM (1 млн строк) и метаданные
Superset пересоздаются при каждом старте (~2–4 мин холодный старт). Секретов нет:
CH и Superset слушают только localhost внутри контейнера, пароли — демо-заглушки,
`SECRET_KEY` генерируется на старте.

## Воспроизводимость (plan_sol шаг 6)

- Space payload включает **`uv.lock`** (`publish_space.py` whitelist).
- `Dockerfile` ставит auto_bi через **`uv sync --frozen`** (тот же graph, что CI/GHCR app).
- `clickhouse-connect==1.6.0` (pin = `uv.lock`; bump вместе с lock).
- Base Superset — digest-пин (как в `docker/superset/Dockerfile`).

## Проверка перед пушем в Space

GitHub Actions → **Demo image (HF Space)**: `workflow_dispatch` **или** PR/push в
`main` при изменении `deploy/hf-demo/**`, `uv.lock`, `pyproject.toml`, demo docker
assets, `demo-image.yml` / `release.yml`. Smoke: роутинг, `assert_demo_profile.py`
(flag + capabilities + 403), анонимный auto-build до `built` + public dashboard URL.

После деплоя живого Space:

```bash
python deploy/hf-demo/assert_demo_profile.py https://<space>.hf.space
# text-профиль (только если намеренно включён LLM):
python deploy/hf-demo/assert_demo_profile.py https://<space>.hf.space --text-enabled
```

## Публикация в Space

Space собирает `Dockerfile` из КОРНЯ своего репо, поэтому Space-репо = содержимое
основного репо + этот Dockerfile, скопированный в корень + README.md с front-matter
(`sdk: docker`, `app_port: 7860`). Публикация — скриптом:

```bash
python deploy/hf-demo/publish_space.py --dry-run   # показать, что уедет (токен не нужен)
HF_TOKEN=hf_... python deploy/hf-demo/publish_space.py
```

Снапшот собирается ТОЛЬКО из tracked-файлов (`git ls-files`) и заменяет дерево
Space-репо целиком — внутренние заметки/скретчи не уезжают by construction.
Рабочий каталог по умолчанию — временный (чистится всегда); существующий
пользовательский путь скрипт трогает только если это клон ИМЕННО этого Space
и передан `--force-clean`. Токен в git-URL/argv не попадает — аутентификация
через inline credential helper из env `HF_TOKEN`.
`SPACE_HOST` Space задаёт сам — из него собирается публичная база ссылок
(`AUTO_BI_SUPERSET_PUBLIC_URL=https://$SPACE_HOST`).
