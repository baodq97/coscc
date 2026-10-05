"""The notice script in `coscc/features/notices.py`, as the studio loads it."""

from __future__ import annotations

import tempfile
import unittest

from fastapi.testclient import TestClient

from coscc.http.app import build
from coscc.config import Config
from coscc.features import notices

# The script's last statements, so a render that cut it short is not counted as carrying it.
_NOTICE_TAIL = "return cursor(); }});\n})();"


class TheNoticeScript(unittest.TestCase):
    def test_the_studio_loads_the_notice_script_once(self):
        """What it does in a browser is `scripts/e2e.py`'s to show."""
        js = notices._NOTICE_JS
        with tempfile.TemporaryDirectory() as tmp:
            shell = TestClient(build(Config(data_dir=tmp))).get("/api/features/scripts").text
        self.assertEqual(shell.count("window.__coscc_notices = true;"), 1)
        self.assertIn(_NOTICE_TAIL, js)
        for said in (
            "__coscc_notices",
            "coscc_notice_after",
            "/api/notices/follow",
            'aria-label", "Dismiss',
        ):
            self.assertIn(said, js)
        self.assertNotIn(
            "__",
            js.replace("__coscc_", "").replace("__proto__", ""),
            "a colour placeholder was left in",
        )
