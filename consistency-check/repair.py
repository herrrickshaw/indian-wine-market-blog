"""Pure body-repair functions for blog posts: string in, string out, no I/O.

Split out of fix.py (which also fetches live posts, serves pages and drives a
headless browser). The only external inputs -- the AI-disclosure paragraph and
the live theme CSS -- are passed in, so everything here is unit-testable
offline.
"""

from __future__ import annotations

import re

from checks import DISCLAIMER_RE, INVERTED_MQ, SCRIPT_RE, STATIC_RULES


def body_of(html):
    s = html.find("<div class='post-body'>")
    if s < 0:
        s = html.find('<div class="post-body"')
    if s < 0:
        return ""
    tail = html[s:]
    depth = 0
    for m in re.finditer(r"<div\b|</div>", tail):
        if m.group(0) == "</div>":
            depth -= 1
            if depth == 0:
                return tail[: m.end()]
        else:
            depth += 1
    return tail


def strip_inverted(src):
    """Remove every inverted media-query block, matching braces properly."""
    n = 0
    while True:
        i = src.find(INVERTED_MQ)
        if i < 0:
            break
        o = src.find("{", i)
        if o < 0:
            break
        depth, j = 0, o
        while j < len(src):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        src = (
            src[:i]
            + "/* gs-fix: removed inverted dark-mode block (it matched in LIGHT mode) */"
            + src[j:]
        )
        n += 1
        if n > 8:
            break
    return src, n


def unsplice_disclaimer(src):
    """Pull an AI-disclosure out of any <script> string literal.

    The splice always landed immediately before  \\n</div>';  so removing the
    <p>...</p> and the newline restores  + var + '</div>';
    """
    pat = re.compile(
        r'<p class="disclaimer" data-gs-ai-disclosure-v1="1"[^>]*>[\s\S]*?</p>\s*\n(?=</div>\';)'
    )
    out, n = pat.subn("", src)
    if n == 0:  # fall back: any disclaimer inside a script at all

        def scrub(m):
            nonlocal n
            inner = m.group(1)
            if DISCLAIMER_RE.search(inner):
                new = re.sub(r'<p class="disclaimer"[\s\S]*?</p>', "", inner)
                n += 1
                return m.group(0).replace(inner, new)
            return m.group(0)

        out = SCRIPT_RE.sub(scrub, out)
    return out, n


def light_lock(src):
    """Build a lock from the post's OWN light palette. Returns '' if none found."""
    if "gs-light-lock-v1" in src:
        return ""
    m = re.search(r':root\[data-theme="light"\]\s*\{([^}]*)\}', src) or re.search(
        r":root\s*\{([^}]*)\}", src
    )
    if not m:
        return ""
    decls = re.findall(r"(--[a-zA-Z0-9-]+)\s*:\s*([^;}]+)", m.group(1))
    if not decls:
        return ""
    css = "".join(f"{k}:{v.strip()}!important;" for k, v in decls)
    return (
        "\n<style>/*gs-light-lock-v1*/"
        ':root,:root[data-theme="dark"],:root[data-theme="light"]{'
        + css
        + "color-scheme:light!important;}</style>"
    )


def _lum(c):
    """Relative luminance of a #hex / rgb() colour, or None."""
    c = c.strip()
    if c.startswith("#"):
        h = c[1:]
        if len(h) == 3:
            h = "".join(x * 2 for x in h)
        if len(h) < 6:
            return None
        try:
            r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None
    else:
        m = re.match(r"rgba?\(([^)]*)\)", c)
        if not m:
            return None
        parts = [p.strip() for p in m.group(1).replace("/", ",").split(",")]
        try:
            r, g, b = (float(parts[i]) for i in range(3))
        except (ValueError, IndexError):
            return None
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


# Selectors that are never a "panel", however dark their declared background is.
# A panel is a bounded box inside the post. These are the document, the post
# root, or a form control — forcing white text on them whitens everything they
# contain, and the light-lock has already forced their background light, so the
# result is white-on-white across the whole page. This is exactly what happened
# to the mineral-oil post: a `body{background:#0b1220}` rule in the post CSS
# produced `body,body *{color:#fff!important}` and hid 819 of 1,403 text nodes.
_NEVER_PANEL = {
    "body",
    "html",
    ":root",
    "*",
    "select",
    "input",
    "textarea",
    "option",
    ".post-body",
    ".artx",
    ".post",
    "article",
    "main",
}


def _is_panel(sel):
    s = sel.strip().lower().rstrip(">+~ ").strip()
    if s in _NEVER_PANEL:
        return False
    # a bare element selector (body, main, article) is document furniture;
    # a class/id/descendant selector is a real panel.
    return any(c in s for c in ".#[:") or " " in s


