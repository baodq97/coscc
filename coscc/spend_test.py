"""`0093`: the cost model, on the pure function and against SQLite.

Every threshold of R10 is tested just above and just under. R4's reference queries are the
spec's `## Design` §5, word for word, run on a `cos.db` this file writes.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

from coscc import present, spend
from coscc.config import Config
from coscc.data import DB_FILENAME
from coscc.service import Service
from coscc.sessions import Sessions

REPO = str(Path(__file__).resolve().parent.parent)

TZ = timezone(timedelta(hours=7))

_seq = 0


def end(unit: str, stage: str, outcome: str = "done", at: str | None = None, **extra: Any) -> dict[str, Any]:
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
    """R7, C1: an `attempt` and an `estimate` repeat a step's cost; an absent one is unknown."""

    def test_attempt_and_estimate_do_not_move_the_total(self):
        m = spend.model([
            {"kind": "attempt", "unit": "u", "stage": "impl", "cost_usd": 5.0},
            end("u", "impl", cost_usd=1.0),
            {"kind": "estimate", "unit": "", "stage": "estimate", "cost_usd": 9.0},
            end("", "estimate", cost_usd=0.5),
        ], tz=TZ)
        self.assertEqual(m["total"]["usd"], 1.5)
        self.assertEqual(m["total"]["steps"], 2)

    def test_an_end_with_no_cost_is_unknown_not_zero(self):
        m = spend.model([end("u", "impl", cost_usd=1.0), end("u", "impl"), end("u", "plan", cost_usd=None)], tz=TZ)
        self.assertEqual((m["total"]["usd"], m["total"]["unknown"]), (1.0, 2))
        plan = next(r for r in m["by_stage"] if r["key"] == "plan")
        self.assertIsNone(plan["usd"])
        self.assertEqual(plan["unknown"], 1)

    def test_rows_are_the_most_money_first_and_no_unit_has_its_own(self):
        m = spend.model([end("a", "impl", cost_usd=1.0), end("b", "impl", cost_usd=3.0),
                         end("", "estimate", cost_usd=2.0), end("c", "impl")], tz=TZ)
        self.assertEqual([r["key"] for r in m["by_unit"]], ["b", "", "a", "c"])


class Days(unittest.TestCase):
    def test_r3_a_day_is_the_local_calendar_day_of_the_end(self):
        m = spend.model([
            end("u", "impl", at="2026-09-24T16:59:59+00:00", cost_usd=1.0),
            end("u", "impl", at="2026-09-24T17:00:00+00:00", cost_usd=2.0),
        ], tz=TZ)
        self.assertEqual([(r["key"], r["usd"]) for r in m["by_day"]], [("2026-09-25", 2.0), ("2026-09-24", 1.0)])
        self.assertEqual(m["offset"], "UTC+07:00")


class Tokens(unittest.TestCase):
    def test_r6_four_kinds_for_the_workspace_and_each_stage(self):
        m = spend.model([
            end("u", "impl", input_tokens=10, output_tokens=20, cache_read_tokens=60, cache_creation_tokens=10),
            end("u", "spec", input_tokens=5),
        ], tz=TZ)
        self.assertEqual(m["tokens"]["workspace"]["total"], 105)
        self.assertEqual(m["tokens"]["workspace"]["cache_read_tokens"], 60)
        impl = next(r for r in m["tokens"]["by_stage"] if r["stage"] == "impl")
        self.assertEqual((impl["total"], impl["output_tokens"]), (100, 20))


