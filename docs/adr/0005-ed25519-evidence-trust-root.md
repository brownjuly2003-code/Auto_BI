# ADR 0005: Ed25519 trust root для evidence manifest

**Статус:** Accepted
**Дата:** 2026-09-12
**Заменяет:** решение 3 в
[текущем состоянии](../DECISIONOPS_CURRENT_STATE.md) и только пункт 6 ADR 0004 в части
отложенной подписи
**Связано:** [целевая архитектура](../DECISIONOPS_ARCHITECTURE.md),
[ADR 0004](0004-synthetic-proxy-offline-investigation.md)

## Context

`integrity=content_hashed` обнаруживает повреждение артефактов относительно manifest, но
не аутентифицирует того, кто создал manifest: его можно заменить вместе со всеми digest.
Решение о trust root было сознательно отложено до отдельного разрешения владельца. Такое
разрешение получено для локального, opt-in контура без развёртывания, push и deploy.

Нужен проверяемый вне процесса выпуска trust anchor, который:

- не помещает секрет в evidence, Git, CLI output или лог;
- сохраняет чтение прежних `content_hashed` bundles;
- даёт детерминированные bytes и понятную ротацию ключа;
- не требует KMS, PKI service или другой новой инфраструктуры на bootstrap-стадии.

## Decision

1. Manifest подписывается **Ed25519** через `cryptography`. Подпись opt-in: без
   `--signing-key` bundle остаётся `integrity=content_hashed` и байтово совместим со старым
   форматом.
2. Signing key — unencrypted PKCS#8 PEM, переданный только явным путём
   `--signing-key`. Его создаёт и хранит оператор вне checkout, evidence-каталога и логов;
   на Mac файл получает mode `0600`. Автопоиска ключа, значения ключа в env/CLI и
   committed development key нет.
3. Trust root — один или несколько явно закреплённых Ed25519 public keys в SPKI PEM,
   переданных читателю повторяемым `--trust-key`. Public key из самого manifest не является
   trust root. `key_id` — `sha256:<64 lowercase hex>` от raw 32-byte Ed25519 public key.
4. Подписываются domain-separated bytes
   `auto-bi.io/evidence-manifest/v1 + NUL + RFC8785(manifest)`. В canonical manifest перед
   подписью уже входят `integrity=signed`, `algorithm`, `key_id` и `signed_at`, а только
   `signature.value` заменён пустой строкой. Подпись кодируется каноническим unpadded
   base64url. `signed_at` равен детерминированному `created_at` прогона: это утверждение
   подписанта, не trusted timestamp authority.
5. Writer выставляет `integrity=signed` только после self-verification публичной половиной
   ключа. Независимый reader требует подходящий out-of-band trust root; отсутствие ключа,
   неизвестный `key_id`, неподдерживаемый алгоритм, неканоничная подпись или изменение
   manifest дают blocking problem. Выдача artifact из signed bundle без такой проверки
   запрещена.
6. Режим подписи, алгоритм и `key_id` входят в idempotency key. Поэтому unsigned run,
   signed run и run после ротации не конкурируют за один `run_id`.
7. Ротация выполняется overlap-периодом: новый public key добавляется к trust root до
   переключения signer. Старые public keys сохраняются столько же, сколько соответствующее
   evidence; удаление ключа намеренно делает такие bundles недоверенными, но не удаляет их.
   `VERIFIER_VERSION` повышен до `14`; `CHECK_VERSION` остаётся `9`, потому что смысл
   отдельных release checks не менялся.

Подпись аутентифицирует canonical manifest и через digest — каждый адресованный artifact.
Она не является доказательством trusted time, human acceptance или того, что отдельный
capture runner действительно исполнил заявленный SQL. Последнее станет доказанным только
когда приватный ключ будет изолирован внутри доверенного capture/execution boundary; этот
ADR такой инфраструктуры не вводит.

## Consequences

Положительные:

- получатель отличает аутентифицированный bundle от внутренне согласованной копии;
- pinned public keys достаточно для offline-проверки и их безопасно распространять;
- несколько trust keys позволяют ротировать signer без окна недоступности;
- существующие unsigned bundles продолжают проверяться по прежнему контракту.

Стоимость и ограничения:

- bootstrap хранит unencrypted private PEM и поэтому полагается на файловые права и
  изоляцию Mac; production key custody/KMS требует отдельного решения;
- потеря private key исключает новые подписи, а потеря архивного public key — независимую
  проверку старого evidence;
- Ed25519 и формат signing payload становятся versioned compatibility contract.

## Alternatives considered

### A. Встроить public key в manifest

Проверяет математическую согласованность, но атакующий заменит и manifest, и ключ. Это не
trust root. Отклонено.

### B. HMAC

Reader должен получить тот же секрет и тем самым сможет выпускать новые «доказательства».
Для независимой проверки это неверная граница полномочий. Отклонено.

### C. X.509, Sigstore или KMS сейчас

Дают управляемую identity/attestation инфраструктуру, но требуют внешнего provider,
доступности и lifecycle, которых bootstrap ещё не выбрал. Отложено; signer/verifier port
оставляет такую замену возможной после нового ADR.

### D. Подписывать только digest без domain separation и metadata

Оставляет неоднозначность протокола и позволяет подменять algorithm/key/timestamp вне
аутентифицированного сообщения. Отклонено.

## Verification

- focused tests проверяют правильный и неправильный trust root, overlap-ротацию,
  изменение manifest/envelope и неканоничный base64url;
- CLI contract проверяет fail-closed чтение signed artifact без trust root и отсутствие
  private bytes в ошибках или manifest;
- docs-as-code фиксирует `VERIFIER_VERSION=14`, `CHECK_VERSION=9` и актуальные CLI flags;
- инфраструктурные проверки выполняются только на Mac; этот срез требует лишь offline
  unit/contract tests.
