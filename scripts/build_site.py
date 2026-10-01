#!/usr/bin/env python3
"""Static site build for freshweights.com (GitHub Pages via Actions).

The dashboard (index.html) stays a client-side app that reads data/*.json.
This build adds crawlable pages on top of it, so search engines and link
previews see real HTML:

  /launch/<slug>/      one page per live + archived launch
  /idea/<slug>/        one page per idea
  /category/           category index, and /category/<cat>/ per category
  /weekly/<YYYY-Www>/  web version of each weekly digest (data/digests/)
  /monthly/<YYYY-MM>/  web version of each monthly digest
  /weekly/             issue archive
  sitemap.xml, robots.txt, feed.xml (RSS)

It also injects a static snapshot (Spotlight + latest launches + ideas) into
index.html between <!--SSR:START--> and <!--SSR:END-->. The dashboard removes
it on load.

Titles and descriptions come from data/seo.json (picked by JEV in
scripts/seo_jev.py) when present, else a deterministic fallback.

Usage:
  python3 scripts/build_site.py --out _site [--data-dir data]

--data-dir reads launches/archive/ideas/spotlight/seo/digests from another
folder (for previews and tests); the default is the repo's data/.

Stdlib only, no network, no credentials. Safe to run anywhere.
"""

import argparse
import html
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from email.utils import format_datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import visuals  # noqa: E402
from jevlib import ROOT, DATA, load_json, load_config, slug_for  # noqa: E402

CATEGORIES = {
    "models": ("AI models", "New frontier, open-weights and on-device AI models."),
    "agents": ("AI agents", "New agent frameworks, orchestration tools and AI assistants."),
    "coding": ("AI coding tools", "New AI IDEs, coding agents and model gateways for developers."),
    "infra": ("AI infrastructure", "New routers, memory layers, tokenizers and inference infrastructure."),
    "media": ("AI media tools", "New image, video, 3D and audio generation tools."),
    "robotics": ("AI robotics", "New robotics models, ROS tooling and embodied AI releases."),
    "security": ("AI security", "New AI identity, compliance and security tooling."),
}
STATIC_FILES = ["index.html", "404.html", "CNAME", "site.config.json"]
BUILD_DATE = datetime.now(timezone.utc).date().isoformat()

e = html.escape


def clip(text, n):
    """Trim to n chars at a word boundary, with an ellipsis if cut."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= n:
        return text
    cut = text[:n - 1]
    if " " in cut:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,;:-—") + "…"


# Clauses that go stale the day after the build never belong in a title.
STALE = re.compile(r"\b(today|this week|right now)\b|#1 on", re.I)

# implementation_idea texts that are the owner's private notes, not a generic
# use case. Hidden at render time (the data itself is left alone).
PRIVATE_IDEA = re.compile(
    r"\b(the radar|radar's|radar itself|boringalgos|builder agents?|radar-ingest|the dashboard|hermes|"
    r"voyagebliss|our |we |jev calls|x-triage|droplet|upstox|zerodha|four-seat)\b", re.I)
FILTERED_IDEAS = set()


def public_idea(l):
    """The launch's implementation_idea, or "" if it reads like a private note."""
    t = l.get("implementation_idea") or ""
    if t and PRIVATE_IDEA.search(t):
        FILTERED_IDEAS.add(l.get("id"))
        return ""
    return t


def safe_url(u):
    """Only http(s) links from the data files are ever put into an href."""
    u = str(u or "").strip()
    return u if re.match(r"^https?://", u, re.I) else "#"


def title_budget(cfg):
    """Characters left for the page title once " | Brand" is appended (60 max)."""
    return 60 - len(" | %s" % cfg["brand"])


def clause_fit(text, budget):
    """Longest run of whole clauses from text that fits in budget chars, so a
    title never ends mid-thought. None if even the first clause is too long.
    Clauses that go stale (today, this week, #1 on ...) are skipped."""
    text = re.sub(r"\s*\([^)]*\)", "", text or "")
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    parts = [p for p in re.split(r"(?<=[,;.:])\s+|\s+[—–]\s+|\s+-\s+|\s+\(", text)
             if p and not STALE.search(p)]
    best = None
    acc = ""
    for part in parts:
        cand = (acc + " " + part).strip() if acc else part
        clean = cand.rstrip(" ,;:.")
        if len(clean) > budget:
            break
        best, acc = clean, cand
    return best


