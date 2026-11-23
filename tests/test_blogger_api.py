"""tools/blogger_api.py with the HTTP layer, curl and the clock faked: no network, no credentials, no real blog."""

import io
import json
import urllib.error

import pytest

import blogger_api as B


@pytest.fixture(autouse=True)
def fakes(monkeypatch, tmp_path):
    monkeypatch.setattr(B, "BLOG_ID", "BLOG1")
    monkeypatch.setattr(B, "BLOG_URL", "https://wine.example")
    monkeypatch.setattr(B, "SECRETS", tmp_path / "sec")
    monkeypatch.setattr(B, "CLIENT_FILE", tmp_path / "sec" / "client.json")
    monkeypatch.setattr(B, "TOKEN_FILE", tmp_path / "sec" / "token.json")
    monkeypatch.setattr(B, "_access", {"token": None, "expires": 0})
    sleeps = []
    monkeypatch.setattr(B.time, "sleep", sleeps.append)
    monkeypatch.setattr(B.random, "uniform", lambda a, b: 0.0)
    return sleeps


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body="boom"):
    return urllib.error.HTTPError("http://x", code, "m", {}, io.BytesIO(body.encode()))


# ---------------------------------- transport ----------------------------------


def test_req_encodes_json_bytes_and_empty_body(monkeypatch):
    seen = {}

    def fake_open(req, timeout):
        seen.update(url=req.full_url, data=req.data, method=req.get_method(), timeout=timeout,
                    hdr=dict(req.header_items()))
        return Resp(b"")

    monkeypatch.setattr(B.urllib.request, "urlopen", fake_open)
    assert B._req("http://u", data={"a": 1}, headers={"X-A": "1"}, method="PUT") == {}
    assert seen["data"] == b'{"a": 1}' and seen["method"] == "PUT" and seen["timeout"] == 60
    assert seen["hdr"]["X-a"] == "1"
    B._req("http://u", data=b"raw")
    assert seen["data"] == b"raw" and seen["method"] == "POST"
    B._req("http://u")
    assert seen["data"] is None and seen["method"] == "GET"


def test_req_parses_json_response(monkeypatch):
    monkeypatch.setattr(B.urllib.request, "urlopen", lambda r, timeout: Resp(b'{"k": [1]}'))
    assert B._req("http://u") == {"k": [1]}


def test_form_posts_urlencoded(monkeypatch):
    seen = {}
    monkeypatch.setattr(B, "_req", lambda url, data=None, headers=None, method=None: seen.update(
        url=url, data=data, headers=headers) or {"ok": 1})
    assert B._form("http://t", {"a": "b c", "d": "e"}) == {"ok": 1}
    assert seen["data"] == b"a=b+c&d=e" and seen["headers"] == {"Content-Type": "application/x-www-form-urlencoded"}


# ---------------------------------- auth plumbing ----------------------------------


def test_client_file_variants(tmp_path):
    with pytest.raises(SystemExit, match="missing"):
        B._client()
    B.SECRETS.mkdir()
    B.CLIENT_FILE.write_text(json.dumps({"installed": {"client_id": "i", "client_secret": "s"}}))
    assert B._client() == ("i", "s", "installed")
    B.CLIENT_FILE.write_text(json.dumps({"web": {"client_id": "w", "client_secret": "t"}}))
    assert B._client() == ("w", "t", "web")
    B.CLIENT_FILE.write_text(json.dumps({"other": {}}))
    with pytest.raises(SystemExit, match="not an OAuth client JSON"):
        B._client()


def _creds():
    B.SECRETS.mkdir(exist_ok=True)
    B.CLIENT_FILE.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}))
    B.TOKEN_FILE.write_text(json.dumps({"refresh_token": "RT"}))


def test_access_token_requires_authorisation():
    B.SECRETS.mkdir()
    B.CLIENT_FILE.write_text("{}")
    with pytest.raises(SystemExit, match="not authorised"):
        B.access_token()


