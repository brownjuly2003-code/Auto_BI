# Capability: A gate run's pull-request comment, and the action that posts it

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

A change to governed semantics is reviewed in a pull request, and before ADR 0012 D3 the answer
of the gate did not reach it: `decision verify` said its verdict in an exit code, in lines on
standard output and in a bundle whose `reports/summary.md` is written for the owner who opens
it. A reviewer saw a step fail or pass and had to fetch the bundle to learn which products and
charts the change reaches and why. The tools of teams that review data changes in pull requests
(dbt CI checks, Datafold, Recce in the market note of 2026-11-13) answer in the pull request.

`decision verify --pr-comment <file>` writes that answer as Markdown, and the composite action
`action.yml` at the root of the repository posts it to the pull request its workflow runs for,
with the token that workflow hands it. The project itself publishes nothing.

## Requirement: the comment states the run and adds no finding

With `--pr-comment <file>`, `decision verify` SHALL write, after it sealed and re-verified the
bundle, a comment derived from the checks of the run and the `impact.products` payload of its
bundle, in this order:

- the heading `### Auto_BI release gate: <verdict>`;
- the product; the verdict, its exit code and how many of all the checks are block, review and
  advisory; the run and its time; the manifest digest with the outcome of `evidence.bundle`; the
  digests of the base and the candidate semantics; the run status when the run did not complete;
- the sentence of the evidence summary that says what the verdict asks of the owner;
- «What stops the release», «Whose numbers this reaches», «What needs a human» and «Advisory
  findings», each only when it has an item; «Not done in this run», always, with the line «every
  step of §8.2 produced evidence in this run» when no step is undone; «Every check», a folded
  table of every check;
- a foot that names the bundle as the record and the command that checks it,
  `auto_bi decision evidence <evidence> --expect-digest <digest>`.

A check is listed in at most one of the first five sections, as the evidence summary lists it: a
step not done under «Not done in this run», every other check that has a severity under the
section of its severity. «Whose numbers this reaches» lists each change that reaches the product:
its severity and code, what raised it, its subject and detail, its metrics when they are more than
its subject, its dashboards, owners and charts — each chart with how it reaches the product
(`computes_metric`, `reads_field`) — and how many charts of each gap code the run could not
resolve in whole, in code order.

The changes and the charts SHALL be those the bundle holds: the payload is read back with the
manifest digest the run reported pinned, a signed bundle through the key that signed it. A run
whose impact check wrote no payload has no section of whose numbers move. The comment carries no
clock of its own: two runs of one change with one `--created-at` write the same bytes, UTF-8 with
line feeds.

The comment is no evidence. The report on standard output and every file of the bundle SHALL be
those of the same run without `--pr-comment`, and the comment SHALL NOT be written into the
evidence directory.

## Requirement: a run without a verdict writes that it has none

When the run stops before a verdict with the one `error:` line and exit 2 of `decision verify` —
an input, the settings, an engine that did not answer, a signing key it cannot use — or when the
payload cannot be read back out of the bundle with the digest the run reported, the command SHALL
exit 2 and write the comment `### Auto_BI release gate: no verdict`. It says that the commit has
no verdict and that this is not a pass, and gives the reason: the text of the `error:` line.

A `--pr-comment` path that is a directory, whose parent is not a directory, or that lies in the
evidence directory, at any depth, or is it SHALL be refused before the run, however the path is
spelled — through a link, or in another case where the file system folds case: exit 2, one
`error: --pr-comment` line, nothing on standard output and nothing in the evidence directory.

The comment SHALL be written whole or not at all, through a temporary file in its directory that
replaces it. A write the file system refuses after the run ends the command with exit 2 under the
verdict it printed, and leaves neither the comment nor the temporary file.

## Requirement: the text of the comment is data

A pull request renders GitHub Flavored Markdown and GitHub's references: an `@name` notifies a
person or a team; `#12`, `GH-12`, `owner/repo#12` and a link to an issue write into that issue's
timeline. What a run read SHALL render as the text it is and SHALL be none of these:

- A value — a ref, a code, a digest, a time — SHALL be one code span. Each character that is not
  printable is spelled by its escape, as the line after `error: ` spells it
  ([one-line errors](one-line-errors.md)); past its cap the value is cut and ends with `…`. The
  fence is one backtick longer than the longest run of backticks inside; a space pads the text
  where CommonMark would strip one or read a backtick of it as the fence's; an empty value is
  `(empty)`.
- A detail SHALL be spelled and cut the same way and set as prose on one line: words are split
  at each space and joined by one. A word that holds `@`, `#` or `://`, or that reads `gh-` and a
  digit or `www.` at its start or after a character other than a letter, a digit or `_`, in any
  case, is a code span; in every other word each ASCII punctuation character is escaped by a
  backslash.
