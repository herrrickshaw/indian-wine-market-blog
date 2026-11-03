"""Characterisation of crosslink_analysis.main(): golden captured before it was split into stages."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import crosslink_analysis as C

GOLDEN = Path(__file__).parent / "golden" / "crosslink_main.json"


def _link(slug):
    return f'<a href="{C._BLOG_URL}/2026/01/{slug}.html">x</a>'


def _build(root):
    labels = {
        "a": ["Wine", "Tariffs"], "b": ["Wine", "Tariffs"], "c": ["Wine", "Tariffs"],
        "d": ["Wine"], "e": ["Wine"], "f": ["Solo"], "g": [],
    }
    (root / "meta").mkdir()
    (root / "posts").mkdir()
    posts = {str(i): {"slug": s, "labels": lab} for i, (s, lab) in enumerate(labels.items())}
    posts["99"] = {"slug": "utility", "labels": ["Wine"]}  # no post file
    (root / "meta" / "manifest.json").write_text(json.dumps({"posts": posts}))
    bodies = {
        "a": _link("b") + _link("a") + _link("article-index-start-here") + _link("ghost"),
        "b": _link("d") + "<script>" + _link("e") + "</script>",
        "c": "", "d": _link("a"), "e": "", "f": "", "g": _link("A"),
    }
    for s, b in bodies.items():
        (root / "posts" / f"{s}.html").write_text(b)
    (root / "posts" / "archived.html").write_text(_link("a"))


_DRIVER = """
import json, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from pathlib import Path
import crosslink_analysis as C
import test_crosslink_main as T
root = Path(sys.argv[3])
T._build(root)
C.ROOT = root
out = {}
import io, contextlib
for name, argv in {
    "text": [],
    "small_top": ["--top", "2", "--min-label-cluster", "2"],
    "json": ["--json", str(root / "r.json")],
}.items():
    sys.argv = ["x", *argv]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C.main()
    out[name] = {"rc": rc, "stdout": buf.getvalue().replace(str(root), "TMP")}
out["report_json"] = json.loads((root / "r.json").read_text())
print(json.dumps(out, sort_keys=True))
"""


def test_crosslink_main_matches_golden(tmp_path):
    # Tie order in the hub lists follows set iteration order, so pin the hash seed.
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    here = Path(__file__).resolve().parent
    r = subprocess.run(
        [sys.executable, "-c", _DRIVER, str(here.parent / "tools"), str(here), str(tmp_path)],
        capture_output=True, text=True, env=env, check=True,
    )
    got = json.loads(r.stdout)
    if not GOLDEN.exists():  # pragma: no cover - one-off capture
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
        pytest.skip("golden written")
    assert got == json.loads(GOLDEN.read_text())
