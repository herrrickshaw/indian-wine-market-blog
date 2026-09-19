#!/usr/bin/env python3
"""Scan posts/*.html for inline SVG charts with likely label collisions or
viewBox overflow -- the "text on top of text" / "graph cut off" bug class.

Approach: parse each <svg viewBox="..."> block's <text> elements (x, y,
text-anchor, font-size, font-weight, content), estimate each one's rendered
bounding box with a per-character width table, and flag:

  - COLLISION: two text elements on (near) the same baseline whose estimated
    x-ranges overlap.
  - OVERFLOW: a text element's estimated x-range extends outside the SVG's
    own viewBox (or width attribute when there's no viewBox), i.e. it will be
    clipped by the SVG viewport.
  - TEXT_HIDDEN_BY_RECT: a <text> whose box overlaps a same-area, opaque
    <rect> that appears LATER in the SVG source -- SVG paints in document
    order, so that rect silently clips whatever part of the label falls
    underneath it. Common bug: a category label written first, then the bar
    rect drawn over the start of it.

This is a heuristic, not a renderer: character-width estimates are close
enough (calibrated against Playwright screenshots of real posts) to be a
useful triage tool, not a guarantee. Always visually confirm a fix with
scripts/render_svg_figure.py before publishing.

Usage:
    python3 tools/svg_chart_lint.py                  # scan all of posts/
    python3 tools/svg_chart_lint.py posts/foo.html    # scan one file
    python3 tools/svg_chart_lint.py --json out.json   # machine-readable report
"""

import argparse
import glob
import json
import re
import sys

TEXT_RE = re.compile(r"<text\b([^>]*)>(.*?)</text>", re.DOTALL)
TSPAN_RE = re.compile(r"<tspan\b([^>]*)>(.*?)</tspan>", re.DOTALL)
ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
SVG_RE = re.compile(r"<svg\b([^>]*)>(.*?)</svg>", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
RECT_RE = re.compile(r"<rect\b([^>]*?)/?>", re.DOTALL)

# Per-character width as a fraction of font-size, roughly calibrated for the
# Georgia/system-sans mixes this blog uses. Digits and most lowercase letters
# cluster tightly; a flat-ish table beats a single multiplier because these
# charts are dense with digits, currency symbols and punctuation.
CHAR_W = {
    **{c: 0.50 for c in "0123456789"},
    **{c: 0.28 for c in "ilIj.,:;'|!"},
    **{c: 0.62 for c in "mMW"},
    **{c: 0.40 for c in " "},
}
DEFAULT_W = 0.52
BOLD_BUMP = 1.08  # font-weight >= 600 renders a touch wider


def text_width(s, font_size, weight_bold):
    s = re.sub(r"&#\d+;|&#x[0-9a-fA-F]+;|&\w+;", "X", s)  # entities ~1 glyph each
    w = sum(CHAR_W.get(ch, DEFAULT_W) for ch in s) * font_size
    return w * BOLD_BUMP if weight_bold else w


def parse_attrs(attr_str):
    return dict(ATTR_RE.findall(attr_str))


CSS_RULE_RE = re.compile(r"\.([\w-]+)\s*\{([^}]*)\}")
STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.DOTALL)
STYLE_PROP_RE = re.compile(r"([\w-]+)\s*:\s*([^;]+)")


def parse_css_classes(html):
    """Collect .classname{prop:value;...} rules from every <style> block in
    the file -- charts commonly set font-size/text-anchor/font-weight this
    way via class="sb-s" etc. rather than as XML attributes, which a linter
    that only reads tag attributes would otherwise miss entirely."""
    classes = {}
    for sm in STYLE_BLOCK_RE.finditer(html):
        for cm in CSS_RULE_RE.finditer(sm.group(1)):
            name, body = cm.group(1), cm.group(2)
            props = dict(STYLE_PROP_RE.findall(body))
            classes.setdefault(name, {}).update(
                {k: v.strip() for k, v in props.items()}
            )
    return classes


def resolve_style(attrs, css_classes):
    """Merge (lowest to highest priority): SVG <style> .class rules, inline
    style="...", then direct XML attributes -- into one flat style dict."""
    resolved = {}
    for cls in attrs.get("class", "").split():
        resolved.update(css_classes.get(cls, {}))
    if "style" in attrs:
        resolved.update(
            {k: v.strip() for k, v in STYLE_PROP_RE.findall(attrs["style"])}
        )
    for key in ("font-size", "text-anchor", "font-weight", "fill"):
        if key in attrs:
            resolved[key] = attrs[key]
    return resolved


