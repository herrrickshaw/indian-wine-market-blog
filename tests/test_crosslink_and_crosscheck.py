"""crosslink_analysis link extraction/graph and research_crosscheck number/term helpers (synthetic posts)."""
import crosslink_analysis as X
import research_crosscheck as R

BASE = X._BLOG_URL


def test_outbound_links_dedupe_and_skip_self_noise_scripts_and_other_hosts(tmp_path):
    p = tmp_path / "a.html"
    p.write_text(
        f'<a href="{BASE}/2026/09/b.html">b</a><a href="{BASE}/2026/09/B.html#x">b again</a>'
        f'<a href="{BASE}/2026/09/a.html">self</a><a href="{BASE}/2026/09/article-index-start-here.html">idx</a>'
        f'<a href="{BASE}/2026/10/c_0123.html">c</a><a href="https://other.example/2026/09/d.html">d</a>'
        f'<script>x = \'<a href="{BASE}/2026/09/e.html">\';</script>')
    assert X.extract_outbound(p, "a") == {"b", "c_0123"}


def test_build_graph_keeps_only_live_slugs(tmp_path, monkeypatch):
    posts = tmp_path / "posts"; posts.mkdir()
    (posts / "a.html").write_text(f'<a href="{BASE}/2026/09/b.html">b</a><a href="{BASE}/2026/09/gone.html">g</a>')
    (posts / "b.html").write_text("no links")
    (posts / "old.html").write_text(f'<a href="{BASE}/2026/09/a.html">a</a>')
    monkeypatch.setattr(X, "ROOT", tmp_path)
    assert X.build_graph({"a": {}, "b": {}}) == {"a": {"b"}, "b": set()}


def test_specific_enough_needs_two_digits():
    assert not R.is_specific_enough("₹0") and not R.is_specific_enough("1%")
    assert R.is_specific_enough("12%") and R.is_specific_enough("₹1.5 cr")


def test_money_and_pct_patterns():
    text = "Imports rose 12.5% to ₹1,234 crore (Rs. 56 lakh, $7 bn); Rs, alone is not money."
    assert R.PCT_RE.findall(text) == ["12.5%"]
    assert [m.strip() for m in R.MONEY_RE.findall(text)] == ["₹1,234 crore", "Rs. 56 lakh", "$7 bn"]


def test_strip_tags_removes_scripts_and_unescapes():
    assert R.strip_tags("<p>A&amp;B</p><script>var x=1;</script><style>p{}</style> C") == " A&B C"


def test_find_term_case_insensitive_with_exclusion():
    arc = {"a": ("", "Sula Vineyards grew"), "b": ("", "sula vineyards fell"), "c": ("", "other")}
    assert [s for s, _ in R.find_term("Sula Vineyards", arc, exclude_slug="a")] == ["b"]


def test_auto_terms_drops_stopword_led_and_short_spans_and_dedupes():
    out = R.auto_terms("The Big Co said. Sula Vineyards and Grover Zampa met. sula vineyards? Sula Vineyards again. Ab Cd")
    assert "Sula Vineyards" in out and "Grover Zampa" in out
    assert not any(t.startswith("The ") for t in out) and "Ab Cd" not in out
