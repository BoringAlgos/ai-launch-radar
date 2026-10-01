"""Illustrated cards for Spotlight launches and ideas (website + newsletter).

Design: a "fresh produce scale ticket". Each card is cool paper with one
produce colour; the memorable element is a tilted, perforated scale ticket
printing one friendly number ("646 languages", "71.9k developers starred it").
A large line-drawn motif sits above the ticket; the plain-English gist is the
headline on the left. Idea cards are a three-step strip: problem -> what you
build -> who pays.

How a card looks is decided by JEV, not hard-coded: for every card we build a
few candidate motifs, numbers and colours from the entry's own data, and one
JEV call scores which combination a non-technical reader grasps fastest
(choose_visuals). Without JEV (budget guard, failure) the first candidate of
each list is used, which is still a sensible card.

A card is fully described by its `spec` dict (stored in spotlight.json /
ideas.json / digest snapshots). build_site.py turns specs into HTML with
card_html() and scripts/render_graphics.mjs screenshots them to PNG at the
path from image_path(spec). Same spec -> same file name, so the website and
the newsletter always show the identical image.

Stdlib only.
"""

import hashlib
import html
import json
import re

W, H = 1200, 630
SIZES = {"spotlight": (1200, 630), "idea": (1200, 860)}

PALETTES = {
    "leaf":   {"accent": "#27b877", "tint": "#e3f5eb", "deep": "#0e6b44", "mood": "fresh, growth, open-source, tools"},
    "sky":    {"accent": "#3d93e8", "tint": "#e2eefb", "deep": "#17558f", "mood": "infrastructure, reliability, scale, models"},
    "citrus": {"accent": "#efa52a", "tint": "#fbf0dc", "deep": "#8a5a09", "mood": "money, opportunity, ideas, speed"},
    "berry":  {"accent": "#df4f86", "tint": "#fbe4ee", "deep": "#8e1f4b", "mood": "media, creativity, voice, consumer"},
}
CATEGORY_PALETTE = {"models": "sky", "agents": "leaf", "coding": "leaf", "infra": "sky",
                    "media": "berry", "robotics": "citrus", "security": "sky"}

