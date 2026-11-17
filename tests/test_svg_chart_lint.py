"""svg_chart_lint: text-width model, overflow, collision, text-hidden-by-rect and double-escaped entities."""
import pytest

import svg_chart_lint as L


def svg(inner, vb="0 0 200 100"):
    return f'<svg viewBox="{vb}">{inner}</svg>'


def lint(html):
    return [i for r in L.find_charts(html) for i in L.lint_svg(*r)]


def test_text_width_table():
    # 3 digits at 0.50 + one 'i' at 0.28 -> 1.78 em
    assert L.text_width("123i", 10, False) == pytest.approx(17.8)
    assert L.text_width("123i", 10, True) == pytest.approx(17.8 * 1.08)
    assert L.text_width("&mdash;", 10, False) == pytest.approx(5.2)   # entity counts as one glyph


def test_anchor_end_and_middle_bboxes():
    (x0, x1, *_), = L.text_lines({"x": "100", "y": "10", "text-anchor": "end", "font-size": "10"}, "00")
    assert (x0, x1) == (90.0, 100.0)
    (x0, x1, *_), = L.text_lines({"x": "100", "y": "10", "text-anchor": "middle", "font-size": "10"}, "00")
    assert (x0, x1) == (95.0, 105.0)


def test_overflow_right_detected_and_inside_is_clean():
    assert lint(svg('<text x="10" y="50" font-size="10">ok</text>')) == []
    issues = lint(svg('<text x="190" y="50" font-size="10">0000</text>'))
    assert [i["type"] for i in issues] == ["OVERFLOW_RIGHT"] and issues[0]["overflow_px"] == 10.0


def test_collision_on_same_baseline():
    issues = lint(svg('<text x="10" y="50" font-size="10">0000</text><text x="20" y="52" font-size="10">0000</text>'))
    assert [i["type"] for i in issues] == ["COLLISION"] and issues[0]["overlap_px"] == 10.0


def test_rect_drawn_after_text_hides_it_but_translucent_or_earlier_does_not():
    t = '<text x="10" y="50" font-size="10">000000</text>'
    r = '<rect x="0" y="40" width="40" height="20" fill="#333"/>'
    assert [i["type"] for i in lint(svg(t + r))] == ["TEXT_HIDDEN_BY_RECT"]
    assert lint(svg(r + t)) == []
    assert lint(svg(t + r.replace('fill="#333"', 'fill="#333" opacity="0.5"'))) == []


def test_rotated_text_is_skipped():
    assert lint(svg('<text x="195" y="50" transform="rotate(-90)">0000000</text>')) == []


def test_inline_tspans_measure_as_one_line():
    lines = L.text_lines({"x": "0", "y": "10", "font-size": "10"}, 'ab <tspan font-weight="700">cd</tspan>')
    assert len(lines) == 1 and lines[0][4] == "ab cd"


def test_tspan_line_wrap_with_em_dy_does_not_crash():
    # dy="1.2em" is the standard SVG line-wrap idiom; it must be read as 1.2 x font-size, not crash the linter
    lines = L.text_lines({"x": "10", "y": "20", "font-size": "10"},
                         '<tspan x="10">first</tspan><tspan x="10" dy="1.2em">second</tspan>')
    assert [(round(y, 3), t) for _, _, y, _, t in lines] == [(20.0, "first"), (32.0, "second")]


def test_double_escaped_entities_found_but_real_ampersand_text_is_not():
    html = "<p>a &amp;mdash; b &amp;#8377; c DGCI&amp;S; chapter</p>"
    assert L.find_double_escaped_entities(html) == ["&amp;mdash;", "&amp;#8377;"]


def test_viewbox_absent_falls_back_to_width_height():
    (_, x0, x1, y0, y1, _), = L.find_charts('<svg width="300" height="120"><text/></svg>')
    assert (x0, x1, y0, y1) == (0.0, 300.0, 0.0, 120.0)


def test_css_class_font_size_is_resolved():
    html = "<style>.lbl{font-size:20px;text-anchor:end}</style>"
    classes = L.parse_css_classes(html)
    (x0, x1, *_), = L.text_lines({"x": "100", "y": "10", "class": "lbl"}, "00", classes)
    assert (x0, x1) == (80.0, 100.0)
