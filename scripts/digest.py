#!/usr/bin/env python3
"""Weekly and monthly newsletter digests for Fresh Weights (freshweights.com).

Builds a digest snapshot from radar data, renders it as an email (HTML for
Kit's custom template plus a plain-text version), writes previews, and can
create a DRAFT broadcast in Kit. It never sends or schedules: a human
presses send in Kit.

Weekly  = the week's Spotlights (ranked by hours held, top 6) + the week's
          debated ideas (origin "weekly-debate"), falling back to the top 3
          daily ideas of the week by JEV score.
Monthly = the single best idea of the month + the month's Spotlights (top 8).

Spotlight history: data/spotlight.json only holds the current picks and is
committed hourly, so each period's history is rebuilt from git
(`git log` + `git show`). If the local checkout is shallow or lacks the
period, the public GitHub REST API is used (commit list, then
raw.githubusercontent.com per commit); last resort is the current file.

Usage:
  python3 scripts/digest.py weekly  [--week 2026-W39]  [--kit-draft] [--out-dir newsletter/out] [--data-dir data]
  python3 scripts/digest.py monthly [--month 2026-09]  [--kit-draft] [--out-dir newsletter/out] [--data-dir data]

Default period: the most recently COMPLETED ISO week (Mon-Sun, IST) or
calendar month. Writes <data-dir>/digests/weekly-YYYY-Www.json (or
monthly-YYYY-MM.json) and <out-dir>/<name>.html (full preview),
<name>.content.html (Kit message body) and <name>.txt.

Other scripts can import `render(snapshot, cfg)` to publish web versions.

Credentials:
  KIT_API_KEY      Kit API v4 key, injected by the cron from the Secure
                   Vault. Only needed with --kit-draft. Never printed,
                   logged or written to disk.
  KIT_TEMPLATE_ID  Optional id of the uploaded custom email template.
The GitHub fallback uses public unauthenticated endpoints only.
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jevlib import (DATA, IST, ROOT, iso_week, load_config, load_json,  # noqa: E402
                    now_ist, save_json, slug_for)

TEMPLATE_PATH = os.path.join(ROOT, "newsletter", "kit-template.html")
SPOTLIGHT_REL = "data/spotlight.json"
KIT_BROADCASTS_URL = "https://api.kit.com/v4/broadcasts"
GITHUB_API = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"
UA = "fresh-weights-digest"

WEEKLY_SPOTLIGHTS = 6
MONTHLY_SPOTLIGHTS = 8
WEEKLY_IDEAS = 3

# Brand tokens
INK = "#0f1412"
TEXT = "#2b3330"
MUTED = "#66706b"
RULE = "#e3e8e5"
PAGE = "#f4f6f5"
CARD = "#ffffff"
SOFT = "#f8faf9"
MINT = "#12b981"
MINT_TEXT = "#0b8a5f"
AMBER = "#b7791f"
FONT = "system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

# implementation_idea is sometimes written for the owner's own stack; those
# lines must not reach subscribers.
PRIVATE_HINTS = re.compile(
    r"\b(jev|laya|kev|upstox|zerodha|trading|four-seat|seats?|radar'?s?|boringalgos|muse|"
    r"instinct|openrouter|droplet|x-triage|x dm|dm drafts?|builder (?:agents?|fleet)|"
    r"agent stack|current default|cron|workers?|dashboard|overnight|regime|ingest|our|my|we|us)\b|\bthe [\w-]+ pipeline\b", re.I)


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------

def week_bounds(week):
    """'2026-W40' -> (monday, sunday) dates."""
    m = re.fullmatch(r"(\d{4})-W(\d{2})", week or "")
    if not m:
        raise ValueError("week must look like 2026-W40, got %r" % week)
    monday = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    return monday, monday + timedelta(days=6)


def month_bounds(month):
    """'2026-09' -> (first, last) dates."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", month or "")
    if not m:
        raise ValueError("month must look like 2026-09, got %r" % month)
    first = date(int(m.group(1)), int(m.group(2)), 1)
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def last_completed_week(now=None):
    today = (now or now_ist()).date()
    return iso_week(today - timedelta(days=today.isoweekday()))


def last_completed_month(now=None):
    today = (now or now_ist()).date()
    prev = today.replace(day=1) - timedelta(days=1)
    return "%04d-%02d" % (prev.year, prev.month)


def period_datetimes(start, end, now=None):
    """IST datetimes covering whole days start..end, capped at now."""
    since = datetime.combine(start, time.min, IST)
    until = datetime.combine(end + timedelta(days=1), time.min, IST) - timedelta(seconds=1)
    now = now or now_ist()
    return since, min(until, now)


def week_of_month(week, month):
    """ISO convention: a week belongs to the month holding its Thursday."""
    try:
        monday, _ = week_bounds(week)
    except ValueError:
        return False
    thu = monday + timedelta(days=3)
    return "%04d-%02d" % (thu.year, thu.month) == month


def in_range(value, start, end):
    try:
        d = date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return False
    return start <= d <= end


# --------------------------------------------------------------------------
# Spotlight history
# --------------------------------------------------------------------------

def _git(args, root):
    proc = subprocess.run(["git", "-C", root] + args, capture_output=True,
                          text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:200])
    return proc.stdout


def git_spotlight_shas(since, until, root=ROOT):
    """Commits touching data/spotlight.json in [since, until], oldest first.
    None if git is unusable here."""
    try:
        out = _git(["log", "--since=" + since.isoformat(),
                    "--until=" + until.isoformat(), "--format=%H",
                    "--", SPOTLIGHT_REL], root)
    except Exception as e:
        print("git log unavailable: %s" % e)
        return None
    return list(reversed([s for s in out.split() if s]))


