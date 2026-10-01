# Monthly digest (first Sunday of the month, 09:00 IST)

Covers the previous calendar month. Schedule it as "every Sunday 09:00" and
have the cron exit immediately unless the day of the month is 1–7.

Same as the weekly digest, with `python3 scripts/digest.py monthly --kit-draft --out-dir /tmp/radar-digest/out`.
It writes `data/digests/monthly-YYYY-MM.json` (PUT it as a new file). The
month's best idea is chosen from that month's weekly snapshots plus
ideas.json, so ideas dropped by the 60-idea cap still count.
Create the Kit draft only. Never send it.
