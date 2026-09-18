#!/usr/bin/env python3
"""
Generate corrected post bodies for the defects audit.py finds, and prove the
fix works *before* it touches the live site.

For each post it:
  1. pulls the live post body,
  2. removes inverted `@media not all and (prefers-color-scheme:dark)` blocks
     (balanced-brace, so nested rules are handled),
  3. lifts any AI-disclosure spliced inside a <script> back out to the end,
  4. appends a light-lock derived from *that post's own* :root (never a generic
     palette — the design must not change),
  5. re-runs the static rules on the result,
  6. renders before/after in a headless browser against a local server and
     reports the contrast delta.

Nothing is uploaded. Output goes to fixes/<id>.html for pasting into Blogger.

Usage:
  python3 fix.py --urls-file out/broken_urls.txt
  python3 fix.py --url <post-url> --verify
"""
import argparse, hashlib, http.server, json, os, re, socketserver, subprocess, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from checks import STATIC_RULES, SCRIPT_RE, DISCLAIMER_RE, INVERTED_MQ, CONTRAST_JS

HERE = os.path.dirname(os.path.abspath(__file__))
FIXES = os.path.join(HERE, "fixes")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# BLOG_URL comes from blogger_api's env-var config (WINE_BLOG_URL); see blogger_api.py.
try:
    import blogger_api as _B
    _BLOG_URL = _B.BLOG_URL
except SystemExit:
    _BLOG_URL = None
_BLOG_URL = _BLOG_URL or os.environ.get("WINE_BLOG_URL") or "https://REPLACE-ME.blogspot.com"

DISCLAIMER = (
    '<p class="disclaimer" data-gs-ai-disclosure-v1="1" '
    'style="font-size:.82rem;color:#555;margin-top:14px">'
    '<strong>About this article:</strong> Researched, written and edited by '
    'Umashankar Triplicane Dwarakanathan, with AI research assistance; every figure '
    'is meant to trace to the primary source cited. See the '
    f'<a href="{_BLOG_URL}/p/disclaimer.html">Editorial Policy</a> '
    'for how sourcing, AI use and corrections work.</p>')


def sh(c):
    return subprocess.run(c, capture_output=True, text=True).stdout


def body_of(html):
    s = html.find("<div class='post-body'>")
    if s < 0:
        s = html.find('<div class="post-body"')
    if s < 0:
        return ""
    tail = html[s:]
    depth = 0
    for m in re.finditer(r'<div\b|</div>', tail):
        if m.group(0) == '</div>':
            depth -= 1
            if depth == 0:
                return tail[:m.end()]
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
        o = src.find('{', i)
        if o < 0:
            break
        depth, j = 0, o
        while j < len(src):
            if src[j] == '{':
                depth += 1
            elif src[j] == '}':
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        src = (src[:i]
               + '/* gs-fix: removed inverted dark-mode block (it matched in LIGHT mode) */'
               + src[j:])
        n += 1
        if n > 8:
            break
    return src, n


def unsplice_disclaimer(src):
    """Pull an AI-disclosure out of any <script> string literal.

    The splice always landed immediately before  \\n</div>';  so removing the
    <p>...</p> and the newline restores  + var + '</div>';
    """
    pat = re.compile(r'<p class="disclaimer" data-gs-ai-disclosure-v1="1"[^>]*>[\s\S]*?</p>\s*\n(?=</div>\';)')
    out, n = pat.subn('', src)
    if n == 0:  # fall back: any disclaimer inside a script at all
        def scrub(m):
            nonlocal n
            inner = m.group(1)
            if DISCLAIMER_RE.search(inner):
                new = re.sub(r'<p class="disclaimer"[\s\S]*?</p>', '', inner)
                n += 1
                return m.group(0).replace(inner, new)
            return m.group(0)
        out = SCRIPT_RE.sub(scrub, out)
    return out, n


def light_lock(src):
    """Build a lock from the post's OWN light palette. Returns '' if none found."""
    if 'gs-light-lock-v1' in src:
        return ''
    m = re.search(r':root\[data-theme="light"\]\s*\{([^}]*)\}', src) or \
        re.search(r':root\s*\{([^}]*)\}', src)
    if not m:
        return ''
    decls = re.findall(r'(--[a-zA-Z0-9-]+)\s*:\s*([^;}]+)', m.group(1))
    if not decls:
        return ''
    css = ''.join(f'{k}:{v.strip()}!important;' for k, v in decls)
    return ('\n<style>/*gs-light-lock-v1*/'
            ':root,:root[data-theme="dark"],:root[data-theme="light"]{'
            + css + 'color-scheme:light!important;}</style>')


