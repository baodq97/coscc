"""Every time a person stepped in, read from the six sources of 0156's R1 and counted once."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from coscc.bus import Bus
from coscc.data import Data
from coscc.runlog.journal import Journal
from coscc.service.attempts import Attempts
from coscc.service.interventions import DETAIL_MAX, KINDS, interventions
from coscc.units.meta import UnitMeta

KEY = "/ws"
OTHER = "/elsewhere"


class _Fixture(unittest.TestCase):
    """One of each kind, a rerun written to both of its sources, and rows that are none."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.data = Data(self.tmp / "data")
        self.journal = Journal(self.tmp / "work", self.data)
        self.meta = UnitMeta(self.tmp / "work", self.data)
        self.attempts = Attempts(self.tmp / "data", Bus())
        root = self.meta.root
        with self.data.write() as conn:

            def run(at: str, ws: str, unit: str, stage: str, kind: str, **record):
                record = {
                    "kind": kind,
                    "at": at,
                    "workspace": ws,
                    "unit": unit,
                    "stage": stage,
                    **record,
                }
                conn.execute(
                    "INSERT INTO runs (at, root, workspace, unit, stage, kind, record) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (at, root, ws, unit, stage, kind, json.dumps(record)),
                )

            def attempt(unit: str, stage: str, rerun: int, note: str, moves, ws: str = KEY):
                cur = conn.execute(
                    "INSERT INTO attempts (machine, workspace, unit, stage, rerun, note) "
                    "VALUES ('step', ?, ?, ?, ?, ?)",
                    (ws, unit, stage, rerun, note),
                )
                for seq, (to, outcome, at) in enumerate(moves, start=1):
                    conn.execute(
                        "INSERT INTO attempt_moves (attempt, seq, moved_to, outcome, at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (cur.lastrowid, seq, to, outcome, at),
                    )

            def move(at: str, unit: str, stage: str, to: str, actor: str, inputs: dict, ws=KEY):
                conn.execute(
                    "INSERT INTO transitions (at, root, workspace, unit, artifact, stage, "
                    "from_state, to_state, actor, session, source, machine, guard, inputs) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'accepted', ?, ?, '', 'run:x', 'm', 'g', ?)",
                    (at, root, ws, unit, f"{stage}.md", stage, to, actor, json.dumps(inputs)),
                )

            def review(at: str, unit: str, n: int, verdict: str, findings=()):
                cur = conn.execute(
                    "INSERT INTO review_rounds (at, root, workspace, unit, n, run, head, verdict) "
                    "VALUES (?, ?, ?, ?, ?, 'r', 'h', ?)",
                    (at, root, KEY, unit, n, verdict),
                )
                for fid, text in findings:
                    conn.execute(
                        "INSERT INTO review_findings (round, finding, open, label, severity, text) "
                        "VALUES (?, ?, 1, 'open', 'high', ?)",
                        (cur.lastrowid, fid, text),
                    )

            attempt(
                "0001_a",
                "spec",
                0,
                "",
                [
                    ("queued", "", "2026-10-02T01:00:00+00:00"),
                    ("refused", "unit-busy", "2026-10-02T01:00:01+00:00"),
                ],
            )
            # One rerun, written to `attempts` at the click and to `start` once it launched.
            attempt(
                "0002_b",
                "intent",
                1,
                "again, with the answer",
                [("queued", "", "2026-10-02T02:00:00+00:00")],
            )
            run(
                "2026-10-02T02:00:20+00:00",
                KEY,
                "0002_b",
                "intent",
                "start",
                rerun=True,
                rerun_note="again",
            )
            run(
                "2026-10-02T03:00:00+00:00",
                KEY,
                "0003_c",
                "spec",
                "start",
                rerun=True,
                rerun_note="by hand",
            )
            run("2026-10-02T03:30:00+00:00", KEY, "0003_c", "spec", "start")
            run(
                "2026-10-02T04:00:00+00:00",
                KEY,
                "0003_c",
                "integrate",
                "integration",
                outcome="refused",
                detail="x" * 500,
            )
            move(
                "2026-10-02T05:00:00+00:00",
                "0004_d",
                "pr",
                "accepted",
                "code:ci",
                {"ci": "red", "head": "abcdef123"},
            )
            move(
                "2026-10-02T05:10:00+00:00", "0004_d", "pr", "accepted", "code:ci", {"ci": "green"}
            )
            move("2026-10-02T06:00:00+00:00", "0004_d", "impl", "draft", "agent", {})
            review(
                "2026-10-02T07:00:00+00:00",
                "0004_d",
                1,
                "changes-requested",
                [("F1", "the test is skipped")],
            )
            review("2026-10-02T08:00:00+00:00", "0004_d", 2, "pass")
            run(
                "2026-10-02T09:00:00+00:00",
                KEY,
                "0004_d",
                "impl",
                "end",
                outcome="done",
                denials=[{"tool": "Bash"}],
            )
            # Another workspace's rows are never this one's.
            run("2026-10-02T09:30:00+00:00", OTHER, "0001_a", "spec", "start", rerun=True)
            move("2026-10-02T09:30:00+00:00", "0001_a", "impl", "draft", "agent", {}, ws=OTHER)

    def read(self, after: str = "", limit: int = 100):
        return interventions(self.journal, self.meta, self.attempts, KEY, after, limit)


class EachKindIsReadOnce(_Fixture):
    def test_each_kind_counts_and_a_rerun_written_twice_counts_once(self):
        found = self.read()
        self.assertEqual(
            Counter(f.kind for f in found),
            Counter(
                {
                    "rerun": 2,
                    "refused": 1,
                    "ci-red": 1,
                    "review-round": 1,
                    "impl-draft": 1,
                    "integrate": 1,
                }
            ),
        )
        self.assertEqual({f.kind for f in found}, set(KINDS))
        reruns = [f for f in found if f.kind == "rerun"]
        self.assertEqual([r.id.split(":")[1] for r in reruns], ["attempts", "runs"])
        self.assertEqual([f.at for f in found], sorted(f.at for f in found))

    def test_ids_are_the_same_on_every_read(self):
        first = [f.id for f in self.read()]
        self.assertEqual(first, [f.id for f in self.read()])
        self.assertEqual(len(set(first)), len(first))
        self.assertTrue(all(i.split(":")[0] in KINDS for i in first))

    def test_a_detail_is_one_line_of_at_most_300_characters(self):
        found = {f.kind: f for f in self.read()}
        self.assertEqual(len(found["integrate"].detail), DETAIL_MAX)
        self.assertTrue(found["integrate"].detail.startswith("refused: xxx"))
        self.assertIn("F1 (high): the test is skipped", found["review-round"].detail)
        self.assertIn("abcdef1", found["ci-red"].detail)
        self.assertEqual(found["refused"].detail, "the gate refused it: unit-busy")


class AfterAndLimitAreKept(_Fixture):
    def test_only_what_is_past_after_is_read(self):
        found = self.read("2026-10-02T05:00:00+00:00")
        self.assertEqual([f.kind for f in found], ["impl-draft", "review-round"])

    def test_at_most_limit_oldest_first(self):
        found = self.read(limit=2)
        self.assertEqual([f.kind for f in found], ["refused", "rerun"])

    def test_a_start_just_past_after_is_still_its_attempt(self):
        found = self.read("2026-10-02T02:00:10+00:00")
        self.assertNotIn("0002_b", [f.unit for f in found if f.kind == "rerun"])

    def test_no_run_log_reads_nothing(self):
        self.assertEqual(interventions(None, self.meta, self.attempts, KEY, "", 10), [])


if __name__ == "__main__":
    unittest.main()
