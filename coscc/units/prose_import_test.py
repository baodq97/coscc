"""`0136` step 14, in part: the review rounds only the prose holds are read into `cos.db` once,
and `cos.mjs` reads the rows exactly as it read the file."""

from __future__ import annotations

import unittest

from coscc.units import prose_import
from coscc.units.seven_places_test import _Review

PROSE = """# Review: a problem
PR: pr.md. Author: t. Status: accepted.

## Round 1

Reviewed: abc1234. Verdict: changes-requested.

### Findings

- F1 [open] coscc/x.py:3-4 — high — S3 the dialog prints a path.
- F2 [open] (none) — low — a note.

## Round 2

Reviewed: abc1234. Verdict: pass.

### Findings

- F1 [fixed deadbee] coscc/x.py:3-4 — high — S3 the dialog prints a path.
- F2 [open] (none) — low — a note.

### Screens

Taken at: abc1234. Standard: .claude/rules/ui-standard.md. Looked at by: Tiwaz (agent, review), from screenshots.

- `.screens/board-1440x900.png` — 1440x900 — /board — nothing breaks a rule

## Round 3

Reviewed: abc1234. Verdict: changes-requested.

### Findings

- F3 [open] a line with no severity in it.
"""


class TheRoundsOnlyTheProseHoldsAreImportedOnce(_Review):

    def setUp(self):
        super().setUp()
        (self.dir / "review.md").write_text(PROSE, encoding="utf-8")
        self.service._imported.clear()

    def rounds(self) -> list[dict]:
        """`cos.mjs`'s rounds, two things aside that every row carries apart from the file,
        a submitted round's as much as an imported one's: the screens' raw header line, which
        a row has none of, and a finding's `rule`, which `reviewFrom` adds and nothing reads."""
        out = []
        for r in self._status()["artifacts"]["review.md"]["review"]["rounds"]:
            r = {**r, "findings": [{k: v for k, v in f.items() if k != "rule"} for f in r["findings"]]}
            if r.get("screens"):
                r = {**r, "screens": {**r["screens"], "header": None}}
            out.append(r)
        return out

    def rows(self) -> list[tuple]:
        with self.service._unit_meta().data.connect() as conn:
            return [tuple(r) for r in conn.execute(
                "SELECT n, run, head, verdict FROM review_rounds WHERE unit = ? ORDER BY n", (self.unit,))]

    def test_cos_mjs_reads_the_rows_as_it_read_the_file(self):
        before = self.rounds()
        self.assertEqual(self.rows(), [])
        self._unit()  # the first board read imports
        self.assertEqual(self.rows(), [(1, "prose-import", "abc1234", "changes-requested"),
                                       (2, "prose-import", "abc1234", "pass")])
        self.assertEqual(self.rounds(), before)

    def test_a_round_that_would_not_read_back_the_same_stays_in_the_file(self):
        self._unit()
        self.assertNotIn(3, [n for n, *_ in self.rows()], "F3's line has no severity")

    def test_a_second_read_imports_nothing(self):
        self._unit()
        meta = self.service._unit_meta()
        key = self.service._journal_key(str(self.repo))
        self.assertIsNone(prose_import.import_rounds(meta, key, [], {}))
        (self.dir / "review.md").write_text(PROSE.replace("## Round 3", "## Round 4"), encoding="utf-8")
        self._unit()
        self.assertEqual(len(self.rows()), 2)

    def test_the_last_round_is_what_the_ship_guard_reads(self):
        from coscc.github import prmachine

        self._unit()
        history = self.service._unit_meta().history
        key = self.service._journal_key(str(self.repo))
        self.assertEqual(prmachine.last_round(history, key, self.unit),
                         {"n": 2, "head": "abc1234", "verdict": "pass"})


class OneFinding(unittest.TestCase):
    """What `reviewFrom` rebuilds from the fields is the line `write-review` wrote."""

    def rebuilt(self, f: dict) -> str:
        where = (f"{f['path']}:{f['lines']}" if f["lines"] else f["path"]) if f["path"] else "(none)"
        return f"{where} — {f['severity']} — {f['rule'] + ' ' if f['rule'] else ''}{f['text']}"

    def test_each_shape_reads_back(self):
        for text in ("coscc/x.py:3-4 — high — S3 the dialog prints a path.",
                     "(none) — low — a note.",
                     "coscc/x.py — medium — no lines named.",
                     "a/b.py:10,12 — low — two lines."):
            f = prose_import.finding_of({"id": "F1", "label": "open", "text": text})
            self.assertIsNotNone(f, text)
            self.assertEqual(self.rebuilt(f), text)

    def test_a_label_or_a_line_the_object_cannot_carry_is_none(self):
        self.assertIsNone(prose_import.finding_of({"id": "F1", "label": "unreadable", "text": "(none) — low — x"}))
        self.assertIsNone(prose_import.finding_of({"id": "F1", "label": "open", "text": "no severity here"}))

    def test_a_fixed_finding_keeps_its_commit_and_no_other_does(self):
        f = prose_import.finding_of({"id": "F1", "label": "fixed", "fixed_by": "deadbee", "text": "(none) — low — x"})
        self.assertEqual((f["state"], f["fixed_in"]), ("fixed", "deadbee"))


if __name__ == "__main__":
    unittest.main()
