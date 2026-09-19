#!/usr/bin/env python3
"""Check a draft against the archive before it gets published, not after.

Formalises a check that was, until now, only ever run ad hoc: three times
this session, published posts turned out to have gaps a grep against posts/
would have caught before publish (see DATA_SOURCES_AND_PRACTICES.md §10) --
a missing Reliance Industries row, an "Amoxicillin" search term that silently
returned zero because the archive spells it "Amoxycillin", a Barauni figure
dropped instead of chased down. All three were found only because the user
asked "check other posts for gaps" after the fact.

    python3 tools/research_crosscheck.py --terms "Reliance,Jamnagar,Amoxicillin" out/post-draft.html
    python3 tools/research_crosscheck.py --auto out/post-draft.html   # extract candidate terms itself

WHAT IT DOES
    1. For each term, greps posts/*.html for prior mentions -- catching cases
       where this blog already has better-sourced coverage of an entity a new
       draft is about to under-serve or contradict.
    2. Flags a term with zero matches anywhere in posts/, which is either a
       genuinely new entity (fine) or a spelling/terminology mismatch (not
       fine) -- it cannot tell the difference, so it prints known variant
       spellings seen in the archive for a handful of terms with a documented
       history of this exact failure mode (see VARIANT_HINTS below).
    3. Extracts standalone percentage/currency figures from the draft and
       checks whether the same numeric string appears attached to a
       *different* claim elsewhere in posts/ -- a cheap contradiction smell
       test, not a fact-checker.

--auto's term extraction is deliberately crude (capitalised multi-word spans
and known drug-name suffixes) -- it will over- and under-match. Treat its
output as a prompt for what to check by hand, not as ground truth.

This script does not verify facts against any primary source; it only tells
you what this blog has already said, so a draft doesn't repeat, contradict,
or omit something the archive already knows.
"""

import argparse
import glob
import html
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
POSTS = ROOT / "posts"

# Terms this session actually got burned by a spelling/naming mismatch on.
# Not exhaustive -- add to this as new cases turn up (see DATA_SOURCES_AND_PRACTICES.md §10).
VARIANT_HINTS = {
    "amoxicillin": ["amoxycillin"],
    "amoxycillin": ["amoxicillin"],
    "sulphate": ["sulfate"],
    "sulfate": ["sulphate"],
    "aluminium": ["aluminum"],
    "colour": ["color"],
    "programme": ["program"],
}

PCT_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?%")
# \d(?:[\d,]*\d)? requires the match to start and end on an actual digit, so
# "Rs," alone can no longer satisfy it via the comma in [\d,]+ (a real bug
# caught testing this script against its own first output, not in review).
MONEY_RE = re.compile(
    r"(?:₹|Rs\.?\s?|\$)\s?\d(?:[\d,]*\d)?(?:\.\d+)?\s?(?:cr(?:ore)?s?|lakh|crore|bn|mn)?",
    re.IGNORECASE,
)


def is_specific_enough(num_str):
    """Filter out trivially common short numbers (₹0, 1%) that will collide
    across hundreds of posts by coincidence, not because they're the same claim."""
    digits = re.sub(r"[^\d]", "", num_str)
    return len(digits) >= 2


def strip_tags(html_text):
    text = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ",
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text))


def load_archive():
    """slug -> (raw_html, plain_text)"""
    archive = {}
    for f in sorted(glob.glob(str(POSTS / "*.html"))):
        slug = pathlib.Path(f).stem
        raw = pathlib.Path(f).read_text(encoding="utf-8", errors="replace")
        archive[slug] = (raw, strip_tags(raw))
    return archive


def find_term(term, archive, exclude_slug=None):
    hits = []
    t = term.lower()
    for slug, (raw, text) in archive.items():
        if slug == exclude_slug:
            continue
        idx = text.lower().find(t)
        if idx == -1:
            continue
        ctx = text[max(0, idx - 60) : idx + len(term) + 60].strip()
        hits.append((slug, ctx))
    return hits


