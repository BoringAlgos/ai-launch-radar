#!/usr/bin/env python3
"""Weekly multi-model idea debate for the AI Launch Radar.

Once a week, the OpenRouter models in site.config.json (debate.models, five
by default)
argue over the last 7 days of radar entries and produce business ideas that
collide two or more radar capabilities:

  Round 1 PROPOSE  each model proposes 3 ideas (role lens rotates weekly)
  Round 2 CRITIQUE each model critiques the other models' ideas, anonymised
                   as A-F, with a keep / fix / kill verdict
  Round 3 REVISE   each model revises its own ideas against the critiques and
                   names the strongest objection it could not answer (dissent)

Survivors pass a deterministic grounding filter (every build_with entry_id
must be a real launches/archive id, every source URL must appear in radar
data or resolve over HTTP), then ONE JEV call judges them on feasibility,
demand, grounding and monetization. The top debate.ideas_per_week ideas are
appended to data/ideas.json with origin="weekly-debate" (see docs/SCHEMA.md).

Usage:
  python3 scripts/idea_debate.py --launches data/launches.json \
      --archive data/archive.json --ideas data/ideas.json \
      [--week 2026-W40] [--dry-run] [--out PATH]

  --out      write the updated ideas file here instead of --ideas
  --dry-run  run the full (paid) debate but never touch the ideas file; the
             transcript goes to stdout and the result to
             ./idea-debate-dry-run-<week>.json

Budget guards:
  - budget_ok() (remaining >= US$2 and tracked 24h spend <= US$1) must pass,
    otherwise the script exits 0 without changes;
  - configured models are checked against the public OpenRouter model list
    before any money is spent (exit 1 on an unknown id);
  - cumulative OpenRouter cost is tracked per call and the run aborts with no
    write once it exceeds debate.max_run_cost_usd;
  - idempotent: a week that already has weekly-debate ideas is skipped.
Nothing is written unless the JEV judge succeeds: unjudged debate ideas are
never published.

Credentials: debate chat calls read OPENROUTER_API_KEY from the environment
(the cron injects it from the Secure Vault) inside jevlib.openrouter_chat; the
JEV judge goes through jev-tracked.py, which reads the key itself. This script
never reads, prints or stores a key.

The network-touching helpers are bound to module-level names (CHAT, JUDGE,
BUDGET, MODELS, RESOLVE) so tests can swap them for fakes.
"""

import argparse
import difflib
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jevlib  # noqa: E402
from jevlib import (load_config, noul, parse_json_reply, iso_week,  # noqa: E402
                    now_ist, load_json, save_json)

CHAT = jevlib.openrouter_chat
JUDGE = jevlib.jev_call
BUDGET = jevlib.budget_ok
MODELS = jevlib.openrouter_models
RESOLVE = jevlib.url_resolves

CONTEXT_DAYS = 7
MAX_CONTEXT_ENTRIES = 40
RECENT_IDEA_TITLES = 30
MAX_IDEAS_FILE = 60
IDEAS_PER_MODEL = 3
DIFFICULTIES = ("weekend", "moderate", "ambitious")
JUDGE_WEIGHTS = {"feasibility": 0.3, "demand": 0.25, "grounding": 0.25,
                 "monetization": 0.2}
LENSES = [
    "BUILDER lens: favour ideas a 2-3 person team can ship in weeks on the "
    "listed entries; be concrete about the stack.",
    "BUYER lens: favour ideas where a specific buyer already has budget and "
    "a painful, measurable problem; be concrete about who signs.",
    "CONTRARIAN lens: favour non-obvious collisions others will miss; avoid "
    "the first idea everyone would have.",
    "DISTRIBUTION lens: favour ideas that ride a channel which already reaches "
    "the buyer (a marketplace, an integration, a B2B2C partner).",
    "MOAT lens: favour ideas that get better with usage data or workflow "
    "lock-in, so a fast follower can't copy them in a week.",
]