def dark_panel_guard(src, theme_css_fn=lambda: ""):
    """Keep intentionally-dark panels, but stop the BW-override painting black
    text on them.

    The site-wide BW-override sets `.artx p, .artx div, ... {color:#000!important}`.
    On a panel that is dark by design (a brown hero, a #182430 stat card) that is
    black-on-dark. Whitening the panel would destroy the design, so instead we
    re-assert light text *on those selectors only*, after the override.
    """
    css = "\n".join(re.findall(r"<style[^>]*>([\s\S]*?)</style>", src))
    if not css:
        return ""
    # resolve :root custom properties so var(--x) backgrounds can be judged
    # Custom properties can be declared on any selector, not just :root - and some
    # (e.g. the hero gradient's --h1/--h2) live in the THEME, not the post, so an
    # unresolvable gradient would otherwise be silently skipped.
    rootvars = {}
    for source in (css, theme_css_fn()):
        for m in re.finditer(r"([^{}]+)\{([^}]*)\}", source):
            for k, v in re.findall(r"(--[a-zA-Z0-9-]+)\s*:\s*([^;}]+)", m.group(2)):
                rootvars.setdefault(k, v.strip())

    def resolve(val, depth=0):
        if depth > 3:
            return val
        m = re.search(r"var\(\s*(--[a-zA-Z0-9-]+)", val)
        if m and m.group(1) in rootvars:
            return resolve(
                val.replace(m.group(0) + ")", rootvars[m.group(1)]), depth + 1
            )
        return val

    dark_sels = []
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        sel, decl = m.group(1).strip(), m.group(2)
        if not sel or sel.startswith("@") or ":root" in sel:
            continue
        bg = re.search(r"background(?:-color|-image)?\s*:\s*([^;]+)", decl)
        if not bg:
            continue
        val = resolve(bg.group(1))
        cols = re.findall(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)", val)
        lums = [l for l in (_lum(c) for c in cols) if l is not None]
        if not lums:
            continue
        if sum(lums) / len(lums) < 0.42:  # dark panel by design
            for s in sel.split(","):
                s = s.strip()
                if not s or len(s) >= 80:
                    continue
                if not _is_panel(s):
                    continue
                # Emit the RESOLVED colour, never the raw `var(--panel)`:
                # the light-lock redefines those custom properties to the light
                # palette, so a var() reference here resolves light while the
                # companion rule forces white text — white-on-light by
                # construction. This is what broke the mineral-oil post.
                dark_sels.append((s, val.strip()))
    seen, uniq = set(), []
    for s, v in dark_sels:
        if s not in seen:
            seen.add(s)
            uniq.append((s, v))
    if not uniq:
        return ""
    parts = []
    for s, orig in uniq:
        # re-assert the panel's OWN background so the light-lock cannot whiten it,
        # then put light text on it. Both !important, both after the override.
        parts.append(f"{s}{{background:{orig}!important}}")
        parts.append(f"{s},{s} *{{color:#fff!important}}")
        parts.append(f"{s} a,{s} a *{{color:#cfe3ff!important}}")
    return "\n<style>/*gs-dark-panel-guard*/" + "".join(parts) + "</style>"


TRANSLATE = (
    '<div id="google_translate_element" style="margin:8px 0 16px;"></div>'
    '<script type="text/javascript">function googleTranslateElementInit(){'
    'new google.translate.TranslateElement({pageLanguage:"en",'
    "layout:google.translate.TranslateElement.InlineLayout.SIMPLE,"
    'autoDisplay:false},"google_translate_element");}</script>'
    '<script type="text/javascript" '
    'src="https://translate.google.com/translate_a/element.js'
    '?cb=googleTranslateElementInit"></script>'
)


def add_translate(src):
    """Insert the per-post Google Translate embed.

    Site-wide placement is blocked in Blogger, so each post carries its own.
    It goes after the opening wrapper div, not at position 0, so it lands inside
    the article container and inherits its width.
    """
    if "google_translate_element" in src:
        return src, False
    m = re.search(
        r'<div[^>]*class="[^"]*\b(?:artx|fertx|ecx|mapx|idx|wrap|bulletin)\b[^"]*"[^>]*>',
        src,
    )
    if m:
        return src[: m.end()] + "\n" + TRANSLATE + src[m.end() :], True
    m = re.search(r"<div class='post-body'>", src)
    if m:
        return src[: m.end()] + "\n" + TRANSLATE + src[m.end() :], True
    return TRANSLATE + "\n" + src, True


def repair(body, disclaimer, theme_css_fn=lambda: ""):
    notes = []
    out, n_inv = strip_inverted(body)
    if n_inv:
        notes.append(f"removed {n_inv} inverted media-query block(s)")
    out, n_spl = unsplice_disclaimer(out)
    if n_spl:
        notes.append(f"lifted {n_spl} disclaimer(s) out of <script>")
    lock = light_lock(out)
    if lock:
        out += lock
        notes.append("appended light-lock from the post's own :root")
    # must come AFTER the light-lock and after the BW-override so it wins the cascade
    guard = dark_panel_guard(out, theme_css_fn)
    if guard and "gs-dark-panel-guard" not in out:
        out += guard
        notes.append(
            f"dark-panel guard ({guard.count(',') // 2 + 1} selectors kept dark)"
        )
    out, added_tr = add_translate(out)
    if added_tr:
        notes.append("added Google Translate widget")
    no_script = SCRIPT_RE.sub("", out)
    if not DISCLAIMER_RE.search(no_script):
        out += "\n" + disclaimer
        notes.append("appended AI disclosure at end of body")
    return out, notes


def static_pass(body):
    """Return list of failing rule names."""
    errs = []
    fails = []
    for name, sev, fn in STATIC_RULES:
        try:
            ok, _ = fn(body=body, node_check=errs)
        # a crashing rule counts as a fail, not a crash
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            fails.append(name)
    return fails


# ---------------------------------------------------------------- verification
