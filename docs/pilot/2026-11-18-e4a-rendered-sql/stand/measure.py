"""E4a measurement on the stand: what SQL the inventory can get without a browser, against what the
engine ran when the dashboard opened.

Per dashboard: the three inventory GETs and `chart/{id}` are recorded raw; each chart's
`GET chart/{id}/data?type=query` is recorded with the engine log of its window; then the dashboard
is opened in headless Chrome as the given user, every tab is clicked and the page scrolled, and
each data request (`/api/v1/chart/data` or legacy `explore_json`, body and answer) is recorded;
the engine log is taken once for the whole open — one window per dashboard, not per request.
A chart the dashboard never asked for is opened in Explore (`context: explore`).

Run with the stand's Playwright venv:
  pwvenv/bin/python measure.py --version 41 --user admin --out rec/41-admin [--dashboard ID ...]
Secrets come from $E4A_STAND/.env (default ~/e4astand) and are never written or printed.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import sync_playwright

STAND = Path(os.environ.get("E4A_STAND", Path.home() / "e4astand"))
ENV = dict(line.split("=", 1) for line in (STAND / ".env").read_text().splitlines() if "=" in line)
DOCKER = ["docker", "--context", "colima-autobi-linux"]
USERS = {
    "admin": ("AUTO_BI_SUPERSET_USER", "AUTO_BI_SUPERSET_PASSWORD"),
    "reader": ("INVENTORY_SUPERSET_USER", "INVENTORY_SUPERSET_PASSWORD"),
}


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Api:
    def __init__(self, base: str, user: str) -> None:
        self.user = user
        self.http = httpx.Client(base_url=base, timeout=120, follow_redirects=True)
        self.login()

    def login(self) -> None:
        name, secret = (ENV[key] for key in USERS[self.user])
        token = self.http.post(
            "/api/v1/security/login",
            json={"username": name, "password": secret, "provider": "db", "refresh": True},
        ).json()["access_token"]
        self.http.headers["Authorization"] = f"Bearer {token}"

    def get(self, path: str, **params: Any) -> tuple[int, Any]:
        for attempt in range(4):
            try:
                answer = self.http.get(path, params=params or None)
                if answer.status_code == 401:  # the access token lives 15 minutes
                    self.login()
                    answer = self.http.get(path, params=params or None)
                # The stand's metadata DB is SQLite: a read during a write answers 500
                # "database is locked"; that is the stand, not the answer being measured.
                if answer.status_code == 500 and "locked" in answer.text and attempt < 3:
                    time.sleep(5 * (attempt + 1))
                    continue
                break
            except httpx.TransportError:  # a gunicorn worker restart drops the connection
                if attempt == 3:
                    raise
                time.sleep(5 * (attempt + 1))
        try:
            body = answer.json()
        except ValueError:
            body = {"_text": answer.text[:2000]}
        return answer.status_code, body


def clickhouse_log(since: dt.datetime, until: dt.datetime) -> list[dict[str, Any]]:
    query = (
        "SYSTEM FLUSH LOGS; SELECT toString(event_time_microseconds) AS at, user, query_id, "
        "toString(type) AS type, query, exception FROM system.query_log "
        f"WHERE event_time_microseconds >= parseDateTime64BestEffort('{since.isoformat()}', 6) "
        f"AND event_time_microseconds <= parseDateTime64BestEffort('{until.isoformat()}', 6) "
        "AND user != 'admin' AND type != 'QueryStart' ORDER BY event_time_microseconds "
        "FORMAT JSONEachRow"
    )
    out = subprocess.run(
        [
            *DOCKER,
            "exec",
            "e4a_clickhouse",
            "sh",
            "-c",
            'clickhouse-client --user admin --password "$CLICKHOUSE_PASSWORD" --multiquery --query "$0"',  # noqa: E501
            query,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


_PG_LINE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+ \w+) \[(\d+)\] db=(\S*) user=(\S*) app=(.*?) (\w+):  (.*)$"  # noqa: E501
)


def postgres_log(since: dt.datetime, until: dt.datetime) -> list[dict[str, Any]]:
    out = subprocess.run(
        [
            *DOCKER,
            "logs",
            "--since",
            since.isoformat(),
            "--until",
            until.isoformat(),
            "e4a_postgres",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    entries: list[dict[str, Any]] = []
    for line in (out.stdout + out.stderr).splitlines():
        match = _PG_LINE.match(line)
        if match:
            at, pid, db, user, app, level, text = match.groups()
            entries.append(
                {
                    "at": at,
                    "pid": pid,
                    "db": db,
                    "user": user,
                    "app": app,
                    "level": level,
                    "text": text,
                }
            )
        elif entries and line.startswith("\t"):
            entries[-1]["text"] += "\n" + line[1:]
    return [e for e in entries if e["db"].startswith("examples_")]


def engine_log(since: dt.datetime, until: dt.datetime) -> dict[str, Any]:
    time.sleep(1.5)  # the Postgres log line and the ClickHouse query_log flush trail the answer
    return {"clickhouse": clickhouse_log(since, until), "postgres": postgres_log(since, until)}


def record_api(api: Api, dashboard: int, out: dict[str, Any]) -> list[int]:
    for name, path in (
        ("dashboard", f"/api/v1/dashboard/{dashboard}"),
        ("charts", f"/api/v1/dashboard/{dashboard}/charts"),
        ("datasets", f"/api/v1/dashboard/{dashboard}/datasets"),
    ):
        out["inventory"][name] = api.get(path)
    status, charts = out["inventory"]["charts"]
    ids = [int(c["id"]) for c in charts.get("result", [])] if status == 200 else []
    # Who wrote the saved query_context last: the chart list carries `last_saved_at` (a save
    # through the UI or the API sets it; Explore's own query_context write does not) next to
    # `changed_on_utc` (every write).
    listed: dict[str, Any] = {}
    for page in range(10):
        status, body = api.get(
            "/api/v1/chart/",
            q=f"(columns:!(id,changed_on_utc,last_saved_at),page:{page},page_size:100)",
        )
        rows = body.get("result", []) if status == 200 else []
        listed.update({str(r["id"]): r for r in rows if int(r["id"]) in ids})
        if len(rows) < 100:
            break
    out["chart_list"] = listed
    for chart in ids:
        start = now()
        detail = api.get(f"/api/v1/chart/{chart}")
        rendered = api.get(f"/api/v1/chart/{chart}/data", type="query", format="json")
        out["charts"][str(chart)] = {
            "detail": detail,
            "get_query": rendered,
            "get_query_engine": engine_log(start, now()),
        }
    return ids


def login(page: Any, base: str, user: str) -> None:
    """Log the browser in and check the session holds.

    The login page of 6.1 posts its event log while the form is sent; when that unauthenticated
    answer lands last, its cookie replaces the new session and every page after the login
    answers 401. So the session is checked through /api/v1/me/ and the login is repeated.
    """
    name, secret = (ENV[key] for key in USERS[user])
    for _ in range(4):
        page.goto(f"{base}/login/", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)  # let the login page post its event log before the form
        page.locator("input[name=username], input#username").first.fill(name, timeout=60_000)
        page.locator("input[type=password]").first.fill(secret)
        page.locator("input[type=password]").first.press("Enter")
        try:
            page.wait_for_url(lambda url: "/login" not in url, timeout=30_000)
        except Exception:
            continue
        page.wait_for_timeout(2000)
        if page.request.get(f"{base}/api/v1/me/").status == 200:
            return
    raise SystemExit(f"browser login as {user}: the session does not hold")


def settle(page: Any, inflight: set[Any], quiet: float = 4.0, limit: float = 120.0) -> None:
    started = last = time.monotonic()
    while time.monotonic() - started < limit:
        page.wait_for_timeout(500)
        if inflight:
            last = time.monotonic()
        elif time.monotonic() - last >= quiet:
            return


def open_in_browser(page: Any, url: str, captured: list[dict[str, Any]], tabs: bool) -> None:
    inflight: set[Any] = set()

    def on_request(request: Any) -> None:
        if request.method == "POST" and (
            "/api/v1/chart/data" in request.url or "/superset/explore_json/" in request.url
        ):
            inflight.add(request)

    def on_finished(request: Any) -> None:
        if request not in inflight:
            return
        inflight.discard(request)
        response = request.response()
        body: Any = None
        if response is not None:
            try:
                body = response.json()
            except Exception as error:
                body = {"_error": repr(error)}
            # The rows are not the question; their count and the SQL are.
            for item in (body or {}).get("result") or [] if isinstance(body, dict) else []:
                if isinstance(item, dict) and "data" in item:
                    data = item.pop("data")
                    item["data_rows"] = len(data) if isinstance(data, list) else None
        try:
            sent = request.post_data_json
        except Exception:
            sent = request.post_data
        captured.append(
            {
                "url": request.url,
                "sent": sent,
                "status": response.status if response is not None else None,
                "answer": body,
                "at": now().isoformat(),
            }
        )

    page.on("request", on_request)
    page.on("requestfinished", on_finished)
    page.on("requestfailed", on_finished)
    page.goto(url, wait_until="domcontentloaded")
    settle(page, inflight)
    for _ in range(12):
        page.mouse.wheel(0, 2500)
        settle(page, inflight, quiet=2.0, limit=30)
    if tabs:
        seen: set[str] = set()
        for _ in range(40):
            tab_items = page.locator(".ant-tabs-tab")
            todo = [
                i for i in range(tab_items.count()) if tab_items.nth(i).inner_text() not in seen
            ]
            if not todo:
                break
            item = tab_items.nth(todo[0])
            seen.add(item.inner_text())
            item.click()
            settle(page, inflight)
            for _ in range(6):
                page.mouse.wheel(0, 2500)
                settle(page, inflight, quiet=2.0, limit=30)
    page.remove_listener("request", on_request)
    page.remove_listener("requestfinished", on_finished)
    page.remove_listener("requestfailed", on_finished)


def explore_open(page: Any, base: str, chart: int) -> dict[str, Any]:
    """Open the chart in Explore, as a user does, and wait for the PUT the page sends by itself
    when the chart has no `query_context`: the frontend builds one from the saved form_data and
    writes it with `query_context_generation: true` (no click, no ownership check on the server).
    Returns that PUT and its status, or `{"put": False}` when none came in 60 seconds."""
    saved: dict[str, Any] = {}

    def on_finished(request: Any) -> None:
        if request.method == "PUT" and re.search(rf"/api/v1/chart/{chart}(\?|$)", request.url):
            response = request.response()
            try:
                sent = request.post_data_json
            except Exception:
                sent = request.post_data
            saved.update({"sent": sent, "status": response.status if response else None})

    page.on("requestfinished", on_finished)
    page.on("requestfailed", on_finished)
    try:
        page.goto(f"{base}/explore/?slice_id={chart}", wait_until="domcontentloaded")
        for _ in range(120):
            if saved:
                break
            page.wait_for_timeout(500)
        if not saved:
            saved["put"] = False
    except Exception as error:
        saved["error"] = repr(error)[:500]
    finally:
        page.remove_listener("requestfinished", on_finished)
        page.remove_listener("requestfailed", on_finished)
    return saved


def ui_save(page: Any, base: str, chart: int) -> dict[str, Any]:
    """Open the chart in Explore and save it over itself through the Save dialog, as a user does:
    the page writes `params` and the `query_context` built from the form it holds (with the
    defaults of its controls). Returns the PUT the page sent and its status."""
    saved: dict[str, Any] = {}

    def on_finished(request: Any) -> None:
        if request.method != "PUT" or not re.search(rf"/api/v1/chart/{chart}(\?|$)", request.url):
            return
        try:
            sent = request.post_data_json
        except Exception:
            sent = request.post_data
        if isinstance(sent, dict) and sent.get("query_context_generation"):
            return  # the page's own generation, not the save
        response = request.response()
        saved.update({"sent": sent, "status": response.status if response else None})

    page.on("requestfinished", on_finished)
    save = page.locator("button", has_text=re.compile(r"^\s*save\s*$", re.I)).first
    try:
        # A stand read that fails (SQLite lock) leaves Explore without the chart ("Missing
        # dataset", Save disabled): the page is opened again, up to three times.
        for attempt in range(3):
            saved.clear()
            saved["attempts"] = attempt + 1
            page.goto(f"{base}/explore/?slice_id={chart}", wait_until="domcontentloaded")
            with contextlib.suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=60_000)
            if save.is_disabled():
                saved["error"] = "Save disabled"
                continue
            save.click(timeout=60_000)
            modal = page.locator(".ant-modal-content").first
            modal.wait_for(timeout=30_000)
            overwrite = modal.get_by_role("radio", name=re.compile(r"overwrite", re.I))
            if overwrite.count() and not overwrite.first.is_checked():
                overwrite.first.check(force=True, timeout=10_000)
            saved["overwrite_checked"] = bool(overwrite.count()) and overwrite.first.is_checked()
            modal.locator("button", has_text=re.compile(r"^\s*save\s*$", re.I)).first.click(
                timeout=30_000
            )
            for _ in range(60):
                if "status" in saved:
                    break
                page.wait_for_timeout(500)
            if "status" in saved:
                break
            saved["put"] = False
        if "status" not in saved:
            page.screenshot(path=str(STAND / "rec" / f"ui_save_error_{chart}.png"))
    except Exception as error:
        saved["error"] = repr(error)[:500]
        page.screenshot(path=str(STAND / "rec" / f"ui_save_error_{chart}.png"))
    finally:
        page.remove_listener("requestfinished", on_finished)
    return saved


def chart_of(entry: dict[str, Any]) -> int | None:
    sent = entry.get("sent")
    if isinstance(sent, dict):
        form = sent.get("form_data") or {}
        slice_id = form.get("slice_id")
        if slice_id is not None:
            return int(slice_id)
    match = re.search(r"slice_id%22%3A(\d+)|slice_id=(\d+)", entry["url"])
    return int(match.group(1) or match.group(2)) if match else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=["41", "61"], required=True)
    parser.add_argument("--user", choices=sorted(USERS), default="admin")
    parser.add_argument("--out", required=True)
    parser.add_argument("--dashboard", type=int, action="append")
    parser.add_argument("--url-suffix", default="")
    parser.add_argument("--explore-open", action="store_true")
    parser.add_argument("--ui-save", type=int, action="append", help="chart id to save in Explore")
    args = parser.parse_args()
    base = "http://127.0.0.1:" + ("8088" if args.version == "41" else "8089")
    out_dir = STAND / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    api = Api(base, args.user)
    status, listed = api.get("/api/v1/dashboard/", q="(page_size:100)")
    dashboards = args.dashboard or sorted(int(d["id"]) for d in listed.get("result", []))
    with sync_playwright() as play:
        browser = play.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 2400})
        page = context.new_page()
        login(page, base, args.user)
        if args.ui_save:
            saves = {str(chart): ui_save(page, base, chart) for chart in args.ui_save}
            for chart, saved in saves.items():
                print("saved", chart, saved.get("status"), saved.get("error", ""), flush=True)
            (out_dir / "ui_save.json").write_text(
                json.dumps(saves, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            browser.close()
            return
        if args.explore_open:
            # Written after every chart and read back on a rerun: an interrupted pass keeps what
            # it recorded, and the rerun opens only the charts not recorded yet.
            done = out_dir / "explore_open.json"
            saves: dict[str, Any] = (
                json.loads(done.read_text(encoding="utf-8")) if done.exists() else {}
            )
            for dashboard in dashboards:
                status, charts = api.get(f"/api/v1/dashboard/{dashboard}/charts")
                if status != 200:
                    raise SystemExit(f"dashboard {dashboard} charts: {status}")
                for chart in sorted(int(c["id"]) for c in charts.get("result", [])):
                    if str(chart) in saves:
                        continue
                    status, detail = api.get(f"/api/v1/chart/{chart}")
                    had = bool(detail.get("result", {}).get("query_context"))
                    # A saved query_context is never regenerated by Explore (null check in the
                    # frontend), so a chart that has one is not opened.
                    saves[str(chart)] = (
                        {"had_qc": True, "opened": False}
                        if had
                        else {"had_qc": False, **explore_open(page, base, chart)}
                    )
                    print(
                        "opened",
                        chart,
                        "had_qc",
                        had,
                        "put",
                        saves[str(chart)].get("status"),
                        saves[str(chart)].get("error", ""),
                        flush=True,
                    )
                    done.write_text(
                        json.dumps(saves, ensure_ascii=False, indent=1), encoding="utf-8"
                    )
            browser.close()
            return
        for dashboard in dashboards:
            record: dict[str, Any] = {
                "dashboard": dashboard,
                "version": args.version,
                "user": args.user,
                "inventory": {},
                "charts": {},
            }
            ids = record_api(api, dashboard, record)
            captured: list[dict[str, Any]] = []
            start = now()
            open_in_browser(
                page,
                f"{base}/superset/dashboard/{dashboard}/{args.url_suffix}",
                captured,
                tabs=True,
            )
            record["dashboard_window"] = {"from": start.isoformat(), "to": now().isoformat()}
            record["dashboard_requests"] = captured
            record["dashboard_engine"] = engine_log(start, now())
            asked = {chart_of(e) for e in captured}
            record["explore"] = {}
            for chart in ids:
                if chart in asked:
                    continue
                explore: list[dict[str, Any]] = []
                start = now()
                open_in_browser(page, f"{base}/explore/?slice_id={chart}", explore, tabs=False)
                record["explore"][str(chart)] = {
                    "requests": explore,
                    "engine": engine_log(start, now()),
                }
            path = out_dir / f"dashboard_{dashboard}.json"
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            print(
                dashboard,
                "charts",
                len(ids),
                "dashboard requests",
                len(captured),
                "explore",
                len(record["explore"]),
                flush=True,
            )
        browser.close()


if __name__ == "__main__":
    main()