def git_history_covers(since, root=ROOT):
    """True if local history reaches back to `since` (not cut by a shallow
    clone)."""
    try:
        shallow = _git(["rev-parse", "--is-shallow-repository"], root).strip()
        if shallow != "true":
            return True
        dates = _git(["log", "--format=%cI"], root).split()
        if not dates:
            return False
        oldest = datetime.fromisoformat(dates[-1])
        return oldest <= since
    except Exception:
        return False


def git_spotlight_at(sha, root=ROOT):
    try:
        return json.loads(_git(["show", "%s:%s" % (sha, SPOTLIGHT_REL)], root))
    except Exception:
        return None


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def github_spotlight_shas(repo, since, until, get=http_get, max_pages=20):
    """Commit shas touching spotlight.json via the public GitHub API,
    oldest first. None on failure."""
    s = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    u = until.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    shas = []
    try:
        for page in range(1, max_pages + 1):
            url = ("%s/repos/%s/commits?path=%s&since=%s&until=%s&per_page=100&page=%d"
                   % (GITHUB_API, repo, SPOTLIGHT_REL, s, u, page))
            batch = json.loads(get(url))
            shas.extend(c["sha"] for c in batch)
            if len(batch) < 100:
                break
    except Exception as e:
        print("GitHub API commit list failed: %s" % e)
        return None
    return list(reversed(shas))


def github_spotlight_at(repo, sha, get=http_get):
    try:
        return json.loads(get("%s/%s/%s/%s" % (RAW_BASE, repo, sha, SPOTLIGHT_REL)))
    except Exception:
        return None


def spotlight_history(since, until, data_dir=DATA, root=ROOT, repo=None,
                      get=http_get):
    """Returns (list of spotlight.json snapshots, source label).
    source: 'git' | 'github-api' | 'git-partial' | 'current'."""
    shas = git_spotlight_shas(since, until, root)
    if shas and git_history_covers(since, root):
        snaps = [s for s in (git_spotlight_at(h, root) for h in shas) if s]
        if snaps:
            print("spotlight history: %d snapshots from git" % len(snaps))
            return snaps, "git"
    if repo:
        api_shas = github_spotlight_shas(repo, since, until, get=get)
        if api_shas:
            def fetch(sha):
                return git_spotlight_at(sha, root) or github_spotlight_at(repo, sha, get=get)
            with ThreadPoolExecutor(max_workers=8) as pool:
                snaps = [s for s in pool.map(fetch, api_shas) if s]
            if snaps:
                print("spotlight history: %d snapshots via GitHub API" % len(snaps))
                return snaps, "github-api"
    if shas:
        snaps = [s for s in (git_spotlight_at(h, root) for h in shas) if s]
        if snaps:
            print("spotlight history: %d snapshots from partial git history" % len(snaps))
            return snaps, "git-partial"
    cur = load_json(os.path.join(data_dir, "spotlight.json"), {}) or {}
    print("spotlight history: falling back to current spotlight.json")
    return ([cur] if cur.get("picks") else []), "current"


