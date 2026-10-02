# Capability: The read-only role an environment is reached through

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

Invariant 3 (CLAUDE.md) says the gate reaches the DWH through a read-only role, and step 7
(DECISIONOPS_ARCHITECTURE §8.2) grades a role that can write as **block** (`access.role_writable`
in `dwh.access`): numbers collected through a role that could have changed the mart are not the
evidence the bundle claims to hold. Whether the role can write is read from what the engine says
about the role, never from attempting a write.

On the PostgreSQL family a write privilege can be granted on columns as well as on the table.
PostgreSQL 16.15 (live, throwaway cluster, 2026-10-01): a role granted `SELECT` on
`dm.sales_daily` and only `UPDATE (revenue)` answers `has_table_privilege('dm.sales_daily',
'UPDATE')` false, `has_any_column_privilege('dm.sales_daily', 'UPDATE')` true, and its
`UPDATE dm.sales_daily SET revenue = revenue + 1` succeeds; `INSERT (revenue)` behaves the same
for `INSERT`. `has_any_column_privilege` answers for effective privileges: a grant to a group
the role inherits from, or to `PUBLIC`, makes it true, and those grants write as well. A
`REFERENCES (revenue)` grant does not make either function true and does not write. Column
privileges exist only for `SELECT`, `INSERT`, `UPDATE` and `REFERENCES`
(`has_any_column_privilege(…, 'DELETE')` is an error), so `DELETE` and `TRUNCATE` stay
table-level questions.

A session can also write through a role other than its current one. PostgreSQL 16.15 (live,
throwaway clusters, 2026-10-02): a `NOINHERIT` role granted a group that holds `INSERT` on
`dm.sales_daily` answers `has_table_privilege` and `has_any_column_privilege` false for itself,
yet `SET ROLE` to that group followed by an `INSERT` succeeds. The same write succeeded through a
`NOINHERIT` intermediate role, through a group holding only `UPDATE (revenue)`, through the
predefined role `pg_write_all_data`, and through a group with `SUPERUSER`. Membership passes
privileges on but not the `SUPERUSER` attribute: an inheriting member of a `SUPERUSER` group
that holds no grants of its own answered every write question false and
`pg_has_role(current_user, …, 'USAGE')` true for the group, yet `SET ROLE` to the group followed
by an `INSERT` succeeded; granted `WITH INHERIT TRUE, SET FALSE`, the switch was refused.
`SET ROLE` is judged by the **session user**: a session whose role default
(`ALTER ROLE … SET role`) was a
select-only role switched to a writable group that its session user may set, although
`pg_has_role(current_user, …, 'SET')` answered false; and `SET ROLE NONE` returned it to the
session user, whose own `INSERT` grant wrote, while `RESET ROLE` returns to the role default
instead. A new session starts as the user it authenticated as: neither a
`session_authorization` startup option nor a role default for it changed `session_user`. A
membership granted `WITH SET FALSE` cannot be switched to (`permission denied to set
role`): `pg_has_role(…, 'SET')` answers false for it while `pg_has_role(…, 'MEMBER')` answers
true. The `SET` option exists from PostgreSQL 16; before it, and in the Greenplum/Greengage
families built on older PostgreSQL, every direct or indirect member may `SET ROLE`, which is
what `pg_has_role(…, 'MEMBER')` answers — documented behaviour, not run live here.

Known boundaries, not covered here: a `SECURITY DEFINER` function the role may execute writes
with its owner's privileges; ClickHouse roles granted to the user but not enabled can be
switched on with `SET ROLE`; whether a role reachable through `SET ROLE` bypasses row security
is not asked; role-administration powers (`CREATEROLE`, `ADMIN OPTION`), with which a role can
change memberships, are not asked about; and a session whose current role cannot log in has no
`pg_user` row, so the
identity question answers a NULL superuser flag for it and access blocks as unestablished
(live, PostgreSQL 16.15).

## Requirement: an unanswered role identity cannot establish read-only access

