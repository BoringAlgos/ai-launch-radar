# AI Launch Radar

Live dashboard of new AI launches: what shipped, the GitHub repo and stars, an implementation idea for each, its build status, and a JEV popularity score.

Data lives in `data/launches.json`; the dashboard is a single static `index.html` that fetches it (relative path, with a raw.githubusercontent.com fallback). Deployed to Cloudflare Pages.

## Entry schema (data/launches.json)

- `id` — kebab-case slug
- `title`, `summary`
- `url` — canonical link (tracking params stripped, lowercase host)
- `source` — `x` | `github` | `article` | `manual`
- `source_url` — where it was found
- `github_repo` — `owner/repo` or null
- `github_stars` — int
- `jev_score` — 0.0 to 1.0, popularity from JEV
- `implementation_idea` — free text
- `implementation_status` — `idea` | `approved` | `building` | `shipped`
- `added_by` — `muse` | `instinct`
- `added_at` — ISO date
- `tags` — array of strings

## Adding entries (Muse and Instinct)

Both Muse and Instinct add launches through `scripts/ingest.py` (`add_launch()`), or by following its rules manually.

Dedupe rule: the key is the GitHub repo full name when present, otherwise the canonical URL. Duplicates are skipped, never merged blindly.

Star counts can be refreshed with `refresh_stars()` (public GitHub API, no auth). Popularity scores come from JEV via the workspace TypeSafe skill (`score_popularity()`).

## Roadmap

- Digest dashboard tab (the Morning Audio Digest, fed from the same repo)
- X + article ingestion crons writing through `add_launch()`
