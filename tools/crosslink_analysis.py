#!/usr/bin/env python3
"""Map the internal cross-link graph across posts/*.html and flag opportunities.

Every post already carries three boilerplate blogspot.com links (the topnav
"All Articles (Index)" link, the AI-disclosure link to /p/disclaimer.html, and
often an About/Contact/Terms footer link). None of those are "cross-links" in
the editorial sense this script cares about -- they say nothing about which
articles are related. What this script counts is inline body links and
"Related on this blog" links from one dated post (/YYYY/MM/slug.html) to
another, which is the signal a reader or a search engine actually uses to
discover a related article.

Output: a markdown report -- orphans (no outbound content links, no inbound
links), best-connected hub posts, and candidate link opportunities scored by
shared taxonomy labels and shared named entities (reusing index_graph.py's
extraction) between posts that currently have no link between them at all.

    python3 tools/crosslink_analysis.py                  # human-readable report
    python3 tools/crosslink_analysis.py --json out.json  # machine-readable
"""

import argparse
import collections
import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

# BLOG_URL comes from blogger_api's env-var config (WINE_BLOG_URL) so this
# stays in sync with whatever blog is actually configured -- see blogger_api.py.
try:
    import blogger_api as _B

    _BLOG_URL = _B.BLOG_URL
except SystemExit:
    _BLOG_URL = None
_BLOG_URL = (
    _BLOG_URL or os.environ.get("WINE_BLOG_URL") or "https://REPLACE-ME.blogspot.com"
)

# A dated post URL: /2026/09/some-slug.html -- what a genuine cross-link looks like.
# Slug charset includes underscores: on masaladeutsch, Blogger appended "_<digits>"
# to disambiguate a duplicate slug (e.g.
# chemical-import-substitution-full-hsn-8_01082878313), and a [a-z0-9-]-only class
# silently missed every link to or from such a post -- it misreported as orphaned
# when it was not. Kept broad here in case this blog hits the same thing.
POST_LINK_RE = re.compile(
    r'href="'
    + re.escape(_BLOG_URL)
    + r'/(\d{4})/(\d{2})/([a-z0-9_-]+)\.html(?:#[^"]*)?"',
    re.IGNORECASE,
)
# Boilerplate destinations that don't count as editorial cross-links.
NOISE_SLUGS = {"article-index-start-here"}


def strip_tags_scripts(html_body):
    return re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ",
        html_body,
        flags=re.DOTALL | re.IGNORECASE,
    )


def load_manifest():
    m = json.loads((ROOT / "meta" / "manifest.json").read_text())["posts"]
    by_slug = {}
    for rec in m.values():
        by_slug[rec["slug"]] = rec
    return by_slug


def extract_outbound(path, self_slug):
    """Return the set of distinct post slugs this file links to (self excluded)."""
    body = strip_tags_scripts(Path(path).read_text(encoding="utf-8", errors="replace"))
    targets = set()
    for _, _, slug in POST_LINK_RE.findall(body):
        slug = slug.lower()
        if slug == self_slug or slug in NOISE_SLUGS:
            continue
        targets.add(slug)
    return targets


