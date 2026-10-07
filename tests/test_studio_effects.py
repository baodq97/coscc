"""An effect written `useEffect(() => call(), …)` returns what `call` returns; when that is a
Promise (`scrollIntoView` in newer browsers), React calls it as the cleanup and the page goes
white. Every effect in the studio and the features' pages has a body."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPRESSION = re.compile(r"use(Layout)?Effect\(\(\) => [^{(]")


class EffectsHaveABody(unittest.TestCase):
    def test_no_effect_returns_an_expression(self):
        files = [
            *(ROOT / "ui" / "src").rglob("*.ts*"),
            *(ROOT / "coscc" / "features").rglob("*.tsx"),
        ]
        bad = [
            f"{p.relative_to(ROOT)}:{n}"
            for p in files
            if "node_modules" not in p.parts
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if EXPRESSION.search(line)
        ]
        self.assertEqual(bad, [])