def _lum(c):
    """Relative luminance of a #hex / rgb() colour, or None."""
    c = c.strip()
    if c.startswith('#'):
        h = c[1:]
        if len(h) == 3:
            h = ''.join(x * 2 for x in h)
        if len(h) < 6:
            return None
        try:
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None
    else:
        m = re.match(r'rgba?\(([^)]*)\)', c)
        if not m:
            return None
        parts = [p.strip() for p in m.group(1).replace('/', ',').split(',')]
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
    'body', 'html', ':root', '*', 'select', 'input', 'textarea', 'option',
    '.post-body', '.artx', '.post', 'article', 'main',
}


def _is_panel(sel):
    s = sel.strip().lower().rstrip('>+~ ').strip()
    if s in _NEVER_PANEL:
        return False
    # a bare element selector (body, main, article) is document furniture;
    # a class/id/descendant selector is a real panel.
    if not any(c in s for c in '.#[:') and ' ' not in s:
        return False
    return True


def dark_panel_guard(src):
    """Keep intentionally-dark panels, but stop the BW-override painting black
    text on them.

    The site-wide BW-override sets `.artx p, .artx div, ... {color:#000!important}`.
    On a panel that is dark by design (a brown hero, a #182430 stat card) that is
    black-on-dark. Whitening the panel would destroy the design, so instead we
    re-assert light text *on those selectors only*, after the override.
    """
    css = "\n".join(re.findall(r'<style[^>]*>([\s\S]*?)</style>', src))
    if not css:
        return ''
    # resolve :root custom properties so var(--x) backgrounds can be judged
    # Custom properties can be declared on any selector, not just :root - and some
    # (e.g. the hero gradient's --h1/--h2) live in the THEME, not the post, so an
    # unresolvable gradient would otherwise be silently skipped.
    rootvars = {}
    for source in (css, theme_css()):
        for m in re.finditer(r'([^{}]+)\{([^}]*)\}', source):
            for k, v in re.findall(r'(--[a-zA-Z0-9-]+)\s*:\s*([^;}]+)', m.group(2)):
                rootvars.setdefault(k, v.strip())

    def resolve(val, depth=0):
        if depth > 3:
            return val
        m = re.search(r'var\(\s*(--[a-zA-Z0-9-]+)', val)
        if m and m.group(1) in rootvars:
            return resolve(val.replace(m.group(0) + ')', rootvars[m.group(1)]), depth + 1)
        return val

    dark_sels = []
    for m in re.finditer(r'([^{}]+)\{([^}]*)\}', css):
        sel, decl = m.group(1).strip(), m.group(2)
        if not sel or sel.startswith('@') or ':root' in sel:
            continue
        bg = re.search(r'background(?:-color|-image)?\s*:\s*([^;]+)', decl)
        if not bg:
            continue
        val = resolve(bg.group(1))
        cols = re.findall(r'#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)', val)
        lums = [l for l in (_lum(c) for c in cols) if l is not None]
        if not lums:
            continue
        if sum(lums) / len(lums) < 0.42:          # dark panel by design
            for s in sel.split(','):
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
        return ''
    parts = []
    for s, orig in uniq:
        # re-assert the panel's OWN background so the light-lock cannot whiten it,
        # then put light text on it. Both !important, both after the override.
        parts.append(f"{s}{{background:{orig}!important}}")
        parts.append(f"{s},{s} *{{color:#fff!important}}")
        parts.append(f"{s} a,{s} a *{{color:#cfe3ff!important}}")
    return '\n<style>/*gs-dark-panel-guard*/' + "".join(parts) + '</style>'


TRANSLATE = (
    '<div id="google_translate_element" style="margin:8px 0 16px;"></div>'
    '<script type="text/javascript">function googleTranslateElementInit(){'
    'new google.translate.TranslateElement({pageLanguage:"en",'
    'layout:google.translate.TranslateElement.InlineLayout.SIMPLE,'
    'autoDisplay:false},"google_translate_element");}</script>'
    '<script type="text/javascript" '
    'src="https://translate.google.com/translate_a/element.js'
    '?cb=googleTranslateElementInit"></script>')


