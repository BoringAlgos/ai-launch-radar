# Monthly running cost (estimate, October 2026 prices)

Target: under **$20/month**. Estimate: **about $5/month**, about $8 worst case.

Cadence: weekly issue every **Saturday** (4–5 a month, from a Friday debate),
monthly issue on the **first Sunday** of the month (no extra debate; it reuses
the month's weekly ideas).

## 1. Weekly idea debate: 5 models via OpenRouter

Prices per 1M tokens (input / output), from the OpenRouter model pages:

| Model (OpenRouter id) | Input | Output | Source |
|---|---|---|---|
| GPT-6 Sol (`openai/gpt-6-sol`) | $2.00 | $10.00 | [openrouter.ai/openai/gpt-6-sol](https://openrouter.ai/openai/gpt-6-sol) |
| Claude Sonnet 5.5 (`anthropic/claude-sonnet-5.5`) | $2.00 | $10.00 | [openrouter.ai/anthropic/claude-sonnet-5.5](https://openrouter.ai/anthropic/claude-sonnet-5.5) |
| Xiaomi MiMo-V2.6-Pro (`xiaomi/mimo-v2.6-pro`) | $0.43 | $0.87 | [openrouter.ai/xiaomi/mimo-v2.6-pro](https://openrouter.ai/xiaomi/mimo-v2.6-pro) |
| Qwen3.8 Max (`qwen/qwen3.8-max`) | $2.00 | $6.00 | [openrouter.ai/qwen/qwen3.8-max-20260803](https://openrouter.ai/qwen/qwen3.8-max-20260803) |
| Kimi K2.5 (`moonshotai/kimi-k2.5`) | $0.45 | $2.25 | [openrouter.ai/moonshotai/kimi-k2.5](https://openrouter.ai/moonshotai/kimi-k2.5) |

Tokens per model per debate (3 rounds, 2 critics per idea, reasoning effort
"low", +25% for retries). The shared radar context is about 7.8k tokens and is
sent with every call:

| Round | Input | Output |
|---|---|---|
| 1 Propose (3 ideas) | ~8k | ~5k |
| 2 Critique (6 rival ideas) | ~14k | ~2k |
| 3 Revise | ~12k | ~5k |
| **Total incl. retry buffer** | **~43k** | **~15k** |

| Model | Cost per debate |
|---|---|
| GPT-6 Sol | $0.24 |
| Claude Sonnet 5.5 | $0.24 |
| Qwen3.8 Max | $0.18 |
| Kimi K2.5 | $0.05 |
| MiMo-V2.6-Pro | $0.03 |
| JEV judge (1 call) | <$0.01 |
| **Per debate** | **≈ $0.74** |

**Per month (4–5 debates): $3.00–3.70.** Hard cap per run: $1.50
(`debate.max_run_cost_usd`). A run that hits it aborts and publishes nothing.

## 2. JEV (OpenRouter, typesafe/jev-1.13: $0.042/1M input, output free)

| Caller | Volume | Per month |
|---|---|---|
| Daily scoring (4 runs × ≤10 calls) | ~80k tokens/day | $0.10 |
| Hourly Spotlight (24 calls/day) | ~190k tokens/day | $0.24 |
| SEO titles (≤2 calls/day) | ~30k tokens/day | $0.04 |
| Debate judge (weekly) | negligible | <$0.01 |
| **JEV total** | | **≈ $0.40** |

## 3. Everything else

| Item | Plan | Per month |
|---|---|---|
| freshweights.com | ₹1240/year | ≈ $1.20 |
| GitHub Pages + Actions | free for public repos | $0 |
| Cloudflare (DNS, redirect rule) | free plan | $0 |
| Kit newsletter | free plan, up to 10,000 subscribers | $0 |
| Instinct ingest Worker | Workers free tier | $0 |

## Total

| | Typical | Worst case |
|---|---|---|
| Debates | $3.00 | $5.50 (5 debates, every turn retried, Qwen at the higher price) |
| JEV | $0.40 | $0.60 |
| Domain | $1.20 | $1.20 |
| **Total** | **≈ $4.60** | **≈ $7.30** |

That leaves about $12 of headroom under $20. Spending some of it on reasoning
effort `"medium"` (roughly doubles debate output tokens, +$2–3/month) is the
cheapest quality upgrade.

## Not included

- **The Muse/Instinct agents' own runtime** for the 4 daily research runs and
  the cron bodies. They run on your existing setup, which these numbers don't
  cover. This is likely the biggest real cost, so check it separately.
- **The $10 OpenRouter cap.** Debates and JEV together draw about $3.50–4.50 a
  month from the same key. If the $10 is a one-time balance rather than a
  monthly limit, it lasts about 2 months, and the budget guard then pauses all
  paid calls (the site keeps working on fallbacks). Top it up monthly, or raise
  it to $15.
- Kit's paid plan, only needed past 10,000 subscribers or to remove Kit
  branding (from about $39/month).
