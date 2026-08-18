#!/usr/bin/env python3
"""Render the dashboard to a static site for GitHub Pages.

The Flask app serves both range variants from one route via ``?range=``.
Pages has no server, so each variant becomes its own file:

    dist/index.html   30-day view
    dist/12m.html     12-month view

Every upstream is fetched once per build, so the published site is as fresh
as the last successful workflow run.  Individual dead upstreams still render
their own error card (the ``_fetch_*`` helpers trap exceptions); a failure
here means the render itself broke, in which case we exit non-zero and let
the workflow skip deployment so Pages keeps serving the previous build.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Rendered as index.html so the 30-day view is the site root.
PAGES = {"30d": "index.html", "12m": "12m.html"}


def main(out_dir: Path) -> int:
    from midas.app import STATIC_LINKS, render_dashboard

    out_dir.mkdir(parents=True, exist_ok=True)

    for period, filename in PAGES.items():
        print(f"rendering {period} -> {filename}", flush=True)
        html = render_dashboard(period, links=STATIC_LINKS)
        (out_dir / filename).write_text(html, encoding="utf-8")

    # Stops GitHub Pages running the output through Jekyll.
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    for filename in PAGES.values():
        size = (out_dir / filename).stat().st_size
        print(f"  {filename}: {size:,} bytes", flush=True)

    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist")
    sys.exit(main(target))
