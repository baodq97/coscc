"""Tests for `Agents` in `coscc/leif/agents.py`."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from coscc.config import Config
from coscc.store.db import Busy, Data
from coscc.http.app import Core
from coscc.kernel import Invalid
from coscc.agent.sessions import Sessions

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _at(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _end(stage: str, outcome: str, days_ago: float, cost=None, turns=None, unit="0001_u"):
    record = {"v": 1, "kind": "end", "workspace": "w", "unit": unit, "stage": stage}
    record.update(outcome=outcome, at=_at(days_ago))
    if cost is not None:
        record["cost_usd"] = cost
    if turns is not None:
        record["turns"] = turns
    return record


class _WithAService(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        config = Config(workspaces=(), working_dir=str(root / "work"), data_dir=str(root / "data"))
        (root / "work").mkdir()
        self.core = Core(config, Sessions(config))
        self.data = Data(config.data_dir)
        self.journal = self.core.ws.journal()

    def _seed(self, records):
        """Write `records` as they are, `at` included, the way `Journal.append` stores them."""
        self.journal.records()
        with self.data.write() as conn:
            conn.executemany(
                "INSERT INTO runs (root, workspace, unit, stage, kind, at, record) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        self.journal._root,
                        r["workspace"],
                        r["unit"],
                        r["stage"],
                        r["kind"],
                        r["at"],
                        json.dumps(r),
                    )
                    for r in records
                ],
            )

    def _settings(self):
        return [
            (r["agent"], r["field"], r["old"], r["new"], r["by"])
            for r in self.journal.records(kind="agent-setting")
        ]

    def _row(self, page, key):
        return next(r for r in page["rows"] if r["key"] == key)


class AFieldIsCheckedSavedAndLogged(_WithAService):
    def test_a_value_out_of_bounds_is_refused_and_nothing_is_written(self):
        wrong = [
            ("spec", "turns", 0),
            ("spec", "turns", 501),
            ("spec", "turns", 2.5),
            ("spec", "budget", 0.09),
            ("spec", "budget", 50.01),
            ("spec", "model", ""),
            ("spec", "model", "x" * 101),
            ("spec", "effort", "turbo"),
            ("chat", "effort", "low"),
            ("chat", "turns", 10),
            ("review:novel", "turns", 10),
            ("estimate", "budget", 1.0),
            ("deploy", "model", "m"),
            ("spec", "tools", ["Bash"]),
            ("", "model", "m"),
            (None, "model", "m"),
        ]
        for key, field, value in wrong:
            with self.assertRaises(Invalid, msg=(key, field, value)):
                self.core.agents.set_agent_field(key, field, value)
        for prefix in ("model:", "effort:", "turns:", "budget:"):
            self.assertEqual(self.data.pref_rows(prefix), {}, prefix)
        self.assertEqual(self._settings(), [])

    def test_identity_fields_keep_their_rules(self):
        agents = self.core.agents
        agents.set_agent_field("review", "name", "Judge")
        self.assertEqual(agents.agent("review")["name"], "Judge")
        for key, field, value in (
            ("review", "name", "Two words"),
            ("review", "glyph", "abc"),
            ("review", "meaning", "m" * 61),
            # Another row's name, whatever its case.
            ("spec", "name", "judge"),
            ("review", "name", "GEBO"),
            ("deploy", "name", "Nobody"),
        ):
            with self.assertRaises(Invalid, msg=(key, field, value)):
                agents.set_agent_field(key, field, value)
        agents.set_agent_field("impl", "name", "Tiwaz")
        # Resetting `review`'s name would bring `Tiwaz` back to it.
        with self.assertRaises(Invalid):
            agents.set_agent_field("review", "name", None)
        agents.set_agent_field("impl", "name", None)
        agents.set_agent_field("review", "name", None)
        self.assertEqual(agents.agent("review")["name"], "Tiwaz")
        self.assertEqual(self.data.pref_rows("agent:"), {})
        self.assertEqual(
            self._settings(),
            [
                ("review", "name", None, "Judge", "owner"),
                ("impl", "name", None, "Tiwaz", "owner"),
                ("impl", "name", "Tiwaz", None, "owner"),
                ("review", "name", "Judge", None, "owner"),
            ],
        )

    def test_an_unreadable_store_writes_nothing(self):
        self.core.agents.set_agent_field("review", "name", "Judge")
        with mock.patch.object(Data, "pref_rows", side_effect=Busy("locked")):
            for field, value in (("role", "Reads it all."), ("turns", 10)):
                with self.assertRaises(Invalid):
                    self.core.agents.set_agent_field("review", field, value)
        self.assertEqual(self.data.pref_rows("agent:"), {"agent:review": '{"name": "Judge"}'})
        self.assertEqual(len(self._settings()), 1)


class ThePage(_WithAService):
    def test_a_row_has_no_command_list_and_no_stage_defaults_to_haiku(self):
        page = self.core.agents.agent_page(now=NOW)
        for row in page["rows"]:
            self.assertNotIn("commands", row["row"], row["key"])
        self.assertNotIn("haiku", json.dumps(page).lower())

    def test_runs_last_five_and_thirty_days(self):
        self._seed(
            [_end("spec", "done", d, cost=0.5, turns=7) for d in (40, 20, 10, 5, 3, 2, 1)]
            + [_end("impl", "done", 2, cost=1.0), _end("impl", "failed", 1, cost=0.25)]
        )
        page = self.core.agents.agent_page(now=NOW)
        by = {r["key"]: r for r in page["rows"]}
        spec = by["spec"]
        self.assertEqual(len(spec["runs"]), 5)
        self.assertEqual(spec["runs"][0]["at"], _at(1))
        # A run names its workspace, so a page over every workspace can tell whose unit it is.
        self.assertEqual(spec["runs"][0]["workspace"], "w")
        self.assertEqual((spec["last"]["outcome"], spec["last"]["turns"]), ("done", 7))
        self.assertEqual((spec["runs_30d"], spec["cost_30d"]), (6, 3.0))
        self.assertEqual(spec["chip"], "ok")
        self.assertEqual((by["impl"]["chip"], by["impl"]["cost_30d"]), ("failed", 1.25))
        # A failed or costly row is listed first.
        self.assertEqual(page["rows"][0]["key"], "impl")

    def test_a_novel_run_is_costly_against_its_own_ceiling(self):
        def start(label, days_ago):
            return {
                "v": 1,
                "kind": "start",
                "workspace": "w",
                "unit": "0001_u",
                "stage": "impl",
            } | {
                "at": _at(days_ago),
                "label": label,
            }

        # $7 is 88 % of the plain $8 but 44 % of the novel $16.
        self._seed([start("novel", 2), _end("impl", "done", 2, cost=7.0)])
        self.assertEqual(self._row(self.core.agents.agent_page(now=NOW), "impl")["chip"], "ok")
        self._seed([start("routine", 1), _end("impl", "done", 1, cost=7.0)])
        self.assertEqual(self._row(self.core.agents.agent_page(now=NOW), "impl")["chip"], "costly")


if __name__ == "__main__":
    unittest.main()
