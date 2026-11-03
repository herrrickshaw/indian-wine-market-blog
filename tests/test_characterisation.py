"""Pins pre-refactor behaviour: the helpers must reproduce outputs captured from the original code."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from characterise import capture

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).parent / "golden"
POST = "out/post-eu-fta-wine-tariff-cut.html"


def test_helpers_reproduce_golden(tmp_path):
    got = json.loads(json.dumps(capture(ROOT, tmp_path), sort_keys=True, default=str))
    assert got == json.loads((GOLDEN / "characterisation.json").read_text())


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_local_check_cli_output():
    r = subprocess.run(
        [sys.executable, "consistency-check/_local_check.py", POST],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert r.stdout == (GOLDEN / "local_check_post.txt").read_text()


def test_svg_lint_cli_clean_post():
    r = subprocess.run(
        [sys.executable, "tools/svg_chart_lint.py", POST],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "0 file(s) with issues, 0 issue(s) total, out of 1 scanned." in r.stdout