IDEA_SCHEMA = """Each idea is a JSON object with ALL of these fields:
  "title": short product name + one-line pitch,
  "outcome": the measurable result for the customer (not a feature),
  "problem": the pain, grounded in the radar entries,
  "idea": what gets built,
  "why_now": why this is possible/urgent this week, citing entries,
  "collision": which 2+ radar capabilities combine and why the combination matters,
  "differentiation": versus existing alternatives,
  "distribution": who pays, the wedge, the channel (B2B2C preferred),
  "mvp": weekend/first-version scope and what to deliberately skip,
  "risks": biggest risk and how the MVP de-risks it,
  "repo_suggestion": "repo-name - one line",
  "build_with": [{"entry_id": "<id from the radar list>", "name": "...", "reason": "..."}]  (2+ items with real ids),
  "difficulty": "weekend" | "moderate" | "ambitious",
  "tags": ["..."],
  "sources": [{"title": "...", "url": "<url copied from the radar list>"}],
  "implementation_plan": [{"step": "...", "detail": "...", "effort": "1 day"}]  (3-7 ordered steps),
  "beneficiaries": [{"who": "...", "benefit": "..."}],
  "monetization": {"model": "...", "pricing": "concrete price point", "wedge": "first customer", "channels": ["..."]}
Keep each text field under ~60 words."""

QUALITY_BAR = """You are one of several analysts in a structured debate that turns this week's AI launch radar into startup ideas.
Quality bar (ideas that miss any point will be killed):
1. COLLISION - combine 2+ radar capabilities and cite their entry ids in build_with.
2. OUTCOME - state a measurable result for the customer, not a feature.
3. DISTRIBUTION - say who pays and what the wedge is; B2B2C preferred.
4. BUILDABLE NOW - only with the entries listed in the radar context plus ordinary software.
5. NO INVENTION - never invent products, repos, numbers, quotes or links. Only cite URLs that appear in the radar context. If you are not sure of a fact, leave it out.
6. NOVELTY - do not repeat an existing idea title.
Reply with a single JSON object and nothing else."""


class Abort(Exception):
    """Stop the run without writing anything."""


class ModelFailed(Exception):
    pass


# ---------------------------------------------------------------- context

def parse_day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def clip(text, n=160):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def week_end(week, today):
    """Last day of the context window: today, or Sunday of an earlier --week."""
    m = re.fullmatch(r"(\d{4})-W(\d{2})", week)
    if not m:
        raise SystemExit("bad --week %r (want YYYY-Www)" % week)
    sunday = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 7)
    return min(today, sunday)


def build_context(live, archive, ideas, end):
    start = end - timedelta(days=CONTEXT_DAYS)
    fresh = []
    for e in live + archive:
        d = parse_day(e.get("added_at"))
        if d and start < d <= end:
            fresh.append(e)
    fresh.sort(key=lambda e: (e.get("jev_score") is None,
                              -(e.get("jev_score") or 0),
                              -(e.get("github_stars") or 0)))
    pack = []
    for e in fresh[:MAX_CONTEXT_ENTRIES]:
        cat = e.get("category") or ""
        if e.get("subcategory"):
            cat += "/" + e["subcategory"]
        pack.append({
            "id": e["id"],
            "title": e.get("title", ""),
            "category": cat,
            "summary": clip(e.get("summary")),
            "usp": clip(e.get("usp")),
            "jev_score": e.get("jev_score"),
            "github_stars": e.get("github_stars") or 0,
            "url": e.get("url") or "",
        })
    recent = sorted(ideas, key=lambda i: str(i.get("added_at", "")),
                    reverse=True)[:RECENT_IDEA_TITLES]
    return {"entries": pack, "existing_idea_titles": [i.get("title", "")
                                                      for i in recent]}


def radar_urls(entries):
    urls = set()
    for e in entries:
        for k in ("url", "source_url", "learn_url"):
            if e.get(k):
                urls.add(e[k])
        for b in e.get("community_builds") or []:
            if isinstance(b, dict) and b.get("url"):
                urls.add(b["url"])
        for r in e.get("implementation_repos") or []:
            if isinstance(r, dict) and r.get("repo"):
                urls.add("https://github.com/" + r["repo"].strip("/"))
        if e.get("github_repo"):
            urls.add("https://github.com/" + str(e["github_repo"]).strip("/"))
    return {u.rstrip("/") for u in urls}


