# Change to the hourly Spotlight cron

`scripts/spotlight.py` now defers to the daily debate. While the published
`data/spotlight.json` is a debate pick less than 26 hours old, it makes no
JEV call and prints `spotlight: debate pick from … is current; unchanged`.

Update the cron body: when that line is printed, **skip the PUT** (nothing
changed). Everything else stays the same. If the debate didn't run, the hourly
picker takes over again automatically.