# Line-drawn motifs on a 120x120 grid, stroked in the card's colour.
MOTIFS = {
    "switchboard": ("router gateway provider fallback proxy route api endpoint",
                    '<circle cx="22" cy="30" r="9"/><circle cx="22" cy="60" r="9"/><circle cx="22" cy="90" r="9"/>'
                    '<rect x="70" y="44" width="34" height="32" rx="8"/><path d="M31 30 C50 30 52 52 70 54M31 60 H70M31 90 C50 90 52 68 70 66"/>'),
    "brain":       ("model llm frontier reasoning weights parameters intelligence benchmark",
                    '<path d="M60 22 C40 14 22 28 26 46 C14 52 16 72 28 76 C26 92 44 102 60 94 C76 102 94 92 92 76 '
                    'C104 72 106 52 94 46 C98 28 80 14 60 22 Z"/><path d="M60 22 V94M40 44 C48 48 52 54 60 54M80 44 '
                    'C72 48 68 54 60 54M38 72 C46 70 52 74 60 74M82 72 C74 70 68 74 60 74"/>'),
    "robot":       ("agent agents assistant autonomous harness orchestration workflow bot",
                    '<rect x="26" y="40" width="68" height="52" rx="14"/><circle cx="46" cy="64" r="6"/><circle cx="74" cy="64" r="6"/>'
                    '<path d="M50 80 H70M60 40 V26"/><circle cx="60" cy="22" r="5"/><path d="M26 64 H16M94 64 H104"/>'),
    "brackets":    ("code coding ide developer codex repo pull github compiler cli terminal",
                    '<path d="M42 30 L18 60 L42 90M78 30 L102 60 L78 90M68 24 L52 96"/>'),
    "stack":       ("infra server inference gpu cluster deploy hosting kernel serving database memory",
                    '<rect x="22" y="22" width="76" height="22" rx="6"/><rect x="22" y="49" width="76" height="22" rx="6"/>'
                    '<rect x="22" y="76" width="76" height="22" rx="6"/><circle cx="36" cy="33" r="3"/><circle cx="36" cy="60" r="3"/>'
                    '<circle cx="36" cy="87" r="3"/><path d="M54 33 H84M54 60 H84M54 87 H84"/>'),
    "frame":       ("image picture photo video 3d render design visual art",
                    '<rect x="18" y="24" width="84" height="72" rx="10"/><circle cx="44" cy="48" r="9"/>'
                    '<path d="M18 84 L48 62 L66 76 L82 64 L102 80"/>'),
    "wave":        ("voice audio speech dubbing music sound tts transcription podcast",
                    '<path d="M14 60 H24M30 44 V76M42 30 V90M54 46 V74M66 20 V100M78 38 V82M90 50 V70M100 60 H106"/>'),
    "shield":      ("security safety compliance identity auth audit policy risk vulnerability",
                    '<path d="M60 16 L96 30 V58 C96 80 80 96 60 104 C40 96 24 80 24 58 V30 Z"/><path d="M44 60 L56 72 L78 48"/>'),
    "arm":         ("robot robotics ros embodied manipulation hardware physical",
                    '<path d="M24 100 H64M44 100 V84"/><circle cx="44" cy="78" r="7"/><path d="M48 72 L72 44"/><circle cx="74" cy="40" r="7"/>'
                    '<path d="M80 36 L98 26M80 44 L98 52"/>'),
    "globe":       ("languages multilingual translation global world countries",
                    '<circle cx="60" cy="60" r="40"/><path d="M20 60 H100M60 20 C44 38 44 82 60 100M60 20 C76 38 76 82 60 100"/>'),
    "bolt":        ("fast speed latency realtime faster flash quick throughput",
                    '<path d="M68 14 L30 66 H58 L50 106 L90 50 H62 Z"/>'),
    "coins":       ("cost cheap price pricing cents dollar budget savings token",
                    '<ellipse cx="60" cy="34" rx="34" ry="12"/><path d="M26 34 V54 C26 61 41 66 60 66 C79 66 94 61 94 54 V34"/>'
                    '<path d="M26 54 V74 C26 81 41 86 60 86 C79 86 94 81 94 74 V54"/>'),
    "lens":        ("search research observability trace eval evaluation monitor analytics",
                    '<circle cx="52" cy="52" r="30"/><path d="M74 74 L102 102"/><path d="M38 56 L48 46 L58 56 L68 42"/>'),
    "puzzle":      ("plugin skills integration extension protocol mcp connect sdk",
                    '<path d="M24 34 H48 C48 22 66 22 66 34 H90 V58 C102 58 102 76 90 76 V96 H66 C66 84 48 84 48 96 H24 V76 '
                    'C36 76 36 58 24 58 Z"/>'),
}
CATEGORY_PLAIN = {"models": "AI model", "agents": "AI agent", "coding": "Coding tool",
                  "infra": "Developer infrastructure", "media": "Media tool", "robotics": "Robotics",
                  "security": "Security tool"}
CATEGORY_MOTIF = {"models": "brain", "agents": "robot", "coding": "brackets", "infra": "stack",
                  "media": "frame", "robotics": "arm", "security": "shield"}

IDEA_STEP_MOTIFS = ("lens", "brackets", "coins")

e = html.escape


# ------------------------------------------------------------------ candidates

def _words(*texts):
    return set(re.findall(r"[a-z0-9]+", " ".join(t or "" for t in texts).lower()))


def motif_candidates(entry, n=3):
    words = _words(entry.get("title"), entry.get("summary"), entry.get("usp"), " ".join(entry.get("tags") or []),
                   entry.get("subcategory"))
    scored = []
    for name, (keys, _) in MOTIFS.items():
        hits = len(words & set(keys.split()))
        if name == CATEGORY_MOTIF.get(entry.get("category")):
            hits += 1.5
        if hits:
            scored.append((hits, name))
    out = [name for _, name in sorted(scored, key=lambda x: -x[0])]
    default = CATEGORY_MOTIF.get(entry.get("category"), "puzzle")
    if default not in out:
        out.append(default)
    return out[:n]


