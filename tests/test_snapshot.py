"""tools/snapshot.py: pure helpers, and main() against a fake Blogger API in a temporary archive (git is faked)."""

import json
import sys

import blogger_api as B
import pytest
import snapshot as S

# ---------------------------------- helpers ----------------------------------


@pytest.mark.parametrize(
    "ts,want",
    [
        ("2026-01-02T03:04:05+05:30", "2026-01-02T03:04:05.000+05:30"),
        ("2026-01-02T03:04:05-08:00", "2026-01-02T03:04:05.000-08:00"),
        ("2026-01-02T03:04:05.123+05:30", "2026-01-02T03:04:05.123+05:30"),
        ("garbage", "garbage"),
    ],
)
def test_with_ms(ts, want):
    assert S._with_ms(ts) == want


def test_slug_of():
    assert S.slug_of("https://w.blogspot.com/2026/01/the-wine.html") == "the-wine"
    assert S.slug_of("https://w.blogspot.com/") == "index"
    assert S.slug_of("https://w.blogspot.com/p/page.html?m=1") == "page"


def _entry(pid="1", url="https://w/2026/a.html", content="<p>x</p>", labels=("b", "a")):
    return {
        "id": {"$t": f"tag:blogger.com,1999:blog.B.post-{pid}"},
        "link": [{"rel": "self", "href": "no"}, {"rel": "alternate", "href": url}],
        "content": {"$t": content},
        "title": {"$t": "T"},
        "published": {"$t": "2026-01-01T00:00:00.000+05:30"},
        "updated": {"$t": "2026-01-02T00:00:00.000+05:30"},
        "category": [{"term": t} for t in labels],
    }


def test_parse():
    rec, body = S.parse(_entry(content="héllo"))
    assert (
        body == "héllo"
        and rec["id"] == "1"
        and rec["slug"] == "a"
        and rec["url"] == "https://w/2026/a.html"
    )
    assert (
        rec["labels"] == ["a", "b"] and rec["bytes"] == 5 and len(rec["sha256"]) == 64
    )
    e = _entry()
    del e["content"]
    assert S.parse(e)[1] == "" and S.parse(e)[0]["bytes"] == 0


def test_markers_each_flag():
    body = (
        "gs-light-lock gs-dark-panel-guard gs-typo-unify google_translate_element article-index-start-here "
        "gs-ai-disclosure gs-revision-v1 @media not all and (prefers-color-scheme:dark)"
    )
    assert all(v for k, v in S.markers(body).items() if k != "disclaimer_in_script")
    assert not any(S.markers("<p>plain</p>").values())
    assert S.markers("<script>AI Disclosure</script>")["disclaimer_in_script"] is True
    assert (
        S.markers("<p>AI Disclosure</p><script>x</script>")["disclaimer_in_script"]
        is False
    )
    assert set(S.markers("")) == set(S.MARKERS)


def test_marker_delta():
    rec = {"markers": {"a": True, "b": False, "c": True}}
    assert S.marker_delta({"markers": {"a": True, "b": True}}, rec) == {
        "b": [True, False],
        "c": [None, True],
    }
    assert S.marker_delta({}, {"markers": {"a": False}}) == {"a": [None, False]}
    assert S.marker_delta({"markers": {"a": True}}, {"markers": {"a": True}}) == {}


def test_visible_text_and_headings():
    h = (
        '<p data-gs-revision-v1 x>rev 1.2.3</p><div id="changelog"><p>old 9</p></div><script>var a=1;</script>'
        "<style>p{}</style><h2>One &amp; Two</h2><p>Body   text</p><H3 class=x>Three</H3>"
    )
    assert S.visible_text(h) == "One & Two Body text Three"
    assert S.headings(h) == ["One &amp; Two", "Three"]
    assert S.headings("<h5>no</h5>") == []


