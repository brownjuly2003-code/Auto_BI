"""The entry point of the composite GitHub Action (ADR 0012, D3): `auto_bi/github_action.py`.

Durable spec: docs/specs/pull-request-comment.md. The action runs `decision verify` as a child
process without the token, sets its comment under a marker keyed to the product, writes the
job summary and the outputs, updates or adds the comment, and exits by the gate's answer and
`fail-on`. Every call to GitHub goes through an injected transport here, except one end to end
run against a local server.
"""

from __future__ import annotations

import http.server
import json
import re
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from auto_bi.github_action import (
    DEFAULTS,
    FLAG_INPUTS,
    OUTPUTS,
    PYTHON_FLAGS,
    CommentError,
    ConfigError,
    GitHub,
    RunContext,
    _command,
    _run,
    child_env,
    comment_body,
    env_name,
    exit_for,
    main,
    marker_for,
    read_config,
    run_context,
    urllib_transport,
    verify_argv,
)
from auto_bi.verification.pull_request import render_pull_request_failure
from tests.test_pull_request_comment import CREATED_AT, OVERLAY, PRODUCT, SOURCE

#: A stand-in for the workflow's token: no credential, only text that must never be shown.
TOKEN = "stand-in-" + "token-value"
HEAD = "0123456789abcdef" * 2 + "01234567"
BASE_SHA = "f" * 40
API = "https://api.github.com"
DIGEST = "sha256:" + "a" * 64
COMMENT = "### Auto_BI release gate: block\n\nthe gate's comment\n"


def _report(verdict: str = "block") -> bytes:
    return json.dumps({"verdict": verdict, "run_id": "run-1", "evidence_digest": DIGEST}).encode()


class Runner:
    """`decision verify` as the action sees it: argv and env in, a comment file and JSON out."""

    def __init__(
        self,
        code: int = 4,
        comment: str | None = COMMENT,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.code = code
        self.comment = comment
        self.stdout = stdout or _report()
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv: Sequence[str], env: Mapping[str, str]) -> tuple[int, bytes, bytes]:
        self.calls.append((list(argv), dict(env)))
        target = next(arg for arg in argv if arg.startswith("--pr-comment="))
        if self.comment is not None:
            Path(target.removeprefix("--pr-comment=")).write_bytes(self.comment.encode("utf-8"))
        return self.code, self.stdout, self.stderr


