#!/usr/bin/env python3
"""Render the dashboard to a static site for GitHub Pages.

The Flask app serves both range variants from one route via ``?range=``.
Pages has no server, so each variant becomes its own file:

    dist/index.html   30-day view
    dist/12m.html     12-month view

Every upstream is fetched once per build, so the published site is as fresh
as the last successful workflow run.

Because each ``_fetch_*`` helper traps its own exceptions and renders an
error card, a build can otherwise go green with every panel dead.  This
script therefore inspects the rendered context and reports panel health:
degraded panels are warnings, and the build fails outright once more than
``--max-failed`` panels are unavailable, so Pages keeps serving the last
good copy rather than publishing a page of error cards.

Usage::

    python scripts/build_static.py dist          # build and publish
    python scripts/build_static.py --check       # report health, write nothing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Rendered as index.html so the 30-day view is the site root.
PAGES = {"30d": "index.html", "12m": "12m.html"}

# Health is a property of the upstreams, not of the range, so it is only
# reported for this variant to avoid duplicate noise in the log.
_HEALTH_PERIOD = "30d"

_STATE_ICON = {"ok": "ok  ", "degraded": "WARN", "failed": "FAIL"}


def _in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def _annotate(level: str, message: str) -> None:
    """Emit a GitHub Actions annotation so health shows in the run UI."""
    if _in_actions():
        print(f"::{level}::{message}", flush=True)


def _step_summary(lines: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:  # never fail a build over reporting
        print(f"could not write step summary: {exc}", flush=True)


def report_health(health: dict) -> None:
    """Print a per-panel health table and surface it to Actions."""
    print("\nupstream panel health:", flush=True)
    for panel in health["detail"].values():
        print(f"  [{_STATE_ICON[panel['state']]}] {panel['label']}", flush=True)

    summary = (
        f"{health['ok']}/{health['total']} sources live, "
        f"{health['degraded']} degraded, {health['failed']} unavailable"
    )
    print(f"\n{summary}", flush=True)

    for panel in health["detail"].values():
        if panel["state"] == "failed":
            _annotate("error", f"{panel['label']} is unavailable")
        elif panel["state"] == "degraded":
            _annotate("warning", f"{panel['label']} is degraded")

    rows = [f"| {p['label']} | {p['state']} |" for p in health["detail"].values()]
    _step_summary(
        ["### Dashboard sources", "", summary, "", "| Panel | State |", "| --- | --- |"]
        + rows
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "out_dir",
        nargs="?",
        default="dist",
        type=Path,
        help="directory to write the static site into (default: dist)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fetch upstreams and report health without writing any files",
    )
    parser.add_argument(
        "--max-failed",
        type=int,
        default=int(os.environ.get("MIDAS_MAX_FAILED_PANELS", "3")),
        help="fail the build when more than this many panels are unavailable",
    )
    args = parser.parse_args(argv)

    from midas.app import STATIC_LINKS, build_context, render_context

    health: dict | None = None
    rendered: dict[str, str] = {}

    for period, filename in PAGES.items():
        print(f"rendering {period} -> {filename}", flush=True)
        context = build_context(period, links=STATIC_LINKS)
        rendered[filename] = render_context(context)
        if period == _HEALTH_PERIOD:
            health = context["health"]

    assert health is not None, f"{_HEALTH_PERIOD} must be among {list(PAGES)}"
    report_health(health)

    if health["failed"] > args.max_failed:
        _annotate(
            "error",
            f"{health['failed']} panels unavailable (limit {args.max_failed}); "
            "not publishing, the previous build stays live",
        )
        print(
            f"\nrefusing to publish: {health['failed']} panels unavailable "
            f"(limit {args.max_failed})",
            flush=True,
        )
        return 1

    if args.check:
        print("\n--check: no files written", flush=True)
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for filename, html in rendered.items():
        (args.out_dir / filename).write_text(html, encoding="utf-8")

    # Stops GitHub Pages running the output through Jekyll.
    (args.out_dir / ".nojekyll").write_text("", encoding="utf-8")

    # Published alongside the pages so health is checkable without scraping
    # the HTML — handy for an uptime probe.
    (args.out_dir / "health.json").write_text(
        json.dumps(health, indent=2), encoding="utf-8"
    )

    print("", flush=True)
    for filename in PAGES.values():
        size = (args.out_dir / filename).stat().st_size
        print(f"  {filename}: {size:,} bytes", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
