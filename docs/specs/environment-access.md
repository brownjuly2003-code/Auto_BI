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

Known boundary, not covered here: a role created `NOINHERIT` and granted a writable group can
`SET ROLE` to that group and write, while both `has_table_privilege` and
`has_any_column_privilege` answer false for the role itself (live, PostgreSQL 16.15). The same
holds for table-level grants today.

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

- **GIVEN** the identity query reports a nonsuperuser and every table-level and column-level
  write privilege is false
- **WHEN** the environment is collected
- **THEN** the role remains read-only and an eligible exact runtime query is executed
- **AND** an answered superuser, positive write grants and ClickHouse keep their existing rules

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

### Scenario: ClickHouse is unchanged

- **GIVEN** a ClickHouse environment, read-only by its profile or by its grants
- **WHEN** the environment is collected
- **THEN** the role facts and the questions asked are the same as before this requirement

## Requirement: the read-only evidence names everything it checked

When the PostgreSQL-family role is found read-only, its evidence SHALL say that no in-scope mart
grants it `INSERT`, `UPDATE`, `DELETE` or `TRUNCATE`, and that no column of those marts grants it
`INSERT` or `UPDATE`.

### Scenario: the read-only evidence mentions the column privileges

- **GIVEN** a nonsuperuser with every table-level and column-level write answer false
- **WHEN** the environment is collected
- **THEN** `RoleFacts.evidence` names the table-level privileges and the column-level `INSERT`
  and `UPDATE` privileges it found absent
