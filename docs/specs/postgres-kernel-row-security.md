# Capability: A PostgreSQL-family kernel without row-level security

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

The v2 scope names Greenplum and Greengage next to PostgreSQL (CLAUDE.md, «Скоуп»). Greenplum 6
and Greengage 6 run on the PostgreSQL 9.4 kernel. Row-level security came with PostgreSQL 9.5
(`server_version_num` 90500): `pg_policy`, `pg_class.relrowsecurity` and `pg_roles.rolbypassrls`
do not exist before it. Step 7 (DECISIONOPS_ARCHITECTURE §8.2) asks all three: the identity
question reads `rolbypassrls`, and every mart is asked for its row policies and its row-security
switch.

PostgreSQL 9.4.26 (live, the vanilla kernel built from source, throwaway cluster on the Mac,
through psycopg 3.3.4, 2026-10-03):

- the identity question answers `UndefinedColumn`, SQLSTATE `42703`, `column "rolbypassrls" does
  not exist`. That state is not a refusal, so the collector raised on its first question for a
  select-only role, for a `NOINHERIT` member of a writing group and for a superuser alike: the
  environment step produced no facts on that kernel at all;
- `SELECT current_setting('server_version_num')::int AS version_num` answers the integer `90426`;
- the identity question without the `rolbypassrls` subselect answers the name and the superuser
  flag;
- the row-policy question answers SQLSTATE `42P01` (`pg_policy` does not exist), the
  row-security switch question `42703` (`relrowsecurity` does not exist), and
  `ALTER TABLE … ENABLE ROW LEVEL SECURITY` and `CREATE ROLE … BYPASSRLS` are syntax errors
  (`42601`);
- every other question of the collector answers: the table-level and column-level write
  privileges, the roles `SET ROLE` reaches (by membership) and their privileges, mart presence,
  column reads, readability, the reading, the column types, `EXPLAIN (FORMAT JSON)`, and the exact
  runtime `SELECT` under `SET TRANSACTION READ ONLY` and `SET LOCAL statement_timeout`;
- membership alone lets a session switch roles there: a `NOINHERIT` member of a group holding
  `INSERT` was refused its own `INSERT` (`42501`), `SET ROLE` to the group followed by the same
  `INSERT` raised no error, and the reachability question named the group.

PostgreSQL 16.15 (live, the same script, 2026-10-03) answers the identity question as before, so
nothing below changes what is asked of a kernel that has row-level security.

## Requirement: a kernel that predates row-level security is graded by the catalogs it has

The collector SHALL ask the PostgreSQL-family identity question as it does on every kernel. Only
when the engine answers that question with an undefined-column error — SQLSTATE `42703`, or, from
a driver that carries no state code, the wording `column "rolbypassrls" does not exist` — SHALL
it ask

`SELECT current_setting('server_version_num')::int AS version_num`

and read `version_num` of the first row. When that value is an integer that is not negative and
is below `90500` (a boolean or a text is not an integer), the kernel predates row-level security
and the collector SHALL:

- ask the identity again as `SELECT current_user AS name, (SELECT rolsuper FROM pg_roles WHERE
  rolname = current_user) AS superuser`, written on one line;
- grade the role from that answer by the existing identity rules, with `BYPASSRLS` not among the
  fields that can go unreported:

  | identity answer | role facts |
  |---|---|
  | refused or no rows | name `unknown`, `read_only` false, evidence `unreported role identity and superuser status, so read-only access is not established`; no write-privilege or reachability question; the exact runtime query is not executed |
  | `name` absent, NULL or empty, or `superuser` absent or NULL, and no positive superuser flag | `read_only` false, evidence `unreported <fields> in role identity, so read-only access is not established`, where `<fields>` lists `role name` and `superuser status` as they apply, joined by `, `; no write-privilege or reachability question; the exact runtime query is not executed |
  | `superuser` true | the existing superuser evidence and short-cut |
  | otherwise | the existing table-level and column-level write-privilege questions and the existing `SET ROLE` reachability questions, with their existing evidence |

