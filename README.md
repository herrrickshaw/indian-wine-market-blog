# indian-wine-market-blog

Home for a dedicated blog on the Indian wine market — production, imports and
tariffs, domestic market size, and the effect of trade deals such as the
India&ndash;EU agreement on import duty (currently 150%, with a path to
20&ndash;30% once that deal is in force). Not `masaladeutsch`: that blog
covers Indian trade/energy/industrial policy broadly and already carries one
wine-market post as a single topic among many; this repo is for a blog that
covers only this sector, in depth.

**Status: pre-launch.** No Blogger blog exists yet, so nothing can actually
publish or snapshot. What exists is the tooling, ported from
`blogger_writeups_code` and genericized — not reinvented — ready to point at
a blog the moment one exists. Then (following the `masaladeutsch` /
`blogger_writeups_code` split already used elsewhere in this account) this
may still split into a public entry-page repo plus this one as the private
version-history archive.

```
out/                        drafts land here before publishing
posts/<slug>.html           full post body, once snapshotted from live
meta/<slug>.json            id, title, url, dates, labels, size, sha256
CHANGELOG.md                one entry per snapshot
templates/post-skeleton.html  starting skeleton for a new post (placeholders
                               {{SITE_URL}} / {{SITE_INDEX_URL}} need filling
                               in once the blog and its index page exist)
tools/blogger_api.py        Blogger API v3 client — publish, update, snapshot
tools/snapshot.py           pull the live blog into posts/ + meta/, commit
tools/crosslink_analysis.py cross-link / orphan-post report across posts/
tools/research_crosscheck.py  dedup a draft's claims against posts/ before publishing
tools/svg_chart_lint.py     static SVG-chart bug lint (overflow, hidden text)
tools/mobile_check.py       render a post at a phone viewport, report overflow
                             (needs `playwright install chromium` once)
consistency-check/checks.py + fix.py + _local_check.py
                             the 10 house style rules, and an importable
                             repair() for local file content
```

## Set up, once the blog exists

1. Create the Blogger blog; note its numeric blog ID and URL.
2. `export WINE_BLOG_ID=<id>` and `export WINE_BLOG_URL=<https://...>`
   (or hardcode them in `tools/blogger_api.py`, matching the masaladeutsch original).
3. OAuth is already set up for this Google account from masaladeutsch — the
   Blogger API scope isn't per-blog, so `~/.config/market-secrets/blogger_client.json`
   and `blogger_token.json` work as-is. See
   `blogger_writeups_code/docs/BLOGGER_API_RUNBOOK.md` if that ever needs redoing.
4. Fill in `templates/post-skeleton.html`'s `{{SITE_URL}}` / `{{SITE_INDEX_URL}}`
   placeholders once there's a real index page to link to.

## How to run, once set up

Draft into `out/`, run `python3 consistency-check/_local_check.py out/<draft>.html`,
cross-check it against `posts/*.html` with
`python3 tools/research_crosscheck.py --auto --numbers out/<draft>.html`,
publish with `python3 tools/blogger_api.py insert --file out/<draft>.html --title "..."`,
then `python3 tools/snapshot.py --note "..." --push` to pull it back into
`posts/`/`meta/` and commit. **Publish before you snapshot** — snapshot.py
treats the live blog as the source of truth and will silently overwrite any
local edit to `posts/*.html` that was never published.

Deliberately not ported yet (masaladeutsch-specific content, or premature
with zero posts): `apply_labels.py`'s topic taxonomy, `build_index_page.py`
and the index-graph tooling, AdSense tooling, and assorted one-off historical
fix scripts. Pull any of them over from `blogger_writeups_code` if and when
this blog actually needs them.

## Data sources

No canonical data file exists yet — there is no automated data pipeline for
this repo. Prior exploratory drafts in this account (see
`blogger_writeups_code/out/post-indian-wine-market.html` and the published
`a-million-wine-market-behind-150-wall` post on masaladeutsch) sourced
figures from the IMARC Group's India Wine Market report and the
India&ndash;EU trade deal text. Any future canonical dataset for this repo
will be named here, with its authoritative location, per the account's
new-repo checklist.

## Backup

GitHub is primary. No large artifacts yet, so no `market-data-artifacts`
release is needed at this stage.