# ---------------------------------------------------------------- debate

class Debate:
    def __init__(self, models, context, max_tokens, max_cost, week_no,
                 critics=2, reasoning_effort=None):
        self.models = list(models)
        self.critics = max(1, int(critics))
        self.reasoning_effort = reasoning_effort
        self.active = list(models)
        self.participated = []
        self.context_json = json.dumps(context, ensure_ascii=False)
        self.max_tokens = max_tokens
        self.max_cost = max_cost
        self.week_no = week_no
        self.cost = 0.0
        self.calls = 0
        self.log = []

    def say(self, line):
        print(line)
        self.log.append(line)

    def system(self):
        return (QUALITY_BAR + "\n\n" + IDEA_SCHEMA +
                "\n\nRADAR CONTEXT (this week's entries and existing idea "
                "titles):\n" + self.context_json)

    def call_json(self, model, user, check):
        """Two attempts per turn; returns the parsed object or raises
        ModelFailed. Cost is counted for every reply received."""
        msgs = [{"role": "system", "content": self.system()},
                {"role": "user", "content": user}]
        last = None
        for attempt in (1, 2):
            try:
                kw = {"max_tokens": self.max_tokens}
                if self.reasoning_effort:
                    kw["reasoning_effort"] = self.reasoning_effort
                text, cost = CHAT(model, msgs, **kw)
            except Exception as e:
                last = "call error: %s" % str(e)[:160]
                self.say("    %s attempt %d: %s" % (model, attempt, last))
                continue
            self.calls += 1
            self.cost += float(cost or 0.0)
            if self.cost > self.max_cost:
                raise Abort("cost cap hit: $%.4f > $%.2f"
                            % (self.cost, self.max_cost))
            try:
                obj = parse_json_reply(text)
                check(obj)
                return obj
            except (ValueError, TypeError, KeyError) as e:
                last = "bad JSON: %s" % str(e)[:160]
                self.say("    %s attempt %d: %s" % (model, attempt, last))
        raise ModelFailed(last)

    def drop(self, model, why):
        self.say("  ! %s dropped (%s)" % (model, why))
        if model in self.active:
            self.active.remove(model)
        if len(self.active) < 2:
            raise Abort("fewer than 2 models left")

    def run_round(self, name, fn):
        """fn(model) -> result. Returns {model: result} for models that
        answered; a failing model is dropped from the rest of the debate."""
        self.say("== Round %s" % name)
        out = {}
        for m in list(self.active):
            try:
                out[m] = fn(m)
            except ModelFailed as e:
                self.drop(m, e)
        return out

    # Round 1 -------------------------------------------------------------
    def propose(self):
        def one(model):
            lens = LENSES[(self.models.index(model) + self.week_no)
                          % len(LENSES)]
            user = ("Round 1 PROPOSE. %s\nPropose exactly %d ideas. Reply "
                    '{"ideas": [ ... ]}.' % (lens, IDEAS_PER_MODEL))
            obj = self.call_json(model, user, check_ideas)
            ideas = [normalize_idea(i) for i in obj["ideas"]]
            ideas = [i for i in ideas if i][:IDEAS_PER_MODEL]
            if not ideas:
                raise ModelFailed("no usable ideas")
            self.say("  %s [%s]:" % (model, lens.split(":")[0]))
            for i in ideas:
                self.say("    + %s" % i["title"])
            return ideas
        proposals = self.run_round("1 PROPOSE", one)
        self.participated = [m for m in self.models if m in proposals]
        return proposals

    # Round 2 -------------------------------------------------------------
    def rivals(self, model, proposals):
        """The next `critics` proposers after `model` in a ring. Each model
        reviews that many rivals, so every idea gets that many critiques
        without each model reading every proposal (keeps input cost flat as
        the panel grows)."""
        ring = [m for m in self.active if m in proposals]
        if model not in ring:
            return [m for m in ring if m != model][:self.critics]
        i = ring.index(model)
        n = min(self.critics, len(ring) - 1)
        return [ring[(i + k) % len(ring)] for k in range(1, n + 1)]

    def critique(self, proposals):
        def one(model):
            labelled, mapping = [], {}
            for other in self.rivals(model, proposals):
                for idx, idea in enumerate(proposals[other]):
                    label = chr(ord("A") + len(mapping))
                    mapping[label] = (other, idx)
                    labelled.append(dict(idea, label=label))
            if not labelled:
                return {}
            user = ("Round 2 CRITIQUE. Below are ideas from the other "
                    "analysts, labelled. Judge each against the quality bar "
                    "and the radar context (check that cited entry ids and "
                    "URLs really exist there). Reply "
                    '{"critiques": [{"label": "A", "verdict": "keep|fix|kill", '
                    '"strongest_objection": "...", "fix": "..."}]}.\n\n'
                    + json.dumps(labelled, ensure_ascii=False))
            obj = self.call_json(model, user, check_critiques)
            got = {}
            for c in obj["critiques"]:
                label = str(c.get("label", "")).strip().upper()[:1]
                if label not in mapping:
                    continue
                verdict = str(c.get("verdict", "fix")).lower()
                if verdict not in ("keep", "fix", "kill"):
                    verdict = "fix"
                got[mapping[label]] = {
                    "verdict": verdict,
                    "strongest_objection": clip(c.get("strongest_objection"), 400),
                    "fix": clip(c.get("fix"), 400),
                    "critic": model,
                }
            return got
        by_critic = self.run_round("2 CRITIQUE", one)
        received = {}  # (author, idx) -> [critique]
        for critic, got in by_critic.items():
            for key, c in got.items():
                received.setdefault(key, []).append(c)
        for author in self.active:
            for idx, idea in enumerate(proposals.get(author, [])):
                verdicts = [c["verdict"] for c in received.get((author, idx), [])]
                self.say("  %s: %s" % (idea["title"][:60],
                                       ", ".join(verdicts) or "no critiques"))
        return received

    # Round 3 -------------------------------------------------------------
    def revise(self, proposals, received):
        def one(model):
            own = []
            for idx, idea in enumerate(proposals.get(model, [])):
                crit = [{k: c[k] for k in ("verdict", "strongest_objection",
                                           "fix")}
                        for c in received.get((model, idx), [])]
                own.append({"idea": idea, "critiques": crit})
            if not own:
                return []
            user = ("Round 3 REVISE. Here are your ideas and the anonymous "
                    "critiques they received. Revise each idea to answer the "
                    "critiques. Drop an idea that was killed and cannot be "
                    "saved. Every returned idea keeps the full schema and adds "
                    '"dissent": the strongest objection you could NOT fully '
                    'answer, in one sentence. Reply {"ideas": [ ... ]}.\n\n'
                    + json.dumps(own, ensure_ascii=False))
            obj = self.call_json(model, user, check_ideas)
            ideas = []
            for raw in obj["ideas"]:
                idea = normalize_idea(raw)
                if idea:
                    idea["_model"] = model
                    idea["_dissent"] = clip(raw.get("dissent"), 300)
                    ideas.append(idea)
            self.say("  %s revised: %s" % (
                model, "; ".join(i["title"][:50] for i in ideas) or "(none)"))
            return ideas
        revised = self.run_round("3 REVISE", one)
        return [i for m in self.active for i in revised.get(m, [])]


