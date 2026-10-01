# Paste this to Muse

Everything below runs on your machine, where the Secure Vault, JEV and the cron
folder live. It covers: (A) the one-off data fix, (B) the cron prompt fix,
(C) installing the new crons, (D) DNS for freshweights.com. Do them in order.
Report each step's result in one line. Stop and report on any failure; don't
loop.

Repo: BoringAlgos/ai-launch-radar. The new scripts are on branch
`claude/elegant-tesla-9aj3au` until it's merged; download from that branch
for A–C.

---

## A. One-off data fix (on main)

1. Download from **main**: `data/launches.json`, `data/archive.json`,
   `data/ideas.json` (record each blob sha). From the branch, download
   `scripts/apply_data_fix.py` and `data-fixes/2026-10-01-public-use-cases.json`.
2. Run `python3 scripts/apply_data_fix.py data-fixes/2026-10-01-public-use-cases.json --data-dir data`.
   Expected: 36 `implementation_idea` rewritten, 2 `build_with` ids repaired.
3. PUT each changed file back to **main** with its sha; verify each PUT.
   Commit message: `data: public use cases for 36 entries, repair 2 idea links`.

## B. Fix the source: the daily run's prompt

In each of the four daily cron bodies
(`ai-launch-radar-{0800,1400,1800,2200}__daily@….md`), replace the
instruction that writes `implementation_idea` with this text:

> `implementation_idea`: ONE generic, public use case, 1–2 sentences: who (a
> team, a startup, an indie developer, a researcher) could build what with
> this launch, and the concrete result. Write it for strangers reading a
> public website. Never mention the radar, the dashboard, BoringAlgos,
> Hermes, VoyageBliss, Instinct, Muse, Jev calls, the trading system,
> droplets, brokers we use, our crons, or any of our own projects. Don't
> write in the first person ("we", "our", "my").

And add this check right before the Push step:

> Before pushing, check every entry you added or edited today. If its
> `implementation_idea` matches
> `/\b(the radar|radar's|boringalgos|builder agents?|radar-ingest|the dashboard|hermes|voyagebliss|our |we |jev calls|x-triage|droplet|upstox|zerodha|four-seat)\b/i`,
> rewrite it under the rule above. Also check that every `build_with[].entry_id`
> in ideas you added is a real id in launches.json or archive.json.

## C. Install the new crons

The drafts are in `docs/crons/` on the branch. Create these files in
`~/workspace/goals/ai-launch-radar-dashboard/crons/`, using the drafts' bodies
and the same style as the existing cron files:

| Create | Body from | Notes |
|---|---|---|
| `daily/ai-launch-radar-spotlight-debate__daily@08:45:00.md` | `docs/crons/daily/ai-launch-radar-spotlight-debate__daily@08:45.md` | needs OPENROUTER_API_KEY from the vault for the one command |
| `daily/ai-launch-radar-idea-debate__daily@11:00:00.md` | `docs/crons/weekly/ai-launch-radar-debate__weekly@Fri-11:00.md` | first line of the body: "Run only on Fridays (IST); otherwise exit silently." |
| `daily/ai-launch-radar-digest-weekly__daily@08:30:00.md` | `docs/crons/weekly/ai-launch-radar-digest__weekly@Sat-08:30.md` | first line: "Run only on Saturdays (IST); otherwise exit silently." Needs KIT_API_KEY (+ KIT_TEMPLATE_ID) |
| `daily/ai-launch-radar-digest-monthly__daily@09:00:00.md` | `docs/crons/monthly/ai-launch-radar-digest__monthly@first-Sun-09:00.md` | first line: "Run only on a Sunday whose day of month is 1–7 (IST); otherwise exit silently." |
| edit `daily/ai-launch-radar-2200__daily@22:00:00.md` | `docs/crons/daily/seo-step-for-2200-run.md` | add the SEO step before Push |
| edit `hourly/radar-spotlight__interval@1h.md` | `docs/crons/daily/hourly-spotlight-change.md` | skip the PUT when the script says the debate pick is current |

If your scheduler supports weekly/monthly schedules natively, use them instead
of the weekday guards. Add the Kit key to the vault as `custom.kit` (and
the template id as `custom.kit_template`) once Anirban creates them.

## D. DNS for freshweights.com (Cloudflare)

The `custom.cloudflare` token currently gets a 403 on DNS. Ask Anirban to add
**Zone → DNS → Edit** for `freshweights.com` and `voyagebliss.in` to it (or
do the records in the dashboard). Then, with `skills/cloudflare/bin/cf.py`:

1. Zone `freshweights.com`, all records **DNS only** (grey cloud):
   - A `@` → 185.199.108.153, 185.199.109.153, 185.199.110.153, 185.199.111.153
   - AAAA `@` → 2606:50c0:8000::153, 2606:50c0:8001::153, 2606:50c0:8002::153, 2606:50c0:8003::153
   - CNAME `www` → `boringalgos.github.io`
2. Verify: `dig +short freshweights.com` returns the four 185.199.x.153 IPs.
3. Don't create the voyagebliss.in redirect yet. It goes live only after the
   branch is merged and the HTTPS certificate exists (docs/DOMAIN_MIGRATION.md,
   step 5).

Report back when D.2 passes; the GitHub side (Pages source, custom domain,
merge, Enforce HTTPS, redirect) follows from there.
