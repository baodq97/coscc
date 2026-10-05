"""`ui/src/api.gen.ts` is the routes' shapes as the app makes them: the studio reads only those."""

from __future__ import annotations

import unittest
from pathlib import Path

from coscc.http import app

FILE = Path(__file__).resolve().parents[2] / "ui" / "src" / "api.gen.ts"


class TheStudiosTypesAreFresh(unittest.TestCase):
    def test_the_file_is_what_the_app_makes(self):
        self.assertEqual(
            FILE.read_text(),
            app.typescript(),
            "ui/src/api.gen.ts is stale: run `uv run python -m coscc.http > ui/src/api.gen.ts`",
        )
