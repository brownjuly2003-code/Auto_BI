"""The entry point of the composite GitHub Action in `action.yml` (ADR 0012, D3).

`auto_bi decision verify` decides and writes the pull-request comment
(`verification/pull_request.py`); this module carries that comment to the pull request the
workflow runs for, and does nothing else. It is the one module of the package that writes
to a service, and it runs only where a workflow runs the action, with the token the
workflow hands it, against that workflow's own pull request: the project publishes nothing.

The action passes its inputs as ``DECISION_GATE_*`` variables — never ``AUTO_BI_*``, which
`Settings` reads as configuration — and this module

1. refuses a ``pull_request_target`` run: that event hands the base repository's write
   token and secrets to a job that checks out the pull request's files;
2. runs the gate as a child process, ``decision verify --json --pr-comment``, each input
   as ``--flag=value`` and the contract paths last. The child inherits the environment the
   connection settings of ``--dwh`` live in, less every ``DECISION_GATE_*`` variable and
   the GitHub token variables: a token is the action's, never the gate's. It runs isolated
   (``-I``), as `action.yml` runs this module: no module of the checked-out pull request,
   in the working directory or on ``PYTHONPATH``, is imported in place of the gate's.
   What it writes to standard error is set in the log between ``stop-commands`` lines,
   so no line of it — a parser warning quoting a chart's SQL — is read as a workflow
   command;
3. sets the comment — or, when the gate wrote none, one that says it did not run — under a
   marker line that keys it to the ``product`` input, and over a line naming the commit
   and the workflow run;
4. writes it to the job summary, and the run's facts to the action's outputs;
5. updates the comment that starts with the same marker and was written by
   ``comment-author``, or adds one. A refusal by GitHub — a fork's read-only token, a
   missing permission — is a warning: the comment copies the gate's answer, and the
   step's exit status is that answer. No redirect is followed, so the token goes to
   ``GITHUB_API_URL`` and nowhere else, and only over https (http only to a loopback
   address);
6. exits by the gate's exit code and ``fail-on``. A gate that did not reach a verdict
   fails the step whatever ``fail-on`` says.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auto_bi.verification.pull_request import render_pull_request_failure

PREFIX = "DECISION_GATE_"

#: Inputs handed to `decision verify` as ``--flag=value``, in this order; an empty one is not.
FLAG_INPUTS: tuple[tuple[str, str], ...] = (
    ("base", "--base"),
    ("candidate", "--candidate"),
    ("product", "--product"),
    ("dashboards", "--dashboards"),
    ("assets", "--assets"),
    ("results", "--results"),
    ("environment", "--environment"),
    ("dwh", "--dwh"),
    ("signing-key", "--signing-key"),
    ("created-at", "--created-at"),
    ("base-semantic-manifest", "--base-semantic-manifest"),
    ("base-dbt-database", "--base-dbt-database"),
    ("candidate-semantic-manifest", "--candidate-semantic-manifest"),
    ("candidate-dbt-database", "--candidate-dbt-database"),
)
#: Inputs without which `decision verify` has nothing to compare; checked before it runs.
REQUIRED_INPUTS: tuple[str, ...] = ("base", "candidate")
#: Inputs this module reads itself.
OWN_INPUTS: tuple[str, ...] = (
    "contracts",
    "evidence",
    "comment",
    "comment-author",
    "fail-on",
    "github-token",
)
#: What an input left empty means; `action.yml` declares the same defaults.
DEFAULTS: Mapping[str, str] = {
    "comment": "true",
    "comment-author": "github-actions[bot]",
    "fail-on": "block",
}
#: The action's outputs, written to ``GITHUB_OUTPUT``.
OUTPUTS: tuple[str, ...] = (
    "verdict",
    "exit-code",
    "run-id",
    "evidence-digest",
    "evidence",
    "report",
)
#: Variables the gate's process does not inherit.
TOKEN_VARIABLES = frozenset(
    {
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "ACTIONS_RUNTIME_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GH_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GITHUB_TOKEN",
    }
)
FAIL_ON = ("block", "review", "never")
#: Exit codes of a gate that reached a verdict (`verification/model.py`).
_VERDICT_EXITS = frozenset({0, 3, 4})
_PAGE = 100
_MAX_PAGES = 100
_TIMEOUT = 30
_MESSAGE_CAP = 300
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_RUN_ID = re.compile(r"[0-9]+")
_SERVER = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?")
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

#: (method, url, body, headers) -> (status, body).
Transport = Callable[[str, str, bytes | None, Mapping[str, str]], tuple[int, bytes]]
#: (arguments of `auto_bi`, environment) -> (exit code, standard output, standard error).
Runner = Callable[[Sequence[str], Mapping[str, str]], tuple[int, bytes, bytes]]
#: How `action.yml` and `_run` start Python: isolated from the working directory and from
#: ``PYTHON*`` variables, its standard streams in UTF-8.
PYTHON_FLAGS: tuple[str, ...] = ("-I", "-X", "utf8")
#: The event the action refuses to run for (item 1 of the module's docstring).
REFUSED_EVENT = "pull_request_target"


class ConfigError(Exception):
    """An input the action cannot run with: nothing is run, nothing is posted."""


class CommentError(Exception):
    """GitHub did not take the comment, or could not be asked."""


def env_name(name: str) -> str:
    """The variable `action.yml` passes the input *name* in."""
    return PREFIX + name.upper().replace("-", "_")


@dataclass(frozen=True)
class Config:
    contracts: tuple[str, ...]
    #: ``(flag, value)`` of each `FLAG_INPUTS` input that is not empty.
    flags: tuple[tuple[str, str], ...]
    product: str
    evidence: str
    comment: bool
    comment_author: str
    fail_on: str
    token: str


def read_config(environ: Mapping[str, str]) -> Config:
    """The action's inputs; one it cannot run with raises `ConfigError`.

    A value is taken without its surrounding whitespace, and ``contracts`` one path a line,
    blank lines left out. A contract path that begins with ``-`` is refused: it would be read
    as an option, and ``./`` before it names the same file.
    """

    def value(name: str) -> str:
        return environ.get(env_name(name), "").strip()

    contracts = tuple(
        line.strip() for line in environ.get(env_name("contracts"), "").splitlines() if line.strip()
    )
    if not contracts:
        raise ConfigError("contracts: no contract path given")
    for name in REQUIRED_INPUTS:
        if not value(name):
            raise ConfigError(f"{name}: no semantic ref given")
    for path in contracts:
        if path.startswith("-"):
            raise ConfigError(
                f"contracts: {path!r} begins with '-' and would be read as an option; "
                f"write ./{path}"
            )
    comment = (value("comment") or DEFAULTS["comment"]).lower()
    if comment not in ("true", "false"):
        raise ConfigError(f"comment: {comment!r} is neither true nor false")
    fail_on = value("fail-on") or DEFAULTS["fail-on"]
    if fail_on not in FAIL_ON:
        raise ConfigError(f"fail-on: {fail_on!r} is not one of {', '.join(FAIL_ON)}")
    flags = tuple((flag, given) for name, flag in FLAG_INPUTS if (given := value(name)))
    return Config(
        contracts=contracts,
        flags=flags,
        product=value("product"),
        evidence=value("evidence"),
        comment=comment == "true",
        comment_author=value("comment-author") or DEFAULTS["comment-author"],
        fail_on=fail_on,
        token=value("github-token"),
    )


def verify_argv(config: Config, evidence: str, comment: Path) -> list[str]:
    """The arguments of `auto_bi`: options as ``--flag=value``, then the contract paths."""
    return [
        "decision",
        "verify",
        *(f"{flag}={given}" for flag, given in config.flags),
        f"--evidence={evidence}",
        f"--pr-comment={comment}",
        "--json",
        *config.contracts,
    ]


def child_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The gate's environment: the job's, less the action's inputs and the token variables."""
    return {
        name: given
        for name, given in environ.items()
        if not name.upper().startswith(PREFIX) and name.upper() not in TOKEN_VARIABLES
    }


