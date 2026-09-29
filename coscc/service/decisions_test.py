"""`0137` R4-R6, R11, R12: the person's decisions, the names in answers, and the measure.

Every write here goes through `Service`, as the Settings screen's handlers do; no route
reaches it (`test_no_route_writes_decisions_or_names`).
"""

from __future__ import annotations

import asyncio
import hashlib
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
from coscc.service.service_test import create_sync

REPO = Path(__file__).resolve().parents[2]

INTENT = (
    "# Intent: q\nAuthor: t. Type: feat. Status: accepted.\n\n## Open questions\n\n"
    "1. A?\n2. B?\n3. C?\n4. D?\n5. E?\n\n## Answers\n"
)
SPEC = "# Spec: q\nIntent: intent.md. Author: t. Status: accepted.\n\n## Open questions\n\n1. A?\n2. B?\n\n## Answers\n"


def block(n: int, by: str, text: str = "Có.") -> str:
    return f"\n### Câu {n}\nAnswered by: {by}. Date: 2026-09-01. Via: product.\n\n{text}\n"


class Fixture(unittest.TestCase):
    """One workspace, `proj`, whose unit's answers carry every kind of name."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.work, self.data = self.root / "work", self.root / "data"
        self.cwd = str(self.work / "proj")
        Path(self.cwd).mkdir(parents=True)
        self.config = Config(workspaces=(self.cwd,), working_dir=str(self.work), data_dir=str(self.data))
        self.service = Service(self.config, Sessions(self.config))
        made = create_sync(self.service, self.cwd, "answered", "x")
        self.unit, self.dir = made["unit"], Path(made["path"])
        (self.dir / "intent.md").write_text(
            INTENT + block(1, "owner") + block(2, "Phong") + block(3, "Leif (CoS), thay người khởi xướng")
            + block(4, "Kenaz (agent, spec)") + block(5, "Minh"), encoding="utf-8")
        (self.dir / "spec.md").write_text(SPEC + block(1, "phong") + block(2, "Jera"), encoding="utf-8")
        self.asked = create_sync(self.service, self.cwd, "asked", "x")["unit"]

    def store(self) -> dict[str, str]:
        units = asyncio.run(self.service.board(self.cwd))["units"]
        found = next(u for u in units if u["name"] == self.asked)
        _, store, _ = self.service._precedent_prompt(units, found, self.asked, cwd=self.cwd)
        return {e["id"]: e["who"] for e in store}


class TheDecisionsPanel(Fixture):
    """R4, R5."""

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
        for fields in (self.GOOD, {**delegation, "workspace": "proj", "until": date.today().isoformat()}):
            self.service.add_decision(fields)
        rows = self.service.decisions_table()["rows"]
        self.assertEqual([(r["id"], r["state"], r["workspace_name"]) for r in rows],
                         [("D1", "in force", "All workspaces"), ("D2", "in force", "proj")])
        self.assertEqual(rows[1]["from_day"], date.today().isoformat(), "from is the day it was entered")

    def test_a_decision_is_withdrawn_not_deleted(self):
        added = self.service.add_decision(self.GOOD)["added"]
        Data(self.data).decision_add(kind="decision", text="cũ", source="s", from_day="2026-01-01",
                                     until_day="2026-01-02", workspace="gone-000000000000")
        table = self.service.withdraw_decision(added)
        self.assertEqual([(r["id"], r["state"]) for r in table["rows"]], [("D1", "withdrawn"), ("D2", "expired")])
        self.assertEqual(table["rows"][1]["workspace_name"], "a removed workspace")
        for d in ("D1", "D2", "D9", ""):
            with self.assertRaises(Invalid):
                self.service.withdraw_decision(d)
        self.assertEqual(len(Data(self.data).decisions()), 2)


class TheNamesPanel(Fixture):
    """R6, R2."""

    def names(self) -> list[tuple[str, int, bool]]:
        return [(r["name"], r["count"], r["mine"]) for r in asyncio.run(self.service.answer_names())["rows"]]

    def test_names_in_answers_lists_every_non_owner_non_agent_name_with_its_count(self):
        self.assertEqual(self.names(), [("Phong", 2, False), ("Minh", 1, False)])

    def test_marking_a_name_mine_makes_its_answers_originator_in_the_store(self):
        before = self.store()
        self.assertEqual((before[f"{self.unit}/intent.md#Câu 2"], before[f"{self.unit}/spec.md#Câu 1"]),
                         ("inferred", "inferred"))
        self.service.set_name_mine("PHONG", True)
        after = self.store()
        self.assertEqual((after[f"{self.unit}/intent.md#Câu 2"], after[f"{self.unit}/spec.md#Câu 1"]),
                         ("originator", "originator"))
        self.assertEqual(after[f"{self.unit}/intent.md#Câu 5"], "inferred")
        self.assertEqual(self.names(), [("Phong", 2, True), ("Minh", 1, False)])
        self.service.set_name_mine("Phong", False)
        self.assertEqual(self.store()[f"{self.unit}/intent.md#Câu 2"], "inferred")

    def test_an_agent_name_cannot_be_marked_mine(self):
        for name in ("Leif (CoS)", "kenaz", "Jera", "owner", "", "a\nb"):
            with self.assertRaises(Invalid, msg=name):
                self.service.set_name_mine(name, True)
        self.assertIsNone(Data(self.data).pref("answer_names_mine"))
        self.assertNotIn("answer_names_mine", self.service.PREFERENCES)
        with self.assertRaises(Invalid):
            self.service.set_preference("answer_names_mine", "Leif")

    def test_building_the_store_and_reading_names_leave_every_md_byte_for_byte(self):
        """R11."""
        def hashes() -> dict[str, str]:
            return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(self.data.rglob("*.md"))}

        before = hashes()
        self.assertTrue(before)
        self.store()
        asyncio.run(self.service.answer_names())
        self.assertEqual(hashes(), before)


class NoRouteReachesThem(unittest.TestCase):
    """R4, R6: only the Settings screen's handlers call the three writers."""

    WRITERS = ("add_decision", "withdraw_decision", "set_name_mine")

    def test_no_route_writes_decisions_or_names(self):
        import httpx

        from coscc.web.api import build

        called: list[str] = []
        # Every public method of `Service` is a stub for this test, so a route posted an
        # empty body starts nothing; the three writers count their calls.
        stubs = {}
        for name in dir(Service):
            if name.startswith("_") or not callable(getattr(Service, name)) or isinstance(getattr(Service, name), type):
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
