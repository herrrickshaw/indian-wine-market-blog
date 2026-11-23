"""consistency-check/fix.py repair functions (pure string transforms) and main() with curl/browser faked."""

import json
import sys

import fix as F
import pytest

# ---------------------------------- body_of ----------------------------------


def test_body_of_single_and_double_quotes_and_nesting():
    html = "<html><div class='post-body'><div>in<div>deep</div></div>tail</div>after</html>"
    assert (
        F.body_of(html)
        == "<div class='post-body'><div>in<div>deep</div></div>tail</div>"
    )
    assert (
        F.body_of('<div class="post-body" id=x><p>a</p></div>zzz')
        == '<div class="post-body" id=x><p>a</p></div>'
    )
    assert F.body_of("<p>no body</p>") == ""
    assert (
        F.body_of("<div class='post-body'><div>never closed")
        == "<div class='post-body'><div>never closed"
    )


def test_sh_returns_stdout(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        F.subprocess,
        "run",
        lambda c, capture_output, text, check: seen.update(c=c, check=check)
        or type("R", (), {"stdout": "out"})(),
    )
    assert F.sh(["curl", "x"]) == "out" and seen == {"c": ["curl", "x"], "check": False}


# ---------------------------------- strip_inverted ----------------------------------


def test_strip_inverted_removes_balanced_blocks():
    mq = F.INVERTED_MQ
    css = (
        f"a{{x:1}}{mq}:dark){{.p{{color:red}} .q{{c:1}}}}b{{y:2}}{mq}:dark){{z{{}}}}end"
    )
    out, n = F.strip_inverted(css)
    assert n == 2 and out.count("gs-fix: removed inverted dark-mode block") == 2
    assert (
        out.startswith("a{x:1}/*")
        and "b{y:2}" in out
        and out.endswith("*/end")
        and "color:red" not in out
    )


def test_strip_inverted_edge_cases():
    assert F.strip_inverted("plain") == ("plain", 0)
    assert (
        F.strip_inverted(F.INVERTED_MQ + ":dark)")[1] == 0
    )  # no opening brace: left alone
    out, n = F.strip_inverted((F.INVERTED_MQ + ":dark){a{}}") * 12)
    assert (
        n == 9 and out.count(F.INVERTED_MQ) == 3
    )  # safety stop after more than 8 passes
    out, n = F.strip_inverted(
        F.INVERTED_MQ + ":dark){a{b"
    )  # unbalanced: consumes to the end
    assert n == 1 and "removed inverted" in out


# ---------------------------------- unsplice_disclaimer ----------------------------------

P = '<p class="disclaimer" data-gs-ai-disclosure-v1="1" style="x">About</p>'


def test_unsplice_exact_pattern():
    src = f"var h='<div>'+x+'<br>{P}\n</div>';"
    out, n = F.unsplice_disclaimer(src)
    assert n == 1 and out == "var h='<div>'+x+'<br></div>';"


def test_unsplice_fallback_scrubs_any_script_literal():
    src = f"<script>var s = '{P}';</script><script>var t=1;</script><p>keep</p>"
    out, n = F.unsplice_disclaimer(src)
    assert (
        n == 1
        and "data-gs-ai-disclosure" not in out
        and "<p>keep</p>" in out
        and "var t=1;" in out
    )
    assert F.unsplice_disclaimer("<script>1</script>") == ("<script>1</script>", 0)


# ---------------------------------- light_lock ----------------------------------


def test_light_lock_prefers_data_theme_light_root():
    src = ':root{--a:#fff}:root[data-theme="light"]{--bg: #eee ; --ink:#111}'
    out = F.light_lock(src)
    assert "--bg:#eee!important;--ink:#111!important;" in out and "--a:" not in out
    assert (
        out.startswith("\n<style>/*gs-light-lock-v1*/")
        and "color-scheme:light!important;}</style>" in out
    )


def test_light_lock_fallbacks():
    assert "--a:#fff!important;" in F.light_lock(":root{--a:#fff}")
    assert F.light_lock("gs-light-lock-v1 :root{--a:#fff}") == ""
    assert F.light_lock("no root here") == "" and F.light_lock(":root{color:red}") == ""


# ---------------------------------- _lum / _is_panel ----------------------------------


