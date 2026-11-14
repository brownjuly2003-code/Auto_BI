# Capability: The decision commands as tools of a read-only MCP server

<!-- Durable spec: it outlives the run. One capability per file; MyFlow injects the requirements a task
     references into the WorkOrder and the review pack. Format is OpenSpec-compatible. -->

Semantic layers became an input of AI agents through the Model Context Protocol in 2026 (ADR
0012: the dbt MCP server, Cube, the MCP services of semantic layers). An agent that changes a
metric, or answers a question with one, could reach the gate only through a shell: run
`decision verify`, parse its standard output, find the bundle. `auto_bi decision mcp` serves the
decision commands as tools of an MCP server on standard input and output (ADR 0012, D5): the
same use cases, the same JSON, and no new verdict. It writes to no BI and reaches no engine.

The server is `auto_bi/mcp/` — `server.py` speaks the protocol, `tools.py` maps tools to use
cases — with no MCP SDK: a server of tools on stdio is one file of JSON-RPC, and the SDK would
bring a web stack into the lock file.

## Requirement: the server speaks MCP 2026-07-28 and the legacy handshake

The server SHALL read one JSON-RPC 2.0 message per line of standard input and write each answer
as one line of standard output, in ASCII (JSON escapes), until standard input closes; then the
command exits 0. It SHALL serve both eras of the protocol:

- a request whose `params._meta` names `io.modelcontextprotocol/protocolVersion` is served
  statelessly at that version. `2026-07-28` is the one modern version; any other value is
  answered `-32022` "Unsupported protocol version" with `data` `{supported: ["2026-07-28"],
  requested}`. A version that is no string, or a request that names the version and has no
  `io.modelcontextprotocol/clientCapabilities` object, is `-32602`, with no `data`: what the
  client sent is not echoed back;
- `server/discover` answers `supportedVersions`, `capabilities` `{tools: {}}`, `instructions`,
  `ttlMs` and `cacheScope` `private`; `tools/list` answers the tools with `ttlMs` and
  `cacheScope`; every modern result carries `resultType` `complete` and
  `_meta.io.modelcontextprotocol/serverInfo`;
- `initialize` selects a legacy version for the rest of the process: the one asked when it is
  `2025-11-25` or `2025-06-18`, otherwise `2025-11-25`. After it, a request without the version
  in `_meta` is served at that version, with `ping`, and without the fields of the modern
  revision; `ping` is answered `{}` before `initialize` too, as the legacy lifecycle allows, and
  selects no version;
- a request with neither — no version in `_meta`, no `initialize` before — is `-32602`.

A line that is no JSON, or nests deeper than the parser goes, is `-32700` with id null. A batch,
or a value that is no object, is `-32600` with id null; so is an id that is not a string or an
integer (null, a boolean, a fraction). A request without `jsonrpc: "2.0"`, with a method that is
not a string or with params that are not an object is `-32600` with its id; an object with no
`method`, `result` or `error` is `-32600` with its id when the id is a string or an integer,
otherwise with id null. An unknown method is `-32601`. A notification, a response and a blank
line are not answered. A defect of the server — an answer JSON cannot spell among them — is
`-32603` "Internal error", its traceback on standard error, and the next request is served.
Each answer is flushed before the next line is read: a client that waits for an answer before
it sends the next request gets it.

## Requirement: each tool answers what its command prints

`tools/call` SHALL answer `content` with one text block — the JSON of `structuredContent` — and
`structuredContent`, always an object, and `isError`. The tools are, in this order:

| Tool | Answers | Command |
|---|---|---|
| `validate` | the validation report | `decision validate --json` |
| `impact` | `validation`, `gaps` of both refs, `impact` | `decision verify`, step `impact.products` |
| `verify` | the gate report; writes the bundle | `decision verify --json` |
| `evidence` | the inspection, or `{artifact, payload}` | `decision evidence [--artifact] --json` |
| `runs` | `{runs: [...]}` | `decision runs --json` |

