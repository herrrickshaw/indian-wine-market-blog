"""svg_chart_lint helpers/main, consistency-check rule gaps and _local_check (node/curl never run)."""

import json
import sys

import pytest

import _local_check as LC
import checks as C
import svg_chart_lint as L


def svg(inner, vb="0 0 200 100"):
    return f'<svg viewBox="{vb}">{inner}</svg>'


def lint(html, css=None):
    return [i for r in L.find_charts(html) for i in L.lint_svg(*r, css_classes=css)]


# ---------------------------------- svg_chart_lint ----------------------------------


def test_parse_css_classes_merges_blocks():
    html = "<style>.a{font-size:12px; fill:red} .b{font-weight:700}</style><style>.a{fill:blue}</style>"
    assert L.parse_css_classes(html) == {
        "a": {"font-size": "12px", "fill": "blue"},
        "b": {"font-weight": "700"},
    }
    assert L.parse_css_classes("<p>none</p>") == {}


def test_resolve_style_priority_class_then_inline_then_attribute():
    css = {"c": {"font-size": "9px", "fill": "red"}}
    got = L.resolve_style(
        {"class": "c d", "style": "font-size: 11px; fill:green", "fill": "blue"}, css
    )
    assert got == {"font-size": "11px", "fill": "blue"}
    assert L.resolve_style({"class": "c"}, css) == {"font-size": "9px", "fill": "red"}
    assert L.resolve_style({"font-weight": "700", "text-anchor": "end"}, {}) == {
        "font-weight": "700",
        "text-anchor": "end",
    }


@pytest.mark.parametrize(
    "v,fs,want",
    [("1.2em", 10, 12.0), ("5px", 10, 5.0), ("7", 10, 7.0), (" -2em ", 10, -20.0)],
)
def test_length(v, fs, want):
    assert L._length(v, fs) == pytest.approx(want)


def test_text_lines_classes_bold_default_size_and_empty_text():
    css = {"big": {"font-size": "20px", "font-weight": "bold", "text-anchor": "middle"}}
    ((x0, x1, y, fs, plain),) = L.text_lines(
        {"x": "100", "y": "5", "class": "big"}, "00", css
    )
    w = L.text_width("00", 20, True)
    assert (fs, plain, y) == (20.0, "00", 5.0) and (x0, x1) == (
        pytest.approx(100 - w / 2),
        pytest.approx(100 + w / 2),
    )
    ((a, b, *_),) = L.text_lines({"x": "0", "y": "0"}, "00")
    assert b - a == pytest.approx(L.text_width("00", 11, False))  # default font size 11
    assert L.text_lines({"x": "0", "y": "0"}, "  <b></b> ") == []


@pytest.mark.parametrize(
    "w,bold",
    [
        ("600", True),
        ("700", True),
        ("800", True),
        ("900", True),
        ("bold", True),
        ("500", False),
        ("400", False),
    ],
)
def test_font_weight_threshold(w, bold):
    ((a, b, *_),) = L.text_lines(
        {"x": "0", "y": "0", "font-size": "10", "font-weight": w}, "0000"
    )
    assert b - a == pytest.approx(L.text_width("0000", 10, bold))


def test_tspan_dy_y_and_new_line_rules():
    inner = '<tspan x="10">one</tspan><tspan dy="12">same x? no x -> inline</tspan><tspan x="10" dy="12">two</tspan><tspan x="5" y="90">three</tspan>'
    lines = L.text_lines({"x": "0", "y": "20", "font-size": "10"}, inner)
    assert [(l[4], l[2]) for l in lines] == [
        ("onesame x? no x -> inline", 20.0),
        ("two", 44.0),
        ("three", 90.0),
    ]
    first = L.text_lines({"x": "7", "y": "3", "font-size": "10"}, "<tspan>solo</tspan>")
    assert first[0][0] == 7.0  # first tspan without x starts at the text x
    bold_ts = L.text_lines(
        {"x": "0", "y": "0", "font-size": "10"},
        '<tspan x="0" font-weight="bold">00</tspan>',
    )
    assert bold_ts[0][1] == pytest.approx(L.text_width("00", 10, True))
    mixed = L.text_lines(
        {"x": "0", "y": "0", "font-size": "10"},
        'lead <tspan font-weight="700">bb</tspan> tail',
    )
    assert len(mixed) == 1 and mixed[0][4] == "lead bb tail"
    assert L.text_lines({"x": "0", "y": "0"}, "<tspan> </tspan>") == []