- A `|` in a cell of the table SHALL be escaped, so the value keeps its row.

The comment SHALL hold no HTML but the `<details>`, `<summary>Every check (N)</summary>` and
`</details>` lines of its table, and no code block, fence or block quote. Its inline content is
text, code spans and its own labels in bold.

## Requirement: the comment fits a pull request

GitHub refuses a comment body longer than 65,536 characters. The comment SHALL be at most 60,000
UTF-16 code units, never fewer than its characters, so that the marker and the line the action
adds fit too:

- a ref is cut at 200 characters, a detail at 600, the reason of a run without a verdict at 2,000;
- a list of refs or of charts shows 10 and ends with «and N more … in the evidence summary»; the
  section of whose numbers move shows 20 changes and then «and N more changes in the evidence
  summary»;
- past the limit, whole sections are left out from the last one up, never a part of one, and the
  line «Left out to fit a pull request comment: … The evidence summary holds them.» names them.

The head and the foot SHALL fit in 8,192 units at every cap, so a comment can always say what it
left out; a limit below 8,192 is refused.

## Requirement: the action keeps one comment a product and exits by the gate's answer

The composite action SHALL install uv pinned to the commit the project's CI pins and run
`python -I -X utf8 -m auto_bi.github_action` from the lock of the action
(`uv run --frozen --no-dev --project "$GITHUB_ACTION_PATH"`) in an environment under
`RUNNER_TEMP`, on the Python of `python-version` (3.12 unless given). Isolated: the working
directory — the checkout of the pull request — is not on its path, nor is `PYTHONPATH`, and no
`PYTHON*` variable applies, so no module of the pull request is imported in place of the
action's; its standard streams are UTF-8. Each input SHALL reach the module as a
`DECISION_GATE_*` variable — never `AUTO_BI_*`, which the gate reads as its configuration — and
none is expanded into the script.

The module SHALL:

1. refuse, with one line `::error::auto_bi decision gate: …` and exit 2, before the gate runs and
   before anything is written or posted: a `pull_request_target` event (`GITHUB_EVENT_NAME`),
   which hands the base repository's write token and secrets to a job that checks out the pull
   request's files; no contract path; an empty `base` or `candidate` (GitHub
   does not enforce `required:` on an input of a composite action); a contract path that begins
   with `-`, which would be read as an option; a `comment` other than `true` or `false` in any
   case; a `fail-on` other than `block`, `review` or `never`;
2. run `decision verify` as a child process: each input that is not empty as `--flag=value` in
   the order of the inputs, `--evidence` — the `evidence` input, or a new directory under
   `RUNNER_TEMP` —, `--pr-comment` in a new work directory, `--json`, then the contract paths,
   one a line of `contracts`. The child inherits the job's environment, the `AUTO_BI_*` settings
   of `--dwh` among it, less each `DECISION_GATE_*` variable and `GITHUB_TOKEN`, `GH_TOKEN`,
   `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN`, `ACTIONS_RUNTIME_TOKEN` and
   `ACTIONS_ID_TOKEN_REQUEST_TOKEN`. The child is started isolated, as the action starts the
   module. What it writes to standard error — a parser warning quotes a chart's SQL, line breaks
   and all — is set in the log after it exits, between `::stop-commands::<token>` and
   `::<token>::`, where the runner reads no workflow command: the token is 32 random hexadecimal
   digits drawn after the child exits, and drawn again while the text holds it. A child that
   wrote nothing to standard error gets no such lines;
3. set the comment the gate wrote — or, when it wrote none, the comment of a run without a
   verdict whose reason is that `decision verify` exited with its code and wrote no comment —
   under the marker line `<!-- auto-bi-decision-gate:<key> -->`, the key the first 16 hex digits
   of the SHA-256 of the `product` input, and above the line that names the commit as
   `Commit <sha>` and links the workflow run: the head commit of the pull request, else
   `GITHUB_SHA`, and the link of the run, each left out unless it has the form GitHub gives it;
4. write that body to `pull-request-comment.md` in its work directory, append it to the job
   summary, and write the outputs `verdict`, `exit-code`, `run-id`, `evidence-digest`, `evidence`
   and `report` as `name<<delimiter` blocks; the verdict, the run and the digest come from the
   gate's JSON report and are empty when the gate did not exit 0, 3 or 4;
