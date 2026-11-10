# Evidence format versions — what each verifier and check version changed

`VERIFIER_VERSION` and `CHECK_VERSION` in `auto_bi/verification/release_gate.py` name the
evidence format a run was graded and written in. This file records what each version
changed, newest first. The code keeps only the newest few entries beside the constants;
`tests/test_evidence_format_versions.py` holds the two in step: every version from the first
recorded one up to the constant has exactly one entry here, and each entry the code keeps
reads the same here. A bump adds its entry in both places.

Versions before verifier 11 and check 7 have no entry here: the code did not describe them.
CHANGELOG names 1 → 2 and the transitions from verifier 7 → 8 and check 5 → 6 on; verifier
2 → 7 and check 2 → 5 are in git history only.

## Verifier version

Part of the idempotency key: a different verifier can derive a different verdict or
evidence artifact from the same inputs, so its bundles must not be reused (§7.6).

### Verifier 134

a column's type on a PostgreSQL kernel is compared by the name `format_type` prints for it, not by
the one the model writes (ADR 0010, F2): `float8` is `double precision`, `int4` is `integer`,
`varchar(n)` is `character varying(n)`, `timestamp` is `timestamp without time zone`, and so are the
other names of the manual's alias table but the serial types, `float(p)`, `numeric(p)`, `char` with
no length, `bpchar(n)` and the brackets of an array of any dimensions. Verifier 133 graded such a
pair `physical.type_drift` (review), and that drift accounted for a `42883` of the reference query
or of `max()` of the event-time column, which now ends the run. ClickHouse types are compared as
before. CHECK_VERSION moves to 84 with it: the rule of `dwh.physical` changed, and a run's identity
holds the verifier version alone.

### Verifier 133

a catalog question the engine refused is not read as an empty answer (ADR 0010, F1). A refused
`pg_attribute` or `system.columns` question leaves the mart's columns unknown, so `dwh.physical`
compares none of them and no drift accounts for a `42703`/`47` of the reference query or of `max()`
of the event-time column, which then ends the run. Verifier 132 graded each declared column whose
reach was not denied `physical.column_absent` (block) — on ClickHouse only with the grants answered
— and that drift accounted for those codes. A refused `pg_tables`/`pg_views` presence question is
now followed by `to_regclass`, where verifier 132 graded a mart the role reads
`access.table_absent` (block), and it asks `pg_matviews` too, where verifier 132 graded a mart
served as a materialized view the same. An answered catalog with no rows is read as before; check
grading is unchanged and CHECK_VERSION stays 83.

### Verifier 132

ClickHouse refusals are read by the error code where the error carries one (slice S8):
`497 ACCESS_DENIED`, `291 DATABASE_ACCESS_DENIED`, `60 UNKNOWN_TABLE` and
`81 UNKNOWN_DATABASE` are refusals whatever the words around them, and any other code is not,
even where the message quotes a refusal's words — a `47 UNKNOWN_IDENTIFIER` for a column
called `access_denied_at` is weighed as the drift it accounts for, or ends the run. The
wording is read only for an error that carries neither a code nor a SQLSTATE. Verifier 131
took such an answer for a refusal; check grading is unchanged and CHECK_VERSION stays 83.

### Verifier 131

a reference query the PostgreSQL mart no longer fits is a fact about the mart too
(slice S7b), as verifier 130 made it on ClickHouse: where the catalog accounts for the state —
a column the query names that `dwh.physical` grades as gone for `42703 undefined_column`, or
as retyped for `42883 undefined_function` — EXPLAIN leaves the plan unpriced, naming those
columns, and its exact SELECT is not sent; `42501 insufficient_privilege` from the exact
SELECT is a refusal; a `max()` of an event-time column whose drift accounts for the state
leaves the rows counted and undated. The evidence names the SQLSTATE and its condition name
alone, and a `42703`/`42883` the catalog does not account for still ends the run. On both
engines that plan's evidence now says the drift is taken to mean the query no longer fits the
mart: the drift answers the code by its kind, not by where the query failed. Verifier 130 let
those answers end a PostgreSQL run with no report and no bundle; check grading is unchanged
and CHECK_VERSION stays 83.

### Verifier 130

a reference query the mart no longer fits is a fact about the mart, not a broken run
(ClickHouse, slice S7): where the catalog accounts for the code — a column the query names
that `dwh.physical` grades as gone for `47 UNKNOWN_IDENTIFIER`, or as retyped for
`43 ILLEGAL_TYPE_OF_ARGUMENT` — EXPLAIN ESTIMATE leaves the plan unpriced, naming those
columns, and its exact SELECT is not sent; `497 ACCESS_DENIED` from the exact SELECT is a
refusal; a `max()` of an event-time column whose drift accounts for the code leaves the
rows counted and undated. The evidence names the code alone, and a `43`/`47` the catalog
does not account for still ends the run. `freshness.unknown` over rows with no newest
event time says the rows are there. Verifier 129 let those answers end the run with no
report and no bundle, so the drift `dwh.physical` reports was never reported; check
grading is unchanged and CHECK_VERSION stays 83.

### Verifier 129

a bundle keeps, beside each snapshot, the canonical closure of every dbt metric a
binding of that snapshot names a closure digest for (ADR 0008, slice S4), and re-verifying
a bundle checks those closures against the bindings: `evidence.closure_missing`,
`evidence.closure_mismatch`, `evidence.closure_unbound`, `evidence.closure_unverified`. A run
without a bound metric keeps no closure.

### Verifier 128

native source version 4 (ADR 0008, slice S3): the node of a metric whose formula a dbt
semantic manifest defines carries its binding and, where the formula resolves from the
artifact the snapshot read, the typed formula, which impact and reference SQL then read;
without the artifact, or where it does not resolve, the formula stays `external_ref` and
the resolution's gaps join `gap.opaque_metric_formula`. Snapshots, the diff and every check
over a bound metric can change; a snapshot without one keeps its digest.

### Verifier 127

PostgreSQL mart questions preserve schema and table case in relation-name
arguments for privileges, ownership, columns and row security. Both engines use the
same two-part mart name for presence and COUNT/MAX. Collected facts and
resulting reports can change; check grading is unchanged and CHECK_VERSION stays 81.

### Verifier 126

generated SQL preserves ClickHouse identifier backslashes and writes PostgreSQL
string values containing backslashes as E literals. NUL and surrogates are refused.
Reference SQL records sql.compile_refused as advisory and omits the refused query.
Compiled SQL and BI payload evidence can change; check grading remains at version 81.

### Verifier 125

shared root-manifest checks bind artifact reads to the requested run;
inspection still reports noncanonical bytes. Negative float tolerances fail asset load,
before result comparison or check grading. Supplied cell validation is unchanged.
CHECK_VERSION remains 81: no check's grading rule changes.

### Verifier 124

capture and runtime require checked read-only sessions. Capture streams under
positive row, text-byte and server-time limits; catalog/statistics questions have
their own server timeout. Capture size excess returns no result; an environment query
timeout ends collection rather than becoming an access or freshness fact.
ClickHouse mode 1 capture/catalog requires a positive session timeout no greater than
the configured limit and throw mode; runtime leaves its bounds unmeasured in mode 1.
Reviewed SETTINGS cannot replace protected limits. Empty metadata wraps SQL on new lines.
All eleven ClickHouse overflow modes require throw; per-query limit/offset are zero,
mode 1 also requires unchanged zero limit/offset, and reviewed profiles are refused.
PostgreSQL capture rolls back its savepoint before metadata; capture/runtime roll back
their transactions. Preparation errors are separate; ClickHouse timeout requires code 159.
PostgreSQL rollback exit requires INTRANS for a savepoint and IDLE for the outer transaction.
Check grading is unchanged; CHECK_VERSION remains 81.

### Verifier 123

PostgreSQL mart and schema ownership, including inherited and SET ROLE
privileges, prevents read-only evidence after DML revocation. Per-mart facts carry
the current owner's row-security bypass together with FORCE.
Reports also ignore policies when the mart's row-security switch is false and
describe established bypass exemptions without claiming a difference of rows.

