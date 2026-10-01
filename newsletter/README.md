# Newsletter (Kit)

`scripts/digest.py` builds the weekly and monthly digests, writes a snapshot to
`data/digests/`, renders the email, and can create a **draft** broadcast in Kit.
It never sends or schedules. A human reviews the draft in Kit and presses send.

## One-time Kit setup

1. In Kit, go to Send → Email Templates → New template → **Start from scratch / HTML**.
   Paste `newsletter/kit-template.html` and save it as "Fresh Weights".
   The template contains `{{ message_content }}`, `{{ unsubscribe_url }}` and `{{ address }}`.
   Kit needs all three, and the physical address comes from your account settings.
2. Note the template's id (it is in the editor URL) and store it as `KIT_TEMPLATE_ID`.
3. Create a v4 API key (Settings → Developer) and store it in the Secure Vault as `KIT_API_KEY`.

## Env vars

| Var | Needed for | Notes |
|---|---|---|
| `KIT_API_KEY` | `--kit-draft` | Sent only as the `X-Kit-Api-Key` header. It is never printed, logged or written. |
| `KIT_TEMPLATE_ID` | optional | When it is unset, Kit uses the account's default template. |

## Cron usage (IST)

```
# Saturday 08:30: the Saturday-Friday week that just ended
python3 scripts/digest.py weekly --kit-draft
# First Sunday of the month 09:00: the previous calendar month
python3 scripts/digest.py monthly --kit-draft
```

Useful flags: `--week 2026-W39`, `--month 2026-09`, `--data-dir`, `--out-dir`
(default `newsletter/out/`). Run the weekly digest **after** the weekly debate
(`idea_debate.py`). If the debate has not run, the digest falls back to the
top 3 daily ideas and records `"ideas_source": "daily-fallback"`. To keep the
`data/digests/*.json` snapshots, commit them.

Spotlight history comes from `git log` of `data/spotlight.json`. When the
checkout is shallow or the period is not in local history, it falls back to
the public GitHub API, and after that to the current file. The cron checkout
needs enough depth, for example `git fetch --shallow-since=40.days`.

`newsletter/examples/` holds previews built from real data.

## Verify against the Kit docs (developers.kit.com could not be reached from the build box)

- [ ] `POST https://api.kit.com/v4/broadcasts` with `send_at: null` creates a **draft** and does not send.
- [ ] The body field names: `subject, preview_text, content, description, public, published_at, send_at, email_template_id`.
- [ ] The response shape is `{"broadcast": {"id": ...}}`.
- [ ] The free plan allows API broadcast creation, and `public: true` (web archive) is allowed.
- [ ] Kit does not rewrite the inline styles or the `<style>` media queries in the custom template.
- [ ] Send a test to Gmail, Apple Mail and Outlook, and check dark mode and the 375px layout.