def _fmt_k(n):
    if n >= 1000:
        v = n / 1000.0
        return ("%.1fk" % v).replace(".0k", "k")
    return str(n)


STAT_RE = re.compile(r"(?<![\w.])((?:\$|₹)?\d[\d,.]*\s?(?:%|x|k|m|b|M|B|K)?\+?)\s+([a-zA-Z][a-zA-Z-]+(?:\s[a-zA-Z-]+)?)")
STAT_NOUNS = {"languages", "providers", "models", "stars", "users", "developers", "tokens", "countries", "agents",
              "tools", "seconds", "minutes", "hours", "days", "commits", "releases", "apps", "teams", "repos",
              "million", "faster", "cheaper", "lower", "parameters", "concurrent", "integrations", "times"}


STAT_LABELS = {"stars": "GitHub stars", "providers": "AI providers", "models": "AI models",
               "faster": "faster", "cheaper": "cheaper", "times": "times"}


def stat_candidates(entry, n=3):
    """Friendly numbers pulled from the entry itself. Never invented."""
    out = []
    for text in (entry.get("usp"), entry.get("summary")):
        for m in STAT_RE.finditer(text or ""):
            value = m.group(1).strip()
            first = m.group(2).split()[0].lower()
            if first in STAT_NOUNS and len(value) <= 8 and all(c["value"] != value for c in out):
                out.append({"value": value, "label": STAT_LABELS.get(first, first)})
    stars = entry.get("github_stars") or 0
    if stars >= 500:
        out.append({"value": _fmt_k(stars), "label": "developers starred it on GitHub"})
    if entry.get("jev_score") is not None:
        out.append({"value": "%d/100" % round(entry["jev_score"] * 100), "label": "traction score"})
    if not out:
        out.append({"value": "New", "label": "this week on the radar"})
    return out[:n]


def palette_candidates(entry):
    first = CATEGORY_PALETTE.get(entry.get("category"), "leaf")
    return [first] + [p for p in PALETTES if p != first]


# ------------------------------------------------------------------ JEV choice

def choose_visuals(items, jev_call=None, caller="radar-visuals"):
    """items: list of dicts {key, kind, gist, entry, motifs, stats, palettes}.
    One JEV call scores every candidate; returns {key: {motif, stat, palette,
    scored_by}}. Falls back to the first candidates when jev_call is None or
    fails."""
    picks = {it["key"]: {"motif": it["motifs"][0], "stat": (it.get("stats") or [None])[0],
                         "palette": it["palettes"][0], "scored_by": "default"} for it in items}
    if not jev_call or not items:
        return picks
    questions, state = {}, {"cards": []}
    for n, it in enumerate(items):
        gist = (it.get("gist") or "")[:200]
        state["cards"].append({"n": n, "gist": gist, "title": (it["entry"].get("title") or "")[:120]})
        for j, m in enumerate(it["motifs"]):
            questions["c%d_m%d" % (n, j)] = {"type": "noul", "instructions": (
                "A newsletter card illustrates: \"%s\". How well does a simple line icon of a %s help a busy, "
                "non-technical reader grasp that at a glance? 1 = instantly clear." % (gist, m))}
        for j, s in enumerate(it.get("stats") or []):
            questions["c%d_s%d" % (n, j)] = {"type": "noul", "instructions": (
                "The card about \"%s\" shows one big number: \"%s %s\". How well does that single number make "
                "a non-technical reader understand why this matters? Penalize jargon." % (gist, s["value"], s["label"]))}
        for j, pal in enumerate(it["palettes"]):
            questions["c%d_p%d" % (n, j)] = {"type": "noul", "instructions": (
                "Colour mood \"%s\" for a card about \"%s\". How well does the mood fit the subject?"
                % (PALETTES[pal]["mood"], gist))}
    answers = jev_call(caller, state, questions)
    if not answers:
        return picks

    def best(prefix, options):
        scores = []
        for j, _ in enumerate(options):
            try:
                scores.append(float(answers[prefix % j]["noul"]))
            except (KeyError, TypeError, ValueError):
                return None
        return options[max(range(len(options)), key=lambda j: scores[j])]

    for n, it in enumerate(items):
        m = best("c%d_m%%d" % n, it["motifs"])
        s = best("c%d_s%%d" % n, it.get("stats") or []) if it.get("stats") else None
        p = best("c%d_p%%d" % n, it["palettes"])
        if m and p:
            picks[it["key"]] = {"motif": m, "stat": s or picks[it["key"]]["stat"], "palette": p, "scored_by": "jev"}
    return picks