def add_translate(src):
    """Insert the per-post Google Translate embed.

    Site-wide placement is blocked in Blogger, so each post carries its own.
    It goes after the opening wrapper div, not at position 0, so it lands inside
    the article container and inherits its width.
    """
    if 'google_translate_element' in src:
        return src, False
    m = re.search(r'<div[^>]*class="[^"]*\b(?:artx|fertx|ecx|mapx|idx|wrap|bulletin)\b[^"]*"[^>]*>', src)
    if m:
        return src[:m.end()] + '\n' + TRANSLATE + src[m.end():], True
    m = re.search(r"<div class='post-body'>", src)
    if m:
        return src[:m.end()] + '\n' + TRANSLATE + src[m.end():], True
    return TRANSLATE + '\n' + src, True


def repair(body):
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
    guard = dark_panel_guard(out)
    if guard and 'gs-dark-panel-guard' not in out:
        out += guard
        notes.append(f"dark-panel guard ({guard.count(',') // 2 + 1} selectors kept dark)")
    out, added_tr = add_translate(out)
    if added_tr:
        notes.append("added Google Translate widget")
    no_script = SCRIPT_RE.sub('', out)
    if not DISCLAIMER_RE.search(no_script):
        out += '\n' + DISCLAIMER
        notes.append("appended AI disclosure at end of body")
    return out, notes


def static_pass(body):
    """Return list of failing rule names."""
    errs = []
    fails = []
    for name, sev, fn in STATIC_RULES:
        try:
            ok, _ = fn(body=body, node_check=errs)
        except Exception:
            ok = False
        if not ok:
            fails.append(name)
    return fails


# ---------------------------------------------------------------- verification
class _Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(directory, port):
    handler = lambda *a, **k: _Q(*a, directory=directory, **k)
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


_THEME_CACHE = {}


def theme_css():
    """The live theme's own stylesheet.

    Rendering a post body on a bare page is NOT faithful: the theme supplies a
    large part of the cascade, and several posts are only broken because of the
    interaction between the theme and the post's CSS. Without this the harness
    reports before=1 where the live page has 446 failures.
    """
    if "css" in _THEME_CACHE:
        return _THEME_CACHE["css"]
    html = sh(["curl", "-s", "-A", UA, _BLOG_URL + "/"])
    blocks = re.findall(r"<style[^>]*id='page-skin-1'[^>]*>([\s\S]*?)</style>", html)
    if not blocks:
        blocks = re.findall(r"<style[^>]*>([\s\S]*?)</style>", html)[:3]
    css = "\n".join(blocks)
    css = css.replace("<!--", "").replace("-->", "")
    _THEME_CACHE["css"] = css
    return css


PAGE = ("<!doctype html><meta charset=utf-8>"
        "<style>{THEME}</style>"
        "<div class='post-body'>{BODY}</div>")


def contrast_of(page, url):
    page.goto(url, wait_until="load", timeout=30000)
    page.wait_for_timeout(400)
    return page.evaluate(CONTRAST_JS)


def verify_live(meta, pairs):
    """Faithful verification: load the real post, measure, then swap the repaired
    body into the live DOM and measure again.

    A local harness is not good enough here. Part of the damage happens on
    theme-level wrappers *outside* .post-body (the post's dark --surface colours
    them), so a bare offline page shows the text on white and reports a pass
    that the live page does not have.
    """
    from playwright.sync_api import sync_playwright
    bodies = {pid: after for pid, _, after in pairs}
    out = {}
    with sync_playwright() as p:
        br = p.chromium.launch()
        ctx = br.new_context(viewport={"width": 1280, "height": 900},
                             color_scheme="light", user_agent=UA)
        pg = ctx.new_page()
        for m in meta:
            try:
                pg.goto(m["url"], wait_until="networkidle", timeout=45000)
                pg.wait_for_timeout(600)
                before = pg.evaluate(CONTRAST_JS)
                pg.evaluate("""(html) => {
                    const el = document.querySelector('.post-body');
                    if (!el) return false;
                    // drop styles the post previously injected, then swap in the fix
                    el.outerHTML = html;
                    return true;
                }""", bodies[m["id"]])
                pg.wait_for_timeout(500)
                after = pg.evaluate(CONTRAST_JS)
                out[m["id"]] = (before, after)
            except Exception as ex:
                out[m["id"]] = ({"fails": -1, "checked": 0}, {"fails": -1, "checked": 0})
        br.close()
    return out