class Transport:
    """GitHub's issue comments: pages of comments to GET, a POST or PATCH that answers."""

    def __init__(self, pages: list[list[Any]] | None = None, status: int = 0) -> None:
        self.pages = pages if pages is not None else [[]]
        self.status = status
        self.requests: list[tuple[str, str, Any, dict[str, str]]] = []

    def __call__(
        self, method: str, url: str, body: bytes | None, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        document = None if body is None else json.loads(body)
        self.requests.append((method, url, document, dict(headers)))
        if self.status:
            return self.status, b'{"message": "Resource not accessible by integration"}'
        if method == "GET":
            page = int(parse_qs(urlsplit(url).query)["page"][0])
            items = self.pages[page - 1] if page <= len(self.pages) else []
            return 200, json.dumps(items).encode()
        return (201 if method == "POST" else 200), b'{"id": 1}'


def _environ(tmp_path: Path, event: Any = None, **inputs: str) -> dict[str, str]:
    """A runner's environment with a pull request event and the action's inputs."""
    path = tmp_path / "event.json"
    if event is None:
        event = {"pull_request": {"number": 7, "head": {"sha": HEAD}}}
    path.write_text(json.dumps(event), encoding="utf-8")
    (tmp_path / "runner").mkdir(exist_ok=True)
    env = {
        "RUNNER_TEMP": str(tmp_path / "runner"),
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(path),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "123",
        "GITHUB_SHA": BASE_SHA,
        "GITHUB_API_URL": API,
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
        "GITHUB_TOKEN": TOKEN,
        "AUTO_BI_CLICKHOUSE_HOST": "warehouse",
    }
    given = {"contracts": "contracts", "base": "base", "candidate": "candidate"}
    given["github-token"] = TOKEN
    for name, value in {**given, **inputs}.items():
        env[env_name(name)] = value
    return env


def _outputs(path: Path) -> dict[str, str]:
    """The ``name<<delimiter`` blocks of a ``GITHUB_OUTPUT`` file, read as the runner does."""
    lines = path.read_text(encoding="utf-8").split("\n")
    values: dict[str, str] = {}
    index = 0
    while index < len(lines) - 1:
        name, delimiter = lines[index].split("<<", 1)
        end = lines.index(delimiter, index + 1)
        values[name] = "\n".join(lines[index + 1 : end])
        index = end + 1
    return values


def _no_token(*texts: str) -> None:
    for text in texts:
        assert TOKEN not in text


# --- inputs -------------------------------------------------------------------------------


def test_the_inputs_become_options_in_their_order_and_the_contracts_come_last(
    tmp_path: Path,
) -> None:
    env = _environ(
        tmp_path,
        contracts="  contracts/a.yaml \n\n contracts/b dir \n",
        product=" decision://sales/weekly_margin ",
        dwh="clickhouse",
        **{"candidate-dbt-database": "", "created-at": CREATED_AT},
    )

    config = read_config(env)

    assert config.contracts == ("contracts/a.yaml", "contracts/b dir")
    assert config.flags == (
        ("--base", "base"),
        ("--candidate", "candidate"),
        ("--product", "decision://sales/weekly_margin"),
        ("--dwh", "clickhouse"),
        ("--created-at", CREATED_AT),
    )
    assert verify_argv(config, "ev", Path("c.md")) == [
        "decision",
        "verify",
        "--base=base",
        "--candidate=candidate",
        "--product=decision://sales/weekly_margin",
        "--dwh=clickhouse",
        f"--created-at={CREATED_AT}",
        "--evidence=ev",
        f"--pr-comment={Path('c.md')}",
        "--json",
        "contracts/a.yaml",
        "contracts/b dir",
    ]
    assert (config.comment, config.comment_author, config.fail_on) == (
        True,
        DEFAULTS["comment-author"],
        "block",
    )


def test_every_option_the_action_passes_is_one_verify_takes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_bi import cli

    seen: list[Any] = []
    monkeypatch.setattr(cli, "_decision_verify", lambda args: seen.append(args) or 0)
    given = {name: "clickhouse" if name == "dwh" else f"v-{name}" for name, _ in FLAG_INPUTS}
    config = read_config(_environ(tmp_path, **given))
    assert len(config.flags) == len(FLAG_INPUTS)

    assert cli.main(verify_argv(config, "ev", tmp_path / "c.md")) == 0

    args = seen[0]
    for name, flag in FLAG_INPUTS:
        assert getattr(args, flag.removeprefix("--").replace("-", "_")) == given[name]
    assert (args.paths, args.evidence, args.json) == (["contracts"], "ev", True)
    assert args.pr_comment == str(tmp_path / "c.md")


@pytest.mark.parametrize(
    ("inputs", "said"),
    [
        ({"contracts": " \n \n"}, "contracts: no contract path given"),
        ({"base": " "}, "base: no semantic ref given"),
        ({"candidate": ""}, "candidate: no semantic ref given"),
        ({"contracts": "a.yaml\n-b.yaml"}, "contracts: '-b.yaml' begins with '-'"),
        ({"comment": "yes"}, "comment: 'yes' is neither true nor false"),
        ({"fail-on": "always"}, "fail-on: 'always' is not one of block, review, never"),
    ],
)
def test_an_input_the_action_cannot_run_with_stops_it_before_the_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], inputs: dict[str, str], said: str
) -> None:
    env = _environ(tmp_path, **inputs)
    runner, transport = Runner(), Transport()

    with pytest.raises(ConfigError, match=re.escape(said)):
        read_config(env)
    assert main(env, transport=transport, runner=runner) == 2

    out = capsys.readouterr().out
    assert out.startswith(f"::error::auto_bi decision gate: {said}") and out.count("\n") == 1
    assert runner.calls == [] and transport.requests == []
    assert not (tmp_path / "summary.md").exists() and not (tmp_path / "output.txt").exists()


@pytest.mark.parametrize(
    ("event", "step"), [("pull_request_target", 2), ("pull_request", 4), ("pull_request_review", 4)]
)
def test_a_pull_request_target_run_is_refused_before_the_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], event: str, step: int
) -> None:
    runner, transport = Runner(), Transport()
    env = {**_environ(tmp_path), "GITHUB_EVENT_NAME": event}

    assert main(env, transport=transport, runner=runner) == step

    out = capsys.readouterr().out
    refused = (
        "::error::auto_bi decision gate: not run for pull_request_target, which hands the base "
        "repository's write token and secrets to the pull request's files; run it on "
        "pull_request\n"
    )
    if step == 2:
        assert out == refused
        assert runner.calls == [] and transport.requests == []
        assert not (tmp_path / "summary.md").exists() and not (tmp_path / "output.txt").exists()
    else:
        assert "::error::" not in out and len(runner.calls) == 1 and transport.requests