### Verifier 122

catalog values preserve backslashes as well as quotes: ClickHouse database
and table names use its string escaping, and PostgreSQL values containing a backslash
use E strings. Gate PostgreSQL connections request and check UTF8 client encoding
and standard_conforming_strings=on before each explicit execute or stream and transaction
start. Capture also checks after reading its result inside its explicit transaction,
detecting local changes still in effect then. Transaction exit has no separate check;
changes restored within a statement are not detected. Earlier sessions could change
characters during encoding or interpret plain-string backslashes; questions could name
another object, gain a condition or fail to parse, changing facts or captured results.
A changed session now refuses the next statement or captured result.

### Verifier 121

an issue of the validation report names a file by the place of the validated path
that reached it, `$1` for the first path given, and the names below that path —
`$1/notes/x.yaml` — never by the text of that path; a path that cannot be gone through is
one issue, however many validated paths reach it. Earlier verifiers named the file by the
path it was reached by: the same files given by another spelling of the paths, or from
another checkout directory, gave another report and run id once an issue named a file,
and a path that is not there, given twice, was two issues.

### Verifier 120

the newest event time is spelled once, in UTC with `Z`, and read in one of three
forms: a day and a clock with no zone in the one timezone the metrics declare for the
column, a day as its 00:00 there, and a UTC moment as the instant it names. Verifier 119
sealed `isoformat()` of what the driver handed back — one instant in as many texts as zones
the driver read it in, so a second run of the same inputs into one evidence directory was
refused — and read a day and a clock in UTC. No one declared zone
(`freshness.event_time_zone_unknown`) and a reading after `created_at`
(`freshness.event_time_ahead`) are review, and text in none of the forms is
`freshness.unreadable_event_time`.

### Verifier 119

a symbolic link below the contracts directory of a semantic ref is read as its text,
the text git commits for it, and not through: git commits a link as its text, and a
checkout makes the link where `core.symlinks` is true and writes a file of that text where
it is false. Verifier 118 read the link through: one commit was two snapshots by the
checkout it was read from — the contract the link leads to, or a file of its text — and two
run ids.

### Verifier 118

the validation report is one report on every system: it names a file by its path
with `/` between the names, and takes what a validated directory holds in the order of its
paths, name by name, by code point with case. Verifier 117 joined the names by the
separator of the system and, on Windows, ordered the files without case: the same files
read on Windows and on POSIX gave issues of files in other words and in another order, and
of an id two files define the run was handed the other contract — another report and run
id.

### Verifier 117

a snapshot freezes its contracts in an order of what they hold: a file that does not
load by file name, then bytes; a contract that loads by metric id, version, then bytes.
Verifier 116 took the order its files were listed in — the directory first and, on
Windows, without case: a contract moved between the directories of `metrics/`, or the ref
read on another system, put its gaps in another order — another snapshot and run id with
the source and canonical digests unchanged — or the nodes of an id two contracts define,
and with them the canonical digest. The checks a duplicated id leaves not run no longer
say a diff compares the contract whose file stands last.

### Verifier 116

a contract file is read once, however many of the validated paths reach it: a
directory and a file in it, one directory twice, one file spelled two ways or through a
link. Verifier 115 read such a file again, and its second reading was
`contract.duplicate_id`: a contract set that holds each id once blocked.

### Verifier 115

a moment is text of RFC 3339's one UTC spelling and no other: a metric contract or a
verified asset reviewed at `2026-08-21T09:00Z`, a week, a comma or text after the sixth
digit of a fraction fails its schema, and so does a recorded result attested so. Verifier
114 took what `datetime.fromisoformat` read: such a contract passed `contracts.validate`,
and a line after the sixth digit was sealed as part of the moment.

### Verifier 114

a product that repeats a dashboard ref in `delivery.dashboards`, or a format in
`delivery.brief_formats`, fails its schema (`contract.invalid`): its run is refused, and
the run of another product of its paths blocks. Verifier 113 compiled each entry: a spec
that compiled sealed its payloads under one id twice, and the run ended in a traceback; a
dashboard with no spec, an invalid one or one the compile refused was named twice in a
bundle.

### Verifier 113

