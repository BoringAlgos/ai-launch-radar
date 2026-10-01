# Monthly running cost (estimate, October 2026 prices)

**Budget: $10/month on OpenRouter** (the key's limit, shared by every paid
call). **Estimate: about $6.40 a month typical, about $9.15 worst case.** The
budget guard stops all paid calls once less than $2 of the month's limit is
left, so the limit can't be overrun. If that happens late in a month, the site
falls back to the free deterministic Spotlight until the limit resets.

Cadence:
- Spotlight: picked **daily** by a five-model debate (website and newsletter
  show the same picks).
- Ideas: one five-model debate every **Friday**.
- Newsletter: weekly issue every **Saturday**; monthly issue on the **first
  Sunday** (no extra debate).

## Model prices (per 1M tokens, input / output, OpenRouter)

| Model (OpenRouter id) | Input | Output | Source |
|---|---|---|---|
| GPT-6 Sol (`openai/gpt-6-sol`) | $2.00 | $10.00 | [openrouter.ai/openai/gpt-6-sol](https://openrouter.ai/openai/gpt-6-sol) |
| Claude Sonnet 5.5 (`anthropic/claude-sonnet-5.5`) | $2.00 | $10.00 | [openrouter.ai/anthropic/claude-sonnet-5.5](https://openrouter.ai/anthropic/claude-sonnet-5.5) |
| Xiaomi MiMo-V2.6-Pro (`xiaomi/mimo-v2.6-pro`) | $0.43 | $0.87 | [openrouter.ai/xiaomi/mimo-v2.6-pro](https://openrouter.ai/xiaomi/mimo-v2.6-pro) |
| Qwen3.8 Max (`qwen/qwen3.8-max`) | $2.00 | $6.00 | [openrouter.ai/qwen/qwen3.8-max-20260803](https://openrouter.ai/qwen/qwen3.8-max-20260803) |
| Kimi K2.5 (`moonshotai/kimi-k2.5`) | $0.45 | $2.25 | [openrouter.ai/moonshotai/kimi-k2.5](https://openrouter.ai/moonshotai/kimi-k2.5) |
| JEV (`typesafe/jev-1.13`) | $0.042 | free | handoff §5 |

## 1. Weekly idea debate (Friday): ≈ $0.74 per run

Three rounds (propose, critique 2 rivals, revise), reasoning effort "low",
+25% retry buffer: about 43k input and 15k output tokens per model.

| Model | Per run |
|---|---|
| GPT-6 Sol | $0.24 |
| Claude Sonnet 5.5 | $0.24 |
| Qwen3.8 Max | $0.18 |
| Kimi K2.5 | $0.05 |
| MiMo-V2.6-Pro | $0.03 |
| JEV judge + card styling | <$0.01 |

4–5 runs a month: **$3.20–3.70**. Hard cap $1.50 per run.

## 2. Daily Spotlight debate: ≈ $0.10 per run

One round: each model ranks 4 of about 20 launches and writes a plain-English
line, an analogy and a "why now". That's about 4.5k input and 1.6k output tokens
per model, +25% retries.

| Model | Per run |
|---|---|
| GPT-6 Sol | $0.031 |
| Claude Sonnet 5.5 | $0.031 |
| Qwen3.8 Max | $0.024 |
| Kimi K2.5 | $0.007 |
| MiMo-V2.6-Pro | $0.004 |
| JEV judge + card styling (2 calls) | <$0.001 |

30–31 runs a month: **≈ $3.00**. Hard cap $0.30 per run.

## 3. JEV everywhere else: ≈ $0.20 a month

Daily scoring (4 runs × ≤10 calls) about $0.10; SEO titles about $0.04;
newsletter card styling about $0. The hourly Spotlight picker now makes **no**
JEV call while the day's debate pick is fresh, which saves about $0.24 a month.

## Total against the $10 limit

| | Typical | Worst case |
|---|---|---|
| Idea debates | $3.20 | $4.75 (5 Fridays, every turn retried) |
| Spotlight debates | $3.00 | $4.00 (31 days, every turn retried) |
| JEV | $0.20 | $0.40 |
| **OpenRouter total** | **≈ $6.40** | **≈ $9.15** |

Outside OpenRouter: domain ≈ $1.20/month. GitHub Pages and Actions,
Cloudflare, Kit (free up to 10,000 subscribers) and the Instinct Worker are
free.

## If a month runs hot

The cheapest lever is the daily Spotlight panel. Set
`spotlight.models` in `site.config.json` to drop GPT-6 Sol or Claude Sonnet 5.5
from the **daily** vote only (each saves about $0.95/month). The weekly idea
debate keeps all five.

## Not included

The Muse/Instinct agents' own runtime (the four daily research runs and the
cron bodies) runs on your existing setup and isn't covered here.