def test_comment_reads_true_and_false_in_any_case(tmp_path: Path) -> None:
    assert read_config(_environ(tmp_path, comment="FALSE")).comment is False
    assert read_config(_environ(tmp_path, comment=" True ")).comment is True


def test_the_gate_inherits_the_job_less_the_inputs_and_the_tokens() -> None:
    env = child_env(
        {
            "PATH": "/bin",
            "AUTO_BI_CLICKHOUSE_HOST": "warehouse",
            "DECISION_GATE_GITHUB_TOKEN": TOKEN,
            "decision_gate_base": "x",
            "GITHUB_TOKEN": TOKEN,
            "GH_TOKEN": TOKEN,
            "GH_ENTERPRISE_TOKEN": TOKEN,
            "GITHUB_ENTERPRISE_TOKEN": TOKEN,
            "ACTIONS_RUNTIME_TOKEN": TOKEN,
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": TOKEN,
            "GITHUB_REPOSITORY": "owner/repo",
        }
    )

    assert env == {
        "PATH": "/bin",
        "AUTO_BI_CLICKHOUSE_HOST": "warehouse",
        "GITHUB_REPOSITORY": "owner/repo",
    }


# --- the answer -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "fail_on", "step"),
    [
        (0, "block", 0),
        (0, "review", 0),
        (3, "block", 0),
        (3, "review", 3),
        (3, "never", 0),
        (4, "block", 4),
        (4, "review", 4),
        (4, "never", 0),
        (2, "never", 2),
        (1, "block", 2),
        (-9, "never", 2),
        (5, "review", 2),
    ],
)
def test_the_step_exits_by_the_gate_and_fail_on(code: int, fail_on: str, step: int) -> None:
    assert exit_for(code, fail_on) == step


def test_the_marker_is_a_hash_of_the_product_alone() -> None:
    marker = marker_for("decision://sales/<b>@x")

    assert marker == marker_for("decision://sales/<b>@x")
    assert marker != marker_for("decision://sales/weekly_margin")
    assert marker.startswith("<!-- auto-bi-decision-gate:") and marker.endswith(" -->")
    assert len(marker) == len("<!-- auto-bi-decision-gate: -->") + 16
    assert "sales" not in marker


def test_the_run_is_named_by_the_head_commit_and_a_link_built_from_checked_parts(
    tmp_path: Path,
) -> None:
    context = run_context(_environ(tmp_path))

    assert context == RunContext(
        number=7,
        commit=HEAD,
        run_url="https://github.com/owner/repo/actions/runs/123",
        api=API,
        repository="owner/repo",
    )
    assert comment_body("<!-- m -->", "core\n\n", context) == (
        f"<!-- m -->\ncore\n\nCommit `{HEAD}` · "
        "[workflow run](https://github.com/owner/repo/actions/runs/123)\n"
    )


@pytest.mark.parametrize(
    ("event", "changed", "number", "commit", "run_url"),
    [
        ({"pull_request": {"number": 7, "head": {"sha": "not-a-sha"}}}, {}, 7, BASE_SHA, True),
        ({"pull_request": {"number": 7}}, {"GITHUB_SHA": "x"}, 7, "", True),
        ({"pull_request": {"number": True}}, {}, None, BASE_SHA, True),
        ({"pull_request": {"number": "7"}}, {}, None, BASE_SHA, True),
        ({"pull_request": {"number": 0}}, {}, None, BASE_SHA, True),
        ({"push": {}}, {}, None, BASE_SHA, True),
        ([], {}, None, BASE_SHA, True),
        (
            {"pull_request": {"number": 7}},
            {"GITHUB_SERVER_URL": "javascript:x"},
            7,
            BASE_SHA,
            False,
        ),
        ({"pull_request": {"number": 7}}, {"GITHUB_REPOSITORY": "a/b) x"}, 7, BASE_SHA, False),
        ({"pull_request": {"number": 7}}, {"GITHUB_RUN_ID": "1 2"}, 7, BASE_SHA, False),
    ],
)
def test_a_value_not_in_the_form_github_gives_it_is_left_out(
    tmp_path: Path,
    event: Any,
    changed: dict[str, str],
    number: int | None,
    commit: str,
    run_url: bool,
) -> None:
    context = run_context({**_environ(tmp_path, event=event), **changed})

    assert (context.number, context.commit, bool(context.run_url)) == (number, commit, run_url)


