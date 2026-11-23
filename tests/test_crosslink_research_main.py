"""main() entry points of crosslink_analysis and research_crosscheck against tiny synthetic archives."""

import json
import sys

import pytest

import crosslink_analysis as X
import research_crosscheck as R

BLOG = "https://wine-test.blogspot.com"


def link(slug, ym="2026/05"):
    return f'<a href="{BLOG}/{ym}/{slug}.html">x</a>'


# ---------------------------------- crosslink_analysis ----------------------------------


@pytest.fixture()
def site(monkeypatch, tmp_path):
    monkeypatch.setattr(X, "ROOT", tmp_path)
    (tmp_path / "posts").mkdir()
    (tmp_path / "meta").mkdir()
    return tmp_path


def make_site(root, posts, labels=None):
    """posts: slug -> html body; labels: slug -> [labels]."""
    manifest = {}
    for i, (slug, body) in enumerate(posts.items()):
        (root / "posts" / f"{slug}.html").write_text(body, encoding="utf-8")
        manifest[str(i)] = {"slug": slug, "labels": (labels or {}).get(slug, [])}
    (root / "meta" / "manifest.json").write_text(json.dumps({"posts": manifest}))


def run_x(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["crosslink_analysis.py", *argv])
    return X.main()


def test_blog_url_comes_from_env_when_blogger_api_unconfigured():
    assert X._BLOG_URL == BLOG


def test_strip_and_extract_outbound(tmp_path):
    assert X.strip_tags_scripts("a<script>x</script>b<STYLE>y</STYLE>c").split() == ["a", "b", "c"]
    p = tmp_path / "p.html"
    p.write_text(
        link("Other-Post") + link("self") + link("article-index-start-here") + link("under_score_1")
        + f'<script>{link("in-script")}</script>' + f'<a href="{BLOG}/2026/05/with-frag.html#top">f</a>'
        + '<a href="https://elsewhere.com/2026/05/foreign.html">no</a>' + link("bad-month", ym="2026/5"),
        encoding="utf-8",
    )
    assert X.extract_outbound(p, "self") == {"other-post", "under_score_1", "with-frag"}


def test_load_manifest_keys_by_slug(site):
    make_site(site, {"a": "", "b": ""})
    assert set(X.load_manifest()) == {"a", "b"}


def test_build_graph_counts_only_live_slugs(site):
    make_site(site, {"a": link("b") + link("ghost"), "b": ""})
    (site / "posts" / "archived-only.html").write_text(link("a"), encoding="utf-8")
    assert X.build_graph(X.load_manifest()) == {"a": {"b"}, "b": set()}


def test_main_json_report(site, monkeypatch, capsys):
    posts = {"a": link("b") + link("c"), "b": link("a"), "c": "", "d": "", "e": link("a")}
    labels = {s: ["Rare"] for s in "abcd"}
    labels["e"] = ["Rare"]
    make_site(site, posts, labels)
    out = site / "r.json"
    assert run_x(monkeypatch, "--json", str(out), "--min-label-cluster", "3") == 0
    assert capsys.readouterr().out == f"wrote {out}\n"
    rep = json.loads(out.read_text())
    assert rep["posts_considered"] == 5 and rep["total_content_link_edges"] == 4 and rep["distinct_linked_pairs"] == 3
    assert rep["orphans_both_directions"] == ["d"]
    assert rep["orphans_no_outbound_only"] == ["c"]
    assert rep["orphans_no_inbound_only"] == ["e"]
    assert rep["top_out_degree"][0] == ["a", 2] and rep["top_in_degree"][0] == ["a", 2]
    pairs = {tuple(c["slugs"]): c for c in rep["candidate_pairs"]}
    assert ("b", "c") in pairs and ("a", "b") not in pairs                    # linked pairs are not candidates
    assert pairs[("b", "c")]["shared_labels"] == ["Rare"] and pairs[("b", "c")]["score"] == 0.2