def rank_spotlights(snapshots, limit):
    """Count per-id appearances (= hours held) and keep the latest pick
    fields. Ties break on composite then JEV score."""
    hours, latest = Counter(), {}
    for snap in snapshots:
        seen = set()
        for pick in snap.get("picks", []) or []:
            pid = pick.get("id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            hours[pid] += 1
            latest[pid] = pick
    ids = sorted(hours, key=lambda i: (-hours[i],
                                       -(latest[i].get("composite") or 0),
                                       -(latest[i].get("jev_score") or 0)))
    return [(i, hours[i], latest[i]) for i in ids[:limit]]


def hydrate(ranked, entries_by_id, hours_known=True):
    out = []
    for pid, hrs, pick in ranked:
        item = dict(pick)
        item.update(entries_by_id.get(pid, {}))
        item["hours_in_spotlight"] = hrs if hours_known else 0
        out.append(item)
    return out


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def load_entries(data_dir):
    live = (load_json(os.path.join(data_dir, "launches.json"), {}) or {}).get("launches", [])
    arch = (load_json(os.path.join(data_dir, "archive.json"), {}) or {}).get("launches", [])
    by_id = {}
    for e in arch + live:  # live wins
        if e.get("id"):
            by_id[e["id"]] = e
    return by_id


def load_ideas(data_dir):
    return (load_json(os.path.join(data_dir, "ideas.json"), {}) or {}).get("ideas", [])


def score(x):
    v = x.get("jev_score")
    return v if isinstance(v, (int, float)) else -1.0


def period_stats(entries_by_id, ideas, start, end):
    new = [e for e in entries_by_id.values() if in_range(e.get("added_at"), start, end)]
    cats = Counter(e.get("category") for e in new if e.get("category"))
    return {
        "launches_tracked": len(entries_by_id),
        "new_this_period": len(new),
        "ideas": sum(1 for i in ideas if in_range(i.get("added_at"), start, end)),
        "top_category": cats.most_common(1)[0][0] if cats else None,
    }


def build_weekly(week, data_dir=DATA, root=ROOT, cfg=None, now=None):
    cfg = cfg or {}
    start, end = week_bounds(week)
    since, until = period_datetimes(start, end, now)
    entries = load_entries(data_dir)
    ideas = load_ideas(data_dir)

    snaps, source = spotlight_history(since, until, data_dir, root, cfg.get("repo"))
    spots = hydrate(rank_spotlights(snaps, WEEKLY_SPOTLIGHTS), entries,
                    hours_known=source != "current")

    debated = [i for i in ideas if i.get("origin") == "weekly-debate" and i.get("week") == week]
    if debated:
        chosen, ideas_source = sorted(debated, key=score, reverse=True), "weekly-debate"
    else:
        daily = [i for i in ideas if in_range(i.get("added_at"), start, end)]
        chosen = sorted(daily, key=score, reverse=True)[:WEEKLY_IDEAS]
        ideas_source = "daily-fallback"

    return {
        "kind": "weekly",
        "week": week,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "generated_at": (now or now_ist()).isoformat(timespec="seconds"),
        "spotlights": spots,
        "ideas": chosen,
        "stats": period_stats(entries, ideas, start, end),
        "ideas_source": ideas_source,
        "spotlight_source": source,
        "spotlight_snapshots": len(snaps),
    }


def build_monthly(month, data_dir=DATA, root=ROOT, cfg=None, now=None):
    cfg = cfg or {}
    start, end = month_bounds(month)
    since, until = period_datetimes(start, end, now)
    entries = load_entries(data_dir)
    ideas = load_ideas(data_dir)

    snaps, source = spotlight_history(since, until, data_dir, root, cfg.get("repo"))
    spots = hydrate(rank_spotlights(snaps, MONTHLY_SPOTLIGHTS), entries,
                    hours_known=source != "current")

    # Every idea we know about: live ideas.json + weekly snapshots (which
    # survive the 60-idea cap). Dedupe by id; ideas.json wins.
    pool = {}
    ddir = os.path.join(data_dir, "digests")
    if os.path.isdir(ddir):
        for name in sorted(os.listdir(ddir)):
            if name.startswith("weekly-") and name.endswith(".json"):
                snap = load_json(os.path.join(ddir, name), {}) or {}
                for i in snap.get("ideas", []) or []:
                    if i.get("id"):
                        pool[i["id"]] = i
    for i in ideas:
        if i.get("id"):
            pool[i["id"]] = i

    debated = [i for i in pool.values() if i.get("origin") == "weekly-debate"
               and week_of_month(i.get("week"), month)]
    if debated:
        idea, idea_source = max(debated, key=score), "weekly-debate"
    else:
        daily = [i for i in pool.values() if in_range(i.get("added_at"), start, end)]
        idea = max(daily, key=score) if daily else None
        idea_source = "daily-fallback" if idea else "none"

    return {
        "kind": "monthly",
        "month": month,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "generated_at": (now or now_ist()).isoformat(timespec="seconds"),
        "spotlights": spots,
        "idea": idea,
        "stats": period_stats(entries, ideas, start, end),
        "idea_source": idea_source,
        "spotlight_source": source,
        "spotlight_snapshots": len(snaps),
    }


# --------------------------------------------------------------------------
# Rendering helpers
# --------------------------------------------------------------------------

def esc(value):
    """HTML-escape, and neutralise Liquid delimiters so data can never
    become a Kit template tag."""
    s = html.escape(str(value if value is not None else ""), quote=True)
    return s.replace("{", "&#123;").replace("}", "&#125;")


def clip(text, n):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= n:
        return text
    cut = text[:n - 1]
    if " " in cut[n // 2:]:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,;:.-–—") + "…"


def short_title(title, n=40):
    """'VoiceStudio: open-source...' -> 'VoiceStudio'."""
    t = re.split(r"\s*(?::|\s[—–-]\s|\s\()", str(title or ""), maxsplit=1)[0].strip()
    return clip(t or title, n)


def campaign(snapshot):
    if snapshot.get("kind") == "monthly":
        return "monthly-" + snapshot["month"]
    return "weekly-" + snapshot["week"]


def utm(url, camp):
    return "%s?utm_source=newsletter&utm_medium=email&utm_campaign=%s" % (url, camp)


def launch_link(base, entry_id, camp):
    return utm("%s/launch/%s/" % (base, slug_for(entry_id)), camp)


def idea_link(base, idea_id, camp):
    return utm("%s/idea/%s/" % (base, idea_id), camp)


def fmt_stars(n):
    if not isinstance(n, int) or n <= 0:
        return None
    if n >= 1000:
        return ("%.1fk" % (n / 1000)).replace(".0k", "k")
    return str(n)


def num_word(n):
    words = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]
    return words[n] if 0 <= n < len(words) else str(n)


def fmt_range(start, end):
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    if s.month == e.month:
        return "%s %d–%d" % (s.strftime("%b"), s.day, e.day)
    return "%s %d–%s %d" % (s.strftime("%b"), s.day, e.strftime("%b"), e.day)


def use_case(item):
    v = item.get("implementation_idea")
    if v and not PRIVATE_HINTS.search(v):
        return v
    return None


def spot_meta(item):
    parts = []
    if isinstance(item.get("jev_score"), (int, float)):
        parts.append("JEV traction %.2f" % item["jev_score"])
    st = fmt_stars(item.get("github_stars"))
    if st:
        parts.append("%s stars" % st)
    if item.get("hours_in_spotlight"):
        parts.append("%dh in Spotlight" % item["hours_in_spotlight"])
    if item.get("category"):
        parts.append(item["category"])
    return parts


def idea_steps(idea, n=None):
    plan = idea.get("implementation_plan") or []
    steps = []
    for p in plan[:n] if n else plan:
        if isinstance(p, dict):
            steps.append((p.get("step") or "", p.get("detail") or "", p.get("effort") or ""))
        elif p:
            steps.append((str(p), "", ""))
    return steps


def idea_money(idea):
    m = idea.get("monetization") or {}
    bits = [b for b in (m.get("pricing"), m.get("wedge")) if b]
    if bits:
        return ". ".join(b.rstrip(". ") for b in bits) + "."
    return idea.get("distribution") or None


def idea_who(idea):
    out = []
    for b in idea.get("beneficiaries") or []:
        if isinstance(b, dict) and b.get("who"):
            out.append((b["who"], b.get("benefit") or ""))
    return out


def built_with(idea):
    return [b.get("name") or b.get("entry_id") for b in idea.get("build_with") or []
            if isinstance(b, dict) and (b.get("name") or b.get("entry_id"))]


# --- HTML atoms (all styles inline) ---------------------------------------

def p(text_html, size=15, color=TEXT, cls="fw-text", margin="0 0 12px 0", extra=""):
    return ('<p class="%s" style="margin:%s;font-family:%s;font-size:%dpx;line-height:%dpx;color:%s;%s">%s</p>'
            % (cls, margin, FONT, size, round(size * 1.55), color, extra, text_html))


def label(text, color=MUTED, cls="fw-muted"):
    return ('<p class="%s" style="margin:0 0 6px 0;font-family:%s;font-size:11px;line-height:16px;'
            'font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:%s;">%s</p>'
            % (cls, FONT, color, esc(text)))


def link(href, text_html, color=INK, cls="fw-ink", underline=False):
    return ('<a href="%s" class="%s" style="color:%s;text-decoration:%s;">%s</a>'
            % (esc(href), cls, color, "underline" if underline else "none", text_html))


def chip(text):
    return ('<span class="fw-chip" style="display:inline-block;margin:0 6px 6px 0;padding:3px 9px;'
            'border:1px solid %s;border-radius:999px;background:%s;font-family:%s;font-size:12px;'
            'line-height:18px;color:%s;white-space:nowrap;">%s</span>' % (RULE, SOFT, FONT, TEXT, esc(text)))


def button(href, text, fill=MINT, color=INK):
    return ('<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:4px 0 0 0;">'
            '<tr><td align="center" bgcolor="%s" style="border-radius:8px;background:%s;">'
            '<a href="%s" style="display:inline-block;padding:12px 22px;font-family:%s;font-size:15px;'
            'line-height:20px;font-weight:700;color:%s;text-decoration:none;border-radius:8px;">%s</a>'
            '</td></tr></table>' % (fill, fill, esc(href), FONT, color, esc(text)))


def row(inner, pad="0 32px", cls="fw-pad"):
    return '<tr><td class="%s" style="padding:%s;">%s</td></tr>' % (cls, pad, inner)


def rule(margin="24px 32px"):
    return ('<tr><td class="fw-pad" style="padding:%s;"><table role="presentation" width="100%%" '
            'cellpadding="0" cellspacing="0" border="0"><tr><td class="fw-rule" style="border-top:1px solid %s;'
            'font-size:0;line-height:0;height:1px;">&nbsp;</td></tr></table></td></tr>' % (margin, RULE))


def card(rows_html, margin_bottom=16):
    return ('<table role="presentation" class="fw-card" width="100%%" cellpadding="0" cellspacing="0" border="0" '
            'bgcolor="%s" style="width:100%%;background:%s;border:1px solid %s;border-radius:12px;'
            'margin:0 0 %dpx 0;border-collapse:separate;">%s</table>' % (CARD, CARD, RULE, margin_bottom, rows_html))


def masthead(kicker, headline, intro_html):
    brand = ('<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
             '<td width="10" height="10" bgcolor="%s" style="width:10px;height:10px;background:%s;border-radius:2px;'
             'font-size:0;line-height:0;">&nbsp;</td>'
             '<td class="fw-ink" style="padding-left:8px;font-family:%s;font-size:15px;line-height:20px;font-weight:800;'
             'letter-spacing:-0.2px;color:%s;">Fresh Weights</td></tr></table>' % (MINT, MINT, FONT, INK))
    return (row(brand, "28px 32px 0 32px")
            + row(label(kicker, MINT_TEXT, "fw-accent"), "18px 32px 0 32px")
            + row('<h1 class="fw-h1 fw-ink" style="margin:0 0 12px 0;font-family:%s;font-size:28px;line-height:34px;'
                  'font-weight:800;letter-spacing:-0.4px;color:%s;">%s</h1>' % (FONT, INK, esc(headline)), "0 32px")
            + row(intro_html, "0 32px 8px 32px"))


def section_head(title, sub=None, color=INK, cls="fw-ink"):
    h = ('<h2 class="fw-h2 %s" style="margin:0 0 4px 0;font-family:%s;font-size:20px;line-height:26px;'
         'font-weight:800;letter-spacing:-0.2px;color:%s;">%s</h2>' % (cls, FONT, color, esc(title)))
    if sub:
        h += p(esc(sub), 13, MUTED, "fw-muted", "0 0 4px 0")
    return row(h, "0 32px 4px 32px")


def spotlight_block(item, base, camp, compact=False, num=None):
    href = launch_link(base, item.get("id", ""), camp)
    title = esc(item.get("title") or item.get("id"))
    why = item.get("usp") or item.get("summary") or ""
    meta = " &nbsp;·&nbsp; ".join(esc(m) for m in spot_meta(item))
    if compact:
        lead = ('<span class="fw-accent" style="color:%s;font-weight:800;">%d.</span> ' % (MINT_TEXT, num)) if num else ""
        inner = (p(lead + link(href, '<strong>%s</strong>' % title), 15, INK, "fw-ink", "0 0 4px 0")
                 + p(esc(clip(why, 160)), 14, TEXT, "fw-text", "0 0 4px 0")
                 + (p(meta, 12, MUTED, "fw-muted", "0") if meta else ""))
        return row(inner, "10px 32px 10px 32px")
    inner = ('<h3 class="fw-ink" style="margin:0 0 6px 0;font-family:%s;font-size:17px;line-height:23px;'
             'font-weight:700;color:%s;">%s</h3>' % (FONT, INK, link(href, title)))
    inner += p(esc(clip(why, 240)), 15, TEXT, "fw-text", "0 0 8px 0")
    if meta:
        inner += p(meta, 12, MUTED, "fw-muted", "0 0 8px 0")
    uc = use_case(item)
    if uc:
        inner += p('<strong class="fw-ink" style="color:%s;">Use it for:</strong> %s' % (INK, esc(clip(uc, 220))),
                   14, TEXT, "fw-text", "0 0 8px 0")
    elif item.get("usability"):
        inner += p('<strong class="fw-ink" style="color:%s;">Reality check:</strong> %s'
                   % (INK, esc(clip(item["usability"], 220))), 14, TEXT, "fw-text", "0 0 8px 0")
    inner += p(link(href, "Open on the radar &rarr;", MINT_TEXT, "fw-accent"), 14, MINT_TEXT, "fw-accent",
               "0", "font-weight:700;")
    return row(inner, "16px 32px 4px 32px")


def idea_block(idea, base, camp, n):
    href = idea_link(base, idea.get("id", ""), camp)
    bar_open = ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr>'
                '<td class="fw-amber-bar" style="border-left:3px solid %s;padding:2px 0 2px 16px;">' % AMBER)
    inner = label("Idea %d" % n + (" · %s" % idea["difficulty"] if idea.get("difficulty") else ""),
                  AMBER, "fw-amber")
    inner += ('<h3 class="fw-ink" style="margin:0 0 6px 0;font-family:%s;font-size:17px;line-height:23px;'
              'font-weight:700;color:%s;">%s</h3>' % (FONT, INK, link(href, esc(idea.get("title")))))
    lede = idea.get("outcome") or clip(idea.get("idea") or idea.get("problem"), 240)
    if lede:
        inner += p(esc(lede), 15, TEXT, "fw-text", "0 0 10px 0")
    who = idea_who(idea)
    if who:
        inner += p('<strong class="fw-ink" style="color:%s;">Who benefits:</strong> %s'
                   % (INK, esc("; ".join("%s (%s)" % (w, clip(b, 90)) if b else w for w, b in who[:3]))),
                   14, TEXT, "fw-text", "0 0 8px 0")
    money = idea_money(idea)
    if money:
        inner += p('<strong class="fw-ink" style="color:%s;">How it makes money:</strong> %s'
                   % (INK, esc(clip(money, 260))), 14, TEXT, "fw-text", "0 0 8px 0")
    steps = idea_steps(idea, 3)
    if steps:
        lis = "".join('<li style="margin:0 0 4px 0;">%s%s</li>'
                      % (esc(s), (' <span class="fw-muted" style="color:%s;">(%s)</span>' % (MUTED, esc(e))) if e else "")
                      for s, _, e in steps)
        inner += p('<strong class="fw-ink" style="color:%s;">First steps:</strong>' % INK, 14, TEXT, "fw-text", "0 0 4px 0")
        inner += ('<ol class="fw-text" style="margin:0 0 8px 0;padding:0 0 0 20px;font-family:%s;font-size:14px;'
                  'line-height:21px;color:%s;">%s</ol>' % (FONT, TEXT, lis))
    elif idea.get("mvp"):
        inner += p('<strong class="fw-ink" style="color:%s;">Weekend MVP:</strong> %s'
                   % (INK, esc(clip(idea["mvp"], 260))), 14, TEXT, "fw-text", "0 0 8px 0")
    bw = built_with(idea)
    if bw:
        inner += p("Built with", 12, MUTED, "fw-muted", "2px 0 4px 0") + \
            '<div style="margin:0 0 4px 0;">%s</div>' % "".join(chip(b) for b in bw[:5])
    foot = []
    if isinstance(idea.get("jev_score"), (int, float)):
        foot.append("JEV confidence %.2f" % idea["jev_score"])
    foot_html = esc(" · ".join(foot))
    inner += p((foot_html + " &nbsp;·&nbsp; " if foot_html else "")
               + link(href, "Full idea &rarr;", AMBER, "fw-amber"), 13, MUTED, "fw-muted", "2px 0 0 0",
               "font-weight:600;")
    return row(bar_open + inner + "</td></tr></table>", "16px 32px 8px 32px")