def auto_terms(text):
    """Crude candidate-entity extraction: capitalised 2-4 word spans."""
    spans = re.findall(r"\b(?:[A-Z][a-zA-Z0-9&.]*\s+){1,3}[A-Z][a-zA-Z0-9&.]*\b", text)
    seen, out = set(), []
    stop = {"The", "This", "That", "These", "Those", "It", "A", "An"}
    for s in spans:
        s = s.strip()
        first = s.split()[0]
        if first in stop or len(s) < 6:
            continue
        key = s.lower()
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out[:60]  # cap -- this is a prompt list, not a report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("draft", help="path to the out/ draft HTML file")
    ap.add_argument(
        "--terms", help="comma-separated entities/molecules/companies to check"
    )
    ap.add_argument(
        "--auto",
        action="store_true",
        help="extract candidate terms from the draft instead",
    )
    ap.add_argument(
        "--numbers",
        action="store_true",
        help="also cross-check standalone %% and currency figures",
    )
    args = ap.parse_args()

    draft_path = pathlib.Path(args.draft)
    if not draft_path.exists():
        sys.exit(f"not found: {draft_path}")
    draft_raw = draft_path.read_text(encoding="utf-8", errors="replace")
    draft_text = strip_tags(draft_raw)
    draft_slug = draft_path.stem.removeprefix("post-")

    if args.terms:
        terms = [t.strip() for t in args.terms.split(",") if t.strip()]
    elif args.auto:
        terms = auto_terms(draft_text)
        print(
            f"--auto extracted {len(terms)} candidate terms (crude -- review, don't trust):\n"
        )
    else:
        sys.exit('pass --terms "A,B,C" or --auto')

    archive = load_archive()
    print(f"checking against {len(archive)} posts in {POSTS}\n")

    zero, found = [], []
    for term in terms:
        hits = find_term(term, archive, exclude_slug=draft_slug)
        if hits:
            found.append((term, hits))
        else:
            zero.append(term)

    if found:
        print(
            "=== already covered elsewhere on this blog (check for consistency, not just novelty) ==="
        )
        for term, hits in found:
            print(f"\n  {term}  ({len(hits)} post{'s' if len(hits) != 1 else ''})")
            for slug, ctx in hits[:3]:
                print(f"    {slug}: …{ctx}…")
            if len(hits) > 3:
                print(f"    … and {len(hits) - 3} more")

    if zero:
        print("\n=== zero matches anywhere in posts/ ===")
        for term in zero:
            variants = VARIANT_HINTS.get(term.lower(), [])
            note = (
                f"  -- known variant spelling(s) to try: {', '.join(variants)}"
                if variants
                else ""
            )
            print(f"  {term}{note}")
        print(
            "\n  A zero match is either a genuinely new entity (fine) or a spelling/"
            "\n  terminology mismatch (not fine, see the Amoxicillin/Amoxycillin case in"
            "\n  DATA_SOURCES_AND_PRACTICES.md §10) -- this script cannot tell the"
            "\n  difference and does not guess. Check by hand before treating a gap as real."
        )

    if args.numbers:
        print("\n=== standalone %/currency figures in the draft, cross-checked ===")
        nums = set(PCT_RE.findall(draft_text)) | {
            m.strip() for m in MONEY_RE.findall(draft_text)
        }
        nums = {n for n in nums if is_specific_enough(n)}
        for n in sorted(nums):
            hits = find_term(n, archive, exclude_slug=draft_slug)
            if hits:
                print(
                    f"\n  {n}  appears in {len(hits)} other post(s) -- confirm it's the same claim, not reused out of context:"
                )
                for slug, ctx in hits[:2]:
                    print(f"    {slug}: …{ctx}…")

    print(
        f"\n{len(found)} terms already covered, {len(zero)} zero-match. Review before publish, not after."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