@pytest.mark.parametrize(
    "c,want",
    [
        ("#000", 0.0),
        ("#fff", 1.0),
        ("#ffffff", 1.0),
        (" #FF0000 ", 0.2126),
        ("#00ff00", 0.7152),
        ("#0000ff", 0.0722),
        ("rgb(255,255,255)", 1.0),
        ("rgba(0, 0, 0, .5)", 0.0),
        ("rgb(255 0 0 / 50%)", None),
        ("#12", None),
        ("#zzzzzz", None),
        ("rgb(1,2)", None),
        ("rgb(a,b,c)", None),
        ("blue", None),
        ("hsl(0,0%,0%)", None),
    ],
)
def test_lum(c, want):
    got = F._lum(c)
    assert (got is None) if want is None else got == pytest.approx(want, abs=1e-4)


def test_lum_slash_syntax():
    assert F._lum("rgb(255 / 255 / 255)") == pytest.approx(1.0)


@pytest.mark.parametrize(
    "sel,ok",
    [
        ("body", False),
        ("HTML", False),
        (":root", False),
        ("*", False),
        ("select", False),
        (".post-body", False),
        (" .artx ", False),
        ("article", False),
        ("main >", False),
        (".hero", True),
        ("#panel", True),
        ("div.card", True),
        ("section .box", True),
        ("a[href]", True),
        ("p", False),
        ("td:first-child", True),
    ],
)
def test_is_panel(sel, ok):
    assert F._is_panel(sel) is ok


# ---------------------------------- dark_panel_guard ----------------------------------


@pytest.fixture()
def no_theme(monkeypatch):
    monkeypatch.setattr(F, "theme_css", lambda: "")


def style(css):
    return f"<style>{css}</style>"


def test_guard_emits_rules_for_dark_panels_only(no_theme):
    css = ".hero{background:#102030} .card{background:#ffffff} body{background:#000} .x{color:red} @media (x){.y{background:#000}}"
    out = F.dark_panel_guard(style(css))
    assert out.startswith("\n<style>/*gs-dark-panel-guard*/") and out.endswith(
        "</style>"
    )
    assert (
        ".hero{background:#102030!important}" in out
        and ".hero,.hero *{color:#fff!important}" in out
    )
    assert ".hero a,.hero a *{color:#cfe3ff!important}" in out
    assert ".card" not in out and "body{" not in out and ".x" not in out


def test_guard_resolves_vars_gradients_and_dedupes(no_theme):
    css = ":root{--panel:#101010;--g:#202020}.a,.b{background:var(--panel)}.a{background-image:linear-gradient(#000,#111)}.c{background:rgba(0,0,0,.9)}"
    out = F.dark_panel_guard(style(css))
    assert out.count(".a{background") == 1 and ".b{background:#101010!important}" in out
    assert (
        ".c{background:rgba(0,0,0,.9)!important}" in out and "var(--panel)" not in out
    )
    assert F.dark_panel_guard("<p>no styles</p>") == ""
    assert F.dark_panel_guard(style(".card{background:#fff}")) == ""
    assert (
        F.dark_panel_guard(style(".t{background:transparent}")) == ""
    )  # unparseable colour is skipped


def test_guard_var_depth_and_long_selector_limits(no_theme):
    css = ":root{--a:var(--b);--b:var(--c);--c:var(--d);--d:var(--e);--e:#000}.p{background:var(--a)}"
    assert F.dark_panel_guard(style(css)) == ""  # more than 3 levels: left unresolved
    long_sel = "." + "x" * 80
    assert (
        F.dark_panel_guard(style(long_sel + "{background:#000}")) == ""
    )  # selectors of 80+ chars are skipped
    mixed = ".a{background:#000;} .b{background:#fff}"
    assert ".b" not in F.dark_panel_guard(style(mixed))
    # average luminance decides: 0.41 dark, 0.43 light
    assert F.dark_panel_guard(style(".m{background:#6a6a6a}")) != ""  # 0.416
    assert F.dark_panel_guard(style(".m{background:#6e6e6e}")) == ""  # 0.431