def _one_line_bbox(x, y, anchor, fs, bold, plain):
    w = text_width(plain, fs, bold)
    if anchor == "end":
        x0, x1 = x - w, x
    elif anchor == "middle":
        x0, x1 = x - w / 2, x + w / 2
    else:
        x0, x1 = x, x + w
    return x0, x1, y, fs, plain


def text_lines(attrs, content, css_classes=None, font_size_default=11):
    """Return a list of (x0, x1, y, font_size, plain_text) -- one per line.
    A <text> with <tspan> children (the multi-line-wrap pattern) yields one
    entry per tspan, using each tspan's own x/dy if given, else the parent's;
    a plain <text> yields a single entry."""
    style = resolve_style(attrs, css_classes or {})
    x = float(attrs.get("x", 0))
    y = float(attrs.get("y", 0))
    fs = float(style.get("font-size", f"{font_size_default}px").rstrip("px"))
    weight = style.get("font-weight", "400")
    bold = weight in ("600", "700", "bold", "800", "900")
    anchor = style.get("text-anchor", "start")

    tspans = list(TSPAN_RE.finditer(content))
    if not tspans:
        plain = TAG_RE.sub("", content).strip()
        if not plain:
            return []
        return [_one_line_bbox(x, y, anchor, fs, bold, plain)]

    # A tspan with an explicit x is a genuine new line (the line-wrap pattern
    # this codebase's own wrap_svg_text produces). A tspan with no x is
    # inline run-styling -- bold/colored words inside one running line -- and
    # must be measured as part of that line's total width, not its own line,
    # or two run-styled words on the same sentence look like a "collision."
    segments = []  # list of (start, end, is_tspan, attrs_or_None)
    prev_end = 0
    for tm in tspans:
        if tm.start() > prev_end:
            segments.append((prev_end, tm.start(), False, None))
        segments.append((tm.start(), tm.end(), True, tm))
        prev_end = tm.end()
    if prev_end < len(content):
        segments.append((prev_end, len(content), False, None))

    lines = []  # each: {'x': , 'y': , 'parts': [(text, bold)]}
    cur = None
    cur_y = y
    for seg_start, seg_end, is_tspan, tm in segments:
        if is_tspan:
            t_attrs = parse_attrs(tm.group(1))
            t_bold = bold
            if "font-weight" in t_attrs:
                t_bold = t_attrs["font-weight"] in ("600", "700", "bold", "800", "900")
            if "dy" in t_attrs:
                cur_y += float(t_attrs["dy"])
            elif "y" in t_attrs:
                cur_y = float(t_attrs["y"])
            if "x" in t_attrs or cur is None:
                t_x = float(t_attrs["x"]) if "x" in t_attrs else x
                cur = {"x": t_x, "y": cur_y, "parts": []}
                lines.append(cur)
            text = TAG_RE.sub("", tm.group(2))
            cur["parts"].append((text, t_bold))
        else:
            text = content[seg_start:seg_end]
            if not text.strip():
                continue
            if cur is None:
                cur = {"x": x, "y": cur_y, "parts": []}
                lines.append(cur)
            cur["parts"].append((text, bold))

    out = []
    for line in lines:
        full_text = "".join(t for t, _ in line["parts"]).strip()
        if not full_text:
            continue
        w = sum(text_width(t, fs, b) for t, b in line["parts"])
        lx = line["x"]
        if anchor == "end":
            x0, x1 = lx - w, lx
        elif anchor == "middle":
            x0, x1 = lx - w / 2, lx + w / 2
        else:
            x0, x1 = lx, lx + w
        out.append((x0, x1, line["y"], fs, full_text))
    return out


def _opacity_of(attrs):
    """Combined opacity from opacity/fill-opacity, as attrs or inline style."""
    style = {}
    if "style" in attrs:
        style.update({k: v.strip() for k, v in STYLE_PROP_RE.findall(attrs["style"])})
    vals = []
    for key in ("opacity", "fill-opacity"):
        v = style.get(key, attrs.get(key))
        if v is None:
            continue
        try:
            vals.append(float(v))
        except ValueError:
            pass
    result = 1.0
    for v in vals:
        result *= v
    return result


