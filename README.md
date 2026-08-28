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

Do not count on the schedule being punctual. GitHub queues scheduled runs and
delivers them late, drops slots under load, and has gone a full day without
firing this one at all before resuming 7.5 hours behind. Every delivered slot
now builds whenever it arrives, and the page footer reports the true age of
the data. If the refresh time genuinely matters, trigger `workflow_dispatch`
from something always-on outside GitHub.

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