@pytest.mark.parametrize(
    "old,new,level,why",
    [
        (None, "x", None, "no previous body on disk"),
        ("<p>a</p>", "<p>a</p>", None, "edited in place"),
        ("<p>a b</p>", '<p class="z">a b</p>', "patch", "visible text identical"),
        (
            "<p>Price 10</p>",
            "<p>Price 12</p>",
            "major",
            "figures changed (+1/-1 distinct numbers)",
        ),
        (
            "<h2>A</h2><p>t</p><h2>B</h2>",
            "<h2>A</h2><p>tt</p>",
            "major",
            "section(s) removed: B",
        ),
        (
            "<h2>A</h2><p>t</p>",
            "<h2>A</h2><h2>New</h2><p>t</p>",
            "minor",
            "section(s) added: New",
        ),
        (
            "<p>" + "word " * 100 + "</p>",
            "<p>" + "word " * 110 + "</p>",
            "minor",
            "prose grew",
        ),
        (
            "<p>" + "word " * 100 + "</p>",
            "<p>" + "term " * 100 + "</p>",
            "patch",
            "wording changed",
        ),
        ("<p>" + "w" * 100 + "</p>", "<p>" + "x" * 103 + "</p>", "minor", "prose grew"),
        (
            "<p>" + "w" * 100 + "</p>",
            "<p>" + "x" * 102 + "</p>",
            "patch",
            "wording changed",
        ),  # 2% growth is not enough
    ],
)
def test_classify(old, new, level, why):
    got_level, reason = S.classify(old, new)
    assert got_level == level and why in reason


def test_classify_numbers_are_compared_as_multisets_of_distinct_strings():
    assert (
        S.classify("<p>1,000 and 2.5</p>", "<p>2.5 and 1,000</p>")[0] == "patch"
    )  # reordered, same numbers
    lvl, why = S.classify("<p>a 1 2</p>", "<p>a 1 2 3</p>")
    assert lvl == "major" and "+1/-0" in why


@pytest.mark.parametrize(
    "v,level,want",
    [
        ("1.2.3", "major", "2.0.0"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "patch", "1.2.4"),
        ("1.2.3", "other", "1.2.4"),
    ],
)
def test_bump(v, level, want):
    assert S.bump(v, level) == want


# ---------------------------------- archive fixture ----------------------------------


@pytest.fixture()
def arch(monkeypatch, tmp_path):
    for name, sub in (
        ("ROOT", ""),
        ("POSTS", "posts"),
        ("META", "meta"),
        ("MANIFEST", "meta/manifest.json"),
        ("CHANGELOG", "CHANGELOG.md"),
    ):
        monkeypatch.setattr(S, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(B, "BLOG_ID", "B")
    git = []

    def fake_run(cmd, **k):
        git.append(cmd)
        out = ""
        if cmd[:2] == ["git", "rev-parse"]:
            out = "main\n"
        if cmd[:2] == ["git", "rev-list"]:
            out = getattr(fake_run, "behind", "0") + "\n"
        rc = getattr(fake_run, "pull_rc", 0) if cmd[:2] == ["git", "pull"] else 0
        return type("R", (), {"stdout": out, "stderr": "ERR", "returncode": rc})()

    monkeypatch.setattr(S.subprocess, "run", fake_run)
    state = {"items": [], "calls": []}

    def fake_api(path, data=None, method="GET"):
        state["calls"].append(path)
        pages = state["items"]
        i = len(state["calls"]) - 1
        page = pages[i] if i < len(pages) else []
        out = {"items": page}
        if i + 1 < len(pages):
            out["nextPageToken"] = f"T{i + 1}"
        return out

    monkeypatch.setattr(B, "api", fake_api)
    state["git"] = git
    state["run"] = fake_run
    state["root"] = tmp_path
    return state


def post(
    pid="1",
    slug="a",
    content="<p>x</p>",
    published="2026-01-01T00:00:00+05:30",
    labels=None,
):
    return {
        "id": pid,
        "url": f"https://w/2026/{slug}.html",
        "content": content,
        "title": f"T{pid}",
        "published": published,
        "updated": "2026-01-02T00:00:00+05:30",
        "labels": labels or [],
    }


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["snapshot.py", *argv])
    S.main()