If the PostgreSQL-family identity question is refused or returns no rows, the collector SHALL
NOT treat the absent superuser and row-security-bypass answers as false. `RoleFacts` SHALL
name the role `unknown`, set `read_only` false and `bypasses_row_security` null, and explain
that role identity, superuser status and `BYPASSRLS` were unreported, so read-only access is
not established. The collector SHALL skip the table-level and column-level write-privilege
questions after this unanswered identity query: their grants have not been observed.
The exact runtime query SHALL NOT execute, and `dwh.access` SHALL block through the existing
`access.role_writable` finding, whose detail SHALL describe unestablished read-only access
without asserting that an unknown role can write. Connection failures SHALL still propagate
as runtime faults.
The collector SHALL NOT attempt a write to establish identity or read-only access.

The same fail-closed rule SHALL apply to a returned identity row with an absent or NULL
`name`, `superuser` or `bypassrls`, or an empty name. A nonempty reported name SHALL be
retained exactly, including whitespace significant in quoted identifiers; otherwise it
SHALL be `unknown`. Role subject references SHALL preserve the same exact identity.
Evidence SHALL name the unreported identity fields,
and table/column write-privilege questions and exact runtime execution SHALL be withheld.
`bypasses_row_security` SHALL be true when either superuser or BYPASSRLS is positively
reported, false only when both flags are reported false, and null otherwise.
A positively reported superuser SHALL retain its existing writable evidence and true
row-security bypass even when another identity field is absent.

### Scenario: a refused identity question blocks

- **GIVEN** the identity query is refused with SQLSTATE `42501`, while mart questions would
  answer readable and every table-level and column-level write privilege would answer false
- **WHEN** the environment is collected with an exact runtime budget and access is graded
- **THEN** the role is `unknown`, read-only access is unestablished, row-security bypass is
  unknown, the runtime callable is not invoked, and `dwh.access` is block

### Scenario: an empty identity answer blocks

- **GIVEN** the same environment, with no rows returned by the identity query
- **WHEN** the environment is collected and access is graded
- **THEN** it has the same unknown role facts and block outcome as a refused identity query

### Scenario: an answered identity keeps the existing privilege rules

- **GIVEN** the identity query reports a name, nonsuperuser status and BYPASSRLS status,
  and every table-level and column-level write privilege is false
- **WHEN** the environment is collected
- **THEN** the role remains read-only and an eligible exact runtime query is executed
- **AND** an answered superuser, positive write grants and ClickHouse keep their existing rules

### Scenario: a partial or NULL identity answer blocks

- **GIVEN** the identity query returns a row with an absent or NULL identity field or an
  empty name, without positively reporting a superuser
- **WHEN** the environment is collected with an exact runtime budget and access is graded
- **THEN** the reported name and proven bypass facts are retained, unknown bypass is null,
  the role is not established read-only, evidence names the missing fields, no write-privilege
  question or exact runtime query is sent, and `dwh.access` is block

### Scenario: a proven superuser does not require a separate BYPASSRLS answer

- **GIVEN** the identity query reports `superuser=true` without a BYPASSRLS answer
- **WHEN** the environment is collected
- **THEN** the existing writable superuser evidence is retained, row-security bypass is true,
  and no write-privilege question or exact runtime query is sent

### Scenario: a quoted role name retains significant whitespace

- **GIVEN** the identity query reports a nonempty role name with leading/trailing whitespace
  or consisting entirely of whitespace
- **WHEN** the environment is collected and access is graded
- **THEN** the exact name is retained; a complete negative identity keeps the existing
  read-only rule, and a partial identity blocks with that exact role subject reference
- **AND** a whitespace-only identifier does not cause a validation error

## Requirement: a column-level write privilege on an in-scope mart makes the role writable