def test_an_event_file_that_cannot_be_read_is_no_pull_request(tmp_path: Path) -> None:
    env = _environ(tmp_path)
    Path(env["GITHUB_EVENT_PATH"]).write_bytes(b"{not json")

    assert run_context(env).number is None
    assert run_context({**env, "GITHUB_EVENT_PATH": str(tmp_path / "missing")}).number is None


# --- one run of the action ------------------------------------------------------------------


def test_the_action_posts_the_gate_comment_and_writes_the_summary_and_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = _environ(tmp_path, product=PRODUCT)
    runner, transport = Runner(), Transport()

    assert main(env, transport=transport, runner=runner) == 4

    marker = marker_for(PRODUCT)
    body = comment_body(marker, COMMENT, run_context(env))
    assert body.startswith(f"{marker}\n{COMMENT}\nCommit `{HEAD}`")
    assert [(method, url) for method, url, _, _ in transport.requests] == [
        ("GET", f"{API}/repos/owner/repo/issues/7/comments?per_page=100&page=1"),
        ("POST", f"{API}/repos/owner/repo/issues/7/comments"),
    ]
    assert transport.requests[1][2] == {"body": body}
    assert transport.requests[0][3] == {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": "auto-bi-decision-gate",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == body
    outputs = _outputs(tmp_path / "output.txt")
    assert list(outputs) == list(OUTPUTS)
    assert outputs["verdict"] == "block" and outputs["exit-code"] == "4"
    assert outputs["run-id"] == "run-1" and outputs["evidence-digest"] == DIGEST
    workdir = Path(outputs["report"]).parent
    assert workdir.parent == tmp_path / "runner"
    assert outputs["evidence"] == str(workdir / "evidence")
    assert Path(outputs["report"]).read_text(encoding="utf-8") == body
    argv, gate_env = runner.calls[0]
    assert f"--evidence={workdir / 'evidence'}" in argv
    assert f"--pr-comment={workdir / 'comment.md'}" in argv
    assert gate_env["AUTO_BI_CLICKHOUSE_HOST"] == "warehouse"
    out = capsys.readouterr().out
    assert f"verdict: block (exit 4), run run-1, evidence {DIGEST}\n" in out
    assert "pull request comment added\n" in out
    _no_token(out, json.dumps(gate_env), (tmp_path / "output.txt").read_text(encoding="utf-8"))


def test_the_comment_the_action_wrote_before_is_updated_and_no_other(tmp_path: Path) -> None:
    env = _environ(tmp_path, product=PRODUCT)
    marker = marker_for(PRODUCT)
    bot = DEFAULTS["comment-author"]
    pages = [
        [
            {"id": 1, "body": f"{marker}\nold", "user": {"login": "someone"}},
            {"id": 2, "body": f"quoting\n{marker}", "user": {"login": bot}},
            {"id": 3, "body": f"{marker_for('other')}\nold", "user": {"login": bot}},
            {"id": True, "body": f"{marker}\nold", "user": {"login": bot}},
            "not a comment",
        ]
        + [{"id": 100 + index, "body": "", "user": None} for index in range(95)],
        [{"id": 42, "body": f"{marker}\r\nold", "user": {"login": bot}}],
        [{"id": 43, "body": f"{marker}\nlater", "user": {"login": bot}}],
    ]
    transport = Transport(pages)

    assert main(env, transport=transport, runner=Runner()) == 4

    assert [(method, url.removeprefix(API)) for method, url, _, _ in transport.requests] == [
        ("GET", "/repos/owner/repo/issues/7/comments?per_page=100&page=1"),
        ("GET", "/repos/owner/repo/issues/7/comments?per_page=100&page=2"),
        ("PATCH", "/repos/owner/repo/issues/comments/42"),
    ]


def test_another_author_named_by_the_input_is_the_one_matched(tmp_path: Path) -> None:
    marker = marker_for("")
    pages = [[{"id": 5, "body": f"{marker}\nold", "user": {"login": "release-bot"}}]]
    transport = Transport(pages)

    main(
        _environ(tmp_path, **{"comment-author": "release-bot"}),
        transport=transport,
        runner=Runner(),
    )

    assert transport.requests[-1][:2] == ("PATCH", f"{API}/repos/owner/repo/issues/comments/5")


def test_a_comment_whose_first_line_only_begins_with_the_marker_is_not_the_action_s(
    tmp_path: Path,
) -> None:
    marker = marker_for("")
    bot = DEFAULTS["comment-author"]
    pages = [
        [
            {"id": 8, "body": f"{marker} edited by hand\nold", "user": {"login": bot}},
            {"id": 9, "body": f"{marker}\nold", "user": {"login": bot}},
        ]
    ]
    transport = Transport(pages)

    main(_environ(tmp_path), transport=transport, runner=Runner())

    assert transport.requests[-1][:2] == ("PATCH", f"{API}/repos/owner/repo/issues/comments/9")


def test_the_comments_are_paged_through_a_hundred_pages_at_most(tmp_path: Path) -> None:
    full = [{"id": index, "body": "", "user": {"login": "x"}} for index in range(100)]
    transport = Transport([full] * 101)

    main(_environ(tmp_path), transport=transport, runner=Runner())

    methods = [method for method, _, _, _ in transport.requests]
    assert methods == ["GET"] * 100 + ["POST"]


def test_a_refusal_by_github_is_a_warning_and_the_step_keeps_the_gate_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = _environ(tmp_path, **{"fail-on": "never"})

    assert main(env, transport=Transport(status=403), runner=Runner()) == 0

    out = capsys.readouterr().out
    assert (
        "::warning::the pull request comment was not posted: GitHub answered 403 to GET "
        "/issues/7/comments: Resource not accessible by integration\n" in out
    )
    assert (tmp_path / "summary.md").exists()
    _no_token(out)


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        ((500, b"<html>"), "GitHub answered 500 to GET /issues/7/comments: no message"),
        ((200, b"{}"), "GitHub listed the comments as something other than a list"),
        ((200, b"<html>"), "GitHub answered GET /issues/7/comments with no JSON"),
        ((404, b'{"message": "100%0A::error::x"}'), "404 to GET /issues/7/comments: 100%250A"),
    ],
)
def test_an_answer_github_should_not_give_is_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], answer: tuple[int, bytes], said: str
) -> None:
    def transport(
        method: str, url: str, body: bytes | None, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        return answer

    assert main(_environ(tmp_path), transport=transport, runner=Runner(code=3)) == 0

    out = capsys.readouterr().out
    warning = next(line for line in out.splitlines() if line.startswith("::warning::"))
    assert said in warning


@pytest.mark.parametrize("status", [301, 302, 304, 307])
def test_a_redirect_github_answers_is_a_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], status: int
) -> None:
    transport = Transport(status=status)

    assert main(_environ(tmp_path), transport=transport, runner=Runner(code=3)) == 0

    out = capsys.readouterr().out
    assert (
        f"::warning::the pull request comment was not posted: GitHub answered {status} to GET "
        "/issues/7/comments: " in out
    )
    assert [method for method, _, _, _ in transport.requests] == ["GET"]