def check_ideas(obj):
    if not isinstance(obj.get("ideas"), list):
        raise ValueError("missing ideas list")


def check_critiques(obj):
    if not isinstance(obj.get("critiques"), list):
        raise ValueError("missing critiques list")


def as_list(v):
    return v if isinstance(v, list) else []


def normalize_idea(raw):
    """Coerce a model idea into the ideas.json shape, or None if it lacks
    the core of the schema."""
    if not isinstance(raw, dict):
        return None
    text = lambda k: " ".join(str(raw.get(k) or "").split())  # noqa: E731
    idea = {k: text(k) for k in (
        "title", "outcome", "problem", "idea", "why_now", "collision",
        "differentiation", "distribution", "mvp", "risks", "repo_suggestion")}
    if not all(idea[k] for k in ("title", "outcome", "idea", "collision",
                                 "distribution")):
        return None
    idea["build_with"] = [
        {"entry_id": (str(b["entry_id"]).strip() if b.get("entry_id") else None),
         "name": str(b.get("name") or ""), "reason": str(b.get("reason") or "")}
        for b in as_list(raw.get("build_with")) if isinstance(b, dict)]
    diff = str(raw.get("difficulty") or "").lower()
    idea["difficulty"] = diff if diff in DIFFICULTIES else "moderate"
    idea["tags"] = [str(t) for t in as_list(raw.get("tags"))][:8]
    idea["sources"] = [
        {"title": str(s.get("title") or ""), "url": str(s.get("url") or "")}
        for s in as_list(raw.get("sources"))
        if isinstance(s, dict) and s.get("url")]
    plan = [{"step": str(p.get("step") or ""), "detail": str(p.get("detail") or ""),
             "effort": str(p.get("effort") or "")}
            for p in as_list(raw.get("implementation_plan"))
            if isinstance(p, dict) and p.get("step")]
    if len(plan) < 3:
        return None
    idea["implementation_plan"] = plan[:7]
    idea["beneficiaries"] = [
        {"who": str(b.get("who") or ""), "benefit": str(b.get("benefit") or "")}
        for b in as_list(raw.get("beneficiaries")) if isinstance(b, dict)]
    mon = raw.get("monetization") if isinstance(raw.get("monetization"), dict) else {}
    idea["monetization"] = {
        "model": str(mon.get("model") or ""),
        "pricing": str(mon.get("pricing") or ""),
        "wedge": str(mon.get("wedge") or ""),
        "channels": [str(c) for c in as_list(mon.get("channels"))],
    }
    if not idea["monetization"]["model"]:
        return None
    return idea


