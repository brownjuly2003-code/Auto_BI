#!/usr/bin/env python
"""Apply or dry-run GitHub repository protection for plan_sol step 5.

Creates/updates repository rulesets for ``main`` and ``v*`` tags, optionally
enables Dependabot security updates, and prints the pypi environment residual
(self-review) for a solo maintainer.

**Default is dry-run.** External mutation requires an explicit flag AND operator
authorization (shared-state / admin gate). Do not run ``--apply`` from an
autonomous agent session without a human yes for that specific external action.

Usage::

    python scripts/apply_github_protection.py              # dry-run, print plan
    python scripts/apply_github_protection.py --status      # current API state
    python scripts/apply_github_protection.py --apply       # mutate (gate!)
    python scripts/apply_github_protection.py --apply --only rulesets
    python scripts/apply_github_protection.py --apply --only security-updates

Requires ``gh`` authenticated with admin on the repo. Repo is detected from
``gh repo view`` unless ``--repo owner/name`` is passed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

# Check run *names* as reported by GitHub Actions on main (must match job `name:`).
# Keep in sync with .github/workflows/{ci,codeql,gitleaks}.yml.
REQUIRED_CHECKS: list[str] = [
    "Lint, format & tests (offline)",
    "Dependency audit (pip-audit)",
    "Docker image build (drift check)",
    "Integration (ClickHouse + Superset stand)",
    "gitleaks",
    "analyze (python)",
]

MAIN_RULESET_NAME = "main protection (plan_sol step 5)"
TAG_RULESET_NAME = "release tags v* (plan_sol step 5)"


def _gh(args: list[str], *, input_json: Any | None = None) -> Any:
    cmd = ["gh", *args]
    raw = subprocess.check_output(
        cmd,
        input=None if input_json is None else json.dumps(input_json).encode(),
        stderr=subprocess.STDOUT,
    )
    text = raw.decode().strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _gh_api(path: str, *extra: str, method: str | None = None, body: Any | None = None) -> Any:
    args = ["api", path, *extra]
    if method:
        args.extend(["-X", method])
    if body is not None:
        args.extend(["--input", "-"])
        return _gh(args, input_json=body)
    return _gh(args)


def detect_repo(explicit: str | None) -> str:
    if explicit:
        return explicit
    data = _gh(["repo", "view", "--json", "nameWithOwner"])
    assert isinstance(data, dict)
    return str(data["nameWithOwner"])


def main_ruleset_body() -> dict[str, Any]:
    # Solo-maintainer defaults: PR required + required checks + no force-push/delete.
    # Code-owner review and approving-review count stay off until a second human
    # reviewer exists (otherwise the only owner cannot merge their own PR).
    return {
        "name": MAIN_RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {
            "ref_name": {
                "include": ["refs/heads/main"],
                "exclude": [],
            }
        },
        "rules": [
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews": True,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": True,
                    "allowed_merge_methods": ["merge", "squash", "rebase"],
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [{"context": name} for name in REQUIRED_CHECKS],
                },
            },
            {"type": "non_fast_forward"},
            {"type": "deletion"},
        ],
        # No permanent admin bypass actors. Operator emergency bypass is the
        # ruleset "bypass" UI / temporary enforcement=disabled — document in DEPLOYMENT.
        "bypass_actors": [],
    }


def tag_ruleset_body() -> dict[str, Any]:
    # Immutable release tags: block update (overwrite) and deletion. Creation stays
    # open to admins so humans can cut vX.Y.Z; release workflow then reacts to the tag.
    return {
        "name": TAG_RULESET_NAME,
        "target": "tag",
        "enforcement": "active",
        "conditions": {
            "ref_name": {
                "include": ["refs/tags/v*"],
                "exclude": [],
            }
        },
        "rules": [
            {"type": "update"},
            {"type": "deletion"},
            {"type": "non_fast_forward"},
        ],
        "bypass_actors": [],
    }


def list_rulesets(repo: str) -> list[dict[str, Any]]:
    data = _gh_api(f"repos/{repo}/rulesets")
    if data is None:
        return []
    assert isinstance(data, list)
    return data


def upsert_ruleset(repo: str, body: dict[str, Any], *, apply: bool) -> str:
    existing = {r["name"]: r for r in list_rulesets(repo)}
    name = body["name"]
    if name in existing:
        rid = existing[name]["id"]
        if apply:
            _gh_api(
                f"repos/{repo}/rulesets/{rid}",
                method="PUT",
                body=body,
            )
            return f"updated ruleset id={rid} name={name!r}"
        return f"would UPDATE ruleset id={rid} name={name!r}"
    if apply:
        created = _gh_api(f"repos/{repo}/rulesets", method="POST", body=body)
        rid = created.get("id") if isinstance(created, dict) else "?"
        return f"created ruleset id={rid} name={name!r}"
    return f"would CREATE ruleset name={name!r}"


def enable_security_updates(repo: str, *, apply: bool) -> str:
    # Dependabot version updates already live in .github/dependabot.yml.
    # Security updates are a separate repo security_and_analysis toggle.
    if not apply:
        return "would PATCH security_and_analysis.dependabot_security_updates=enabled"
    _gh_api(
        f"repos/{repo}",
        method="PATCH",
        body={
            "security_and_analysis": {
                "dependabot_security_updates": {"status": "enabled"},
            }
        },
    )
    return "enabled dependabot_security_updates"


def status_report(repo: str) -> dict[str, Any]:
    rulesets = list_rulesets(repo)
    try:
        repo_meta = _gh_api(
            f"repos/{repo}",
            "--jq",
            "{visibility, security_and_analysis}",
        )
    except subprocess.CalledProcessError as exc:
        repo_meta = {"error": str(exc)}
    try:
        envs = _gh_api(f"repos/{repo}/environments")
    except subprocess.CalledProcessError as exc:
        envs = {"error": str(exc)}
    return {
        "repo": repo,
        "rulesets": [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "target": r.get("target"),
                "enforcement": r.get("enforcement"),
            }
            for r in rulesets
        ],
        "required_checks_planned": REQUIRED_CHECKS,
        "repo_meta": repo_meta,
        "environments": envs,
        "pypi_self_review_note": (
            "Solo maintainer: leave prevent_self_review=false on environment "
            "`pypi` until a second reviewer exists; enabling it deadlocks releases."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="owner/name (default: gh repo view)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Mutate GitHub state (requires operator authorization)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print current protection state as JSON and exit",
    )
    parser.add_argument(
        "--only",
        choices=("rulesets", "security-updates", "all"),
        default="all",
        help="Which external actions to plan/apply",
    )
    args = parser.parse_args(argv)

    try:
        repo = detect_repo(args.repo)
    except (subprocess.CalledProcessError, FileNotFoundError, AssertionError) as exc:
        print(f"error: cannot detect repo via gh: {exc}", file=sys.stderr)
        return 2

    if args.status:
        print(json.dumps(status_report(repo), indent=2, ensure_ascii=False))
        return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] repo={repo}")
    print(f"required status checks ({len(REQUIRED_CHECKS)}):")
    for name in REQUIRED_CHECKS:
        print(f"  - {name}")

    actions: list[str] = []
    if args.only in ("rulesets", "all"):
        actions.append(upsert_ruleset(repo, main_ruleset_body(), apply=args.apply))
        actions.append(upsert_ruleset(repo, tag_ruleset_body(), apply=args.apply))
    if args.only in ("security-updates", "all"):
        actions.append(enable_security_updates(repo, apply=args.apply))

    for line in actions:
        print(f"  * {line}")

    print()
    print("Residual (not auto-applied):")
    print("  - environment `pypi`: keep prevent_self_review=false for solo owner")
    print("  - open Dependabot PRs: review in small compatible groups (see plan_sol step 5)")
    print("  - coverage badge commit-back soft-skips if main rejects direct push")
    if not args.apply:
        print()
        print("Re-run with --apply after explicit operator authorization.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