5. on a pull request event, with a token and `comment: true`, read the comments of the pull
   request, 100 a page and 100 pages at most, update the first whose first line is the marker and
   whose author is `comment-author` (`github-actions[bot]` unless given), and add one when it
   finds none. A comment that holds the marker below its first line, or that another account
   wrote, is never edited. The token goes only to `GITHUB_API_URL` — an https address, or an http
   one on `127.0.0.1`, `localhost` or `::1`, with no user in it; one that does not parse as an
   address is refused like one that is not https — and no redirect is followed;
6. say in one line why it posted nothing and go on: `::notice::` for an event that is not a pull
   request, `::warning::` without a token, and `::warning::` for a refusal or an answer GitHub
   should not give, with the status and GitHub's message on one line, cut at 300 characters, and
   each `%` written as `%25`;
7. exit 4 when the gate blocks and `fail-on` is `block` or `review`, 3 when it needs review and
   `fail-on` is `review`, 2 when the gate reached no verdict whatever `fail-on` says, and 0
   otherwise. Posting never changes the exit status.

### Scenario: the chart-impact pilot

- **GIVEN** the timezone change of `2026-08-25-ch-tz-margin` with the dashboards of the
  chart-impact overlay, `decision://sales/weekly_margin` and `--created-at 2026-09-12T16:00:00Z`
- **WHEN** `decision verify --json --pr-comment comment.md` runs
- **THEN** it exits 4, and the comment is the one `comment_for_run` renders for the report it
  printed: `### Auto_BI release gate: block`; `block, exit code 4 (checks: 1 block, 3 review, 6
  advisory, of 15)`; the manifest digest with `evidence.verified`; the chart
  `dashboard://weekly_margin@7#margin_by_region` (`computes_metric`); one `chart.metric_unread`;
  no carriage return
- **AND** the directory holds the comment and the evidence directory only, and a second run of
  the change writes the same bytes
- **AND** the report and every file of the bundle are those of the run without `--pr-comment`

### Scenario: a signed bundle

- **GIVEN** the same run with `--signing-key`
- **WHEN** it writes the comment
- **THEN** the comment names the chart, read back through the key that signed the manifest
- **AND** reading the payload back without the key raises `EvidenceReadError`

### Scenario: no verdict

- **GIVEN** the same run with a base that does not exist; or the run whose manifest is gone after
  it sealed the bundle; or the run that reports a digest the bundle does not hash to
- **WHEN** `decision verify --pr-comment comment.md` runs
- **THEN** it exits 2 with one `error:` line, and the comment is `### Auto_BI release gate: no
  verdict` with the reason of that line; after a run that reached a verdict, the verdict is
  printed above the error

### Scenario: a path the comment cannot take

- **GIVEN** a `--pr-comment` that is a directory, lies in a directory that does not exist, lies in
  the evidence directory or in a run directory under it, is the evidence directory, or lies under
  it spelled in capitals on a file system that folds case
- **WHEN** `decision verify` runs
- **THEN** it exits 2 with one `error: --pr-comment` line before the run: standard output is
  empty and the evidence directory holds nothing
- **AND** when the file system refuses the comment after the run (`No space left on device`), the
  command exits 2 under its verdict, and no comment and no temporary file are left

### Scenario: references in a detail

- **GIVEN** a detail with mentions (`@octocat`, `@org/team,`), issues (`#12,`, `owner/repo#5,`,
  `GH-7,`), links (`https://example.com/x`, `www.example.com`, `(www.x.org)`), `**bold**`, a
  Markdown link to `javascript:x`, `<img src=x>`, a pipe, `1.`, `~~s~~` and `$x$`
- **WHEN** the comment is rendered
- **THEN** each word with a mention, an issue, a link or `www.` is a code span, the rest is
  escaped, and a CommonMark reader with tables and strikethrough finds no link, image, HTML,
  emphasis or reference in the comment
- **AND** so it is for 40 random details and refs and 10 random reasons drawn from line breaks,
  a tab, NUL, a no-break space, a line separator, a lone surrogate, an emoji, `www.`, `gh-1`,
  `://`, `@x`, `#1`, HTML, a link target, four spaces and ASCII punctuation, each of which reads
  back as its spelling

### Scenario: a comment past the limit

- **GIVEN** the pilot's report with 200 more checks, each with a detail of 700 characters
- **WHEN** the comment is rendered
- **THEN** it is at most 60,000 units; «Advisory findings», «Not done in this run» and «Every
  check» are left out and named, and it still ends with the command that checks the bundle
- **AND** with each value of the head an emoji 600 times — or a backtick 600 times, the value a
  code span costs most for —, a failed run and a limit of 8,192, all six sections are left out
  and named, and the head and the foot fit

### Scenario: the action on a pull request, twice

- **GIVEN** the inputs of the chart-impact pilot, a pull request event, a token, and GitHub's API
  on this machine
