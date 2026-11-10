# Capability: The run of a product its schema refuses says why

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

`decision verify` verifies one product of the validated paths (DECISIONOPS_ARCHITECTURE §12.1). A
product file whose contract fails its schema is a `contract.invalid` issue of the validation report
(§7.7) and no product of the paths, so its run is refused as the run of a product that is not
there: `no decision product contract found in the given paths`, or with `--product` `no decision
product '<id>' in the given paths (<the products read>)`, exit 2. Until now the refusal said that
the product was not among what was read and not why: the product file was read, its schema
refused it, and only `decision validate` said so — a product that delivers one dashboard twice
([repeated delivery entries](repeated-delivery-entries.md)) or pins one metric twice was refused
as if it had never been written.

## Requirement: the refusal names the decision products the schema refused, and why

A file of the validated paths that holds a contract of kind `DecisionProduct` and fails its schema
is a decision product the schema refused. It SHALL be named by the id its `metadata` declares —
the subject `decision validate` gives its `contract.invalid` issue — and by its file, with what
the schema refused of it: the first three refusals, each as `<place>: <reason>` in one line,
joined by `; `, then `(+<k> more)` — the reason a usage error gives of every document a schema
refuses (`schema_reason`). A file that declares no id is named by its file alone.

When `decision verify` is asked for a product with `--product` and the paths hold no such product,
its usage error SHALL go on, after `(<the products read>)`, with `; <n> decision product(s) failed
the schema: ` and the decision products the schema refused whose declared id is the text of
`--product`, each as `'<id>' in <file> (<reason>)`, separated by `, `. A refused product that
declares another id SHALL NOT be named there, nor counted: the run asked for one product.

When `decision verify` is not asked for a product and the paths hold none, `no decision product
contract found in the given paths` SHALL go on the same way with every decision product the schema
refused — `<file> (<reason>)` for one that declares no id.

In either case `<n>` counts the products named for the case, and the first three of them are
named, then ` (+<k> more)` when there are more: under `--product` the products of other ids are
left out before the three are taken.

The products are named in the order their files were read, which is the order of their
`contract.invalid` issues in the report. The id is spelled as Python spells a string, as
`'<id>'` of `--product` is; the file by the name its issue carries
([validated path names](validated-path-names.md)), as a diagnostic line spells a path — quoted
when it holds a control character — so what this names of a product holds no line break. What
the paths could not read (`; <n> path(s) could not be read: …`,
[files a command cannot read](unreadable-inputs.md))
SHALL follow, as before. The error is printed after `error: ` on standard error, exit 2, nothing
on standard output and no evidence directory created; it is one line (since T-117 whatever a
path that could not be read holds: [one-line errors](one-line-errors.md)).

A contract of another kind that fails its schema SHALL NOT be named, whatever id it declares: a
metric contract that declares `decision://sales/weekly_margin` is no decision product of that id.
Nor SHALL a file that cannot be read or holds a kind that is neither (`contract.unreadable`,
`contract.unknown_kind`). Where the paths hold no decision product the schema refused, every
refusal SHALL read as before. A refusal for two or more products and no `--product` names the
products read and not the refused ones, as before; with one product read, it is the one run, and
`contracts.validate` blocks on the issue of the refused file
([validation issue scope](validation-issue-scope.md)).

The reason is what the schema says of the file: it is printed and never kept. The report, the
gate and every bundle are unchanged — no run that wrote a bundle writes another one — so
`VERIFIER_VERSION` stays 114, `CHECK_VERSION` 77 and `NATIVE_SOURCE_VERSION` 3.

### Scenario: the product asked for delivers one dashboard twice

- **GIVEN** the validated paths hold one product file, of `decision://sales/weekly_margin`, that
  delivers `dashboard://weekly_margin@7` twice
- **WHEN** `decision verify --product decision://sales/weekly_margin` runs
- **THEN** it is refused with `no decision product 'decision://sales/weekly_margin' in the given
  paths (none); 1 decision product(s) failed the schema: 'decision://sales/weekly_margin' in
  <the file> (delivery: Value error, duplicate entries in delivery.dashboards:
  dashboard://weekly_margin@7)`, exit 2, and no evidence directory exists
