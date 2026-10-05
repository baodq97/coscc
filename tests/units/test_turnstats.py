"""`coscc/units/turnstats.py`, on a `cos.db` and a unit store in a temporary directory."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coscc import units

from coscc.units import turnstats
from coscc.store.db import Data


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.ws = self.root / "work" / "coscc"
        self.ws.mkdir(parents=True)
        self.key = units.key(self.ws)
        with Data(self.data).connect():
            pass

    def run_row(
        self, at: str, unit: str, stage: str, kind: str, workspace: str | None = None, **record
    ) -> None:
        with Data(self.data).connect() as conn:
            conn.execute(
                "INSERT INTO runs (at, root, workspace, unit, stage, kind, record) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    at,
                    "/r",
                    workspace or self.key,
                    unit,
                    stage,
                    kind,
                    json.dumps({"kind": kind, **record}),
                ),
            )

    def step(
        self, start_at: str, unit: str, end_at: str | None = None, start: dict | None = None, **end
    ) -> None:
        self.run_row(start_at, unit, "impl", "start", **(start or {}))
        self.run_row(end_at or start_at, unit, "impl", "end", outcome="done", **end)

    def shipped(self, at: str, unit: str) -> None:
        with Data(self.data).connect() as conn:
            conn.execute(
                "INSERT INTO transitions (at, root, workspace, unit, artifact, stage, from_state, to_state, "
                "actor, session, source, machine) VALUES (?, '/r', ?, ?, 'ship.md', 'ship', 'draft', "
                "'accepted', 'a', 's', 'x', 'm')",
                (at, self.key, unit),
            )

    def events(self, run: str, *kinds: str) -> None:
        with Data(self.data).connect() as conn:
            for seq, kind in enumerate(kinds):
                conn.execute(
                    "INSERT INTO step_events (run, seq, at, kind, event, bytes) VALUES (?, ?, 0, ?, '{}', 2)",
                    (run, seq, kind),
                )

    def calls(self, run: str, *events: dict) -> None:
        with Data(self.data).connect() as conn:
            for seq, event in enumerate(events):
                conn.execute(
                    "INSERT INTO step_events (run, seq, at, kind, event, bytes) VALUES (?, ?, 0, ?, ?, 2)",
                    (run, seq, event["kind"], json.dumps(event)),
                )

    @staticmethod
    def use(i: str, name: str, **tool_input) -> dict:
        return {"kind": "tool_use", "id": i, "name": name, "input": tool_input}

    @staticmethod
    def result(i: str, content, **extra) -> dict:
        return {"kind": "tool_result", "tool_use_id": i, "content": content, **extra}

    def review(self, unit: str, text: str) -> None:
        d = units.cos_dir(self.key, self.data) / unit
        d.mkdir(parents=True, exist_ok=True)
        (d / "review.md").write_text(text, encoding="utf-8")


class ThePairs(Fixture):
    def pairs(self, since: str | None = "2026-09-24", until: str | None = None) -> list[dict]:
        with Data(self.data).connect() as conn:
            return turnstats.pairs(conn, self.key, since, until)

    def test_each_end_is_paired_with_the_latest_start_before_it(self):
        self.run_row("2026-09-23T10:00:00", "0001_a", "impl", "start")
        self.step("2026-09-24T10:00:00", "0001_a", "2026-09-24T11:00:00", turns=10, cost_usd=1.0)
        # Another workspace's rows are not this one's.
        self.run_row("2026-09-24T12:00:00", "0001_a", "impl", "start", workspace="/elsewhere")
        self.run_row(
            "2026-09-24T12:30:00", "0001_a", "impl", "end", workspace="/elsewhere", turns=500
        )
        (p,) = self.pairs()
        self.assertEqual((p["unit"], p["end"]["turns"], p["end"]["cost_usd"]), ("0001_a", 10, 1.0))

    def test_a_start_before_the_window_puts_its_end_outside_it(self):
        self.step("2026-09-23T23:00:00", "0001_a", "2026-09-24T01:00:00", turns=10)
        self.assertEqual(self.pairs(), [])

    def test_an_end_after_another_stage_starts_is_timed_by_itself(self):
        self.run_row("2026-09-23T23:00:00", "0001_a", "plan", "start")
        self.run_row("2026-09-24T01:00:00", "0001_a", "impl", "end", turns=4)
        self.assertEqual(len(self.pairs()), 1)

    def test_until_is_exclusive(self):
        self.step("2026-09-24T10:00:00", "0001_a", turns=1)
        self.step("2026-09-26T10:00:00", "0002_b", turns=1)
        self.assertEqual(len(self.pairs(until="2026-09-26T10:00:00")), 1)
        self.assertEqual(len(self.pairs(until="2026-09-26T10:00:01")), 2)
        self.assertEqual(len(self.pairs(until=None)), 2)


class TheReviews(Fixture):
    def test_shipped_units_are_those_accepted_in_the_window(self):
        self.shipped("2026-09-25T10:00:00", "0002_b")
        self.shipped("2026-09-25T11:00:00", "0001_a")
        self.shipped("2026-09-27T11:00:00", "0003_c")
        with Data(self.data).connect() as conn:
            got = turnstats.shipped_units(conn, self.key, "2026-09-24", "2026-09-26")
        self.assertEqual(got, ["0001_a", "0002_b"])

    def test_only_the_first_verdict_of_a_round_counts(self):
        text = (
            "# Review\nStatus: accepted.\n\n## Round 1\n\nReviewed: abc. Verdict: changes-requested.\n\n"
            "- F1 [open] x — high — Verdict: pass.\n\n## Round 2\n\nReviewed: def. Verdict: "
            "changes-requested.\n\n## Round 3\n\nReviewed: 012. Verdict: pass.\n"
        )
        self.assertEqual(turnstats.changes_requested(text), 2)


class TheCharactersRead(Fixture):
    def chars(self) -> dict:
        with Data(self.data).connect() as conn:
            steps = turnstats.pairs(conn, self.key, None, None)
            runs = [p["end"].get("run") or "" for p in steps]
            return turnstats.read_chars(conn, runs, "graph")

    def test_every_read_and_the_servers_tools_are_summed_apart_and_the_rest_ignored(self):
        self.step("2026-09-24T10:00:00", "0001_a", turns=1, run="r1")
        self.calls(
            "r1",
            self.use("a", "Read", file_path="/anywhere/x.py"),
            self.result("a", "x" * 5, truncated=True, length=1000, truncated_fields=["content"]),
            self.use("b", "Read", file_path="/w/y.py"),
            self.result("b", "y" * 7),
            self.use("c", "mcp__graph__find", query="q"),
            self.result("c", [{"type": "text", "text": "z" * 11}]),
            self.use("d", "Grep", pattern="p"),
            self.result("d", "g" * 50),
            self.use("e", "mcp__other__find"),
            self.result("e", "o" * 50),
        )
        self.assertEqual(self.chars(), {"r1": (1007, 11)})

    def test_a_purged_step_or_one_with_no_run_is_left_out(self):
        self.step("2026-09-24T10:00:00", "0001_a", turns=1, run="r1")
        self.step("2026-09-24T11:00:00", "0002_b", turns=1)
        self.step("2026-09-24T12:00:00", "0003_c", turns=1, run="r3")
        self.calls("r3", self.use("a", "Read", file_path="/x"), self.result("a", "abc"))
        with Data(self.data).connect() as conn:
            conn.execute(
                "INSERT INTO step_runs (run, root, workspace, unit, stage, started_at, purged_at) "
                "VALUES ('r1', '/r', ?, 'u', 'impl', 0, '2026-10-25T00:00:00')",
                (self.key,),
            )
        self.assertEqual(self.chars(), {"r3": (3, 0)})

    def test_the_ends_say_without_an_event_which_runs_read_chars_has(self):
        self.step("2026-09-24T10:00:00", "0001_a", turns=1, run="r1")
        self.step("2026-09-24T11:00:00", "0002_b", turns=1)
        self.step("2026-09-24T12:00:00", "0003_c", turns=1, run="r3")
        self.run_row("2026-09-24T13:00:00", "0004_d", "review", "end", run="r4")
        with Data(self.data).connect() as conn:
            conn.execute(
                "INSERT INTO step_runs (run, root, workspace, unit, stage, started_at, purged_at) "
                "VALUES ('r1', '/r', ?, 'u', 'impl', 0, '2026-10-25T00:00:00')",
                (self.key,),
            )
            ends = turnstats.impl_ends(conn, self.key)
        self.assertEqual(ends, [("r1", "0001_a", True), ("r3", "0003_c", False)])
        self.assertEqual({r for r, _, gone in ends if not gone}, set(self.chars()))
