"""The cost model, on the pure function and against SQLite."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

from coscc.agent import pack
from coscc.leif import spend
from coscc.config import Config
from coscc.store.db import DB_FILENAME
from coscc.http.app import Core
from coscc.agent.sessions import Sessions

REPO = str(Path(__file__).resolve().parents[2])

TZ = timezone(timedelta(hours=7))

_seq = 0


def end(
    unit: str, stage: str, outcome: str = "done", at: str | None = None, **extra: Any
) -> dict[str, Any]:
    global _seq
    _seq += 1
    at = at or f"2026-09-2{_seq % 3}T0{_seq % 10}:00:00+00:00"
    return {"kind": "end", "unit": unit, "stage": stage, "outcome": outcome, "at": at, **extra}


def start(unit: str, stage: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "start", "unit": unit, "stage": stage, "mode": "manual", **extra}


def kinds(model: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [a for a in model["anomalies"] if a["kind"] == kind]


def waste(model: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(w for w in model["waste"] if w["kind"] == kind)


class OnlyEndsAreAdded(unittest.TestCase):
    """An `attempt` and an `estimate` repeat a step's cost; an absent one is unknown."""

    def test_attempt_and_estimate_do_not_move_the_total(self):
        m = spend.model(
            [
                {"kind": "attempt", "unit": "u", "stage": "impl", "cost_usd": 5.0},
                end("u", "impl", cost_usd=1.0),
                {"kind": "estimate", "unit": "", "stage": "estimate", "cost_usd": 9.0},
                end("", "estimate", cost_usd=0.5),
            ],
            tz=TZ,
        )
        self.assertEqual(m["total"]["usd"], 1.5)
        self.assertEqual(m["total"]["steps"], 2)

    def test_an_end_with_no_cost_is_unknown_not_zero(self):
        m = spend.model(
            [end("u", "impl", cost_usd=1.0), end("u", "impl"), end("u", "plan", cost_usd=None)],
            tz=TZ,
        )
        self.assertEqual((m["total"]["usd"], m["total"]["unknown"]), (1.0, 2))
        plan = next(r for r in m["by_stage"] if r["key"] == "plan")
        self.assertIsNone(plan["usd"])
        self.assertEqual(plan["unknown"], 1)


class EachRowIsTheAgentThatRanIt(unittest.TestCase):
    """An `end` naming no agent falls to the agent that ran it: a row, or a stage the app runs."""

    def test_no_row_is_a_stage_and_no_cent_is_lost(self):
        ends = [
            end("u", "pr", cost_usd=0.5, run="p1"),
            end("u", "ship", cost_usd=0.25),
            end("", "precedent", cost_usd=1.0, run="old1"),
            end("", "chat", cost_usd=0.75, run="c1"),
            end("", "estimate", cost_usd=0.1),
            end("u", "", cost_usd=2.0, agent="review"),
        ]
        m = spend.model(ends, tz=TZ)
        keys = {r["key"] for r in m["by_agent"]}
        self.assertLessEqual(
            keys, set(pack.rows()) | {a["key"] for a in pack.app_agents("coscc-sdlc")}
        )
        self.assertEqual(keys, {"pr", "ship", "leif", "estimate", "review"})
        self.assertEqual(sum(r["usd"] for r in m["by_agent"]), sum(e["cost_usd"] for e in ends))
        self.assertEqual(sum(r["steps"] for r in m["by_agent"]), len(ends))
        [leif] = [r for r in m["by_agent"] if r["key"] == "leif"]
        self.assertEqual((leif["usd"], leif["steps"]), (1.75, 2))
        self.assertEqual({r["run"] for r in leif["runs"]}, {"old1", "c1"})


