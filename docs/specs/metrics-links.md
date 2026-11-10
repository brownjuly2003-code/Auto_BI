# Capability: A symbolic link below `metrics/` is frozen as the text git commits for it

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

A native snapshot reads `model.yaml` and every `.yaml`/`.yml` contract file below the contracts
path of a semantic ref (`NativeModelSource`, `auto_bi/semantic/gateway.py`). It read a symbolic
link through: a link to a contract was a second contract of its id, block `gap.metric_duplicate`
([two contracts of one metric id](repeated-metric-contracts.md)), and a link to nothing or to a
directory was no file and was not read. A semantic ref is most often a checkout of a commit, and
git does not commit what a link leads to: it commits a symbolic link as its text — mode `120000`, the blob
is the text — and a checkout makes the link again where `core.symlinks` is true and writes a file
of that text where it is false, as the system configuration of git for Windows set it on the
machine this was reproduced on. One commit was then two snapshots, and two run ids, by the
checkout it was read from. A hard link is no part of a commit: git commits it as one more entry
of the same blob, and every checkout writes it as a copy.

Reproduced at HEAD `0e8db1f`, 2026-10-28, on Windows with git 2.50.0.windows.2:

- a commit whose `metrics/` holds the contract `gross_margin_pct.yaml`, its hard link `hard.yaml`
  and `soft.yaml`, a symbolic link to it: git records `hard.yaml` as a second `100644` entry of
  the contract's blob and `soft.yaml` as `120000` with the 21 bytes `gross_margin_pct.yaml`. The
  tree it was committed from and a clone with `core.symlinks=true` froze into three nodes of the
  id and `gap.metric_duplicate`, one snapshot; a clone with `core.symlinks=false` froze into two
  nodes, `gap.contract_invalid` at `contracts.soft.yaml` and `gap.metric_duplicate`, with another
  source digest and another canonical digest;
- a commit whose only contract is `metrics/out.yaml`, a link to `../shared/gross_margin_pct.yaml`:
  where the link was made the snapshot held the metric and no gap; where its text was written,
  `gap.contract_invalid` and no metric;
- `decision verify` over a candidate holding, beside its contract, `linked.yaml` — a symbolic link
  to it, a hard link of it or a byte-for-byte copy — gave `run-525149c3e8a23257`, block with
  `gap.metric_duplicate`; with `linked.yaml` the file of the link's text, `run-dd17d0f8b1cfe39c`,
  block with `gap.contract_invalid`.

## Requirement: a symbolic link below the contracts directory is the file of its text

A path below the contracts directory whose name has a contract suffix (`.yaml`, `.yml`) and that
is a symbolic link SHALL be a contract file whatever it leads to — a contract, another file, a
directory, nothing, the link itself, a path the filesystem refuses to look at — and SHALL be read
as the text the link holds, not through it: the snapshot SHALL be the one of a file of that text
in its place, the file a checkout without links writes. What the link leads to SHALL NOT be read,
nor looked at. The text SHALL be the one git commits:

- on POSIX, the text the link holds, a backslash in it a character of a name;
- on Windows, where git for Windows makes a link with a backslash between names and the system
  gives an absolute target back in its long form, the text with each backslash read as `/`,
  then `//?/UNC/` read as `//` and any other leading `//?/` dropped: `C:/x` and
  `//server/share/x`, as git committed them.

The file is then what a file of that text is: its bytes are parsed as those of every contract
file are, the snapshot holds what they parse into, and the source digest keeps their digest under
the file name. A text that YAML reads as a string, as it reads `gross_margin_pct.yaml`,
`../shared/elsewhere.yaml` and `C:/no/such/x.yaml`, or as a mapping without
`kind: MetricContract`, is no `MetricContract`: the snapshot carries `gap.contract_invalid`
(block) at `contracts.<file name>`, with the reason
`file in the contracts path is not a MetricContract` and the digest of the text as its
`source_value_ref`. A text YAML cannot parse, such as `[x.yaml`, and bytes no UTF-8 decodes, such
as a byte `0xff` a link on POSIX may hold, carry the gap a file of them carries:
`gap.contract_invalid` with the reason `metric contract cannot be read:` and the name of the
error. The gap SHALL NOT say that the file was a link: a checkout that wrote the text holds no
link to name.