def test_label_cluster_size_window_and_multi_label_scoring(site, monkeypatch):
    posts = {s: "" for s in ("a", "b", "c", "d")}
    make_site(site, posts, {"a": ["X", "Y"], "b": ["X", "Y"], "c": ["X", "Y"], "d": ["X"]})
    out = site / "r.json"
    run_x(monkeypatch, "--json", str(out), "--min-label-cluster", "3")
    rep = json.loads(out.read_text())
    top = rep["candidate_pairs"][0]
    assert top["slugs"] in (["a", "b"], ["a", "c"], ["b", "c"]) and sorted(top["shared_labels"]) == ["X", "Y"]
    assert top["score"] == round(1 / 4 + 1 / 3, 3)                             # two labels' weights add up
    run_x(monkeypatch, "--json", str(out), "--min-label-cluster", "5")
    assert json.loads(out.read_text())["candidate_pairs"] == []                 # clusters smaller than 5 are ignored
    run_x(monkeypatch, "--json", str(out), "--min-label-cluster", "4", "--top", "2")
    assert len(json.loads(out.read_text())["candidate_pairs"]) <= 2


def test_big_label_clusters_are_ignored(site, monkeypatch):
    posts = {f"p{i:02d}": "" for i in range(41)}
    make_site(site, posts, {s: ["Generic"] for s in posts})
    out = site / "r.json"
    run_x(monkeypatch, "--json", str(out))
    assert json.loads(out.read_text())["candidate_pairs"] == []              # > 40 posts: too generic to be a signal


def test_main_text_report(site, monkeypatch, capsys):
    posts = {"a": link("b"), "b": "", "c": "", "d": link("a")}
    make_site(site, posts, {"a": ["L"], "b": ["L"], "c": ["L"], "d": ["L"]})
    assert run_x(monkeypatch, "--top", "1") == 0
    out = capsys.readouterr().out
    assert "posts considered: 4" in out and "content cross-link edges (directed): 2" in out
    assert "distinct linked pairs: 2" in out and "average outbound content links per post: 0.50" in out
    assert "=== Orphans: zero outbound AND zero inbound content links (1) ===\n  c\n" in out
    assert "=== Zero outbound, but linked FROM elsewhere (1) ===\n  b  (in-links: 1)" in out
    assert "=== Never linked TO from any other post (1) ===\n  d  (out-links: 1)" in out
    assert "Best-connected hubs, by outbound content links (top 1)" in out
    assert "  1  a" in out and "Top candidate link opportunities" in out and "[L]" in out
    assert "... and" not in out


def test_text_report_truncates_long_lists(site, monkeypatch, capsys):
    make_site(site, {"a": "", "b": "", "c": ""})
    run_x(monkeypatch, "--top", "2")
    out = capsys.readouterr().out
    assert "(3) ===\n  a\n  b\n  ... and 1 more" in out


# ---------------------------------- research_crosscheck ----------------------------------


@pytest.fixture()
def archive(monkeypatch, tmp_path):
    posts = tmp_path / "posts"
    posts.mkdir()
    monkeypatch.setattr(R, "POSTS", posts)
    return posts


def run_r(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["research_crosscheck.py", *argv])
    return R.main()


def test_strip_tags_and_money_regexes():
    assert R.strip_tags("<p>A&amp;B</p><script>x</script> <b>c</b>").strip() == "A&B c"
    assert R.MONEY_RE.findall("Rs 1,500 cr and ₹20 lakh, $5.5bn, Rs, only") == ["Rs 1,500 cr", "₹20 lakh", "$5.5bn"]
    assert R.PCT_RE.findall("12.5% and 100% and 1234%") == ["12.5%", "100%"]
    assert R.is_specific_enough("12%") and not R.is_specific_enough("1%") and not R.is_specific_enough("₹0")


def test_load_archive_and_find_term(archive):
    (archive / "a.html").write_text("<p>Sula Vineyards in Nashik</p>", encoding="utf-8")
    (archive / "b.html").write_text("<p>nothing</p>", encoding="utf-8")
    arch = R.load_archive()
    assert set(arch) == {"a", "b"} and arch["a"][0].startswith("<p>")
    hits = R.find_term("SULA", arch)
    assert [h[0] for h in hits] == ["a"] and "Sula Vineyards" in hits[0][1]
    assert R.find_term("sula", arch, exclude_slug="a") == []
    ctx = R.find_term("x", {"s": ("", "y" * 100 + "x" + "z" * 100)})[0][1]
    assert ctx == "y" * 60 + "x" + "z" * 60