def first_clause(text):
    text = re.sub(r"\s*\([^)]*\)", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    for p in re.split(r"(?<=[,;.:])\s+|\s+[—–]\s+|\s+-\s+", text):
        p = p.strip().rstrip(" ,;:.")
        if p and not STALE.search(p):
            return p
    return ""


def title_candidates(name, *texts, budget=44):
    """Untruncated 'Name: clause' titles from each text. A name that is
    itself a clipped long title yields its own leading clauses instead."""
    out = []
    if name.endswith("…"):
        return [c for c in [clause_fit(t, budget) for t in texts] if c]
    for t in texts:
        c = clause_fit(t, budget - len(name) - 2)
        if c and ": " in c:  # never "Name: Clause: more"
            c = c.split(": ")[0]
        if c and c.lower() != name.lower():
            out.append("%s: %s" % (name, c[0].upper() + c[1:]))
    return out


def short_name(title):
    """'VoiceStudio: open-source, fully-local…' -> 'VoiceStudio'."""
    for sep in (": ", " — ", " – ", " - ", " ("):
        if sep in (title or ""):
            head = title.split(sep)[0].strip()
            if 2 <= len(head) <= 48:
                return head
    return clip(title, 48)


# ---------------------------------------------------------------- SEO text

def fit_title(text, budget):
    """A stored title that fits as is, else its leading clauses, else clipped."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= budget and not STALE.search(text):
        return text
    return clause_fit(text, budget) or clip(text, budget)


def seo_launch(l, seo, budget=44):
    """(title without the brand suffix, meta description). The description is
    the summary only: implementation ideas can be private notes."""
    rec = seo.get(l["id"]) or {}
    name = short_name(l.get("title", ""))
    if rec.get("title"):
        title = fit_title(rec["title"], budget)
    elif len(name) < 6 and not name.endswith("…"):
        # 'OCE' alone says nothing: 'OCE: <first clause of the summary>'
        room = budget - len(name) - 2
        lead = (clause_fit(l.get("summary"), room) or clause_fit(l.get("usp"), room)
                or first_clause(l.get("summary")) or first_clause(l.get("usp")))
        title = clip("%s: %s" % (name, lead[:1].upper() + lead[1:]), budget) if lead else name
    else:
        title = (title_candidates(name, l.get("usp"), l.get("summary"), budget=budget)
                 or [c for c in [clause_fit(l.get("title"), budget)] if c]
                 or [clip(name, budget)])[0]
    desc = rec.get("description") or clip(l.get("summary") or l.get("usp") or "", 155)
    return title, desc


def seo_idea(it, seo, budget=44):
    rec = seo.get(it["id"]) or {}
    full = re.sub(r"\s+", " ", rec.get("title") or it.get("title") or "").strip()
    if len(full) <= budget and not STALE.search(full):
        title = full
    else:
        name = short_name(full)
        room = budget - len(name) - 2
        rest = full[len(name):].lstrip(" :—–-(") if full.startswith(name) else ""
        # outcome's first clause if it fits, else the title's own subtitle, else clipped
        # the subtitle up to its first connective: "the audit console for X" -> "audit console"
        phrase = re.sub(r"^(the|a|an)\s+", "", re.split(r"\s+(?:for|that|with|when|which|so|by|to|in|without|from)\s+",
                                                        first_clause(rest))[0], flags=re.I) if rest else ""
        lead = (clause_fit(it.get("outcome") or it.get("problem"), room) or clause_fit(rest, room)
                or (phrase if 0 < len(phrase) <= room else "")
                or first_clause(it.get("outcome") or it.get("problem")))
        if lead and ": " in lead:
            lead = lead.split(": ")[0]
        title = clip("%s: %s" % (name, lead[0].upper() + lead[1:]) if lead and not name.endswith("…") else full, budget)
    desc = rec.get("description") or clip(it.get("outcome") or it.get("problem") or it.get("idea"), 155)
    return title, desc


# ---------------------------------------------------------------- layout

PAGE_CSS = """
:root{--bg:#0a0c0b;--bg-1:#0f1211;--bg-2:#151918;--border:rgba(255,255,255,.08);--border-hi:rgba(255,255,255,.16);
--t1:#f2f5f3;--t2:#a3aca7;--t3:#8b958f;--t4:#78827d;--acc:#3ddc97;--ink:#06140d;--amb:#f5b545;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;--mono:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t1);font-family:var(--sans);-webkit-font-smoothing:antialiased;line-height:1.6}
a{color:var(--acc)}.wrap{max-width:760px;margin:0 auto;padding:0 20px}.wide{max-width:1120px}
nav{max-width:1120px;margin:0 auto;padding:14px 20px;display:flex;justify-content:space-between;align-items:center;gap:12px}
.brand{color:var(--t1);text-decoration:none;font-weight:750;letter-spacing:-.02em;display:inline-flex;gap:9px;align-items:center}
.brand svg{width:22px;height:22px}.nl a{color:var(--t3);text-decoration:none;font-size:.88rem;margin-left:14px}.nl a:hover{color:var(--t1)}
.btn{display:inline-block;background:var(--acc);color:var(--ink)!important;font-weight:650;font-size:.88rem;padding:10px 15px;border-radius:6px;text-decoration:none;border:0;cursor:pointer;font-family:var(--sans)}
.btn.ghost{background:transparent;color:var(--t1)!important;border:1px solid var(--border-hi)}
.crumbs{font-family:var(--mono);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--t3);margin:28px 0 10px}
.crumbs a{color:var(--t3);text-decoration:none}.crumbs a:hover{color:var(--acc)}
h1{font-size:clamp(1.7rem,4vw,2.4rem);line-height:1.12;letter-spacing:-.03em;margin:0 0 14px;overflow-wrap:anywhere}
h2{font-size:1.1rem;letter-spacing:-.01em;margin:30px 0 10px}
.lede{font-size:1.06rem;color:var(--t2);margin:0 0 18px}
.meta{display:flex;flex-wrap:wrap;gap:8px 14px;font-family:var(--mono);font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:var(--t3);margin:0 0 20px}
.meta b{color:var(--acc);font-weight:600}.meta .amb{color:var(--amb)}
.box{background:var(--bg-1);border:1px solid var(--border);border-left:2px solid var(--acc);border-radius:6px;padding:14px 16px;color:var(--t2);margin:0 0 12px}
.box.amb{border-left-color:var(--amb)}.box>span{display:block;font-family:var(--mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--t4);margin-bottom:6px}
p{color:var(--t2)}ul.links{list-style:none;padding:0;margin:0;display:grid;gap:8px}
ul.links li{background:var(--bg-1);border:1px solid var(--border);border-radius:6px;padding:10px 12px}
ul.links li small{display:block;color:var(--t3)}ol.steps{padding-left:20px;color:var(--t2)}ol.steps li{margin:0 0 6px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}
.card{display:block;background:var(--bg-1);border:1px solid var(--border);border-radius:10px;padding:14px;text-decoration:none;color:var(--t1)}
.card:hover{border-color:var(--border-hi);background:var(--bg-2)}.card b{display:block;margin-bottom:4px;overflow-wrap:anywhere}.card span{color:var(--t2);font-size:.86rem}
.card i{font-style:normal;display:block;margin-top:8px;font-family:var(--mono);font-size:10.5px;color:var(--t3);text-transform:uppercase;letter-spacing:.04em}
.cta{margin:44px 0 0;border:1px solid rgba(61,220,151,.35);border-radius:14px;padding:22px;background:radial-gradient(500px 200px at 0 0,rgba(61,220,151,.12),transparent 70%),var(--bg-1)}
.cta h2{margin:0 0 6px}.cta form{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.cta input{flex:1 1 220px;background:var(--bg);border:1px solid var(--border-hi);color:var(--t1);border-radius:6px;padding:10px 12px;font:inherit}
.cta .msg{font-size:.86rem;color:var(--acc);margin:8px 0 0}.cta .msg:empty{display:none}.cta .soon{margin:12px 0 0;color:var(--t1)}
.plain{display:block;margin:0 0 18px;border:1px solid rgba(61,220,151,.35);border-radius:12px;overflow:hidden;background:var(--bg-1)}
.plain img{display:block;width:100%;aspect-ratio:1200/630;height:auto;background:#f4f7f2}
.plain div{padding:14px 16px}.plain span{display:block;font-family:var(--mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--acc);margin-bottom:6px}
.plain b{display:block;font-size:1.08rem;line-height:1.45;color:var(--t1);font-weight:600}.plain i{display:block;font-style:normal;color:var(--t3);margin-top:4px}
dl.kv{display:grid;grid-template-columns:120px 1fr;gap:6px 12px;margin:0}dl.kv dt{color:var(--t4);font-family:var(--mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;padding-top:4px}dl.kv dd{margin:0;color:var(--t2)}
footer{max-width:1120px;margin:56px auto 0;padding:24px 20px 48px;border-top:1px solid var(--border);color:var(--t3);font-size:.84rem}
footer a{color:var(--t2);text-decoration:none;margin-right:14px}
.brand{white-space:nowrap}@media(max-width:560px){.nl a.x{display:none}.nl a{margin-left:10px}}
"""

MARK = ('<svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="12" fill="none" stroke="#3ddc97" '
        'stroke-width="2.5"/><circle cx="16" cy="16" r="6.5" fill="none" stroke="#3ddc97" stroke-opacity=".45" '
        'stroke-width="2"/><circle cx="16" cy="16" r="2.6" fill="#3ddc97"/></svg>')

SIGNUP_JS = """<script>
document.querySelectorAll("form[data-kit]").forEach(function(f){f.addEventListener("submit",function(ev){
ev.preventDefault();var m=f.parentNode.querySelector(".msg"),id=f.getAttribute("data-kit"),v=f.email_address.value.trim();
if(!/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(v)){m.textContent="That email doesn't look right.";return;}
if(!id){m.textContent="Sign-ups open with the first issue. Check back soon.";return;}
var b=new FormData();b.append("email_address",v);
fetch("https://app.kit.com/forms/"+encodeURIComponent(id)+"/subscriptions",{method:"POST",body:b,mode:"no-cors"})
.then(function(){m.textContent="Almost done. Check your inbox and confirm your subscription.";f.reset();})
.catch(function(){m.textContent="Couldn't reach the newsletter service. Try again in a minute.";});});});
</script>"""


def cta_block(cfg):
    form_id = ((cfg.get("kit") or {}).get("form_id") or "").strip()
    head = ('<section class="cta"><h2>Get the week\'s best AI launches, plus 3 ideas worth building</h2>'
            '<p style="margin:0">One email every Saturday. Ranked by traction, not hype. Free.</p>')
    if not form_id:
        # no Kit form yet: say so plainly instead of showing a form that can't subscribe
        return head + '<p class="soon">The first issue goes out Saturday. Sign-ups open here shortly.</p></section>'
    return head + (
        '<form data-kit="%s">'
        '<input type="email" name="email_address" aria-label="Email address" placeholder="you@company.com" autocomplete="email" required>'
        '<button class="btn" type="submit">Get the weekly</button></form><p class="msg" aria-live="polite"></p></section>'
        % e(form_id))


def page(cfg, path, title, desc, body, jsonld=None, og_type="article", wide=False):
    base = cfg["base_url"].rstrip("/")
    url = base + path
    ld = ""
    if jsonld:
        ld = '<script type="application/ld+json">%s</script>' % json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/")
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><meta name="description" content="{desc}"><link rel="canonical" href="{url}">
<meta property="og:type" content="{og}"><meta property="og:site_name" content="{brand}"><meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}"><meta property="og:url" content="{url}"><meta property="og:image" content="{base}/assets/og.png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="{base}/assets/og.png">
<meta name="theme-color" content="#0a0c0b"><link rel="alternate" type="application/rss+xml" title="{brand}" href="/feed.xml">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%230a0c0b'/%3E%3Ccircle cx='16' cy='16' r='9' fill='none' stroke='%233ddc97' stroke-width='2.5'/%3E%3Ccircle cx='16' cy='16' r='3' fill='%233ddc97'/%3E%3C/svg%3E">
<style>{css}</style>{ld}</head><body>
<nav><a class="brand" href="/">{mark}{brand}</a><span class="nl"><a class="x" href="/#live">Live</a><a href="/#ideas">Ideas</a><a class="x" href="/category/">Categories</a><a href="/weekly/">Newsletter</a></span></nav>
<main class="wrap{wide}">{body}{cta}</main>
<footer><a href="/">Live radar</a><a href="/category/">Categories</a><a href="/weekly/">Newsletter</a><a href="/feed.xml">RSS</a><a href="https://github.com/{repo}">Data on GitHub</a>
<p>{brand}: new AI models, agents and dev tools, ranked by real traction instead of hype.</p></footer>{js}</body></html>
""".format(title=e(title), desc=e(desc), url=e(url), og=og_type, brand=e(cfg["brand"]), css=PAGE_CSS,
           ld=ld, mark=MARK, body=body, cta=cta_block(cfg), repo=e(cfg["repo"]), js=SIGNUP_JS,
           wide=" wide" if wide else "", base=e(base))


def write(out, path, content):
    full = os.path.join(out, path.lstrip("/"))
    if path.endswith("/"):
        full = os.path.join(full, "index.html")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def crumbs(*parts):
    bits = ['<a href="/">Radar</a>']
    for label, href in parts:
        bits.append('<a href="%s">%s</a>' % (e(href), e(label)) if href else e(label))
    return '<div class="crumbs">%s</div>' % " / ".join(bits)


def breadcrumb_ld(base, parts):
    items = [{"@type": "ListItem", "position": 1, "name": "Radar", "item": base + "/"}]
    for i, (label, href) in enumerate(parts, start=2):
        items.append({"@type": "ListItem", "position": i, "name": label, "item": base + href})
    return {"@type": "BreadcrumbList", "itemListElement": items}


def launch_card(l):
    jev = l.get("jev_score")
    return ('<a class="card" href="/launch/%s/"><b>%s</b><span>%s</span><i>%s%s</i></a>' % (
        e(slug_for(l["id"])), e(l.get("title", "")), e(clip(l.get("summary"), 120)),
        e(l.get("category") or "misc"), (" · JEV %s" % jev) if jev is not None else ""))


def idea_card(it):
    return ('<a class="card" href="/idea/%s/"><b>%s</b><span>%s</span><i>%s · JEV confidence %s</i></a>' % (
        e(slug_for(it["id"])), e(it.get("title", "")), e(clip(it.get("outcome") or it.get("problem"), 120)),
        e(it.get("difficulty") or "idea"), it.get("jev_score") if it.get("jev_score") is not None else "–"))


# ---------------------------------------------------------------- pages

def article_ld(cfg, headline, desc, published, path, extra=None):
    base = cfg["base_url"].rstrip("/")
    brand = {"@type": "Organization", "name": cfg["brand"], "url": base + "/"}
    ld = {"@type": "Article", "headline": headline, "description": desc,
          "image": base + "/assets/og.png", "author": brand, "publisher": brand,
          "datePublished": published, "dateModified": BUILD_DATE, "mainEntityOfPage": base + path}
    ld.update(extra or {})
    return ld


def plain_block(pick):
    """Today's debate Spotlight card + plain-English gist, for a launch page."""
    if not pick:
        return ""
    img = ('<img src="/%s" alt="%s" width="1200" height="630" loading="lazy" onerror="this.remove()">'
           % (e(pick["image"]), e(pick.get("gist") or "")) if pick.get("image") else "")
    return ('<div class="plain">%s<div><span>In today\'s Spotlight · %s</span><b>%s</b>%s</div></div>' % (
        img, e(pick.get("reason") or "picked by the model panel"), e(pick.get("gist") or ""),
        ("<i>%s</i>" % e(pick["analogy"])) if pick.get("analogy") else ""))


def launch_page(cfg, l, by_cat, seo, spot_pick=None):
    base = cfg["base_url"].rstrip("/")
    slug = slug_for(l["id"])
    path = "/launch/%s/" % slug
    title, desc = seo_launch(l, seo, title_budget(cfg))
    cat = l.get("category") or ""
    cat_label = CATEGORIES.get(cat, ("AI launches",))[0]
    meta = []
    meta.append("<span>%s</span>" % ("trending" if l.get("kind") == "trending" else "new launch"))
    if cat:
        meta.append('<span><a href="/category/%s/" style="color:inherit">%s</a>%s</span>' % (
            e(cat), e(cat), (" · " + e(l["subcategory"])) if l.get("subcategory") else ""))
    if l.get("jev_score") is not None:
        meta.append("<span>JEV traction <b>%s</b></span>" % l["jev_score"])
    if l.get("github_repo"):
        meta.append('<span><a href="https://github.com/%s" style="color:inherit">%s</a> · %s ★</span>' % (
            e(l["github_repo"]), e(l["github_repo"]), "{:,}".format(l.get("github_stars") or 0)))
    meta.append("<span>added %s</span>" % e(l.get("added_at") or ""))
    body = [crumbs((cat_label, "/category/%s/" % cat if cat else "/category/"), (short_name(l.get("title")), None)),
            "<h1>%s</h1>" % e(l.get("title", "")),
            plain_block(spot_pick),
            '<p class="lede">%s</p>' % e(l.get("summary") or ""),
            '<div class="meta">%s</div>' % "".join(meta),
            '<div class="actions"><a class="btn" href="%s" rel="noopener">Open %s →</a>'
            '<a class="btn ghost" href="/#launch=%s">View on the radar</a></div>' % (
                e(safe_url(l.get("url"))), e(short_name(l.get("title"))), e(l["id"]))]
    if l.get("usp"):
        body.append('<h2>Why it matters</h2><div class="box">%s</div>' % e(l["usp"]))
    idea = public_idea(l)
    if idea:
        body.append('<h2>What you could build with it</h2><div class="box amb">%s</div>' % e(idea))
    if l.get("usability"):
        body.append("<h2>Does it hold up?</h2><p>%s</p>" % e(l["usability"]))
    builds = []
    for b in l.get("community_builds") or []:
        builds.append('<li><a href="%s" rel="noopener nofollow">%s</a><small>%s%s</small></li>' % (
            e(safe_url(b.get("url"))), e(b.get("title") or b.get("url") or ""), e(b.get("source") or "web"),
            (" · " + e(b["summary"])) if b.get("summary") else ""))
    for r in l.get("implementation_repos") or []:
        builds.append('<li><a href="https://github.com/%s" rel="noopener nofollow">%s</a><small>github%s%s</small></li>' % (
            e(r.get("repo") or ""), e(r.get("repo") or ""),
            (" · %s ★" % "{:,}".format(r["stars"])) if r.get("stars") else "",
            (" · " + e(r["summary"])) if r.get("summary") else ""))
    if builds:
        body.append('<h2>Built with %s</h2><ul class="links">%s</ul>' % (e(short_name(l.get("title"))), "".join(builds)))
    if l.get("learn_url"):
        body.append('<h2>Learn more</h2><p><a href="%s" rel="noopener">%s →</a></p>' % (
            e(safe_url(l["learn_url"])), e(l.get("learn_label") or "Deep dive")))
    if l.get("source_url"):
        body.append('<p style="font-size:.86rem">First spotted on %s: <a href="%s" rel="noopener nofollow">source</a>.</p>' % (
            e(l.get("source") or "the web"), e(safe_url(l["source_url"]))))
    related = [x for x in by_cat.get(cat, []) if x["id"] != l["id"]][:6]
    if related:
        body.append("<h2>More %s</h2><div class=\"grid\">%s</div>" % (e(cat_label), "".join(launch_card(x) for x in related)))
    about = {"@type": "SoftwareApplication", "name": short_name(l.get("title")), "applicationCategory": cat_label}
    if safe_url(l.get("url")) != "#":
        about["url"] = l["url"]
    ld = {"@context": "https://schema.org", "@graph": [
        article_ld(cfg, clip(l.get("title"), 110), desc, l.get("added_at"), path, {"about": about}),
        breadcrumb_ld(base, [(cat_label, "/category/%s/" % cat if cat else "/category/"),
                             (short_name(l.get("title")), path)])]}
    return path, page(cfg, path, "%s | %s" % (title, cfg["brand"]), desc, "".join(body), ld)


def idea_page(cfg, it, entries_by_id, seo):
    base = cfg["base_url"].rstrip("/")
    path = "/idea/%s/" % slug_for(it["id"])
    title, desc = seo_idea(it, seo, title_budget(cfg))
    meta = ["<span>%s</span>" % ("weekly debate · " + e(it.get("week") or "") if it.get("origin") == "weekly-debate" else "daily idea"),
            "<span>%s</span>" % e(it.get("difficulty") or "buildable"),
            '<span>JEV confidence <b class="amb">%s</b></span>' % (it.get("jev_score") if it.get("jev_score") is not None else "–"),
            "<span>%s</span>" % e(it.get("added_at") or "")]
    body = [crumbs(("Ideas", "/#ideas"), (clip(it.get("title"), 40), None)),
            "<h1>%s</h1>" % e(it.get("title", "")),
            '<div class="meta">%s</div>' % "".join(meta)]
    if it.get("outcome"):
        body.append('<div class="box amb"><span>Outcome</span>%s</div>' % e(it["outcome"]))

    def sec(h, txt):
        if txt:
            body.append("<h2>%s</h2><p>%s</p>" % (h, e(txt)))
    sec("The problem", it.get("problem"))
    if it.get("idea"):
        body.append('<h2>The idea</h2><div class="box">%s</div>' % e(it["idea"]))
    sec("Why now", it.get("why_now"))
    sec("What it combines", it.get("collision"))
    plan = it.get("implementation_plan") or []
    if plan:
        body.append('<h2>How to build it</h2><ol class="steps">%s</ol>' % "".join(
            "<li><strong>%s.</strong> %s%s</li>" % (e(s.get("step") or ""), e(s.get("detail") or ""),
                                                    (" <em>(%s)</em>" % e(s["effort"])) if s.get("effort") else "")
            for s in plan))
    else:
        sec("MVP", it.get("mvp"))
    bens = it.get("beneficiaries") or []
    if bens:
        body.append('<h2>Who benefits</h2><dl class="kv">%s</dl>' % "".join(
            "<dt>%s</dt><dd>%s</dd>" % (e(b.get("who") or ""), e(b.get("benefit") or "")) for b in bens))
    m = it.get("monetization") or {}
    if m:
        rows = [("Model", m.get("model")), ("Pricing", m.get("pricing")), ("Wedge", m.get("wedge")),
                ("Channels", " · ".join(m.get("channels") or []))]
        body.append('<h2>How it makes money</h2><dl class="kv">%s</dl>' % "".join(
            "<dt>%s</dt><dd>%s</dd>" % (k, e(v)) for k, v in rows if v))
    sec("Distribution", it.get("distribution"))
    sec("Why it wins", it.get("differentiation"))
    sec("Risks", it.get("risks"))
    if (it.get("debate") or {}).get("dissent"):
        sec("Strongest objection left standing", it["debate"]["dissent"])
    bw = []
    for b in it.get("build_with") or []:
        ent = entries_by_id.get(b.get("entry_id") or "")
        link = ('<a href="/launch/%s/">%s</a>' % (e(slug_for(ent["id"])), e(ent.get("title", ""))) if ent
                else "<strong>%s</strong>" % e(b.get("name") or ""))
        bw.append("<li>%s<small>%s</small></li>" % (link, e(b.get("reason") or "")))
    if bw:
        body.append('<h2>Build it with</h2><ul class="links">%s</ul>' % "".join(bw))
    if it.get("repo_suggestion"):
        sec("Repo to start from", it["repo_suggestion"])
    src = it.get("sources") or []
    if src:
        body.append('<h2>Evidence</h2><ul class="links">%s</ul>' % "".join(
            '<li><a href="%s" rel="noopener nofollow">%s</a></li>' % (e(safe_url(s.get("url"))), e(s.get("title") or s.get("url") or ""))
            for s in src))
    if (it.get("debate") or {}).get("models"):
        body.append('<p style="font-size:.86rem">Debated by %s. Judged by JEV.</p>' % e(", ".join(it["debate"]["models"])))
    ld = {"@context": "https://schema.org", "@graph": [
        article_ld(cfg, clip(it.get("title"), 110), desc, it.get("added_at"), path),
        breadcrumb_ld(base, [("Ideas", "/#ideas"), (clip(it.get("title"), 40), path)])]}
    return path, page(cfg, path, "%s | %s" % (title, cfg["brand"]), desc, "".join(body), ld)


def category_pages(cfg, by_cat):
    out = []
    cards = []
    for cat, (label, blurb) in CATEGORIES.items():
        items = by_cat.get(cat, [])
        cards.append('<a class="card" href="/category/%s/"><b>%s</b><span>%s</span><i>%d tracked</i></a>' % (
            e(cat), e(label), e(blurb), len(items)))
        subs = {}
        for l in items:
            subs.setdefault(l.get("subcategory") or "other", []).append(l)
        body = [crumbs(("Categories", "/category/"), (label, None)),
                "<h1>New %s, ranked by traction</h1>" % e(label),
                '<p class="lede">%s Every launch from the last few weeks, ranked by JEV traction and GitHub stars. Updated four times a day.</p>' % e(blurb)]
        for sub, ls in sorted(subs.items(), key=lambda kv: -len(kv[1])):
            body.append('<h2 id="%s">%s <span style="color:var(--t3);font-weight:400">· %d</span></h2><div class="grid">%s</div>' % (
                e(sub), e(sub.replace("-", " ").capitalize()), len(ls), "".join(launch_card(x) for x in ls[:24])))
        if not items:
            body.append("<p>Nothing tracked here yet.</p>")
        path = "/category/%s/" % cat
        title = "New %s, ranked by traction | %s" % (label, cfg["brand"])
        desc = clip("%s Ranked by JEV traction and GitHub stars, with real community builds. Updated four times a day." % blurb, 155)
        out.append((path, page(cfg, path, title, desc, "".join(body), og_type="website", wide=True)))
    body = (crumbs(("Categories", None)) + "<h1>AI launches by category</h1>"
            '<p class="lede">Browse new AI releases by what they are. Each page ranks the latest launches by real traction.</p>'
            '<div class="grid">%s</div>' % "".join(cards))
    out.append(("/category/", page(cfg, "/category/", "AI launches by category | %s" % cfg["brand"],
                                   "Browse new AI models, agents, coding tools, infrastructure, media, robotics and security launches, ranked by traction.",
                                   body, og_type="website", wide=True)))
    return out


def digest_pages(cfg):
    """Web versions of past newsletter issues, rendered by scripts/digest.py."""
    out = []
    ddir = os.path.join(DATA, "digests")
    if not os.path.isdir(ddir):
        return out, []
    try:
        import digest
    except Exception as ex:  # digest.py missing or broken: skip, never fail the site
        print("digest pages skipped: %s" % ex)
        return out, []
    issues = []
    for name in sorted(os.listdir(ddir), reverse=True):
        m = re.match(r"(weekly|monthly)-(\d{4}-(?:W\d{2}|\d{2}))\.json$", name)
        if not m:
            continue
        snap = load_json(os.path.join(ddir, name), {})
        try:
            r = digest.render(snap, cfg)
        except Exception as ex:
            print("digest render failed for %s: %s" % (name, ex))
            continue
        path = "/%s/%s/" % (m.group(1), m.group(2))
        full = r["full_html"]
        canon = '<link rel="canonical" href="%s%s">' % (e(cfg["base_url"].rstrip("/")), path)
        full = full.replace("</head>", canon + '<meta name="description" content="%s"></head>' % e(r.get("preheader", "")), 1)
        out.append((path, full))
        issues.append((path, m.group(1), m.group(2), r.get("subject", name)))
    return out, issues


def issues_index(cfg, issues):
    if issues:
        body = '<ul class="links">%s</ul>' % "".join(
            '<li><a href="%s">%s</a><small>%s · %s</small></li>' % (e(p), e(subj), e(kind), e(per)) for p, kind, per, subj in issues)
    else:
        body = "<p>The first issue goes out soon. Subscribe below to get it.</p>"
    html_ = (crumbs(("Newsletter", None)) + "<h1>%s</h1>" % e(cfg["kit"].get("newsletter_weekly", "The weekly")) +
             '<p class="lede">Every Saturday: the week\'s Spotlight launches and 3 product ideas that five AI models argued over and JEV judged. '
             'On the first Sunday of each month: the best idea of the month, in depth.</p>' + body)
    return "/weekly/", page(cfg, "/weekly/", "Newsletter archive | %s" % cfg["brand"],
                            "Past issues of the Fresh Weights weekly and monthly AI launch digests.", html_, og_type="website")


def ssr_snapshot(spot, live, ideas):
    """Plain links for crawlers/no-JS. Removed by the dashboard on load."""
    parts = ["<h2>Spotlight</h2><ul>"]
    for p in spot.get("picks", []):
        parts.append('<li><a href="/launch/%s/">%s</a>: %s</li>' % (e(slug_for(p["id"])), e(p.get("title", "")), e(clip(p.get("summary"), 140))))
    parts.append("</ul><h2>Latest AI launches</h2><ul>")
    for l in sorted(live, key=lambda x: x.get("added_at") or "", reverse=True)[:60]:
        parts.append('<li><a href="/launch/%s/">%s</a>: %s</li>' % (e(slug_for(l["id"])), e(l.get("title", "")), e(clip(l.get("summary"), 140))))
    parts.append("</ul><h2>Ideas worth building</h2><ul>")
    for it in sorted(ideas, key=lambda x: x.get("added_at") or "", reverse=True)[:20]:
        parts.append('<li><a href="/idea/%s/">%s</a></li>' % (e(slug_for(it["id"])), e(it.get("title", ""))))
    parts.append('</ul><p><a href="/category/">Browse by category</a></p>')
    return "".join(parts)


def rss(cfg, live):
    base = cfg["base_url"].rstrip("/")
    items = []
    for l in sorted(live, key=lambda x: x.get("added_at") or "", reverse=True)[:50]:
        try:
            d = datetime.fromisoformat(str(l.get("added_at"))[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            d = datetime.now(timezone.utc)
        link = "%s/launch/%s/" % (base, slug_for(l["id"]))
        items.append("<item><title>%s</title><link>%s</link><guid isPermaLink=\"true\">%s</guid><pubDate>%s</pubDate>"
                     "<category>%s</category><description>%s</description></item>" % (
                         e(l.get("title", "")), e(link), e(link), format_datetime(d), e(l.get("category") or "misc"),
                         e((l.get("summary") or "") + (" Why it matters: " + l["usp"] if l.get("usp") else ""))))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
            "<title>%s: new AI launches</title><link>%s/</link><description>%s</description><language>en</language>"
            '<atom:link href="%s/feed.xml" rel="self" type="application/rss+xml"/>%s</channel></rss>\n' % (
                e(cfg["brand"]), base, e(cfg["description"]), base, "".join(items)))


def sitemap(cfg, entries):
    base = cfg["base_url"].rstrip("/")
    rows = []
    for path, lastmod in entries:
        rows.append("<url><loc>%s%s</loc>%s</url>" % (e(base), e(path), ("<lastmod>%s</lastmod>" % lastmod) if lastmod else ""))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">%s</urlset>\n'
            % "".join(rows))


def collect_graphics(out, spot, ideas):
    """Write one HTML card per unique visual spec plus a manifest for
    scripts/render_graphics.mjs. Specs come from today's Spotlight, ideas and
    every digest snapshot, so newsletter images stay online for good."""
    specs = []
    for p in spot.get("picks", []):
        if p.get("visual"):
            specs.append(p["visual"])
    for it in ideas:
        if it.get("visual"):
            specs.append(it["visual"])
    ddir = os.path.join(DATA, "digests")
    if os.path.isdir(ddir):
        for name in sorted(os.listdir(ddir)):
            if name.endswith(".json"):
                snap = load_json(os.path.join(ddir, name), {}) or {}
                for x in (snap.get("spotlights") or []) + (snap.get("ideas") or []) + [snap.get("idea") or {}]:
                    if x.get("visual"):
                        specs.append(x["visual"])
    jobs, seen = [], set()
    gdir = os.path.join(out, "_graphics")
    for spec in specs:
        png = visuals.image_path(spec)
        if png in seen:
            continue
        seen.add(png)
        os.makedirs(gdir, exist_ok=True)
        name = "card-%d.html" % len(jobs)
        with open(os.path.join(gdir, name), "w", encoding="utf-8") as f:
            f.write(visuals.card_html(spec))
        jobs.append({"html": "_graphics/" + name, "png": png})
    if jobs:
        with open(os.path.join(gdir, "manifest.json"), "w") as f:
            json.dump(jobs, f)
    return len(jobs)


def build(out):
    cfg = load_config()
    cfg.setdefault("kit", {})
    live = load_json(os.path.join(DATA, "launches.json"), {}).get("launches", [])
    arch = load_json(os.path.join(DATA, "archive.json"), {}).get("launches", [])
    ideas = load_json(os.path.join(DATA, "ideas.json"), {}).get("ideas", [])
    spot = load_json(os.path.join(DATA, "spotlight.json"), {}) or {}
    seo = (load_json(os.path.join(DATA, "seo.json"), {}) or {}).get("entries", {})

    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    for name in STATIC_FILES:
        src = os.path.join(ROOT, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(out, name))
    shutil.copytree(DATA, os.path.join(out, "data"))
    if os.path.isdir(os.path.join(ROOT, "assets")):
        shutil.copytree(os.path.join(ROOT, "assets"), os.path.join(out, "assets"))

    # every entry once; live wins over a stale archive copy
    seen, entries = set(), []
    for l in live + arch:
        if l.get("id") and l["id"] not in seen:
            seen.add(l["id"])
            entries.append(l)
    entries_by_id = {l["id"]: l for l in entries}
    for it in ideas:
        for b in it.get("build_with") or []:
            if b.get("entry_id") and b["entry_id"] not in entries_by_id:
                print("warning: idea %s builds with unknown entry_id %s" % (it.get("id"), b["entry_id"]))
    spot_picks = {}
    if spot.get("picked_by") == "debate":
        spot_picks = {p["id"]: p for p in spot.get("picks", []) if p.get("id") and p.get("gist")}
    by_cat = {}
    for l in sorted(entries, key=lambda x: ((x.get("jev_score") or 0), x.get("github_stars") or 0), reverse=True):
        by_cat.setdefault(l.get("category") or "", []).append(l)

    sm = [("/", spot.get("updated_at", "")[:10] or None)]
    slugs = {}
    for l in entries:
        slug = slug_for(l["id"])
        if slug in slugs:
            print("slug collision, skipping page: %s vs %s" % (l["id"], slugs[slug]))
            continue
        slugs[slug] = l["id"]
        path, html_ = launch_page(cfg, l, by_cat, seo, spot_picks.get(l["id"]))
        write(out, path, html_)
        sm.append((path, (l.get("added_at") or "")[:10] or None))
    for it in ideas:
        if not it.get("id"):
            continue
        path, html_ = idea_page(cfg, it, entries_by_id, seo)
        write(out, path, html_)
        sm.append((path, (it.get("added_at") or "")[:10] or None))
    for path, html_ in category_pages(cfg, by_cat):
        write(out, path, html_)
        sm.append((path, None))
    dpages, issues = digest_pages(cfg)
    for path, html_ in dpages:
        write(out, path, html_)
        sm.append((path, None))
    path, html_ = issues_index(cfg, issues)
    write(out, path, html_)
    sm.append((path, None))

    index_path = os.path.join(out, "index.html")
    with open(index_path, encoding="utf-8") as f:
        idx = f.read()
    idx = re.sub(r"<!--SSR:START-->.*?<!--SSR:END-->",
                 lambda _m: "<!--SSR:START-->" + ssr_snapshot(spot, live, ideas) + "<!--SSR:END-->", idx, flags=re.S)
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(idx)

    n_cards = collect_graphics(out, spot, ideas)
    write(out, "/feed.xml", rss(cfg, live))
    write(out, "/sitemap.xml", sitemap(cfg, sm))
    write(out, "/robots.txt", "User-agent: *\nAllow: /\nSitemap: %s/sitemap.xml\n" % cfg["base_url"].rstrip("/"))
    print("built %d launch, %d idea, %d category, %d digest pages, %d cards to render -> %s" % (
        len(slugs), len(ideas), len(CATEGORIES) + 1, len(dpages), n_cards, out))
    print("hid %d implementation ideas that read like private notes" % len(FILTERED_IDEAS))


def main():
    global DATA
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "_site"))
    ap.add_argument("--data-dir", default=None, help="read data from here instead of data/ (previews, tests)")
    args = ap.parse_args()
    if args.data_dir:
        DATA = os.path.abspath(args.data_dir)
    build(os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