A path whose name has no contract suffix SHALL NOT be read, a link or not, and a directory
reached through a link below the contracts directory SHALL NOT be walked, as before.

### Scenario: a link to a contract beside it

- **GIVEN** a ref of the gateway test model whose `metrics/` holds `gross_margin_pct.yaml`, the
  contract of `metric://sales/gross_margin_pct` at version 3, and `linked.yaml`, a symbolic link
  to `gross_margin_pct.yaml`
- **WHEN** it is frozen
- **THEN** the snapshot is the one of the same ref with `linked.yaml` a file of the 21 bytes
  `gross_margin_pct.yaml`: one node of the id and one gap, `gap.contract_invalid` at
  `contracts.linked.yaml` with the digest of those bytes

### Scenario: a link wherever it leads

- **GIVEN** the ref of the scenario above with `linked.yaml` a symbolic link made as git makes
  it — on Windows with a backslash between names — to `../shared/elsewhere.yaml`, a contract of
  `metric://sales/elsewhere`; to `sub/x.yaml`, `/no/such/x.yaml` or `C:/no/such/x.yaml`, which
  are not there; to `nowhere.yaml`, not there; to the directory `../shared`; or to itself
- **WHEN** it is frozen
- **THEN** the snapshot is the one of the ref with `linked.yaml` a file of the link's text
  written with `/`, with one `gap.contract_invalid` at `contracts.linked.yaml`, and it holds no
  node of `metric://sales/elsewhere`

### Scenario: a look through the link refused

- **GIVEN** the ref of the first scenario, the filesystem refusing every look through
  `linked.yaml` at what it leads to, as at a file in a directory the process may not search
- **WHEN** it is frozen
- **THEN** no `OSError` is raised, and the snapshot is the one of the first scenario

### Scenario: the text Windows gives back

- **GIVEN** the texts `os.readlink` gives back on Windows for links git for Windows makes of the
  texts it commits: `sub/x.yaml`, `/no/x.yaml`, `//?/C:/x.yaml` and
  `//?/UNC/server/share/x.yaml`, each spelled with a backslash for every `/`; and `sub/x.yaml`
  as it is
- **WHEN** each is read as a link's text
- **THEN** on Windows it is `sub/x.yaml`, `/no/x.yaml`, `C:/x.yaml`, `//server/share/x.yaml` and
  `sub/x.yaml`, the texts git committed; on POSIX it is the text given back, unchanged
- **AND** on POSIX a link whose text is `a`, a backslash and `b.yaml` is the file of those bytes

### Scenario: a link's text YAML cannot read

- **GIVEN** the ref of the first scenario with `linked.yaml` a symbolic link to `[x.yaml`; and,
  on POSIX, one whose text is the byte `0xff` and `.yaml`, which no UTF-8 decodes
- **WHEN** it is frozen
- **THEN** the snapshot is the one of the ref with `linked.yaml` a file of those bytes: one
  `gap.contract_invalid` at `contracts.linked.yaml`, its reason `metric contract cannot be read:`
  and the name of the error — `UnicodeDecodeError` for the byte `0xff` — and the digest of those
  bytes

### Scenario: a link without a contract suffix

- **GIVEN** the ref of the first scenario with, in `metrics/`, `shared`, a symbolic link to a
  directory holding a contract of `metric://sales/elsewhere`, and `linked.txt`, a symbolic link
  to `gross_margin_pct.yaml`
- **WHEN** it is frozen
- **THEN** the snapshot is the one of the ref without them

## Requirement: a hard link is the copy a checkout writes