def test_guard_uses_theme_variables(monkeypatch):
    monkeypatch.setattr(F, "theme_css", lambda: ":root{--h1:#001122}")
    out = F.dark_panel_guard(
        style(".hero{background:linear-gradient(var(--h1),var(--h1))}")
    )
    assert ".hero{background:linear-gradient(#001122,#001122)!important}" in out


# ---------------------------------- translate / repair / static_pass ----------------------------------


def test_add_translate_positions():
    out, added = F.add_translate('<div class="artx wrap">X</div>')
    assert (
        added
        and out.startswith(
            '<div class="artx wrap">\n<div id="google_translate_element"'
        )
        and out.endswith("X</div>")
    )
    out, added = F.add_translate("<div class='post-body'>Y</div>")
    assert added and out.startswith("<div class='post-body'>\n<div id=")
    out, added = F.add_translate("<p>plain</p>")
    assert added and out == F.TRANSLATE + "\n<p>plain</p>"
    assert F.add_translate("google_translate_element here") == (
        "google_translate_element here",
        False,
    )
    out, _ = F.add_translate(
        '<div class="notartx">Z</div>'
    )  # \b matching: a longer word is not a wrapper
    assert out.startswith(F.TRANSLATE)


def test_repair_full_pipeline_notes(no_theme):
    body = (
        "<div class='post-body'><style>:root{--bg:#fff;--p:#001}.hero{background:#112}"
        f"{F.INVERTED_MQ}:dark){{.z{{c:1}}}}</style>"
        f"<script>var h='<div>'+x+'{P}\n</div>';</script></div>"
    )
    out, notes = F.repair(body)
    assert (
        notes[0] == "removed 1 inverted media-query block(s)"
        and notes[1] == "lifted 1 disclaimer(s) out of <script>"
    )
    assert "appended light-lock from the post's own :root" in notes and any(
        n.startswith("dark-panel guard (") for n in notes
    )
    assert (
        "added Google Translate widget" in notes
        and notes[-1] == "appended AI disclosure at end of body"
    )
    assert (
        out.endswith(F.DISCLAIMER)
        and "gs-light-lock-v1" in out
        and "gs-dark-panel-guard" in out
    )
    again, notes2 = F.repair(out)  # idempotent: nothing left to do
    assert notes2 == [] and again == out


def test_repair_clean_body_is_untouched_except_required_widgets(no_theme):
    body = f"<div class='post-body'>{F.TRANSLATE}<p>ok</p>{F.DISCLAIMER}</div>"
    assert F.repair(body) == (body, [])


def test_disclaimer_mentions_blog_url():
    assert f'href="{F._BLOG_URL}/p/disclaimer.html"' in F.DISCLAIMER


def test_static_pass_lists_failing_rules_and_survives_rule_crashes(monkeypatch):
    rules = [
        ("ok", "BLOCKER", lambda body, node_check: (True, "")),
        ("bad", "BLOCKER", lambda body, node_check: (False, "x")),
        ("boom", "BLOCKER", lambda body, node_check: 1 / 0),
    ]
    monkeypatch.setattr(F, "STATIC_RULES", rules)
    assert F.static_pass("b") == ["bad", "boom"]


def test_static_pass_real_rules_flag_a_missing_widget():
    fails = F.static_pass("<p>x</p>")
    assert "translate-widget" in fails or any("translate" in n for n in fails)
    assert F.static_pass(F.repair("<div class='post-body'><p>x</p></div>")[0]) != fails


# ---------------------------------- theme_css ----------------------------------


def test_theme_css_extracts_page_skin_and_caches(monkeypatch):
    calls = []
    page = "<style type='text/css' id='page-skin-1'><!--a{b:1}--></style><style>other{}</style>"
    monkeypatch.setattr(F, "sh", lambda cmd: calls.append(cmd) or page)
    monkeypatch.setattr(F, "_THEME_CACHE", {})
    assert F.theme_css() == "a{b:1}" and F.theme_css() == "a{b:1}"
    assert (
        len(calls) == 1
        and calls[0][-1] == F._BLOG_URL + "/"
        and calls[0][:4] == ["curl", "-s", "-A", F.UA]
    )


def test_theme_css_falls_back_to_first_three_style_blocks(monkeypatch):
    monkeypatch.setattr(
        F,
        "sh",
        lambda cmd: "<style>a{}</style><style>b{}</style><style>c{}</style><style>d{}</style>",
    )
    monkeypatch.setattr(F, "_THEME_CACHE", {})
    assert F.theme_css() == "a{}\nb{}\nc{}"