def test_auto_terms_filters_and_caps():
    text = "We met Grover Zampa today. Then Grover Zampa again, and the The Big Company; It Is Here; Ab Cd; Sula Vineyards"
    # duplicates collapse case-insensitively; stop-word starts ("The ...", "It ...") and spans under 6 chars are dropped
    assert R.auto_terms(text) == ["Grover Zampa", "Then Grover Zampa", "Sula Vineyards"]
    many = " | ".join(f"Alpha{i} Beta{i}" for i in range(100))
    assert len(R.auto_terms(many)) == 60


def test_main_requires_terms_and_existing_draft(archive, tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="not found"):
        run_r(monkeypatch, str(tmp_path / "nope.html"))
    d = tmp_path / "post-x.html"
    d.write_text("<p>x</p>", encoding="utf-8")
    with pytest.raises(SystemExit, match="pass --terms"):
        run_r(monkeypatch, str(d))


def test_main_terms_found_zero_and_variants(archive, tmp_path, monkeypatch, capsys):
    for i in range(5):
        (archive / f"p{i}.html").write_text("<p>Sula uses Sulphate. Sula again.</p>", encoding="utf-8")
    (archive / "draft.html").write_text("<p>Sula here</p>", encoding="utf-8")
    d = tmp_path / "post-draft.html"                                           # prefix is stripped: excludes draft.html
    d.write_text("<p>Sula and Sulfate and Newco, 50% gain, Rs 2,000 cr</p>", encoding="utf-8")
    assert run_r(monkeypatch, "--terms", " Sula , ,Sulfate,Newco ", str(d)) == 0
    out = capsys.readouterr().out
    assert "checking against 6 posts" in out and "Sula  (5 posts)" in out and "… and 2 more" in out
    assert out.count("    p") == 3 and "    p0: …" in out
    assert "Sulfate" in out.split("zero matches anywhere in posts/ ===")[1] and "Newco" in out
    assert "known variant spelling(s) to try: sulphate" in out
    assert "\n  Newco\n" in out and "\n1 terms already covered, 2 zero-match." in out


def test_main_singular_plural_and_no_found_section(archive, tmp_path, monkeypatch, capsys):
    (archive / "a.html").write_text("<p>Unique word</p>", encoding="utf-8")
    d = tmp_path / "d.html"
    d.write_text("<p>x</p>", encoding="utf-8")
    run_r(monkeypatch, "--terms", "Unique", str(d))
    assert "Unique  (1 post)" in capsys.readouterr().out
    run_r(monkeypatch, "--terms", "Missing", str(d))
    out = capsys.readouterr().out
    assert "already covered elsewhere" not in out and "0 terms already covered, 1 zero-match" in out


def test_main_numbers_cross_check(archive, tmp_path, monkeypatch, capsys):
    (archive / "a.html").write_text("<p>Growth hit 45% last year and Rs 2,000 cr invested</p>", encoding="utf-8")
    d = tmp_path / "d.html"
    d.write_text("<p>We saw 45% and 7% and Rs 2,000 cr and ₹0 here</p>", encoding="utf-8")
    run_r(monkeypatch, "--terms", "zzz", "--numbers", str(d))
    out = capsys.readouterr().out
    assert "standalone %/currency figures" in out and "45%  appears in 1 other post(s)" in out
    assert "Rs 2,000 cr  appears in 1 other post(s)" in out and "7%" not in out.split("figures in the draft")[1]
    run_r(monkeypatch, "--terms", "zzz", str(d))
    assert "standalone" not in capsys.readouterr().out


def test_main_auto_mode(archive, tmp_path, monkeypatch, capsys):
    (archive / "a.html").write_text("<p>Grover Zampa is known</p>", encoding="utf-8")
    d = tmp_path / "d.html"
    d.write_text("<p>Grover Zampa Vineyards expanded</p>", encoding="utf-8")
    run_r(monkeypatch, "--auto", str(d))
    out = capsys.readouterr().out
    assert "--auto extracted 1 candidate terms (crude" in out and "Grover Zampa Vineyards" in out
