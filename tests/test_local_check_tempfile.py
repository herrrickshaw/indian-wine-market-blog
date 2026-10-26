"""2026-10-25 static sweep: _local_check wrote its node helper to a fixed, world-writable /tmp path (bandit B108 / ruff S108)."""
import os
import tempfile

import _local_check


def test_js_syntax_helper_uses_a_private_temp_file(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["script"] = cmd[1]
        seen["existed_during_run"] = os.path.exists(cmd[1])
        seen["content"] = open(cmd[1]).read()

        class R:
            stdout = "[]"
        return R()

    monkeypatch.setattr(_local_check.subprocess, "run", fake_run)
    assert _local_check.js_syntax_errors("out/some.html") == []
    script = seen["script"]
    # a fixed name lets another local user pre-create/symlink it or swap its body before node runs it
    assert script != "/tmp/_syntax_check.js"
    assert os.path.dirname(script) == tempfile.gettempdir()
    assert seen["existed_during_run"] and seen["content"] == _local_check.NODE_SYNTAX
    assert not os.path.exists(script), "helper script must be removed after the run"