- report `bypasses_row_security` null for every role: the kernel has no row security to bypass;
- ask neither the row-policy question nor the row-security switch question for any mart. A mart
  that is present SHALL carry `row_policies` `[]` and `row_security_enabled` null; every other
  fact of the mart is collected as on any kernel;
- record the value in `EnvironmentFacts.server_version_num`.

In every other case — the version question refused or answered with no rows, `version_num`
absent, NULL, a boolean, not an integer or negative, or a value of `90500` and above — the
collector SHALL raise the engine's undefined-column error as it does today: an undefined column
on a kernel that has row-level security is not an access fact. An error of the version question
that is not a refusal (a lost connection) propagates as a runtime fault.

`EnvironmentFacts.server_version_num` SHALL be null whenever the version question was not asked:
on ClickHouse and on every PostgreSQL-family kernel that answers the identity question. A kernel
that answers the identity question SHALL be asked exactly the questions it is asked today, in the
same order and with the same text.

When the facts carry a `server_version_num` below `90500`, the clean `dwh.security` detail that
reports coverage SHALL name it between the coverage clause and the closing clause:
`; <target> reports server_version_num <n>, a kernel without row-level security, so no row-policy
catalog was read`. The row-policy coverage counts each readable mart that carries `row_policies`
as answered, as on any kernel: the clause says where that answer comes from. The detail of an
environment in which no in-scope mart could be read reports no coverage and SHALL keep its text.
Findings, summary codes, statuses and verdicts of the five environment checks follow their
existing rules from the facts.

The collector SHALL NOT switch roles, write, or attempt a row-security statement to learn what
the kernel has. `VERIFIER_VERSION` and `CHECK_VERSION` SHALL increase: the sealed environment
facts gain a field, and the clean `dwh.security` detail gains a clause. Historical bundles are
not rewritten.

### Scenario: a select-only role on a 9.4 kernel is read-only (false-reject control)

- **GIVEN** a `greengage` engine that answers as the live 9.4.26 kernel did — `42703` for the
  identity question, `version_num` `90426`, the name and a false superuser flag for the identity
  without `BYPASSRLS`, false for every write privilege, no reachable role — and one in-scope mart
  with a runtime budget
- **WHEN** the environment is collected and graded
- **THEN** the role is read-only with the existing read-only evidence, `bypasses_row_security` is
  null, the mart carries `row_policies` `[]` and `row_security_enabled` null,
  `server_version_num` is `90426`, the exact runtime query is executed, `dwh.access` is pass, and
  `dwh.security` has no finding

### Scenario: the questions asked of a 9.4 kernel

- **GIVEN** the same engine and role
- **WHEN** the environment is collected
- **THEN** the questions are, in order: the identity question, the version question, the identity
  without `BYPASSRLS`, the table-level and the column-level write-privilege questions, the
  reachability question, for the mart its presence, column reads, readability, reading and
  column types, and then the plan question of each reference query; no question names
  `pg_policy`, `relrowsecurity` or, after the first, `rolbypassrls`

### Scenario: the clean security detail names the kernel

- **GIVEN** the same engine and role, every requested column readable
- **WHEN** the gate grades `dwh.security`
- **THEN** it is pass with `security.unfiltered`, and the detail is
  `security observation on <target>: <c> of <c> requested column reads confirmed and 1 of 1
  row-policy questions answered per in-scope mart/event-time question; coverage is complete;
  <target> reports server_version_num 90426, a kernel without row-level security, so no
  row-policy catalog was read; no restrictions found in the observed facts for <role>`, written
  on one line, where `<c>` is the number of requested columns

### Scenario: no readable mart keeps the detail of any kernel

- **GIVEN** facts that carry `server_version_num` `90426` and, in turn, an in-scope mart that is
  absent and one that is present and not readable
- **WHEN** the gate grades `dwh.security`
- **THEN** it is pass with `security.not_run`, the detail is `no in-scope mart could be read on
  <target>, so nothing was asked about what narrows it`, written on one line, and the sealed
  facts carry `server_version_num` `90426`

### Scenario: a writable role on a 9.4 kernel still blocks

