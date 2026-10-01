# Domain migration: dashboard.voyagebliss.in → freshweights.com

Decision (2026-10-01): **apex `freshweights.com` is canonical**, `www` redirects
to apex, and `dashboard.voyagebliss.in` **301-redirects permanently**.

## Why the order matters

GitHub Pages serves one custom domain per repo. The moment the `CNAME` file
on `main` says `freshweights.com`, Pages stops serving
`dashboard.voyagebliss.in`. So DNS for the new domain must exist **before**
this branch is merged, and the old-domain redirect must exist **before or at**
the merge.

## Steps

1. **Cloudflare DNS for `freshweights.com`.** Do this in the dashboard; the
   `custom.cloudflare` token has no DNS scope and gets a 403. Alternatively,
   add `Zone → DNS → Edit` for freshweights.com and voyagebliss.in to the
   token first.
   | Type | Name | Content | Proxy |
   |---|---|---|---|
   | A | `@` | 185.199.108.153 | DNS only (grey) at first |
   | A | `@` | 185.199.109.153 | DNS only |
   | A | `@` | 185.199.110.153 | DNS only |
   | A | `@` | 185.199.111.153 | DNS only |
   | AAAA | `@` | 2606:50c0:8000::153 | DNS only |
   | AAAA | `@` | 2606:50c0:8001::153 | DNS only |
   | AAAA | `@` | 2606:50c0:8002::153 | DNS only |
   | AAAA | `@` | 2606:50c0:8003::153 | DNS only |
   | CNAME | `www` | `boringalgos.github.io` | DNS only |

   Start with **DNS only (grey cloud)**. GitHub's certificate issuance
   (Let's Encrypt HTTP challenge) is most reliable when it can see GitHub's
   IPs directly. After the cert shows as issued, you can switch to proxied
   (orange). If you do, set SSL/TLS mode to **Full**, not Flexible, to avoid
   redirect loops.

2. **Pages source → GitHub Actions.** Repo → Settings → Pages → Build and
   deployment → Source: *GitHub Actions*. This branch adds
   `.github/workflows/pages.yml`, which builds the static SEO pages
   (`/launch/…`, `/category/…`, sitemap, RSS) on every push and deploys them.
   Until you flip this, the legacy build keeps serving `main` as-is (the
   dashboard still works; the SEO pages just aren't published).

3. **Merge this branch to `main`.** That commits `CNAME = freshweights.com`.
   With an Actions deploy the custom domain is taken from Settings → Pages →
   Custom domain, so also type `freshweights.com` there and save. That's the
   setting Actions deploys actually use; the `CNAME` file is kept for the
   legacy build and as documentation.

4. **Certificate.** Settings → Pages: wait for "DNS check successful", then
   tick **Enforce HTTPS**. Usually under 30 minutes, sometimes up to 24h.

5. **Old domain 301.** Cloudflare → voyagebliss.in → Rules → Redirect Rules →
   Create:
   - When: Hostname equals `dashboard.voyagebliss.in`
   - Then: Dynamic redirect, expression
     `concat("https://freshweights.com", http.request.uri.path)`,
     status **301**, *preserve query string* on.

   The `dashboard` DNS record must stay **proxied (orange)** so the rule can
   run. Its target doesn't matter once the rule is in place.

   Because the redirect keeps the path, old deep links still land: `/live`,
   `/ideas` and `/launch/<id>` resolve on the new site (real pages or the
   404 shim).

6. **www → apex.** GitHub Pages already redirects `www` to the apex when the
   apex is the configured custom domain and the `www` CNAME exists. No rule
   is needed.

7. **Search Console.** Add `freshweights.com` as a Domain property (TXT record
   in Cloudflare), submit `https://freshweights.com/sitemap.xml`, and use
   *Change of address* from the old property if voyagebliss.in was verified.

## Things that keep working untouched

- Crons PUT to `data/*.json` through the GitHub API. They don't depend on
  the domain.
- The Instinct Worker (`radar-ingest.voyagebliss-visa-auditor.workers.dev`)
  is on workers.dev and doesn't depend on the domain.
- The dashboard's `raw.githubusercontent.com` data fallback.

## Rollback

Set the `CNAME` file and the Pages custom domain back to
`dashboard.voyagebliss.in`, and disable the redirect rule.
