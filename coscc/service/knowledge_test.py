"""`0131` plan step 9: `Service.knowledge_page`, what the Knowledge page reads (R24-R26)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import knowledge, units
from coscc.agent.sessions import Sessions
from coscc.config import Config
from coscc.git import fetches
from coscc.knowledge import admit, gather
from coscc.service import Service


class TheKnowledgePage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "work" / "proj"
        (self.repo / ".git").mkdir(parents=True)
        self.data = self.root / "data"
        config = Config(workspaces=(str(self.repo),), working_dir=str(self.root / "work"),
                        data_dir=str(self.data), knowledge=True)
        self.service = Service(config, Sessions(config))
        self.slot = units.slot(str(self.repo))
        self.key = self.service._journal_key(str(self.repo))

    def write_store(self) -> None:
        src = f"{self.slot}/0001_a/plan.md ## Order of work"
        text = ("# Knowledge\nVersion: 2. Gathered: 2026-09-27T00:00:00Z. Max id: K3.\n\n"
                f"## K1\nScope: tool:reflex 0.9.12\nSource: {src}\nMeasured: 2026-09-25\nA tool fact.\n\n"
                f"## K2\nScope: workspace:{self.slot}\nSource: {src}\nRef: a.py\nMeasured: 2026-09-25\nA code fact.\n\n"
                "## K3\nScope: workspace:other-0123456789ab\nSource: other-0123456789ab/0001_a/spike.md ## U1\n"
                "Ref: b.py\nMeasured: 2026-09-25\nNot this workspace.\n")
        knowledge.save(knowledge.path_of(str(self.data)) / knowledge.STORE, text)

    def log(self) -> None:
        journal = self.service._journal()
        for n, (stage, arm) in enumerate((("spec", knowledge.ON), ("plan", knowledge.ON), ("pr", knowledge.OFF))):
            extra = {knowledge.TRIAL_FIELD: {"arm": arm}}
            if arm == knowledge.ON:
                extra["knowledge"] = {"version": "v", "entries": 1, "bytes": 9, "ids": ["K1"],
                                      "withheld": [{"id": "K2", "reason": "a.py is not on HEAD"}], "head": "f" * 40}
            journal.started(self.key, f"000{n}_u", stage, "manual", **extra)
        journal.started(self.key, "0009_before", "spec", "manual")
        journal.append({"kind": gather.KIND, "workspace": self.slot, "mode": "unit", "unit": "0001_a",
                        "cost_usd": 0.47, "outcome": "saved", "dropped": [], "sessions": []})

    def test_the_page_reads_entries_health_last_gather_recent_steps_and_the_measure(self):
        self.write_store()
        admit.save_health(str(self.data), {self.slot: "a" * 40}, {"K1": "", "K2": "ref missing: a.py"})
        self.log()
        page = self.service.knowledge_page(str(self.repo))
        self.assertEqual(page["note"], "")
        self.assertEqual([(e["id"], e["broken"]) for e in page["entries"]], [("K1", ""), ("K2", "ref missing: a.py")])
        self.assertEqual(page["entries"][0]["sources"],
                         [{"slot": self.slot, "unit": "0001_a", "file": "plan.md", "anchor": "## Order of work"}])
        self.assertEqual(page["checked"]["sha"], "a" * 40)
        self.assertEqual({k: page["last_gather"][k] for k in ("unit", "outcome", "cost_usd")},
                         {"unit": "0001_a", "outcome": "saved", "cost_usd": 0.47})
        # Newest first, and only the steps that carried the arm.
        self.assertEqual([(r["unit"], r["stage"], r["arm"]) for r in page["recent"]],
                         [("0002_u", "pr", "off"), ("0001_u", "plan", "on"), ("0000_u", "spec", "on")])
        self.assertEqual((page["recent"][1]["ids"], page["recent"][1]["withheld"]),
                         (["K1"], [{"id": "K2", "reason": "a.py is not on HEAD"}]))
        self.assertEqual(page["measure"]["workspace"], self.slot)
        self.assertEqual(page["measure"]["verdict"], "chưa đủ mẫu")

    def test_no_health_file_reads_not_checked_yet(self):
        self.write_store()
        page = self.service.knowledge_page(str(self.repo))
        self.assertIsNone(page["checked"])
        self.assertEqual([e["broken"] for e in page["entries"]], [None, None])

    def test_an_empty_store_is_one_sentence(self):
        self.assertEqual(self.service.knowledge_page(str(self.repo))["note"], "No knowledge has been gathered yet.")
        knowledge.save(knowledge.path_of(str(self.data)) / knowledge.STORE,
                       "# Knowledge\nVersion: 0. Gathered: never. Max id: K0.\n")
        page = self.service.knowledge_page(str(self.repo))
        self.assertEqual((page["note"], page["entries"]), ("The knowledge store holds no entry for this workspace.", []))
        (knowledge.path_of(str(self.data)) / knowledge.STORE).write_bytes(b"\xff not utf-8")
        self.assertEqual(self.service.knowledge_page(str(self.repo))["note"],
                         "The knowledge store cannot be read: UnicodeDecodeError.")

    def test_the_page_fetches_nothing(self):
        self.write_store()
        self.log()
        with mock.patch.object(fetches.shared, "fetch", side_effect=AssertionError("fetched")), \
                mock.patch.object(admit, "_git", side_effect=AssertionError("git")), \
                mock.patch.object(gather, "gather_unit", side_effect=AssertionError("gathered")):
            page = self.service.knowledge_page(str(self.repo))
        self.assertEqual(len(page["entries"]), 2)
        self.assertFalse((knowledge.path_of(str(self.data)) / knowledge.HEALTH).exists())
        json.dumps(page, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
