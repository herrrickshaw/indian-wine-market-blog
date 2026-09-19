"""Render a post body at a phone viewport and list elements that overflow it.

usage: python3 tools/mobile_check.py <post-body.html> [width=375]

Wraps the body in a viewport-meta page, opens it in the bundled Chromium via
Playwright, prints scrollWidth vs viewport plus any element whose right edge
crosses the viewport, and writes <file>.<width>.png beside the input.
Requires: pip install playwright (browser at PLAYWRIGHT_BROWSERS_PATH).
"""

import asyncio
import glob
import json
import sys

from playwright.async_api import async_playwright

src = sys.argv[1]
w = int(sys.argv[2]) if len(sys.argv) > 2 else 375
import os

with open(src, encoding="utf-8") as f:
    body = f.read()
path = os.path.abspath(src) + ".render.html"
with open(path, "w", encoding="utf-8") as f:
    f.write(
        '<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1"><style>body{margin:0;background:#fff}</style></head><body><div style="max-width:100%;padding:8px">'
        + body
        + "</div></body></html>"
    )
JS = """() => {const out=[]; const vw=document.documentElement.clientWidth;
 for (const el of document.querySelectorAll('body *')) {const r=el.getBoundingClientRect();
   if (r.right>vw+2 && r.width>0) out.push([el.tagName.toLowerCase()+(el.className&&typeof el.className==='string'?'.'+el.className.split(' ').slice(0,2).join('.'):''), Math.round(r.left), Math.round(r.right), Math.round(r.width)]);}
 return {sw:document.documentElement.scrollWidth, vw, over:out.slice(0,25)};}"""


async def main():
    async with async_playwright() as p:
        exe = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome") or [None])[
            0
        ]
        try:
            b = await p.chromium.launch()
        except Exception:
            if not exe:
                raise
            b = await p.chromium.launch(
                executable_path=exe
            )  # pip playwright revision != bundled one
        pg = await b.new_page(viewport={"width": w, "height": 800})
        await pg.goto("file://" + path, wait_until="load")
        r = await pg.evaluate(JS)
        print(json.dumps(r, indent=1))
        await pg.screenshot(
            path=f"{path}.{w}.png", clip={"x": 0, "y": 0, "width": w, "height": 1600}
        )
        await b.close()


asyncio.run(main())
