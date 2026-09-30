"""The notice script in `coscc/features/notices.py`, as the shell carries it."""

from __future__ import annotations

import json
import unittest

from coscc import screens
from coscc.features import notices

# The script's last statements, so a render that cut it short is not counted as carrying it.
_NOTICE_TAIL = "setInterval(refresh, 60000);\n  connect();\n})();"


class TheNoticeScript(unittest.TestCase):
    def test_the_shell_carries_the_notice_script_once_and_it_touches_no_reflex_state(self):
        """What it does in a browser is `scripts/e2e.py`'s to show."""
        js = notices._NOTICE_JS
        shell = json.dumps(screens.index().render(), ensure_ascii=False, default=str)
        self.assertEqual(shell.count("window.__coscc_notices = true;"), 1)
        self.assertIn(_NOTICE_TAIL, js)
        for said in (
            "__coscc_notices",
            "coscc_notice_after",
            "/api/notices/follow",
            'aria-label", "Dismiss',
        ):
            self.assertIn(said, js)
        for reflex in ("_rx_state_", "addEvents", "__reflex"):
            self.assertNotIn(reflex, js)
        self.assertNotIn(
            "__",
            js.replace("__coscc_", "").replace("__proto__", ""),
            "a colour placeholder was left in",
        )