def test_opacity_combines_attribute_and_style_forms():
    assert L._opacity_of({}) == 1.0
    assert L._opacity_of({"opacity": "0.5", "fill-opacity": "0.5"}) == 0.25
    assert L._opacity_of({"style": "opacity: .4"}) == pytest.approx(0.4)
    assert L._opacity_of({"opacity": "x"}) == 1.0
    assert (
        L._opacity_of({"opacity": "0.9", "style": "opacity:0.5"}) == 0.5
    )  # inline style wins over the attribute


def test_find_rects_filters():
    inner = (
        '<rect x="1" y="2" width="3" height="4" fill="#111"/>'
        '<rect x="1" y="2" width="3" height="4" fill="none"/><rect x="1" y="2" width="3" height="4"/>'
        '<rect x="1" y="2" width="3" height="4" fill="#111" transform="rotate(5)"/>'
        '<rect x="1" y="2" width="0" height="4" fill="#111"/><rect x="1" y="2" width="3" height="-1" fill="#111"/>'
        '<rect x="a" y="2" width="3" height="4" fill="#111"/><rect x="1" y="2" width="3" fill="#111"/>'
        '<rect x="5" y="6" width="7" height="8" fill="red" opacity="0.5"></rect>'
    )
    got = L.find_rects(inner)
    assert [(r[0], r[1], r[2], r[3], r[4]) for r in got] == [
        (1.0, 2.0, 3.0, 4.0, 1.0),
        (5.0, 6.0, 7.0, 8.0, 0.5),
    ]
    assert got[0][5] < got[1][5]


def test_find_charts_viewbox_and_fallbacks():
    html = '<svg viewBox="10 20 300 150"><g/></svg><svg width="500" height="250">x</svg><svg>y</svg><svg width="" height="">z</svg>'
    charts = [c[1:5] for c in L.find_charts(html)]
    assert charts[0] == (10.0, 310.0, 20.0, 170.0) and charts[1] == (
        0.0,
        500.0,
        0.0,
        250.0,
    )
    assert charts[2] == (0.0, 700.0, 0.0, 400.0) and charts[3] == (
        0.0,
        700.0,
        0.0,
        400.0,
    )
    assert [c[5] for c in L.find_charts(html)][:2] == ["<g/>", "x"]


def test_overflow_left_and_tolerance_boundaries():
    issues = lint(svg('<text x="-5" y="50" font-size="10">00</text>'))
    assert (
        [i["type"] for i in issues] == ["OVERFLOW_LEFT"]
        and issues[0]["x0"] == -5.0
        and issues[0]["canvas_x0"] == 0.0
    )
    assert (
        lint(svg('<text x="-1" y="50" font-size="10">00</text>')) == []
    )  # exactly 1px of tolerance
    assert (
        lint(svg('<text x="191" y="50" font-size="10">00</text>')) == []
    )  # right edge at 201 = canvas + 1
    assert [
        i["type"] for i in lint(svg('<text x="192" y="50" font-size="10">00</text>'))
    ] == ["OVERFLOW_RIGHT"]
    assert lint(svg('<text y="50">00</text><text x="5">no y</text>')) == []


def test_collision_threshold_and_ordering():
    a = '<text x="10" y="50" font-size="10">0000</text>'
    assert (
        lint(svg(a + '<text x="29" y="50" font-size="10">0000</text>')) == []
    )  # touching ends: overlap 1.0 is not > 1
    far = lint(
        svg(a + '<text x="10" y="57" font-size="10">0000</text>')
    )  # 7px apart > 0.6 * 10: different lines
    assert far == []
    near = lint(
        svg(a + '<text x="10" y="56" font-size="10">0000</text>')
    )  # 6px apart is within 0.6 * font size
    assert (
        [i["type"] for i in near] == ["COLLISION"]
        and near[0]["text_a"] == "0000"
        and near[0]["y"] == 50.0
    )


