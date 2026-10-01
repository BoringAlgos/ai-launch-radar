# Data schema: additions for the redesign, debate and digests

Nothing in `data/launches.json`, `data/archive.json` or `data/spotlight.json`
changes. The Instinct Worker (`radar-ingest`), `scripts/ingest.py` and
`scripts/spotlight.py` keep working without edits.

All additions below are **optional fields** or **new files**. Older readers
ignore them; the dashboard renders them when present.

## data/ideas.json: new optional idea fields

Existing fields (`id, title, outcome, problem, idea, why_now, collision,
differentiation, distribution, mvp, risks, repo_suggestion, build_with[],
difficulty, jev_score, tags, sources[], added_at`) are unchanged. The daily
cron keeps producing ideas exactly as before.

| Field | Type | Meaning |
|---|---|---|
| `origin` | `"daily"` \| `"weekly-debate"` | Which process produced the idea. Missing = `"daily"`. |
| `week` | `"YYYY-Www"` | ISO week of the debate (weekly-debate ideas only). |
| `implementation_plan` | `[{step, detail, effort}]` | Concrete build plan, 3–7 ordered steps. `effort` is a short estimate (`"1 day"`, `"1 week"`). |
| `beneficiaries` | `[{who, benefit}]` | Who gains and how: end users, buyers, operators. |
| `monetization` | `{model, pricing, wedge, channels[]}` | How it makes money: pricing model, a concrete price point, the first-customer wedge, distribution channels. |
| `debate` | `{models[], rounds, judge, dissent, scores{}}` | Provenance. `models` are the OpenRouter model ids that argued; `judge` is `"jev"`; `dissent` is the strongest surviving objection in one line; `scores` holds the JEV judge's sub-scores (`feasibility`, `demand`, `grounding`, `monetization`). |

Weekly-debate ideas also always fill the existing `outcome`, `collision`,
`differentiation`, `distribution`, `mvp`, `risks` fields.

Grounding rules (enforced in `scripts/idea_debate.py`):
- every `build_with[].entry_id` must be a real id in launches or archive;
- every `sources[].url` must either appear in radar data or resolve over HTTP;
  anything else is dropped before writing, and an idea left with no
  grounded `build_with` entries is rejected.

`ideas.json` keeps its max-60 rule (oldest dropped). Weekly-debate ideas are
additionally snapshotted in `data/digests/` so the monthly digest never loses
them to that cap.

## data/digests/ (new)

| File | Written by | Contents |
|---|---|---|
| `weekly-YYYY-Www.json` | `scripts/digest.py weekly` | `{kind, week, start, end, generated_at, spotlights[], ideas[], stats{}}` |
| `monthly-YYYY-MM.json` | `scripts/digest.py monthly` | `{kind, month, generated_at, spotlights[], idea, stats{}}` |

`spotlights[]` items are the launch entry fields plus `hours_in_spotlight`
(how many hourly refreshes the entry held a Spotlight slot that period, read
from the git history of `data/spotlight.json`).

## data/seo.json (new)

`{updated_at, entries: {<launch or idea id>: {title, description, scored_by}}}`.
Written by `scripts/seo_jev.py`; read by `scripts/build_site.py`. An entry with
no SEO record falls back to a deterministic title and description.
