# Dashboard refresh Worker

A Cloudflare Worker that triggers the dashboard rebuild on a schedule.

## Why this exists

GitHub's own `schedule` trigger proved unusable for this repository. Across
four consecutive weekdays it delivered **1–3 of 11** scheduled slots, **never
once before 13:00 UK**, and sometimes hours after the cron window had closed:

```
Mon 31 Aug   2 runs   16:10, 21:52          none before 12:00
Tue 01 Sep   3 runs   13:36, 18:20, 20:58   none before 12:00
Wed 02 Sep   3 runs   13:08, 17:40, 20:51   none before 12:00
Thu 03 Sep   1 run    13:08                 none before 12:00
```

The root cause was never established — the workflow is `active`, the repo is
public with zero billable minutes, and every run that did fire was green.
What *is* established is that `workflow_dispatch` has succeeded on every
single attempt. So the schedule lives here, and GitHub is only asked to run
the build.

The `schedule:` trigger is deliberately left in `pages.yml` as a free
backstop. Duplicate builds are harmless — the workflow's `concurrency` group
serialises them, and Actions is free on a public repo.

## What it does

Fires `30 7-17 * * 1-5` UTC — the union of the BST and GMT windows — and
trims whichever edge slot is an hour out for the offset currently in effect,
leaving exactly **ten rebuilds a working day, 08:30 to 17:30 UK**.

Gating inside the Worker is safe in a way it was not inside the GitHub
workflow: Cloudflare fires punctually, so the gate rejects only the slot it is
meant to, rather than discarding a genuine refresh that arrived late.

## Deploy

**1. A Cloudflare account.** The free plan is enough, and you do **not** need a
domain — the Worker is reachable on a `workers.dev` subdomain. Sign up at
<https://dash.cloudflare.com/sign-up>.

**2. Authenticate wrangler** on the machine you deploy from:

```bash
cd worker
npm install -g wrangler      # or use `npx wrangler` throughout
wrangler login               # opens a browser, stores a token locally
```

If the account has more than one Cloudflare account attached, wrangler will
ask which to use; add the chosen `account_id` to `wrangler.toml` to stop it
asking again. For CI instead of a laptop, skip `wrangler login` and set
`CLOUDFLARE_API_TOKEN` from an API token built on Cloudflare's **Edit
Cloudflare Workers** template.

**3. Deploy first**, so the Worker and its cron trigger exist:

```bash
wrangler deploy
```

Until step 5 it will run and log `GITHUB_TOKEN secret is not set` on each
firing rather than failing — harmless.

**4. Create a GitHub token.** A fine-grained personal access token at
<https://github.com/settings/personal-access-tokens/new>:

- Repository access: **only** `alanmurray180/ProjectMidas`
- Permissions → Repository → **Actions: Read and write**
- Nothing else. That permission is all `workflow_dispatch` needs.
- Note the expiry date — see *If it stops working* below.

**5. Store the secrets** (they never go in the repo, and take effect
immediately without redeploying):

```bash
wrangler secret put GITHUB_TOKEN     # paste the token
wrangler secret put TRIGGER_KEY      # optional: any long random string
```

`TRIGGER_KEY` is only for the manual endpoint. Leaving it unset does not open
the endpoint up — it makes it answer `401` to everything, which is the safe
default.

## Verify

Trigger it by hand without waiting for the cron — this is also the "rebuild
now" button that a public static page cannot safely have:

```bash
curl -X POST https://projectmidas-refresh.<your-subdomain>.workers.dev/ \
     -H "X-Trigger-Key: <your TRIGGER_KEY>"
```

Expect `202` and `{"ok": true, "status": 204, "detail": "dispatched"}`, then a
new run in the repo's Actions tab within seconds. Without the header you get
`401`, so the public URL cannot be used to spend your Actions minutes.

Watch scheduled firings live with `wrangler tail`. Each one logs either
`firing: Wed 09:xx London` or `skipped: Sat 10:xx London is outside the
window`.

## Cost

Cloudflare's free plan covers 100,000 Worker requests a day and cron triggers
are included. This uses eleven invocations a working day.

## If it stops working

- `401` or `403` from GitHub → the token expired or lacks **Actions: Read and
  write**. Fine-grained tokens expire; set a calendar reminder.
- `404` → the token cannot see the repo, or `WORKFLOW_FILE` is wrong.
- Nothing in `wrangler tail` at all → the cron trigger did not deploy. Check
  the Worker's Settings → Triggers in the Cloudflare dashboard.