def featured_idea(idea, base, camp):
    href = idea_link(base, idea.get("id", ""), camp)
    out = row(label("Idea of the month", AMBER, "fw-amber")
              + '<h2 class="fw-h2 fw-ink" style="margin:0 0 8px 0;font-family:%s;font-size:24px;line-height:30px;'
                'font-weight:800;letter-spacing:-0.3px;color:%s;">%s</h2>' % (FONT, INK, link(href, esc(idea.get("title"))))
              + (p(esc(idea["outcome"]), 16, TEXT, "fw-text", "0 0 4px 0") if idea.get("outcome") else ""),
              "24px 32px 8px 32px")

    def part(title, body_html):
        return row(p('<strong class="fw-ink" style="color:%s;">%s</strong>' % (INK, esc(title)), 14, INK,
                     "fw-ink", "0 0 4px 0") + body_html, "10px 32px 4px 32px")

    for key, title in (("problem", "The problem"), ("idea", "The idea")):
        if idea.get(key):
            out += part(title, p(esc(idea[key]), 15))
    steps = idea_steps(idea)
    if steps:
        lis = "".join('<li style="margin:0 0 6px 0;"><strong>%s</strong>%s%s</li>'
                      % (esc(s), (" — " + esc(d)) if d else "",
                         (' <span class="fw-muted" style="color:%s;">(%s)</span>' % (MUTED, esc(e))) if e else "")
                      for s, d, e in steps)
        out += part("The plan", '<ol class="fw-text" style="margin:0 0 8px 0;padding:0 0 0 20px;font-family:%s;'
                                'font-size:15px;line-height:23px;color:%s;">%s</ol>' % (FONT, TEXT, lis))
    elif idea.get("mvp"):
        out += part("Weekend MVP", p(esc(idea["mvp"]), 15))
    who = idea_who(idea)
    if who:
        lis = "".join('<li style="margin:0 0 4px 0;"><strong>%s</strong>%s</li>'
                      % (esc(w), (" — " + esc(b)) if b else "") for w, b in who)
        out += part("Who benefits", '<ul class="fw-text" style="margin:0 0 8px 0;padding:0 0 0 20px;font-family:%s;'
                                    'font-size:15px;line-height:23px;color:%s;">%s</ul>' % (FONT, TEXT, lis))
    m = idea.get("monetization") or {}
    if any(m.get(k) for k in ("model", "pricing", "wedge", "channels")):
        bits = []
        for k, t in (("model", "Model"), ("pricing", "Pricing"), ("wedge", "First customers")):
            if m.get(k):
                bits.append('<strong>%s:</strong> %s' % (t, esc(m[k])))
        if m.get("channels"):
            bits.append('<strong>Channels:</strong> %s' % esc(", ".join(m["channels"])))
        out += part("How it makes money", p("<br>".join(bits), 15))
    elif idea.get("distribution"):
        out += part("How it makes money", p(esc(idea["distribution"]), 15))
    if idea.get("risks"):
        out += part("Risks", p(esc(idea["risks"]), 15))
    dissent = (idea.get("debate") or {}).get("dissent")
    if dissent:
        out += part("Strongest objection", p(esc(dissent), 15, TEXT, "fw-text", "0 0 12px 0", "font-style:italic;"))
    bw = built_with(idea)
    chips = ('<div style="margin:0 0 4px 0;">%s</div>' % "".join(chip(b) for b in bw[:6])) if bw else ""
    conf = ("JEV confidence %.2f" % idea["jev_score"]) if isinstance(idea.get("jev_score"), (int, float)) else ""
    out += row((p("Built with", 12, MUTED, "fw-muted", "4px 0 4px 0") + chips if chips else "")
               + (p(esc(conf), 13, MUTED, "fw-muted", "4px 0 12px 0") if conf else "")
               + button(href, "Read the full idea", AMBER, INK), "6px 32px 28px 32px")
    return out


