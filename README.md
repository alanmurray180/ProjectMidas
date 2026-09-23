# ProjectMidas

Gold Dashboard

## Running locally

```bash
pip install -e .
cp .env.example .env   # then fill in your keys
python -m midas.app    # http://localhost:5000
```

The Flask app serves both range variants from `/` via `?range=30d` / `?range=12m`,
and the weekly COT history from `/cot_history.csv`.

Tests cover the positioning maths and the panel that renders it:

```bash
pip install -e ".[dev]"
pytest
```

## CFTC positioning (COT)

The COT card splits open interest into contracts by side — long, short,
spread and net for each of the five trader categories, each as a share of
open interest — and then measures how those numbers are moving: the change
in the long leg, the short leg and the net over 1, 4, 13 and 52 weekly
reports, plus where managed-money net sits in its own 52-week range as a
percentile.

That crowding percentile is measured on managed-money net **as a share of
open interest**, not in contracts: open interest is not constant across a
year, so a contract percentile drifts to "crowded" whenever the market
grows, whoever is holding it. The bands are the 20th and 80th percentile,
giving three states — Crowded long, Mid-range, Washed out. Those came out
of `scripts/cot_backtest.py`, which scores candidate COT signals against
forward gold returns on 260 reports: the share-of-open-interest reading was
the only candidate that separated at all, and of the thresholds swept,
20/80 held up where 10/90 fired too rarely to trust and 30/70 left no
effect. The label describes the crowd, not a recommendation — a crowded
long is where the fuel for a liquidation sits, not a sell signal.

The long and short legs are shown separately on purpose. A net that rises
because shorts covered is a different market from one that rises because
longs were added, and the net figure alone cannot tell them apart.

Both CFTC reports are shown, one table each: **futures only**, the cleaner
read on outright speculative positioning and the basis most commentary
quotes, and **options and futures combined**, which folds in delta-adjusted
options and is what the CFTC's own published combined tables show. They
are not roundings of each other — on 15 September 2026 open interest read
409,899 against 577,454 — so neither figure appears without naming its
dataset. The charts, the 52-week percentile and the one-pager column all
use futures only, and say so.

History is fetched, not accumulated: the CFTC publishes five years of
weekly reports, so the trend is right on the first run rather than a year
from now. `dist/cot_history.csv` carries the full weekly series — every
category, every leg, both datasets, tagged with a `dataset` column — for
use in a spreadsheet, and the card links to it.

Field names are the trap here, and they are not guessable: `prod_merc` and
`other_rept` carry no `_all` suffix while `swap`, `m_money` long/short and
`nonrept` do, `m_money` spread has none either, and the swap columns carry
a doubled underscore. A name that does not match parses as zero, which on
screen is a category holding no position rather than an error — so every
snapshot is checked against the invariant that gross long equals gross
short, and a parse that fails it degrades the panel instead of publishing.
`scripts/cot_probe.py` resolves the mapping against a live row from both
datasets and runs in the **Check data sources** workflow.

The one-pager carries the condensed version — managed-money net, its
crowding label, and the net change over 1, 4 and 13 weeks — so positioning
is readable without scrolling to the card.

Two caveats worth remembering when reading the card:

- Positions are as at **Tuesday's close** and published the **following
  Friday**, so the freshest report is three to ten days old, and the trend
  numbers only move on Fridays.
- Several gold contracts (full-size, micro) answer a loose name search.
  The series asks for the full-size COMEX contract by its contract market
  code, then by its exact name; only if both fail does it fall back to a
  name search and de-duplicate what comes back, keeping the largest
  contract per week. The card names the contract it is showing, and the
  health report flags a run served by a fallback filter.

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