class Waste(unittest.TestCase):
    def test_failed_and_run_again(self):
        m = spend.model(
            [
                end("u", "spec", cost_usd=1.0),
                end("u", "spec", outcome="failed", cost_usd=2.0),
                end("u", "spec", outcome="paused-budget"),
                end("u", "plan", outcome="stopped", cost_usd=4.0),
                end("", "estimate", cost_usd=1.0),
                end("", "estimate", cost_usd=1.0),
            ],
            tz=TZ,
        )
        self.assertEqual(
            waste(m, "failed"),
            {"kind": "failed", "count": 1, "usd": 2.0, "unknown": 0, "note": None},
        )
        again = waste(m, "run-again")
        self.assertEqual((again["count"], again["usd"], again["unknown"]), (2, 2.0, 1))

    def test_three_integrate_steps_fall_into_three_rows(self):
        m = spend.model(
            [
                start("u", "integrate", integrate_state="conflicting"),
                end("u", "integrate", cost_usd=1.0),
                start("u", "integrate", integrate_state="behind"),
                end("u", "integrate", cost_usd=2.0),
                start("u", "integrate"),
                end("u", "integrate"),
            ],
            tz=TZ,
        )
        self.assertEqual(
            (waste(m, "integrate-conflict")["count"], waste(m, "integrate-conflict")["usd"]),
            (1, 1.0),
        )
        self.assertEqual(
            (waste(m, "integrate-other")["count"], waste(m, "integrate-other")["usd"]), (1, 2.0)
        )
        none = waste(m, "integrate-not-recorded")
        self.assertEqual((none["count"], none["usd"], none["unknown"]), (1, None, 1))

    def test_an_integrate_step_with_a_null_cost_is_unknown_not_zero(self):
        m = spend.model(
            [
                start("u", "integrate", integrate_state="conflicting"),
                end("u", "integrate", cost_usd=None),
                start("u", "integrate", integrate_state="conflicting"),
                end("u", "integrate", cost_usd=1.5),
                start("u", "integrate", integrate_state="behind"),
                end("u", "integrate", cost_usd=None),
            ],
            tz=TZ,
        )
        conflict = waste(m, "integrate-conflict")
        self.assertEqual((conflict["count"], conflict["usd"], conflict["unknown"]), (2, 1.5, 1))
        other = waste(m, "integrate-other")
        self.assertEqual((other["count"], other["usd"], other["unknown"]), (1, None, 1))
        self.assertEqual((m["total"]["unknown"], m["total"]["usd"]), (2, 1.5))

    def test_rounds_from_review_md_money_from_the_steps_that_said(self):
        m = spend.model(
            [
                end("u", "review", cost_usd=2.0, verdicts=["changes-requested"]),
                end("u", "review", cost_usd=5.0, verdicts=["pass"]),
                end("u", "review", cost_usd=7.0),
            ],
            rounds={"u": ["changes-requested", "changes-requested", "pass"]},
            tz=TZ,
        )
        row = waste(m, "changes-requested")
        self.assertEqual((row["count"], row["usd"], row["note"]), (2, 2.0, 1))


