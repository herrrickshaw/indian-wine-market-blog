"""
Rule definitions for this blog's consistency checker.

Ported verbatim from masaladeutsch's blogger_writeups_code: every rule here
came out of a real defect found on that live blog, not a generic
best-practice list, and the checks are blog-agnostic (they operate on post
HTML, not on masaladeutsch-specific content). The comment on each rule
records what it caught there.

Severity:
  BLOCKER  reader sees broken output (invisible text, dead chart)
  MAJOR    wrong or misattributed information
  MINOR    house-style / consistency drift
"""

import re

BLOCKER, MAJOR, MINOR = "BLOCKER", "MAJOR", "MINOR"

_COMMENT_RE = re.compile(r"<!--.*?-->|/\*.*?\*/", re.DOTALL)


def _declares_dark(body):
    """Whether the post actually declares a dark palette -- ignoring the
    strings inside HTML/CSS comments, so a comment that only *discusses*
    prefers-color-scheme (e.g. to explain why it was deliberately left out)
    doesn't trip a false BLOCKER. Caught on quarterly-reportage-4034-scheme,
    whose own explanatory comment contains the literal phrase."""
    stripped = _COMMENT_RE.sub("", body)
    return ("prefers-color-scheme" in stripped) or ('data-theme="dark"' in stripped)


# --------------------------------------------------------------------------
# Static rules: run against the post's rendered HTML (no browser needed).
# Each returns (ok, detail).
# --------------------------------------------------------------------------

