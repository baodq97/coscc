"""`ui/src/api.gen.ts` is the routes' shapes as the app makes them: the studio reads only those."""

from __future__ import annotations

import unittest
from pathlib import Path

from coscc import api

FILE = Path(__file__).resolve().parents[1] / "ui" / "src" / "api.gen.ts"


class TheStudiosTypesAreFresh(unittest.TestCase):
    def test_the_file_is_what_the_app_makes(self):
        self.assertEqual(
            FILE.read_text(),
            api.typescript(),
            "ui/src/api.gen.ts is stale: run `uv run python -m coscc.api > ui/src/api.gen.ts`",
        )

    def test_a_schema_becomes_a_type(self):
        self.assertEqual(api._ts({"type": "array", "items": {"type": "integer"}}), "number[]")
        self.assertEqual(
            api._ts({"anyOf": [{"$ref": "#/components/schemas/Hold"}, {"type": "null"}]}),
            "Hold | null",
        )
        self.assertEqual(
            api._ts({"type": "object", "properties": {"a": {"type": "string"}}, "required": []}),
            '{\n  "a"?: string;\n}',
        )
        self.assertEqual(
            api._ts({"type": "object", "additionalProperties": {"type": "boolean"}}),
            "Record<string, boolean>",
        )