def find_rects(inner):
    """Plain axis-aligned <rect x y width height>, skipping any with a
    transform (rotated/skewed bars aren't worth the geometry) or a fill of
    none/transparent (can't hide anything underneath)."""
    rects = []
    for rm in RECT_RE.finditer(inner):
        attrs = parse_attrs(rm.group(1))
        if not all(k in attrs for k in ("x", "y", "width", "height")):
            continue
        if "transform" in attrs:
            continue
        fill = attrs.get("fill", "")
        if fill in ("none", "transparent", ""):
            continue
        try:
            rx = float(attrs["x"])
            ry = float(attrs["y"])
            rw = float(attrs["width"])
            rh = float(attrs["height"])
        except ValueError:
            continue
        if rw <= 0 or rh <= 0:
            continue
        rects.append((rx, ry, rw, rh, _opacity_of(attrs), rm.start()))
    return rects


def find_charts(html):
    for m in SVG_RE.finditer(html):
        svg_attrs = parse_attrs(m.group(1))
        vb = svg_attrs.get("viewBox", "")
        parts = vb.split()
        if len(parts) == 4:
            vb_x0, vb_y0, vb_w, vb_h = map(float, parts)
        else:
            vb_x0, vb_y0 = 0.0, 0.0
            vb_w = float(svg_attrs.get("width", 700) or 700)
            vb_h = float(svg_attrs.get("height", 400) or 400)
        yield m.start(), vb_x0, vb_x0 + vb_w, vb_y0, vb_y0 + vb_h, m.group(2)


def lint_svg(body_offset, vb_x0, vb_x1, vb_y0, vb_y1, inner, css_classes=None):
    issues = []
    texts = []
    for tm in TEXT_RE.finditer(inner):
        attrs = parse_attrs(tm.group(1))
        if "x" not in attrs or "y" not in attrs:
            continue
        if "rotate" in attrs.get("transform", ""):
            # Rotated text (angled tick labels, vertical axis titles) doesn't
            # occupy a horizontal bbox the way this linter assumes -- skip
            # it rather than produce a false collision/overflow report.
            continue
        for x0, x1, y, fs, plain in text_lines(attrs, tm.group(2), css_classes):
            texts.append((x0, x1, y, fs, plain, tm.start()))

    # overflow: bbox outside the declared canvas (with a tiny 1px tolerance)
    for x0, x1, y, fs, plain, pos in texts:
        if x0 < vb_x0 - 1:
            issues.append(
                {
                    "type": "OVERFLOW_LEFT",
                    "text": plain,
                    "x0": round(x0, 1),
                    "canvas_x0": vb_x0,
                }
            )
        if x1 > vb_x1 + 1:
            issues.append(
                {
                    "type": "OVERFLOW_RIGHT",
                    "text": plain,
                    "x1": round(x1, 1),
                    "canvas_x1": vb_x1,
                    "overflow_px": round(x1 - vb_x1, 1),
                }
            )

    # collision: same-baseline (within half a line-height) x-range overlap
    texts_sorted = sorted(texts, key=lambda t: t[2])
    for i in range(len(texts_sorted)):
        x0a, x1a, ya, fsa, pa, _ = texts_sorted[i]
        for j in range(i + 1, len(texts_sorted)):
            x0b, x1b, yb, fsb, pb, _ = texts_sorted[j]
            if yb - ya > max(fsa, fsb) * 0.6:
                break  # sorted by y; no further candidates this close
            if x0a < x1b and x0b < x1a:
                overlap = min(x1a, x1b) - max(x0a, x0b)
                if overlap > 1:
                    issues.append(
                        {
                            "type": "COLLISION",
                            "text_a": pa,
                            "text_b": pb,
                            "overlap_px": round(overlap, 1),
                            "y": round(ya, 1),
                        }
                    )

    # text hidden by rect: an opaque <rect> that appears LATER in the SVG
    # source than a <text> paints on top of it in SVG's document-order
    # painting model -- if their boxes overlap, the rect silently clips
    # whatever part of the label falls underneath (a real, common bug in
    # hand-authored bar charts: category label written first, then the bar
    # rect drawn over the start of it). A translucent rect (opacity < 0.85)
    # lets the text show through and isn't flagged.
    rects = find_rects(inner)
    for x0, x1, y, fs, plain, pos in texts:
        ty0, ty1 = y - fs * 0.85, y + fs * 0.25  # rough baseline-relative text bbox
        for rx, ry, rw, rh, opacity, rpos in rects:
            if rpos <= pos or opacity < 0.85:
                continue
            rx0, rx1, ry0, ry1 = rx, rx + rw, ry, ry + rh
            if x0 < rx1 and rx0 < x1 and ty0 < ry1 and ry0 < ty1:
                x_overlap = min(x1, rx1) - max(x0, rx0)
                if x_overlap > 3:
                    issues.append(
                        {
                            "type": "TEXT_HIDDEN_BY_RECT",
                            "text": plain,
                            "overlap_px": round(x_overlap, 1),
                            "y": round(y, 1),
                        }
                    )
    return issues


