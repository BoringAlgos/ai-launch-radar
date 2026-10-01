#!/usr/bin/env python3
"""Daily Spotlight, picked by the same five-model panel as the weekly ideas.

The website Spotlight and the newsletter Spotlight are the same picks: this
script writes data/spotlight.json once a day, the dashboard shows it, and the
weekly/monthly digests rank the launches the panel picked during the period.

Flow (one run a day, ~$0.10):
  1. Vote      each model (site.config.json -> debate.models) ranks its top 4
               from this week's launches and writes, for each, a plain-English
               one-line gist, an analogy and why it matters now.
  2. Tally     Borda count (4/3/2/1 points), shortlist the top 6.
  3. Judge     one JEV call scores the shortlist on use case, traction and
               momentum (the same three dimensions as scripts/spotlight.py)
               and scores every model's gist and analogy for clarity to a
               non-technical reader. Final = 50% votes + 50% JEV; the clearest
               wording wins.
  4. Visuals   one JEV call picks each card's icon, number and colour
               (scripts/visuals.py). The PNGs are rendered at site build.

If JEV fails, picks fall back to votes alone (marked "votes-only"). If fewer
than 2 models answer, nothing is written and the hourly JEV picker
(scripts/spotlight.py) keeps the Spotlight fresh instead; it defers to a
debate pick less than 26 hours old.

Usage:
  python3 scripts/spotlight_debate.py --launches data/launches.json --out data/spotlight.json [--dry-run]

Credentials: OPENROUTER_API_KEY in the environment (from the Secure Vault, set
by the cron for this one command). JEV calls go through jev-tracked.py.
"""

import argparse
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jevlib  # noqa: E402
import visuals  # noqa: E402
from jevlib import load_config, load_json, save_json, now_ist, parse_json_reply  # noqa: E402

CHAT = jevlib.openrouter_chat
JUDGE = jevlib.jev_call
BUDGET = jevlib.budget_ok
MODELS = jevlib.openrouter_models

N_PICKS = 4
SHORTLIST = 6
CANDIDATES = 20
FRESH_DAYS = 7
DIMS = {"usecase": 0.4, "popularity": 0.3, "trend": 0.3}

SYSTEM = """You help pick today's Spotlight for Fresh Weights, a free AI-launch radar and newsletter read by busy people. Many readers are not engineers.
From the launches below, choose the {n} that matter most this week: real usefulness, real traction, and momentum. Ignore hype.
For each pick write:
- "gist": what it does for a person, in plain English, at most 18 words. No jargon, no acronyms, no hype words (revolutionary, game-changing, powerful). Only numbers that appear in the data.
- "analogy": at most 12 words, starting with "Like", that a non-engineer instantly gets.
- "why": why it matters this week, at most 20 words.
Only use ids from the list. Reply with one JSON object and nothing else:
{{"picks": [{{"id": "...", "gist": "...", "analogy": "Like ...", "why": "..."}}]}}  (exactly {n} picks, best first)

LAUNCHES:
{launches}"""


