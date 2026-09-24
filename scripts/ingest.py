"""
Ingestion helper for the AI Launch Radar (BoringAlgos/ai-launch-radar).

Both Muse and Instinct add launches through this module (or by following the
same rules), so every entry lands in data/launches.json in the same shape and
duplicates never get in twice.

Entry schema:
  id                 kebab-case slug
  title              display title
  summary            one-line summary
  url                canonical link (see canonical_url)
  source             "x" | "github" | "article" | "manual"
  source_url         where it was found (X post, release page, article, ...)
  github_repo        "owner/repo" or None
  github_stars       int
  jev_score          0.0 - 1.0, popularity score from JEV
  implementation_idea  free text: an idea that can be built with this launch
  implementation_status "idea" | "approved" | "building" | "shipped"
  added_by           "muse" | "instinct"
  added_at           ISO date (YYYY-MM-DD)
  launched_at        ISO date the tool actually launched (YYYY-MM-DD)
  tags               list of strings

Dedupe rule: entry_key() is the github_repo full name when present, otherwise
the canonical URL. add_launch() skips any entry whose key is already stored.

Freshness rule: only launches from the last MAX_LAUNCH_AGE_DAYS days are
accepted. add_launch() rejects anything older (checked against launched_at,
falling back to added_at).
"""

import json
import os
import re
import subprocess
from datetime import date
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "launches.json")

# Freshness rule: never add a launch older than this many days.
MAX_LAUNCH_AGE_DAYS = 7

# Tracking params stripped by canonical_url().
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src",
    "src", "s", "t", "si",
}


def slugify(text):
    """Turn a title into a kebab-case slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "launch"


def canonical_url(url):
    """Normalize a URL: lowercase scheme/host, drop tracking params and fragments."""
    if not url:
        return url
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k not in _TRACKING_PARAMS]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((scheme, host, path, "", urlencode(query), ""))


def entry_key(entry):
    """Dedupe key: repo full name wins, otherwise the canonical URL."""
    repo = entry.get("github_repo")
    if repo:
        return "repo:" + repo.strip().lower()
    return "url:" + canonical_url(entry.get("url", ""))


def load_data():
    """Load the launches file; return {"updated_at": ..., "launches": [...]}."""
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("updated_at", "")
    data.setdefault("launches", [])
    return data


def save_data(data):
    """Write the launches file back, pretty-printed."""
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _launch_date(entry):
    """The date the tool launched: launched_at, falling back to added_at."""
    for field in ("launched_at", "added_at"):
        value = entry.get(field)
        if not value:
            continue
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            continue
    return None


def add_launch(entry):
    """
    Add one launch entry. Dedupes on entry_key() and enforces the freshness
    rule (rejects launches older than MAX_LAUNCH_AGE_DAYS).
    Returns True if added, False if skipped (duplicate or too old).
    """
    data = load_data()
    existing = {entry_key(e) for e in data["launches"]}
    key = entry_key(entry)
    if key in existing:
        print("skipped (duplicate): {}".format(entry.get("title")))
        return False
    launch_date = _launch_date(entry)
    if launch_date and (date.today() - launch_date).days > MAX_LAUNCH_AGE_DAYS:
        print("skipped (older than {} days): {}".format(
            MAX_LAUNCH_AGE_DAYS, entry.get("title")))
        return False
    entry = dict(entry)
    entry["id"] = entry.get("id") or slugify(entry.get("title", ""))
    entry["url"] = canonical_url(entry.get("url", ""))
    today = date.today().isoformat()
    entry["added_at"] = entry.get("added_at") or today
    entry["launched_at"] = entry.get("launched_at") or entry["added_at"]
    data["launches"].append(entry)
    save_data(data)
    return True


def refresh_stars():
    """
    Update github_stars for every entry with a github_repo, using the public
    GitHub API (no auth needed, 60 requests/hour).
    """
    import urllib.request

    data = load_data()
    for entry in data["launches"]:
        repo = entry.get("github_repo")
        if not repo:
            continue
        req = urllib.request.Request(
            "https://api.github.com/repos/" + repo,
            headers={"User-Agent": "ai-launch-radar", "Accept": "application/vnd.github+json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                info = json.load(resp)
            entry["github_stars"] = int(info.get("stargazers_count", entry.get("github_stars", 0)))
        except Exception as exc:  # network/rate-limit hiccups: keep the old count
            print("star refresh failed for {}: {}".format(repo, exc))
    save_data(data)


def score_popularity(entry):
    """
    Score a launch's popularity with JEV via the workspace TypeSafe skill.

    The skill reads the OpenRouter API key from the Secure Vault itself, so no
    credentials ever appear here or on the command line. Exact invocation:

        python3 ~/workspace/skills/typesafe/bin/jev.py \
            --provider openrouter \
            --state '{"title": "...", "summary": "...", "source": "github", "stars": 45200}' \
            --question '{"popularity": {"type": "noul",
                          "instructions": "How popular and noteworthy is this AI launch for a technical, AI-building audience? Consider the launch significance, GitHub stars, and source credibility. Ignore hype and marketing."}}'

    The response carries the noul value in .decisions.popularity.value (float
    0.0-1.0). In production, parse that value and assign it to jev_score.
    This stub shows the wiring; it does not call the API itself.
    """
    state = {
        "title": entry.get("title", ""),
        "summary": entry.get("summary", ""),
        "source": entry.get("source", ""),
        "stars": entry.get("github_stars", 0),
    }
    cmd = [
        "python3", os.path.expanduser("~/workspace/skills/typesafe/bin/jev.py"),
        "--provider", "openrouter",
        "--state", json.dumps(state),
        "--question", json.dumps({
            "popularity": {
                "type": "noul",
                "instructions": ("How popular and noteworthy is this AI launch for a technical, "
                                "AI-building audience? Consider the launch significance, GitHub "
                                "stars, and source credibility. Ignore hype and marketing."),
            }
        }),
    ]
    print("score_popularity would run:", " ".join(cmd))
    # TODO: run subprocess and set entry["jev_score"] from .decisions.popularity.value
    return None


if __name__ == "__main__":
    demo = {
        "title": "Anthropic Claude 4.5",
        "summary": "Latest frontier model release with stronger agentic coding.",
        "url": "https://www.anthropic.com/news/claude-4-5?utm_source=x",
        "source": "article",
        "source_url": "https://www.anthropic.com/news/claude-4-5",
        "github_repo": None,
        "github_stars": 0,
        "jev_score": 0.88,
        "implementation_idea": "Benchmark it as the reviewer agent in the Hermes pipeline.",
        "implementation_status": "idea",
        "added_by": "muse",
        "added_at": "2026-09-24",
        "tags": ["models", "anthropic"],
    }
    added = add_launch(demo)
    print("demo entry added:" if added else "demo entry skipped (duplicate)")