def test_access_token_refreshes_and_caches(monkeypatch):
    _creds()
    calls = []
    monkeypatch.setattr(B, "_form", lambda url, f: calls.append((url, f)) or {"access_token": "AT", "expires_in": 3600})
    monkeypatch.setattr(B.time, "time", lambda: 1000.0)
    assert B.access_token() == "AT" and B.access_token() == "AT"
    assert len(calls) == 1 and calls[0][0] == "https://oauth2.googleapis.com/token"
    assert calls[0][1] == {"client_id": "cid", "client_secret": "sec", "refresh_token": "RT", "grant_type": "refresh_token"}
    assert B._access["expires"] == 1000.0 + 3600
    # expiry minus the 60 s margin triggers a refresh
    monkeypatch.setattr(B.time, "time", lambda: 1000.0 + 3600 - 59)
    B.access_token()
    assert len(calls) == 2
    monkeypatch.setattr(B.time, "time", lambda: 1000.0 + 3600 - 60 - 1)   # 61 s before expiry: still cached
    B.access_token()
    assert len(calls) == 2


def test_access_token_default_expiry(monkeypatch):
    _creds()
    monkeypatch.setattr(B, "_form", lambda url, f: {"access_token": "AT"})
    monkeypatch.setattr(B.time, "time", lambda: 0.0)
    B.access_token()
    assert B._access["expires"] == 3600


def test_access_token_cache_boundary(monkeypatch):
    _creds()
    monkeypatch.setattr(B, "_form", lambda url, f: pytest.fail("cached"))
    B._access.update(token="CACHED", expires=1000)
    monkeypatch.setattr(B.time, "time", lambda: 939.0)               # 939 < 1000 - 60
    assert B.access_token() == "CACHED"


# ---------------------------------- api() with backoff ----------------------------------


def test_api_sends_bearer_and_json_headers(monkeypatch):
    seen = {}
    monkeypatch.setattr(B, "access_token", lambda: "TKN")
    monkeypatch.setattr(B, "_req", lambda url, data=None, method=None, headers=None: seen.update(
        url=url, data=data, method=method, headers=headers) or {"ok": True})
    assert B.api("/x", data={"a": 1}, method="PUT") == {"ok": True}
    assert seen == {"url": "https://blogger.googleapis.com/v3/x", "data": {"a": 1}, "method": "PUT",
                    "headers": {"Authorization": "Bearer TKN", "Content-Type": "application/json"}}


def test_api_retries_transient_codes_with_doubling_delay(monkeypatch, fakes):
    monkeypatch.setattr(B, "access_token", lambda: "T")
    seq = [http_error(429), http_error(500), http_error(503), {"done": 1}]

    def fake(url, **k):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(B, "_req", fake)
    assert B.api("/p") == {"done": 1}
    assert fakes == [1.0, 2.0, 4.0]


def test_api_403_exhausts_backoff_into_ceiling(monkeypatch, fakes, capsys):
    monkeypatch.setattr(B, "access_token", lambda: "T")
    monkeypatch.setattr(B, "_req", lambda url, **k: (_ for _ in ()).throw(http_error(403, "quota")))
    with pytest.raises(B.Ceiling, match="403 quota"):
        B.api("/p")
    assert fakes == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]          # delay <= MAX_BACKOFF_SECONDS (64) inclusive
    assert "403 — backing off 1.0s" in capsys.readouterr().out


def test_api_non_retryable_error_exits_with_detail(monkeypatch):
    monkeypatch.setattr(B, "access_token", lambda: "T")
    monkeypatch.setattr(B, "_req", lambda url, **k: (_ for _ in ()).throw(http_error(404, "nope" * 100)))
    with pytest.raises(SystemExit) as e:
        B.api("/p")
    assert str(e.value) == "HTTP 404: " + ("nope" * 100)[:300]


# ---------------------------------- find_post / whoami / get ----------------------------------


def _pages(*pages):
    pages = list(pages)
    calls = []

    def fake(path, data=None, method="GET"):
        calls.append(path)
        return pages.pop(0)
    return fake, calls


def test_find_post_paginates_and_matches_exact_slug(monkeypatch):
    fake, calls = _pages(
        {"items": [{"url": "https://w/2026/a-wine.html", "id": "1"}], "nextPageToken": "TOK"},
        {"items": [{"url": "https://w/2026/wine.html", "id": "2"}]})
    monkeypatch.setattr(B, "api", fake)
    assert B.find_post("wine")["id"] == "2"
    assert "pageToken" not in calls[0] and "pageToken=TOK" in calls[1]
    assert "maxResults=100" in calls[0] and "fetchBodies=false" in calls[0] and "status=LIVE" in calls[0]
    assert calls[0].startswith("/blogs/BLOG1/posts?")