class Anomalies(unittest.TestCase):
    """Each kind just above its threshold is flagged, just under it is not."""

    def test_over_budget(self):
        m = spend.model(
            [end("above", "impl", cost_usd=15.01), end("at", "impl", cost_usd=15.0)], tz=TZ
        )
        self.assertEqual(
            [(a["unit"], a["value"], a["limit"]) for a in kinds(m, "over-budget")],
            [("above", 15.01, 15.0)],
        )
        self.assertEqual({r["key"]: r["over"] for r in m["by_unit"]}, {"above": True, "at": False})

    def test_reruns(self):
        records = (
            [end("four", "review") for _ in range(4)]
            + [end("three", "review") for _ in range(3)]
            + [end("four", "spec") for _ in range(3)]
            + [end("three", "spec") for _ in range(2)]
            + [end("three", "integrate") for _ in range(3)]
        )
        m = spend.model(records, tz=TZ)
        self.assertEqual(
            sorted((a["unit"], a["stage"], a["value"], a["limit"]) for a in kinds(m, "reruns")),
            [("four", "review", 4, 3), ("four", "spec", 3, 2), ("three", "integrate", 3, 2)],
        )

    def test_tokens_per_turn(self):
        # A median of 10,000 per turn: 30,100 is above three times it, 30,000 is not.
        records = [end("u", "impl", turns=1, input_tokens=10_000) for _ in range(4)]
        records += [
            end("u", "impl", turns=1, input_tokens=30_100, at="2026-09-24T01:00:00+00:00"),
            end("u", "impl", turns=2, input_tokens=60_000),
        ]
        m = spend.model(records, tz=TZ)
        flagged = kinds(m, "tokens-per-turn")
        self.assertEqual([(a["value"], a["limit"]) for a in flagged], [(30_100, 30_000)])

    def test_a_stage_with_four_valid_steps_flags_nothing(self):
        records = [end("u", "impl", turns=1, input_tokens=10) for _ in range(3)]
        records += [
            end("u", "impl", turns=1, input_tokens=1_000_000),
            end("u", "impl", turns=0, input_tokens=1),
        ]
        records += [end("u", "impl", turns=3)]
        self.assertEqual(kinds(spend.model(records, tz=TZ), "tokens-per-turn"), [])

    def test_failed(self):
        m = spend.model(
            [
                end("u", "impl", outcome="failed"),
                end("u", "plan", outcome="stopped"),
                end("v", "spec", outcome="failed", cost_usd=0.5),
            ],
            tz=TZ,
        )
        self.assertEqual(
            sorted((a["unit"], a["value"]) for a in kinds(m, "failed")),
            [("u", "failed"), ("v", "failed")],
        )

    def test_no_unit_is_never_over_budget(self):
        m = spend.model([end("", "estimate", cost_usd=20.0)], tz=TZ)
        self.assertEqual(kinds(m, "over-budget"), [])


BY_UNIT = (
    "SELECT SUM(json_extract(record, '$.cost_usd')) FROM runs "
    "WHERE root = :root AND workspace = :ws AND kind = 'end' AND unit = :unit"
)
BY_STAGE = (
    "SELECT SUM(json_extract(record, '$.cost_usd')) FROM runs "
    "WHERE root = :root AND workspace = :ws AND kind = 'end' AND stage = :stage"
)
BY_DAY = (
    "SELECT SUM(json_extract(record, '$.cost_usd')) FROM runs "
    "WHERE root = :root AND workspace = :ws AND kind = 'end' AND date(at, 'localtime') = :day"
)
BY_UNIT_STAGE = BY_UNIT + " AND stage = :stage"


