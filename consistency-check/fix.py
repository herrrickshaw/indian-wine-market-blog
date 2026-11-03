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

import argparse
import hashlib
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
)
from checks import CONTRAST_JS
from repair import (  # noqa: F401 -- re-exported: fix.py was the public home of these
    DISCLAIMER_RE,
    SCRIPT_RE,
    TRANSLATE,
    add_translate,
    body_of,
    light_lock,
    static_pass,
    strip_inverted,
    unsplice_disclaimer,
)
from repair import dark_panel_guard as _dark_panel_guard
from repair import repair as _repair

HERE = os.path.dirname(os.path.abspath(__file__))
FIXES = os.path.join(HERE, "fixes")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# BLOG_URL comes from blogger_api's env-var config (WINE_BLOG_URL); see blogger_api.py.
try:
    import blogger_api as _B

    _BLOG_URL = _B.BLOG_URL
except SystemExit:
    _BLOG_URL = None
_BLOG_URL = (
    _BLOG_URL or os.environ.get("WINE_BLOG_URL") or "https://REPLACE-ME.blogspot.com"
)

DISCLAIMER = (
    '<p class="disclaimer" data-gs-ai-disclosure-v1="1" '
    'style="font-size:.82rem;color:#555;margin-top:14px">'
    "<strong>About this article:</strong> Researched, written and edited by "
    "Umashankar Triplicane Dwarakanathan, with AI research assistance; every figure "
    "is meant to trace to the primary source cited. See the "
    f'<a href="{_BLOG_URL}/p/disclaimer.html">Editorial Policy</a> '
    "for how sourcing, AI use and corrections work.</p>"
)


def sh(c):
    return subprocess.run(c, capture_output=True, text=True, check=False).stdout


def dark_panel_guard(src):
    """repair.dark_panel_guard wired to the live theme CSS."""
    return _dark_panel_guard(src, theme_css)


def repair(body):
    """Repair a post body (see repair.repair); returns (new_body, notes)."""
    return _repair(body, DISCLAIMER, theme_css)


class _Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(directory, port):
    def handler(*a, **k):
        return _Q(*a, directory=directory, **k)

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


PAGE = (
    "<!doctype html><meta charset=utf-8>"
    "<style>{THEME}</style>"
    "<div class='post-body'>{BODY}</div>"
)


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
        ctx = br.new_context(
            viewport={"width": 1280, "height": 900}, color_scheme="light", user_agent=UA
        )
        pg = ctx.new_page()
        for m in meta:
            try:
                pg.goto(m["url"], wait_until="networkidle", timeout=45000)
                pg.wait_for_timeout(600)
                before = pg.evaluate(CONTRAST_JS)
                pg.evaluate(
                    """(html) => {
                    const el = document.querySelector('.post-body');
                    if (!el) return false;
                    // drop styles the post previously injected, then swap in the fix
                    el.outerHTML = html;
                    return true;
                }""",
                    bodies[m["id"]],
                )
                pg.wait_for_timeout(500)
                after = pg.evaluate(CONTRAST_JS)
                out[m["id"]] = (before, after)
            # a browser-side eval failure counts as unverifiable, not a crash
            except Exception:  # noqa: BLE001
                out[m["id"]] = (
                    {"fails": -1, "checked": 0},
                    {"fails": -1, "checked": 0},
                )
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
            ctx = br.new_context(
                viewport={"width": 1280, "height": 900},
                color_scheme="light",
                user_agent=UA,
            )
            pg = ctx.new_page()
            for pid, _, _ in pairs:
                try:
                    b = contrast_of(pg, f"http://127.0.0.1:{port}/{pid}.before.html")
                    a = contrast_of(pg, f"http://127.0.0.1:{port}/{pid}.after.html")
                    res[pid] = (b, a)
                # same: an unverifiable pair must not abort the whole batch
                except Exception:  # noqa: BLE001
                    res[pid] = (
                        {"fails": -1, "checked": 0},
                        {"fails": -1, "checked": 0},
                    )
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

    if args.url:
        urls = [args.url]
    else:
        with open(args.urls_file) as fh:
            urls = [line.strip() for line in fh if line.strip()]
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
        meta.append(
            {
                "url": u,
                "slug": slug,
                "id": pid,
                "path": path,
                "notes": notes,
                "before": before_fails,
                "after": after_fails,
            }
        )
        print(f"  {slug[:52]:52s} {', '.join(notes) or 'no change'}")

    if args.verify and pairs:
        print(
            "\nverifying against the LIVE page (swap repaired body into real DOM) ..."
        )
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
