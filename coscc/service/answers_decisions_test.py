"""The person's decisions and delegations.

Every write here goes through `Service`, as the Settings screen's handlers do; no route
reaches it (`test_no_route_writes_decisions`).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from coscc.config import Config
from coscc.data import Data
from coscc.service import Service
from coscc.service.common import Invalid
from coscc.agent.sessions import Sessions

REPO = Path(__file__).resolve().parents[2]


class Fixture(unittest.TestCase):
    """One workspace, `proj`, and a service on a scratch data directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.work, self.data = self.root / "work", self.root / "data"
        self.cwd = str(self.work / "proj")
        Path(self.cwd).mkdir(parents=True)
        self.config = Config(
            workspaces=(self.cwd,), working_dir=str(self.work), data_dir=str(self.data)
        )
        self.service = Service(self.config, Sessions(self.config))


class TheDecisionsPanel(Fixture):
    GOOD = {"kind": "decision", "text": "Luôn rẻ.", "source": "chat 2026-09-28"}

    def test_the_form_refuses_each_case_with_one_sentence(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        delegation = {**self.GOOD, "kind": "delegation", "agent": "Leif", "covers": "naming"}
        cases = [
            {**self.GOOD, "kind": "rule"},
            {**self.GOOD, "text": "  "},
            {**self.GOOD, "text": "x" * 2001},
            {**self.GOOD, "source": ""},
            {**self.GOOD, "source": "a\nb"},
            {**self.GOOD, "until": "2026-13-01"},
            {**self.GOOD, "until": "soon"},
            {**self.GOOD, "until": yesterday},
            {**self.GOOD, "workspace": "nowhere"},
            {**delegation, "agent": ""},
            {**delegation, "agent": "Kenaz"},
            {**delegation, "covers": ""},
            {**delegation, "covers": "a\nb"},
        ]
        for fields in cases:
            with self.assertRaises(Invalid, msg=fields) as refused:
                self.service.add_decision(fields)
            said = str(refused.exception)
            self.assertTrue(said.endswith(".") and ". " not in said, said)
        self.assertEqual(Data(self.data).decisions(), [])
        for fields in (
            self.GOOD,
            {**delegation, "workspace": "proj", "until": date.today().isoformat()},
        ):
            self.service.add_decision(fields)
        rows = self.service.decisions_table()["rows"]
        self.assertEqual(
            [(r["id"], r["state"], r["workspace_name"]) for r in rows],
            [("D1", "in force", "All workspaces"), ("D2", "in force", "proj")],
        )
        self.assertEqual(
            rows[1]["from_day"], date.today().isoformat(), "from is the day it was entered"
        )

    def test_a_decision_is_withdrawn_not_deleted(self):
        added = self.service.add_decision(self.GOOD)["added"]
        Data(self.data).decision_add(
            kind="decision",
            text="cũ",
            source="s",
            from_day="2026-01-01",
            until_day="2026-01-02",
            workspace="gone-000000000000",
        )
        table = self.service.withdraw_decision(added)
        self.assertEqual(
            [(r["id"], r["state"]) for r in table["rows"]], [("D1", "withdrawn"), ("D2", "expired")]
        )
        self.assertEqual(table["rows"][1]["workspace_name"], "a removed workspace")
        for d in ("D1", "D2", "D9", ""):
            with self.assertRaises(Invalid):
                self.service.withdraw_decision(d)
        self.assertEqual(len(Data(self.data).decisions()), 2)


class NoRouteReachesThem(unittest.TestCase):
    """Only the Settings screen's handlers call the two writers."""

    WRITERS = ("add_decision", "withdraw_decision")

    def test_no_route_writes_decisions(self):
        import httpx

        from coscc.api import build

        called: list[str] = []
        # Every public method of `Service` is a stub for this test, so a route posted an
        # empty body starts nothing; the two writers count their calls.
        stubs = {}
        for name in dir(Service):
            if (
                name.startswith("_")
                or not callable(getattr(Service, name))
                or isinstance(getattr(Service, name), type)
            ):
                continue
            stubs[name] = (lambda n: lambda *a, **k: called.append(n) or {})(name)
        with tempfile.TemporaryDirectory() as d, mock.patch.multiple(Service, **stubs):
            app = build(Config(workspaces=(d,), data_dir=str(Path(d) / "data")))

            async def post_all() -> int:
                n = 0
                transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
                async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                    for route in app.routes:
                        if "POST" not in (getattr(route, "methods", None) or ()):
                            continue
                        path = route.path.replace("{name}", "x")
                        await client.post(path, json={})
                        n += 1
                return n

            posted = asyncio.run(post_all())
        self.assertGreater(posted, 20)
        self.assertEqual([c for c in called if c in self.WRITERS], [])
        for path in sorted((REPO / "coscc" / "web").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for writer in self.WRITERS:
                self.assertNotIn(f".{writer}(", text, f"{path.name} calls {writer}")


if __name__ == "__main__":
    unittest.main()