def footer_cta(base, camp):
    inner = (p('<strong>See every launch, live.</strong>', 17, INK, "fw-ink", "0 0 4px 0")
             + p("The radar updates four times a day, ranked by real traction instead of hype.", 14, TEXT,
                 "fw-text", "0 0 14px 0")
             + button(utm(base + "/", camp), "View on the radar")
             + p("Know someone who ships with AI? Forward this to them. They can subscribe at "
                 + link(utm(base + "/", camp), esc(re.sub(r"^https?://", "", base)), MINT_TEXT, "fw-accent", True)
                 + ".", 13, MUTED, "fw-muted", "16px 0 0 0"))
    return card(row(inner, "24px 32px 26px 32px"), 0)


# --------------------------------------------------------------------------
# render()
# --------------------------------------------------------------------------

def _subject_fit(prefix, core, suffix, limit=70):
    room = limit - len(prefix) - len(suffix)
    return prefix + clip(core, max(room, 8)) + suffix


def _web_shell(content_html, preheader, base, title):
    try:
        with open(TEMPLATE_PATH, encoding="utf-8") as f:
            tpl = f.read()
    except OSError:
        tpl = "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>Fresh Weights</title></head><body>{{ message_content }}</body></html>"
    hidden = ('<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">%s%s</div>'
              % (esc(preheader), "&#847;&zwnj;&nbsp;" * 30))
    out = tpl.replace("{{ unsubscribe_url }}", esc(base + "/"))
    out = re.sub(r"\s*\{\{\s*address\s*\}\}\s*", "", out)
    out = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", lambda m: "{{ message_content }}" if "message_content" in m.group(0) else "", out)
    out = out.replace("<title>Fresh Weights</title>", "<title>%s</title>" % esc(title), 1)
    out = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + hidden, out, count=1)
    return out.replace("{{ message_content }}", content_html)