def exit_for(code: int, fail_on: str) -> int:
    """The step's exit code: the gate's, where ``fail-on`` makes its verdict a failure."""
    if code not in _VERDICT_EXITS:
        return 2
    if code == 4 and fail_on in ("block", "review"):
        return 4
    if code == 3 and fail_on == "review":
        return 3
    return 0


def marker_for(product: str) -> str:
    """The first line of the comment of *product*: a hash, so no input text is in it."""
    key = hashlib.sha256(product.encode("utf-8")).hexdigest()[:16]
    return f"<!-- auto-bi-decision-gate:{key} -->"


@dataclass(frozen=True)
class RunContext:
    """What the workflow run says of itself: the pull request, the commit, the run."""

    number: int | None
    commit: str
    run_url: str
    api: str
    repository: str


def run_context(environ: Mapping[str, str]) -> RunContext:
    """Read the pull request from the event payload, the rest from the runner's variables.

    A value that does not have the form GitHub gives it is left out rather than written into
    the comment: a commit is a full hexadecimal SHA, a run link is built only from an https
    server, an ``owner/name`` repository and a numeric run id.
    """
    event: Any = {}
    path = environ.get("GITHUB_EVENT_PATH", "")
    if path:
        try:
            event = json.loads(Path(path).read_bytes())
        except (OSError, ValueError):
            event = {}
    pull = event.get("pull_request") if isinstance(event, dict) else None
    number = pull.get("number") if isinstance(pull, dict) else None
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        number = None
    head = pull.get("head") if isinstance(pull, dict) else None
    commit = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        commit = environ.get("GITHUB_SHA", "")
    if not _SHA.fullmatch(commit):
        commit = ""
    server = environ.get("GITHUB_SERVER_URL", "")
    repository = environ.get("GITHUB_REPOSITORY", "")
    run_id = environ.get("GITHUB_RUN_ID", "")
    run_url = ""
    if (
        _SERVER.fullmatch(server)
        and _REPOSITORY.fullmatch(repository)
        and _RUN_ID.fullmatch(run_id)
    ):
        run_url = f"{server}/{repository}/actions/runs/{run_id}"
    return RunContext(
        number=number,
        commit=commit,
        run_url=run_url,
        api=environ.get("GITHUB_API_URL", "") or "https://api.github.com",
        repository=repository,
    )


