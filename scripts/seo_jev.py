#!/usr/bin/env python3
"""JEV-picked SEO titles and descriptions for launch and idea pages.

JEV makes typed judgments; it doesn't write prose. So this script writes 3
title and 3 description candidates per page deterministically, from the
entry's own fields, and asks JEV which one a searching developer is most
likely to click while still finding it accurate. Winners go to data/seo.json,
which scripts/build_site.py reads. Pages without a record use the build's
deterministic fallback, so skipping this script never breaks anything.

Only entries with no seo.json record are scored (newest first). Records are
never rewritten, so results stay stable for search engines.

Budget: same guard as the crons (remaining >= $2, 24h spend <= $1). At most
seo.max_jev_calls_per_run calls (site.config.json), each covering
seo.entries_per_call entries (6 noul questions per entry).

Usage:
  python3 scripts/seo_jev.py [--dry-run]

--dry-run prints the candidates and makes no JEV call.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jevlib  # noqa: E402
from jevlib import DATA, load_json, save_json, load_config, now_ist  # noqa: E402
from build_site import clip, clause_fit, short_name, title_candidates, CATEGORIES  # noqa: E402

SEO_PATH = os.path.join(DATA, "seo.json")
TITLE_MAX = 58
DESC_MAX = 155

TITLE_Q = ("A developer searched Google for '%s' and sees this result title: \"%s\". "
           "How likely are they to click it AND find it an accurate description of the "
           "page (an AI launch called %s: %s)? Penalize vagueness, hype words, truncation, "
           "keyword stuffing and claims not in the summary.")
DESC_Q = ("Search snippet under a result about %s: \"%s\". How likely is this snippet to "
          "earn a click from a developer evaluating it, while staying factual to: %s? "
          "Penalize hype, vagueness and cut-off sentences.")


def launch_candidates(l):
    name = short_name(l.get("title", ""))
    cat = CATEGORIES.get(l.get("category"), ("AI launch",))[0]
    usp = l.get("usp") or l.get("summary") or ""
    stars = l.get("github_stars") or 0
    single = cat[:-1] if cat.endswith("s") else cat
    titles = title_candidates(name, usp, l.get("summary"), budget=TITLE_MAX)[:2]
    if stars >= 1000:
        titles.append("%s: %s with %s GitHub stars" % (name, single, "{:,}".format(stars)))
    own = None
    if name.endswith("…"):
        own = clause_fit(l.get("title"), TITLE_MAX + 8)
    titles.append(own or "%s: new %s, traction and real builds" % (name, single))
    descs = [
        clip(l.get("summary"), DESC_MAX),
        clip("%s %s" % (usp, l.get("usability") or ""), DESC_MAX),
        clip("%s What you could build: %s" % (l.get("summary") or "", l.get("implementation_idea") or ""), DESC_MAX),
    ]
    return name, titles, descs


def idea_candidates(it):
    name = it.get("title", "")
    head = short_name(name)
    titles = [name] + title_candidates(head, it.get("outcome"), it.get("idea"), budget=TITLE_MAX)[:2]
    descs = [
        clip(it.get("outcome") or it.get("problem"), DESC_MAX),
        clip(it.get("problem"), DESC_MAX),
        clip("%s %s" % (it.get("outcome") or "", it.get("distribution") or ""), DESC_MAX),
    ]
    return head, titles, descs


def dedupe(cands):
    out = []
    for c in cands:
        if c and c not in out:
            out.append(c)
    return out[:4]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = load_config().get("seo", {})
    max_calls = int(cfg.get("max_jev_calls_per_run", 2))
    per_call = int(cfg.get("entries_per_call", 10))

    seo = load_json(SEO_PATH, None) or {"updated_at": "", "entries": {}}
    done = seo.setdefault("entries", {})
    live, arch = jevlib.all_entries()
    ideas = load_json(os.path.join(DATA, "ideas.json"), {}).get("ideas", [])

    todo = []
    for l in sorted(live, key=lambda x: x.get("added_at") or "", reverse=True):
        if l.get("id") and l["id"] not in done:
            todo.append(("launch", l) + launch_candidates(l))
    for it in sorted(ideas, key=lambda x: x.get("added_at") or "", reverse=True):
        if it.get("id") and it["id"] not in done:
            todo.append(("idea", it) + idea_candidates(it))
    todo = todo[:max_calls * per_call]
    if not todo:
        print("seo: nothing new to score")
        return 0
    if args.dry_run:
        for kind, ent, name, titles, descs in todo:
            print("%s %s" % (kind, ent["id"]))
            for t in dedupe(titles):
                print("  T %2d  %s" % (len(t), t))
            for d in dedupe(descs):
                print("  D %3d %s" % (len(d), d))
        return 0
    if not jevlib.budget_ok():
        return 0

    written = 0
    for i in range(0, len(todo), per_call):
        batch = todo[i:i + per_call]
        questions, state = {}, {"pages": []}
        for n, (kind, ent, name, titles, descs) in enumerate(batch):
            facts = clip(ent.get("summary") or ent.get("problem") or "", 240)
            state["pages"].append({"id": ent["id"], "kind": kind, "name": name, "facts": facts})
            for j, t in enumerate(dedupe(titles)):
                questions["p%d_t%d" % (n, j)] = {"type": "noul", "instructions": TITLE_Q % (name, t, name, facts)}
            for j, d in enumerate(dedupe(descs)):
                questions["p%d_d%d" % (n, j)] = {"type": "noul", "instructions": DESC_Q % (name, d, facts)}
        answers = jevlib.jev_call("radar-seo", state, questions)
        if answers is None:
            print("seo: JEV call failed, stopping (fallback titles stay in use)")
            break
        for n, (kind, ent, name, titles, descs) in enumerate(batch):
            ts, ds = dedupe(titles), dedupe(descs)
            tsc = [jevlib.noul(answers, "p%d_t%d" % (n, j)) for j in range(len(ts))]
            dsc = [jevlib.noul(answers, "p%d_d%d" % (n, j)) for j in range(len(ds))]
            if None in tsc or None in dsc:
                continue
            bt = max(range(len(ts)), key=lambda j: tsc[j])
            bd = max(range(len(ds)), key=lambda j: dsc[j])
            done[ent["id"]] = {"title": ts[bt], "description": ds[bd], "scored_by": "jev",
                               "scores": {"title": round(tsc[bt], 2), "description": round(dsc[bd], 2)}}
            written += 1
    if written:
        seo["updated_at"] = now_ist().isoformat(timespec="minutes")
        save_json(SEO_PATH, seo)
    print("seo: %d records written to %s" % (written, SEO_PATH))
    return 0


if __name__ == "__main__":
    sys.exit(main())
