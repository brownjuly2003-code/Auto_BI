# Публичное демо на Hugging Face Space (P8)

Один контейнер: ClickHouse (синтетический демо-DM) + Superset + Auto_BI + nginx.
Space отдаёт один порт (7860), nginx маршрутизирует:

- `/agent/*` → auto_bi (:8200, префикс срезается — фронт работает на относительных путях);
- всё остальное → Superset (:8088, ему нужен корень: `/superset/*`, `/static/assets`, его `/api/v1/*`);
- `/` → 302 на `/agent/` (демо начинается с агента, не с логин-страницы Superset).

Режим — `AUTO_BI_DEMO_AUTO_ONLY=true`: доступен только детерминированный авто-обзор
(без LLM, без ключей, ноль расходов); text/fields и enrichment отвечают 403, вкладки в UI
задизейблены. Зритель строит авто-дашборд и открывает его в Superset анонимно
(`PUBLIC_ROLE_LIKE="Gamma"` + `all_datasource_access` для Public — см.
`superset_public_role.py`; адаптер создаёт дашборды `published: true`).

Всё эфемерно by design: диск Space не персистентен, демо-DM (1 млн строк) и метаданные
Superset пересоздаются при каждом старте (~2–4 мин холодный старт). Секретов нет:
CH и Superset слушают только localhost внутри контейнера, пароли — демо-заглушки,
`SECRET_KEY` генерируется на старте.

## Проверка перед пушем в Space

GitHub Actions → **Demo image (HF Space)** (workflow_dispatch): собирает образ и гоняет
smoke — роутинг, 403-гейты, анонимная auto-сессия до `built`, публичная ссылка на дашборд
без login-редиректа.

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
