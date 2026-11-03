#!/usr/bin/env python3
"""Run STATIC_RULES against local out/*.html files (not yet published)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from checks import STATIC_RULES

NODE_SYNTAX = r"""
const fs=require('fs');
const html=fs.readFileSync(process.argv[1],'utf8');
const re=/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g;
let m,i=0,bad=[];
while((m=re.exec(html))){ i++;
  let c=m[1].replace(/^\s*\/\/<!\[CDATA\[/,'').replace(/\/\/\]\]>\s*$/,'');
  if(!c.trim()) continue;
  try{ new Function(c); }catch(e){ bad.push(i); }
}
console.log(JSON.stringify(bad));
"""


def js_syntax_errors(path: str) -> list:
    """1-based indexes of <script> blocks in the HTML file at `path` that fail to parse (via node)."""
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "_syntax_check.js")
        with open(script, "w") as f:
            f.write(NODE_SYNTAX)
        out = subprocess.run(
            ["node", script, path], capture_output=True, text=True, check=False
        ).stdout.strip()
    try:
        return json.loads(out)
    except ValueError:  # malformed node output should not abort
        return []


def run_rules(body: str, node_errors: list) -> list:
    """Evaluate STATIC_RULES on a post body; returns [(name, severity, ok, detail)]."""
    results = []
    for name, sev, fn in STATIC_RULES:
        try:
            ok, detail = fn(body=body, node_check=node_errors)
        # a crashing rule counts as a fail, not an abort
        except Exception as ex:  # noqa: BLE001
            ok, detail = False, f"rule error: {type(ex).__name__}: {ex}"
        results.append((name, sev, ok, detail))
    return results


def main() -> None:
    ok_all = True
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        errs = js_syntax_errors(path)
        print("===", path)
        for name, sev, ok, detail in run_rules(body, errs):
            if not ok:
                ok_all = False
            print(f"  [{'OK' if ok else 'FAIL'}] {sev} {name}: {detail}")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
