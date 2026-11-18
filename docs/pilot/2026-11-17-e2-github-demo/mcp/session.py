"""E2: one session of `auto_bi decision mcp` with an MCP client outside the tests.

Client: the MCP Python SDK (package `mcp`), two sessions — the modern stateless revision
(`server/discover`, 2026-07-28) and the legacy handshake (`initialize`). Every tool is called on
the chart-impact pilot; each answer is compared with what the CLI prints for the same inputs.

usage: python -I -X utf8 session.py <repo> <out_dir>

The client runs from its own virtual environment (`pip install mcp==2.3.0`); the server is the
repository's `.venv`. The record of this run: `session-summary.json` and the README beside it.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
EXE = str(REPO / ".venv" / ("Scripts/auto_bi.exe" if sys.platform == "win32" else "bin/auto_bi"))
P = "docs/pilot/2026-08-25-ch-tz-margin"
OVERLAY = "docs/pilot/2026-09-12-owner-review-chart-impact"
PRODUCT = "decision://sales/weekly_margin"
CREATED = "2026-09-12T16:00:00Z"
GATE_ARGS = {
    "paths": [f"{P}/contracts"],
    "base": f"{P}/base",
    "candidate": f"{P}/candidate",
    "dashboards": f"{OVERLAY}/dashboards",
    "product": PRODUCT,
}


def cli(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [EXE, "decision", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return proc.returncode, proc.stdout


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(f.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def structured(result) -> dict:
    data = result.structured_content
    text = result.content[0].text if result.content else None
    assert text is not None and json.loads(text) == data, "text block != structuredContent"
    return {"isError": result.is_error, "structured": data}


async def one_session(era: str, evidence: Path) -> dict:
    params = StdioServerParameters(
        command=EXE,
        args=["decision", "mcp", "--evidence", str(evidence)],
        cwd=str(REPO),
    )
    log: dict = {"era": era}
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        if era == "modern":
            res = await session.discover()
            log["handshake"] = res.model_dump(mode="json", by_alias=True, exclude_none=True)
        else:
            res = await session.initialize()
            log["handshake"] = res.model_dump(mode="json", by_alias=True, exclude_none=True)
        log["protocol_version"] = session.protocol_version
        tools = await session.list_tools()
        log["tools"] = [t.name for t in tools.tools]
        calls = {}
        calls["validate"] = structured(
            await session.call_tool("validate", {"paths": GATE_ARGS["paths"]})
        )
        calls["impact"] = structured(await session.call_tool("impact", dict(GATE_ARGS)))
        calls["verify"] = structured(
            await session.call_tool("verify", {**GATE_ARGS, "created_at": CREATED})
        )
        calls["evidence"] = structured(
            await session.call_tool("evidence", {"path": str(evidence)})
        )
        digest = calls["evidence"]["structured"].get("manifest_digest", "")
        calls["evidence_artifact"] = structured(
            await session.call_tool(
                "evidence",
                {
                    "path": str(evidence),
                    "artifact": "check-impact.products",
                    "expect_digest": digest,
                },
            )
        )
        calls["runs"] = structured(await session.call_tool("runs", {"path": str(evidence)}))
        log["calls"] = calls
    return log


def same_payload(payload, text: str) -> bool:
    if isinstance(payload, str):
        return payload.rstrip(chr(10)) == text.rstrip(chr(10))
    return payload == json.loads(text)


def strip_paths(value, *roots: str):
    text = json.dumps(value, sort_keys=True)
    for root in roots:
        text = text.replace(json.dumps(root)[1:-1], "<EVIDENCE>")
    return json.loads(text)


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    report: dict = {
        "client": {
            "name": "mcp (MCP Python SDK)",
            "version": importlib.metadata.version("mcp"),
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "server": EXE,
        "inputs": {**GATE_ARGS, "created_at": CREATED},
    }
    sessions = {}
    for era in ("modern", "legacy"):
        ev = OUT / f"mcp_evidence_{era}"
        sessions[era] = anyio.run(one_session, era, ev)
        sessions[era]["bundle_tree"] = tree_digest(ev)
    report["sessions"] = sessions

    # The CLI on the same inputs.
    cli_ev = OUT / "cli_evidence"
    flags = [
        f"--base={GATE_ARGS['base']}",
        f"--candidate={GATE_ARGS['candidate']}",
        f"--dashboards={GATE_ARGS['dashboards']}",
        f"--product={PRODUCT}",
    ]
    code_v, out_v = cli("validate", "--json", *GATE_ARGS["paths"])
    code_g, out_g = cli(
        "verify", *flags, f"--created-at={CREATED}", f"--evidence={cli_ev}", "--json",
        *GATE_ARGS["paths"],
    )
    code_e, out_e = cli("evidence", str(cli_ev), "--json")
    digest = json.loads(out_e)["manifest_digest"]
    code_a, out_a = cli(
        "evidence", str(cli_ev), "--artifact=check-impact.products",
        f"--expect-digest={digest}",
    )
    code_r, out_r = cli("runs", str(cli_ev), "--json")
    report["cli"] = {
        "validate_exit": code_v,
        "verify_exit": code_g,
        "evidence_exit": code_e,
        "artifact_exit": code_a,
        "runs_exit": code_r,
        "bundle_tree": tree_digest(cli_ev),
        "manifest_digest": digest,
    }
    cli_json = {
        "validate": json.loads(out_v),
        "verify": json.loads(out_g),
        "evidence": json.loads(out_e),
        "artifact": json.loads(out_a),
        "runs": json.loads(out_r),
    }
    (OUT / "cli.json").write_text(json.dumps(cli_json, indent=2, sort_keys=True), "utf-8")

    compare = {}
    for era, log in sessions.items():
        ev = str(OUT / f"mcp_evidence_{era}")
        c = log["calls"]
        roots = (ev, str(cli_ev))
        res = {
            "validate": c["validate"]["structured"] == cli_json["validate"],
            "verify": strip_paths(c["verify"]["structured"], *roots)
            == strip_paths(cli_json["verify"], *roots),
            "evidence": strip_paths(c["evidence"]["structured"], *roots)
            == strip_paths(cli_json["evidence"], *roots),
            "evidence_artifact": same_payload(
                c["evidence_artifact"]["structured"].get("payload"), out_a
            ),
            "impact_is_bundle_payload": c["impact"]["structured"].get("impact")
            == cli_json["artifact"],
            "runs": strip_paths(c["runs"]["structured"].get("runs"), *roots)
            == strip_paths(cli_json["runs"], *roots),
            "bundle_bytes_equal_cli": log["bundle_tree"] == report["cli"]["bundle_tree"],
            "any_isError": any(v["isError"] for v in c.values()),
        }
        compare[era] = res
    report["compare"] = compare
    (OUT / "session.json").write_text(json.dumps(report, indent=2, sort_keys=True), "utf-8")
    print(json.dumps({"client": report["client"], "compare": compare,
                      "protocols": {e: s["protocol_version"] for e, s in sessions.items()},
                      "tools": {e: s["tools"] for e, s in sessions.items()},
                      "cli": report["cli"]}, indent=2))
    ok = all(all(v for k, v in r.items() if k != "any_isError") and not r["any_isError"]
             for r in compare.values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