def verify(pairs, port=8901):
    """Offline harness (kept for posts that render standalone)."""
    from playwright.sync_api import sync_playwright
    tmp = os.path.join(FIXES, "_verify")
    os.makedirs(tmp, exist_ok=True)
    css = theme_css()
    for pid, before, after in pairs:
        for tag, b in (("before", before), ("after", after)):
            with open(os.path.join(tmp, f"{pid}.{tag}.html"), "w") as f:
                f.write(PAGE.replace("{THEME}", css).replace("{BODY}", b))
    httpd = serve(tmp, port)
    res = {}
    try:
        with sync_playwright() as p:
            br = p.chromium.launch()
            ctx = br.new_context(viewport={"width": 1280, "height": 900},
                                 color_scheme="light", user_agent=UA)
            pg = ctx.new_page()
            for pid, _, _ in pairs:
                try:
                    b = contrast_of(pg, f"http://127.0.0.1:{port}/{pid}.before.html")
                    a = contrast_of(pg, f"http://127.0.0.1:{port}/{pid}.after.html")
                    res[pid] = (b, a)
                except Exception as ex:
                    res[pid] = ({"fails": -1, "checked": 0}, {"fails": -1, "checked": 0})
            br.close()
    finally:
        httpd.shutdown()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    ap.add_argument("--urls-file", default="")
    ap.add_argument("--verify", action="store_true", default=True)
    ap.add_argument("--no-verify", dest="verify", action="store_false")
    args = ap.parse_args()
    os.makedirs(FIXES, exist_ok=True)

    urls = ([args.url] if args.url else
            [l.strip() for l in open(args.urls_file) if l.strip()])
    print(f"repairing {len(urls)} posts\n")

    pairs, meta = [], []
    for u in urls:
        pid = hashlib.md5(u.encode()).hexdigest()[:8]
        slug = u.rsplit("/", 1)[-1].replace(".html", "")
        body = body_of(sh(["curl", "-s", "-A", UA, u]))
        if not body:
            print(f"  !! could not extract body: {u}")
            continue
        before_fails = static_pass(body)
        fixed, notes = repair(body)
        after_fails = static_pass(fixed)
        path = os.path.join(FIXES, f"{slug}.html")
        with open(path, "w") as f:
            f.write(fixed)
        pairs.append((pid, body, fixed))
        meta.append({"url": u, "slug": slug, "id": pid, "path": path,
                     "notes": notes, "before": before_fails, "after": after_fails})
        print(f"  {slug[:52]:52s} {', '.join(notes) or 'no change'}")

    if args.verify and pairs:
        print("\nverifying against the LIVE page (swap repaired body into real DOM) ...")
        res = verify_live(meta, pairs)
        print(f"\n{'post':52s} {'before':>9} {'after':>9}   verdict")
        print("-" * 92)
        allgood = True
        for m in meta:
            b, a = res.get(m["id"], ({"fails": -1}, {"fails": -1}))
            m["contrast_before"], m["contrast_after"] = b["fails"], a["fails"]
            if a["fails"] < 0:
                verdict = "render error"
            elif b["fails"] == 0 and a["fails"] == 0:
                verdict = "already clean"
            elif a["fails"] == 0:
                verdict = "FIXED"
            elif a["fails"] < b["fails"]:
                verdict = f"improved ({b['fails']-a['fails']} fewer)"
                allgood = False
            else:
                verdict = "NOT FIXED"
                allgood = False
            print(f"{m['slug'][:52]:52s} {b['fails']:>9} {a['fails']:>9}   {verdict}")
        print("\nall repaired" if allgood else "\nsome posts need manual attention")

    with open(os.path.join(FIXES, "manifest.json"), "w") as f:
        json.dump(meta, f, indent=1)
    print(f"\ncorrected bodies: {FIXES}/  (manifest.json lists them)")
    print("Nothing was uploaded. Paste a file's contents into the post's HTML view.")


if __name__ == "__main__":
    main()