def test_all_entries_paginates_and_reshapes(arch):
    arch["items"] = [[post("1", "a", labels=["L"])], [post("2", "b")], []]
    entries = S.all_entries()
    assert [e["id"]["$t"] for e in entries] == [
        "tag:blogger.com,1999:blog.B.post-1",
        "tag:blogger.com,1999:blog.B.post-2",
    ]
    assert entries[0]["published"]["$t"] == "2026-01-01T00:00:00.000+05:30"
    assert entries[0]["category"] == [{"term": "L"}] and entries[0]["link"] == [
        {"rel": "alternate", "href": "https://w/2026/a.html"}
    ]
    assert "pageToken" not in arch["calls"][0] and "pageToken=T1" in arch["calls"][1]
    assert (
        "fetchBodies=true" in arch["calls"][0] and "maxResults=100" in arch["calls"][0]
    )
    arch["calls"].clear()
    arch["items"] = [[]]
    assert S.all_entries() == []


def test_load_manifest(arch):
    assert S.load_manifest() == {"posts": {}}
    S.META.mkdir()
    S.MANIFEST.write_text('{"posts": {"1": {}}}')
    assert S.load_manifest() == {"posts": {"1": {}}}


def test_main_refuses_empty_feed(arch, monkeypatch):
    arch["items"] = [[]]
    with pytest.raises(SystemExit, match="refusing to snapshot"):
        run_main(monkeypatch)


def test_first_snapshot_writes_files_versions_and_commits(arch, monkeypatch, capsys):
    arch["items"] = [
        [post("1", "a", "<p>one</p>", labels=["x"]), post("2", "b", "<p>two</p>")]
    ]
    run_main(monkeypatch, "--note", "first run")
    root = arch["root"]
    assert (root / "posts" / "a.html").read_text() == "<p>one</p>"
    side = json.loads((root / "meta" / "a.json").read_text())
    assert (
        side["version"] == "1.0.0"
        and side["version_history"][0]["level"] == "initial"
        and side["version_history"][0]["note"] == "first run"
    )
    man = json.loads((root / "meta" / "manifest.json").read_text())
    assert man["count"] == 2 and list(man["posts"]) == ["1", "2"]
    log = (root / "CHANGELOG.md").read_text()
    assert (
        log.startswith("# Change log")
        and "**Added**" in log
        and "`a` **v1.0.0** — T1 (10 bytes)" in log
        and "first run" in log
    )
    out = capsys.readouterr().out
    assert "2 posts | +2 added | ~0 changed" in out and "  +  a" in out
    cmds = [c[:2] for c in arch["git"]]
    assert cmds[0] == ["git", "add"] and cmds[1] == ["git", "commit"]
    assert "+2 ~0 ->0 -0" in arch["git"][1][3] and arch["git"][1][3].endswith(
        "\n\nfirst run"
    )


def test_unchanged_resnapshot_leaves_no_trace(arch, monkeypatch, capsys):
    arch["items"] = [[post("1", "a")]]
    run_main(monkeypatch, "--no-commit")
    before = (arch["root"] / "CHANGELOG.md").read_text()
    arch["calls"].clear()
    arch["git"].clear()
    run_main(monkeypatch)
    assert "no change since the last snapshot" in capsys.readouterr().out
    assert (arch["root"] / "CHANGELOG.md").read_text() == before and arch["git"] == []
    arch["calls"].clear()
    run_main(monkeypatch, "--allow-empty", "--no-commit")
    assert "No changes." in (arch["root"] / "CHANGELOG.md").read_text()


def test_changed_body_bumps_version_with_marker_delta(arch, monkeypatch, capsys):
    arch["items"] = [[post("1", "a", "<h2>S</h2><p>price 10</p>")]]
    run_main(monkeypatch, "--no-commit")
    arch["calls"].clear()
    arch["items"] = [[post("1", "a", "<h2>S</h2><p>price 12</p> gs-light-lock")]]
    run_main(monkeypatch, "--no-commit")
    side = json.loads((arch["root"] / "meta" / "a.json").read_text())
    assert (
        side["version"] == "2.0.0" and side["version_history"][-1]["level"] == "major"
    )
    assert side["version_history"][-1]["markers_changed"] == {
        "light_lock": [False, True]
    }
    assert (
        side["version_history"][-1]["bytes"][0]
        < side["version_history"][-1]["bytes"][1]
    )
    log = (arch["root"] / "CHANGELOG.md").read_text()
    assert (
        "**Versions**" in log
        and "**1.0.0 → 2.0.0** (MAJOR)" in log
        and "markers light_lock False→True" in log
    )
    out = capsys.readouterr().out
    assert "v a: 1.0.0 -> 2.0.0 (major)" in out and "  ~  a" in out