class MatchesTheRunsTable(unittest.TestCase):
    """Every figure within 1% of SQLite's sum, `NULL` as `None`, midnight where SQLite puts it."""

    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Ho_Chi_Minh"
        time.tzset()
        self.addCleanup(self._restore_tz)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "work").mkdir()
        config = Config(
            workspaces=(REPO,), working_dir=str(root / "work"), data_dir=str(root / "data")
        )
        self.core = Core(config, Sessions(config))
        self.db = root / "data" / DB_FILENAME

    def _restore_tz(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def _write(self):
        j = self.core.ws.journal()
        key = self.core.ws.key(REPO)
        steps = [
            ("0001_a", "spec", "done", "2026-09-23T03:00:00+00:00", {"cost_usd": 1.234567}),
            ("0001_a", "plan", "done", "2026-09-24T16:59:59+00:00", {"cost_usd": 0.004561}),
            ("0001_a", "impl", "failed", "2026-09-24T17:00:00+00:00", {}),
            ("0002_b", "impl", "done", "2026-09-24T17:00:01+00:00", {"cost_usd": 12.5}),
            ("0002_b", "impl", "paused-budget", "2026-09-25T01:00:00+00:00", {"cost_usd": None}),
            ("0003_c", "spec", "done", "2026-09-25T02:00:00+00:00", {"cost_usd": 0.0}),
            ("0003_c", "review", "failed", "2026-09-25T03:00:00+00:00", {}),
            ("", "estimate", "done", "2026-09-25T04:00:00+00:00", {"cost_usd": 0.0371}),
        ]
        for unit, stage, outcome, at, extra in steps:
            j.started(key, unit, stage, "manual", at=at)
            if outcome != "done":
                j.attempted(key, unit, stage, at=at, cost_usd=3.0)
            j.finished(key, unit, stage, outcome, at=at, **extra)
        j.append(
            {
                "kind": "estimate",
                "workspace": key,
                "unit": "",
                "stage": "estimate",
                "at": "2026-09-25T04:00:00+00:00",
                "cost_usd": 0.0371,
            }
        )

    def _sql(self, conn, query, **params):
        [(value,)] = conn.execute(query, params).fetchall()
        return value

    def _same(self, served, sql, where):
        if sql is None:
            self.assertIsNone(served, where)
            return
        self.assertIsNotNone(served, where)
        self.assertLessEqual(abs(served - sql), 0.01 * abs(sql), where)

    def test_every_unit_stage_and_day_matches_the_reference_queries(self):
        self._write()
        rows = self.core.ws.journal().records(self.core.ws.key(REPO))
        served = spend.model(rows)
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        [(root, ws)] = conn.execute("SELECT DISTINCT root, workspace FROM runs").fetchall()
        scope = {"root": root, "ws": ws}
        ends = "FROM runs WHERE root = :root AND workspace = :ws AND kind = 'end'"

        units = {r for (r,) in conn.execute(f"SELECT DISTINCT unit {ends}", scope)}
        stages = {r for (r,) in conn.execute(f"SELECT DISTINCT stage {ends}", scope)}
        days = {r for (r,) in conn.execute(f"SELECT DISTINCT date(at, 'localtime') {ends}", scope)}
        self.assertEqual({r["key"] for r in served["by_unit"]}, units)
        self.assertEqual({r["key"] for r in served["by_stage"]}, stages)
        self.assertEqual({r["key"] for r in served["by_day"]}, days)
        self.assertGreaterEqual(len(units), 3)
        self.assertGreaterEqual(len(stages), 3)
        self.assertGreaterEqual(len(days), 2)

        for row in served["by_unit"]:
            self._same(row["usd"], self._sql(conn, BY_UNIT, unit=row["key"], **scope), row["key"])
        for row in served["by_stage"]:
            self._same(row["usd"], self._sql(conn, BY_STAGE, stage=row["key"], **scope), row["key"])
        for row in served["by_day"]:
            self._same(row["usd"], self._sql(conn, BY_DAY, day=row["key"], **scope), row["key"])
        for unit in units:
            found = served["unit_stages"].get(unit, [])
            self.assertEqual(
                {r["key"] for r in found},
                {
                    s
                    for (s,) in conn.execute(
                        f"SELECT DISTINCT stage {ends} AND unit = :unit", {**scope, "unit": unit}
                    )
                },
            )
            for row in found:
                self._same(
                    row["usd"],
                    self._sql(conn, BY_UNIT_STAGE, unit=unit, stage=row["key"], **scope),
                    (unit, row["key"]),
                )

        # The two ends either side of local midnight fall on the days SQLite puts them on.
        by_day = {r["key"]: r for r in served["by_day"]}
        self.assertEqual(by_day["2026-09-24"]["usd"], 0.004561)
        self.assertEqual(by_day["2026-09-25"]["unknown"], 3)
        # An `attempt` and an `estimate` carry money and add none; `null` and absent are unknown.
        self.assertEqual(served["total"]["usd"], round(1.234567 + 0.004561 + 12.5 + 0.0371, 6))
        self.assertEqual(served["total"]["unknown"], 3)
        self.assertEqual(served["offset"], "UTC+07:00")


if __name__ == "__main__":
    unittest.main()


class Days(unittest.TestCase):
    def test_a_day_is_the_local_calendar_day_of_the_end(self):
        m = spend.model(
            [
                end("u", "impl", at="2026-09-24T16:59:59+00:00", cost_usd=1.0),
                end("u", "impl", at="2026-09-24T17:00:00+00:00", cost_usd=2.0),
            ],
            tz=TZ,
        )
        self.assertEqual(
            [(r["key"], r["usd"]) for r in m["by_day"]], [("2026-09-25", 2.0), ("2026-09-24", 1.0)]
        )
        self.assertEqual(m["offset"], "UTC+07:00")