def comment_body(marker: str, core: str, context: RunContext) -> str:
    """The marker line, the gate's comment, and the line naming the commit and the run."""
    lines = [marker, core.rstrip("\n")]
    named = []
    if context.commit:
        named.append(f"Commit `{context.commit}`")
    if context.run_url:
        named.append(f"[workflow run]({context.run_url})")
    if named:
        lines += ["", " · ".join(named)]
    return "\n".join(lines) + "\n"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is answered as the status it is: the token is not sent anywhere else."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_transport(
    method: str, url: str, body: bytes | None, headers: Mapping[str, str]
) -> tuple[int, bytes]:
    """One request with the standard library; an answer of any status is returned."""
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with _OPENER.open(request, timeout=_TIMEOUT) as response:
            return int(response.status), bytes(response.read())
    except urllib.error.HTTPError as error:
        with error:
            return int(error.code), bytes(error.read())
    except (OSError, http.client.HTTPException, ValueError) as error:
        raise CommentError(f"{method} could not be sent: {type(error).__name__}") from None


class GitHub:
    """The four calls the comment needs, on one repository's issue comments."""

    def __init__(self, api: str, repository: str, token: str, transport: Transport) -> None:
        refused = "GITHUB_API_URL is neither an https address nor an http one on this machine"
        try:
            parts = urllib.parse.urlsplit(api)
        except ValueError:
            raise CommentError(refused) from None
        secure = parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in _LOOPBACK)
        if not secure or not parts.hostname or parts.username or parts.password:
            raise CommentError(refused)
        if not _REPOSITORY.fullmatch(repository):
            raise CommentError("GITHUB_REPOSITORY is not an owner/name repository")
        self._base = f"{api.rstrip('/')}/repos/{repository}"
        self._token = token
        self._transport = transport

    def upsert(self, number: int, marker: str, author: str, body: str) -> str:
        """Update the comment *marker* keys, or add one; say which."""
        found = self.find(number, marker, author)
        if found is None:
            self._call("POST", f"/issues/{number}/comments", {"body": body})
            return "added"
        self._call("PATCH", f"/issues/comments/{found}", {"body": body})
        return "updated"

    def find(self, number: int, marker: str, author: str) -> int | None:
        """The first comment whose first line is *marker* and whose author is *author*.

        A comment quoting the marker, or one another account wrote, is not the action's: it
        is never edited.
        """
        for page in range(1, _MAX_PAGES + 1):
            items = self._call("GET", f"/issues/{number}/comments?per_page={_PAGE}&page={page}")
            if not isinstance(items, list):
                raise CommentError("GitHub listed the comments as something other than a list")
            for item in items:
                if not isinstance(item, dict):
                    continue
                body, user, ident = item.get("body"), item.get("user"), item.get("id")
                login = user.get("login") if isinstance(user, dict) else None
                if (
                    isinstance(body, str)
                    and body.split("\n", 1)[0].rstrip("\r") == marker
                    and login == author
                    and isinstance(ident, int)
                    and not isinstance(ident, bool)
                ):
                    return ident
            if len(items) < _PAGE:
                return None
        return None

    def _call(self, method: str, path: str, document: Mapping[str, str] | None = None) -> Any:
        data = None if document is None else json.dumps(document).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "auto-bi-decision-gate",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        status, answer = self._transport(method, self._base + path, data, headers)
        endpoint = path.split("?", 1)[0]
        if not 200 <= status < 300:
            raise CommentError(f"GitHub answered {status} to {method} {endpoint}: {_said(answer)}")
        if not answer:
            return None
        try:
            return json.loads(answer.decode("utf-8"))
        except ValueError:
            raise CommentError(f"GitHub answered {method} {endpoint} with no JSON") from None