class Waste(unittest.TestCase):
    def test_r7_failed_and_run_again(self):
        m = spend.model([
            end("u", "spec", cost_usd=1.0),
            end("u", "spec", outcome="failed", cost_usd=2.0),
            end("u", "spec", outcome="exhausted"),
            end("u", "plan", outcome="stopped", cost_usd=4.0),
            end("", "estimate", cost_usd=1.0),
            end("", "estimate", cost_usd=1.0),
        ], tz=TZ)
        self.assertEqual(waste(m, "exhausted-or-failed"), {
            "kind": "exhausted-or-failed", "count": 2, "usd": 2.0, "unknown": 1, "note": None})
        again = waste(m, "run-again")
        self.assertEqual((again["count"], again["usd"], again["unknown"]), (2, 2.0, 1))

    def test_r8_three_integrate_steps_fall_into_three_rows(self):
        m = spend.model([
            start("u", "integrate", integrate_state="conflicting"),
            end("u", "integrate", cost_usd=1.0),
            start("u", "integrate", integrate_state="behind"),
            end("u", "integrate", cost_usd=2.0),
            start("u", "integrate"),
            end("u", "integrate"),
        ], tz=TZ)
        self.assertEqual((waste(m, "integrate-conflict")["count"], waste(m, "integrate-conflict")["usd"]), (1, 1.0))
        self.assertEqual((waste(m, "integrate-other")["count"], waste(m, "integrate-other")["usd"]), (1, 2.0))
        none = waste(m, "integrate-not-recorded")
        self.assertEqual((none["count"], none["usd"], none["unknown"]), (1, None, 1))

    def test_r5_an_integrate_step_with_a_null_cost_is_unknown_not_zero(self):
        m = spend.model([
            start("u", "integrate", integrate_state="conflicting"),
            end("u", "integrate", cost_usd=None),
            start("u", "integrate", integrate_state="conflicting"),
            end("u", "integrate", cost_usd=1.5),
            start("u", "integrate", integrate_state="behind"),
            end("u", "integrate", cost_usd=None),
        ], tz=TZ)
        conflict = waste(m, "integrate-conflict")
        self.assertEqual((conflict["count"], conflict["usd"], conflict["unknown"]), (2, 1.5, 1))
        other = waste(m, "integrate-other")
        self.assertEqual((other["count"], other["usd"], other["unknown"]), (1, None, 1))
        self.assertEqual((m["total"]["unknown"], m["total"]["usd"]), (2, 1.5))

    def test_r9_rounds_from_review_md_money_from_the_steps_that_said(self):
        m = spend.model(
            [end("u", "review", cost_usd=2.0, verdicts=["changes-requested"]),
             end("u", "review", cost_usd=5.0, verdicts=["pass"]),
             end("u", "review", cost_usd=7.0)],
            rounds={"u": ["changes-requested", "changes-requested", "pass"]}, tz=TZ,
        )
        row = waste(m, "changes-requested")
        self.assertEqual((row["count"], row["usd"], row["note"]), (2, 2.0, 1))

    def test_there_is_no_total_row(self):
        m = spend.model([end("u", "impl", cost_usd=1.0)], tz=TZ)
        self.assertEqual([w["kind"] for w in m["waste"]], list(spend.WASTE_KINDS))