- **GIVEN** the 9.4 answers and, in turn, a role holding a table-level `INSERT`, a role holding
  a column-level `UPDATE`, and a role whose reachability answer names a role holding `INSERT`
- **WHEN** the environment is collected and access is graded
- **THEN** each role is writable with the evidence the same grants give on any kernel, the exact
  runtime query is not executed, and `dwh.access` is block with `access.role_writable`

### Scenario: a superuser on a 9.4 kernel keeps the superuser evidence

- **GIVEN** the 9.4 answers with a true superuser flag
- **WHEN** the environment is collected and access is graded
- **THEN** the role is writable with the existing superuser evidence, `bypasses_row_security` is
  null, no write-privilege or reachability question is sent, and `dwh.access` is block

### Scenario: an unanswered identity on a 9.4 kernel blocks

- **GIVEN** the 9.4 answers where the identity without `BYPASSRLS` is, in turn, refused, answered
  with no rows, answered with a NULL name, answered with an empty name, and answered with a NULL
  superuser flag
- **WHEN** the environment is collected and access is graded
- **THEN** the first two name the role `unknown` with the evidence
  `unreported role identity and superuser status, so read-only access is not established`; the
  others name `role name` or `superuser status` as unreported; none of the evidences mentions
  `BYPASSRLS`; no write-privilege or reachability question is sent, the exact runtime query is not
  executed, and `dwh.access` is block

### Scenario: an undefined column on a kernel with row-level security still raises

- **GIVEN** an engine that answers the identity question with `42703` and the version question
  with, in turn, `90500`, `160015`, `-1`, no rows, a refusal, NULL, `true`, and the texts `90426`
  and `9.4.26`
- **WHEN** the environment is collected
- **THEN** the undefined-column error of the identity question is raised, and no further question
  is asked after the version question

### Scenario: another error of the identity question does not ask the version

- **GIVEN** an engine whose identity question is, in turn, refused with `42501`, answered with no
  rows, and failing with a lost connection
- **WHEN** the environment is collected
- **THEN** the first two are graded as an unanswered identity by the existing rule and the third
  propagates; the version question is not asked in any of them

### Scenario: a driver without a state code is recognised by the wording

- **GIVEN** an engine that raises an error with no `sqlstate` and the text
  `column "rolbypassrls" does not exist` for the identity question, and otherwise answers as the
  9.4 kernel
- **WHEN** the environment is collected
- **THEN** the facts are those of the 9.4 kernel; an error with no state code and another text
  (`column "rolsuper" does not exist`) is raised without a version question

### Scenario: a kernel with row-level security is asked what it is asked today

- **GIVEN** PostgreSQL-family engines that answer the identity question
- **WHEN** the environment is collected
- **THEN** the questions and their order are unchanged, `server_version_num` is null, and the
  role and mart facts are those of the previous verifier version

### Scenario: a mart the model calls restricted on a 9.4 kernel

- **GIVEN** the 9.4 answers and a mart whose model declares a confidential column the role may
  read
- **WHEN** `dwh.security` is graded
- **THEN** it is review with `security.unenforced_classification` by the existing rule: with no
  row policy and every column in reach, the classification is not enforced on that kernel

### Scenario: ClickHouse collection is unchanged

- **GIVEN** a ClickHouse engine
- **WHEN** the environment is collected
- **THEN** no PostgreSQL identity or version question is asked and `server_version_num` is null

## Known boundaries

- The live facts are those of the vanilla PostgreSQL 9.4.26 kernel. No Greenplum or Greengage
  stand has been available: their own catalogs (distribution policy, partition levels) and their
  `EXPLAIN` output are not covered by this capability.
- PostgreSQL 9.5 and 9.6 have row-level security but not `pg_policy.polpermissive`, which came
  with PostgreSQL 10; on them the row-policy question still answers an undefined column and the
  collector raises. Both are past their end of life and are not the kernel of a Greenplum-family
  release.
- A kernel older than 9.4 is not covered: `to_regclass` came with 9.4.
- The version is asked only after the identity question failed, so the facts of a kernel that
  answers the identity question do not record a version.
