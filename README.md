# Fresh Weights: AI Launch Radar

A live, free radar of new AI launches at **https://freshweights.com**
(formerly dashboard.voyagebliss.in). Each launch has its GitHub repo and stars,
a JEV traction score, why it matters, a generic use case, real community
builds, a usability verdict and its build status. Ideas combine radar launches
into products you could build. The site feeds a weekly and a monthly newsletter
(Kit).

## How it fits together

| Piece | What | Runs where |
|---|---|---|
| `data/*.json` | launches, archive, ideas, spotlight (+ `seo.json`, `digests/`) | written by the crons and the Instinct Worker via the GitHub API |
| `scripts/ingest.py` | dedupe, 7-day freshness, star refresh, archiving | daily crons (unchanged) |
| `scripts/spotlight.py` | hourly Spotlight picker | hourly cron (unchanged) |
| `index.html` | the dashboard and landing page; reads `data/*.json` in the browser | GitHub Pages |
| `scripts/build_site.py` | static SEO pages (`/launch/`, `/idea/`, `/category/`, `/weekly/`), sitemap, RSS | GitHub Actions on every push (`.github/workflows/pages.yml`) |
| `scripts/seo_jev.py` | JEV picks each page's search title and description | the 22:00 daily run |
| `scripts/idea_debate.py` | 3 OpenRouter models debate the week's radar; JEV judges; top 3 ideas appended | weekly cron (Sunday) |
| `scripts/digest.py` | weekly and monthly newsletter, Kit draft broadcast, web archive snapshot | weekly and monthly crons |
| `scripts/jevlib.py` | shared budget guard, JEV call, OpenRouter, slugs | used by the new scripts only |
| `site.config.json` | brand, domain, Kit form id, debate models and caps | read by the site and the scripts |

Docs: `docs/DOMAIN_MIGRATION.md` (freshweights.com cutover),
`docs/SCHEMA.md` (optional new idea fields and new files; the launch schema is
unchanged), `docs/crons/` (cron drafts), `newsletter/README.md` (Kit setup).

## Launch entry schema (data/launches.json): unchanged

`id, title, summary, usp, url, source, source_url, github_repo, github_stars,
implementation_idea, implementation_status, tags, launched_at, kind,
category, subcategory, learn_url, learn_label, community_builds[],
implementation_repos[], usability, jev_score, added_by, added_at`. See the
docstring in `scripts/ingest.py` for each field.

`implementation_idea` is a **generic** use case: who could build what with the
launch. It never refers to BoringAlgos, Hermes, VoyageBliss, the radar itself
or any internal project.

## Rules that keep it running

- Freshness: only launches from the last 7 days go live. `kind: "trending"`
  bypasses the age check. After 7 days entries move to `data/archive.json`;
  nothing is ever deleted.
- Dedupe: by GitHub repo full name, else by canonical URL.
- JEV budget: skip paid calls if OpenRouter remaining < $2 or 24h spend > $1.
  At most 10 scoring calls per daily run, 1 per hourly Spotlight, 2 per SEO pass,
  1 judge call and a $0.50 cap per weekly debate.
- Credentials live in the Secure Vault and Cloudflare, never in this repo.

## Local preview

```
python3 scripts/build_site.py --out _site && python3 -m http.server -d _site 8000
```
