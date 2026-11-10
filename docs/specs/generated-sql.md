# Capability: Generated SQL preserves identifier and string characters

The chart, source and metric-reference builders render sqlglot expressions through
`auto_bi.sql_render.render_sql`. DataLens display scaling and Superset SQL expressions
use its `quote_identifier` entry point. The tests in `tests/test_generated_sql_escaping.py`
cover both ClickHouse and PostgreSQL for chart/source/reference/DataLens SQL, and
ClickHouse for the existing Superset path.

## Requirement: identifiers retain their characters and statement structure

Generated quoted identifiers SHALL preserve backslashes, quotes and line feeds when
read by sqlglot in the target dialect. ClickHouse SHALL double each backslash before
the generator quotes the identifier. A name SHALL NOT add a clause or comment to the
statement. The generated SELECT SHALL remain acceptable to `guard_sql`.

### Scenario: a label contains a backslash before a quote

- **WHEN** a chart label contains `x\" FROM mart WHERE 1=1 --`
- **THEN** the parsed label equals that string, no comment is present, and after
  replacing the label with a marker the AST equals the ordinary-label AST
- **AND** the same comparison holds for source aliases, metric-reference keys,
  DataLens subqueries and scaling, and Superset own/source/ratio/KPI/heatmap expressions

Evidence: `test_identifiers_preserve_structure` and `test_superset_identifiers`.

## Requirement: PostgreSQL strings do not depend on standard strings

A generated PostgreSQL string holding a backslash SHALL use `E'…'`, doubling both
backslashes and single quotes. A string without a backslash SHALL retain its previous
literal form. For the tested values, the sqlglot reader and the independent string
reader SHALL recover the same value; the latter SHALL agree for
`standard_conforming_strings=on` and `off`.

### Scenario: filters contain escape-like text

- **WHEN** an `=`, `!=`, `>=`, `<=` or `in` filter contains a backslash before a quote,
  `a\nb`, `a\x27b`, two backslashes, a final backslash, quotes, a line feed or `¥`
- **THEN** flat, window, comparison, histogram and DataLens SQL retain the string
  value and the AST structure of the ordinary-value query in both dialects

Evidence: `test_filter_values_preserve_structure`, using `read_literals` from
`tests/test_catalog_literals.py` in addition to sqlglot and `guard_sql`.

### Scenario: a PostgreSQL filter holds both a backslash and a single quote

- **WHEN** a filter at any PostgreSQL differential site contains `a\'b`, `'\` or `\'`
- **THEN** its exact E literal doubles the backslash and writes the quote as `''`
- **AND** replacing that literal with the ordinary marker gives the ordinary query bytes

Evidence: `test_postgres_quote_bytes` covers all five filter operators at all six
PostgreSQL filter sites. `test_m20_text_differs_despite_same_sqlglot_value` shows that
writing the quote with a backslash changes the text even though sqlglot reads the same
value. This is a byte contract, not a server run with `backslash_quote=off`.

## Requirement: unrepresentable text is refused

Generated names and string values containing NUL or a surrogate SHALL raise
`SQLRenderRefusal`, a `ValueError` subclass, before a SQL string is returned. Its
message SHALL name the refused text using its escaped representation and contain
`NUL or surrogate`. The metric compiler SHALL catch only this renderer refusal in
`_compile_one`, record advisory `sql.compile_refused` with the metric ref as subject,
and emit no query for that metric. Other metrics SHALL still compile. An unrelated
`ValueError` SHALL propagate.

### Scenario: a name or filter contains invalid text

- **WHEN** a covered builder receives a name or filter containing NUL, U+D800 or U+DFFF
- **THEN** it raises the stated error; Superset expressions also refuse these names

Evidence: `test_unrepresentable_names_refused`, `test_unrepresentable_values_refused`
and `test_superset_unrepresentable_names_refused`.

### Scenario: a metric contract YAML contains NUL in a grain key or aggregate column

- **WHEN** the loaded contract is compiled with another valid metric
- **THEN** the SQL step records the advisory finding and the other metric's query
- **AND** `run_gate` writes a bundle containing that finding on both snapshots
- **AND** a budgeted refused metric keeps its plan question with empty dialect and SQL

Evidence: `test_refused_metric_is_advisory_and_other_metric_compiles`,
`test_refused_metric_gate_writes_bundle`, `test_refused_metric_gate_writes_files` and
`test_refused_metric_keeps_empty_plan_question` in `tests/test_sql_compile_refusal.py`.
`test_unrelated_render_value_error_surfaces` pins the narrow exception boundary.

### Scenario: a delivered BI spec contains a name the renderer refuses

- **WHEN** a valid Superset or DataLens spec reaches the renderer with a NUL column name
- **THEN** the BI step records `bi.compile_refused` with block status and no compiled payload
- **AND** the bundle can be read and verified

Evidence: `test_renderer_refusal_remains_a_bi_finding`.

## Requirement: ordinary SQL bytes remain stable

The ordinary SQL corpus SHALL retain the SHA-256 digests in
`tests/fixtures/generated_sql_sha256.json`: the original 21 chart/source/reference/adapter cases,
8 SQL fragments from the existing KPI and trend adapter fixtures, and 86 golden
fixture groups (43 fixtures in two dialects). A golden group joins its
chart SQL strings with LF in fixture order, including a final LF. The corpus also
pins names and filter values containing a single quote, double quote, backtick, LF,
TAB and non-ASCII text at every applicable site, including adapter and golden variants:
1416 digests in total. Superset metric fragments have no filter SQL. Source and reference
queries have no filters. All these digests were computed on the preceding implementation.

Backslashes, NUL and surrogates are outside this equal-byte corpus. The heatmap LPAD
name containing `"` is also outside it: its previous form did not double that quote.
OWN metric aliases already doubled it and remain in the equal-byte corpus.

### Scenario: ordinary inputs are compiled

- **WHEN** `ordinary_sql` renders the corpus
- **THEN** its keys and UTF-8 SHA-256 digests equal the recorded mapping

Evidence: `test_ordinary_sql_sha256`. The verifier version is at least 126 and the
check version remains 81 (`test_generated_sql_verifier_version`): serialization
changes compiled evidence, while check grading rules are unchanged.

### Scenario: a Superset heatmap name contains a double quote

- **WHEN** the LPAD fragment reads the name `a"b`
- **THEN** its exact text is `SELECT LPAD(CAST("a""b" AS VARCHAR), 2, '0')`
- **AND** its digest differs from the preceding form, which does not parse as that identifier

Evidence: `test_changed_name_bytes_and_head_reading` and
`tests/fixtures/generated_sql_changed.json` pin that form and 78 ClickHouse name forms
containing backslashes. The latter include 26 cases with `\'` or `\x27` where sqlglot
reads the preceding identifier unchanged; those cases prove a byte change only.

## Known boundaries

- These are offline reader and byte-preservation contracts, not a live server result.
- No guarantee is made here about the client encoding selected by a BI tool's own
  connection, conversion to a database encoding, or server limits on identifier length.
- Operator `raw_sql` and reviewed SQL are outside this generated-expression contract.
- Superset PostgreSQL support is outside this capability; its covered SQL fragments
  use the existing ClickHouse path.
- Advisor DDL suggestions and catalog queries are outside this SELECT-builder contract.