# ---------------------------------------------------------------- grounding

def title_key(title):
    return " ".join(re.findall(r"[a-z0-9]+", title.lower()))


def similar(a, b):
    """Same product name (text before ':') or near-identical full title."""
    ka, kb = title_key(a), title_key(b)
    if not ka or not kb:
        return False
    if title_key(a.split(":")[0]) == title_key(b.split(":")[0]):
        return True
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.85


def ground(ideas, valid_ids, known_urls, existing_titles, say):
    resolved = {}
    kept = []
    for idea in ideas:
        bw, grounded = [], 0
        for b in idea["build_with"]:
            if b["entry_id"] is None:
                bw.append(b)
            elif b["entry_id"] in valid_ids:
                bw.append(b)
                grounded += 1
            else:
                say("  - %s: dropped build_with %r (not a radar id)"
                    % (idea["title"][:40], b["entry_id"]))
        if grounded < 2:
            say("  x %s: rejected (only %d grounded build_with)"
                % (idea["title"][:60], grounded))
            continue
        idea["build_with"] = bw
        srcs = []
        for s in idea["sources"]:
            url = s["url"].strip()
            ok = url.rstrip("/") in known_urls
            if not ok:
                if url not in resolved:
                    resolved[url] = bool(RESOLVE(url))
                ok = resolved[url]
            if ok:
                srcs.append({"title": s["title"], "url": url})
            else:
                say("  - %s: dropped source %s" % (idea["title"][:40], url))
        idea["sources"] = srcs
        if any(similar(idea["title"], k["title"]) for k in kept):
            say("  x %s: duplicate title" % idea["title"][:60])
            continue
        if any(similar(idea["title"], t) for t in existing_titles):
            say("  x %s: repeats an existing idea" % idea["title"][:60])
            continue
        kept.append(idea)
    return kept


# ---------------------------------------------------------------- judge

