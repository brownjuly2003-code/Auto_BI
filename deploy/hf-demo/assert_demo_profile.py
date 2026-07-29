#!/usr/bin/env python3
"""Post-deploy / local assertion of the public demo capability profile.

Usage:
  python deploy/hf-demo/assert_demo_profile.py https://example.hf.space
  python deploy/hf-demo/assert_demo_profile.py http://localhost:7860 --prefix /agent

Default expectation is auto-only (text/fields/enrichment closed, LLM not wired).
Pass --text-enabled only when the Space intentionally runs with a live LLM.
Exits 0 on match, 1 on mismatch, 2 on transport/parse failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def _get(url: str) -> tuple[int, dict | list | str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            code = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        code = exc.code
    except Exception as exc:
        print(f"transport error GET {url}: {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        return code, json.loads(body)
    except json.JSONDecodeError:
        return code, body


def _post(url: str, payload: dict) -> tuple[int, dict | list | str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            code = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        code = exc.code
    except Exception as exc:
        print(f"transport error POST {url}: {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        return code, json.loads(body)
    except json.JSONDecodeError:
        return code, body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="Space or local base, e.g. https://x.hf.space")
    parser.add_argument(
        "--prefix",
        default="/agent",
        help="API mount prefix (HF demo nginx: /agent; bare serve: empty)",
    )
    parser.add_argument(
        "--text-enabled",
        action="store_true",
        help="Expect text/fields open (demo_auto_only=false, llm_wired=true)",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    prefix = args.prefix.rstrip("/")
    api = f"{base}{prefix}/api/v1"

    failures: list[str] = []

    code, health = _get(f"{api}/health")
    if code != 200 or not isinstance(health, dict):
        print(f"FAIL health: HTTP {code} body={health!r}", file=sys.stderr)
        return 1

    caps = health.get("capabilities")
    if not isinstance(caps, dict):
        failures.append(f"health.capabilities missing or not an object: {caps!r}")
    else:
        expected_auto_only = not args.text_enabled
        if health.get("demo_auto_only") is not expected_auto_only:
            failures.append(
                f"demo_auto_only={health.get('demo_auto_only')!r}, "
                f"expected {expected_auto_only}"
            )
        if caps.get("auto_overview") is not True:
            failures.append(f"auto_overview={caps.get('auto_overview')!r}, expected True")
        if args.text_enabled:
            for key in ("text_session", "fields_session", "word_edit", "llm_wired"):
                if caps.get(key) is not True:
                    failures.append(f"capabilities.{key}={caps.get(key)!r}, expected True")
        else:
            for key in ("text_session", "fields_session", "word_edit", "llm_wired"):
                if caps.get(key) is not False:
                    failures.append(f"capabilities.{key}={caps.get(key)!r}, expected False")
            if caps.get("enrichment") is not False:
                failures.append(
                    f"capabilities.enrichment={caps.get('enrichment')!r}, expected False"
                )

    if not args.text_enabled:
        # 403 before any LLM call — gates must not depend on provider reachability
        for label, path, payload in (
            ("text session", f"{api}/sessions", {"request": "revenue"}),
            (
                "fields session",
                f"{api}/sessions",
                {
                    "request": "",
                    "seed": {
                        "groups": [{"label": "g", "fields": ["dm.sales_daily.revenue"]}],
                        "comment": "",
                    },
                },
            ),
            ("enrichment table", f"{api}/model/tables/dm.sales_daily", None),
        ):
            if payload is None:
                req = urllib.request.Request(
                    path,
                    data=json.dumps({"description": "x"}).encode("utf-8"),
                    method="PATCH",
                    headers={"Content-Type": "application/json"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        gcode = resp.status
                except urllib.error.HTTPError as exc:
                    gcode = exc.code
                except Exception as exc:
                    failures.append(f"{label}: transport {exc}")
                    continue
            else:
                gcode, _ = _post(path, payload)
            if gcode != 403:
                failures.append(f"{label}: HTTP {gcode}, expected 403")

    if failures:
        print("demo profile assertion FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print(f"health={json.dumps(health, ensure_ascii=False)}", file=sys.stderr)
        return 1

    print(
        "demo profile OK:",
        f"demo_auto_only={health.get('demo_auto_only')}",
        f"capabilities={json.dumps(caps, ensure_ascii=False)}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