def render(snapshot, cfg):
    """Pure renderer. Returns {subject, preheader, content_html, full_html, text}."""
    base = (cfg.get("base_url") or "https://freshweights.com").rstrip("/")
    camp = campaign(snapshot)
    stats = snapshot.get("stats") or {}
    spots = snapshot.get("spotlights") or []
    new_n = stats.get("new_this_period") or 0
    topcat = stats.get("top_category")
    blocks, text = [], []

    if snapshot.get("kind") == "monthly":
        first = date.fromisoformat(snapshot["start"])
        mname = first.strftime("%B")
        idea = snapshot.get("idea")
        if idea:
            full = "%s's best idea: %s" % (mname, re.sub(r"\s+", " ", idea.get("title") or "").strip())
            subject = full if len(full) <= 70 else _subject_fit("%s's best idea: " % mname,
                                                                short_title(idea.get("title"), 60), "")
            tail = " Plus %s's top %d launches." % (mname, len(spots)) if spots else ""
            lead = clip(idea.get("outcome") or idea.get("title"), 110 - len(tail) - 1).rstrip(".")
            pre = lead + "." + tail
        else:
            subject = _subject_fit("%s on the radar: " % mname,
                                   short_title(spots[0]["title"]) if spots else "the month's launches", "")
            pre = clip("The %d launches that held the Spotlight longest in %s." % (len(spots), mname), 110)
        intro = ("We tracked %d new launches in %s%s. " % (new_n, mname,
                 (", most of them in %s" % topcat) if topcat else ""))
        intro += ("Below: the one idea we would build first, then the %d launches that held the Spotlight longest."
                  % len(spots) if idea else "Below: the %d launches that held the Spotlight longest." % len(spots))
        head = masthead("Monthly · %s %d" % (mname, first.year),
                        "%s: the idea worth building" % mname if idea else "%s on the radar" % mname,
                        p(esc(intro), 16, TEXT, "fw-text", "0 0 16px 0"))
        blocks.append(card(head + (featured_idea(idea, base, camp) if idea else row("", "0 0 12px 0"))))
        if spots:
            rows_html = section_head("The month's Spotlights", "Ranked by hours held in a Spotlight slot.")
            rows_html = row("", "24px 0 0 0", "") + rows_html
            rows_html += "".join(spotlight_block(s, base, camp, compact=True, num=i + 1) for i, s in enumerate(spots))
            rows_html += row("", "0 0 16px 0", "")
            blocks.append(card(rows_html))
        title = "Fresh Weights Monthly · %s %d" % (mname, first.year)
        text += [title, "", intro, ""]
        if idea:
            text += ["IDEA OF THE MONTH: " + (idea.get("title") or ""), idea.get("outcome") or ""]
            for k, t in (("problem", "Problem"), ("idea", "Idea")):
                if idea.get(k):
                    text += ["", t + ": " + idea[k]]
            steps = idea_steps(idea)
            if steps:
                text += ["", "Plan:"] + ["%d. %s%s%s" % (i + 1, s, (" — " + d) if d else "", (" (%s)" % e) if e else "")
                                         for i, (s, d, e) in enumerate(steps)]
            elif idea.get("mvp"):
                text += ["", "Weekend MVP: " + idea["mvp"]]
            who = idea_who(idea)
            if who:
                text += ["", "Who benefits:"] + ["- %s%s" % (w, (" — " + b) if b else "") for w, b in who]
            money = idea_money(idea)
            if money:
                text += ["", "How it makes money: " + money]
            if idea.get("risks"):
                text += ["", "Risks: " + idea["risks"]]
            if (idea.get("debate") or {}).get("dissent"):
                text += ["", "Strongest objection: " + idea["debate"]["dissent"]]
            text += ["", "Full idea: " + idea_link(base, idea.get("id", ""), camp), ""]
        if spots:
            text += ["THE MONTH'S SPOTLIGHTS", ""]
            for i, s in enumerate(spots):
                text += ["%d. %s" % (i + 1, s.get("title")), "   " + clip(s.get("usp") or s.get("summary"), 160),
                         "   " + " · ".join(spot_meta(s)), "   " + launch_link(base, s.get("id", ""), camp), ""]
    else:
        wk = int(snapshot["week"].split("-W")[1])
        ideas = snapshot.get("ideas") or []
        top = short_title(spots[0]["title"]) if spots else None
        if ideas:
            suffix = " + %d idea%s you can build — week %d" % (len(ideas), "" if len(ideas) == 1 else "s", wk)
        else:
            suffix = " and %d more launches — week %d" % (max(len(spots) - 1, 0), wk)
        subject = _subject_fit("", top, suffix) if top else "This week on the radar — week %d" % wk
        if len(subject) > 70:
            subject = clip(subject, 70)
        pre_bits = []
        if spots:
            pre_bits.append("%d launches held the Spotlight" % len(spots))
        if topcat:
            pre_bits.append("%s led with the most new entries" % topcat)
        pre = "; ".join(pre_bits) + "."
        if ideas:
            pre += " Plus %d build-ready idea%s." % (len(ideas), "" if len(ideas) == 1 else "s")
        pre = clip(pre, 110)
        rng = fmt_range(snapshot["start"], snapshot["end"])
        s1 = "We tracked %d new launches this week%s." % (new_n, (", most of them in %s" % topcat) if topcat else "")
        if spots:
            lead = spots[0]
            hrs = lead.get("hours_in_spotlight")
            s2 = ("%s launches held the Spotlight; %s led%s." % (num_word(len(spots)), short_title(lead.get("title")),
                  (" with %d hours" % hrs) if hrs else ""))
        else:
            s2 = "Nothing held the Spotlight long enough to rank."
        intro = s1 + " " + s2
        head = masthead("Weekly · Week %d · %s" % (wk, rng), "What held the radar in week %d" % wk,
                        p(esc(intro), 16, TEXT, "fw-text", "0 0 16px 0"))
        rows_html = head
        if spots:
            rows_html += rule("16px 32px 20px 32px")
            sub = ("Ranked by hours held in a Spotlight slot." if snapshot.get("spotlight_source") != "current"
                   else "The current Spotlight picks.")
            rows_html += section_head("This week's Spotlight", sub)
            rows_html += "".join(spotlight_block(s, base, camp) for s in spots)
        rows_html += row("", "0 0 20px 0", "")
        blocks.append(card(rows_html))
        if ideas:
            n = len(ideas)
            isub = ("Debated by three models, judged by JEV." if snapshot.get("ideas_source") == "weekly-debate"
                    else "The week's highest-scoring ideas from the daily run.")
            irows = row("", "24px 0 0 0", "") + section_head(
                "%s idea%s worth building" % ({1: "One", 2: "Two", 3: "3"}.get(n, str(n)), "" if n == 1 else "s"), isub)
            irows += "".join(idea_block(i, base, camp, k + 1) for k, i in enumerate(ideas))
            irows += row("", "0 0 20px 0", "")
            blocks.append(card(irows))
        title = "Fresh Weights Weekly · Week %d · %s" % (wk, rng)
        text += [title, "", intro, ""]
        if spots:
            text += ["THIS WEEK'S SPOTLIGHT", ""]
            for s in spots:
                text += [s.get("title") or "", clip(s.get("usp") or s.get("summary"), 240), " · ".join(spot_meta(s))]
                uc = use_case(s)
                if uc:
                    text.append("Use it for: " + clip(uc, 220))
                text += [launch_link(base, s.get("id", ""), camp), ""]
        if ideas:
            text += ["IDEAS WORTH BUILDING", ""]
            for k, i in enumerate(ideas):
                text += ["%d. %s" % (k + 1, i.get("title")), i.get("outcome") or clip(i.get("idea") or i.get("problem"), 240)]
                who = idea_who(i)
                if who:
                    text.append("Who benefits: " + "; ".join(w for w, _ in who[:3]))
                money = idea_money(i)
                if money:
                    text.append("How it makes money: " + clip(money, 260))
                steps = idea_steps(i, 3)
                if steps:
                    text += ["First steps:"] + ["  %d) %s" % (j + 1, s) for j, (s, _, _) in enumerate(steps)]
                elif i.get("mvp"):
                    text.append("Weekend MVP: " + clip(i["mvp"], 260))
                if built_with(i):
                    text.append("Built with: " + ", ".join(built_with(i)[:5]))
                if isinstance(i.get("jev_score"), (int, float)):
                    text.append("JEV confidence %.2f" % i["jev_score"])
                text += [idea_link(base, i.get("id", ""), camp), ""]

    blocks.append(footer_cta(base, camp))
    content_html = "\n".join(blocks)
    text += ["See every launch, live: " + utm(base + "/", camp),
             "Know someone who ships with AI? Forward this email.", ""]
    return {
        "subject": subject,
        "preheader": pre,
        "content_html": content_html,
        "full_html": _web_shell(content_html, pre, base, subject),
        "text": "\n".join(text),
    }


