"""Pure post-versioning logic: design-marker tracking and semver classification.

Split out of snapshot.py so it can be imported (and tested) without the Blogger
API client, which needs environment configuration at import time.
"""

from __future__ import annotations

import html
import re

# Design/template markers this blog's house style depends on. Tracked per post so
# a regression (a lost light-lock, a re-spliced disclaimer) shows up in the diff
# even when the prose is untouched.
MARKERS = {
    "light_lock": lambda b: "gs-light-lock" in b,
    "dark_panel_guard": lambda b: "gs-dark-panel-guard" in b,
    "typo_unify": lambda b: "gs-typo-unify" in b,
    "translate_widget": lambda b: "google_translate_element" in b,
    "index_link": lambda b: "article-index-start-here" in b,
    "ai_disclosure": lambda b: "gs-ai-disclosure" in b,
    "revision_line": lambda b: "gs-revision-v1" in b,
    "inverted_media_query": lambda b: bool(
        re.search(r"not all and \(prefers-color-scheme", b)
    ),
    "disclaimer_in_script": lambda b: any(
        "AI Disclosure" in s
        for s in re.findall(r"<script\b[^>]*>([\s\S]*?)</script>", b, re.IGNORECASE)
    ),
}


def markers(body: str) -> dict[str, bool]:
    """Evaluate every design marker against a post body (bool per marker)."""
    return {k: fn(body) for k, fn in MARKERS.items()}


def marker_delta(old: dict, rec: dict) -> dict[str, list]:
    """Markers whose value differs between a manifest entry and a new record: {name: [old, new]}."""
    return {
        k: [old.get("markers", {}).get(k), rec["markers"][k]]
        for k in rec["markers"]
        if old.get("markers", {}).get(k) != rec["markers"][k]
    }


# ---------------------------------------------------------------- versioning
#
# Semver (semver.org) applied to prose. The "public API" of a post is what a
# reader could act on — its figures and its claims:
#
#   MAJOR  a figure or a conclusion changed. Someone who acted on the previous
#          version could now be wrong. This is the incompatible change.
#   MINOR  material new content, backward compatible — a new section, table or
#          chart. Nothing previously stated stopped being true.
#   PATCH  nothing the reader relies on moved: styling, markup, a design fix, a
#          typo, a reworded sentence.
#
# Existing posts start at 1.0.0, not 0.1.0: they are published and stable, and
# semver reserves 0.y.z for "anything MAY change at any time".
FIRST_VERSION = "1.0.0"

_DROP = re.compile(r"<(script|style)\b[^>]*>[\s\S]*?</\1>", re.IGNORECASE)
# The revision line and the revision-history block are *about* the version, so
# they must not feed back into classifying it — their dates and version numbers
# would otherwise read as "figures changed" and force a spurious MAJOR bump.
_REVLINE = re.compile(r"<p\b[^>]*data-gs-revision-v1[^>]*>[\s\S]*?</p>", re.IGNORECASE)
_REVBLOCK = re.compile(
    r"<div\b[^>]*id=[\"']changelog[\"'][^>]*>[\s\S]*?</div>", re.IGNORECASE
)
_TAG = re.compile(r"<[^>]+>")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_HEAD = re.compile(r"<h[1-4][^>]*>([\s\S]*?)</h[1-4]>", re.IGNORECASE)


def visible_text(html_str: str) -> str:
    """Reader-visible text of a post (scripts, styles and revision blocks removed), whitespace-collapsed."""
    s = _REVLINE.sub(" ", html_str)
    s = _REVBLOCK.sub(" ", s)
    s = _DROP.sub(" ", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def headings(html_str: str) -> list[str]:
    """Plain text of every h1-h4 in a post, excluding the revision-history block."""
    html_str = _REVBLOCK.sub(" ", html_str)
    return [
        re.sub(r"\s+", " ", _TAG.sub("", h)).strip() for h in _HEAD.findall(html_str)
    ]


def classify(old_body: str | None, new_body: str) -> tuple[str | None, str]:
    """Return (level, reason). Heuristic — override with --as when it is wrong."""
    if old_body is None:
        return None, "no previous body on disk"
    if old_body == new_body:
        # posts/ is the PREVIOUS snapshot. If it already matches what the feed
        # just returned while the manifest says the bytes moved, the file was
        # edited in place and then published — so this diff is the new body
        # against itself and would score "nothing changed" for any edit at all.
        return None, (
            "posts/ already held the published body — edited in place, "
            "so no diff was possible; set the level with --as"
        )
    ot, nt = visible_text(old_body), visible_text(new_body)
    if ot == nt:
        return "patch", "visible text identical; only markup/styling changed"

    on, nn = sorted(_NUM.findall(ot)), sorted(_NUM.findall(nt))
    if on != nn:
        added, removed = len(set(nn) - set(on)), len(set(on) - set(nn))
        return "major", f"figures changed (+{added}/-{removed} distinct numbers)"

    oh, nh = headings(old_body), headings(new_body)
    gone = [h for h in oh if h not in nh]
    if gone:
        return "major", f"section(s) removed: {', '.join(gone[:3])}"

    fresh = [h for h in nh if h not in oh]
    if fresh:
        return "minor", f"section(s) added: {', '.join(fresh[:3])}"
    if len(nt) > len(ot) * 1.02:
        return "minor", f"prose grew {len(ot):,}→{len(nt):,} chars with no new figures"
    return "patch", "wording changed; no figures, sections or claims moved"


def bump(version: str, level: str) -> str:
    """Apply a semver bump ('major'|'minor'|else patch) to 'X.Y.Z'."""
    major, minor, patch = (int(x) for x in version.split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"