the id of an equivalent result's claim and of a compiled BI payload ends in the full
SHA-256 of the ref it is about (with the compiler's id of the payload), after the slug it
had. Verifier 112 named them by the slug alone, which folds `_`, `-`, `/` and `@` into one
`-`: two equivalent assets or two compiled dashboards of one slug ended the run in a
traceback, no bundle written.

### Verifier 112

the spec of a delivered dashboard is read from the path of its whole ref, every segment
but the last a directory (`sales/weekly_margin@7.yaml`). Verifier 111 read the file of the
last segment, so `dashboard://ops/weekly_margin@7` could compile, and chart impact could read,
the layout of another dashboard, and a dashboard nobody supplied a spec for passed as compiled.

### Verifier 111

a semantic ref that holds two or more contracts of one metric id carries the blocking
gap `gap.metric_duplicate`, one to the id, and the checks a contract that did not load leaves
not run — the diff, impact, assets, both result checks and the reference SQL — are not run.
Verifier 110 compared the contract of the id whose file stood last, so moving a file between
directories of the metrics could turn `block` into `pass_with_advisory`.

### Verifier 110

a snapshot's source digest holds the digest of every file it was read from: metric
contracts of one file name in two directories of the metrics, or the model and a contracts
file named `model`, are kept under that name as the sorted list of their digests, repeats
included; a ref without such a name keeps its source digest. Verifier 109 kept the digest
of the last file read under a name, so a change to the others, or to the model, that
neither the graph nor a gap carries moved neither the snapshot nor the run id.

### Verifier 109

validated directories include JSON contracts alongside YAML; previously skipped
invalid or duplicate JSON can block, and JSON metrics can resolve unknown references.
A directory under them with a contract suffix (`archive.json/`, `old.yaml/`) is walked,
no longer read as a contract.

### Verifier 108

product, metric and dashboard refs in the summary are literal text.

### Verifier 107

semantic impact subjects in the summary are literal text.

### Verifier 106

all check explanations in the summary are literal text.

### Verifier 105

chart impact explanations in the summary are literal text.

### Verifier 104

semantic change explanations in the impact summary are literal text.

### Verifier 103

whole chart gap explanations in the impact summary are literal text.

### Verifier 102

chart references and gap subjects in the impact summary are literal text.

### Verifier 101

owner names in the impact summary are literal text, not Markdown instructions.

### Verifier 100

the environment name in the summary heading and the whole `results.provenance` detail
are literal text, where verifier 99 let Markdown and line breaks in an environment name
add a false Verdict or Owner action line. Findings, claims, the JSON payload and check
grading are unchanged; check version stays 73. (Reconstructed from commit `4c3d4d0` and
its status log entry: the chronicle in the code skipped this version.)

### Verifier 99

the summary names the validation issues of other products already sealed in
contracts.validate, explicitly outside this run's verdict. Check payloads are unchanged.

### Verifier 98

refused ClickHouse runtime settings leave cost bounds unmeasured. A setting name
alone no longer proves an exceeded bound, and row-limit errors do not prove byte excess.

### Verifier 97

a table, a column or a join a model declares more than once is one node of the graph
and a named repeat when its declarations are alike, and a named conflict and no node when
they are not. Verifier 96 kept a node for each declaration: two refs that differed in one
of two declarations of a table were graded as unchanged, and a ref that repeated a
declaration word for word had another canonical digest and no change to show for it.

### Verifier 96

a delivered dashboard whose spec validates and whose compile is refused is listed
by bi.contract with what the compile said, and the check seals the list, empty or not.
Verifier 95 let what the compile raised end the run: no report and no bundle.

### Verifier 95

a table a model declares with no name is a gap.table_unnamed gap of the snapshot,
and neither it nor its columns are in the graph. Verifier 94 kept it as a table whose id
is empty text: a run over two refs that held it alike was graded without the gap, and a
run over refs that differed in it ended in an exception while they were compared.

### Verifier 94

a YAML file that spells a surrogate in an escape is not read. Under the validated
paths it is a contract.unreadable issue the gate grades and seals, a metric contract of a
semantic ref is a gap.contract_invalid gap, and any other file of a run ends the run
before any bundle. Verifier 93 read the string PyYAML builds of the escape: where a
schema took it, the run was graded with it or ended in an exception.

### Verifier 93

a directory under the validated paths that the filesystem refuses to list is a
contract.unreadable issue of the validation report the gate grades and seals, where
verifier 92 read it as an empty directory: the same working tree gave a report without
the issue and a run that could pass. A directory of a semantic ref or of the verified
assets that cannot be listed now ends the run before any bundle, where verifier 92
froze the ref and loaded the assets without what the directory holds.

### Verifier 92

contracts.validate grades the validation issues of the product it verifies and of
loading the validated paths, and seals what the rules found for another product of those
paths apart, as other_product_issues. Verifier 91 graded every issue of the report, so
another product's unresolved pin, undeclared dimension, retired or draft metric or foreign
governing policy blocked a release it says nothing of, or sent it to review.

### Verifier 91

the run identity covers every static input the gate is handed: the validation
report, the validated contracts, both snapshots whole, the candidate model, the dashboard
specs and the verified assets. Verifier 90 keyed on the product and the two canonical
digests, so two runs that differed in any of those shared a run id, whatever each found.

### Verifier 90

contracts.validate answers the three validation rules that read a metric contract
(dimensions, status, governing policies) on the candidate's definition of every compared
pin and seals the rule issues, where verifier 89 sealed the answers on the validated
copy alone.

### Verifier 89

contracts.validate compares the validated contract of every pinned metric with
the candidate's definition at the pinned version and seals the difference codes,
where verifier 88 compared the version alone.

### Verifier 88

assets.invalidated resolves every metric pin of every verified asset in the
candidate snapshot and seals the graded pins, where verifier 87 read an asset's
metrics by id only.

### Verifier 87

a ClickHouse own-grant row with no access type leaves the grants answer incomplete:
the role is not read-only by its grants and column reach is unknown, where verifier 86
read the row as no grant.

### Verifier 86

a PostgreSQL-family kernel that predates row-level security (9.4, the kernel of
Greenplum 6 and Greengage 6) is graded where verifier 85 raised on the identity
question; the sealed environment facts gain server_version_num, so every environment
payload differs from verifier 85.

### Verifier 85

contracts.validate resolves every product metric pin in the candidate snapshot,
so another version of a pinned metric, several contracts of it or none blocks where
verifier 84 passed.

### Verifier 84

the superuser flag of the current PostgreSQL-family role is read from pg_roles,
so a current role without LOGIN is graded by its privileges where verifier 83 blocked
as unreported, and a NOLOGIN superuser gets the existing superuser evidence.

### Verifier 83

a ClickHouse role granted to the session user but not enabled is now asked about
through system.role_grants and system.grants of the reached roles, so a write grant
verifier 82 left unseen on a role `SET ROLE` reaches blocks dwh.access where it passed,
and role names are written in those questions with every backslash and quote doubled.

### Verifier 82

snapshots carry each metric's declared join paths (native projection version 3),
moving the canonical digest of every native snapshot with metrics; a snapshot without
metrics keeps its digest. semantic.diff names a removed metric join path as block and
a changed cardinality as review where it was semantic.unchanged, an added path as advisory.
semantic.gaps blocks on a metric join path with an endpoint the model does not declare
or declared with more than one cardinality.

### Verifier 81

snapshots carry metric freshness (native projection version 2), moving the
canonical digest of every native snapshot with metrics; a snapshot without metrics keeps
its digest. semantic.diff names relaxed freshness as review where it was
semantic.unchanged, and tightened or restated freshness as advisory. Fixed windows are
compared in exact integer seconds however many digits a component has: windows too long
for timedelta or for one int() conversion now grade instead of raising in semantic.diff,
dwh.freshness and the provenance stale-capture rule.

### Verifier 80

the same inputs now give bi.contract evidence with an invalid list and no BI
payloads for delivered specs that do not validate against the candidate model.

### Verifier 79

the PostgreSQL-family role probe also asks which roles the session user can
`SET ROLE` to and their table-level and column-level write privileges per in-scope
mart; a superuser group stays in that question even when the current role inherits
it, because membership does not pass `SUPERUSER` on. A role verifier 78 recorded
read-only although a reachable role could write (or a reachable superuser, an
inheriting member of a superuser group, or an unanswered reachability question) is
now not read-only, and `dwh.access` blocks with `access.role_writable`; the
read-only evidence names the reachable roles it checked. ClickHouse is unchanged;
historical bundles are not rewritten.

### Verifier 78

absent readable marts no longer produce a fresh outcome without an age comparison.

### Verifier 77

security.not_run exposes zero actual column/policy observations in the owner summary.

### Verifier 76

physical.not_run exposes zero actual column/layout comparisons in the owner summary.

### Verifier 75

cost facts for ineligible questions no longer produce cost.within_budget;
cost.not_run exposes zero eligible comparisons in the owner's What was not done section.

### Verifier 74

clean cost evidence counts checked plan-row and exact-runtime byte/time ceilings
against all declared bounds, without extending partial observations to all queries.
Grading and CHECK_VERSION are unchanged.

### Verifier 73

security clean evidence counts confirmed requested column reads and observed
row-policy reports per mart/event-time question, without extending a partial observation
to the whole scope. Grading and CHECK_VERSION are unchanged.

### Verifier 72

freshness clean evidence names the number of compared mart/event-time questions,
so a fresh readable subset no longer claims that every in-scope mart was dated.
Grading and CHECK_VERSION are unchanged.

### Verifier 71

physical clean evidence counts only answered column types and declared layout
fields on attributable, existing, readable marts. Unobserved facts no longer appear
verified. Grading and CHECK_VERSION are unchanged.

### Verifier 70

ClickHouse ALTER/CREATE/DROP families include canonical space-delimited leaf
access types, and canonical ALL includes write capabilities. Positive grants block
grant-based read-only and exact runtime SQL,
retaining their reported names in evidence. Profiles and partial revokes keep their rules.

### Verifier 69

incomplete ClickHouse enabled roles no longer establish grant-based read-only
access or effective column/policy reach. Profiles 1/2 and proven write grants retain
their rules. Unknown reach cannot prove classification unenforced or a hidden CH column
physically absent. SQL questions, public schemas and historical bundles are unchanged.

### Verifier 68

missing or NULL PostgreSQL-family write-privilege fields no longer establish
read-only access. Evidence names unreported privileges and their mart; exact runtime
SQL is withheld and dwh.access blocks. Proven grants retain their positive evidence.
SQL questions and historical bundles are unchanged.

### Verifier 67

a partial PostgreSQL-family role-identity row, NULL identity field or empty name
no longer establishes read-only access. Known names and proven row-security bypass are
retained; unknown bypass stays null. Write-privilege questions and exact runtime SQL
are withheld, and dwh.access blocks. Proven superusers keep their existing evidence
even with partial identity rows. Historical bundles are not rewritten.

### Verifier 66

a refused or empty PostgreSQL-family role-identity answer no longer establishes
read-only access. The role and row-security bypass remain unknown; write-privilege
questions and exact runtime SQL are withheld, and dwh.access blocks with
access.role_writable. Its detail describes unestablished read-only access rather than
asserting that an unknown role can write. Historical bundles are not rewritten.

### Verifier 65

an unanswered PostgreSQL-family table-write privilege question no longer
establishes read-only access when column-write answers are negative. It blocks
dwh.access with access.role_writable and withholds exact runtime SQL; evidence names
unreported table privileges. Positive column grants keep their evidence, and the
questions sent are unchanged. Historical bundles are not rewritten.

### Verifier 64

the PostgreSQL-family role probe also asks column-level INSERT/UPDATE privileges
(has_any_column_privilege) per in-scope mart. A role verifier 63 recorded read-only with
only a column-level write grant is now writable: dwh.access blocks with
access.role_writable. Read-only role evidence names the column privileges it checked;
an unanswered column-write question also blocks and withholds exact runtime SQL.
ClickHouse is unchanged. Historical bundles are not rewritten.

### Verifier 63

a non-finite value compares by kind. Same-sign infinities, including different
spellings and float overflow, move from value.changed BLOCK to equal. Expected NaN
moves from BLOCK to value.expected_nan REVIEW unless another finding proves a
difference; decimal sNaN against itself moves from written-form equal to REVIEW.
A float key written nan no longer reports its own cell. Candidate NaN against a
number, opposite infinities and infinity against a finite value stay value.changed;
decimal infinity against itself stays equal. Historical bundles are not rewritten.

### Verifier 62

float tolerance compares the exact binary64 difference with the exact declared
decimal. An absolute or relative excess smaller than binary64 rounding now gives
value.changed where 61 saw equivalent; an exact relative difference within tolerance
now stays equivalent where the rounded ratio rejected it. Grading moves both ways at
the rounding boundary. An infinity against a finite value or the opposite infinity was
equal under an absolute tolerance beyond the binary64 range, which rounded to infinity;
it is now value.changed. Historical bundles are not rewritten.

### Verifier 61

Environment readings record their event-time column; every check grades each
(mart, event-time column) question from its own reading. A multi-clock mart previously
graded from its first reading could pass with a stale clock or a denied column (now
freshness.stale REVIEW / security.column_unreadable BLOCK / access.table_unreadable
BLOCK), or report a fresh clock stale (that finding is gone). Unattributable readings
give access.reading_unattributed REVIEW; freshness.stale names its clock and evidence
facts carry event_time_column. Historical bundles are not rewritten.

### Verifier 60

integer/decimal business keys and aggregate group_by values are identified by number.
The same inputs move both ways: rescaled keys previously different (BLOCK, row.missing /
row.added) now match; two written forms of one key previously paired by spelling (possibly
equivalent, PASS) are now insufficient / key.not_unique (REVIEW). A share_sum or
partition_conservation group split by scale (possibly insufficient or different) is now
one group. The conflicting-totals detail lists totals as numbers, not `Decimal(...)` reprs.
Historical bundles are not rewritten.

### Verifier 59

`results.reconcile` reads supplied columns by name after the schema step.
Permuted columns with changed named values move from equivalent (PASS, sealed claim)
to different (BLOCK); expected named values or a moved key move from BLOCK to equivalent.
Failed candidate aggregate checks name the row by its own key. Historical bundles stay intact.

### Verifier 58

A CTE table exemption requires its declaring query to enclose that table reference.
Nested or sibling declarations no longer hide physical tables from governance/schema-RBAC
or exempt denylisted bare names. Whole-AST extraction and name comparison stay unchanged.

### Verifier 57

ClickHouse table extraction and SQL guard compare CTE exemptions case-sensitively.
A differently cased declaration no longer hides a physical table from governance or
schema-RBAC, or exempts a denylisted bare table. PostgreSQL keeps its existing comparison.

### Verifier 56

ClickHouse governance applies CTE reachability to `JOIN ... USING` keys. The
shared extractor stays whole-AST; only the reviewed-query advisory narrows to keys in
query parts that actually run.

### Verifier 55

ClickHouse governance applies CTE reachability to `COLUMNS(...)` selectors as it
already does for column-reading stars. The shared extractor stays whole-AST; only the
reviewed-query advisory narrows to selectors in query parts that actually run.

### Verifier 54

ClickHouse reachability binds named CTE and scalar WITH references case-sensitively.
Differently cased declarations no longer hide an outer read or open an unread sibling;
governance star reasons and chart stars, selectors, JOIN keys and getter reads can change.

### Verifier 53

ClickHouse reviewed-query governance reports column-reading stars only in reachable
query parts, using the same CTE gates as chart impact. Unread named CTE and scalar WITH
stars no longer leave the "it selects *" advisory; named reads and other remainders stay.

### Verifier 52

PostgreSQL ORDER BY / DISTINCT ON and set-operation output resolution preserve
already known strong output names through CAST and postfix typecasts. Unary plus still
prevents the implicit name; argument, FILTER and window input reads remain graded.

### Verifier 51

PostgreSQL ORDER BY / DISTINCT ON and set-operation output resolution also
recognize only Window-wrapped unaliased ROW_NUMBER outputs. Its PARTITION BY and inner
ORDER BY expressions remain reads; bare row_number() and other window functions do not.

### Verifier 50

PostgreSQL ORDER BY / DISTINCT ON and set-operation output resolution also
recognize Window-wrapped unaliased COUNT, SUM, AVG, MIN and MAX outputs. Window
arguments, FILTER predicates, PARTITION BY and inner ORDER BY expressions remain reads.

### Verifier 49

chart impact distinguishes ClickHouse's empty `map()` constant from a Superset
`{{ ... }}` template placeholder, so only the latter leaves operator SQL partially read.

### Verifier 48

PostgreSQL ORDER BY / DISTINCT ON and set-operation output resolution now also
recognize FILTER-wrapped unaliased COUNT, SUM, AVG, MIN and MAX outputs, including
parenthesized projections. Aggregate arguments and FILTER predicates remain reads;
existing reviewed queries can lose a phantom field REVIEW or sourceless advisory.

### Verifier 47

PostgreSQL ORDER BY / DISTINCT ON can use the implicit output names of direct
unaliased AVG, MIN and MAX calls, including the left branch of a set operation.
Existing reviewed queries can lose a phantom field REVIEW or sourceless advisory.

### Verifier 46

PostgreSQL named-table and parenthesized-JOIN aliases with column lists leave
references to declared output names as an advisory: their physical ordinal lineage
is not proven by the snapshot.
Existing queries can move from a false field REVIEW or clean settlement to the advisory;
named-table references outside the declared alias list keep ordinary physical grading.

### Verifier 45

PostgreSQL NATURAL JOIN leaves an advisory for keys selected from joined-source
output names, potentially dependent on physical schema. Existing queries can gain it;
check 26 records the changed meaning of assets.invalidated.

### Verifier 44

PostgreSQL ORDER BY / DISTINCT ON can also use an unaliased SUM output's implicit
name, including the left branch of a set operation. Existing reviewed queries can lose
a phantom field REVIEW or sourceless advisory; check 25 records that.

### Verifier 43

PostgreSQL ORDER BY / DISTINCT ON can use an unaliased COUNT output's implicit
name, including the left branch of a set operation, instead of a phantom input read.
Existing reviewed queries can lose false governance findings; check 24 records that.

### Verifier 42

ClickHouse operator SQL rooted at INTERSECT/EXCEPT can now name field reads where
the same text previously left chart.opaque_sql. Set-operation LIMIT/OFFSET subqueries
also contribute field reads; SELECT INTO is rejected. Reviewed queries of the new root
shape were previously rejected; changes to existing queries are covered by check 23.

### Verifier 41

a parenthesized ClickHouse ARRAY JOIN expression no longer creates a phantom
column or crashes governance. Its outer AS replaces an inner AS, so a later use of
the discarded name becomes a column read and may add review; chart reads can move
in either direction. Parenthesized query items keep their query scope.

### Verifier 40

PostgreSQL-family ORDER BY / DISTINCT ON expression names become input reads,
potentially adding REVIEW or advisory findings. Case-folded whole output names stop
producing phantom input REVIEW; a set operation's ORDER BY output name stops producing
a sourceless advisory. A parenthesized query's trailing ORDER BY now grades names it
previously ignored and can add findings; GROUP BY +name moves from output-column
advisory to input REVIEW. Unary plus on an unaliased projection does not create a
plain-column output name. ClickHouse and chart impact are unchanged.

### Verifier 39

a PostgreSQL-family GROUP BY name that may be a SELECT output column moves REVIEW
`asset.query_ungoverned_field` to advisory `asset.query_fields_unchecked`; without local
sources it stops being a remainder. Aggregate/window outputs, expressions and qualified
names retain input reads; ClickHouse and chart impact are unchanged.

### Verifier 38

governance now reports `JOIN ... USING` keys as an advisory remainder because the
shared key cannot be attributed to one table. Explicit neighboring column findings stay.

### Verifier 37

an ordinary three-part column naming one unaliased named-table source
in its own SELECT no longer opens a scalar WITH named like its database.
Conflicting source names remain conservative. Evidence only; CHECK_VERSION stays 18.

### Verifier 36

an ordinary two-part column qualified by one explicit named-table alias
in its own SELECT no longer opens an equally named scalar WITH in operator SQL.
False chart reads and partial-read gaps disappear; CHECK_VERSION does not move.

### Verifier 35

a nested named CTE shadows an outer CTE of the same name for unambiguous
operator SQL references. An outer CTE that only the inner reference appeared to
open no longer adds false chart reads or partial-read gaps. Evidence only;
CHECK_VERSION does not move.

### Verifier 34

operator chart impact ignores `JOIN ... USING` keys in unread named CTEs or
scalar WITH bodies. A tile verifier 33 called `chart.sql_partial` may now be read
in whole and ruled out for a changed metric. Reachable JOINs keep the gap;
CHECK_VERSION does not move because asset checks are unchanged.

### Verifier 33

operator chart impact ignores `COLUMNS(...)` selectors in unread named CTEs or
scalar WITH bodies. A tile that verifier 32 called `chart.sql_partial` may now be read
in whole and ruled out for a changed metric. Selectors reached by the statement keep
the partial-read gap. CHECK_VERSION does not move: asset governance still scans all AST.

### Verifier 32

field-level governance now reports a ClickHouse `COLUMNS(...)` selector as an
incomplete physical-column read, where verifier 31 could return a false clean result.
Named column findings remain alongside the new advisory. The selector's evidence is
also carried through the typed `ColumnReads` remainder, so an old bundle does not answer
today's field-read question.

### Verifier 31

a nested scalar WITH declaration shadows an outer one in operator SQL, so chart
impact drops false `reads_field` and `chart.sql_partial` evidence from an outer alias
that only the inner alias was read as. An actual outer read remains visible. Chart
evidence only; CHECK_VERSION does not move.

### Verifier 30

A direct COUNT(*) is a row count, not a column-reading star. Governance drops the
advisory `asset.query_fields_unchecked` whose only reason was a COUNT(*) star; chart impact
adds `chart.sql_partial` for a tile that counts DISTINCT *, where it resolved to "not this
tile" or to its other reads alone.

### Verifier 29

ClickHouse SETTINGS names and values are no longer field reads. Their phantom REVIEW
`asset.query_ungoverned_field`, advisory `asset.query_fields_unchecked`, chart
`reads_field`, and `chart.sql_partial` findings go away. `additional_table_filters` moves
from that phantom REVIEW to the advisory `asset.query_fields_unchecked` and a
`chart.sql_partial`, because its row-filter SQL is not read. Both directions.

### Verifier 28

unqualified inputs in HAVING and QUALIFY are reads. The only direction is added
evidence: a new REVIEW `asset.query_ungoverned_field`, a new advisory
`asset.query_fields_unchecked`, and a new chart `reads_field`; no finding is removed.

### Verifier 27

ClickHouse inline and ARRAY JOIN aliases are query-level aliases, so the same
operator SQL can lose a phantom `reads_field` on the alias. An inline definition in
HAVING or QUALIFY that earlier attribution skipped is now graded where written, so it
can name a real read (for example `internal_cost_raw` in `(internal_cost_raw AS y)`).
ARRAY JOIN still reads its source array. Both directions, so a verifier 26 report does
not answer today's chart-read question.

### Verifier 26

a raw-SQL tile's `chart.metric_unread` is decided per dashboard, against
the changed metrics the in-scope products delivering that exact pinned
dashboard ref declare, so the same inputs can carry fewer `chart.metric_unread`
gaps than verifier 25: a dashboard delivered only by products that declare
none of those metrics no longer carries one. Field reads, the IR
`computes_metric` term match and the tile-level gaps keep the whole change.
Evidence only: CHECK_VERSION does not move.

### Verifier 25

ClickHouse `x IN <table>` is read as `x IN (SELECT * FROM <table>)`.
The same operator SQL loses a false `chart.sql_partial` that named the
table as an unattributed column of the tile, and gains a star hole (and a
named-CTE open) for the table the right-hand side names. Field-level
governance findings move with CHECK_VERSION 13. Both directions.

### Verifier 24

a ClickHouse alias is not a column read, and an expression-form WITH
alias is read through. The same operator SQL can lose a `chart.sql_partial`
that named the alias as unattributed, and can gain a `reads_field` for a
column written only in the WITH expression. Inside the definition of SELECT
alias X, a WITH expression read through from it included, X is the column, so
a convert-and-keep-the-name tile now names that column. A name inside a query
nested in a referenced WITH expression is now graded (as the same subquery
written at the reference) and can raise the unresolved-field advisory. A
pathological alias chain past the read-through depth cap or expansion budget
is `chart.sql_partial` / `asset.query_fields_unchecked` under the alias's own
name. Field-level governance findings move with CHECK_VERSION 12 (fewer
`asset.query_ungoverned_field` for alias names; a new one when a referenced
WITH expression reads an undeclared column; an advisory for a name inside a
nested query of that expression). Both directions.

### Verifier 23

more reads and gaps than 22: a CTE is now named case-insensitively and
regardless of declaration order (a forward reference); a query nested anywhere
in a running part of the statement runs, and a named CTE it names opens — that
covers a query inside a live scalar WITH body and a subquery sqlglot's scope
builder does not reach (an argument of `arrayJoin((SELECT groupArray(a) FROM
x))`); a scalar alias read only through a tuple access at any depth (`t.1`,
`t.a.b`) is read — for a star under an expression-form WITH this matches 22,
for a star under a subquery-form WITH and for a dictionary call it is new; a
database or catalog qualifier that shares a scalar alias (`dm.sales_daily.revenue`
beside `WITH … AS dm`) opens that WITH — more reads than 22, a documented
over-report.
Fewer reads and gaps than 22: a dead reader opens nothing; an unread
expression-form star is not a `chart.sql_partial`; a named CTE declared
inside a nested query and never named does not run. Evidence only:
CHECK_VERSION does not move.

### Verifier 22

a ClickHouse `joinGet` / `joinGetOrNull` call with a literal Join table and
column now names a `reads_field` where verifier 21 answered "not this tile", and a
non-literal or non-reader `joinget*` call adds a `chart.sql_partial` clause (a gap
where there was none). Evidence only: CHECK_VERSION does not move.

### Verifier 21

a dictionary call (and a projection star) inside `WITH (SELECT …) AS alias`
whose alias the statement reads as a column is now in a scope the statement runs,
so the same inputs can name a tile or a hole verifier 20 skipped as "not this tile".
A typed `dictGet<Type>` call with a literal dictionary and attribute now names a
read and carries no gap where verifier 20 left `chart.sql_partial` — fewer gaps on
the same inputs, the direction that matters when re-reading an old bundle. The
wording of the dictionary `chart.sql_partial` detail also changed, so the same
unnameable call produces a different detail string. Evidence only: CHECK_VERSION
does not move.

### Verifier 20

metric-derived chart gaps (`chart.metric_unread`, `chart.metric_unknown`,
`chart.formula_untyped`) are raised only for a change that reaches an in-scope
product, so the same inputs can yield fewer gaps than verifier 19. Evidence only:
CHECK_VERSION does not move.

### Verifier 19

a ClickHouse dictionary call in an operator tile now names its read as
`<dictionary>.<attribute>` when both are literals, and otherwise leaves the tile
`chart.sql_partial`, so the same inputs can name a tile or a hole verifier 18 read as
"not this tile". Evidence only: CHECK_VERSION does not move.

### Verifier 18

chart resolution now reads an operator `raw_sql` tile from its SQL, so the same
inputs name tiles (and `chart.sql_partial` / `chart.metric_unread` holes) verifier 17
left as `chart.opaque_sql`. Chart resolution is evidence and does not grade, so
CHECK_VERSION does not move.

### Verifier 17

a LATERAL body's sibling FROM items are now visible at every nested scope that
can read them, so any scope nested in a lateral body, including a subquery written
inside it, that verifier 16 reported as `asset.query_ungoverned_field` on the body's
own table (or left as `asset.query_fields_unchecked` for a subquery that only the
sibling declares) can grade clean as a column of the sibling mart. The same walk
also moves inputs the other way: a bare name that governed sources declare at two
different lateral levels, and a name read beside an ungoverned mart written at an
outer lateral level, now carry `asset.query_fields_unchecked` where verifier 16
graded clean. A name written inside a lateral body may therefore move in either
direction, an advisory that became `needs_review` included, so an old bundle must not
be read against the current rules.

### Verifier 16

a reviewed query's bare column is now resolved against the sources its own scope
reads and is left unchecked where a correlated scope may have read it from the query
around it. The same inputs therefore move against verifier 15 in three directions: a
bare name beside an unread CTE is graded where it was only an advisory, a subquery's
bare name its own table does not declare is an advisory where it was `needs_review`,
and a query verifier 15 graded clean may now carry an advisory, where a subquery's only
source is a CTE or derived table whose columns the snapshot cannot see.

### Verifier 15

the owner summary adds a general guide to reading supplied results, provenance,
cost measurements and separately verified signatures. The report remains sealed before
bundle verification and makes no signature claim of its own.

### Verifier 14

a run can seal its canonical manifest with an authenticated Ed25519 envelope. The
new run identity avoids verifier-13 bundles, and its signing identity also separates
unsigned, signed and rotated-key runs under verifier 14.

### Verifier 13

`dwh.cost` now stores typed exact-query execution facts next to plan facts. The same
inputs therefore produce a different evidence artifact even when every bound passes.

### Verifier 12

a bare column the statement alone cannot pin is now attributed through the governed
model when exactly one source in its scope declares it. Verifier 11 left every such name
unchecked, so the same inputs can now yield `needs_review` where it saw only an advisory
— and, where the name is declared, a clean asset where it flagged a partial check.

### Verifier 11

`assets.invalidated` grades a reviewed query's governance down to the field, so the
same inputs can now yield `needs_review` where verifier 10 saw a clean asset.

## Check version

Bumped when a check's meaning changes, so an old evidence bundle is not read as if it
had been graded by today's rule. Adding a *new* check is not such a change: no existing
`CheckResult` is re-graded by it, and the verifier version is what stops a bundle written
before it from being reused as a complete verification.

### Check 84

dwh.physical compares the type of a column on a PostgreSQL kernel (`postgres`, `greenplum`,
`greengage`) by the name `format_type` prints for it, where rule 83 compared the names as written,
spacing and case aside: a declared alias of the type the engine holds — `float8` for `double
precision`, `int4` for `integer`, `varchar(n)` for `character varying(n)`, `timestamp` for
`timestamp without time zone` — is no longer `physical.type_drift` (ADR 0010, F2). ClickHouse types
are compared as before.

### Check 83

evidence.bundle also checks the closures of dbt formulas a bundle keeps against the
bindings of its snapshots (ADR 0008, item 11): a closure missing, mismatched, unbound or
left unverified blocks, where rule 82 checked the presence, hash and size of artifacts.

### Check 82

semantic.gaps grades the gaps of a formula a dbt semantic manifest defines (block where
the artifact is missing or refused, the metric is missing from it, the pin, the relation or
the time contradicts it; review where the match is unproven or the construct unsupported),
where rule 81 graded every such formula `gap.opaque_metric_formula` alone; semantic.diff
names a changed binding `metric.binding_changed` (review), which reaches the metric's
products and verified assets; contracts.validate compares a bound metric's declaration and
pin with the validated contract, not the formula resolved from the artifact.

### Check 81

row-security findings and policy applicability respect the per-mart owner
bypass: an owner without FORCE is neither filtered nor denied all rows by policies.
A false row-security switch makes reported policies inapplicable to every session,
including classification checks, and suppresses bypass findings. Bypass details name
the established exemptions without claiming that consumers receive different rows.

### Check 80

dwh.freshness reads a day and a clock with no zone in the one timezone the metrics
declare for the column, a day as its 00:00 there, where rule 79 read both in UTC; with no
one such zone it reports freshness.event_time_zone_unknown, and for a reading after
created_at freshness.event_time_ahead, both review, where rule 79 graded the age. Text in
none of the three forms — a week, `YYYYMMDD`, a blank for `T`, an offset — is
freshness.unreadable_event_time, where rule 79 read what `datetime.fromisoformat` read.

### Check 79

contracts.validate grades each file of the validated paths once, where rule 78
blocked on the second reading of a file two of the paths reach (`contract.duplicate_id`).

### Check 78

contracts.validate blocks on a validated file (`contract.invalid`), and semantic.gaps
on a metric contract of a ref (`gap.contract_invalid`), that holds a moment of another
spelling than RFC 3339's one for UTC, where rule 77 graded the contract
`datetime.fromisoformat` read.

### Check 77

contracts.validate blocks on a product file that repeats an entry of
`delivery.dashboards` or `delivery.brief_formats` (`contract.invalid`), where rule 76
graded the product built of it.

### Check 76

bi.contract compiles, and impact.products reads, the spec of the whole ref of a dashboard
of more than one segment, where rule 75 took the file of its last segment: its `bi.compiled`
could stand for the layout of another dashboard, and its missing spec gave no review.

### Check 75

semantic.gaps blocks on `gap.metric_duplicate`, and a snapshot that carries it leaves
semantic.diff, impact.products, assets.invalidated, results.reconcile, results.provenance
and sql.compile `check.not_run`, with a reason that says so.

### Check 74

contracts.validate includes JSON files discovered under validated paths, using
the same schema and cross-contract rules as explicitly supplied files; a directory with
a contract suffix is walked, where it blocked as an unreadable contract.

### Check 73

dwh.cost reports a known runtime-setting refusal as cost.not_measured rather than
a false cost.over_budget; other driver failures retain their error boundary.

### Check 72

semantic.gaps blocks on a table, a column or a join a model declares more than once
and not alike, and names a repeat that is alike with an advisory, where rule 71 graded a
snapshot that held a node for each declaration.

### Check 71

bi.contract blocks on a delivered dashboard the compile refuses and names it, where
rule 70 gave no result for the run; its evidence always lists the refused dashboards.

### Check 70

semantic.gaps blocks on a table a model declares with no name, where rule 69 graded
a snapshot that held it as a table.

### Check 69

contracts.validate blocks on a validated file, and semantic.gaps on a metric contract
of a ref, that spells a surrogate in an escape, where rule 68 graded the contract PyYAML
built of the file.

### Check 68

contracts.validate blocks on a directory under the validated paths that could not
be listed, where rule 67 graded a report that read it as an empty directory.

### Check 67

an issue the validation rules give for another product of the validated paths is not
graded by contracts.validate, where rule 66 blocked, reviewed or advised by it.

### Check 66

a candidate definition that fails a product rule the validated contract satisfies is
a block, a review or an advisory of contracts.validate by the rule's own severity, where
rule 65 weighed it by the diff's classification alone and a draft or a foreign governing
policy passed with an advisory.

### Check 65

a candidate definition that differs from the validated contract while the base
does not hold the validated definition is a block or an advisory of contracts.validate,
by the diff's classification of the differences, where rule 64 passed it as contracts.clean.

### Check 64

a pin that names another version, several contracts or no contract of its metric
blocks assets.invalidated, where rule 63 passed it as assets.valid.

### Check 63

dwh.access blocks and dwh.security reports security.not_observed for a ClickHouse
own-grants answer carrying an untyped row, where rule 62 passed access and could call
a column unreadable.

### Check 62

the role and the marts of a PostgreSQL-family kernel that predates row-level
security are graded where rule 61 produced no result, and the clean dwh.security detail
names that kernel: no row-policy catalog was read there.

### Check 61

contracts.validate also grades product metric pins against the candidate snapshot:
a pass under rule 60 did not ask that question, and the detail now counts matching pins.

### Check 60

the current PostgreSQL-family role's superuser flag is read from pg_roles:
a role without LOGIN is graded by its privileges where check 59 blocked as unreported,
and a NOLOGIN superuser gets the existing superuser evidence in dwh.access.

### Check 59

a ClickHouse role granted but not enabled is asked about through system.role_grants
and the grants of the roles SET ROLE reaches, so dwh.access blocks with
access.role_writable where check 58 recorded read-only over a hidden write grant;
role names are written with backslashes doubled.

### Check 58

snapshots carry each metric's declared join paths (native projection version 3),
moving the canonical digest of every native snapshot with metrics; a snapshot without
metrics keeps its digest. semantic.diff names a removed metric join path as block and
a changed cardinality as review where it was semantic.unchanged, an added path as advisory.
semantic.gaps blocks on a metric join path with an endpoint the model does not declare
or declared with more than one cardinality.

### Check 57

semantic.diff grades relaxed metric freshness as review where it was
semantic.unchanged, tightened or restated freshness as advisory; snapshots now carry
freshness, moving the canonical digest of every native snapshot with metrics (native
projection version 2); a snapshot without metrics keeps its digest. Fixed windows are
compared in exact integer seconds however many digits a component has: windows too long
for timedelta or for one int() conversion now grade instead of raising in semantic.diff,
dwh.freshness and the provenance stale-capture rule.

### Check 56

bi.contract blocks with bi.spec_invalid when a delivered spec does not validate
against the candidate model, where it passed or only gave bi.filter_unwired before.

### Check 55

the PostgreSQL-family role probe grades a role writable when a role the session
user can `SET ROLE` to holds a write privilege on an in-scope mart (or is a superuser,
including a superuser group the current role inherits without gaining the attribute,
or its reachability or write privileges went unanswered), so `dwh.access` blocks with
`access.role_writable` where check 54 recorded read-only; the read-only evidence names
the reachable roles it checked. ClickHouse is unchanged.

### Check 54

the clean freshness outcome requires an existing mart eligible for dating.

### Check 53

the clean security outcome requires an actual requested column/policy observation.

### Check 52

the clean physical outcome requires an actual declared column/layout comparison.

### Check 51

the clean cost outcome requires an eligible comparison, not just supplied facts.
Findings, statuses and verdicts are unchanged.

### Check 50

dwh.access recognizes ClickHouse ALTER/CREATE/DROP leaf write privileges;
SQL questions and historical bundles are unchanged.

### Check 49

unknown ClickHouse roles block grant-based access and leave security unobserved;
physical absence requires known ClickHouse reach, classification requires known reach.

### Check 48

dwh.access blocks for partial or NULL table/column write-privilege answers.
Missing fields are unknown rather than negative; complete answers retain their rules.

### Check 47

dwh.access blocks for partial or NULL PostgreSQL-family role identities even when
in-scope grants would be negative. Unknown bypass is not false; proven positive flags
and known names are retained. Exact runtime SQL and write-privilege questions are
withheld; complete identities and proven superusers keep their rules.

### Check 46

dwh.access blocks when the PostgreSQL-family role-identity question is unanswered.
An unknown superuser or BYPASSRLS answer is not false, and exact runtime SQL is withheld.
The finding describes unestablished read-only access for both unanswered and writable
roles. Answered identities and ClickHouse grading are unchanged.

### Check 45

dwh.access blocks when the PostgreSQL-family table-write privilege question is
unanswered even if column-write answers are negative. Read-only access is unestablished,
exact runtime SQL is withheld and evidence names the unreported table privileges.
Positive grants and ClickHouse are unchanged. Historical bundles are not rewritten.

### Check 44

dwh.access blocks with access.role_writable for a PostgreSQL-family role verifier
63 recorded read-only with only a column-level write grant. The role probe also asks
column-level INSERT/UPDATE privileges (has_any_column_privilege) per in-scope mart;
read-only role evidence names the column privileges it checked. ClickHouse is unchanged.
An unanswered column-write question also blocks and withholds exact runtime SQL.
Historical bundles are not rewritten.

### Check 43

results.reconcile compares a non-finite value by kind. Same-sign infinities,
including different spellings and float overflow, move from value.changed BLOCK to
equal. Expected NaN moves from BLOCK to value.expected_nan REVIEW unless another
finding proves a difference; decimal sNaN against itself moves from written-form
equal to REVIEW. A float key written nan no longer reports its own cell. Candidate
NaN against a number, opposite infinities and infinity against a finite value stay
value.changed; decimal infinity against itself stays equal. Historical bundles are
not rewritten.

### Check 42

results.reconcile compares the exact binary64 difference with the exact declared
decimal tolerance. An absolute or relative excess hidden by rounding now gives
value.changed where 41 saw equivalent; an exact relative difference within tolerance
now stays equivalent where the rounded ratio rejected it. Grading moves both ways at
the rounding boundary. An infinity against a finite value or the opposite infinity was
equal under an absolute tolerance beyond the binary64 range, which rounded to infinity;
it is now value.changed. Historical bundles are not rewritten.

### Check 41

Environment readings record their event-time column; every check grades each
(mart, event-time column) question from its own reading. A multi-clock mart previously
graded from its first reading could pass with a stale clock or a denied column (now
freshness.stale REVIEW / security.column_unreadable BLOCK / access.table_unreadable
BLOCK), or report a fresh clock stale (that finding is gone). Unattributable readings
give access.reading_unattributed REVIEW; freshness.stale names its clock and evidence
facts carry event_time_column. Historical bundles are not rewritten.

### Check 40

integer/decimal business keys and aggregate group_by values are identified by number.
A rescaled key moves from different (BLOCK, row.missing / row.added) to a matched row;
duplicate numbers previously paired by written form (possibly equivalent, PASS) move to
insufficient / key.not_unique (REVIEW). A share_sum or partition_conservation group split
by scale (possibly insufficient or different) now forms one group. Both directions on
the same inputs; historical bundles are not rewritten.

### Check 39

`results.reconcile` reads supplied columns by name after the schema step.
The same permuted inputs move both ways: changed named values previously equivalent
(PASS, sealed result.equivalent claim) now differ (BLOCK); expected named values or a
moved key previously different (BLOCK) now agree. Failed aggregates name the candidate's
own row key; historical bundles are not rewritten.

### Check 38

`assets.invalidated` no longer overlooks physical tables matching CTE declarations
owned only by nested or sibling queries. A missing ungoverned-table REVIEW can return;
enclosing declarations, whole-AST extraction and existing name comparison keep their rules.

### Check 37

`assets.invalidated` no longer overlooks a physical ClickHouse table whose name
differs in case from a CTE declaration. A missing ungoverned-table REVIEW can return;
exact-case CTE references and PostgreSQL keep their existing rules.

### Check 36

`assets.invalidated` applies the same ClickHouse CTE and scalar WITH reachability to
`JOIN ... USING` keys. An unread key no longer leaves a false partial-field advisory;
reachable keys and all other field reasons keep their contract.

### Check 35

`assets.invalidated` applies the same ClickHouse CTE and scalar WITH reachability to
`COLUMNS(...)` selectors. An unread selector no longer leaves a false partial-field
advisory; reachable selectors and all other field reasons keep their contract.

### Check 34

`assets.invalidated` compares ClickHouse CTE and scalar WITH names case-sensitively
when deciding which stars are reachable. A missing star advisory can return, or a false
one disappear; explicit table/field findings and other advisory reasons keep their rules.

### Check 33

`assets.invalidated` no longer reports "it selects *" for an unreachable ClickHouse
star. Other advisory reasons and explicit table/field findings remain; PostgreSQL keeps
its whole-AST star rule. An earlier advisory may disappear from the same reviewed query.

### Check 32

PostgreSQL sorting by a cast of an already known strong output no longer creates
a phantom field REVIEW or ambiguous/sourceless advisory. Cast argument reads remain
graded; unary plus, explicit aliases and input sort expressions retain their boundaries.

### Check 31

PostgreSQL sorting by Window-wrapped unaliased ROW_NUMBER outputs no longer creates
a phantom field REVIEW or sourceless advisory. PARTITION BY and inner ORDER BY expressions
remain graded as field reads; bare row_number() and other window functions retain input reads.

### Check 30

PostgreSQL sorting by Window-wrapped unaliased COUNT, SUM, AVG, MIN or MAX
outputs no longer creates a phantom field REVIEW or sourceless advisory. Window
arguments, FILTER predicates, PARTITION BY and inner ORDER BY expressions remain
graded as field reads.

### Check 29

PostgreSQL sorting by FILTER-wrapped unaliased COUNT, SUM, AVG, MIN or MAX
outputs no longer creates a phantom field REVIEW or sourceless advisory, including
DISTINCT ON, set-operation output and parenthesized forms. Arguments and predicates
remain graded as field reads.

### Check 28

PostgreSQL sorting by an unaliased AVG, MIN or MAX output no longer creates a
phantom field REVIEW or sourceless advisory. Aggregate argument reads remain graded.

### Check 27

PostgreSQL table column alias lists are now a typed partial field-check remainder:
references to their output names cannot be settled against physical snapshot columns.
Adjacent explicit fields and named-table names outside the list retain existing grading.

### Check 26

PostgreSQL NATURAL JOIN is now a typed partial field-check remainder because
matching joined-source output names determine its implicit equality keys.

### Check 25

PostgreSQL sorting by an unaliased SUM output no longer creates a phantom
field REVIEW or sourceless advisory. Real SUM argument reads remain graded.

### Check 24

PostgreSQL sorting by an unaliased COUNT output no longer creates a phantom
field REVIEW or sourceless advisory. Real COUNT argument reads remain graded.

### Check 23

set-operation LIMIT/OFFSET subqueries now contribute field reads, so an old
pass on a UNION or nested set operation may become REVIEW or advisory. SELECT INTO
is rejected as a write, including previously accepted SELECT and UNION statements.

### Check 22

governance grades parenthesized ClickHouse ARRAY JOIN expressions as their
unparenthesized forms, removing phantom field findings and the scope crash. An
outer AS discards the inner alias, so its later name may add a field finding.

### Check 21

PostgreSQL-family ORDER BY / DISTINCT ON expression names become input reads,
potentially adding REVIEW or advisory findings. Case-folded whole output names stop
producing phantom input REVIEW; a set operation's ORDER BY output name stops producing
a sourceless advisory. A parenthesized query's trailing ORDER BY now grades names it
previously ignored and can add findings; GROUP BY +name moves from output-column
advisory to input REVIEW. Unary plus on an unaliased projection does not create a
plain-column output name. ClickHouse and chart impact are unchanged.

### Check 20

a PostgreSQL-family GROUP BY name that may be a SELECT output column moves REVIEW
`asset.query_ungoverned_field` to advisory `asset.query_fields_unchecked`; without local
sources it stops being a remainder. Aggregate/window outputs, expressions and qualified
names retain input reads; ClickHouse and chart impact are unchanged.

### Check 19

`JOIN ... USING` keys are field reads, but their shared SQL form gives no table to
attribute. Governance adds an advisory naming those keys on both engine families while
continuing to grade explicit neighboring columns.

### Check 18

A ClickHouse `COLUMNS(...)` selector reads a physical set of columns that the SQL
text does not enumerate. Governance adds `asset.query_fields_unchecked` where check
version 17 could return clean or report only a named-field review; no existing field
finding is removed. PostgreSQL-family parsing keeps its prior result.

### Check 17

A direct COUNT(*) is a row count, not a column-reading star. Governance drops the
advisory `asset.query_fields_unchecked` whose only reason was a COUNT(*) star; chart impact
adds `chart.sql_partial` for a tile that counts DISTINCT *, where it resolved to "not this
tile" or to its other reads alone.

### Check 16

ClickHouse SETTINGS names and values are no longer fields of a reviewed query. This
removes their phantom REVIEW `asset.query_ungoverned_field` and advisory
`asset.query_fields_unchecked`; `additional_table_filters` instead produces that advisory
because its row-filter SQL is opaque. Chart `reads_field` / `chart.sql_partial` changes
from the same phantom names are recorded by verifier 29. Both directions.

### Check 15

unqualified inputs in HAVING and QUALIFY are reads. The only direction is added
evidence: a new REVIEW `asset.query_ungoverned_field`, a new advisory
`asset.query_fields_unchecked`, and a new chart `reads_field`; no finding is removed.

### Check 14

`assets.invalidated` no longer treats a visible ClickHouse inline or ARRAY JOIN
alias as a field of the table in scope. It removes false
`asset.query_ungoverned_field` findings for phantom alias fields, but an inline
definition in HAVING or QUALIFY that earlier attribution skipped is now graded where
written and can name a real field (for example `internal_cost_raw` in
`(internal_cost_raw AS y)`). ARRAY JOIN still reads its source array. Both directions,
so a `pass` written under rule 13 does not answer today's question.

### Check 13

`assets.invalidated` reads ClickHouse `x IN <table>` as
`x IN (SELECT * FROM <table>)`. A false `asset.query_ungoverned_field` for a
column nobody has (`dm.sales_daily.rev` from `WHERE region IN rev`) is gone;
an ungoverned table on the right of IN that rule 12 raised as an advisory or
nothing now carries REVIEW `asset.query_ungoverned`. Both directions, so a
`pass` written under rule 12 does not answer today's question.

### Check 12

`assets.invalidated` no longer treats a ClickHouse alias as a column of the
table in scope, so a reviewed query that reads a number only through an alias of
declared columns no longer carries `asset.query_ungoverned_field`. The same
change grades columns written in a referenced expression-form `WITH <expr> AS a`
at the referencing scope, so a query that previously named the alias as the
undeclared field now names the column the expression reads — a new
`asset.query_ungoverned_field` where rule 11 was silent, or a clean pass where
rule 11 named the alias. Inside the definition of SELECT alias X, a WITH
expression read through from it included, X is the column, so convert-and-keep-
the-name SQL names the kept column. A name inside a query nested in that
expression is graded as the same subquery written at the reference and can raise
`asset.query_fields_unchecked`. A reference past the read-through depth cap or
expansion budget raises the same advisory under the alias's own name. Both
directions, so a `pass` written under rule 11 does not answer today's question.

### Check 11

`assets.invalidated` asks the field question of a LATERAL body's sibling sources
at every nested level that can read them. A review finding under rule 10 on a
set-operation or nested-lateral body, or an advisory on a subquery written inside
the body, may therefore be a clean pass here. A query rule 10 graded clean may now
carry an advisory, where a bare name is declared by governed sources at two
different lateral levels, or is read beside an ungoverned mart written at an outer
lateral level. A name written inside a lateral body can move either way under
rule 11 — an advisory under rule 10 may be `needs_review` here — so a `pass`
written under rule 10 does not answer today's question.

### Check 10

`assets.invalidated` asks the field question of the sources a scope actually reads,
and stops asking it of a bare name a correlated scope may have taken from the enclosing
query. An advisory under rule 9 may therefore be a review finding or a clean pass here,
a review finding under rule 9 may be an advisory, and a query rule 9 graded clean may
now carry an advisory, where a subquery's only source is a CTE or derived table whose
columns the snapshot cannot see.

### Check 9

byte/time cost ceilings use exact-query runtime facts. Missing evidence is now review,
while successful engine-enforced completion can pass a bound rule 8 called unmeasurable.

### Check 8

`assets.invalidated` asks that field question of more of the query: a bare column the
SQL alone cannot attribute is now graded whenever the governed model leaves exactly one
source in its scope that declares it. A `pass` under rule 7 only means no column the SQL
itself could attribute was undeclared, and an advisory under it may cover a name rule 8
grades outright.

### Check 7

`assets.invalidated` answers a wider question: an asset stops being evidence not
only when semantics moved the number under it, but also when its reviewed query reads a
mart the model does not govern or a column it does not declare. An earlier `pass` on
that check did not ask either question.