def test_force_level_and_unclassifiable_edit(arch, monkeypatch):
    arch["items"] = [[post("1", "a", "<p>one</p>")]]
    run_main(monkeypatch, "--no-commit")
    arch["calls"].clear()
    arch["items"] = [[post("1", "a", "<p>two</p>")]]
    run_main(monkeypatch, "--no-commit", "--as", "minor")
    h = json.loads((arch["root"] / "meta" / "a.json").read_text())["version_history"][
        -1
    ]
    assert h["level"] == "minor" and h["reason"].startswith("forced via --as minor (")
    # file edited in place so posts/ already holds the new body: classify cannot compare -> defaults to patch
    (arch["root"] / "posts" / "a.html").write_text("<p>three</p>")
    arch["calls"].clear()
    arch["items"] = [[post("1", "a", "<p>three</p>")]]
    run_main(monkeypatch, "--no-commit")
    h = json.loads((arch["root"] / "meta" / "a.json").read_text())["version_history"][
        -1
    ]
    assert h["level"] == "patch" and h["reason"].startswith(
        "UNCLASSIFIED, defaulted to patch:"
    )


def test_moved_url_redate_removed_and_baseline(arch, monkeypatch, capsys):
    arch["items"] = [[post("1", "a"), post("2", "b"), post("3", "c")]]
    run_main(monkeypatch, "--no-commit")
    man = json.loads((arch["root"] / "meta" / "manifest.json").read_text())
    man["posts"]["3"].pop("version_history")  # predates versioning
    man["posts"]["3"].pop("version")
    (arch["root"] / "meta" / "manifest.json").write_text(json.dumps(man))
    arch["calls"].clear()
    arch["items"] = [
        [post("1", "a2"), post("2", "b", published="2026-03-03T00:00:00+05:30")]
    ]
    run_main(monkeypatch, "--no-commit")
    log = (arch["root"] / "CHANGELOG.md").read_text()
    assert "**Moved**" in log and "`a` → `a2`" in log
    assert "**Re-dated**" in log and "`b` — 2026-01-01 &rarr; 2026-03-03" in log
    assert "**No longer published**" in log and "`c` — T3" in log
    assert (
        arch["root"] / "posts" / "c.html"
    ).exists()  # the archive keeps removed posts
    one = json.loads((arch["root"] / "meta" / "a2.json").read_text())
    assert (
        one["version"] == "1.0.1"
        and one["version_history"][-1]["reason"] == "moved to a new URL; body unchanged"
    )
    assert one["version_history"][-1]["url_changed"] == [
        "https://w/2026/a.html",
        "https://w/2026/a2.html",
    ]
    two = json.loads((arch["root"] / "meta" / "b.json").read_text())
    assert (
        two["version"] == "1.0.0" and len(two["version_history"]) == 1
    )  # re-dating alone does not bump
    out = capsys.readouterr().out
    assert (
        "  d  b: 2026-01-01 -> 2026-03-03" in out
        and "  -  c" in out
        and "  -> a => a2" in out
    )


def test_baseline_history_added_for_unversioned_post(arch, monkeypatch):
    arch["items"] = [[post("1", "a")]]
    run_main(monkeypatch, "--no-commit")
    man = json.loads((arch["root"] / "meta" / "manifest.json").read_text())
    man["posts"]["1"].pop("version_history")
    (arch["root"] / "meta" / "manifest.json").write_text(json.dumps(man))
    arch["calls"].clear()
    arch["items"] = [[post("1", "a", "<p>y</p>")]]
    run_main(monkeypatch, "--no-commit")
    hist = json.loads((arch["root"] / "meta" / "a.json").read_text())["version_history"]
    assert [h["level"] for h in hist] == ["baseline", "patch"] and hist[0][
        "version"
    ] == "1.0.0"