def _said(answer: bytes) -> str:
    """The ``message`` of GitHub's error document, on one line and short."""
    try:
        document = json.loads(answer.decode("utf-8"))
    except ValueError:
        document = None
    message = document.get("message") if isinstance(document, dict) else None
    if not isinstance(message, str) or not message:
        return "no message"
    return _one_line(message)[:_MESSAGE_CAP]


def _one_line(text: str) -> str:
    return "".join(char if char.isprintable() else ascii(char)[1:-1] for char in text)


def _command(kind: str, message: str) -> None:
    """A workflow command on one line, read back as written.

    A character that is not printable — a CR or a LF among them — is spelled by its escape, so
    the command stays on its line; the runner reads ``%25``, ``%0D`` and ``%0A`` back as ``%``,
    CR and LF, so a ``%`` is written as ``%25``.
    """
    print(f"::{kind}::{_one_line(message).replace('%', '%25')}", flush=True)


def _run(argv: Sequence[str], env: Mapping[str, str]) -> tuple[int, bytes, bytes]:
    completed = subprocess.run(
        [sys.executable, *PYTHON_FLAGS, "-m", "auto_bi.cli", *argv],
        env=dict(env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _relay(stderr: bytes) -> None:
    """The gate's standard error, in the log as text that the runner reads no command from.

    The runner reads ``::command::`` lines and ``##[command]`` from both streams of a step,
    and a line the gate writes may quote the pull request — a parser warning holds a chart's
    SQL. Between ``::stop-commands::<token>`` and ``::<token>::`` it runs none of them. The
    token is drawn after the gate has exited, and drawn again while the text holds it, so no
    line of the text ends the stop early.
    """
    text = stderr.decode("utf-8", errors="replace")
    if not text:
        return
    token = uuid.uuid4().hex
    while token in text:
        token = uuid.uuid4().hex
    ended = text if text.endswith("\n") else text + "\n"
    sys.stdout.write(f"::stop-commands::{token}\n{ended}::{token}::\n")
    sys.stdout.flush()


def _facts(stdout: bytes) -> dict[str, str]:
    """Verdict, run id and evidence digest from the gate's JSON report."""
    try:
        document = json.loads(stdout.decode("utf-8"))
    except ValueError:
        return {}
    if not isinstance(document, dict):
        return {}
    facts = {}
    for output, field in (
        ("verdict", "verdict"),
        ("run-id", "run_id"),
        ("evidence-digest", "evidence_digest"),
    ):
        given = document.get(field)
        facts[output] = given if isinstance(given, str) else ""
    return facts


def _append(path: str, text: str, what: str) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as error:
        _command("warning", f"the {what} was not written: {type(error).__name__}")


def _outputs(values: Mapping[str, str]) -> str:
    """``name<<delimiter`` blocks: a value may hold any line and still end where it does."""
    blocks = []
    for name in OUTPUTS:
        delimiter = f"ghadelimiter_{uuid.uuid4()}"
        blocks.append(f"{name}<<{delimiter}\n{values.get(name, '')}\n{delimiter}\n")
    return "".join(blocks)


def main(
    environ: Mapping[str, str] | None = None,
    *,
    transport: Transport | None = None,
    runner: Runner | None = None,
) -> int:
    """Run the gate, publish its comment where the inputs say, and exit by its answer."""
    env = dict(os.environ if environ is None else environ)
    if env.get("GITHUB_EVENT_NAME", "") == REFUSED_EVENT:
        _command(
            "error",
            f"auto_bi decision gate: not run for {REFUSED_EVENT}, which hands the base "
            "repository's write token and secrets to the pull request's files; "
            "run it on pull_request",
        )
        return 2
    try:
        config = read_config(env)
        workdir = Path(tempfile.mkdtemp(prefix="auto-bi-gate-", dir=env.get("RUNNER_TEMP") or None))
    except ConfigError as error:
        _command("error", f"auto_bi decision gate: {error}")
        return 2
    except OSError as error:
        _command("error", f"auto_bi decision gate: no work directory: {type(error).__name__}")
        return 2
    written = workdir / "comment.md"
    evidence = config.evidence or str(workdir / "evidence")
    code, stdout, stderr = (runner or _run)(verify_argv(config, evidence, written), child_env(env))
    _relay(stderr)
    facts = _facts(stdout) if code in _VERDICT_EXITS else {}
    if facts:
        line = (
            f"verdict: {facts['verdict']} (exit {code}), run {facts['run-id']}, "
            f"evidence {facts['evidence-digest']}"
        )
        print(_one_line(line), flush=True)
    try:
        core = written.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        core = render_pull_request_failure(
            f"auto_bi decision verify exited with code {code} and wrote no comment"
        )
    context = run_context(env)
    marker = marker_for(config.product)
    body = comment_body(marker, core, context)
    report = workdir / "pull-request-comment.md"
    report.write_bytes(body.encode("utf-8"))
    _append(env.get("GITHUB_STEP_SUMMARY", ""), body, "job summary")
    _append(
        env.get("GITHUB_OUTPUT", ""),
        _outputs({**facts, "exit-code": str(code), "evidence": evidence, "report": str(report)}),
        "action outputs",
    )
    if config.comment:
        _post(config, context, marker, body, transport or urllib_transport)
    return exit_for(code, config.fail_on)


def _post(
    config: Config, context: RunContext, marker: str, body: str, transport: Transport
) -> None:
    if context.number is None:
        _command("notice", "not a pull request event: the comment is in the job summary only")
        return
    if not config.token:
        _command("warning", "no github-token: the pull request comment was not posted")
        return
    try:
        client = GitHub(context.api, context.repository, config.token, transport)
        outcome = client.upsert(context.number, marker, config.comment_author, body)
    except CommentError as error:
        _command("warning", f"the pull request comment was not posted: {error}")
        return
    print(f"pull request comment {outcome}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
