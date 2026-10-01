# Weekly idea debate (Friday 11:00 IST)

Goal: 3 debated product ideas for this ISO week, appended to data/ideas.json
in time for Saturday's issue. Five models argue (GPT-6 Sol, Claude Sonnet 5.5,
MiMo-V2.6-Pro, Qwen3.8 Max, Kimi K2.5; see site.config.json) and JEV judges.

1. Download `scripts/idea_debate.py`, `scripts/jevlib.py`, `site.config.json`,
   `data/launches.json`, `data/archive.json` and `data/ideas.json` from
   BoringAlgos/ai-launch-radar (main), keeping the same layout. Record the blob
   sha of `data/ideas.json`.
2. Run, with OPENROUTER_API_KEY injected from the Secure Vault (custom.openrouter)
   for this one command:
   `python3 scripts/idea_debate.py --launches data/launches.json --archive data/archive.json --ideas data/ideas.json`
   - Exit 0 with "already has debate ideas", "budget guard" or "too few entries":
     stop silently. Nothing to push.
   - Exit 1: report the printed reason in one line (for a missing model, include
     the close matches it printed). Don't retry in a loop.
3. If `data/ideas.json` changed, PUT it with the sha from step 1. On a 409/422
   sha mismatch (a daily run pushed in the meantime), don't re-run the debate,
   because that spends again. Re-download ideas.json, append the 3 new ideas
   (`origin == "weekly-debate"` and this week's `week`) from the local file,
   apply the max-60 rule (drop the oldest by added_at), and PUT once more.
4. Verify the PUT. Report: the 3 titles, JEV scores, total debate cost (the
   script prints it).

Cost: about $0.75 per run with the five default models (docs/COSTS.md); hard
cap $1.50 in site.config.json (`debate.max_run_cost_usd`). Plus 1 JEV call
(`--caller radar-debate-judge`).
