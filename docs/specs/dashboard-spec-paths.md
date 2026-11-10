# Capability: Where `decision verify` reads the spec of a delivered dashboard

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

`decision verify --dashboards <dir>` hands the gate the spec of every dashboard the product
delivers, each read from a file under that directory: `bi.contract` compiles it and
`impact.products` reads its tiles (DECISIONOPS_ARCHITECTURE §8.5, §12.1). The file was named by
the last segment of the ref and its pin alone, so `dashboard://sales/weekly_margin@7` and
`dashboard://ops/weekly_margin@7` read one file `weekly_margin@7.yaml`. With no spec supplied for
the second, verifier 111 compiled the layout of the first under its ref, sealed it, read its tiles
for chart impact, and `bi.contract` passed with `bi.compiled` where a missing spec gives the review
`bi.spec_missing`: the verdict `needs_review` became `pass_with_advisory`. A product that delivers
`dashboard://ops/weekly_margin@7` alone read the layout of `dashboard://weekly_margin@7` the same
way.

## Requirement: the spec of a dashboard is the file of its whole ref

For a delivered ref `dashboard://<s1>/…/<sn>@<v>`, `decision verify` SHALL read the spec from
`<dir>/<s1>/…/<sn-1>/<sn>@<v><suffix>`: every segment but the last a directory, the last segment
with the pin the name of the file, and `<suffix>` the first of `.yaml`, `.yml` and `.json` under
which a file is there. A ref of one segment reads `<dir>/<s1>@<v><suffix>`, as before. No other
file SHALL be read for the ref: neither the file its last segment names at the top of the
directory nor the file of another version. A segment is lowercase letters, digits, `_` and `-`
(the grammar of a ref), so the text of the path never leaves the directory and no two refs spell
one path; where a link on that path leads is a known boundary.

When no such file is there, the spec of the dashboard SHALL be one the run did not load, as for a
ref of one segment: `bi.contract` gives the review `bi.spec_missing` and lists the ref as missing,
no BI payload of it is stored, and chart impact names `chart.dashboard_spec_missing` for it. A
segment that names a file rather than a directory is such a case. A look the filesystem refuses, a
file that cannot be read and a file that is not a valid `DashboardSpec` SHALL end the run as before,
with the path of that file and nothing written.

A run that delivers a ref of more than one segment reads another file than verifier 111 read, and
`bi.contract` grades the spec of the whole ref, so `VERIFIER_VERSION` and `CHECK_VERSION` SHALL be
bumped. The gate, the compile and chart impact are not changed: they take the specs by ref, as
before. Run identity is computed as before, from the specs by ref and the verifier version, so the
bump gives every run another run id, a run of refs of one segment too.

### Scenario: a dashboard of two segments does not read the file of its last segment

- **GIVEN** a product delivering `dashboard://ops/weekly_margin@7` alone, a dashboards directory
  holding `weekly_margin@7.yaml` and nothing under `ops/`, and a run that passes when the spec it
  delivers compiles
- **WHEN** `decision verify` runs
- **THEN** `bi.contract` gives the review `bi.spec_missing`, compiles nothing and lists the ref as
  missing, the bundle holds no BI payload, `bi.contract` is the only check that asks for a review
  or blocks, and the verdict is `needs_review`

### Scenario: the file of the last segment does not drive chart impact

- **GIVEN** the same product and directory, and a candidate that changes the type of
  `dm.sales_daily.region`, which the chart of `weekly_margin@7.yaml` groups by
- **WHEN** `decision verify` runs
- **THEN** the change reaches no tile, and the only chart gap is `chart.dashboard_spec_missing`
  for `dashboard://ops/weekly_margin@7`

### Scenario: two dashboards of one last segment read their own files

- **GIVEN** a product delivering `dashboard://sales/weekly_margin@7` and
  `dashboard://ops/weekly_margin@7`, and a directory holding `sales/weekly_margin@7.yaml` and
  `weekly_margin@7.yaml`
- **WHEN** `decision verify` runs from the command line
- **THEN** it exits 3 with `bi.contract` under review; the sales dashboard is compiled from
  `sales/weekly_margin@7.yaml`, the ops dashboard is listed as missing, and the bundle re-verifies

### Scenario: each dashboard compiles its own spec and reaches its own tiles

- **GIVEN** `sales/weekly_margin@7.yaml` with a chart of revenue by region and
  `ops/weekly_margin@7.yaml` with a chart of gross margin by category, both delivered, and a
  candidate that changes the type of `dm.sales_daily.region`
- **WHEN** `decision verify` runs
- **THEN** each dashboard is compiled with the digest of its own spec and the bundle holds BI
  payloads of both; the change reaches `dashboard://sales/weekly_margin@7#revenue_by_region`
  alone, and no chart gap is named

### Scenario: every segment but the last is a directory, under each suffix

- **GIVEN** `dashboard://sales/emea/weekly_margin@7`, its spec in
  `sales/emea/weekly_margin@7.yaml`, `.yml` or `.json`
- **WHEN** `decision verify` runs
- **THEN** the spec of that file is compiled, `bi.contract` passes with `bi.compiled`, and the
  verdict is `pass_with_advisory`

### Scenario: of the suffixes, the first a spec is there under is read

- **GIVEN** `sales/weekly_margin@7.yaml`, `.yml` and `.json`, three specs with other titles
- **WHEN** `decision verify` runs with all three, then without `.yaml`, then with `.json` alone
- **THEN** it compiles the `.yaml`, then the `.yml`, then the `.json` spec

### Scenario: a dashboard of two segments does not load another version

- **GIVEN** `dashboard://sales/weekly_margin@7` delivered, a directory holding
  `sales/weekly_margin@8.yaml` and `weekly_margin@7.yaml`
- **WHEN** `decision verify` runs
- **THEN** `bi.contract` gives `bi.spec_missing` and lists the ref as missing

### Scenario: a segment that names a file is a spec that is not there

- **GIVEN** `dashboard://sales/weekly_margin@7` delivered, `sales` a file of the directory, and
  `weekly_margin@7.yaml` beside it
- **WHEN** `decision verify` runs
- **THEN** `bi.contract` gives `bi.spec_missing` and lists the ref as missing; the run is not
  refused

### Scenario: a spec of its path that is refused ends the run

- **GIVEN** `sales/weekly_margin@7.yaml` that may not be looked at, or may not be read, or that is
  no `DashboardSpec` (it has no chart), and a valid `sales/weekly_margin@7.yml` beside it
- **WHEN** `decision verify` runs
- **THEN** it is refused, nothing is written, and the refusal is `cannot read <path>:
  PermissionError` or begins `<path> is not a valid DashboardSpec: `, `<path>` being the `.yaml`:
  the `.yml` does not take its place

## Known boundaries

- A spec laid out by its last segment at the top of the directory, as verifier 111 read it, is not
  read for a ref of more than one segment: such a dashboard is `bi.spec_missing` until its file
  stands at the path of its ref. Refs of one segment, which every pilot delivers, read the file
  they read.
- The directory is not listed: a file no delivered ref names is neither read nor reported.
- Links are followed, as by every look at an input ([unreadable inputs](unreadable-inputs.md)):
  only the text of the path is kept under the directory and apart for each ref. A symlink or a junction under the directory may lead the path
  of a ref out of it or to the file of another ref, and a hard link gives two refs one file.
- Bundles written before verifier 112 are not rewritten; one of them may hold the compile of
  another dashboard's layout under a ref of more than one segment.