def build_graph(by_slug):
    """out_links[slug] = set of slugs it links to. Only counts slugs that are
    live (in the manifest) on both ends, so a link to a since-deleted post
    doesn't show up as a dangling edge."""
    out_links = {}
    for f in sorted(glob.glob(str(ROOT / "posts" / "*.html"))):
        slug = Path(f).stem
        if slug not in by_slug:
            continue  # archived file, no live post -- same rule index_graph.py uses
        targets = extract_outbound(f, slug)
        out_links[slug] = {t for t in targets if t in by_slug}
    return out_links


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", metavar="PATH")
    ap.add_argument(
        "--min-label-cluster",
        type=int,
        default=3,
        help="ignore labels with fewer than this many live posts",
    )
    ap.add_argument("--top", type=int, default=40, help="max rows per list section")
    args = ap.parse_args()

    by_slug = load_manifest()
    out_links = build_graph(by_slug)

    in_links = collections.defaultdict(set)
    for src, targets in out_links.items():
        for t in targets:
            in_links[t].add(src)

    all_slugs = set(by_slug) & set(out_links)  # skip UTILITY pages with no post file
    out_deg = {s: len(out_links.get(s, set())) for s in all_slugs}
    in_deg = {s: len(in_links.get(s, set())) for s in all_slugs}

    orphans_both = sorted(s for s in all_slugs if out_deg[s] == 0 and in_deg[s] == 0)
    orphans_out_only = sorted(s for s in all_slugs if out_deg[s] == 0 and in_deg[s] > 0)
    orphans_in_only = sorted(s for s in all_slugs if out_deg[s] > 0 and in_deg[s] == 0)

    hubs_out = sorted(all_slugs, key=lambda s: -out_deg[s])[: args.top]
    hubs_in = sorted(all_slugs, key=lambda s: -in_deg[s])[: args.top]

    total_edges = sum(out_deg.values())
    linked_pairs = set()
    for s, targets in out_links.items():
        for t in targets:
            linked_pairs.add(frozenset((s, t)))

    # Label clusters: which posts share a label, per the manifest's own tagging.
    label_members = collections.defaultdict(list)
    for slug, rec in by_slug.items():
        if slug not in all_slugs:
            continue
        for lab in rec.get("labels", []):
            label_members[lab].append(slug)

    # Candidate opportunities: same-label pairs with zero link in either direction,
    # ranked by how small/specific the shared label is (a shared niche label like
    # "Circularity & Waste-to-Value" is a much stronger signal than both posts
    # merely sharing "Energy & Fuels", which half the blog carries).
    candidates = []
    for lab, members in label_members.items():
        n = len(members)
        if n < args.min_label_cluster or n > 40:
            continue  # too small to matter, or too generic to be a real signal
        weight = 1.0 / n  # rarer label => higher-confidence signal per pair
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if frozenset((a, b)) in linked_pairs:
                    continue
                candidates.append((weight, lab, a, b))

    # Aggregate multi-label evidence: a pair sharing two rare labels is a much
    # better candidate than a pair sharing one.
    pair_score = collections.defaultdict(float)
    pair_labels = collections.defaultdict(list)
    for weight, lab, a, b in candidates:
        key = frozenset((a, b))
        pair_score[key] += weight
        pair_labels[key].append(lab)

    ranked_candidates = sorted(pair_score.items(), key=lambda kv: -kv[1])[: args.top]

    report = {
        "posts_considered": len(all_slugs),
        "total_content_link_edges": total_edges,
        "distinct_linked_pairs": len(linked_pairs),
        "orphans_both_directions": orphans_both,
        "orphans_no_outbound_only": orphans_out_only,
        "orphans_no_inbound_only": orphans_in_only,
        "top_out_degree": [(s, out_deg[s]) for s in hubs_out],
        "top_in_degree": [(s, in_deg[s]) for s in hubs_in],
        "candidate_pairs": [
            {
                "slugs": sorted(key),
                "score": round(score, 3),
                "shared_labels": pair_labels[key],
            }
            for key, score in ranked_candidates
        ],
    }

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))
        print(f"wrote {args.json}")
        return 0

    print(f"posts considered: {report['posts_considered']}")
    print(f"content cross-link edges (directed): {report['total_content_link_edges']}")
    print(f"distinct linked pairs: {report['distinct_linked_pairs']}")
    avg_out = report["total_content_link_edges"] / max(1, report["posts_considered"])
    print(f"average outbound content links per post: {avg_out:.2f}")
    print()
    print(
        f"=== Orphans: zero outbound AND zero inbound content links ({len(orphans_both)}) ==="
    )
    for s in orphans_both[: args.top]:
        print(f"  {s}")
    if len(orphans_both) > args.top:
        print(f"  ... and {len(orphans_both) - args.top} more")
    print()
    print(f"=== Zero outbound, but linked FROM elsewhere ({len(orphans_out_only)}) ===")
    for s in orphans_out_only[: args.top]:
        print(f"  {s}  (in-links: {in_deg[s]})")
    print()
    print(f"=== Never linked TO from any other post ({len(orphans_in_only)}) ===")
    for s in orphans_in_only[: args.top]:
        print(f"  {s}  (out-links: {out_deg[s]})")
    if len(orphans_in_only) > args.top:
        print(f"  ... and {len(orphans_in_only) - args.top} more")
    print()
    print(
        f"=== Best-connected hubs, by outbound content links (top {min(args.top,10)}) ==="
    )
    for s, d in report["top_out_degree"][:10]:
        print(f"  {d:2d}  {s}")
    print()
    print(
        f"=== Best-connected hubs, by inbound content links (top {min(args.top,10)}) ==="
    )
    for s, d in report["top_in_degree"][:10]:
        print(f"  {d:2d}  {s}")
    print()
    print(
        "=== Top candidate link opportunities (shared labels, currently unlinked) ==="
    )
    for c in report["candidate_pairs"][:30]:
        print(
            f"  {c['score']:.3f}  {c['slugs'][0][:45]:47} <-> {c['slugs'][1][:45]:47} [{', '.join(c['shared_labels'])}]"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
