#!/usr/bin/env python3
"""Hourly Spotlight picker for the AI Launch Radar.

Reads data/launches.json, asks JEV (OpenRouter) to score recent entries on
three dimensions -- implementation use-case, popularity/traction, trend
momentum -- and writes the top 3-4 picks to data/spotlight.json, which the
dashboard renders as the "Spotlight" section.

Budget guards (same rules as the radar updater):
  - skip JEV when OpenRouter remaining budget < US$2 (via check-balance.py)
  - skip JEV when tracked 24h spend > US$1 (via summary.py)
On skip/failure the picker falls back to a deterministic ranking
(JEV traction score, then recency) and marks picked_by="fallback".

Usage:
  python3 scripts/spotlight.py --launches data/launches.json \
      --out data/spotlight.json

The script never touches credentials: jev-tracked.py reads the OpenRouter
key from the Secure Vault itself.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CANDIDATE_DAYS = 7
MAX_CANDIDATES = 12
N_PICKS = 4
WEIGHTS = {"usecase": 0.4, "popularity": 0.3, "trend": 0.3}
DIM_REASONS = {
    "usecase": "Strongest build idea in the feed",
    "popularity": "Highest traction right now",
    "trend": "Hottest trend momentum",
}
JEV_TRACKED = os.path.expanduser("~/workspace/jev-costs/jev-tracked.py")
CHECK_BALANCE = os.path.expanduser("~/workspace/jev-costs/check-balance.py")
SUMMARY = os.path.expanduser("~/workspace/jev-costs/summary.py")


def budget_ok():
    """Fail closed: any parse problem means skip JEV."""
    try:
        out = subprocess.run(["python3", CHECK_BALANCE], capture_output=True,
                             text=True, timeout=60).stdout
        m = re.search(r"Remaining:\s*\$\s*([\d.]+)", out)
        if not m or float(m.group(1)) < 2.0:
            print("budget guard: remaining < $2, skipping JEV")
            return False
        out = subprocess.run(["python3", SUMMARY], capture_output=True,
                             text=True, timeout=60).stdout
        m = re.search(r"Last 24h:\s*\$\s*([\d.]+)", out)
        if not m or float(m.group(1)) > 1.0:
            print("budget guard: 24h spend > $1, skipping JEV")
            return False
        return True
    except Exception as e:
        print("budget guard: check failed (%s), skipping JEV" % e)
        return False


def parse_added_at(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load_candidates(path, max_candidates=MAX_CANDIDATES):
    with open(path) as f:
        data = json.load(f)
    launches = data.get("launches", [])
    today = datetime.now(IST).date()
    fresh = []
    for e in launches:
        d = parse_added_at(e.get("added_at"))
        if d and (today - d) <= timedelta(days=CANDIDATE_DAYS):
            fresh.append(e)
    scored = [e for e in fresh if e.get("jev_score") is not None]
    unscored = [e for e in fresh if e.get("jev_score") is None]
    scored.sort(key=lambda e: e["jev_score"], reverse=True)
    unscored.sort(key=lambda e: str(e.get("added_at", "")), reverse=True)
    return (scored + unscored)[:max_candidates]


def jev_dimensions(cands):
    """One JEV call, three noul questions per candidate. Returns
    {entry_id: {"usecase": f, "popularity": f, "trend": f}} or None."""
    today = datetime.now(IST).date()
    state_cands = []
    questions = {}
    for e in cands:
        eid = e["id"]
        d = parse_added_at(e.get("added_at"))
        age = (today - d).days if d else 99
        state_cands.append({
            "id": eid,
            "title": e.get("title", ""),
            "summary": (e.get("summary") or "")[:300],
            "usp": (e.get("usp") or "")[:300],
            "category": e.get("category", ""),
            "traction": e.get("jev_score"),
            "stars": e.get("github_stars", 0),
            "source": e.get("source", ""),
            "implementation_idea": (e.get("implementation_idea") or "")[:300],
            "added_at": e.get("added_at", ""),
            "age_days": age,
            "kind": e.get("kind", "launch"),
        })
        t = e.get("title", eid)
        idea = (e.get("implementation_idea") or "none given")[:300]
        questions["%s__usecase" % eid] = {
            "type": "noul",
            "instructions": (
                "How strong is the implementation use-case of '%s' for an "
                "indie AI builder? Proposed implementation idea: '%s'. "
                "Consider concreteness and buildability. Ignore hype."
                % (t, idea)),
        }
        questions["%s__popularity" % eid] = {
            "type": "noul",
            "instructions": (
                "How much real traction/popularity does '%s' have? "
                "JEV traction=%s, GitHub stars=%s, source=%s. "
                "Ignore hype and marketing."
                % (t, e.get("jev_score"), e.get("github_stars", 0),
                   e.get("source", ""))),
        }
        questions["%s__trend" % eid] = {
            "type": "noul",
            "instructions": (
                "How strong is the trend momentum of '%s'? Added %s "
                "(%d days ago), kind=%s. Consider novelty and timeliness. "
                "Ignore hype." % (t, e.get("added_at", "?"), age,
                                  e.get("kind", "launch"))),
        }
    cmd = ["python3", JEV_TRACKED, "--caller", "radar-spotlight",
           "--provider", "openrouter",
           "--state", json.dumps({"candidates": state_cands}),
           "--questions-json", json.dumps(questions)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            print("jev call failed: %s" % proc.stderr.strip()[:200])
            return None
        answers = json.loads(proc.stdout).get("answers", {})
    except Exception as e:
        print("jev call error: %s" % e)
        return None
    dims = {}
    for e in cands:
        eid = e["id"]
        row = {}
        ok = True
        for dim in ("usecase", "popularity", "trend"):
            try:
                row[dim] = float(
                    answers["%s__%s" % (eid, dim)]["noul"])
            except (KeyError, TypeError, ValueError):
                ok = False
                break
        if ok:
            dims[eid] = row
    if len(dims) < 3:
        print("jev returned too few complete scores (%d)" % len(dims))
        return None
    return dims


def composite(row):
    return sum(row[d] * WEIGHTS[d] for d in WEIGHTS)


def build_picks(cands, dims, n_picks=N_PICKS):
    picks = []
    if dims:
        ranked = sorted(
            (e for e in cands if e["id"] in dims),
            key=lambda e: composite(dims[e["id"]]), reverse=True)
        for e in ranked[:n_picks]:
            row = dims[e["id"]]
            best = max(WEIGHTS, key=lambda d: row[d])
            picks.append({
                "id": e["id"],
                "title": e.get("title", ""),
                "url": e.get("url", ""),
                "summary": e.get("summary", ""),
                "implementation_idea": e.get("implementation_idea", ""),
                "category": e.get("category", ""),
                "jev_score": e.get("jev_score"),
                "added_by": e.get("added_by", ""),
                "reason": DIM_REASONS[best],
                "composite": round(composite(row), 2),
                "dimensions": {d: round(row[d], 2) for d in row},
            })
        return picks, "jev"
    # deterministic fallback: traction, then recency
    ranked = sorted(
        cands,
        key=lambda e: (e.get("jev_score") or 0, str(e.get("added_at", ""))),
        reverse=True)
    for e in ranked[:n_picks]:
        picks.append({
            "id": e["id"],
            "title": e.get("title", ""),
            "url": e.get("url", ""),
            "summary": e.get("summary", ""),
            "implementation_idea": e.get("implementation_idea", ""),
            "category": e.get("category", ""),
            "jev_score": e.get("jev_score"),
            "added_by": e.get("added_by", ""),
            "reason": "Top JEV traction",
            "composite": None,
            "dimensions": None,
        })
    return picks, "fallback"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--launches", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-candidates", type=int, default=MAX_CANDIDATES)
    ap.add_argument("--picks", type=int, default=N_PICKS)
    args = ap.parse_args()

    cands = load_candidates(args.launches, args.max_candidates)
    if not cands:
        print("no fresh candidates; spotlight left unchanged")
        return 0
    dims = jev_dimensions(cands) if budget_ok() else None
    picks, method = build_picks(cands, dims, args.picks)
    out = {
        "updated_at": datetime.now(IST).isoformat(timespec="minutes"),
        "picked_by": method,
        "picks": picks,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print("spotlight: %d picks via %s -> %s" % (len(picks), method, args.out))
    for p in picks:
        print("  - %s (%s)" % (p["title"][:60], p["reason"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
