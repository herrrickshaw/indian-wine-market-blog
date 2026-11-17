"""consistency-check static rules on synthetic post HTML."""
import checks as C

DISC = '<p data-gs-ai-disclosure-v1>AI-assisted</p>'


def test_disclaimer_inside_script_is_a_blocker_and_outside_counts_as_present():
    body = f"<script>var s = '{DISC}';</script>"
    assert C.r_disclaimer_not_in_script(body) == (False, "disclaimer inside <script> #[1]")
    assert C.r_disclaimer_present(body)[0] is False
    assert C.r_disclaimer_present(DISC + "<script>x()</script>")[0] is True


def test_inverted_media_query_counted():
    body = "@media not all and (prefers-color-scheme:dark){} " * 2
    assert C.r_no_inverted_media_query(body) == (False, "2 inverted dark-mode media query block(s)")


def test_dark_palette_mentioned_only_in_a_comment_is_not_declared():
    body = "<!-- we do not use prefers-color-scheme here --><p style='color:var(--text)'>x</p>"
    assert C.r_dark_vars_locked(body) == (True, "n/a (no dark palette)")
    assert C.r_bw_override(body)[0] is True


def test_dark_palette_consumed_without_light_lock_fails_and_important_fixes_it():
    body = "<style>@media (prefers-color-scheme:dark){:root{--text:#eee}} p{color:var(--text)}</style>"
    assert C.r_dark_vars_locked(body)[0] is False
    assert C.r_dark_vars_locked(body.replace("--text:#eee", "--text:#111 !important"))[0] is True


def _table(cells):
    return "<table>" + "".join(f"<td>{c}</td>" for c in cells) + "</table>"


def test_units_in_cells_rule_counts_only_bare_number_unit_cells():
    bare = ["₹1,234 cr"] * 5 + ["Maharashtra"] * 7          # 5/12 = 42% bare
    ok, detail = C.r_table_units_in_header(_table(bare))
    assert not ok and detail == "42% of 12 cells carry units"
    descriptive = ["~25.96% of all technical-textile imports"] * 12
    assert C.r_table_units_in_header(_table(descriptive))[0] is True
    assert C.r_table_units_in_header(_table(["5%"] * 11)) == (True, "n/a (few cells)")


def test_wpi_attribution():
    assert C.r_wpi_attribution("MoSPI's Wholesale Price Index rose") == (False, "WPI attributed to MoSPI")
    assert C.r_wpi_attribution("Wholesale Price Index (published by DPIIT) rose")[0] is True
    assert C.r_wpi_attribution("no index mentioned") == (True, "n/a")


def test_js_parse_rule_skips_without_node():
    assert C.r_scripts_parse("", node_check=None) == (True, "skipped (no node)")
    assert C.r_scripts_parse("", node_check=[2]) == (False, "JS syntax error in script(s) [2]")


def test_every_rule_returns_a_bool_detail_pair_on_the_shipped_draft():
    import pathlib
    body = (pathlib.Path(__file__).resolve().parent.parent / "out" / "post-eu-fta-wine-tariff-cut.html").read_text()
    for name, sev, fn in C.STATIC_RULES:
        ok, detail = fn(body, node_check=None)
        assert isinstance(ok, bool) and isinstance(detail, str), name
