/**
 * Cloudflare Worker that triggers the dashboard rebuild on a schedule.
 *
 * GitHub's own cron proved unusable for this repo: across four consecutive
 * weekdays it delivered 1-3 of 11 scheduled slots, never once before 13:00
 * UK, and sometimes hours after the cron window had closed.  workflow_dispatch,
 * by contrast, has succeeded on every attempt.  So the schedule lives here and
 * GitHub is only asked to run the build.
 *
 * Cloudflare cron is also UTC-only, so the trigger covers the union of the BST
 * and GMT windows and this Worker trims whichever edge slot is an hour out for
 * the offset currently in effect.  That is safe here in a way it was not
 * inside the GitHub workflow: Cloudflare fires punctually, so a gate rejects
 * only the slot it is meant to, not a genuine refresh that arrived late.
 */

// Inclusive London hours the dashboard should rebuild in: 08:30 to 17:30.
export const FIRST_HOUR = 8;
export const LAST_HOUR = 17;

/**
 * Weekday and hour in London, so BST and GMT need no special handling.
 */
export function londonParts(date) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London",
    weekday: "short",
    hour: "2-digit",
    hour12: false,
  }).formatToParts(date);

  const got = {};
  for (const part of parts) got[part.type] = part.value;
  return { weekday: got.weekday, hour: parseInt(got.hour, 10) % 24 };
}

/**
 * True when *date* falls in the UK working-day window we publish for.
 */
export function shouldRun(date) {
  const { weekday, hour } = londonParts(date);
  if (weekday === "Sat" || weekday === "Sun") return false;
  return hour >= FIRST_HOUR && hour <= LAST_HOUR;
}

/**
 * Ask GitHub to run the build workflow.  Returns a plain result object rather
 * than throwing so both entry points can report it the same way.
 */
export async function dispatchWorkflow(env, reason) {
  const owner = env.GITHUB_OWNER;
  const repo = env.GITHUB_REPO;
  const workflow = env.WORKFLOW_FILE;
  const url =
    `https://api.github.com/repos/${owner}/${repo}` +
    `/actions/workflows/${workflow}/dispatches`;

  if (!env.GITHUB_TOKEN) {
    return { ok: false, status: 0, detail: "GITHUB_TOKEN secret is not set" };
  }

  const resp = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      // GitHub rejects API requests without a User-Agent.
      "User-Agent": `${repo}-refresh-worker`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: env.GIT_REF || "main" }),
  });

  // A successful dispatch is 204 with an empty body.
  if (resp.status === 204) {
    console.log(`dispatched ${workflow} (${reason})`);
    return { ok: true, status: 204, detail: "dispatched" };
  }

  const detail = (await resp.text()).slice(0, 500);
  console.log(`dispatch failed ${resp.status} (${reason}): ${detail}`);
  return { ok: false, status: resp.status, detail };
}

export default {
  async scheduled(event, env, ctx) {
    const now = new Date(event.scheduledTime);
    const { weekday, hour } = londonParts(now);

    if (!shouldRun(now)) {
      console.log(`skipped: ${weekday} ${hour}:xx London is outside the window`);
      return;
    }
    console.log(`firing: ${weekday} ${hour}:xx London`);
    ctx.waitUntil(dispatchWorkflow(env, "cron"));
  },

  /**
   * Manual trigger, for testing the Worker without waiting for the cron.
   * Requires the TRIGGER_KEY secret, so the endpoint being public does not
   * mean anyone can spend your Actions minutes.
   */
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("POST with X-Trigger-Key to rebuild\n", {
        status: 405,
      });
    }
    if (!env.TRIGGER_KEY || request.headers.get("X-Trigger-Key") !== env.TRIGGER_KEY) {
      return new Response("unauthorised\n", { status: 401 });
    }

    const result = await dispatchWorkflow(env, "manual");
    return new Response(JSON.stringify(result, null, 2) + "\n", {
      status: result.ok ? 202 : 502,
      headers: { "Content-Type": "application/json" },
    });
  },
};
