"""Characterisation of snapshot.main(): golden captured from the code before it was split into stages.

Fully offline: Blogger API, clock, filesystem roots and git subprocess calls are all stubbed.
"""

import datetime as real_dt
import json
import sys
import types
from pathlib import Path

import pytest

import snapshot

GOLDEN = Path(__file__).parent / "golden" / "snapshot_main.json"
BODY = "<h2>One</h2><p>Duty is 150 percent</p>"


class _FakeDT:
    class datetime:
        @staticmethod
        def now():
            fixed = real_dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=real_dt.timezone.utc)
            return types.SimpleNamespace(astimezone=lambda: fixed)


def _item(pid, slug, content, published="2026-01-01T16:00:00+05:30", labels=("wine",)):
    return {
        "id": pid,
        "url": f"https://b.example/2026/01/{slug}.html",
        "content": content,
        "title": f"Title {pid}",
        "published": published,
        "updated": published,
        "labels": list(labels),
    }


def _run(monkeypatch, tmp_path, items, argv, runs):
    monkeypatch.setattr(snapshot, "ROOT", tmp_path)
    monkeypatch.setattr(snapshot, "POSTS", tmp_path / "posts")
    monkeypatch.setattr(snapshot, "META", tmp_path / "meta")
    monkeypatch.setattr(snapshot, "MANIFEST", tmp_path / "meta" / "manifest.json")
    monkeypatch.setattr(snapshot, "CHANGELOG", tmp_path / "CHANGELOG.md")
    monkeypatch.setattr(snapshot, "dt", _FakeDT)
    monkeypatch.setattr(snapshot.B, "api", lambda path: {"items": items})
    monkeypatch.setattr(snapshot.B, "BLOG_ID", "1", raising=False)

    def fake_run(cmd, **kw):
        runs.append(cmd)
        out = "1" if cmd[:2] == ["git", "rev-list"] else ("main" if "--abbrev-ref" in cmd else "")
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(snapshot.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["snapshot.py", *argv])
    try:
        snapshot.main()
        return None
    except SystemExit as e:
        return str(e.code)


def _tree(root):
    return {
        str(p.relative_to(root)): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_snapshot_main_matches_golden(monkeypatch, tmp_path, capsys):
    steps = []
    runs = []
    scenarios = [
        ([_item("1", "a", BODY), _item("2", "b", BODY), _item("3", "dup", BODY), _item("4", "dup", BODY), _item("1", "a", BODY)], ["--note", "first", "--no-commit"]),
        ([_item("1", "a", BODY), _item("2", "b", BODY), _item("3", "dup", BODY), _item("4", "dup", BODY)], ["--no-commit"]),
        ([_item("1", "a", BODY), _item("2", "b", BODY), _item("3", "dup", BODY), _item("4", "dup", BODY)], ["--allow-empty", "--no-commit"]),
        (
            [
                _item("1", "a", BODY.replace("150", "120")),
                _item("2", "b-moved", BODY + "<h2>Two</h2>"),
                _item("3", "dup", BODY, published="2026-02-01T16:00:00+05:30"),
            ],
            ["--note", "second", "--as", "minor"],
        ),
        ([_item("1", "a", BODY.replace("150", "99"))], ["--push"]),
        ([], ["--no-commit"]),
    ]
    for items, argv in scenarios:
        err = _run(monkeypatch, tmp_path, items, argv, runs)
        steps.append({"argv": argv, "exit": err, "stdout": capsys.readouterr().out})
    got = json.loads(json.dumps({"steps": steps, "git": runs, "files": _tree(tmp_path)}, sort_keys=True))
    if not GOLDEN.exists():  # pragma: no cover - one-off capture
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
        pytest.skip("golden written")
    assert got == json.loads(GOLDEN.read_text())