# --------------------------------------------------------------------------
# Kit
# --------------------------------------------------------------------------

def kit_create_draft(rendered, snapshot, cfg, opener=None):
    """Create a DRAFT broadcast (send_at null). Returns the broadcast id.
    The API key is read here and never leaves the request header."""
    key = os.environ.get("KIT_API_KEY")
    if not key:
        raise RuntimeError("KIT_API_KEY not set (inject it from the vault)")
    kit = cfg.get("kit") or {}
    name = kit.get("newsletter_monthly" if snapshot.get("kind") == "monthly" else "newsletter_weekly") \
        or "Fresh Weights"
    body = {
        "subject": rendered["subject"],
        "preview_text": rendered["preheader"],
        "content": rendered["content_html"],
        "description": "%s %s (draft from digest.py)" % (name, campaign(snapshot).split("-", 1)[1]),
        "public": True,
        "published_at": None,
        "send_at": None,
    }
    tid = os.environ.get("KIT_TEMPLATE_ID")
    if tid:
        body["email_template_id"] = int(tid) if tid.isdigit() else tid
    req = urllib.request.Request(KIT_BROADCASTS_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"X-Kit-Api-Key": key, "Content-Type": "application/json",
                                          "Accept": "application/json", "User-Agent": UA})
    opener = opener or urllib.request.urlopen
    try:
        with opener(req, timeout=60) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300].replace(key, "***")
        raise RuntimeError("Kit API HTTP %s: %s" % (e.code, detail)) from None
    bc = payload.get("broadcast") or payload
    return bc.get("id")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def write_outputs(name, rendered, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "full": os.path.join(out_dir, name + ".html"),
        "content": os.path.join(out_dir, name + ".content.html"),
        "text": os.path.join(out_dir, name + ".txt"),
    }
    for k, src in (("full", "full_html"), ("content", "content_html"), ("text", "text")):
        with open(paths[k], "w", encoding="utf-8") as f:
            f.write(rendered[src])
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build Fresh Weights weekly/monthly newsletter digests.")
    ap.add_argument("kind", choices=["weekly", "monthly"])
    ap.add_argument("--week", help="ISO week, e.g. 2026-W39 (default: last completed week, IST)")
    ap.add_argument("--month", help="Month, e.g. 2026-09 (default: last completed month, IST)")
    ap.add_argument("--kit-draft", action="store_true", help="Create a DRAFT broadcast in Kit (never sends)")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "newsletter", "out"))
    ap.add_argument("--data-dir", default=DATA)
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.kind == "weekly":
        period = args.week or last_completed_week()
        week_bounds(period)
        snap = build_weekly(period, args.data_dir, ROOT, cfg)
        name = "weekly-" + period
    else:
        period = args.month or last_completed_month()
        month_bounds(period)
        snap = build_monthly(period, args.data_dir, ROOT, cfg)
        name = "monthly-" + period

    snap_path = os.path.join(args.data_dir, "digests", name + ".json")
    save_json(snap_path, snap)
    print("snapshot: %s (%d spotlights, source %s)" % (snap_path, len(snap["spotlights"]), snap["spotlight_source"]))

    rendered = render(snap, cfg)
    paths = write_outputs(name, rendered, args.out_dir)
    print("subject: %s" % rendered["subject"])
    print("preheader: %s" % rendered["preheader"])
    print("preview: %s (%.1f KB)" % (paths["full"], len(rendered["full_html"].encode()) / 1024))
    if len(rendered["full_html"].encode()) > 90 * 1024:
        print("WARNING: email over 90 KB; Gmail clips at 102 KB")

    if args.kit_draft:
        bid = kit_create_draft(rendered, snap, cfg)
        print("Kit draft broadcast created: id %s (review and send it in Kit)" % bid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
