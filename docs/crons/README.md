# Cron drafts for the new processes

Copy these into `~/workspace/goals/ai-launch-radar-dashboard/crons/` and adjust
the wording to match the existing cron files. I couldn't see those files from
the repo. Each draft follows the same pattern as the daily runs: download what
you need, run a script, PUT only the files it changed, verify each PUT.

| File | When (IST) | Writes |
|---|---|---|
| `weekly/ai-launch-radar-debate__weekly@Fri-11:00.md` | Friday 11:00, between the 08:00 and 14:00 runs | `data/ideas.json` (+3 ideas) |
| `weekly/ai-launch-radar-digest__weekly@Sat-08:30.md` | Saturday 08:30 (issue covers Sat–Fri) | `data/digests/weekly-YYYY-Www.json`, Kit draft |
| `monthly/ai-launch-radar-digest__monthly@first-Sun-09:00.md` | First Sunday of the month, 09:00 (covers the previous month) | `data/digests/monthly-YYYY-MM.json`, Kit draft |
| addition to `daily/ai-launch-radar-2200__daily@22:00:00.md` | daily 22:00 | `data/seo.json` |
| `daily/ai-launch-radar-spotlight-debate__daily@08:45.md` | daily 08:45 | `data/spotlight.json` (debate picks, shared by site and newsletter) |
| `daily/hourly-spotlight-change.md` | hourly (existing cron) | skip the PUT while a debate pick is current |

Secrets: `OPENROUTER_API_KEY` (debate) and `KIT_API_KEY` / `KIT_TEMPLATE_ID`
(digests) are read from the Secure Vault into the environment of the single
command that needs them. Never echo them, put them on a command line, or write
them to a file.

The daily idea step needs no change. The debate's ideas carry extra optional
fields (see `docs/SCHEMA.md`). When the daily run rewrites `data/ideas.json`,
it must keep any fields it doesn't recognise, especially `origin`, `week`,
`implementation_plan`, `beneficiaries`, `monetization` and `debate`.
