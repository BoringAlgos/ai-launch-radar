# Add to the 22:00 run, after "Brief" and before "Push"

SEO: download `scripts/seo_jev.py`, `scripts/build_site.py`, `scripts/jevlib.py`,
`site.config.json` and `data/seo.json` (it may not exist yet; that's fine).
Run `python3 scripts/seo_jev.py`. It scores titles and descriptions only for
pages without a record, makes at most 2 JEV calls (`--caller radar-seo`), and
honours the same budget guard. If `data/seo.json` changed, PUT it with the
other files. These calls are separate from the 10-call scoring cap.