def test_a_long_message_of_github_is_cut(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def transport(
        method: str, url: str, body: bytes | None, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        return 422, json.dumps({"message": "m" * 300 + "z" * 50}).encode()

    main(_environ(tmp_path), transport=transport, runner=Runner())

    out = capsys.readouterr().out
    warning = next(line for line in out.splitlines() if line.startswith("::warning::"))
    assert warning.endswith("/issues/7/comments: " + "m" * 300)


def test_a_message_of_github_is_set_on_one_line_before_it_is_cut(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def transport(
        method: str, url: str, body: bytes | None, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        return 422, json.dumps({"message": "\n" * 400}).encode()

    main(_environ(tmp_path), transport=transport, runner=Runner())

    out = capsys.readouterr().out
    warning = next(line for line in out.splitlines() if line.startswith("::warning::"))
    assert warning.endswith("/issues/7/comments: " + "\\n" * 150)


def test_a_workflow_command_keeps_to_its_line_whatever_its_message_holds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _command("warning", "a\nb\rc%d")

    assert capsys.readouterr().out == "::warning::a\\nb\\rc%25d\n"


def _window(out: str) -> tuple[str, str, str]:
    """The log before a ``stop-commands`` window, the text in it, and the log after it."""
    found = re.search(
        r"^::stop-commands::([0-9a-f]{32})\n(.*?)^::\1::\n", out, re.DOTALL | re.MULTILINE
    )
    assert found, out
    return out[: found.start()], found[2], out[found.end() :]


def test_what_the_gate_writes_to_stderr_is_logged_where_the_runner_reads_no_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Commands for the runner, one after a lone CR, a stop of its own, a byte that is not
    # UTF-8, and no line break at the end.
    stderr = (
        b"::warning title=Auto_BI release gate::pass\n"
        b"##[add-mask]block\n"
        b"::stop-commands::its-own\n"
        b"::its-own::\n"
        b"a line\r::error::after a carriage return \xff"
    )
    runner = Runner(stderr=stderr)

    assert main(_environ(tmp_path, comment="false"), transport=Transport(), runner=runner) == 4

    before, inside, after = _window(capsys.readouterr().out)
    assert before == ""
    assert inside == (
        "::warning title=Auto_BI release gate::pass\n"
        "##[add-mask]block\n"
        "::stop-commands::its-own\n"
        "::its-own::\n"
        "a line\r::error::after a carriage return " + chr(0xFFFD) + "\n"
    )
    assert after == f"verdict: block (exit 4), run run-1, evidence {DIGEST}\n"


def test_a_gate_that_wrote_nothing_to_stderr_stops_no_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(_environ(tmp_path, comment="false"), transport=Transport(), runner=Runner())

    assert capsys.readouterr().out == f"verdict: block (exit 4), run run-1, evidence {DIGEST}\n"


def test_a_token_the_gate_s_text_holds_is_drawn_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn = iter(range(1, 100))
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=next(drawn)))
    first, second, third = (uuid.UUID(int=n).hex for n in (1, 2, 3))
    stderr = f"::{first}::\n::warning::after the first token\n::{second}::\n"

    main(
        _environ(tmp_path, comment="false"),
        transport=Transport(),
        runner=Runner(stderr=stderr.encode()),
    )

    out = capsys.readouterr().out
    assert out.startswith(f"::stop-commands::{third}\n")
    assert _window(out)[1] == stderr


@pytest.mark.parametrize(
    "api",
    [
        "http://api.example.com",
        "https://user:pw@api.example.com",
        "ftp://api.example.com",
        "x",
        "https://[::1",
    ],
)
def test_the_token_goes_only_to_an_https_api_or_one_on_this_machine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], api: str
) -> None:
    transport = Transport()

    main({**_environ(tmp_path), "GITHUB_API_URL": api}, transport=transport, runner=Runner())

    assert transport.requests == []
    assert "::warning::the pull request comment was not posted: GITHUB_API_URL" in (
        capsys.readouterr().out
    )
    for loopback in ("http://127.0.0.1:9", "http://localhost:9", "http://[::1]:9"):
        GitHub(loopback, "owner/repo", TOKEN, Transport())
    with pytest.raises(CommentError, match="GITHUB_REPOSITORY"):
        GitHub(API, "owner/repo/extra", TOKEN, Transport())