def test_find_post_missing_exits_with_count(monkeypatch):
    fake, _ = _pages({"items": [{"url": "u/a.html"}, {"url": "u/b.html"}]}, )
    monkeypatch.setattr(B, "api", fake)
    with pytest.raises(SystemExit, match=r"no live post with slug 'z' \(searched 2\)"):
        B.find_post("z")
    fake2, _ = _pages({})
    monkeypatch.setattr(B, "api", fake2)
    with pytest.raises(SystemExit, match="searched 0"):
        B.find_post("z")


def test_whoami_output(monkeypatch, capsys):
    monkeypatch.setattr(B, "api", lambda p, **k: {"name": "Wine", "url": "https://w", "posts": {"totalItems": 7}})
    B.cmd_whoami(None)
    out = capsys.readouterr().out
    assert "blog   : Wine" in out and "posts  : 7   pages: ?" in out and "auth   : OK" in out
    monkeypatch.setattr(B, "api", lambda p, **k: {"name": "W", "url": "u", "posts": {"totalItems": 1}, "pages": {"totalItems": 3}})
    B.cmd_whoami(None)
    assert "pages: 3" in capsys.readouterr().out


def test_cmd_get_prints_and_writes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(B, "find_post", lambda slug: {"id": "9"})
    monkeypatch.setattr(B, "api", lambda p, **k: {"id": "9", "title": "T", "url": "U", "updated": "UP", "content": "é" * 1500})
    out_file = tmp_path / "o.html"
    B.cmd_get(type("A", (), {"slug": "s", "out": str(out_file)})())
    out = capsys.readouterr().out
    assert "bytes  : 1,500" in out and f"wrote  : {out_file}" in out and out_file.read_text(encoding="utf-8") == "é" * 1500
    B.cmd_get(type("A", (), {"slug": "s", "out": None})())
    assert "wrote" not in capsys.readouterr().out


# ---------------------------------- flatten / verify ----------------------------------


def test_flatten_html_strips_scripts_tags_entities_and_limits():
    raw = "<p>Cr&eacute;mant&nbsp;&amp; <b>Co</b></p><script>var x='<b>no</b>';</script><STYLE>p{}</STYLE>  tail"
    # whitespace is collapsed before entities are unescaped, so an encoded nbsp stays a literal U+00A0
    assert B._flatten_html(raw) == "Crémant\xa0& Co tail"
    assert B._flatten_html(raw, limit=5) == "Crémant\xa0& Co tail"[:5]
    assert B._flatten_html("<p>abcdef</p>", limit=3) == "abc"
    assert B._flatten_html("<p>abc</p>", limit=0) == "abc"            # a limit of 0 means no limit


def _curl(monkeypatch, stdout):
    seen = {}

    def fake_run(cmd, capture_output, text, check):
        seen.update(cmd=cmd, capture=capture_output, text=text, check=check)
        return type("R", (), {"stdout": stdout})()
    monkeypatch.setattr(B.subprocess, "run", fake_run)
    monkeypatch.setattr(B.secrets, "randbelow", lambda n: 42)
    return seen


def test_verify_live_cases(monkeypatch):
    seen = _curl(monkeypatch, "")
    assert B.verify_live("http://p") == (False, "empty response")
    assert seen["cmd"] == ["curl", "-s", "-A", "Mozilla/5.0", "http://p?cb=42"] and seen["check"] is False
    _curl(monkeypatch, "<p>Hello &amp; welcome</p>")
    assert B.verify_live("u") == (True, "26 bytes")
    assert B.verify_live("u", needle="Hello & welcome") == (True, "26 bytes")        # found via the flattened text
    assert B.verify_live("u", needle="Hello &amp; welcome")[0] is True                # found raw
    assert B.verify_live("u", needle="missing") == (False, "expected content missing")
    assert B.verify_live("u", absent="welcome") == (False, "content that should be gone is still present")
    assert B.verify_live("u", absent="Hello & welcome")[0] is False                   # flattened absent check
    assert B.verify_live("u", needle="Hello", absent="nothere")[0] is True