A hard link of a contract file below the contracts directory SHALL be read as one more file of
its bytes, as before: the snapshot SHALL be the one of the same ref with a byte-for-byte copy in
its place. Of the id the two define it carries `gap.metric_duplicate` with the versions of both
and holds two nodes ([two contracts of one metric id](repeated-metric-contracts.md)).

### Scenario: a hard link

- **GIVEN** the ref of the first scenario with `linked.yaml` a hard link of
  `gross_margin_pct.yaml`
- **WHEN** it is frozen
- **THEN** the snapshot is the one of the ref with `linked.yaml` a copy: `gap.metric_duplicate`
  at `metrics.metric://sales/gross_margin_pct` with the reason
  `metric is defined by 2 contracts (versions 3, 3): which of them the ref means is not known`,
  and two nodes of version 3

## Requirement: the paths the caller names are read through a link

The contracts path the caller names — a directory or one file — and the model path SHALL be read
through a link, as before: a contracts path that is a symbolic link to a directory SHALL be
listed as that directory, one that is a symbolic link to a file SHALL be read as that file, and
a model path that is a symbolic link SHALL be read as the model it leads to.

### Scenario: the contracts path and the model through a link

- **GIVEN** the ref of the first scenario without `linked.yaml`, and a directory of symbolic links
  to its `model.yaml`, to its `metrics` directory and to its contract
- **WHEN** the linked model is frozen with the linked directory as the contracts path, and with
  the linked contract as the contracts path
- **THEN** each snapshot is the one of the ref's own paths: no gap, one node of
  `metric://sales/gross_margin_pct`

## Requirement: one commit is one snapshot, and one run where nothing else it reads differs

A commit whose contracts directory holds a symbolic link SHALL freeze into one snapshot — graph,
gaps, source digest and canonical digest — whether its checkout made the link or wrote a file of
its text, where the text the link gives back reads as the text git committed: every text on
POSIX, and on Windows the texts the first requirement reads back — not a backslash committed on
POSIX, nor a long form other than a drive or a UNC share. The contracts path and `model.yaml` are
the caller's and are read through a link: a ref where they are themselves links is still two refs
by checkout (Known boundaries).

`decision verify` over the two checkouts SHALL give one run id and one verdict where nothing else
the run reads differs between them. What else it reads can differ. The validation report is part
of a run's key, and `decision validate` reads a symbolic link through: a validated path that
reaches a link — the candidate's `metrics/` or any other — reads what the link leads to on one
checkout and the file of its text on the other, and where the two reports differ, as in the
scenario of a validated path that reaches a link, so do the run ids. A verified asset that is a
link loads on one checkout and is refused on the other (Known boundaries). A hard link SHALL give
the run id a copy gives.

### Scenario: a checkout with and without links

- **GIVEN** an index holding the gateway test model, the contract `metrics/gross_margin_pct.yaml`
  and `metrics/linked.yaml` of mode `120000`, whose blob is `gross_margin_pct.yaml`
- **WHEN** `git checkout-index` writes it with `core.symlinks=true` — a link — and with
  `core.symlinks=false` — a file of the text — and each tree is frozen
- **THEN** the two snapshots are equal and hold `gap.contract_invalid` at `contracts.linked.yaml`

### Scenario: the runs

- **GIVEN** the gate run of the tests of `decision verify` (`Case`), whose validated contracts are
  a directory of their own, and whose candidate `metrics/` holds, beside `gross_margin_pct.yaml`,
  `linked.yaml`: a symbolic link to it; a file of the text `gross_margin_pct.yaml`; a hard link of
  it; a copy of it
- **WHEN** each runs
- **THEN** the link and the file of its text give one run id, verdict `block`; the hard link and
  the copy give one run id, verdict `block`

### Scenario: a validated path that reaches a link

- **GIVEN** the run of the scenario above with the link, and with the file of its text, where the
  validated contracts directory holds a `linked.yaml` of the same kind beside its contract
