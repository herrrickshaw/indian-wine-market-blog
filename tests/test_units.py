"""Unit tests for the pure helpers split out of fix.py / snapshot.py / _local_check.py."""

import _local_check
import pytest
import repair
import versioning

DISC = '<p data-gs-ai-disclosure-v1="1">AI</p>'


def test_strip_inverted_removes_nested_block():
    src = "a@media not all and (prefers-color-scheme:dark){x{y:1}}b"
    out, n = repair.strip_inverted(src)
    assert n == 1 and out.startswith("a/* gs-fix") and out.endswith("b")
    assert "prefers-color-scheme" not in out


def test_repair_is_idempotent_enough_to_add_disclaimer_and_translate_once():
    out, notes = repair.repair("<div class='post-body'><p>x</p></div>", DISC)
    assert out.count("google_translate_element") >= 1
    assert DISC in out
    again, notes2 = repair.repair(out, DISC)
    assert "added Google Translate widget" not in notes2
    assert again.count(DISC) == 1


def test_dark_panel_guard_uses_injected_theme_lazily():
    calls = []
    assert (
        repair.dark_panel_guard("<p>no style</p>", lambda: calls.append(1) or "") == ""
    )
    assert calls == []  # theme only fetched when the post has CSS
    out = repair.dark_panel_guard(
        "<style>.hero{background:var(--p)}</style>", lambda: ":root{--p:#101010}"
    )
    assert ".hero{background:#101010!important}" in out


@pytest.mark.parametrize(
    "colour,expected_dark",
    [("#000", True), ("#fff", False), ("rgb(0,0,0)", True), ("nonsense", None)],
)
def test_lum(colour, expected_dark):
    lum = repair._lum(colour)
    assert (None if lum is None else lum < 0.42) is expected_dark


def test_classify_levels():
    a = "<p>Duty 150</p><h2>One</h2>"
    assert versioning.classify(None, a)[0] is None
    assert versioning.classify(a, a)[0] is None
    assert versioning.classify(a, a.replace("150", "120"))[0] == "major"
    assert versioning.classify(a, a + "<h2>Two</h2>")[0] == "minor"
    assert versioning.classify(a, a + "<style>p{}</style>")[0] == "patch"


@pytest.mark.parametrize(
    "level,expected", [("major", "2.0.0"), ("minor", "1.3.0"), ("patch", "1.2.4")]
)
def test_bump(level, expected):
    assert versioning.bump("1.2.3", level) == expected


def test_markers_and_delta():
    rec = {"markers": versioning.markers("gs-light-lock")}
    assert rec["markers"]["light_lock"] is True
    old = {"markers": versioning.markers("")}
    assert versioning.marker_delta(old, rec) == {"light_lock": [False, True]}


def test_run_rules_reports_every_rule_and_survives_a_crashing_rule(monkeypatch):
    def boom(**_kw):
        raise RuntimeError("x")

    monkeypatch.setattr(_local_check, "STATIC_RULES", [("boom", "MINOR", boom)])
    [(name, sev, ok, detail)] = _local_check.run_rules("", [])
    assert (name, sev, ok) == ("boom", "MINOR", False)
    assert detail == "rule error: RuntimeError: x"