@pytest.mark.parametrize(
    ("changed", "said"),
    [
        ({"GITHUB_EVENT_PATH": ""}, "::notice::not a pull request event"),
        ({env_name("github-token"): ""}, "::warning::no github-token"),
        ({env_name("comment"): "false"}, ""),
    ],
)
def test_no_comment_is_posted_where_there_is_none_to_post_or_none_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], changed: dict[str, str], said: str
) -> None:
    transport = Transport()

    assert main({**_environ(tmp_path), **changed}, transport=transport, runner=Runner()) == 4

    assert transport.requests == []
    out = capsys.readouterr().out
    assert said in out
    assert (tmp_path / "summary.md").exists()


def test_a_gate_that_wrote_no_comment_gets_the_one_of_no_verdict_and_fails_the_step(
    tmp_path: Path,
) -> None:
    env = _environ(tmp_path, **{"fail-on": "never"})
    transport = Transport()

    assert main(env, transport=transport, runner=Runner(code=1, comment=None)) == 2

    core = render_pull_request_failure(
        "auto_bi decision verify exited with code 1 and wrote no comment"
    )
    body = comment_body(marker_for(""), core, run_context(env))
    assert transport.requests[-1][2] == {"body": body}
    outputs = _outputs(tmp_path / "output.txt")
    assert outputs["verdict"] == "" and outputs["exit-code"] == "1"
    assert outputs["run-id"] == "" and outputs["evidence-digest"] == ""


def test_a_gate_that_exits_two_is_read_for_no_verdict_whatever_it_printed(tmp_path: Path) -> None:
    failure = render_pull_request_failure("the base has no model.yaml")
    runner = Runner(code=2, comment=failure, stdout=_report("pass"))

    assert main(_environ(tmp_path), transport=Transport(), runner=runner) == 2

    outputs = _outputs(tmp_path / "output.txt")
    assert outputs["verdict"] == "" and outputs["exit-code"] == "2"
    assert failure in (tmp_path / "summary.md").read_text(encoding="utf-8")