class Anomalies(unittest.TestCase):
    """R10: each kind just above its threshold is flagged, just under it is not."""

    def test_over_budget(self):
        m = spend.model([end("above", "impl", cost_usd=15.01), end("at", "impl", cost_usd=15.0)], tz=TZ)
        self.assertEqual([(a["unit"], a["value"], a["limit"]) for a in kinds(m, "over-budget")],
                         [("above", 15.01, 15.0)])
        self.assertEqual({r["key"]: r["over"] for r in m["by_unit"]}, {"above": True, "at": False})

    def test_no_unit_is_never_over_budget(self):
        m = spend.model([end("", "estimate", cost_usd=20.0)], tz=TZ)
        self.assertEqual(kinds(m, "over-budget"), [])

    def test_reruns(self):
        records = (
            [end("four", "review") for _ in range(4)] + [end("three", "review") for _ in range(3)]
            + [end("four", "spec") for _ in range(3)] + [end("three", "spec") for _ in range(2)]
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
        records += [end("u", "impl", turns=1, input_tokens=30_100, at="2026-09-24T01:00:00+00:00"),
                    end("u", "impl", turns=2, input_tokens=60_000)]
        m = spend.model(records, tz=TZ)
        flagged = kinds(m, "tokens-per-turn")
        self.assertEqual([(a["value"], a["limit"]) for a in flagged], [(30_100, 30_000)])

    def test_a_stage_with_four_valid_steps_flags_nothing(self):
        records = [end("u", "impl", turns=1, input_tokens=10) for _ in range(3)]
        records += [end("u", "impl", turns=1, input_tokens=1_000_000), end("u", "impl", turns=0, input_tokens=1)]
        records += [end("u", "impl", turns=3)]
        self.assertEqual(kinds(spend.model(records, tz=TZ), "tokens-per-turn"), [])

    def test_failed(self):
        m = spend.model([end("u", "impl", outcome="exhausted"), end("u", "plan", outcome="stopped"),
                         end("v", "spec", outcome="failed", cost_usd=0.5)], tz=TZ)
        self.assertEqual(sorted((a["unit"], a["value"]) for a in kinds(m, "failed")),
                         [("u", "exhausted"), ("v", "failed")])

    def test_kinds_come_in_order_each_latest_first(self):
        m = spend.model([
            end("u", "impl", outcome="failed", at="2026-09-20T00:00:00+00:00", cost_usd=16.0),
            end("u", "impl", outcome="failed", at="2026-09-22T00:00:00+00:00"),
            end("u", "impl", at="2026-09-21T00:00:00+00:00"),
        ], tz=TZ)
        self.assertEqual([a["kind"] for a in m["anomalies"]], ["over-budget", "failed", "failed", "reruns"])
        self.assertEqual([a["ended"] for a in kinds(m, "failed")],
                         ["2026-09-22T00:00:00+00:00", "2026-09-20T00:00:00+00:00"])


class UnitStages(unittest.TestCase):
    def test_r11_one_units_cost_by_stage(self):
        m = spend.model([end("u", "impl", cost_usd=1.0), end("u", "impl", cost_usd=2.0),
                         end("u", "spec"), end("v", "impl", cost_usd=9.0)], tz=TZ)
        self.assertEqual([(r["key"], r["usd"], r["steps"], r["unknown"]) for r in m["unit_stages"]["u"]],
                         [("impl", 3.0, 2, 0), ("spec", None, 1, 1)])


# `0093` spec `## Design` §5, word for word; R11 adds `unit` and `stage`.
BY_UNIT = ("SELECT SUM(json_extract(record, '$.cost_usd')) FROM runs "
           "WHERE root = :root AND workspace = :ws AND kind = 'end' AND unit = :unit")
BY_STAGE = ("SELECT SUM(json_extract(record, '$.cost_usd')) FROM runs "
            "WHERE root = :root AND workspace = :ws AND kind = 'end' AND stage = :stage")
BY_DAY = ("SELECT SUM(json_extract(record, '$.cost_usd')) FROM runs "
          "WHERE root = :root AND workspace = :ws AND kind = 'end' AND date(at, 'localtime') = :day")
BY_UNIT_STAGE = BY_UNIT + " AND stage = :stage"


def _read_money(text: str) -> float:
    return float(text.lstrip("$").replace(",", ""))


class MatchesTheRunsTable(unittest.TestCase):
    """R4, R11: every figure within 1% of SQLite's sum, `NULL` as `—`, midnight where SQLite puts it."""

    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Ho_Chi_Minh"
        time.tzset()
        self.addCleanup(self._restore_tz)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "work").mkdir()
        config = Config(workspaces=(REPO,), working_dir=str(root / "work"), data_dir=str(root / "data"))
        self.service = Service(config, Sessions(config))
        self.db = root / "data" / DB_FILENAME

    def _restore_tz(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def _write(self):
        j = self.service._journal()
        key = self.service._journal_key(REPO)
        steps = [
            ("0001_a", "spec", "done", "2026-09-23T03:00:00+00:00", {"cost_usd": 1.234567}),
            ("0001_a", "plan", "done", "2026-09-24T16:59:59+00:00", {"cost_usd": 0.004561}),
            ("0001_a", "impl", "failed", "2026-09-24T17:00:00+00:00", {}),
            ("0002_b", "impl", "done", "2026-09-24T17:00:01+00:00", {"cost_usd": 12.5}),
            ("0002_b", "impl", "exhausted", "2026-09-25T01:00:00+00:00", {"cost_usd": None}),
            ("0003_c", "spec", "done", "2026-09-25T02:00:00+00:00", {"cost_usd": 0.0}),
            ("0003_c", "review", "failed", "2026-09-25T03:00:00+00:00", {}),
            ("", "estimate", "done", "2026-09-25T04:00:00+00:00", {"cost_usd": 0.0371}),
        ]
        for unit, stage, outcome, at, extra in steps:
            j.started(key, unit, stage, "manual", at=at)
            if outcome != "done":
                j.attempted(key, unit, stage, at=at, cost_usd=3.0)
            j.finished(key, unit, stage, outcome, at=at, **extra)
        j.append({"kind": "estimate", "workspace": key, "unit": "", "stage": "estimate",
                  "at": "2026-09-25T04:00:00+00:00", "cost_usd": 0.0371})

    def _sql(self, conn, query, **params):
        [(value,)] = conn.execute(query, params).fetchall()
        return value

    def _same(self, served, sql, where):
        if sql is None:
            self.assertIsNone(served, where)
            self.assertEqual(present.money(served), "—", where)
            return
        self.assertIsNotNone(served, where)
        self.assertLessEqual(abs(served - sql), 0.01 * abs(sql), where)
        self.assertLessEqual(abs(_read_money(present.money(served)) - sql), 0.01 * abs(sql), where)

    def test_every_unit_stage_and_day_matches_the_reference_queries(self):
        self._write()
        served = self.service.cost(REPO)
        self.assertTrue(served["recording"])
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
        self.assertGreaterEqual((len(units), len(stages), len(days)), (3, 3, 2))

        for row in served["by_unit"]:
            self._same(row["usd"], self._sql(conn, BY_UNIT, unit=row["key"], **scope), row["key"])
        for row in served["by_stage"]:
            self._same(row["usd"], self._sql(conn, BY_STAGE, stage=row["key"], **scope), row["key"])
        for row in served["by_day"]:
            self._same(row["usd"], self._sql(conn, BY_DAY, day=row["key"], **scope), row["key"])
        for unit in units:
            found = self.service.unit_cost(REPO, unit)["by_stage"]
            self.assertEqual({r["key"] for r in found}, {
                s for (s,) in conn.execute(f"SELECT DISTINCT stage {ends} AND unit = :unit", {**scope, "unit": unit})})
            for row in found:
                self._same(row["usd"], self._sql(conn, BY_UNIT_STAGE, unit=unit, stage=row["key"], **scope),
                           (unit, row["key"]))

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