def test_duplicate_ids_and_shared_slugs(arch, monkeypatch, capsys):
    arch["items"] = [
        [post("1", "same"), post("1", "dupe"), post("2", "same"), post("3", "same")]
    ]
    run_main(monkeypatch, "--no-commit")
    out = capsys.readouterr().out
    assert "post 1 twice" in out and "slug 'same' used by posts 1 and 2" in out
    assert (arch["root"] / "posts" / "same.html").exists() and (
        arch["root"] / "posts" / "same--2.html"
    ).exists()
    assert (
        json.loads((arch["root"] / "meta" / "same--2.json").read_text())["slug"]
        == "same--2"
    )
    assert (arch["root"] / "posts" / "same--3.html").exists()


def test_commit_message_with_bumps_and_push_paths(arch, monkeypatch, capsys):
    arch["items"] = [[post("1", "a", "<p>1</p>")]]
    run_main(monkeypatch, "--no-commit")
    arch["calls"].clear()
    arch["git"].clear()
    arch["items"] = [[post("1", "a", "<p>2</p>")]]
    run_main(monkeypatch, "--push")
    msgs = [c for c in arch["git"] if c[:2] == ["git", "commit"]]
    assert msgs[0][3].endswith("| v1 bumped")
    cmds = [c[1] for c in arch["git"]]
    assert cmds == ["add", "commit", "fetch", "rev-parse", "rev-list", "push"]
    assert arch["git"][4][-1] == "HEAD..origin/main" and arch["git"][5] == [
        "git",
        "push",
        "origin",
        "HEAD",
    ]
    assert "ERR" in capsys.readouterr().out


def test_push_rebases_when_behind_and_aborts_on_conflict(arch, monkeypatch, capsys):
    arch["items"] = [[post("1", "a")]]
    arch["run"].behind = "3"
    run_main(monkeypatch, "--push")
    cmds = [c[1] for c in arch["git"]]
    assert cmds == ["add", "commit", "fetch", "rev-parse", "rev-list", "pull", "push"]
    assert "remote moved on by 3 commit(s) — rebasing first" in capsys.readouterr().out
    arch["git"].clear()
    arch["calls"].clear()
    arch["items"] = [[post("1", "a", "<p>z</p>")]]
    arch["run"].pull_rc = 1
    run_main(monkeypatch, "--push")
    assert [c[1] for c in arch["git"]][-2:] == ["pull", "rebase"] and arch["git"][
        -1
    ] == ["git", "rebase", "--abort"]
    assert "nothing was pushed" in capsys.readouterr().out


def test_classify_counts_repeated_numbers(arch):
    # same distinct numbers, but one value now appears once instead of twice: the figures changed
    assert S.classify("<p>5 and 5</p>", "<p>5</p>")[0] == "major"


def test_redate_is_not_reported_when_the_body_also_changed(arch, monkeypatch):
    arch["items"] = [[post("1", "a", "<p>one</p>")]]
    run_main(monkeypatch, "--no-commit")
    arch["calls"].clear()
    arch["items"] = [
        [post("1", "a", "<p>two</p>", published="2026-05-05T00:00:00+05:30")]
    ]
    run_main(monkeypatch, "--no-commit")
    log = (arch["root"] / "CHANGELOG.md").read_text()
    assert "**Changed**" in log and "**Re-dated**" not in log


def test_a_lone_redate_still_writes_a_changelog_entry_and_commits(
    arch, monkeypatch, capsys
):
    arch["items"] = [[post("1", "a")]]
    run_main(monkeypatch, "--no-commit")
    arch["calls"].clear()
    arch["git"].clear()
    arch["items"] = [[post("1", "a", published="2026-02-02T00:00:00+05:30")]]
    run_main(monkeypatch)
    log = (arch["root"] / "CHANGELOG.md").read_text()
    assert (
        "**Re-dated**" in log
        and "no change since the last snapshot" not in capsys.readouterr().out
    )
    assert [c[1] for c in arch["git"]][:2] == ["add", "commit"]