# ---------------------------------- main ----------------------------------


@pytest.fixture()
def fixdir(monkeypatch, tmp_path):
    monkeypatch.setattr(F, "FIXES", str(tmp_path / "fixes"))
    monkeypatch.setattr(F, "theme_css", lambda: "")
    return tmp_path


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["fix.py", *argv])
    F.main()


def test_main_no_verify_writes_fixed_body_and_manifest(monkeypatch, fixdir, capsys):
    page = "<div class='post-body'><p>hello</p></div>"
    monkeypatch.setattr(
        F, "sh", lambda cmd: page if cmd[-1].endswith("a-post.html") else ""
    )
    run_main(monkeypatch, "--url", "https://w/2026/01/a-post.html", "--no-verify")
    out = capsys.readouterr().out
    assert (
        "repairing 1 posts" in out and "a-post" in out and "Nothing was uploaded" in out
    )
    written = (fixdir / "fixes" / "a-post.html").read_text()
    assert (
        written.startswith(
            "<div class='post-body'>\n<div id=\"google_translate_element\""
        )
        and "<p>hello</p>" in written
    )
    assert F.DISCLAIMER in written
    man = json.loads((fixdir / "fixes" / "manifest.json").read_text())
    assert (
        man[0]["slug"] == "a-post"
        and man[0]["url"] == "https://w/2026/01/a-post.html"
        and len(man[0]["id"]) == 8
    )
    assert (
        "added Google Translate widget" in man[0]["notes"]
        and man[0]["after"] != man[0]["before"]
    )


def test_main_urls_file_skips_unextractable(monkeypatch, fixdir, capsys):
    uf = fixdir / "urls.txt"
    uf.write_text("https://w/a.html\n\n  https://w/b.html  \n")
    monkeypatch.setattr(
        F,
        "sh",
        lambda cmd: (
            "<div class='post-body'>x</div>"
            if cmd[-1].endswith("a.html")
            else "<html></html>"
        ),
    )
    run_main(monkeypatch, "--urls-file", str(uf), "--no-verify")
    out = capsys.readouterr().out
    assert (
        "repairing 2 posts" in out
        and "!! could not extract body: https://w/b.html" in out
    )
    assert [
        m["slug"] for m in json.loads((fixdir / "fixes" / "manifest.json").read_text())
    ] == ["a"]


@pytest.mark.parametrize(
    "before,after,verdict,allgood",
    [
        (0, 0, "already clean", True),
        (5, 0, "FIXED", True),
        (5, 2, "improved (3 fewer)", False),
        (2, 2, "NOT FIXED", False),
        (2, 7, "NOT FIXED", False),
        (-1, -1, "render error", True),
    ],
)
def test_main_verdicts(monkeypatch, fixdir, capsys, before, after, verdict, allgood):
    monkeypatch.setattr(F, "sh", lambda cmd: "<div class='post-body'>x</div>")
    monkeypatch.setattr(
        F,
        "verify_live",
        lambda meta, pairs: {meta[0]["id"]: ({"fails": before}, {"fails": after})},
    )
    run_main(monkeypatch, "--url", "https://w/p.html")
    out = capsys.readouterr().out
    assert f"{before:>9} {after:>9}   {verdict}" in out
    assert ("\nall repaired" in out) is allgood and ("manual attention" in out) is (
        not allgood
    )
    man = json.loads((fixdir / "fixes" / "manifest.json").read_text())
    assert (man[0]["contrast_before"], man[0]["contrast_after"]) == (before, after)


def test_main_verify_skipped_when_nothing_extracted(monkeypatch, fixdir, capsys):
    monkeypatch.setattr(F, "sh", lambda cmd: "")
    monkeypatch.setattr(
        F, "verify_live", lambda *a: pytest.fail("no browser needed without posts")
    )
    run_main(monkeypatch, "--url", "https://w/p.html")
    assert "could not extract body" in capsys.readouterr().out


def test_main_requires_a_source(monkeypatch, fixdir):
    with pytest.raises(FileNotFoundError):
        run_main(monkeypatch, "--no-verify")
