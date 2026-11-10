# DecisionOps — симуляция owner-gate 2026-09-04

**Статус:** proxy-review завершён; реальный owner sign-off не получен

**Вывод:** основной вопрос «что сломалось и у кого» читается правильно из обоих
v7-отчётов. Affected-отчёт теперь показывает точное `base → candidate` изменение,
а оба отчёта явно формулируют следующее действие владельца. Симуляция не закрывает
Task 8: человеческое время чтения и понимание по-прежнему нужно измерить на владельце.

## Вход и ограничения

Симулируется analytics owner, который знает свои метрики и дашборды, но не код
DecisionOps. Ответы извлекаются только из `reports/summary.md`; bundle используется
только для проверки целостности.

Проверены два Mac v7 bundle:

- affected: `decision://sales/weekly_revenue@1`, run `run-7d96f81f2ac4009f`,
  digest `sha256:d7dc80b9ea49bf142579feac0980b530a7cc10d8353fbfebae295b6e2b4adc68`;
- control: `decision://sales/weekly_orders@1`, run `run-fae00f0db7e5a6ff`,
  digest `sha256:d158f6813596326983506347c22cab87beeb37c1b8fb857fd0cb4344c6dfb6e6`.

Оба bundle прошли `auto_bi decision evidence`: verifier 7, по 19 artifacts,
verdict чтения `pass`, проблем целостности нет.

В изолированном native ClickHouse 24.8.14.39 проверки `dwh.access`,
`dwh.freshness` и `dwh.physical` прошли, но `dwh.security` вернул
`security.not_observed`: временная read-only роль не могла читать системные каталоги
доступа. Это не изменило общие verdict, но security evidence v7 не равноценно v6;
повторять pilot без отдельного решения о необходимости такой parity не требуется.

Время ниже — модельная оценка, а не stopwatch measurement. Расчёт использует
180 английских технических слов в минуту. Decision surface заканчивается там,
где уже можно ответить про verdict, scope и следующее действие: перед
`What needs a human` для affected и после `Owner action` для control.

| Отчёт | Decision surface | Полный отчёт | Оценка до ответа | Оценка полного чтения |
|---|---:|---:|---:|---:|
| affected v7 | 157 слов | 572 слова | 52 с | 3 мин 11 с |
| control v7 | 99 слов | 546 слов | 33 с | 3 мин 02 с |

Для контроля метода whitespace-подсчёт прежних v6 summary воспроизводит исходные
463/436 слов полного отчёта и 132/121 слово decision surface. Рост полного v7
отчёта в основном вызван подробным `security.not_observed`, а не owner-action текстом.

## Ответ simulated owner

### Affected product

- Решение: релиз заблокирован.
- Что сломалось: timezone `metric://sales/revenue` изменён с `Europe/Moscow` на
  `UTC`.
- У кого: `dashboard://weekly_revenue@1`, owner `commercial-analytics`.
- Действие: не выпускать candidate, устранить blocking findings и повторить gate.
- Что не доказано: нет expected result для сверки числа и cost ceiling для плана.
- Ограничение: доступ роли к системным каталогам не позволил доказать column grants
  и row-policy scope.

### Control product

- Решение: `needs_review`.
- Scope: 0 из 1 изменений достигают `weekly_orders`; отчёт прямо говорит, что это
  не означает изменения чисел продукта.
- Действие: проверить оставшиеся human findings перед решением о выпуске.
- Почему всё же review: snapshots различаются и security scope не наблюдался,
  поэтому остаются `semantic.diff` и `dwh.security`, хотя `impact.products` для
  продукта имеет status `pass`.
- Что не доказано: нет expected result и cost ceiling.

## Proxy-решение

| Критерий | Результат | Основание |
|---|---|---|
| Verdict заметен | pass | стоит вверху обоих отчётов |
| Затронутый scope понятен | pass | названы metric, dashboard и owner; control явно говорит `impact.none` |
| Границы evidence понятны | pass | непройденные проверки вынесены в `What was not done` |
| Следующее действие понятно | pass | отдельный `Owner action` различает block и scope-safe review |
| Человеческое время/понимание измерено | no | модельная оценка не заменяет владельца |

Итог proxy-review: **scope clarity и actionability проходят**. Task 8 и hard gate
следующей платформенной фазы остаются открыты до короткого blind review живым
владельцем.

## Как провести реальный gate

1. Дать владельцу affected v7-отчёт без предварительного объяснения и засечь время
   до ответа «что сломалось, у кого и что теперь делать».
2. Повторить с control-отчётом и спросить, изменились ли числа `weekly_orders`.
3. Записать время, ответы своими словами и места, где потребовался второй проход.
4. Закрыть Task 8 только при правильном scope обоих ответов; замечания к wording
   сохранить отдельно от технического verdict.