def test_a_named_evidence_directory_is_the_one_the_gate_writes(tmp_path: Path) -> None:
    runner = Runner()

    main(_environ(tmp_path, evidence="out/evidence"), transport=Transport(), runner=runner)

    assert "--evidence=out/evidence" in runner.calls[0][0]
    assert _outputs(tmp_path / "output.txt")["evidence"] == "out/evidence"


def test_an_output_value_of_many_lines_ends_where_its_delimiter_does(tmp_path: Path) -> None:
    stdout = json.dumps({"verdict": "block\nexit-code<<x", "run_id": 7}).encode()

    main(_environ(tmp_path), transport=Transport(), runner=Runner(stdout=stdout))

    outputs = _outputs(tmp_path / "output.txt")
    assert outputs["verdict"] == "block\nexit-code<<x"
    assert outputs["run-id"] == "" and outputs["exit-code"] == "4"


# --- the transport --------------------------------------------------------------------------


class _Server(http.server.ThreadingHTTPServer):
    requests: list[tuple[str, str, str, bytes]]
    comments: list[dict[str, Any]]


class _Handler(http.server.BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _record(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.requests.append(
            (self.command, self.path, self.headers.get("Authorization", ""), body)
        )
        return body

    def _answer(self, status: int, document: Any, **headers: str) -> None:
        data = json.dumps(document).encode()
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._record()
        if self.path.startswith("/redirect"):
            port = self.server.server_address[1]
            self._answer(302, {}, Location=f"http://127.0.0.1:{port}/elsewhere")
        elif self.path.startswith("/repos/owner/repo/issues/7/comments"):
            self._answer(200, self.server.comments)
        else:
            self._answer(404, {"message": "Not Found"})

    def do_POST(self) -> None:
        body = json.loads(self._record())
        comment = {"id": len(self.server.comments) + 1, "body": body["body"]}
        comment["user"] = {"login": DEFAULTS["comment-author"]}
        self.server.comments.append(comment)
        self._answer(201, comment)

    def do_PATCH(self) -> None:
        body = json.loads(self._record())
        ident = int(self.path.rsplit("/", 1)[1])
        for comment in self.server.comments:
            if comment["id"] == ident:
                comment["body"] = body["body"]
        self._answer(200, {"id": ident})


@pytest.fixture
def server() -> Iterator[_Server]:
    httpd = _Server(("127.0.0.1", 0), _Handler)
    httpd.requests, httpd.comments = [], []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def test_a_redirect_is_answered_as_it_is_and_never_followed(server: _Server) -> None:
    port = server.server_address[1]

    status, _ = urllib_transport(
        "GET", f"http://127.0.0.1:{port}/redirect", None, {"Authorization": f"Bearer {TOKEN}"}
    )

    assert status == 302
    assert [path for _, path, _, _ in server.requests] == ["/redirect"]


def test_a_server_that_cannot_be_reached_is_a_comment_error_without_the_token() -> None:
    with pytest.raises(CommentError) as raised:
        urllib_transport("GET", "http://127.0.0.1:9/x", None, {"Authorization": TOKEN})

    assert str(raised.value) == "GET could not be sent: URLError"


def _pilot(**changed: str) -> dict[str, str]:
    """The inputs of pilot 2026-08-25 with the chart overlay: a run that blocks."""
    return {
        "contracts": str(SOURCE / "contracts"),
        "base": str(SOURCE / "base"),
        "candidate": str(SOURCE / "candidate"),
        "dashboards": str(OVERLAY / "dashboards"),
        "assets": str(SOURCE / "assets"),
        "results": str(SOURCE / "results.yaml"),
        "product": PRODUCT,
        "environment": "synthetic-offline",
        "created-at": CREATED_AT,
        **changed,
    }


def _decoy(root: Path) -> Path:
    """A checkout whose `auto_bi` package, imported in place of the action's, exits 97."""
    (root / "auto_bi").mkdir(parents=True)
    (root / "auto_bi" / "__init__.py").write_text("import sys\nsys.exit(97)\n", encoding="utf-8")
    return root


def test_the_action_runs_the_gate_and_keeps_one_comment_per_product_across_runs(
    tmp_path: Path, server: _Server, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real `decision verify` in a child process, and GitHub's API on this machine."""
    env = _environ(tmp_path, **_pilot())
    env["GITHUB_API_URL"] = f"http://127.0.0.1:{server.server_address[1]}"

    assert main(env) == 4
    first = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert main(env) == 4

    assert [(method, path) for method, path, _, _ in server.requests] == [
        ("GET", "/repos/owner/repo/issues/7/comments?per_page=100&page=1"),
        ("POST", "/repos/owner/repo/issues/7/comments"),
        ("GET", "/repos/owner/repo/issues/7/comments?per_page=100&page=1"),
        ("PATCH", "/repos/owner/repo/issues/comments/1"),
    ]
    assert {authorization for _, _, authorization, _ in server.requests} == {f"Bearer {TOKEN}"}
    assert [comment["body"] for comment in server.comments] == [first]
    assert first.startswith(f"{marker_for(PRODUCT)}\n### Auto_BI release gate: block\n")
    assert "`dashboard://weekly_margin@7#margin_by_region` (computes_metric)" in first
    outputs = _outputs(tmp_path / "output.txt")
    assert outputs["verdict"] == "block" and outputs["evidence-digest"] in first
    assert Path(outputs["evidence"]).is_dir()
    out = capsys.readouterr().out
    assert out.count("pull request comment added\n") == 1
    assert out.count("pull request comment updated\n") == 1
    _no_token(out)


def test_the_gate_s_stderr_reaches_the_log_as_text_and_the_checkout_is_not_imported(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real gate, on a chart whose SQL is written into a parser warning, from a checkout
    that is the working directory and on ``PYTHONPATH``."""
    dashboard = yaml.safe_load(
        (OVERLAY / "dashboards" / "weekly_margin@7.yaml").read_text(encoding="utf-8")
    )
    charts = [chart for chart in dashboard["charts"] if chart["id"] == "operator_table"]
    assert len(charts) == 1
    charts[0]["query"]["raw_sql"] = (
        "SHOW TABLES\n"
        "::warning title=Auto_BI release gate::pass, written by the chart SQL\n"
        "::add-mask::block"
    )
    (tmp_path / "dashboards").mkdir()
    (tmp_path / "dashboards" / "weekly_margin@7.yaml").write_text(
        yaml.safe_dump(dashboard, allow_unicode=True), encoding="utf-8"
    )
    checkout = _decoy(tmp_path / "checkout")
    env = _environ(tmp_path, comment="false", **_pilot(dashboards=str(tmp_path / "dashboards")))
    env["PYTHONPATH"] = str(checkout)
    monkeypatch.chdir(checkout)

    assert main(env) == 4

    out, err = capfd.readouterr()
    before, inside, after = _window(out)
    assert "\n::warning title=Auto_BI release gate::pass, written by the chart SQL\n" in inside
    assert "\n::add-mask::block" in inside
    assert before == "" and err == ""
    assert after.startswith("verdict: block (exit 4), run ") and after.count("\n") == 1


def test_the_module_runs_as_the_action_runs_it(tmp_path: Path) -> None:
    """With the options `action.yml` gives Python, from a checkout that is the working
    directory and on ``PYTHONPATH``: the gate it runs says itself why there is no verdict."""
    import subprocess
    import sys

    checkout = _decoy(tmp_path / "checkout")
    env = _environ(tmp_path, comment="false")
    env[env_name("base")] = str(tmp_path / "missing")
    env["PYTHONPATH"] = str(checkout)

    completed = subprocess.run(
        [sys.executable, *PYTHON_FLAGS, "-m", "auto_bi.github_action"],
        cwd=checkout,
        env=env,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    summary = (tmp_path / "summary.md").read_bytes()
    assert b"### Auto_BI release gate: no verdict" in summary
    assert b"exited with code" not in summary
    assert TOKEN.encode() not in completed.stdout + completed.stderr


def test_the_gate_starts_isolated_with_its_streams_in_utf_8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As `action.yml` starts the action: ``-I`` keeps the checkout and ``PYTHONPATH`` off the
    gate's path, and ``-X utf8`` makes its streams UTF-8 whatever the runner's locale is —
    ``-I`` drops ``PYTHONIOENCODING``, so nothing else would."""
    import subprocess
    import sys

    started: list[tuple[list[str], dict[str, Any]]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        started.append((args, kwargs))
        return subprocess.CompletedProcess(args, 3, b"out", b"err")

    monkeypatch.setattr(subprocess, "run", run)

    assert _run(["decision", "verify"], {"HOME": "/home/runner"}) == (3, b"out", b"err")
    assert started == [
        (
            [sys.executable, "-I", "-X", "utf8", "-m", "auto_bi.cli", "decision", "verify"],
            {
                "env": {"HOME": "/home/runner"},
                "stdin": subprocess.DEVNULL,
                "capture_output": True,
                "check": False,
            },
        )
    ]