def test_hidden_by_rect_geometry_boundaries():
    t = '<text x="10" y="50" font-size="10">000000</text>'  # x 10..40, y box 41.5..52.5

    def hit(rect):
        return [i["type"] for i in lint(svg(t + rect))]

    assert (
        hit('<rect x="37" y="45" width="30" height="5" fill="#222"/>') == []
    )  # 3px overlap is not > 3
    assert hit('<rect x="36.5" y="45" width="30" height="5" fill="#222"/>') == [
        "TEXT_HIDDEN_BY_RECT"
    ]
    assert hit('<rect x="36" y="45" width="30" height="5" fill="#222"/>') == [
        "TEXT_HIDDEN_BY_RECT"
    ]
    assert hit('<rect x="37.5" y="45" width="30" height="5" fill="#222"/>') == []
    assert (
        hit('<rect x="0" y="60" width="30" height="5" fill="#222"/>') == []
    )  # below the text
    assert hit(
        '<rect x="0" y="45" width="30" height="5" fill="#222" opacity="0.85"/>'
    ) == ["TEXT_HIDDEN_BY_RECT"]
    assert (
        hit('<rect x="0" y="45" width="30" height="5" fill="#222" opacity="0.84"/>')
        == []
    )
    assert (
        hit('<rect x="0" y="45" width="30" height="5" fill="#222" fill-opacity="0.5"/>')
        == []
    )
    assert (
        lint(
            svg(
                '<text x="10" y="50" font-size="10">000000</text>'
                '<rect x="0" y="45" width="30" height="5" fill="#222"/>'
            )
        )[0]["overlap_px"]
        == 20.0
    )


def test_double_escaped_entities():
    html = "a &amp;mdash; b &amp;#8377; c &amp;#x20B9; d &amp;amp; e &amp;foo; f &mdash; g &amp;mdash;"
    assert L.find_double_escaped_entities(html) == [
        "&amp;mdash;",
        "&amp;#8377;",
        "&amp;#x20B9;",
        "&amp;amp;",
        "&amp;mdash;",
    ]
    assert L.find_double_escaped_entities("clean &amp; text &copy;") == []


def test_lint_file_reports_chart_and_entity_issues(tmp_path):
    p = tmp_path / "p.html"
    p.write_text(
        "<style>.s{font-size:10px}</style>"
        + svg('<text class="s" x="195" y="50">0000</text>')
        + "<p>&amp;mdash; &amp;mdash; &amp;ndash;</p>",
        encoding="utf-8",
    )
    res = L.lint_file(str(p))
    assert (
        res[0]["viewBox"] == [0.0, 0.0, 200.0, 100.0]
        and res[0]["issues"][0]["type"] == "OVERFLOW_RIGHT"
    )
    assert res[1]["viewBox"] is None
    assert sorted((i["text"], i["count"]) for i in res[1]["issues"]) == [
        ("&amp;mdash;", 2),
        ("&amp;ndash;", 1),
    ]
    clean = tmp_path / "c.html"
    clean.write_text(
        svg('<text x="5" y="50" font-size="10">ok</text>'), encoding="utf-8"
    )
    assert L.lint_file(str(clean)) == []


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["svg_chart_lint.py", *argv])
    L.main()


