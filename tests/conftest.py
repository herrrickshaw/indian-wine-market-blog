import os
import socket
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "consistency-check"))
# crosslink_analysis builds its link regex from the blog URL at import time.
os.environ.setdefault("WINE_BLOG_URL", "https://wine-test.blogspot.com")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def guard(*a, **k):
        raise RuntimeError("network access attempted during a test")
    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "getaddrinfo", guard)
