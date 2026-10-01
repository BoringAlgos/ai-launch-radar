# Weekly digest (Monday 07:30 IST)

Goal: a draft Kit broadcast for last week (Spotlights + debated ideas) and a
snapshot in data/digests/ that the website publishes at /weekly/<week>/.

1. Clone with enough history for the Spotlight log (public repo, no token
   needed): `git clone --filter=blob:none --shallow-since="40 days ago" https://github.com/BoringAlgos/ai-launch-radar.git /tmp/radar-digest`
   (the script falls back to the GitHub API if history is missing).
2. In that clone, with KIT_API_KEY and KIT_TEMPLATE_ID injected from the Secure
   Vault for this one command:
   `python3 scripts/digest.py weekly --kit-draft --out-dir /tmp/radar-digest/out`
3. PUT the new `data/digests/weekly-YYYY-Www.json` (a new file, so no sha) and
   verify it. This publishes the web version on the next site build.
4. Message Anirban: subject line, the Kit draft id, and the preview path. **Do
   not send or schedule the broadcast.** A human presses send in Kit.

If the debate didn't run, the digest falls back to the week's top daily ideas
and records `"ideas_source": "daily-fallback"` in the snapshot. Mention that in
the message.
