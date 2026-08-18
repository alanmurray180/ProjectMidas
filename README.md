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
working days between 08:30 and 17:30. Because Actions cron is UTC-only with no
DST handling, the schedule covers the union of the BST and GMT windows and a
guard job drops whichever end is an hour out for the offset currently in
effect — so the cron never needs editing twice a year.

The published page is only as fresh as the last successful run. If a build
fails, deployment is skipped and Pages keeps serving the previous version.

### One-time setup

1. **Settings → Pages → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions**, add:
   - `METALPRICEAPI_KEY`
   - `FRED_API_KEY`
   - `FINANCIAL_ANALYSIS_COT` (optional — raises the CFTC rate limit)

Note that scheduled workflows are disabled automatically after 60 days without
repository activity, and Pages deployments do not count as activity.