def test_verify_stored():
    ok, why = B.verify_stored("<p>The Wine &amp; more</p>", "The Wine & more")
    assert ok and why == "via API, public fetch unavailable from this host (26 bytes stored)"
    assert B.verify_stored("<p>other</p>", "The Wine") == (False, "stored content does not contain the expected text")
    assert B.verify_stored(None, "x")[0] is False and B.verify_stored(None, "")[0] is True


# ---------------------------------- do_update ----------------------------------


def test_do_update_dry_run_never_calls_api(monkeypatch, capsys):
    monkeypatch.setattr(B, "api", lambda *a, **k: pytest.fail("no API in a dry run"))
    assert B.do_update("1", "u", "t", "b" * 2000, dry_run=True) is True
    assert "DRY RUN — would PUT 2,000 bytes" in capsys.readouterr().out


def test_do_update_payload_preserves_published_and_labels(monkeypatch, capsys, fakes):
    calls = []
    monkeypatch.setattr(B, "api", lambda path, data=None, method="GET": calls.append((path, data, method)) or {})
    monkeypatch.setattr(B, "verify_live", lambda url, needle=None, absent=None: (True, "ok"))
    assert B.do_update("P1", "http://u", "Title", "<p>Body</p>", published="2026-01-01", labels=["L"]) is True
    assert calls == [("/blogs/BLOG1/posts/P1", {"id": "P1", "title": "Title", "content": "<p>Body</p>",
                                                "published": "2026-01-01", "labels": ["L"]}, "PUT")]
    assert fakes == [3] and "    verified: ok" in capsys.readouterr().out
    calls.clear()
    B.do_update("P1", "u", "T", "b")
    assert calls[0][1] == {"id": "P1", "title": "T", "content": "b"}           # absent published/labels are omitted


def test_do_update_probe_is_first_40_flattened_chars(monkeypatch):
    seen = {}
    monkeypatch.setattr(B, "api", lambda *a, **k: {})
    monkeypatch.setattr(B, "verify_live", lambda url, needle=None, absent=None: seen.update(n=needle) or (True, "ok"))
    B.do_update("1", "u", "t", "<p>" + "x" * 100 + "</p>")
    assert seen["n"] == "x" * 40


def test_do_update_falls_back_to_stored_content_only_on_empty_response(monkeypatch, capsys):
    gets = []

    def fake_api(path, data=None, method="GET"):
        gets.append(method)
        return {"content": "<p>hello world</p>"}
    monkeypatch.setattr(B, "api", fake_api)
    monkeypatch.setattr(B, "verify_live", lambda *a, **k: (False, "empty response"))
    assert B.do_update("1", "u", "t", "hello world") is True and gets == ["PUT", "GET"]
    assert "verified: via API" in capsys.readouterr().out
    gets.clear()
    monkeypatch.setattr(B, "verify_live", lambda *a, **k: (False, "expected content missing"))
    assert B.do_update("1", "u", "t", "hello world") is False and gets == ["PUT"]
    assert "WROTE BUT VERIFY FAILED: expected content missing" in capsys.readouterr().out


# ---------------------------------- cmd_update / delete / insert / page / pages / bulk ----------------------------------


class A:
    def __init__(self, **k):
        self.__dict__.update(dict(slug=None, id=None, title=None, file=None, dry_run=False, yes=False, draft=False,
                                  manifest=None, limit=0), **k)


def test_cmd_update_by_slug_and_title(monkeypatch, tmp_path, capsys):
    f = tmp_path / "b.html"
    f.write_text("<p>new</p>", encoding="utf-8")
    monkeypatch.setattr(B, "find_post", lambda s: {"id": "1", "url": "u", "title": "Old", "published": "P", "labels": ["x"]})
    seen = {}
    monkeypatch.setattr(B, "do_update", lambda *a, **k: seen.update(a=a, k=k) or True)
    with pytest.raises(SystemExit) as e:
        B.cmd_update(A(slug="s", file=str(f), title="New", dry_run=True))
    assert e.value.code == 0
    assert seen["a"] == ("1", "u", "New", "<p>new</p>") and seen["k"] == {"published": "P", "labels": ["x"], "dry_run": True}
    out = capsys.readouterr().out
    assert "s  id=1" in out and "title: 'Old'" in out and "-> 'New'" in out