# Named-entity part is restricted to a whitelist, not any \w+; -- a real
# ampersand can legitimately be followed by a word and then an unrelated
# semicolon (e.g. "DGCI&amp;S; chapter mapping..."), which is not a
# double-escaping bug and must not be flagged as one.
_KNOWN_ENTITY_NAMES = "mdash|ndash|minus|nbsp|hellip|rsquo|lsquo|ldquo|rdquo|amp|copy|reg|trade|times|middot|bull|deg"
DOUBLE_ESCAPE_RE = re.compile(
    r"&amp;(#\d+|#x[0-9a-fA-F]+|(?:" + _KNOWN_ENTITY_NAMES + r"));"
)


def find_double_escaped_entities(html):
    """A named/numeric entity that itself got HTML-escaped renders as the
    literal text "&amp;mdash;" etc. instead of the intended character --
    seen repeatedly across hand-authored chart captions (&amp;mdash;,
    &amp;ndash;, &amp;minus;). Not a layout bug, but a real on-page
    correctness/readability defect the geometry checks above can't see."""
    seen = []
    for m in DOUBLE_ESCAPE_RE.finditer(html):
        seen.append(m.group(0))
    return seen


def lint_file(path):
    with open(path, encoding="utf-8") as f:
        html = f.read()
    css_classes = parse_css_classes(html)
    results = []
    for _, vb_x0, vb_x1, vb_y0, vb_y1, inner in find_charts(html):
        issues = lint_svg(0, vb_x0, vb_x1, vb_y0, vb_y1, inner, css_classes)
        if issues:
            results.append(
                {
                    "viewBox": [vb_x0, vb_y0, vb_x1 - vb_x0, vb_y1 - vb_y0],
                    "issues": issues,
                }
            )
    bad_entities = find_double_escaped_entities(html)
    if bad_entities:
        from collections import Counter

        counts = Counter(bad_entities)
        results.append(
            {
                "viewBox": None,
                "issues": [
                    {"type": "DOUBLE_ESCAPED_ENTITY", "text": ent, "count": n}
                    for ent, n in counts.items()
                ],
            }
        )
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "files", nargs="*", help="specific post(s); default: all posts/*.html"
    )
    ap.add_argument("--json", help="write machine-readable report to this path")
    ap.add_argument(
        "--min-overflow",
        type=float,
        default=2.0,
        help="ignore OVERFLOW_RIGHT/LEFT smaller than this many px (default 2)",
    )
    args = ap.parse_args()

    files = args.files or sorted(glob.glob("posts/*.html"))
    report = {}
    total_issues = 0
    for f in files:
        # one malformed post must not abort the whole scan
        try:
            charts = lint_file(f)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {f}: parse error {e}", file=sys.stderr)
            continue
        kept = []
        for c in charts:
            issues = [
                i
                for i in c["issues"]
                if i["type"] not in ("OVERFLOW_LEFT", "OVERFLOW_RIGHT")
                or i.get("overflow_px", 999) >= args.min_overflow
            ]
            if issues:
                kept.append({**c, "issues": issues})
        if kept:
            report[f] = kept
            total_issues += sum(len(c["issues"]) for c in kept)

    for f, charts in report.items():
        print(f"=== {f}")
        for c in charts:
            for i in c["issues"]:
                if i["type"] == "COLLISION":
                    print(
                        f'  COLLISION  "{i["text_a"]}"  overlaps  "{i["text_b"]}"  ({i["overlap_px"]}px, y={i["y"]})'
                    )
                elif i["type"] == "DOUBLE_ESCAPED_ENTITY":
                    print(
                        f'  DOUBLE-ESCAPED-ENTITY  "{i["text"]}"  renders as literal text, x{i["count"]}'
                    )
                elif i["type"] == "TEXT_HIDDEN_BY_RECT":
                    print(
                        f'  TEXT-HIDDEN-BY-RECT  "{i["text"]}"  clipped by a later opaque <rect> ({i["overlap_px"]}px, y={i["y"]})'
                    )
                else:
                    side = "right" if i["type"] == "OVERFLOW_RIGHT" else "left"
                    print(
                        f'  OVERFLOW-{side.upper():<5} "{i["text"]}"  by {i.get("overflow_px", "?")}px past canvas edge'
                    )

    print(
        f"\n{len(report)} file(s) with issues, {total_issues} issue(s) total, out of {len(files)} scanned."
    )
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
