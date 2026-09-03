# ProjectMidas

Gold Dashboard

## Running locally

```bash
pip install -e .
cp .env.example .env   # then fill in your keys
python -m midas.app    # http://localhost:5000
```

The Flask app serves both range variants from `/` via `?range=30d` / `?range=12m`.

## Deployment (GitHub Pages)

The dashboard is published as a static site. `scripts/build_static.py` fetches
every upstream once and renders both variants to `dist/`:

```bash
python scripts/build_static.py dist
```

`.github/workflows/pages.yml` runs that build and deploys it, hourly on UK
working days. Actions cron is UTC-only with no DST handling, so the schedule
covers the union of the BST and GMT windows — one edge slot therefore falls
outside 08:30–17:30 depending on the offset in effect, and is allowed to build
anyway.

**Do not rely on that schedule.** GitHub delivered 1–3 of 11 slots a day and
never once before 13:00 UK, sometimes hours after the window had closed. The
real schedule is a Cloudflare Worker in [`worker/`](worker/README.md), which
calls `workflow_dispatch` at 08:30–17:30 UK on working days; that mechanism has
succeeded on every attempt. The `schedule:` trigger stays in the workflow as a
free backstop — duplicate builds are harmless, since the `concurrency` group
serialises them.

Every delivered slot builds whenever it arrives, and the page footer reports
the true age of the data.

The published page is only as fresh as the last successful run. If a build
fails, or too many upstreams are unavailable, deployment is skipped and Pages
keeps serving the previous version. `scripts/build_static.py --check` reports
which sources are live without writing anything, and the **Check data
sources** workflow runs that probe on any branch.

### One-time setup

1. **Settings → Pages → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions**, add:
   - `METALPRICEAPI_KEY`
   - `FRED_API_KEY`
   - `FINANCIAL_ANALYSIS_COT` (optional — raises the CFTC rate limit)

Note that scheduled workflows are disabled automatically after 60 days without
repository activity, and Pages deployments do not count as activity.
