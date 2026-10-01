"""Shared helpers for the newer radar scripts (seo_jev.py, idea_debate.py,
digest.py, build_site.py).

spotlight.py and ingest.py keep their own copies of these helpers on purpose:
they are live in the crons and are not touched by this module.

Credential rules (same as the rest of the radar):
  - JEV calls go through ~/workspace/jev-costs/jev-tracked.py, which reads the
    OpenRouter key from the Secure Vault itself.
  - Direct OpenRouter chat calls (the weekly debate) read the key from the
    OPENROUTER_API_KEY environment variable that the cron injects from the
    vault. The key is never printed, logged or written to disk.
"""

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.join(ROOT, "data")

JEV_TRACKED = os.path.expanduser("~/workspace/jev-costs/jev-tracked.py")
CHECK_BALANCE = os.path.expanduser("~/workspace/jev-costs/check-balance.py")
SUMMARY = os.path.expanduser("~/workspace/jev-costs/summary.py")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def now_ist():
    return datetime.now(IST)


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_config():
    return load_json(os.path.join(ROOT, "site.config.json"), {})


def budget_ok():
    """Same guard as the crons: skip paid calls if OpenRouter remaining < $2
    or tracked 24h spend > $1. Fails closed."""
    try:
        out = subprocess.run(["python3", CHECK_BALANCE], capture_output=True,
                             text=True, timeout=60).stdout
        m = re.search(r"Remaining:\s*\$\s*([\d.]+)", out)
        if not m or float(m.group(1)) < 2.0:
            print("budget guard: remaining < $2, skipping paid calls")
            return False
        out = subprocess.run(["python3", SUMMARY], capture_output=True,
                             text=True, timeout=60).stdout
        m = re.search(r"Last 24h:\s*\$\s*([\d.]+)", out)
        if not m or float(m.group(1)) > 1.0:
            print("budget guard: 24h spend > $1, skipping paid calls")
            return False
        return True
    except Exception as e:
        print("budget guard: check failed (%s), skipping paid calls" % e)
        return False


def jev_call(caller, state, questions, timeout=180):
    """One tracked JEV call (OpenRouter provider). Returns the answers dict,
    or None on any failure."""
    cmd = ["python3", JEV_TRACKED, "--caller", caller,
           "--provider", "openrouter",
           "--state", json.dumps(state),
           "--questions-json", json.dumps(questions)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
        if proc.returncode != 0:
            print("jev call failed: %s" % proc.stderr.strip()[:200])
            return None
        return json.loads(proc.stdout).get("answers", {})
    except Exception as e:
        print("jev call error: %s" % e)
        return None


def noul(answers, key):
    """Pull a 0..1 float out of a JEV noul answer, or None."""
    try:
        return max(0.0, min(1.0, float(answers[key]["noul"])))
    except (KeyError, TypeError, ValueError):
        return None


def openrouter_models():
    """Public model list (no key needed). Returns a set of ids, or None."""
    try:
        req = urllib.request.Request(OPENROUTER_MODELS_URL,
                                     headers={"User-Agent": "ai-launch-radar"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {m["id"] for m in json.load(resp).get("data", [])}
    except Exception as e:
        print("could not list OpenRouter models: %s" % e)
        return None


def openrouter_chat(model, messages, max_tokens=4000, json_mode=True,
                    timeout=240, reasoning_effort=None):
    """One chat completion. Returns (text, cost_usd). Raises on failure.
    The key comes from OPENROUTER_API_KEY and never leaves this function."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set (inject it from the vault)")
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "usage": {"include": True},
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if reasoning_effort:
        # OpenRouter's unified knob; models without reasoning ignore it.
        # Reasoning tokens bill as output, so "low" keeps the debate cheap.
        body["reasoning"] = {"effort": reasoning_effort}
    req = urllib.request.Request(
        OPENROUTER_URL, data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 "HTTP-Referer": "https://freshweights.com",
                 "X-Title": "Fresh Weights idea debate"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    text = payload["choices"][0]["message"]["content"] or ""
    cost = float((payload.get("usage") or {}).get("cost") or 0.0)
    return text, cost


def parse_json_reply(text):
    """Models sometimes wrap JSON in prose or fences; take the outermost object."""
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start:end + 1])


def url_resolves(url, timeout=12):
    """True if the URL answers with a non-error status. Used to reject
    invented links before anything is published."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method,
                                         headers={"User-Agent": "Mozilla/5.0 (radar link check)"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status < 400:
                    return True
        except urllib.error.HTTPError as e:
            if method == "GET" and e.code in (401, 403, 429):
                # bot-walled but exists (X, LinkedIn, Medium...)
                return True
        except Exception:
            pass
    return False


def all_entries():
    live = load_json(os.path.join(DATA, "launches.json"), {}).get("launches", [])
    arch = load_json(os.path.join(DATA, "archive.json"), {}).get("launches", [])
    return live, arch


def iso_week(d):
    y, w, _ = d.isocalendar()
    return "%d-W%02d" % (y, w)


def slug_for(entry_id):
    """Stable URL slug for a launch/idea page. Ids can run past 120 chars
    (they are slugified titles), so long ones are cut at the last hyphen
    within 72 chars. Deterministic forever: changing this breaks links.
    Mirrored in index.html (slugFor) and must stay identical."""
    entry_id = entry_id or ""
    if len(entry_id) <= 72:
        return entry_id
    cut = entry_id[:72]
    if "-" in cut:
        cut = cut[:cut.rindex("-")]
    return cut.strip("-")