def test_main_report_output_and_json(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.html"
    bad.write_text(
        svg(
            '<text x="10" y="50" font-size="10">0000</text><text x="10" y="52" font-size="10">0000</text>'
            '<text x="10" y="80" font-size="10">000000</text><rect x="0" y="75" width="30" height="8" fill="#222"/>'
            '<text x="196" y="20" font-size="10">0000</text>'
        )
        + "<p>&amp;mdash;</p>",
        encoding="utf-8",
    )
    good = tmp_path / "good.html"
    good.write_text("<p>nothing</p>", encoding="utf-8")
    out = tmp_path / "rep.json"
    run_main(monkeypatch, str(bad), str(good), "--json", str(out))
    text = capsys.readouterr().out
    assert (
        f"=== {bad}" in text and "COLLISION" in text and "TEXT-HIDDEN-BY-RECT" in text
    )
    assert (
        "OVERFLOW-RIGHT" in text
        and "DOUBLE-ESCAPED-ENTITY" in text
        and "renders as literal text, x1" in text
    )
    assert (
        "1 file(s) with issues, 4 issue(s) total, out of 2 scanned." in text
        and f"wrote {out}" in text
    )
    assert list(json.loads(out.read_text())) == [str(bad)]


def test_main_min_overflow_filters_small_overflow_only(tmp_path, monkeypatch, capsys):
    f = tmp_path / "f.html"
    f.write_text(
        svg('<text x="193" y="50" font-size="10">00</text>'), encoding="utf-8"
    )  # overflows by 3px
    run_main(monkeypatch, str(f))
    assert "0 file(s) with issues" not in capsys.readouterr().out
    run_main(monkeypatch, str(f), "--min-overflow", "3")
    assert "1 file(s) with issues" in capsys.readouterr().out  # >= threshold is kept
    run_main(monkeypatch, str(f), "--min-overflow", "3.5")
    assert (
        "0 file(s) with issues, 0 issue(s) total, out of 1 scanned."
        in capsys.readouterr().out
    )


def test_main_survives_parse_errors_and_defaults_to_posts_glob(
    tmp_path, monkeypatch, capsys
):
    f = tmp_path / "bad.html"
    f.write_text('<svg viewBox="a b c d">x</svg>', encoding="utf-8")
    run_main(monkeypatch, str(f))
    cap = capsys.readouterr()
    assert (
        f"! {f}: parse error" in cap.err
        and "0 file(s) with issues, 0 issue(s) total, out of 1 scanned." in cap.out
    )
    (tmp_path / "posts").mkdir()
    (tmp_path / "posts" / "x.html").write_text("<p>x</p>", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    run_main(monkeypatch)
    assert "out of 1 scanned." in capsys.readouterr().out


# ---------------------------------- checks gaps ----------------------------------


def test_scripts_parse_rule():
    assert C.r_scripts_parse("b", None) == (True, "skipped (no node)")
    assert C.r_scripts_parse("b", []) == (True, "")
    assert C.r_scripts_parse("b", [2, 3]) == (
        False,
        "JS syntax error in script(s) [2, 3]",
    )


def test_translate_and_index_rules():
    assert C.r_translate_widget("x") == (False, "missing Google Translate widget")
    assert C.r_translate_widget("google_translate_element") == (True, "")
    assert C.r_index_link("x") == (False, "missing All-Articles index link")
    assert C.r_index_link("topnav-index-link") == (True, "")


def test_table_units_rule_thresholds():
    cell = lambda t: f"<td>{t}</td>"
    few = "".join(cell("5 %") for _ in range(11))
    assert C.r_table_units_in_header(few) == (True, "n/a (few cells)")
    bare = "".join(cell("12.5 %") for _ in range(4)) + "".join(
        cell(f"word{i} text") for i in range(8)
    )
    ok, why = C.r_table_units_in_header(bare)
    assert (ok, why) == (False, "33% of 12 cells carry units")  # 4/12 = 33% > 30
    mostly_text = "".join(cell("₹1,200 cr") for _ in range(3)) + "".join(
        cell("descriptive phrase 25.96% of imports") for _ in range(9)
    )
    assert C.r_table_units_in_header(mostly_text) == (True, "25%")
    over = "".join(cell("₹1,200 cr (FY26)") for _ in range(4)) + "".join(
        cell("text") for _ in range(8)
    )
    assert C.r_table_units_in_header(over) == (False, "33% of 12 cells carry units")
    edge = "".join(cell("10 MT") for _ in range(6)) + "".join(
        cell("text") for _ in range(14)
    )
    assert C.r_table_units_in_header(edge) == (True, "30%")  # exactly 30% passes


def test_dark_vars_without_consumers_and_wpi_rule():
    body = "<style>@media (prefers-color-scheme:dark){:root{--accent:#fff}} p{color:var(--accent)}</style>"
    assert C.r_dark_vars_locked(body) == (
        True,
        "n/a (vars declared but never consumed)",
    )
    assert (
        C.r_bw_override("@media (prefers-color-scheme:dark){} BW-OVERRIDE")[0] is True
    )
    assert C.r_bw_override("@media (prefers-color-scheme:dark){}") == (
        False,
        "dark palette present with no BW-override / light lock",
    )
    assert C.r_wpi_attribution("no index here") == (True, "n/a")
    assert C.r_wpi_attribution("MoSPI publishes the Wholesale Price Index") == (
        False,
        "WPI attributed to MoSPI",
    )
    assert C.r_wpi_attribution("the WPI, as published by MoSPI today")[0] is True
    assert C.r_wpi_attribution("Wholesale Price Index (not a MoSPI product)")[0] is True
    assert (
        C.r_wpi_attribution("MoSPI Wholesale Price Index but published by DPIIT")[0]
        is True
    )


def test_rule_table_has_unique_names_and_valid_severities():
    names = [n for n, _, _ in C.STATIC_RULES]
    assert len(names) == len(set(names)) and all(
        callable(f) for _, _, f in C.STATIC_RULES
    )
    assert {s for _, s, _ in C.STATIC_RULES} <= {C.BLOCKER, C.MAJOR, C.MINOR}


# ---------------------------------- _local_check ----------------------------------


def test_js_syntax_errors_runs_node_and_parses_output(monkeypatch):
    written = {}

    class FakeFile:
        def __init__(self, name, mode):
            written["path"], self.buf = name, []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            written["text"] = "".join(self.buf)

        def write(self, s):
            self.buf.append(s)

    monkeypatch.setattr(
        LC, "open", lambda name, mode="r", **k: FakeFile(name, mode), raising=False
    )
    seen = {}
    monkeypatch.setattr(
        LC.subprocess,
        "run",
        lambda cmd, capture_output, text, check: seen.update(cmd=cmd)
        or type("R", (), {"stdout": " [1, 3]\n"})(),
    )
    assert LC.js_syntax_errors("post.html") == [1, 3]
    assert (
        seen["cmd"] == ["node", "/tmp/_syntax_check.js", "post.html"]
        and written["text"] == LC.NODE_SYNTAX
    )
    monkeypatch.setattr(
        LC.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "not json"})()
    )
    assert LC.js_syntax_errors("p") == []
    monkeypatch.setattr(
        LC.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": ""})()
    )
    assert LC.js_syntax_errors("p") == []