def judge(ideas, titles_by_id):
    state, questions = [], {}
    for n, idea in enumerate(ideas, 1):
        cid = "c%d" % n
        idea["_cid"] = cid
        cited = ", ".join("%s (%s)" % (b["entry_id"], titles_by_id.get(
            b["entry_id"], b["name"])) for b in idea["build_with"] if b["entry_id"])
        state.append({
            "id": cid,
            "title": idea["title"],
            "outcome": clip(idea["outcome"], 300),
            "problem": clip(idea["problem"], 300),
            "idea": clip(idea["idea"], 400),
            "collision": clip(idea["collision"], 300),
            "distribution": clip(idea["distribution"], 300),
            "monetization": idea["monetization"],
            "build_with": cited,
            "sources": [s["url"] for s in idea["sources"]],
            "dissent": idea.get("_dissent", ""),
        })
        t = idea["title"][:80]
        questions[cid + "__feasibility"] = {"type": "noul", "instructions": (
            "Can a small team build '%s' within a few weeks using the cited "
            "radar entries (%s) plus ordinary software? 1 = clearly yes."
            % (t, cited))}
        questions[cid + "__demand"] = {"type": "noul", "instructions": (
            "Is there real evidence that someone pays for the outcome of "
            "'%s' (%s)? Ignore hype; 1 = strong evidence of paying demand."
            % (t, clip(idea["outcome"], 200)))}
        questions[cid + "__grounding"] = {"type": "noul", "instructions": (
            "Are the claims in '%s' supported by its cited radar entries and "
            "sources, with no invented products, numbers or facts? 1 = fully "
            "grounded." % t)}
        questions[cid + "__monetization"] = {"type": "noul", "instructions": (
            "Is the pricing and first-customer wedge for '%s' credible? "
            "Model: %s; pricing: %s; wedge: %s. 1 = very credible."
            % (t, idea["monetization"]["model"], idea["monetization"]["pricing"],
               idea["monetization"]["wedge"]))}
    answers = JUDGE(caller="radar-debate-judge", state={"ideas": state},
                    questions=questions)
    if not answers:
        raise Abort("JEV judge failed; never publishing unjudged ideas")
    scored = []
    for idea in ideas:
        sub = {d: noul(answers, "%s__%s" % (idea["_cid"], d))
               for d in JUDGE_WEIGHTS}
        if any(v is None for v in sub.values()):
            print("  judge: incomplete scores for %s, skipped" % idea["title"][:50])
            continue
        idea["_scores"] = {d: round(v, 2) for d, v in sub.items()}
        idea["_score"] = round(sum(sub[d] * w for d, w in JUDGE_WEIGHTS.items()), 2)
        scored.append(idea)
    if not scored:
        raise Abort("JEV judge returned no complete scores")
    scored.sort(key=lambda i: i["_score"], reverse=True)
    return scored


# ---------------------------------------------------------------- write

def finalize(winners, existing_ids, today, week, models):
    out, used = [], set(existing_ids)
    stamp = today.strftime("%Y%m%d")
    n = 0
    for idea in winners:
        n += 1
        iid = "idea-%s-w%d" % (stamp, n)
        while iid in used:
            n += 1
            iid = "idea-%s-w%d" % (stamp, n)
        used.add(iid)
        rec = {"id": iid}
        rec.update({k: v for k, v in idea.items() if not k.startswith("_")})
        rec.update({
            "jev_score": idea["_score"],
            "added_at": today.isoformat(),
            "origin": "weekly-debate",
            "week": week,
            "debate": {
                "models": list(models),
                "rounds": 3,
                "judge": "jev",
                "dissent": idea.get("_dissent", ""),
                "scores": idea["_scores"],
            },
        })
        out.append(rec)
    return out


def cap_ideas(ideas, limit=MAX_IDEAS_FILE):
    """Same rule as the daily cron: keep the newest `limit` by added_at."""
    if len(ideas) <= limit:
        return ideas
    order = sorted(range(len(ideas)),
                   key=lambda i: str(ideas[i].get("added_at", "")), reverse=True)
    keep = set(order[:limit])
    return [x for i, x in enumerate(ideas) if i in keep]


# ---------------------------------------------------------------- main