- **WHEN** each runs
- **THEN** `decision validate` reads the link as the contract it leads to and finds no issue, and
  the file of its text as `contract.unreadable`: two reports, and two run ids

## Versions

Verifier 118 → 119: a ref holding a symbolic link below its contracts directory is frozen
from the link's text, where verifier 118 read the file the link led to, or nothing for a link
to nothing or to a directory. Its source digest, and with it its snapshot, run id and sealed
artifacts, are then other ones unless those bytes happen to be the same; its gaps and graph are
what the text parses into — `linked.yaml`, a link to the contract `gross_margin_pct.yaml` beside
it, was a second node of the id and `gap.metric_duplicate`, and is `gap.contract_invalid` beside
one node. A ref whose only contract was `out.yaml`, a symbolic link to
`../shared/gross_margin_pct.yaml` outside the contracts directory, was frozen with that contract
and without a gap where the link was made; YAML reads its text as a string, no contract, and the
ref now blocks with `gap.contract_invalid` on every checkout, as it did where the text was
written.
A ref without a symbolic link is frozen as it was: the 11 semantic refs the repository tracks
give the snapshots they gave, and the repository tracks no symbolic link. Check 79 is unchanged:
no check grades anything new, and `semantic.gaps` blocks on `gap.contract_invalid` as it did on
`gap.metric_duplicate`. Native source 3 is unchanged: the graph the bytes of a contract file
project into is the one it was. Historical bundles SHALL NOT be rewritten.

## Known boundaries

- `decision validate` reads a symbolic link through, and a file reached twice once
  ([contract files once](contract-files-once.md)): of a `metrics/` holding a contract and a
  symbolic link to it, `decision validate` passes where the snapshot blocks with
  `gap.contract_invalid`. Nor is its report one to a commit: of the commit of the hard link and
  the symbolic link above, the tree it was committed from passed; a clone with links made gave
  `contract.duplicate_id`, the hard link a copy there; a clone with the texts written gave
  `contract.duplicate_id` and `contract.unreadable`, `contract file must contain a YAML mapping`.
  The report is part of a run's key: where the reports of two checkouts differ, as they do here,
  so do their run ids. Since verifier 121, a file of the link's text under the first validated
  directory is named `$1/linked.yaml` in `contract.unreadable`
  ([validated path names](validated-path-names.md)); naming does not change what the link reads.
- A ref whose `metrics` or `model.yaml` is itself a symbolic link is still two refs by checkout:
  where the link is made it is read through; where its text is written, `metrics` is a file and
  no directory, and the ref is frozen without contracts, and `model.yaml` is a file of the text
  and no model — `decision verify` says it `is not a valid semantic model`.
- The verified assets a run reads (`VerifiedAssetRepository.load`) read a symbolic link through,
  as before: of an asset directory holding `weekly.yaml`, a link to `../shared/weekly.yaml`, the
  asset loads where the checkout made the link, and where it wrote the text the command refuses
  it — `<file> is not a valid verified asset: <root>: Input should be a valid dictionary or
  instance of VerifiedAsset`. One commit is a run on one checkout and a refusal on the other.
- A link text holding a backslash, committed on POSIX, is read on Windows with `/` in its place
  where the checkout made the link, and as committed where it wrote the text: two snapshots on
  Windows, one on POSIX.
- A long form on Windows other than a drive or a UNC share — `//?/Volume{…}/`, a device path —
  is read with `//?/` dropped; that this is the text git commits for such a target was not
  checked.
- A junction on Windows is no symbolic link to `lstat`: below `metrics/` it is walked as a
  directory and the contracts it leads to are read, as before. Git does not write one.
- The gap of a link says what a file of its text says, and no word of a link: an operator who
  made `linked.yaml` a link to `gross_margin_pct.yaml` reads
  `file in the contracts path is not a MetricContract` at `contracts.linked.yaml` with the digest
  of the link's text.
