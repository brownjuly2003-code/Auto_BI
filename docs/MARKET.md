# Auto_BI — Рыночный контекст

Снимок на 2026-06-11 (web-ресёрч с проверкой первоисточников). Обновлять при пересмотре стратегии.
**Решение 2026-06-11: целевой рынок — российский; v1-стек = ClickHouse + Superset** (см. ARCHITECTURE §1.1).

## Вывод

Зрелого бесплатного инструмента «NL-описание → готовый мультичартовый дашборд поверх DWH с выбором BI» не существует ни глобально, ни в России. Вендоры продают это как платные copilot'ы внутри своей платформы; open source даёт только «текст → SQL → один чарт». В России ближайшее — «Нейроаналитик 2.0» в DataLens (один чарт по датасету). Ниша Auto_BI (диалог + IR + целый дашборд + engine-aware advisor) — пустая, окно ограничено скоростью Яндекса.

## Российский рынок (целевой)

### СУБД под DM-слоем

Тезис «российский DWH = Greenplum или ClickHouse» подтверждается:

- **Greenplum-семейство** — стандарт ядра крупных DWH. После закрытия open-source Greenplum (Broadcom, 2024) российская ветка консолидировалась вокруг форка **Greengage** (Apache 2.0, инициирован Arenadata; стабильные релизы с 02.2025; в 11.2025 Arenadata DB переведена на Greengage). Правила advisor'а для GP работают для всей семьи.
- **ClickHouse** — «быстрые витрины» и real-time BI; типовая пара: GP — ядро/детальный слой, CH — DM-слой под дашборды. **Поэтому v1 Auto_BI = ClickHouse.**
- Postgres Pro — ниша малых хранилищ, в DWH-контексте не доминирует.

### BI-платформы

- Объём рынка BI РФ 2024 — >63 млрд руб., рост 16–20%/год. Power BI/Tableau/Qlik ушли (2022), легаси дорабатывает миграции.
- Вендорский рейтинг «Компьютерры» 2026: Visiology (313), AW BI (299), Luxms BI (289), далее Sigla Vision, Alpha BI, Glarus BI… «Круги Громова» покрывают >80 систем.
- **DataLens** — лидер self-service-сегмента; **Superset** — главная open-source-альтернатива в новых проектах (количественной статистики нет, но во всех обзорах).
- **Критичный факт для нас: у DataLens есть Public API** (`api.datalens.tech`, статус Preview, IAM-auth): createConnection/createDataset, создание Wizard/QL-чартов, createDashboard, workbooks → программная генерация дашбордов возможна. OSS-версия DataLens (Apache 2.0) из коробки коннектится к ClickHouse и Postgres.

### Superset в России: правовой и операционный статус

«Не зарублен» и зарубить практически невозможно:

- Лицензия Apache 2.0 — безотзывная, без гео-ограничений; ASF гео-блокировок не вводила. Код уже распространён, форкается свободно; использовать и включать в коммерческие продукты в РФ можно.
- Операционные риски — только каналы доставки: Docker Hub блокировал российские IP в мае 2024 (доступ вернулся; штатные обходы — зеркала: huecker.io, Yandex mirror, dockerhub.timeweb.cloud, свой registry). GitHub (public) и PyPI доступны.
- Реестр Минцифры: сам Superset в реестре не состоит — важно только для госзаказчиков; продукты «на базе open source» в реестре бывают, путь открыт.
- Митигация в проекте: пин версии + локальное зеркало образов; при желании — vendored исходники.

### AI-фичи российских BI (конкуренция)

| Кто | Что умеет | Чего не умеет |
|---|---|---|
| DataLens «Нейроаналитик 2.0» | AI-агент: по вопросу подбирает похожий чарт или **строит новый чарт по датасету**; вычисляемые поля, инсайты | целый дашборд не строит; работает внутри DataLens по уже настроенному датасету |
| Visiology ViTalk GPT / Cortex | NL→DAX, NL→Python/ETL | чарты/дашборды по описанию не строит |
| Luxms, Modus, Polymatica | подтверждённых NL→chart фич не найдено | — |

Отдельных российских продуктов «text-to-dashboard поверх DWH» не обнаружено.

## Глобальный контекст (для справки)

Платные copilot'ы внутри платформ: Power BI Copilot (мин. Fabric F2 ~$262/мес), Tableau Agent/Pulse, ThoughtSpot Spotter, Databricks Genie, Looker Conversational Analytics.

Бесплатное/OSS (ближайшие аналоги, все — «SQL/один чарт», не дашборд):

| Инструмент | Лицензия | Статус 06.2026 |
|---|---|---|
| Metabase OSS + Metabot | AGPL | AI на своём Anthropic-ключе: NL→SQL/чарт; дашборды не генерирует |
| WrenAI | Apache-2.0 (ядро) | жив, но сменил фокус на «context layer для агентов»; GenBI-UI в `legacy/v1` |
| Vanna | MIT | архив с 03.2026; паттерны text-to-SQL можно заимствовать |
| Superset OSS | Apache-2.0 | встроенного AI нет (только платный Preset Cloud) — наша цель компиляции |
| Chat2DB / DataLine / Briefer | разные | NL→SQL/чарты; дашборды в платном Pro / roadmap / не NL |

Строительные блоки: Vanna (text-to-SQL ядро), WrenAI MDL (референс семантической модели).

## Дифференциация Auto_BI

1. Уточняющий диалог, привязанный к семантике конкретного DM (grounding report), а не свободный чат.
2. BI-агностичный IR → **дашборд целиком** (layout, фильтры), а не один чарт по датасету (отличие от Нейроаналитика 2.0).
3. **Engine-aware Feasibility Advisor** — «такой дашборд витриной не предусмотрен, вот evidence и варианты, вплоть до заявки на новую витрину» — этого нет ни у кого, ни в RU, ни глобально.
4. Независимость от одной BI: Superset + DataLens из одного spec.

## Источники (ключевые)

- arenadata.tech / cnews.ru — Greengage, перевод ADB (11.2025)
- computerra.ru/339597 — рейтинг BI 2026; russianbi.ru — «Круги Громова»; vsl-bi.ru — объём рынка
- yandex.cloud/ru/docs/datalens/operations/api-start — DataLens Public API (Preview); yandex.cloud/ru/docs/datalens/concepts/neuroanalyst
- habr.com/ru/companies/visiology/articles/742152 — ViTalk GPT
- github.com: Canner/WrenAI, vanna-ai/vanna, apache/superset, datalens-tech/datalens
- learn.microsoft.com — Fabric Copilot capacity; metabase.com/docs/latest/ai/metabot