- **WHEN** the action runs twice
- **THEN** the first run adds the comment and the second updates it: one comment, which starts
  with the product's marker and the heading of `block`
- **AND** each request carried the token in `Authorization` only, no line of the output holds it,
  and the outputs name the verdict, the digest the comment holds and the evidence directory

### Scenario: a chart's SQL in the log, from a checkout of its own

- **GIVEN** the inputs of the chart-impact pilot, a chart whose SQL holds the lines
  `::warning title=Auto_BI release gate::pass, …` and `::add-mask::block`, and a checkout — the
  working directory and on `PYTHONPATH` — whose `auto_bi` package exits 97 when imported
- **WHEN** the action runs, and when its module runs as `action.yml` runs it
- **THEN** the gate blocks (exit 4); the parser warning that quotes the SQL is in the log between
  the `stop-commands` lines, and nothing else of the run's standard error is in the log or on
  standard error; the module run writes the gate's own comment of no verdict
- **AND** a `pull_request_target` event is refused with exit 2 before the gate runs, while
  `pull_request` and `pull_request_review` run it

### Scenario: comments the action did not write

- **GIVEN** a first page of comments with the marker in a comment of another account, the marker
  below the first line of a comment of the bot, another product's marker, an id that is not a
  number and an item that is not a comment; and a second page with the bot's comment whose first
  line is the marker and a carriage return
- **WHEN** the action runs
- **THEN** it reads two pages and updates the comment of the second

### Scenario: GitHub refuses

- **GIVEN** a token GitHub answers 403 to, as it answers a fork's read-only token
- **WHEN** the action runs over a gate that blocks, with `fail-on: never`
- **THEN** it warns that the comment was not posted, with GitHub's message, writes the job summary
  and exits 0

### Scenario: a gate that wrote no comment

- **GIVEN** a gate that exits 1 and writes no comment, and `fail-on: never`
- **WHEN** the action runs
- **THEN** it posts the comment of no verdict, its outputs hold no verdict, and it exits 2

## Known boundaries

- No test runs GitHub's renderer: a CommonMark reader with tables and strikethrough stands in.
  GitHub applies a repository's own autolink references (a prefix like `JIRA-` set in its
  settings), emoji shortcodes (`:x:`) and links to commit SHAs to the text after Markdown, so a
  word of a detail can render as one of them; none notifies a person or writes into a timeline.
- A workflow on `pull_request` from a fork gets a read-only token: the comment is a warning and
  the job summary holds it. `pull_request_target` is refused; another trigger that hands secrets
  to a job checking out the pull request's files — `workflow_run` with the head checked out — is
  the workflow's to guard: the action does not see what was checked out.
- With `dwh`, `decision verify` reads its `AUTO_BI_*` settings from the environment and then from
  a `.env` file in the working directory, the checkout of the pull request. A workflow that runs
  the gate on an engine sets every connection variable itself — host, port, database, user,
  password — so that a `.env` of the pull request sets none of them (backlog).
- `astral-sh/setup-uv` without a `version` takes uv's version from a `required-version` in the
  `pyproject.toml` or `uv.toml` of the working directory, and the latest one otherwise: a pull
  request can choose which released uv installs the action's frozen environment; a uv that cannot
  read the lock fails the step (backlog).
- Between the `stop-commands` lines the runner runs no command, but a line of the gate's standard
  error still reaches the log as the gate wrote it, where it may look like a line of the runner's
  own.
- A workflow that runs `decision verify` itself, not through the action, gets the gate's standard
  error as it is: sqlglot's warning about a chart's SQL it does not support quotes that SQL, line
  breaks kept, on a stream the runner reads workflow commands from (backlog).
- The marker keys a comment to the `product` input alone: two jobs of one pull request that verify
  one product — on two environments — share one comment, the last run's. Two runs at once can
  each find none and each add one.
- The comment names the bundle by its digest and does not carry it: a workflow that wants the
  bundle kept uploads the `evidence` directory itself.
- `decision verify` that ends in an exception it does not handle (exit 1) leaves the comment file
  as it was — a chart's SQL with a quote it does not close is one (backlog). The action reads such
  a run as one that wrote no comment because its work directory is new each run.
- The action is not published: no tag, no release, no Marketplace listing. A workflow uses it by
  the commit it pins.

## Usage

```yaml
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<commit>
        with:
          fetch-depth: 0
      - name: Check out the base
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
        run: git worktree add "$RUNNER_TEMP/base" "$BASE_SHA"
      - uses: brownjuly2003-code/Auto_BI@<commit>
        with:
          contracts: contracts
          base: ${{ runner.temp }}/base/semantic
          candidate: semantic
          dashboards: dashboards
```
