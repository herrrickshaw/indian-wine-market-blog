# Indian wine market blog toolkit

Tooling and drafts for a dedicated blog on the Indian wine market: domestic production, imports and
tariffs, market size, and the effect of trade deals such as the India&ndash;EU agreement on import duty
(currently 150%, with a path to 20&ndash;30% once that deal is in force). It is not `masaladeutsch`, which
covers Indian trade/energy/industrial policy broadly and already carries one wine-market post; this repo is
for a blog that covers only this sector, in depth. Outputs are post HTML files in `out/` (drafts) and, once
a blog exists, `posts/` + `meta/` snapshots of the live site.

> Status: pre-launch. No Blogger blog exists yet, so nothing can publish or snapshot. The tooling was
> ported from `blogger_writeups_code` and genericised. Figures in drafts are from secondary sources
> (see Data sources); this is not investment or trade advice.

[How to use this repo with AI (Claude, Gemini, ChatGPT)](HOW_TO_USE_WITH_AI.md) - clone it, paste it into an
AI tool, and ask in plain English.

## What it does
- Publishes, updates and deletes posts through the Blogger API v3 (`tools/blogger_api.py`).
- Snapshots the live blog into `posts/` + `meta/` and semver-classifies each change (`tools/snapshot.py`).
- Checks a draft against the 10 house style rules (`consistency-check/_local_check.py`) and can repair
  known defects in a post body (`consistency-check/fix.py`).
- Lints inline SVG charts for label collisions and viewBox overflow (`tools/svg_chart_lint.py`).
- Reports orphan posts / cross-links and de-duplicates a draft's claims against published posts.
- Renders a post at phone width and reports horizontal overflow (`tools/mobile_check.py`).

## Quick start
```bash
git clone https://github.com/herrrickshaw/indian-wine-market-blog.git
cd indian-wine-market-blog
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest ruff
python3 consistency-check/_local_check.py out/post-eu-fta-wine-tariff-cut.html   # needs node
```

## How it works
Draft, check, publish, snapshot:

| Stage | Module | Output |
|---|---|---|
| Draft | `templates/post-skeleton.html`, `templates/*.css` | `out/<draft>.html` |
| Check | `consistency-check/checks.py` (rules), `_local_check.py` (CLI) | pass/fail per rule |
| Repair | `consistency-check/repair.py` (pure), `fix.py` (fetch + browser verify) | corrected bodies in `consistency-check/fixes/` |
| Lint charts | `tools/svg_chart_lint.py` | issue list / JSON |
| Cross-check | `tools/research_crosscheck.py`, `tools/crosslink_analysis.py` | duplicate-claim / orphan reports |
| Publish | `tools/blogger_api.py insert --file out/<draft>.html --title "..."` | live post |
| Snapshot | `tools/snapshot.py` (I/O), `tools/versioning.py` (pure semver logic) | `posts/`, `meta/`, `CHANGELOG.md` |

Design notes: every rule in `checks.py` came from a real defect on the original blog; pure logic
(`repair.py`, `versioning.py`) is kept apart from network, subprocess and browser code so it can be tested
offline. **Publish before you snapshot**: `snapshot.py` treats the live blog as the source of truth and will
silently overwrite any local edit to `posts/*.html` that was never published.

Typical session:
```bash
python3 consistency-check/_local_check.py out/<draft>.html
python3 tools/research_crosscheck.py --auto --numbers out/<draft>.html
python3 tools/blogger_api.py insert --file out/<draft>.html --title "..."
python3 tools/snapshot.py --note "..." --push
```

## Repository layout
| Path | Purpose |
|---|---|
| `out/` | Drafts land here before publishing (one draft tracked: `post-eu-fta-wine-tariff-cut.html`) |
| `posts/<slug>.html` | Full post bodies, once snapshotted from live |
| `meta/<slug>.json` | id, title, url, dates, labels, size, sha256 per post |
| `templates/` | `post-skeleton.html` (fill `{{SITE_URL}}` / `{{SITE_INDEX_URL}}`), site and research CSS |
| `tools/` | Blogger client, snapshot, versioning, cross-link, cross-check, SVG lint, mobile check |
| `consistency-check/` | House rules, local checker, repair functions and live-verify harness |
| `tests/` | Offline pytest suite incl. golden characterisation of pre-refactor behaviour |
| `CHANGELOG.md` | One entry per snapshot (none yet) |

## Data sources
| Source | What is taken | Access route | Licence / caveat |
|---|---|---|---|
| IMARC Group, India Wine Market report | Market-size figures in earlier drafts | Secondary, via earlier drafts in `blogger_writeups_code` | Vendor estimates; verify before citing |
| India&ndash;EU trade deal text | Import-duty schedule | Public text | Check final in-force schedule |

No canonical data file exists yet and there is no automated data pipeline. Earlier exploratory drafts live in
`blogger_writeups_code/out/post-indian-wine-market.html` and the published `a-million-wine-market-behind-150-wall`
post on masaladeutsch.

## Configuration
| Variable / file | Meaning |
|---|---|
| `WINE_BLOG_ID`, `WINE_BLOG_URL` | Numeric Blogger blog ID and URL (or hardcode in `tools/blogger_api.py`) |
| `~/.config/market-secrets/blogger_client.json`, `blogger_token.json` | OAuth client + token, shared with masaladeutsch (the Blogger scope is not per-blog) |

Secrets are never committed (`.gitignore` blocks `*client_secret*`, `*blogger_client*`, `*token*`). See
`blogger_writeups_code/docs/BLOGGER_API_RUNBOOK.md` if OAuth needs redoing. `tools/mobile_check.py` needs
`playwright install chromium` once.

## Testing
```bash
pytest -q          # 16 tests, offline; the node-based test skips if node is absent
ruff check .
```

## Limitations and known issues
- Pre-launch: no blog, so publish / snapshot paths are untested against a live API.
- `fix.py` live verification needs a published blog and a headless browser; it is not covered by tests.
- Deliberately not ported yet: `apply_labels.py` topic taxonomy, `build_index_page.py` and index-graph
  tooling, AdSense tooling, and one-off historical fix scripts. Pull them from `blogger_writeups_code` if needed.
- The disclosure paragraph in `consistency-check/fix.py` names the author and links to `/p/disclaimer.html`.
- Once live this may split into a public entry-page repo plus a private version-history archive, mirroring
  the `masaladeutsch` / `blogger_writeups_code` split.

## Related repositories
- `blogger_writeups_code` - origin of the ported tooling and the Blogger runbook.
- `masaladeutsch` - the broader trade/energy blog this one is separate from.

## Licence
No licence file yet - all rights reserved.

GitHub is primary for backup; there are no large artifacts yet, so no `market-data-artifacts` release is needed.