- **AND** without `--product` it is refused with `no decision product contract found in the given
  paths; 1 decision product(s) failed the schema: ` and the same product

### Scenario: only the product asked for is named

- **GIVEN** the product of the run validates, and beside it a product file of
  `decision://sales/other_margin` that pins one metric twice
- **WHEN** `decision verify --product decision://sales/other_margin` runs
- **THEN** the refusal names the products read (`decision://sales/weekly_margin`) and the refused
  `decision://sales/other_margin` with `scope: Value error, duplicate entries in scope.metrics:
  metric://sales/gross_margin_pct@3`
- **AND** asked for `decision://sales/missing`, the refusal is the one it was: the refused
  product declares another id

### Scenario: two files of the id asked for

- **GIVEN** two product files that declare `decision://sales/weekly_margin`, each failing its
  schema
- **WHEN** `decision verify --product decision://sales/weekly_margin` runs
- **THEN** the refusal says `2 decision product(s) failed the schema` and names both, in the
  order of their files

### Scenario: the products of other ids are left out before three are taken

- **GIVEN** three refused product files of other ids, read first, and four refused product files
  of `decision://sales/weekly_margin`, and no product of that id
- **WHEN** `decision verify --product decision://sales/weekly_margin` runs
- **THEN** the refusal says `4 decision product(s) failed the schema`, names the first three files
  of that id and ends ` (+1 more)`

### Scenario: no product, many refused

- **GIVEN** five product files, each failing its schema, and no other product
- **WHEN** `decision verify` runs without `--product`
- **THEN** the refusal names the first three in the order of their files and ends ` (+2 more)`

### Scenario: a file that declares no id

- **GIVEN** a product file with no `metadata`, and no other product
- **WHEN** `decision verify` runs without `--product`
- **THEN** the refusal names `<the file> (metadata: Field required)`
- **AND** asked for `--product` of the file's name, the refusal is the one it was

### Scenario: a reason that spans lines is said in one

- **GIVEN** a product file that fails its schema on a key that holds a line break
- **WHEN** `decision verify` runs, through the command line
- **THEN** the reason is given in the one line of the error, and the command exits 2

### Scenario: what is not a decision product is not named

- **GIVEN** no product file, and a metric contract that fails its schema and declares
  `decision://sales/weekly_margin`; or a file of `kind: Other`; or a file that cannot be read
- **WHEN** `decision verify` runs, with or without `--product decision://sales/weekly_margin`
- **THEN** the refusal is the one it was, and a file that cannot be read is still named as one

### Scenario: a refused product and a path that could not be read

- **GIVEN** the refused product of the first scenario and a directory under the validated paths
  that cannot be listed
- **WHEN** `decision verify` runs without `--product`
- **THEN** the refusal names the refused product and then, as before, the path that could not be
  read

## Known boundaries

- `--product` is compared with the declared id as text, as the products read are: a declared id
  that is no string is compared as Python spells it (`str`), the subject `decision validate` gives
  its issue. A file that declares no id is not named under `--product`, even of its file's name.
- A refused product is named only when no product of the paths answers the run. A product file of
  the id asked for that fails its schema beside one that validates is no refusal: the valid one is
  run, and `contracts.validate` blocks on the issue of the other file.
- `decision validate` is unchanged: it prints the issues of the report above its own error.
- The reason is what the schema says, as `decision verify` says it of every file a schema refuses:
  a validator may quote a value of the file in it (the repeated refs of a delivery are quoted).
  `schema_reason` gives each run of whitespace as one space, a line break included, and leaves
  any other character as it is: a key spelled with a YAML escape such as `\e` reaches the message
  as that character, as it does in the refusal of any run file a schema refuses. The id and the
  file are escaped; the reason is not. Since T-117 the line the command prints spells that
  character by its escape, `\x1b` ([one-line errors](one-line-errors.md)); the message keeps it.
- The suffix of the paths that could not be read was unchanged by this requirement: it spelled
  each path as it is, so a path with a line break there broke the line of the error
  ([files a command cannot read](unreadable-inputs.md)). Closed by T-117: each path is spelled as
  the file of a refused product is ([one-line errors](one-line-errors.md)).
