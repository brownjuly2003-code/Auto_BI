"""The tools of the DecisionOps MCP server answer what the `decision` commands print (ADR 0012, D5).

Each tool is called through `Server.handle_line`, as a client calls it, and its answer is held
against the `--json` output of the command it stands for — or, for `impact`, against the
payload of the gate's `impact.products` check.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

import pytest

from auto_bi.cli import main
from auto_bi.decision.use_cases import VerifyError
from auto_bi.evidence.signing import load_ed25519_signer, load_ed25519_trust_store
from auto_bi.mcp import tools as mcp_tools
from auto_bi.mcp.server import Server
from auto_bi.mcp.tools import ToolBox
from auto_bi.verification.model import EXIT_ERROR
from tests.test_decision_cli import _run_dir, _verify_argv, _written_bundle
from tests.test_decision_validate import write_contracts
from tests.test_decision_verify import (
    CREATED_AT,
    PRODUCT_ID,
    Case,
    _check_payload,
    _formula_change,
    _removed_field,
    _write_yaml,
    dashboard_spec,
    metric_contract,
    product_contract,
)
from tests.test_evidence_signing import _key_files
from tests.test_mcp_server import META

REPOSITORY = Path(__file__).resolve().parents[1]
OTHER_PRODUCT = "decision://sales/margin_alert"
BROKEN_CONTRACT = "kind: MetricContract\nmetadata: {id: metric://sales/x}\n"


def _call(box: ToolBox, name: str, **arguments: Any) -> tuple[dict[str, Any], bool]:
    """Call *name* as a client does; the structured answer and whether it is an error."""
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"_meta": META, "name": name, "arguments": arguments},
    }
    raw = Server(box).handle_line(json.dumps(message).encode())
    assert raw is not None
    result = json.loads(raw)["result"]
    structured: dict[str, Any] = result["structuredContent"]
    assert json.loads(result["content"][0]["text"]) == structured
    return structured, result["isError"]


def _refs(case: Case) -> dict[str, Any]:
    return {
        "paths": [str(case.contracts)],
        "base": str(case.base),
        "candidate": str(case.candidate),
        "dashboards": str(case.dashboards),
    }


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


# --- validate ------------------------------------------------------------------


@pytest.mark.parametrize(
    "repoint",
    [None, ["metric://sales/net_margin_pct@1"]],
    ids=["pass", "block"],
)
def test_validate_answers_the_report_the_command_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], repoint: list[str] | None
) -> None:
    def edit(data: dict[str, Any]) -> None:
        if repoint is not None:
            data["scope"]["metrics"] = repoint

    contracts = write_contracts(tmp_path, product=edit)
    main(["decision", "validate", str(contracts), "--json"])
    printed = json.loads(capsys.readouterr().out)

    answer, is_error = _call(ToolBox(), "validate", paths=[str(contracts)])

    assert answer == printed
    assert is_error is False  # a block is an answer, not an error
    assert answer["verdict"] == ("pass" if repoint is None else "block")


def test_validate_of_no_product_is_an_error_that_still_carries_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_yaml(tmp_path / "gross_margin_pct.yaml", metric_contract())
    assert main(["decision", "validate", str(tmp_path), "--json"]) == EXIT_ERROR
    printed = json.loads(capsys.readouterr().out)

    answer, is_error = _call(ToolBox(), "validate", paths=[str(tmp_path)])

    assert is_error is True
    assert answer == {
        "error": "no decision product contract found in the given paths",
        "report": printed,
    }


# --- verify --------------------------------------------------------------------


@pytest.mark.parametrize("contract", [None, _formula_change()], ids=["unchanged", "formula"])
def test_verify_answers_the_report_and_seals_the_bundle_the_command_does(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], contract: dict[str, Any] | None
) -> None:
    case = Case(tmp_path).with_candidate(contract=contract)
    code = main(_verify_argv(case, "--json"))
    printed = json.loads(capsys.readouterr().out)
    evidence = tmp_path / "evidence"
    sealed = _tree(evidence)
    evidence.rename(tmp_path / "evidence-of-the-command")

    answer, is_error = _call(
        ToolBox(evidence_dir=evidence), "verify", **_refs(case), created_at=CREATED_AT
    )

    assert answer == printed
    assert is_error is False  # a block is an answer, not an error
    assert (code, answer["verdict"]) == (
        (0, "pass_with_advisory") if contract is None else (4, "block")
    )
    assert _tree(evidence) == sealed


def test_verify_without_a_timestamp_takes_the_servers_clock(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    box = ToolBox(evidence_dir=tmp_path / "evidence", clock=lambda: "2026-09-01T10:00:00Z")

    report, _ = _call(box, "verify", **_refs(case))
    inspection, _ = _call(box, "evidence", path=str(tmp_path / "evidence"))

    assert inspection["run_id"] == report["run_id"]
    assert inspection["bundle"]["created_at"] == "2026-09-01T10:00:00Z"


def test_the_servers_clock_is_utc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Clock:
        """The clock of a machine five hours east of UTC."""

        @staticmethod
        def now(tz: tzinfo | None = None) -> datetime:
            moment = datetime(2031, 2, 3, 4, 5, 6, tzinfo=UTC)
            local = moment.astimezone(timezone(timedelta(hours=5)))
            return moment.astimezone(tz) if tz is not None else local.replace(tzinfo=None)

    monkeypatch.setattr(mcp_tools, "datetime", Clock)
    case = Case(tmp_path).with_candidate()
    box = ToolBox(evidence_dir=tmp_path / "evidence")

    _call(box, "verify", **_refs(case))
    inspection, _ = _call(box, "evidence", path=str(tmp_path / "evidence"))

    assert inspection["bundle"]["created_at"] == "2031-02-03T04:05:06Z"


def test_a_timestamp_the_call_gives_is_the_bundles(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    box = ToolBox(evidence_dir=tmp_path / "evidence", clock=lambda: "2026-09-01T10:00:00Z")

    _call(box, "verify", **_refs(case), created_at=CREATED_AT)
    inspection, _ = _call(box, "evidence", path=str(tmp_path / "evidence"))

    assert inspection["bundle"]["created_at"] == CREATED_AT


def test_verify_again_is_the_same_run_and_other_evidence_under_its_id_is_refused(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path).with_candidate()
    box = ToolBox(evidence_dir=tmp_path / "evidence")
    first, _ = _call(box, "verify", **_refs(case), created_at=CREATED_AT)
    sealed = _tree(tmp_path / "evidence")

    assert _call(box, "verify", **_refs(case), created_at=CREATED_AT) == (first, False)
    later, is_error = _call(box, "verify", **_refs(case), created_at="2026-09-01T10:00:00Z")

    assert is_error is True
    assert "already holds bundle" in later["error"]
    assert _tree(tmp_path / "evidence") == sealed


def test_verify_says_why_it_reached_no_verdict_as_the_command_does(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = Case(tmp_path).with_candidate()
    (case.contracts / "weekly_margin.yaml").unlink()
    assert main(_verify_argv(case)) == EXIT_ERROR
    said = capsys.readouterr().err
    evidence = tmp_path / "evidence-of-the-tool"

    answer, is_error = _call(
        ToolBox(evidence_dir=evidence), "verify", **_refs(case), created_at=CREATED_AT
    )

    assert is_error is True
    assert said == f"error: {answer['error']}\n"
    assert "no decision product" in answer["error"]
    assert not evidence.exists()


def test_verify_of_several_products_asks_which_one(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    other = product_contract()
    other["metadata"]["id"] = OTHER_PRODUCT
    _write_yaml(case.contracts / "margin_alert.yaml", other)
    box = ToolBox(evidence_dir=tmp_path / "evidence")

    answer, is_error = _call(box, "verify", **_refs(case), created_at=CREATED_AT)
    assert is_error is True and "2 decision products" in answer["error"]

    report, is_error = _call(
        box, "verify", **_refs(case), created_at=CREATED_AT, product=OTHER_PRODUCT
    )
    assert is_error is False and report["product_ref"] == f"{OTHER_PRODUCT}@1"


@pytest.mark.parametrize("tool", ["impact", "verify"])
@pytest.mark.parametrize("side", ["base", "candidate"])
def test_a_dbt_database_without_its_manifest_is_refused(
    tmp_path: Path, tool: str, side: str
) -> None:
    case = Case(tmp_path).with_candidate()
    evidence = tmp_path / "evidence"
    arguments = {**_refs(case), f"{side}_dbt_database": "analytics"}

    answer, is_error = _call(ToolBox(evidence_dir=evidence), tool, **arguments)

    assert is_error is True
    assert answer == {"error": f"{side}_dbt_database is given without {side}_semantic_manifest"}
    assert not evidence.exists()


def test_a_server_with_keys_seals_its_bundles_and_trusts_them(tmp_path: Path) -> None:
    private_path, public_path, _ = _key_files(tmp_path, "trusted")
    case = Case(tmp_path / "case").with_candidate()
    evidence = case.root / "evidence"
    sealing = ToolBox(evidence_dir=evidence, signer=load_ed25519_signer(private_path))

    _, is_error = _call(sealing, "verify", **_refs(case), created_at=CREATED_AT)
    assert is_error is False

    untrusted, is_error = _call(ToolBox(), "evidence", path=str(evidence))
    assert is_error is False
    assert untrusted["signature_verified"] is False
    assert [problem["code"] for problem in untrusted["problems"]] == [
        "evidence.signature_untrusted"
    ]

    trusting = ToolBox(verifier=load_ed25519_trust_store([public_path]))
    trusted, _ = _call(trusting, "evidence", path=str(evidence))
    assert trusted["verdict"] == "pass"
    assert trusted["signature_verified"] is True
    assert trusted["bundle"]["integrity"] == "signed"

    pinned = {"artifact": "check-semantic.diff", "expect_digest": trusted["manifest_digest"]}
    refused, is_error = _call(ToolBox(), "evidence", path=str(evidence), **pinned)
    assert is_error is True and "trust root" in refused["error"]
    read, is_error = _call(trusting, "evidence", path=str(evidence), **pinned)
    assert is_error is False and json.loads(read["payload"])["changes"] == []


# --- evidence and runs ---------------------------------------------------------


def test_evidence_answers_the_inspection_the_command_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _written_bundle(tmp_path)
    capsys.readouterr()
    assert main(["decision", "evidence", str(evidence), "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    run = _run_dir(evidence)

    for arguments in (
        {"path": str(evidence)},
        {"path": str(run)},
        {"path": str(evidence), "run": run.name},
    ):
        assert _call(ToolBox(), "evidence", **arguments) == (printed, False), arguments


def test_evidence_answers_an_artifact_as_the_command_prints_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _written_bundle(tmp_path)
    capsys.readouterr()
    assert main(["decision", "evidence", str(evidence), "--artifact", "check-impact.products"]) == 0
    printed = capsys.readouterr().out

    answer, is_error = _call(
        ToolBox(), "evidence", path=str(evidence), artifact="check-impact.products"
    )

    assert is_error is False
    assert answer["payload"] + "\n" == printed
    assert answer["artifact"]["artifact_id"] == "check-impact.products"
    assert answer["artifact"]["digest"].startswith("sha256:")


def test_a_tampered_bundle_is_an_inspection_that_blocks_not_an_error(tmp_path: Path) -> None:
    evidence = _written_bundle(tmp_path)
    (_run_dir(evidence) / "checks" / "check-semantic.diff.json").write_bytes(b"{}")

    answer, is_error = _call(ToolBox(), "evidence", path=str(evidence))

    assert is_error is False
    assert answer["verdict"] == "block"
    assert "evidence.digest_mismatch" in [problem["code"] for problem in answer["problems"]]


def test_a_digest_the_bundle_is_not_blocks_its_inspection_and_refuses_its_artifact(
    tmp_path: Path,
) -> None:
    evidence = _written_bundle(tmp_path)
    stale = "sha256:" + "0" * 64

    inspection, is_error = _call(ToolBox(), "evidence", path=str(evidence), expect_digest=stale)
    assert is_error is False
    assert inspection["verdict"] == "block"
    assert "evidence.digest_unexpected" in [problem["code"] for problem in inspection["problems"]]

    refused, is_error = _call(
        ToolBox(),
        "evidence",
        path=str(evidence),
        artifact="check-semantic.diff",
        expect_digest=stale,
    )
    assert is_error is True
    assert "evidence.digest_unexpected" in refused["error"]
    assert set(refused) == {"error"}  # no payload of a bundle that is not the one pinned


def test_an_artifact_that_is_no_text_is_named_not_answered(tmp_path: Path) -> None:
    from tests.test_evidence_store import RUN, full_bundle

    builder = full_bundle(tmp_path)
    builder.add_blob(
        "screenshot",
        kind="raw_payload",
        payload=b"\x89PNG\r\n\x1a\n\xff\xfe",
        media_type="image/png",
    )
    builder.write()

    answer, is_error = _call(ToolBox(), "evidence", path=str(tmp_path / RUN), artifact="screenshot")

    assert is_error is True
    assert "image/png" in answer["error"] and "blobs/sha256/" in answer["error"]


@pytest.mark.parametrize(
    ("where", "arguments", "said"),
    [
        ("bundle", {"artifact": "check-nope"}, "check-semantic.diff"),
        ("bundle", {"run": "run-nope"}, "run-nope"),
        ("empty", {}, "no evidence bundle"),
        ("missing", {}, "missing"),
    ],
)
def test_evidence_says_what_it_could_not_find(
    tmp_path: Path, where: str, arguments: dict[str, str], said: str
) -> None:
    roots = {
        "bundle": lambda: _written_bundle(tmp_path),
        "empty": lambda: tmp_path,
        "missing": lambda: tmp_path / "missing",
    }

    answer, is_error = _call(ToolBox(), "evidence", path=str(roots[where]()), **arguments)

    assert is_error is True
    assert said in answer["error"]


def test_runs_answers_the_catalogue_the_command_prints(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _written_bundle(tmp_path)
    capsys.readouterr()
    assert main(["decision", "runs", str(evidence), "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)

    assert printed  # the catalogue lists the run the gate wrote
    assert _call(ToolBox(), "runs", path=str(evidence)) == ({"runs": printed}, False)


def test_runs_of_no_directory_is_an_error(tmp_path: Path) -> None:
    answer, is_error = _call(ToolBox(), "runs", path=str(tmp_path / "missing"))

    assert is_error is True
    assert "missing" in answer["error"]


# --- impact --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "contract"),
    [(None, None), (None, _formula_change()), (_removed_field(), None)],
    ids=["unchanged", "formula", "removed-field"],
)
@pytest.mark.parametrize("named", [True, False], ids=["named", "unnamed"])
def test_impact_of_a_product_is_what_the_gate_resolves_for_it(
    tmp_path: Path, model: Any, contract: dict[str, Any] | None, named: bool
) -> None:
    case = Case(tmp_path).with_candidate(model, contract)
    report = case.run()
    resolved = _check_payload(tmp_path, report, "impact.products")
    gaps = _check_payload(tmp_path, report, "semantic.gaps")
    product = {"product": PRODUCT_ID} if named else {}

    answer, is_error = _call(ToolBox(), "impact", **_refs(case), **product)

    assert is_error is False
    assert answer["impact"] == resolved
    assert answer["gaps"] == {"base": gaps["base"], "candidate": gaps["candidate"]}
    assert answer["validation"]["products"] == [f"{PRODUCT_ID}@1"]
    assert set(answer) == {"validation", "gaps", "impact"}  # no verdict of its own


def test_impact_without_a_product_reaches_every_product_of_the_paths(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate(contract=_formula_change())
    other = product_contract()
    other["metadata"]["id"] = OTHER_PRODUCT
    _write_yaml(case.contracts / "margin_alert.yaml", other)
    both = [f"{OTHER_PRODUCT}@1", f"{PRODUCT_ID}@1"]

    every, is_error = _call(ToolBox(), "impact", **_refs(case))
    assert is_error is False
    assert sorted(every["impact"]["products_in_scope"]) == both
    reached = {ref for item in every["impact"]["impacts"] for ref in item["products"]}
    assert reached == set(both)

    one, _ = _call(ToolBox(), "impact", **_refs(case), product=PRODUCT_ID)
    report = case.run(product_id=PRODUCT_ID)
    assert one["impact"] == _check_payload(tmp_path, report, "impact.products")
    assert one["impact"]["products_in_scope"] == [f"{PRODUCT_ID}@1"]


@pytest.mark.parametrize("product", ["decision://sales/nope", ""])
def test_impact_refuses_a_product_the_paths_do_not_hold_as_verify_does(
    tmp_path: Path, product: str
) -> None:
    case = Case(tmp_path).with_candidate()
    if not product:
        (case.contracts / "weekly_margin.yaml").unlink()

    answer, is_error = _call(ToolBox(), "impact", **_refs(case), product=product)

    with pytest.raises(VerifyError) as refused:
        case.run(product_id=product)
    assert is_error is True
    assert answer == {"error": str(refused.value)}


@pytest.mark.parametrize("side", ["base", "candidate"])
def test_impact_refuses_a_ref_the_gate_computes_no_diff_over(tmp_path: Path, side: str) -> None:
    case = Case(tmp_path).with_candidate()
    metrics = (case.base if side == "base" else case.candidate) / "metrics"
    (metrics / "broken.yaml").write_text(BROKEN_CONTRACT, encoding="utf-8")

    answer, is_error = _call(ToolBox(), "impact", **_refs(case))

    assert is_error is True
    assert answer["error"].startswith(
        "no semantic diff over a snapshot with 1 gap(s) the gate computes none over: "
        "gap.contract_invalid at "
    )
    assert "broken.yaml" in answer["error"] and "more)" not in answer["error"]


def test_impact_names_three_gaps_and_counts_the_rest(tmp_path: Path) -> None:
    case = Case(tmp_path).with_candidate()
    for index in range(4):
        (case.candidate / "metrics" / f"broken_{index}.yaml").write_text(
            BROKEN_CONTRACT, encoding="utf-8"
        )
    _write_yaml(case.base / "metrics" / "copy.yaml", metric_contract())

    answer, _ = _call(ToolBox(), "impact", **_refs(case))

    error = answer["error"]
    assert "with 5 gap(s)" in error
    assert error.count(" at ") == 3
    assert error.endswith(" (+2 more)")
    # the base side is named first
    assert 0 < error.index("gap.metric_duplicate at ") < error.index("gap.contract_invalid at ")


@pytest.mark.parametrize(("broken", "more"), [(2, ""), (3, ""), (4, " (+1 more)")])
def test_impact_counts_only_the_gaps_it_does_not_name(
    tmp_path: Path, broken: int, more: str
) -> None:
    case = Case(tmp_path).with_candidate()
    for index in range(broken):
        (case.candidate / "metrics" / f"broken_{index}.yaml").write_text(
            BROKEN_CONTRACT, encoding="utf-8"
        )

    answer, _ = _call(ToolBox(), "impact", **_refs(case))

    error = answer["error"]
    assert f"with {broken} gap(s)" in error
    assert error.count(" at ") == min(broken, 3)
    assert ("more)" in error) is bool(more)
    assert error.endswith(more)


def _ratio_tile(chart_id: str, numerator: str) -> dict[str, Any]:
    return {
        "id": chart_id,
        "title": chart_id,
        "viz": "bar",
        "query": {
            "table": "dm.sales_daily",
            "dimensions": ["region"],
            "measures": [
                {
                    "column": numerator,
                    "agg": "sum",
                    "denominator": {"column": "revenue", "agg": "sum"},
                }
            ],
        },
    }


@pytest.mark.parametrize("change", ["formula", "typed"])
def test_impact_finds_the_tile_of_a_definition_either_side_holds_as_the_gate_does(
    tmp_path: Path, change: str
) -> None:
    """The tile that computed the old formula moves with a new one; so does the tile of a
    formula the base held as a reference only.

    The metric is matched on the terms of both sides, base first: after a formula change only
    the base holds the terms of the tile; after a typed formula replaces an `external_ref`,
    only the candidate does.
    """
    case = Case(tmp_path).with_candidate(
        contract=_formula_change() if change == "formula" else None
    )
    if change == "typed":
        external = metric_contract()
        external["formula"] = {
            "kind": "external_ref",
            "external": {
                "source": "dbt",
                "ref": "metric.sales.gross_margin_pct",
                "source_revision": "3f1c2b9a4d5e6f708192a3b4c5d6e7f809a1b2c3",
            },
        }
        _write_yaml(case.base / "metrics" / "gross_margin_pct.yaml", external)
    spec = dashboard_spec()
    spec["charts"].append(_ratio_tile("margin", "gross_margin"))
    _write_yaml(case.dashboards / "weekly_margin@7.yaml", spec)
    report = case.run()

    answer, is_error = _call(ToolBox(), "impact", **_refs(case))

    assert is_error is False
    assert answer["impact"] == _check_payload(tmp_path, report, "impact.products")
    computed = {
        chart["chart"]
        for item in answer["impact"]["impacts"]
        for chart in item["charts"]
        if chart["relation"] == "computes_metric"
    }
    assert computed == {"margin"}


def test_impact_reads_and_writes_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path).with_candidate(contract=_formula_change())
    before = _tree(tmp_path)

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("an MCP tool must not reach an engine, a BI or an LLM")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for name in ("validate", "impact"):
        arguments = _refs(case) if name == "impact" else {"paths": [str(case.contracts)]}
        _, is_error = _call(ToolBox(), name, **arguments)
        assert is_error is False, name

    assert _tree(tmp_path) == before


def test_the_read_only_tools_leave_a_bundle_as_it_was(tmp_path: Path) -> None:
    evidence = _written_bundle(tmp_path)
    before = _tree(evidence)

    _call(ToolBox(), "runs", path=str(evidence))
    _call(ToolBox(), "evidence", path=str(evidence))
    _call(ToolBox(), "evidence", path=str(evidence), artifact="check-semantic.diff")

    assert _tree(evidence) == before


# --- the command ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (["--signing-key", "key.pem"], "--signing-key is given without --evidence"),
        (["--trust-key", "missing.pem"], "missing.pem"),
        (["--evidence", "evidence", "--signing-key", "missing.pem"], "missing.pem"),
    ],
)
def test_the_command_refuses_keys_it_cannot_use_before_it_serves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    said: str,
) -> None:
    monkeypatch.chdir(tmp_path)

    def serve(*args: object) -> None:
        raise AssertionError("the server must not start")

    monkeypatch.setattr("auto_bi.mcp.server.serve_stdio", serve)

    assert main(["decision", "mcp", *argv]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ") and said in captured.err


def test_the_command_serves_on_stdio_and_keeps_stray_output_off_it(tmp_path: Path) -> None:
    private_path, public_path, _ = _key_files(tmp_path, "trusted")
    case = Case(tmp_path).with_candidate()
    evidence = tmp_path / "evidence"
    keys = ["--signing-key", str(private_path), "--trust-key", str(public_path)]
    script = (
        "import os, sys\n"
        "print('noise before serving')\n"
        "from auto_bi.mcp import tools\n"
        "real = tools.validate_paths\n"
        "def noisy(paths):\n"
        "    print('noise of a library')\n"
        "    os.write(1, b'noise of the descriptor' + bytes([10]))\n"
        "    return real(paths)\n"
        "tools.validate_paths = noisy\n"
        "from auto_bi.cli import main\n"
        "sys.exit(main(['decision', 'mcp', '--evidence', *sys.argv[1:]]))\n"
    )

    def modern(ident: int, method: str, **params: Any) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": ident,
            "method": method,
            "params": {"_meta": META, **params},
        }

    requests = [
        modern(1, "server/discover"),
        modern(2, "tools/list"),
        modern(3, "tools/call", name="validate", arguments={"paths": [str(case.contracts)]}),
        modern(
            4,
            "tools/call",
            name="verify",
            arguments={**_refs(case), "created_at": CREATED_AT},
        ),
        modern(5, "tools/call", name="evidence", arguments={"path": str(evidence)}),
    ]
    stdin = b"".join(json.dumps(request).encode() + b"\n" for request in requests)

    # The line printed before serving is held by a buffered `sys.stdout`; an unbuffered one
    # would have written it before the server could point the descriptor elsewhere.
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONUNBUFFERED"}
    completed = subprocess.run(
        [sys.executable, "-c", script, str(evidence), *keys],
        input=stdin,
        capture_output=True,
        cwd=REPOSITORY,
        env=environment,
        timeout=300,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    answers = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [answer["id"] for answer in answers] == [1, 2, 3, 4, 5]
    assert all("result" in answer for answer in answers)
    for noise in (b"noise before serving", b"noise of a library", b"noise of the descriptor"):
        assert noise not in completed.stdout, noise
        assert noise in completed.stderr, noise
    assert "verify" in [tool["name"] for tool in answers[1]["result"]["tools"]]
    assert answers[2]["result"]["structuredContent"]["verdict"] == "pass"
    assert answers[3]["result"]["structuredContent"]["verdict"] == "pass_with_advisory"
    assert (_run_dir(evidence) / "manifest.json").is_file()
    # The keys are the server's: its bundles are sealed, and trusted when read back.
    inspection = answers[4]["result"]["structuredContent"]
    assert inspection["bundle"]["integrity"] == "signed"
    assert inspection["signature_verified"] is True