def test_cmd_update_by_id_keeps_title_and_reports_failure(monkeypatch, tmp_path):
    f = tmp_path / "b.html"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(B, "api", lambda p, **k: {"id": "5", "url": "u", "title": "Keep"})
    seen = {}
    monkeypatch.setattr(B, "find_post", lambda s: pytest.fail("id lookup must not search by slug"))
    monkeypatch.setattr(B, "do_update", lambda *a, **k: seen.update(a=a) or False)
    with pytest.raises(SystemExit) as e:
        B.cmd_update(A(id="5", file=str(f)))
    assert e.value.code == 2 and seen["a"][2] == "Keep"


def test_cmd_update_ceiling_exits_3(monkeypatch, tmp_path, capsys):
    f = tmp_path / "b.html"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(B, "find_post", lambda s: {"id": "1", "url": "u", "title": "T"})
    monkeypatch.setattr(B, "do_update", lambda *a, **k: (_ for _ in ()).throw(B.Ceiling("403 q")))
    with pytest.raises(SystemExit) as e:
        B.cmd_update(A(slug="s", file=str(f)))
    assert e.value.code == 3 and "daily write ceiling reached: 403 q" in capsys.readouterr().out


def test_cmd_delete_requires_yes(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(B, "find_post", lambda s: {"id": "1", "title": "T", "url": "U"})
    monkeypatch.setattr(B, "api", lambda path, data=None, method="GET": calls.append((path, method)) or {})
    B.cmd_delete(A(slug="s"))
    out = capsys.readouterr().out
    assert calls == [] and "DRY RUN (default)" in out and "status: LIVE" in out and "title : T" in out
    B.cmd_delete(A(slug="s", yes=True))
    assert calls == [("/blogs/BLOG1/posts/1", "DELETE")] and "  deleted" in capsys.readouterr().out


def test_cmd_delete_by_id_uses_direct_fetch(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(B, "find_post", lambda s: pytest.fail("not by slug"))
    monkeypatch.setattr(B, "api", lambda path, data=None, method="GET": calls.append((path, method)) or {
        "id": "7", "title": "T", "url": "U", "status": "DRAFT"})
    B.cmd_delete(A(id="7"))
    assert calls == [("/blogs/BLOG1/posts/7", "GET")] and "status: DRAFT" in capsys.readouterr().out


def test_cmd_insert_dry_run_and_draft(monkeypatch, tmp_path, capsys):
    f = tmp_path / "b.html"
    f.write_text("<p>hi</p>", encoding="utf-8")
    monkeypatch.setattr(B, "api", lambda *a, **k: pytest.fail("dry run"))
    B.cmd_insert(A(file=str(f), title="T", dry_run=True, draft=True))
    out = capsys.readouterr().out
    assert "would insert 9 bytes" in out and "status: DRAFT" in out
    calls = []
    monkeypatch.setattr(B, "api", lambda path, data=None, method="GET": calls.append((path, data, method)) or {"id": "n1"})
    B.cmd_insert(A(file=str(f), title="T", draft=True))
    assert calls == [("/blogs/BLOG1/posts?isDraft=true", {"kind": "blogger#post", "title": "T", "content": "<p>hi</p>"}, "POST")]
    out = capsys.readouterr().out
    assert "created id=n1" in out and "(draft — no public URL)" in out


def test_cmd_insert_live_verify_paths(monkeypatch, tmp_path, capsys, fakes):
    f = tmp_path / "b.html"
    f.write_text("<p>hello there</p>", encoding="utf-8")
    monkeypatch.setattr(B, "api", lambda path, data=None, method="GET": {"id": "n", "url": "http://p", "content": "<p>hello there</p>"}
                        if method == "POST" else {})
    monkeypatch.setattr(B, "verify_live", lambda url, needle=None, absent=None: (True, "fine"))
    with pytest.raises(SystemExit) as e:
        B.cmd_insert(A(file=str(f), title="T"))
    assert e.value.code == 0 and fakes[-1] == 4 and "verified: fine" in capsys.readouterr().out
    monkeypatch.setattr(B, "verify_live", lambda *a, **k: (False, "empty response"))
    with pytest.raises(SystemExit) as e:
        B.cmd_insert(A(file=str(f), title="T"))
    assert e.value.code == 0 and "verified: via API" in capsys.readouterr().out
    monkeypatch.setattr(B, "verify_live", lambda *a, **k: (False, "expected content missing"))
    with pytest.raises(SystemExit) as e:
        B.cmd_insert(A(file=str(f), title="T"))
    assert e.value.code == 2 and "PUBLISHED BUT VERIFY FAILED" in capsys.readouterr().out
    monkeypatch.setattr(B, "api", lambda *a, **k: {"id": "n"})              # no url returned: nothing to verify
    B.cmd_insert(A(file=str(f), title="T"))


def test_cmd_insert_ceiling(monkeypatch, tmp_path, capsys):
    f = tmp_path / "b.html"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(B, "api", lambda *a, **k: (_ for _ in ()).throw(B.Ceiling("q")))
    with pytest.raises(SystemExit) as e:
        B.cmd_insert(A(file=str(f), title="T"))
    assert e.value.code == 3


def test_cmd_page_create_update_and_dry_run(monkeypatch, tmp_path, capsys, fakes):
    f = tmp_path / "p.html"
    f.write_text("<p>page body</p>", encoding="utf-8")
    B.cmd_page(A(file=str(f), title="T", dry_run=True))
    assert "would create page 16 bytes" in capsys.readouterr().out
    B.cmd_page(A(file=str(f), title="T", dry_run=True, id="9"))
    assert "would update page" in capsys.readouterr().out
    calls = []
    monkeypatch.setattr(B, "api", lambda path, data=None, method="GET": calls.append((path, data, method)) or {"id": "9", "url": "http://p"})
    monkeypatch.setattr(B, "verify_live", lambda url, needle=None, absent=None: (True, "ok"))
    B.cmd_page(A(file=str(f), title="T", id="9"))
    assert calls[0] == ("/blogs/BLOG1/pages/9", {"kind": "blogger#page", "title": "T", "content": "<p>page body</p>", "id": "9"}, "PUT")
    B.cmd_page(A(file=str(f), title="T"))
    assert calls[1][0] == "/blogs/BLOG1/pages" and calls[1][2] == "POST" and "id" not in calls[1][1]
    assert "page id=9" in capsys.readouterr().out
    monkeypatch.setattr(B, "api", lambda *a, **k: {"id": "9"})
    B.cmd_page(A(file=str(f), title="T"))
    assert "(draft)" in capsys.readouterr().out
    monkeypatch.setattr(B, "api", lambda *a, **k: {"id": "9", "url": "u", "content": "<p>page body</p>"})
    monkeypatch.setattr(B, "verify_live", lambda *a, **k: (False, "empty response"))
    B.cmd_page(A(file=str(f), title="T"))
    assert "verified: via API" in capsys.readouterr().out
    monkeypatch.setattr(B, "verify_live", lambda *a, **k: (False, "expected content missing"))
    B.cmd_page(A(file=str(f), title="T"))
    assert "WROTE BUT VERIFY FAILED" in capsys.readouterr().out


def test_cmd_pages_lists(monkeypatch, capsys):
    monkeypatch.setattr(B, "api", lambda p, **k: {"items": [{"id": "1", "status": "LIVE", "title": "T" * 80, "url": "U"}, {"id": "2", "title": "N"}]})
    B.cmd_pages(None)
    out = capsys.readouterr().out
    assert f"  1  LIVE      {'T' * 52}\n      U\n" in out and "  2  ?         N\n      (no url)" in out
    monkeypatch.setattr(B, "api", lambda p, **k: {})
    B.cmd_pages(None)


def _manifest(tmp_path, names, missing=()):
    ents = []
    for n in names:
        p = tmp_path / f"{n}.html"
        if n not in missing:
            p.write_text(f"<p>{n}</p>", encoding="utf-8")
        ents.append({"slug": n, "path": str(p)})
    m = tmp_path / "m.json"
    m.write_text(json.dumps(ents))
    return str(m)


def test_cmd_bulk_counts_skips_and_limit(monkeypatch, tmp_path, capsys, fakes):
    m = _manifest(tmp_path, ["a", "b", "gone", "c", "d"], missing=("gone",))
    posts = {"a": {"id": "1", "url": "u1", "title": "A"}, "b": {"id": "2", "url": "u2", "title": "B"},
             "d": {"id": "4", "url": "u4", "title": "D"}}

    def fake_find(slug):
        if slug not in posts:
            raise SystemExit(f"no live post {slug}")
        return posts[slug]
    results = {"1": True, "2": False, "4": True}
    monkeypatch.setattr(B, "find_post", fake_find)
    monkeypatch.setattr(B, "do_update", lambda pid, url, title, body, **k: results[pid])
    with pytest.raises(SystemExit) as e:
        B.cmd_bulk(A(manifest=m))
    out = capsys.readouterr().out
    assert e.value.code == 2 and "2 updated, 1 failed" in out and "skip  gone" in out and "skip: no live post c" in out
    assert "[1/5] a" in out and fakes.count(B.WRITE_GAP_SECONDS) == 3
    with pytest.raises(SystemExit) as e:
        B.cmd_bulk(A(manifest=m, limit=1))
    out = capsys.readouterr().out
    assert "stopping at --limit 1; 4 not attempted" in out and "1 updated, 0 failed" in out and e.value.code == 0


def test_cmd_bulk_ceiling_exits_3(monkeypatch, tmp_path, capsys):
    m = _manifest(tmp_path, ["a"])
    monkeypatch.setattr(B, "find_post", lambda s: {"id": "1", "url": "u", "title": "A"})
    monkeypatch.setattr(B, "do_update", lambda *a, **k: (_ for _ in ()).throw(B.Ceiling("q")))
    with pytest.raises(SystemExit) as e:
        B.cmd_bulk(A(manifest=m))
    assert e.value.code == 3 and "after 0 writes" in capsys.readouterr().out


# ---------------------------------- main() ----------------------------------


def _main(monkeypatch, argv):
    monkeypatch.setattr(B.sys, "argv", ["blogger_api.py", *argv])
    B.main()


def test_main_requires_blog_env(monkeypatch):
    monkeypatch.setattr(B, "BLOG_ID", None)
    with pytest.raises(SystemExit, match="BLOG_ID/BLOG_URL not set"):
        _main(monkeypatch, ["pages"])
    monkeypatch.setattr(B, "BLOG_ID", "x")
    monkeypatch.setattr(B, "BLOG_URL", "")
    with pytest.raises(SystemExit, match="BLOG_ID/BLOG_URL not set"):
        _main(monkeypatch, ["pages"])


@pytest.mark.parametrize("argv,fn,attrs", [
    (["whoami"], "cmd_whoami", {}),
    (["pages"], "cmd_pages", {}),
    (["get", "--slug", "s", "--out", "o"], "cmd_get", {"slug": "s", "out": "o"}),
    (["update", "--id", "5", "--file", "f", "--dry-run", "--title", "T"], "cmd_update", {"id": "5", "dry_run": True, "title": "T"}),
    (["delete", "--slug", "s", "--yes"], "cmd_delete", {"slug": "s", "yes": True}),
    (["insert", "--file", "f", "--title", "T", "--draft"], "cmd_insert", {"draft": True, "dry_run": False}),
    (["page", "--file", "f", "--title", "T", "--id", "9"], "cmd_page", {"id": "9"}),
    (["bulk", "--manifest", "m", "--limit", "3", "--dry-run"], "cmd_bulk", {"limit": 3, "dry_run": True}),
    (["auth", "--port", "9000"], "cmd_auth", {"port": 9000}),
    (["auth"], "cmd_auth", {"port": 0}),
])
def test_main_dispatch(monkeypatch, argv, fn, attrs):
    seen = {}
    monkeypatch.setattr(B, fn, lambda args: seen.update(vars(args)))
    # subparsers captured the original function objects at parse time, so patch before building the parser
    _main(monkeypatch, argv)
    for k, v in attrs.items():
        assert seen[k] == v


def test_main_rejects_unknown_command(monkeypatch):
    with pytest.raises(SystemExit) as e:
        _main(monkeypatch, [])
    assert e.value.code == 2