def test_local_check_main_reports_and_exit_code(tmp_path, monkeypatch, capsys):
    good = tmp_path / "g.html"
    good.write_text("<p>x</p>", encoding="utf-8")
    monkeypatch.setattr(LC, "js_syntax_errors", lambda path: [])
    rules = [
        ("passes", "BLOCKER", lambda body, node_check: (True, "fine")),
        ("fails", "MINOR", lambda body, node_check: (False, "why")),
        ("crashes", "BLOCKER", lambda body, node_check: {}["x"]),
    ]
    monkeypatch.setattr(LC, "STATIC_RULES", rules)
    monkeypatch.setattr(sys, "argv", ["lc", str(good)])
    with pytest.raises(SystemExit) as e:
        LC.main()
    out = capsys.readouterr().out
    assert (
        e.value.code == 1
        and f"=== {good}" in out
        and "  [OK] BLOCKER passes: fine" in out
    )
    assert (
        "  [FAIL] MINOR fails: why" in out
        and "[FAIL] BLOCKER crashes: rule error: KeyError: 'x'" in out
    )
    monkeypatch.setattr(LC, "STATIC_RULES", rules[:1])
    with pytest.raises(SystemExit) as e:
        LC.main()
    assert e.value.code == 0
    monkeypatch.setattr(sys, "argv", ["lc"])
    with pytest.raises(SystemExit) as e:
        LC.main()
    assert e.value.code == 0  # no files: vacuously fine
