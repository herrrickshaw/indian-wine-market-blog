#!/usr/bin/env python3
"""Run STATIC_RULES against local out/*.html files (not yet published)."""
import sys, os, json, subprocess
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

def js_syntax_errors(path):
    script = "/tmp/_syntax_check.js"
    with open(script, "w") as f:
        f.write(NODE_SYNTAX)
    out = subprocess.run(["node", script, path], capture_output=True, text=True).stdout.strip()
    try:
        return json.loads(out)
    except Exception:
        return []

def main():
    ok_all = True
    for path in sys.argv[1:]:
        body = open(path, encoding="utf-8").read()
        errs = js_syntax_errors(path)
        print("===", path)
        for name, sev, fn in STATIC_RULES:
            try:
                ok, detail = fn(body=body, node_check=errs)
            except Exception as ex:
                ok, detail = False, f"rule error: {type(ex).__name__}: {ex}"
            status = "OK" if ok else "FAIL"
            if not ok:
                ok_all = False
            print(f"  [{status}] {sev} {name}: {detail}")
    sys.exit(0 if ok_all else 1)

if __name__ == "__main__":
    main()