For every in-scope mart, the PostgreSQL-family collector SHALL ask, besides the table-level
`INSERT`, `UPDATE`, `DELETE` and `TRUNCATE` privileges, whether the role holds the `INSERT` or
`UPDATE` privilege on any column of that mart, through `has_any_column_privilege`. Any such
privilege SHALL make `RoleFacts.read_only` false, with evidence naming the privilege, that it is
held on columns, and the mart. A privilege the table-level question already reported SHALL NOT
be reported again as a column privilege. The question SHALL cover the whole mart, not only the
columns the in-scope metrics read. The collector SHALL NOT attempt a write to find out.

### Scenario: a column-only UPDATE grant blocks

- **GIVEN** a nonsuperuser on PostgreSQL whose answers for `dm.sales_daily` are false for every
  table-level write privilege, true for `has_any_column_privilege('dm.sales_daily', 'UPDATE')`
  and false for `has_any_column_privilege('dm.sales_daily', 'INSERT')`
- **WHEN** the environment is collected and access is graded
- **THEN** `read_only` is false, the role evidence names `UPDATE`, columns and `dm.sales_daily`,
  and `check_access` reports `access.role_writable` with severity block

### Scenario: a column-only INSERT grant blocks

- **GIVEN** the same role, with the column `INSERT` answer true and the column `UPDATE` answer
  false
- **WHEN** the environment is collected and access is graded
- **THEN** `read_only` is false, the evidence names `INSERT` on columns of `dm.sales_daily`, and
  `access.role_writable` is a block

### Scenario: a write grant on a column the metrics do not read still blocks

- **GIVEN** a question for `dm.sales_daily` whose columns are `date` and `revenue`, and a role
  whose column `UPDATE` answer for the mart is true
- **WHEN** the environment is collected
- **THEN** the collector asked `has_any_column_privilege` for the mart as a whole, not per asked
  column, and `read_only` is false

### Scenario: a table-level write privilege keeps its evidence

- **GIVEN** a role whose table-level `UPDATE` answer for `dm.sales_daily` is true and whose
  column `UPDATE` answer is therefore true as well
- **WHEN** the environment is collected
- **THEN** `read_only` is false and the evidence names `UPDATE on dm.sales_daily` once, without a
  second, column-level entry for the same privilege

### Scenario: a superuser stays writable without the column questions

- **GIVEN** a role the catalog reports as a superuser
- **WHEN** the environment is collected
- **THEN** `read_only` is false with today's superuser evidence

### Scenario: select-only roles stay read-only (false-reject control)

- **GIVEN** a role holding only table-level `SELECT`, or only column-level `SELECT` on some
  columns, or `SELECT` plus a column-level `REFERENCES` grant, so that every table-level and
  column-level `INSERT` and `UPDATE` answer is false
- **WHEN** the environment is collected and access is graded
- **THEN** `read_only` is true, there is no `access.role_writable`, and the existing readability
  of a column-granted mart is unchanged

### Scenario: a write privilege outside the scope is not asked about

- **GIVEN** a role whose only write privilege is on a table that no in-scope question names
- **WHEN** the environment is collected
- **THEN** no privilege question names that table and `read_only` is true

### Scenario: a writable role withholds the exact runtime probe

- **GIVEN** a question with a runtime budget and a role whose only write privilege is a
  column-level `UPDATE` on the in-scope mart
- **WHEN** the environment is collected
- **THEN** the exact reference query is not executed, and the gate records `dwh.access` as block

### Scenario: no question writes

- **GIVEN** any of the roles above
- **WHEN** the environment is collected
- **THEN** every statement the collector sent is a read; none is `INSERT`, `UPDATE`, `DELETE`,
  `TRUNCATE` or DDL

### Scenario: an unanswered column-write question cannot establish read-only access

- **GIVEN** a nonsuperuser whose table-level write answers are false, while the column-level
  INSERT/UPDATE question is refused or returns no rows
- **WHEN** the environment is collected
- **THEN** `read_only` is false, evidence names the unreported column INSERT/UPDATE privileges
  and the mart without claiming they are absent, the exact runtime query is not executed,
  and `dwh.access` reports `access.role_writable` with severity block
- **AND** a table-level write grant still keeps its existing positive evidence when the
  column question is unanswered