# ------------------------------------------------------------------ specs

def spotlight_spec(entry, rank, gist, analogy, choice, label=None):
    return {
        "kind": "spotlight",
        "id": entry.get("id"),
        "rank": rank,
        "rank_label": label or ("Pick %d of today" % rank),
        "category": CATEGORY_PLAIN.get(entry.get("category"), "New AI launch"),
        "title": entry.get("title") or "",
        "gist": gist or entry.get("usp") or entry.get("summary") or "",
        "analogy": analogy or "",
        "stat": choice.get("stat") or {"value": "New", "label": "this week on the radar"},
        "motif": choice["motif"],
        "palette": choice["palette"],
    }


def idea_spec(idea, choice):
    plain = idea.get("plain") or {}
    return {
        "kind": "idea",
        "id": idea.get("id"),
        "title": plain.get("pitch") or idea.get("title") or "",
        "steps": [plain.get("problem_short") or "", plain.get("build_short") or "", plain.get("payer_short") or ""],
        "motif": choice.get("motif") or "puzzle",
        "palette": choice.get("palette") or "citrus",
    }


def image_path(spec):
    """Content-addressed: g/<kind>/<id>-<hash>.png."""
    blob = json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()
    h = hashlib.sha1(blob).hexdigest()[:10]
    slug = re.sub(r"[^a-z0-9-]+", "-", (spec.get("id") or "card").lower()).strip("-")[:48] or "card"
    return "g/%s/%s-%s.png" % (spec["kind"], slug, h)


