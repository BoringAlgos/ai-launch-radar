# Daily Spotlight debate (08:45 IST, after the 08:00 run)

Goal: today's 4 Spotlight picks, chosen by the five-model panel, written to
data/spotlight.json. The website and the Saturday newsletter both show these
picks.

1. Download `scripts/spotlight_debate.py`, `scripts/jevlib.py`,
   `scripts/visuals.py`, `site.config.json`, `data/launches.json` and
   `data/spotlight.json` (record its blob sha).
2. Run, with OPENROUTER_API_KEY injected from the Secure Vault for this one
   command:
   `python3 scripts/spotlight_debate.py --launches data/launches.json --out data/spotlight.json`
   - "today's pick already exists" or "budget guard": stop silently.
   - Exit 1: report the printed reason in one line. The hourly picker keeps
     the Spotlight fresh in the meantime.
3. If `data/spotlight.json` changed, PUT it with the sha from step 1 (on a sha
   mismatch, re-download and PUT once more: the hourly job writes identical
   content while a debate pick is fresh). Verify the PUT.

Cost: about $0.10 a run, capped at $0.30 (`spotlight.max_run_cost_usd`).