### Scenario: an unanswered table-write question cannot establish read-only access

- **GIVEN** a nonsuperuser whose table-level INSERT/UPDATE/DELETE/TRUNCATE question
  is refused or returns no rows, while the column-level INSERT/UPDATE answers are false
- **WHEN** the environment is collected
- **THEN** `read_only` is false, evidence names the unreported table write privileges
  and the mart without claiming they are absent, the exact runtime query is not executed,
  and `dwh.access` reports `access.role_writable` with severity block
- **AND** the column question is still asked; if it proves a column write grant,
  the role keeps its positive column-grant evidence and block outcome

### Scenario: partial or NULL write-privilege answers cannot establish read-only access

- **GIVEN** a nonsuperuser with a missing or NULL table INSERT/UPDATE/DELETE/TRUNCATE
  field or column INSERT/UPDATE field, including an empty returned row
- **WHEN** the environment is collected
- **THEN** read-only access is unestablished, evidence names the unreported privileges
  and mart, exact runtime SQL is withheld, and `dwh.access` blocks
- **AND** all in-scope marts and both privilege questions are still examined; a proven
  write grant retains its existing positive evidence and table/column deduplication
- **AND** complete negative answers retain read-only access and eligible runtime execution

### Scenario: PostgreSQL privilege handling leaves ClickHouse unchanged

- **GIVEN** a ClickHouse environment, read-only by its profile or by its grants
- **WHEN** the environment is collected
- **THEN** the role facts and the questions asked are the same as before this requirement

## Requirement: a role reachable through SET ROLE cannot hide a write privilege

After an answered identity that is not a superuser, the PostgreSQL-family collector SHALL ask
once which roles the session can switch to and whose powers the current role does not already
hold: every role that is a superuser or for which `pg_has_role(current_user, role, 'USAGE')` is
false — membership passes privileges on, but not the `SUPERUSER` attribute — and which is either
the session user or a role the session user may set — `pg_has_role(session_user,
role, 'SET')` when `server_version_num` is at least `160000`, `pg_has_role(session_user, role,
'MEMBER')` otherwise. The server SHALL choose the privilege word inside the question, so one
question serves every version. The answer SHALL carry each role's name and superuser flag.

A reachable superuser SHALL make `RoleFacts.read_only` false, with evidence naming that role as
a superuser reachable through `SET ROLE`; the privilege questions for reachable roles SHALL then
not be asked.

Otherwise, when roles are reachable, the collector SHALL ask for every in-scope mart, in one
question per mart naming those roles, the table-level `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`
and column-level `INSERT`, `UPDATE` privileges it asks for the current role. Any such privilege
SHALL make `read_only` false, with evidence naming the privilege, the mart, whether it is held
on columns, and the role it is reached through; a privilege reported at table level SHALL NOT be
reported again for the same role's columns.

An empty reachability answer SHALL mean that the session can switch to no other role. A refused
reachability question, a reachable role with a missing, NULL or empty name, a missing or NULL
superuser flag, a refused privilege question, a named role absent from its answer, and a missing
or NULL privilege field SHALL NOT establish read-only access: the evidence names what went
unreported without claiming it absent, and the remaining questions are still asked. A nonempty
reported role name SHALL be retained exactly. Positive evidence keeps priority, as for the
current role.

The collector SHALL NOT switch roles, reset them or write to find out. The identity question,
the superuser and unanswered-identity short-cuts (which ask no reachability question), the
current role's questions, `RoleFacts` fields and ClickHouse collection SHALL stay as they are.
A role made writable here withholds the exact runtime SQL, and `dwh.access` blocks through
`access.role_writable`.

### Scenario: a NOINHERIT member of a writable group blocks

- **GIVEN** a nonsuperuser whose own table-level and column-level write answers for
  `dm.sales_daily` are all false, and whose reachability answer is the nonsuperuser role
  `writer`, which holds table-level `INSERT` on `dm.sales_daily`