def check_models(models):
    available = MODELS()
    if available is None:
        print("model list unavailable; proceeding with configured models")
        return True
    missing = [m for m in models if m not in available]
    for m in missing:
        vendor = m.split("/")[0] + "/"
        close = difflib.get_close_matches(
            m, sorted(a for a in available if a.startswith(vendor)), n=5, cutoff=0.3)
        print("model %s not on OpenRouter; close matches: %s"
              % (m, ", ".join(close) or "(none)"))
    return not missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--launches", required=True)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--ideas", required=True)
    ap.add_argument("--week", help="ISO week label, e.g. 2026-W40 (default: this week)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", help="write updated ideas here instead of --ideas")
    args = ap.parse_args()

    cfg = load_config().get("debate", {})
    models = cfg.get("models") or []
    per_week = int(cfg.get("ideas_per_week", 3))
    max_cost = float(cfg.get("max_run_cost_usd", 0.5))
    max_tokens = int(cfg.get("max_tokens_per_turn", 4000))
    critics = int(cfg.get("critics_per_model", 2))
    effort = cfg.get("reasoning_effort")
    if len(models) < 2:
        print("debate: need at least 2 models in site.config.json")
        return 1

    today = now_ist().date()
    week = args.week or iso_week(today)
    ideas_doc = load_json(args.ideas, {"ideas": []}) or {"ideas": []}
    ideas = ideas_doc.get("ideas", [])
    if any(i.get("origin") == "weekly-debate" and i.get("week") == week
           for i in ideas):
        print("debate: %s already has weekly-debate ideas; nothing to do" % week)
        return 0
    if not BUDGET():
        print("debate: budget guard failed; no changes")
        return 0
    if not check_models(models):
        print("debate: fix debate.models in site.config.json; nothing spent")
        return 1

    live = (load_json(args.launches, {}) or {}).get("launches", [])
    arch = (load_json(args.archive, {}) or {}).get("launches", [])
    context = build_context(live, arch, ideas, week_end(week, today))
    if len(context["entries"]) < 4:
        print("debate: only %d fresh entries; skipping" % len(context["entries"]))
        return 0
    approx = len(json.dumps(context, ensure_ascii=False)) // 4
    print("debate %s: %d entries in context (~%d tokens), models: %s"
          % (week, len(context["entries"]), approx, ", ".join(models)))

    week_no = int(week.split("-W")[1])
    debate = Debate(models, context, max_tokens, max_cost, week_no,
                    critics=critics, reasoning_effort=effort)
    try:
        proposals = debate.propose()
        received = debate.critique(proposals)
        revised = debate.revise(proposals, received)
        debate.say("== Grounding (%d revised ideas)" % len(revised))
        valid_ids = {e["id"] for e in live + arch if e.get("id")}
        survivors = ground(revised, valid_ids, radar_urls(live + arch),
                           context["existing_idea_titles"], debate.say)
        if not survivors:
            raise Abort("no idea survived grounding")
        debate.say("== JEV judge (%d ideas)" % len(survivors))
        titles = {e["id"]: e.get("title", "") for e in live + arch}
        scored = judge(survivors, titles)
    except Abort as e:
        print("debate aborted, nothing written: %s (spent $%.4f over %d calls)"
              % (e, debate.cost, debate.calls))
        return 1
    for i in scored:
        debate.say("  %.2f %s  %s  [%s]" % (i["_score"], i["title"][:60],
                   json.dumps(i["_scores"]), i["_model"]))

    winners = finalize(scored[:per_week], {i.get("id") for i in ideas},
                       today, week, debate.participated)
    debate.say("== Total: %d calls, $%.4f; publishing %d ideas"
               % (debate.calls, debate.cost, len(winners)))
    for w in winners:
        debate.say("  -> %s %s (%.2f)" % (w["id"], w["title"][:60], w["jev_score"]))

    if args.dry_run:
        path = os.path.abspath("idea-debate-dry-run-%s.json" % week)
        save_json(path, {"week": week, "cost_usd": round(debate.cost, 4),
                         "transcript": debate.log, "ideas": winners})
        print("dry run: ideas file untouched; result -> %s" % path)
        return 0
    ideas_doc["ideas"] = cap_ideas(ideas + winners)
    ideas_doc["updated_at"] = today.isoformat()
    out = args.out or args.ideas
    save_json(out, ideas_doc)
    print("debate: wrote %d ideas -> %s" % (len(winners), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