DISCLAIMER_RE = re.compile(r"data-gs-ai-disclosure-v1")
SCRIPT_RE = re.compile(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>")
INVERTED_MQ = "@media not all and (prefers-color-scheme"


def _scripts(body):
    return [m.group(1) for m in SCRIPT_RE.finditer(body)]


def _strip_scripts(body):
    return SCRIPT_RE.sub("", body)


def r_disclaimer_not_in_script(body, **kw):
    """Bulk disclaimer insertion spliced <p> into a JS string literal, with a raw
    newline inside a quoted string -> SyntaxError -> every chart on the page died.
    Found on 7 posts."""
    bad = [i + 1 for i, s in enumerate(_scripts(body)) if DISCLAIMER_RE.search(s)]
    return (not bad), (f"disclaimer inside <script> #{bad}" if bad else "")


def r_disclaimer_present(body, **kw):
    """Disclaimer must exist OUTSIDE any script. On the 7 broken posts the only
    copy was the spliced one, so removing it would have left none."""
    ok = bool(DISCLAIMER_RE.search(_strip_scripts(body)))
    return ok, ("" if ok else "no AI-disclosure outside a <script>")


def r_no_inverted_media_query(body, **kw):
    """`@media not all and (prefers-color-scheme:dark)` matches in LIGHT mode.
    Blocks doing this set --text:#e8e8e8 / .artx{color:#ddd} -> near-white text on
    white. On one post 242 of 645 text nodes were invisible."""
    n = body.count(INVERTED_MQ)
    return (n == 0), (f"{n} inverted dark-mode media query block(s)" if n else "")


def r_dark_vars_locked(body, **kw):
    """A post that declares its own dark palette must also pin the light values
    with !important, or the BW-override blackens text while the surface goes dark."""
    declares_dark = _declares_dark(body)
    if not declares_dark:
        return True, "n/a (no dark palette)"
    # only a hazard if something actually consumes the vars
    consumes = bool(
        re.search(
            r"var\(\s*--(surface|surface-alt|border|text|text-mut|ink|page-bg)\b", body
        )
    )
    if not consumes:
        return True, "n/a (vars declared but never consumed)"
    ok = "gs-light-lock-v1" in body or bool(
        re.search(r"--[a-zA-Z0-9-]+\s*:\s*[^;}]+!important", body)
    )
    return ok, ("" if ok else "dark palette consumed with no !important light lock")


def r_translate_widget(body, **kw):
    """Per-post Google Translate embed (site-wide placement is blocked in Blogger)."""
    ok = "google_translate_element" in body
    return ok, ("" if ok else "missing Google Translate widget")


def r_index_link(body, **kw):
    """Every post carries the back-to-index nav link."""
    ok = "topnav-index-link" in body
    return ok, ("" if ok else "missing All-Articles index link")


def r_bw_override(body, **kw):
    """The black-on-white override safety net. Only required where the post
    ships its own colour system that could go dark; a plain post that never
    declares a dark palette does not need it."""
    risky = _declares_dark(body)
    if not risky:
        return True, "n/a (no dark palette)"
    ok = "BW-OVERRIDE" in body or "gs-light-lock-v1" in body
    return ok, ("" if ok else "dark palette present with no BW-override / light lock")


def r_table_units_in_header(body, **kw):
    """Units belong in <th>, not repeated in every <td>. Was polluting 30-91% of
    cells across 7 flagship posts.

    A cell only counts as a violation if it IS a bare number+unit (what a
    header could absorb) -- not merely a cell that mentions a unit inside a
    longer explanatory phrase, e.g. "~25.96% of all technical-textile
    imports" or "FY2025-26, 10.35% CAGR". The original substring-search
    version flagged both alike, which misfired on 18 of 19 posts checked
    during a 2026-09-18 audit: their tables were fine, just descriptive."""
    tds = re.findall(r"<td[^>]*>([\s\S]{0,120}?)</td>", body)
    if len(tds) < 12:
        return True, "n/a (few cells)"
    bare_unit = re.compile(
        r"^\s*(₹|&#8377;|US\$|\$)?\s*[\d,.−-]+\s*"
        r"(cr|crore|lakh|%|MT|LMT|MW|kg|bn|mn)\.?\s*"
        r"(\([^)]{0,40}\))?\s*$",
        re.IGNORECASE,
    )
    hits = sum(1 for t in tds if bare_unit.match(re.sub(r"<[^>]+>", "", t).strip()))
    pct = hits / len(tds) * 100
    ok = pct <= 30
    return ok, (
        f"{pct:.0f}% of {len(tds)} cells carry units" if not ok else f"{pct:.0f}%"
    )


def r_scripts_parse(body, node_check, **kw):
    """Any JS syntax error kills the whole block (charts, tables, stat strips)."""
    if node_check is None:
        return True, "skipped (no node)"
    bad = node_check
    return (not bad), (f"JS syntax error in script(s) {bad}" if bad else "")


def r_wpi_attribution(body, **kw):
    """WPI is published by the Office of the Economic Adviser, DPIIT - not MoSPI
    (which publishes CPI). Several posts had this wrong."""
    mentions_wpi = re.search(r"\bWPI\b|Wholesale Price Index", body)
    if not mentions_wpi:
        return True, "n/a"
    bad = re.search(r"MoSPI[^<.]{0,40}Wholesale Price Index", body) or re.search(
        r"Wholesale Price Index[^<.]{0,50}MoSPI(?!\.)", body
    )
    # allow the explicit corrective phrasing
    if bad and re.search(r"not a MoSPI product|not by MoSPI|published by DPIIT", body):
        bad = None
    return (not bad), ("WPI attributed to MoSPI" if bad else "")


STATIC_RULES = [
    ("js-parses", BLOCKER, r_scripts_parse),
    ("no-disclaimer-in-script", BLOCKER, r_disclaimer_not_in_script),
    ("no-inverted-media-query", BLOCKER, r_no_inverted_media_query),
    ("dark-vars-locked", BLOCKER, r_dark_vars_locked),
    ("wpi-attribution", MAJOR, r_wpi_attribution),
    ("disclaimer-present", MAJOR, r_disclaimer_present),
    ("translate-widget", MINOR, r_translate_widget),
    ("index-link", MINOR, r_index_link),
    ("bw-override", MINOR, r_bw_override),
    ("table-units-in-header", MINOR, r_table_units_in_header),
]

# --------------------------------------------------------------------------
# Rendered rules: need a real browser (computed styles). Run in both colour
# schemes, because the inverted media query only bites in LIGHT mode.
# --------------------------------------------------------------------------

CONTRAST_JS = r"""
() => {
  // A gradient lives in background-IMAGE, not background-color. Walking past it
  // to the page behind reports a correctly-rendered dark hero as white-on-white.
  // So: stop at any element painting a gradient, and use its mean stop colour.
  const gradAvg = bi => {
    const cols = bi.match(/rgba?\([^)]*\)|#[0-9a-f]{3,8}/gi);
    if (!cols || !cols.length) return null;
    const parse = c => { if (c[0] === '#') { let h = c.slice(1);
        if (h.length === 3) h = h.split('').map(x => x + x).join('');
        return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16)]; }
      const m = c.match(/[\d.]+/g); return m ? m.slice(0,3).map(Number) : null; };
    const ps = cols.map(parse).filter(Boolean);
    if (!ps.length) return null;
    const a = [0,1,2].map(i => Math.round(ps.reduce((s,p) => s + p[i], 0) / ps.length));
    return `rgb(${a[0]}, ${a[1]}, ${a[2]})`;
  };
  const eff = el => { let e = el;
    while (e) { const cs = getComputedStyle(e);
      const b = cs.backgroundColor;
      if (b && b !== 'rgba(0, 0, 0, 0)' && b !== 'transparent') {
        const m = b.match(/[\d.]+/g);
        if (!m || m.length < 4 || Number(m[3]) > 0.5) return b;   // skip near-transparent
      }
      const bi = cs.backgroundImage;
      if (bi && bi !== 'none' && /gradient/i.test(bi)) { const g = gradAvg(bi); if (g) return g; }
      e = e.parentElement; }
    return 'rgb(255, 255, 255)'; };
  const lum = c => { const m = c.match(/[\d.]+/g); if (!m) return null;
    const [r,g,b] = m.slice(0,3).map(Number);
    const f = v => { v/=255; return v <= .03928 ? v/12.92 : Math.pow((v+.055)/1.055, 2.4); };
    return .2126*f(r) + .7152*f(g) + .0722*f(b); };
  const ratio = (a,b) => { const [hi,lo] = a > b ? [a,b] : [b,a]; return (hi+.05)/(lo+.05); };
  const out = [];
  const els = [...document.querySelectorAll('.post-body *')].filter(el =>
    (el.textContent||'').trim().length > 3 && el.children.length === 0 && el.offsetParent !== null);
  for (const el of els.slice(0, 1500)) {
    const fg = getComputedStyle(el).color, bg = eff(el);
    const lf = lum(fg), lb = lum(bg);
    if (lf === null || lb === null) continue;
    const r = ratio(lf, lb);
    if (r < 3.0) out.push({tag: el.tagName, ratio: +r.toFixed(2), fg, bg,
                           text: (el.textContent||'').trim().slice(0,40)});
  }
  return {checked: els.length, fails: out.length, worst: out.slice(0, 6)};
}
"""

OVERFLOW_JS = r"""
() => {
  const de = document.documentElement;
  const wide = [...document.querySelectorAll('.post-body *')]
    .filter(el => el.scrollWidth > el.clientWidth + 4 &&
                  getComputedStyle(el).overflowX !== 'auto' &&
                  getComputedStyle(el).overflowX !== 'scroll')
    .slice(0, 5)
    .map(el => el.tagName + '.' + (el.className||'').toString().slice(0,24));
  return {pageOverflow: de.scrollWidth > de.clientWidth + 2,
          docW: de.scrollWidth, viewW: de.clientWidth, wideEls: wide};
}
"""