- **WHEN** the environment is collected and access is graded
- **THEN** `read_only` is false, the evidence names `INSERT`, `dm.sales_daily` and `writer` as
  reached through `SET ROLE`, and `check_access` reports `access.role_writable` with severity
  block

### Scenario: a column-only writer reached through SET ROLE blocks

- **GIVEN** the same session, with a reachable role `colwriter` whose only true write answer is
  the column-level `UPDATE` on `dm.sales_daily`
- **WHEN** the environment is collected and access is graded
- **THEN** `read_only` is false, the evidence names `UPDATE` on columns of `dm.sales_daily`
  through `colwriter`, and `access.role_writable` is a block

### Scenario: the session user reached with SET ROLE NONE blocks

- **GIVEN** an identity naming the current role `reader`, and a reachability answer naming the
  session user `login`, which holds table-level `INSERT` on `dm.sales_daily`
- **WHEN** the environment is collected
- **THEN** `read_only` is false and the evidence names `INSERT` on `dm.sales_daily` through
  `login`

### Scenario: a reachable superuser blocks without privilege questions

- **GIVEN** a reachability answer in which the role `su` is flagged superuser, next to a
  nonsuperuser role
- **WHEN** the environment is collected
- **THEN** `read_only` is false, the evidence names `su` as a superuser reachable through
  `SET ROLE`, and no privilege question for reachable roles is sent

### Scenario: every reachable role is asked about in one question per mart

- **GIVEN** two in-scope marts and two reachable nonsuperuser roles, where only the second role
  holds a write privilege, and only on the second mart
- **WHEN** the environment is collected
- **THEN** exactly one reachable-role privilege question is sent per mart, each naming both
  roles, and the evidence names the second role and the second mart

### Scenario: one question serves every server version

- **GIVEN** any answered nonsuperuser identity
- **WHEN** the environment is collected
- **THEN** the reachability question is sent once; it compares roles with `session_user`,
  excludes roles for which `pg_has_role(current_user, …, 'USAGE')` is true unless they are
  superusers, and lets the server choose `'SET'` when `server_version_num` is at least `160000`
  and `'MEMBER'` otherwise

### Scenario: an inheriting member of a superuser group blocks

- **GIVEN** a nonsuperuser whose own write answers are all false and which inherits the
  superuser role `su_grp`, so `pg_has_role(current_user, 'su_grp', 'USAGE')` is true, while the
  session user may set `su_grp`
- **WHEN** the environment is collected and access is graded
- **THEN** the reachability question keeps `su_grp` because it is a superuser, `read_only` is
  false, the evidence names `su_grp` as a superuser reachable through `SET ROLE`, and
  `access.role_writable` is a block

### Scenario: no reachable role, or none that writes, stays read-only (false-reject control)

- **GIVEN** an empty reachability answer, or reachable nonsuperuser roles whose every
  table-level and column-level write answer is false
- **WHEN** the environment is collected and access is graded
- **THEN** `read_only` is true, there is no `access.role_writable`, an eligible exact runtime
  probe runs as before, and the evidence names the reachable roles it checked, or says that
  `SET ROLE` reaches no other role

### Scenario: an unanswered reachability or reachable-role privilege question blocks

- **GIVEN** a refused reachability question, or a reachable role with a missing, NULL or empty
  name or a NULL superuser flag, or a refused reachable-role privilege question, or a privilege
  answer that omits a named role, or a NULL field in that answer
- **WHEN** the environment is collected with an exact runtime budget and access is graded
- **THEN** `read_only` is false, the evidence names what went unreported without claiming it
  absent, the exact runtime query is not executed, and `dwh.access` reports
  `access.role_writable` with severity block
- **AND** a proven write privilege of the current role or of another reachable role keeps its
  positive evidence

### Scenario: the short-cuts ask no reachability question

- **GIVEN** an identity reported as a superuser, or a refused, empty or partial identity answer
- **WHEN** the environment is collected
- **THEN** neither the reachability question nor a reachable-role privilege question is sent,
  and the role facts are the same as before this requirement