`isError` SHALL be true exactly where the command exits 2 — an input refused, no verdict reached —
and for an artifact read out of a bundle whose manifest digest is not the one pinned (the
command's exit 4 without an inspection). The answer is then `{error: <the command's error
line>}`; `validate` of paths that hold no product adds the `report`. A verdict is never an error:
`block` from `validate` or `verify`, and an inspection that blocks — a tampered bundle, a digest
that is not the one expected, a signature not trusted — are answers with `isError` false.

A name the server does not offer is `-32602` "Unknown tool"; so are a name that is not a string
and arguments that are not an object. Arguments its schema refuses — a key the tool does not
take, a value of another JSON type, an empty path — are an execution error `invalid arguments: …`.
Each `inputSchema` is the strict pydantic model of the arguments: every property described, no
title, `additionalProperties: false`.

## Requirement: impact is the gate's impact step without the run

`impact` SHALL read the contracts, both semantic refs, their dbt artifacts and the delivered
dashboards with the readers and refusals of `verify`, and answer `resolve_impact` over the
products in scope: the one `product` names, or every product of the paths in id order. For one
product its `impact` SHALL be the payload of the `impact.products` check the gate writes for the
same inputs. A ref holding a gap the gate computes no diff over (`gap.contract_invalid`,
`gap.metric_duplicate`) SHALL be refused, three of the gaps named and the rest counted: the
gate skips its diff for it, and the answer would be a diff the gate does not trust. `impact`
grades nothing and writes nothing.

## Requirement: one tool writes, into the directory the operator chose

`verify` SHALL be offered only when the server is started with `--evidence <dir>`, and write its
bundle there; `created_at`, when the call gives none, is the server's clock in UTC. It is
annotated `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: false`: the same
inputs land on the same run id, a second call with them is a no-op and with another `created_at`
a refusal (`already holds bundle`), but inputs changed between calls make another run. Every
other tool is `readOnlyHint: true`; every tool is `openWorldHint: false`.

`--signing-key` (with `--evidence` only) seals the bundles of `verify`; `--trust-key`, repeatable,
is the trust root of `evidence`. Keys are the operator's configuration, never a call's argument;
a key that cannot be read, or `--signing-key` without `--evidence`, ends the command with exit 2
and one `error:` line before the first message.

## Requirement: nothing but messages reaches standard output

Before it reads the first message, the server SHALL keep a duplicate of standard output for the
protocol and point the process's own descriptor of standard output at standard error: a `print`
anywhere in the process — of this code or of a library —, a write to descriptor 1 and what a
buffered `sys.stdout` still holds from before serving began are on standard error, never a
message. What an unbuffered `sys.stdout` (`python -u`, `PYTHONUNBUFFERED`) wrote before that is
out already; `decision mcp` itself prints nothing before it serves.

### Scenario: discovery and a block

- **GIVEN** `decision mcp --evidence evidence` and a formula change of the gross-margin metric
- **WHEN** a client sends `server/discover`, then `tools/call` `verify` with the contracts, both
  refs, the dashboards and `created_at`
- **THEN** discovery lists `2026-07-28`; the call answers the report `decision verify --json`
  prints for the same inputs, verdict `block`, `isError` false
- **AND** the bundle it wrote is byte for byte the command's

### Scenario: the gate's impact, before the gate

- **GIVEN** the formula change, unchanged refs, or a candidate without a field a metric reads
- **WHEN** `impact` is called with or without `product`
- **THEN** its `impact` is the gate's `impact.products` payload and its `gaps` the gate's
  `semantic.gaps` payload; with a second product in the paths and no `product`, both are in
  scope and both are reached

### Scenario: a ref the gate does not diff

- **GIVEN** a candidate with four metric contracts that do not parse and a base holding two
  contracts of one metric
- **WHEN** `impact` is called
- **THEN** it is an error that counts 5 gaps, names three, the base's `gap.metric_duplicate`
  first, and ends `(+2 more)`

### Scenario: an old client

- **GIVEN** `initialize` with `2025-06-18`
- **WHEN** the client sends `notifications/initialized`, `ping`, `tools/list` and `tools/call`
  without `_meta`
- **THEN** the notification is not answered, `ping` is `{}`, and the results carry no
  `resultType`, `ttlMs` or `_meta`; `server/discover` is `-32601`

### Scenario: noise of a library

- **GIVEN** a process with buffered standard output that prints a line before it starts the
  server, and a server whose
  `validate` prints a line and writes one to descriptor 1 before it answers, started with a
  signing and a trust key
- **WHEN** a client calls `server/discover`, `tools/list`, `validate`, `verify` and `evidence`
- **THEN** standard output holds five JSON lines, the three lines are on standard error, and the
  inspection says the bundle is signed and its signature verified

## Known boundaries

- The server is no sandbox: paths are read as the commands read them — relative to its working
  directory, with the rights of the user who started it, through links — and `verify` writes
  only under `--evidence`. A client that should see one checkout runs the server as a user that
  sees only it (backlog: MCP roots).
- No `--dwh`: `verify` is offline, and step 7 answers `check.not_configured` (backlog).
- `pin`, `capture` and `investigate` are not tools.
- No `outputSchema`, no Tasks extension, no progress or cancellation: requests are served one at
  a time in the order they arrive, so a `notifications/cancelled` finds none in flight.
- stdio only; no Streamable HTTP.
- An error line names the command's flags where the use case does (`name the one to verify with
  --product`), not the tool's argument (backlog).

## Usage

```json
{
  "mcpServers": {
    "auto-bi-decisionops": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/Auto_BI_new", "auto_bi", "decision", "mcp",
               "--evidence", "/path/to/evidence"]
    }
  }
}
```
