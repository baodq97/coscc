"""`coscc/turnstats.py` (`0096` plan step 1), on a `cos.db` and a unit store in a temporary
directory. The marks of `spec.md` R1 and R3 on the real run log are checked at a terminal."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import config, turnstats, units
from coscc.data import Data


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

    def run_row(self, at: str, unit: str, stage: str, kind: str, workspace: str | None = None, **record) -> None:
        with Data(self.data).connect() as conn:
            conn.execute(
                "INSERT INTO runs (at, root, workspace, unit, stage, kind, record) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (at, "/r", workspace or self.key, unit, stage, kind, json.dumps({"kind": kind, **record})),
            )

    def step(self, start_at: str, unit: str, end_at: str | None = None, start: dict | None = None, **end) -> None:
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

    def review(self, unit: str, text: str) -> None:
        d = units.cos_dir(self.key, self.data) / unit
        d.mkdir(parents=True, exist_ok=True)
        (d / "review.md").write_text(text, encoding="utf-8")

    def measure(self, since: str | None = "2026-09-24", until: str | None = None) -> dict:
        return turnstats.measure(str(self.ws), self.data, since, until)


class TheSteps(Fixture):
    def test_each_end_is_paired_with_the_latest_start_before_it(self):
        self.run_row("2026-09-23T10:00:00", "0001_a", "impl", "start")
        self.step("2026-09-24T10:00:00", "0001_a", "2026-09-24T11:00:00", turns=10, cost_usd=1.0)
        # Another workspace's rows are not this one's.
        self.run_row("2026-09-24T12:00:00", "0001_a", "impl", "start", workspace="/elsewhere")
        self.run_row("2026-09-24T12:30:00", "0001_a", "impl", "end", workspace="/elsewhere", turns=500)
        f = self.measure()
        self.assertEqual((f["steps"], f["turns_mean"], f["cost_mean"]), (1, 10, 1.0))

    def test_a_start_before_the_window_puts_its_end_outside_it(self):
        self.step("2026-09-23T23:00:00", "0001_a", "2026-09-24T01:00:00", turns=10)
        self.assertEqual(self.measure()["steps"], 0)

    def test_an_end_after_another_stage_starts_is_timed_by_itself(self):
        self.run_row("2026-09-23T23:00:00", "0001_a", "plan", "start")
        self.run_row("2026-09-24T01:00:00", "0001_a", "impl", "end", turns=4)
        self.assertEqual(self.measure()["steps"], 1)

    def test_until_is_exclusive(self):
        self.step("2026-09-24T10:00:00", "0001_a", turns=1)
        self.step("2026-09-26T10:00:00", "0002_b", turns=1)
        self.assertEqual(self.measure(until="2026-09-26T10:00:00")["steps"], 1)
        self.assertEqual(self.measure(until="2026-09-26T10:00:01")["steps"], 2)
        self.assertEqual(self.measure(until=None)["steps"], 2)

    def test_an_end_without_turns_is_zero_turns_and_no_tokens_per_turn(self):
        self.step("2026-09-24T10:00:00", "0001_a", turns=200, cost_usd=4.0, duration_ms=1000,
                  input_tokens=100, cache_creation_tokens=300, cache_read_tokens=19600)
        self.run_row("2026-09-24T11:00:00", "0002_b", "impl", "start")
        self.run_row("2026-09-24T11:05:00", "0002_b", "impl", "end", outcome="failed")
        f = self.measure()
        self.assertEqual(f["steps"], 2)
        self.assertEqual(f["turns_mean"], 100)
        self.assertEqual((f["over100"], f["over100_share"]), (1, 0.5))
        self.assertEqual((f["cost_mean"], f["cost_median"]), (2.0, 2.0))
        self.assertEqual(f["duration_mean_ms"], 500)
        self.assertEqual((f["tokens_per_turn_mean"], f["tokens_per_turn_max"]), (100, 100))

    def test_no_step_is_no_mean(self):
        f = self.measure()
        self.assertEqual((f["steps"], f["turns_mean"], f["cost_median"]), (0, None, None))


class TheQuality(Fixture):
    def test_a_shipped_unit_counts_only_the_impl_starts_in_the_window(self):
        self.run_row("2026-09-23T10:00:00", "0001_a", "impl", "start")
        self.step("2026-09-24T10:00:00", "0001_a", turns=1)
        self.step("2026-09-24T10:00:00", "0002_b", turns=1)
        self.step("2026-09-24T12:00:00", "0002_b", turns=1)
        self.shipped("2026-09-25T10:00:00", "0001_a")
        self.shipped("2026-09-25T11:00:00", "0002_b")
        self.shipped("2026-09-27T11:00:00", "0003_c")
        for u in ("0001_a", "0002_b"):
            self.review(u, "## Round 1\n\nVerdict: pass.\n")
        f = self.measure(until="2026-09-26")
        self.assertEqual((f["shipped"], f["shipped_reimpl"], f["reimpl_share"]), (2, 1, 0.5))

    def test_changes_requested_rounds_come_from_the_unit_store(self):
        self.shipped("2026-09-25T10:00:00", "0001_a")
        self.shipped("2026-09-25T11:00:00", "0002_b")
        self.review("0001_a", "# Review\nStatus: accepted.\n\n## Round 1\n\nReviewed: abc. Verdict: changes-requested.\n\n"
                              "- F1 [open] x — high — Verdict: pass.\n\n## Round 2\n\nReviewed: def. Verdict: "
                              "changes-requested.\n\n## Round 3\n\nReviewed: 012. Verdict: pass.\n")
        f = self.measure()
        self.assertEqual(f["changes_requested_rounds"], 2)
        self.assertEqual(f["reviews_missing"], 1)
        self.assertEqual(f["changes_requested_mean"], 2.0)


class TheEvents(Fixture):
    def test_tool_uses_per_turn_over_the_steps_with_events(self):
        self.step("2026-09-24T10:00:00", "0001_a", turns=3, run="r1",
                  start={"included": ["plan.md", "plan-map", "commands"]})
        self.step("2026-09-24T11:00:00", "0002_b", turns=3, run="r2", start={"included": ["plan.md"]})
        self.step("2026-09-24T12:00:00", "0003_c", turns=3)
        self.events("r1", "turn", "tool_use", "tool_use", "denied", "turn", "tool_use", "text")
        f = self.measure()
        self.assertEqual((f["event_steps"], f["turn_events"], f["tool_uses"]), (1, 2, 3))
        self.assertEqual(f["tool_uses_per_turn"], 1.5)
        self.assertEqual((f["carried_plan_map"], f["carried_commands"]), (1, 1))


class TheCommand(Fixture):
    def main(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = turnstats.main(["--workspace", str(self.ws), "--data-root", str(self.data), *args])
        return code, out.getvalue(), err.getvalue()

    def test_it_prints_the_fields_as_json(self):
        self.step("2026-09-24T10:00:00", "0001_a", turns=40, cost_usd=1.0, duration_ms=10)
        code, out, _ = self.main("--since", "2026-09-24")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["steps"], 1)

    def test_outcome_holds(self):
        self.step("2026-09-24T10:00:00", "0001_a", turns=40, cost_usd=1.0, duration_ms=10)
        code, out, _ = self.main("--since", "2026-09-24", "--outcome")
        self.assertEqual((code, out.splitlines()[-1]), (0, "đạt"))

    def test_outcome_names_what_it_missed(self):
        self.step("2026-09-24T10:00:00", "0001_a", turns=120, cost_usd=3.0, duration_ms=10)
        code, out, _ = self.main("--since", "2026-09-24", "--outcome")
        self.assertEqual(code, 1)
        last = out.splitlines()[-1]
        self.assertTrue(last.startswith("không đạt: "), last)
        for name in ("turns_mean", "over100_share", "cost_mean"):
            self.assertIn(name, last)
        self.assertNotIn("duration_mean_ms", last)

    def test_no_step_does_not_hold(self):
        code, out, _ = self.main("--since", "2026-09-24", "--outcome")
        self.assertEqual((code, out.splitlines()[-1]), (1, "không đạt: steps 0 < 1"))

    def test_a_protected_database_is_refused_before_it_is_opened(self):
        db = self.data / "cos.db"
        env = {config.PROTECTED_DB_VAR: os.pathsep.join(["/nowhere/cos.db", str(db)])}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(turnstats.sqlite3, "connect", side_effect=AssertionError("opened")):
            code, out, err = self.main("--since", "2026-09-24")
        self.assertEqual((code, out), (2, ""))
        self.assertIn(config.PROTECTED_DB_VAR, err)

    def test_a_missing_database_is_exit_2_and_is_not_made(self):
        self.data = self.root / "none"
        code, _, err = self.main()
        self.assertEqual(code, 2)
        self.assertIn("does not exist", err)
        self.assertFalse(self.data.exists())


if __name__ == "__main__":
    unittest.main()