### Scenario: nothing is switched or written

- **GIVEN** any of the sessions above
- **WHEN** the environment is collected
- **THEN** every statement the collector sent is a `SELECT`; none is `SET`, `RESET`, `INSERT`,
  `UPDATE`, `DELETE`, `TRUNCATE` or DDL

### Scenario: ClickHouse collection ignores SET ROLE reachability

- **GIVEN** a ClickHouse environment, read-only by its profile or by its grants
- **WHEN** the environment is collected
- **THEN** the role facts and the questions asked are the same as before this requirement

## Requirement: unknown ClickHouse enabled roles are not an empty role set

Refused `system.enabled_roles` or any row with missing, NULL or empty `role_name`
SHALL leave the effective principal set incomplete. Successful `[]` SHALL remain
known-empty. Valid names SHALL be retained exactly in the grants question.
`readonly=1/2` SHALL still establish read-only; otherwise a proven write grant
SHALL retain its positive evidence, and incomplete roles without such a grant
SHALL block access and withhold exact runtime SQL with evidence naming the gap.

Incomplete roles SHALL yield `column_reads=None`. A successful empty policy catalog
SHALL remain `[]`; a nonempty or refused catalog SHALL leave `row_policies=None`.
Unknown reach SHALL produce `security.not_observed`, not invented unreadable columns,
policy applicability or unenforced classification. Classification requires known
column reach. A column absent from the filtered ClickHouse catalog with unknown
reach SHALL NOT be called physically absent; PostgreSQL absence and observed type
drift retain their rules. Known reach still allows a true absent-column finding.
The existing eligible runtime probe under `readonly=1/2` SHALL remain allowed.
Connection failures SHALL propagate. SQL shapes and public schemas SHALL NOT change.

## Requirement: ClickHouse write families include canonical leaf access types

The existing `ALTER`, `CREATE` and `DROP` write families SHALL match both the exact
group name and a canonical access type beginning with that name followed by a space.
Canonical `ALL` SHALL also be writable because it includes those write capabilities.
ClickHouse 24.8.14.39 reports `ALTER UPDATE`, `ALTER DELETE`, `CREATE TABLE` and
`DROP TABLE` from `system.grants`; these SHALL block grant-based read-only and exact
runtime execution, including grants reached through enabled roles. Evidence SHALL
retain the exact reported access type. Existing exact write grants retain their rules.
`readonly=1/2` retain priority; partial revoke rows are not positive grants. A known
positive leaf grant retains evidence even when enabled roles are unknown.
`SHOW CREATE`, `SHOW TABLES`, `ALTERED` and `SYSTEM DROP ...` do not match these families.
This is a lexical verb-family rule, including CREATE/ALTER/DROP engine objects;
it does not reproduce the entire upstream privilege hierarchy. Other privilege families,
SQL queries and PostgreSQL rules remain outside this change.

## Requirement: the read-only evidence names everything it checked

When the PostgreSQL-family role is found read-only, its evidence SHALL say that no in-scope mart
grants it `INSERT`, `UPDATE`, `DELETE` or `TRUNCATE`, that no column of those marts grants it
`INSERT` or `UPDATE`, and which roles reachable through `SET ROLE` were checked the same way —
or that `SET ROLE` reaches no other role.

### Scenario: the read-only evidence mentions the column privileges

- **GIVEN** a nonsuperuser with every table-level and column-level write answer false
- **WHEN** the environment is collected
- **THEN** `RoleFacts.evidence` names the table-level privileges and the column-level `INSERT`
  and `UPDATE` privileges it found absent

### Scenario: the read-only evidence names the roles SET ROLE reaches

- **GIVEN** a read-only nonsuperuser whose reachability answer is empty, and another whose
  reachability answer names `mid` and `reader2` with every write answer false
- **WHEN** the environment is collected
- **THEN** the first evidence says that `SET ROLE` reaches no other role, and the second names
  `mid` and `reader2` as checked