def clip(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rsplit(" ", 1)[0] + "…"


def candidates(launches, today):
    fresh = []
    for e in launches:
        try:
            d = date.fromisoformat(str(e.get("added_at"))[:10])
        except ValueError:
            continue
        if today - d <= timedelta(days=FRESH_DAYS):
            fresh.append(e)
    fresh.sort(key=lambda e: (e.get("jev_score") is not None, e.get("jev_score") or 0, e.get("github_stars") or 0),
               reverse=True)
    return fresh[:CANDIDATES]


def compact(e):
    return {"id": e["id"], "title": clip(e.get("title"), 100), "category": e.get("category"),
            "summary": clip(e.get("summary"), 200), "why_it_matters": clip(e.get("usp"), 160),
            "github_stars": e.get("github_stars") or 0, "traction": e.get("jev_score"),
            "added": e.get("added_at"), "kind": e.get("kind")}


def word_count(s):
    return len(str(s or "").split())


def ask(model, system, max_tokens, effort, valid, state):
    """Two attempts. Returns a list of validated picks or None."""
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": "Pick today's Spotlight. JSON only."}]
    for attempt in (1, 2):
        try:
            kw = {"max_tokens": max_tokens}
            if effort:
                kw["reasoning_effort"] = effort
            text, cost = CHAT(model, msgs, **kw)
        except Exception as ex:
            print("  %s attempt %d: call error: %s" % (model, attempt, str(ex)[:160]))
            continue
        state["calls"] += 1
        state["cost"] += float(cost or 0)
        if state["cost"] > state["cap"]:
            raise RuntimeError("cost cap hit: $%.4f > $%.2f" % (state["cost"], state["cap"]))
        try:
            obj = parse_json_reply(text)
            picks, seen = [], set()
            for p in obj.get("picks") or []:
                pid = str(p.get("id") or "").strip()
                if pid not in valid or pid in seen:
                    continue
                seen.add(pid)
                gist, analogy = clip(p.get("gist"), 160), clip(p.get("analogy"), 100)
                picks.append({"id": pid, "gist": gist if word_count(gist) <= 24 else "",
                              "analogy": analogy if analogy.lower().startswith("like") and word_count(analogy) <= 16 else "",
                              "why": clip(p.get("why"), 180)})
            if picks:
                return picks[:N_PICKS]
            print("  %s attempt %d: no valid ids" % (model, attempt))
        except (ValueError, TypeError, AttributeError) as ex:
            print("  %s attempt %d: bad JSON: %s" % (model, attempt, str(ex)[:120]))
    return None


def judge(shortlist, by_id, copy):
    """One JEV call: 3 dimensions per launch + clarity of every gist/analogy."""
    state, q = {"launches": []}, {}
    for n, pid in enumerate(shortlist):
        e = by_id[pid]
        state["launches"].append({"n": n, "title": clip(e.get("title"), 100), "summary": clip(e.get("summary"), 240),
                                  "stars": e.get("github_stars") or 0, "traction": e.get("jev_score")})
        t = clip(e.get("title"), 80)
        q["l%d__usecase" % n] = {"type": "noul", "instructions": (
            "How useful is '%s' to people who build things with AI? Summary: %s. Ignore hype." % (t, clip(e.get("summary"), 200)))}
        q["l%d__popularity" % n] = {"type": "noul", "instructions": (
            "How much real traction does '%s' have? GitHub stars=%s, radar traction=%s. Ignore marketing."
            % (t, e.get("github_stars") or 0, e.get("jev_score")))}
        q["l%d__trend" % n] = {"type": "noul", "instructions": (
            "How strong is the momentum of '%s' this week (added %s, kind=%s)? Consider novelty and timeliness."
            % (t, e.get("added_at"), e.get("kind")))}
        for j, c in enumerate(copy[pid]):
            if c["gist"]:
                q["l%d__g%d" % (n, j)] = {"type": "noul", "instructions": (
                    "A non-technical reader sees this one-line description of '%s': \"%s\". How clearly do they now "
                    "understand what it does for them, and is it accurate to: %s? Penalize jargon and hype."
                    % (t, c["gist"], clip(e.get("summary"), 200)))}
            if c["analogy"]:
                q["l%d__a%d" % (n, j)] = {"type": "noul", "instructions": (
                    "Analogy for '%s': \"%s\". How well does it help a non-engineer grasp it, without being misleading?"
                    % (t, c["analogy"]))}
    return JUDGE("radar-spotlight-debate", state, q)


def score_of(answers, key):
    return jevlib.noul(answers or {}, key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--launches", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    dcfg = cfg.get("debate", {})
    scfg = cfg.get("spotlight", {})
    # spotlight.models can trim the daily panel to save money; default = the
    # same five models as the weekly idea debate.
    models = scfg.get("models") or dcfg.get("models") or []
    cap = float(scfg.get("max_run_cost_usd", 0.3))
    max_tokens = int(scfg.get("max_tokens_per_turn", 3000))
    effort = dcfg.get("reasoning_effort")
    now = now_ist()
    today = now.date()

    current = load_json(args.out, {}) or {}
    if current.get("picked_by") == "debate" and (current.get("debate") or {}).get("date") == today.isoformat():
        print("spotlight debate: today's pick already exists; nothing to do")
        return 0
    if not BUDGET():
        print("spotlight debate: budget guard failed; hourly picker stays in charge")
        return 0
    available = MODELS()
    if available is not None:
        missing = [m for m in models if m not in available]
        if missing:
            print("spotlight debate: models not on OpenRouter: %s; nothing spent" % ", ".join(missing))
            return 1

    launches = (load_json(args.launches, {}) or {}).get("launches", [])
    cands = candidates(launches, today)
    if len(cands) < N_PICKS:
        print("spotlight debate: only %d fresh launches; skipping" % len(cands))
        return 0
    by_id = {e["id"]: e for e in cands}
    system = SYSTEM.format(n=N_PICKS, launches=json.dumps([compact(e) for e in cands], ensure_ascii=False))
    state = {"calls": 0, "cost": 0.0, "cap": cap}

    votes, copy, voters, answered = {}, {}, {}, []
    try:
        for m in models:
            picks = ask(m, system, max_tokens, effort, set(by_id), state)
            if not picks:
                print("  ! %s dropped" % m)
                continue
            answered.append(m)
            print("  %s: %s" % (m, ", ".join(p["id"][:30] for p in picks)))
            for rank, p in enumerate(picks):
                votes[p["id"]] = votes.get(p["id"], 0) + (N_PICKS - rank)
                voters.setdefault(p["id"], []).append(m)
                copy.setdefault(p["id"], []).append(dict(p, model=m, rank=rank))
    except RuntimeError as ex:
        print("spotlight debate aborted, nothing written: %s" % ex)
        return 1
    if len(answered) < 2:
        print("spotlight debate aborted: fewer than 2 models answered (spent $%.4f)" % state["cost"])
        return 1

    def tiebreak(pid):
        return (votes[pid], by_id[pid].get("jev_score") or 0, by_id[pid].get("github_stars") or 0)
    shortlist = sorted(votes, key=tiebreak, reverse=True)[:SHORTLIST]
    max_votes = float(N_PICKS * len(answered))
    answers = judge(shortlist, by_id, copy)
    scored_by = "jev" if answers else "votes-only"

    final = []
    for n, pid in enumerate(shortlist):
        dims = {d: score_of(answers, "l%d__%s" % (n, d)) for d in DIMS}
        jev = sum(dims[d] * w for d, w in DIMS.items()) if answers and None not in dims.values() else None
        vote_share = votes[pid] / max_votes
        total = 0.5 * vote_share + 0.5 * jev if jev is not None else vote_share
        cs = copy[pid]

        def best(kind):
            opts = [(j, c) for j, c in enumerate(cs) if c[kind]]
            if not opts:
                return ""
            if answers:
                ranked = [(score_of(answers, "l%d__%s%d" % (n, kind[0], j)) or 0, -c["rank"], c[kind]) for j, c in opts]
                return max(ranked)[2]
            return min(opts, key=lambda x: x[1]["rank"])[1][kind]
        top_vote = min(cs, key=lambda c: c["rank"])
        final.append({"id": pid, "total": total, "dims": dims if jev is not None else None, "jev": jev,
                      "gist": best("gist"), "analogy": best("analogy"), "why": top_vote["why"]})
    final.sort(key=lambda x: x["total"], reverse=True)
    final = final[:N_PICKS]

    items = []
    for f in final:
        e = by_id[f["id"]]
        items.append({"key": f["id"], "kind": "spotlight", "gist": f["gist"], "entry": e,
                      "motifs": visuals.motif_candidates(e), "stats": visuals.stat_candidates(e),
                      "palettes": visuals.palette_candidates(e)})
    looks = visuals.choose_visuals(items, JUDGE if answers else None, caller="radar-spotlight-visuals")

    picks = []
    for rank, f in enumerate(final, start=1):
        e = by_id[f["id"]]
        n_voters = len(voters.get(f["id"], []))
        spec = visuals.spotlight_spec(e, rank, f["gist"], f["analogy"], looks[f["id"]],
                                      label="Today's pick %d of %d" % (rank, len(final)))
        picks.append({
            "id": e["id"], "title": e.get("title", ""), "url": e.get("url", ""), "summary": e.get("summary", ""),
            "implementation_idea": e.get("implementation_idea", ""), "category": e.get("category", ""),
            "jev_score": e.get("jev_score"), "added_by": e.get("added_by", ""),
            "reason": "Picked by %d of %d models" % (n_voters, len(answered)),
            "composite": round(f["total"], 2),
            "dimensions": {d: round(v, 2) for d, v in f["dims"].items()} if f["dims"] else None,
            "gist": f["gist"], "analogy": f["analogy"], "why": f["why"],
            "votes": votes[f["id"]], "voters": voters.get(f["id"], []),
            "visual": spec, "image": visuals.image_path(spec),
        })
    out = {
        "updated_at": now.isoformat(timespec="minutes"),
        "picked_by": "debate",
        "debate": {"date": today.isoformat(), "models": answered, "scored_by": scored_by,
                   "visuals_by": next(iter(looks.values()))["scored_by"] if looks else "default",
                   "calls": state["calls"], "cost_usd": round(state["cost"], 4)},
        "picks": picks,
    }
    print("spotlight debate: %d picks (%s), %d model calls, $%.4f" % (len(picks), scored_by, state["calls"], state["cost"]))
    for p in picks:
        print("  #%s %s | %s | %s" % (p["visual"]["rank"], p["title"][:50], p["reason"], p["gist"][:70]))
    if args.dry_run:
        print(json.dumps(out, indent=2, ensure_ascii=False)[:4000])
        return 0
    save_json(args.out, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