# ------------------------------------------------------------------ HTML

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:%dpx;height:%dpx;overflow:hidden}
body{background:#F4F7F2;color:#0F1D16;font-family:"Inter","Helvetica Neue",Arial,sans-serif;position:relative}
.mono{font-family:"JetBrains Mono","DejaVu Sans Mono",Menlo,monospace}
"""


def _fit(text, sizes):
    """Pick a font size by length so the headline always fits."""
    n = len(text or "")
    for limit, size in sizes:
        if n <= limit:
            return size
    return sizes[-1][1]


def _motif_svg(name, color, size, stroke=6):
    path = MOTIFS.get(name, MOTIFS["puzzle"])[1]
    return ('<svg width="%d" height="%d" viewBox="0 0 120 120" fill="none" stroke="%s" stroke-width="%s" '
            'stroke-linecap="round" stroke-linejoin="round">%s</svg>' % (size, size, color, stroke, path))


def _wordmark(pos="left:72px;bottom:46px"):
    return ('<div style="position:absolute;%s;display:flex;align-items:center;gap:12px;'
            'font-size:24px;font-weight:700;color:#0F1D16;letter-spacing:-0.01em">'
            '<svg width="30" height="30" viewBox="0 0 32 32"><circle cx="16" cy="16" r="12" fill="none" stroke="#0F1D16" '
            'stroke-width="2.6"/><circle cx="16" cy="16" r="3" fill="#0F1D16"/></svg>Fresh Weights</div>' % pos)


def card_html(spec, fonts=True):
    pal = PALETTES.get(spec.get("palette"), PALETTES["leaf"])
    font_link = ('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;600;800&'
                 'family=JetBrains+Mono:wght@500;700&display=block" rel="stylesheet">') if fonts else ""
    w, h = SIZES.get(spec["kind"], (W, H))
    # render_graphics.mjs reads card-size to set the viewport.
    head = ('<!doctype html><html><head><meta charset="utf-8"><meta name="card-size" content="%dx%d">%s'
            '<style>%s</style></head><body>' % (w, h, font_link, CSS % (w, h)))
    if spec["kind"] == "spotlight":
        return head + _spotlight_body(spec, pal) + "</body></html>"
    return head + _idea_body(spec, pal) + "</body></html>"


def _spotlight_body(s, pal):
    gist = s.get("gist") or ""
    # Cards shrink to ~311px wide in a phone email (x0.26): type stays >= 44px.
    gsize = _fit(gist, [(45, 76), (70, 66), (95, 58), (130, 50), (999, 44)])
    stat = s.get("stat") or {}
    value = stat.get("value", "")
    vsize = _fit(value, [(4, 120), (6, 100), (8, 80), (99, 64)])
    return (
        # colour field on the right, the ticket sits on it
        '<div style="position:absolute;right:0;top:0;width:430px;height:630px;background:%s"></div>' % pal["tint"]
        + '<div style="position:absolute;right:110px;top:50px;opacity:.95">%s</div>' % _motif_svg(s["motif"], pal["accent"], 210)
        # the scale ticket
        + '<div style="position:absolute;right:54px;top:278px;width:330px;transform:rotate(-4deg);background:#ffffff;'
          'border-radius:6px;box-shadow:0 18px 40px rgba(15,29,22,.16);padding:30px 30px 26px">'
          '<div style="position:absolute;left:0;right:0;top:-9px;height:18px;background:radial-gradient(circle at 9px 9px,%s 7px,transparent 7.5px) 0 0/22px 18px repeat-x"></div>'
          '<div class="mono" style="font-size:%dpx;line-height:1;font-weight:700;color:#0F1D16;letter-spacing:-0.03em">%s</div>'
          '<div style="margin-top:14px;font-size:32px;line-height:1.2;font-weight:700;color:#33453b">%s</div>'
          '</div>' % (pal["tint"], vsize, e(value), e(stat.get("label", "")))
        # text column
        + '<div style="position:absolute;left:64px;top:56px;width:660px">'
          '<div style="display:inline-block;background:%s;color:#fff;font-size:30px;font-weight:700;padding:10px 22px;'
          'border-radius:999px">%s</div>'
          '<h1 style="margin-top:30px;font-size:%dpx;line-height:1.08;font-weight:800;letter-spacing:-0.03em;color:#0F1D16">%s</h1>'
          '</div>' % (pal["deep"], e(s.get("rank_label", "")), gsize, e(gist))
        + _wordmark()
    )


def _idea_body(s, pal):
    """1200x860, three stacked rows so the text survives a phone screen."""
    title = s.get("title") or ""
    tsize = _fit(title, [(45, 68), (70, 60), (100, 52), (999, 46)])
    heads = ("The problem", "What you build", "Who pays")
    motifs = (s.get("motif") or IDEA_STEP_MOTIFS[0],) + IDEA_STEP_MOTIFS[1:]
    rows = ""
    for i, (h, txt) in enumerate(zip(heads, s.get("steps") or ["", "", ""])):
        rows += (
            '<div style="display:flex;align-items:center;gap:30px;background:%s;border-radius:22px;padding:24px 32px;'
            'margin-top:%dpx">'
            '<div style="flex:none;width:96px;height:96px;border-radius:50%%;background:#fff;display:flex;'
            'align-items:center;justify-content:center">%s</div>'
            '<div><div style="font-size:30px;font-weight:700;color:%s">%d. %s</div>'
            '<div style="margin-top:4px;font-size:44px;line-height:1.15;font-weight:800;color:#0F1D16;'
            'letter-spacing:-0.02em">%s</div></div></div>'
            % (pal["tint"], 0 if i == 0 else 18, _motif_svg(motifs[i], pal["accent"], 64, 8), pal["deep"],
               i + 1, h, e(txt)))
    return (
        '<div style="position:absolute;left:64px;right:64px;top:52px">'
        '<div style="display:flex;justify-content:space-between;align-items:center">'
        '<div style="display:inline-block;background:%s;color:#fff;font-size:30px;font-weight:700;padding:10px 22px;'
        'border-radius:999px">Idea worth building</div>%s</div>'
        '<h1 style="margin-top:26px;font-size:%dpx;line-height:1.08;font-weight:800;letter-spacing:-0.03em">%s</h1>'
        '<div style="margin-top:36px">%s</div></div>'
        % (pal["deep"], _wordmark("position:static").replace("position:absolute;", ""), tsize, e(title), rows)
    )
