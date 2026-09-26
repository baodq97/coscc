"""`0090` plan step 1: the store, its reader and writer, and the check a gathered store passes."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from coscc import knowledge
from coscc.knowledge import CAP_BYTES, ENTRY_BYTES

SLOT = "coscc-fb0599d12eeb"
OTHER = "other-0123456789ab"


def entry(n: int, scope: str = "tool:claude-agent-sdk 0.2.158", source: str = "",
          measured: str = "2026-09-25", statement: str = "A measured fact.") -> str:
    source = source or f"{SLOT}/0088_a-unit/spike.md ## U6"
    return f"## K{n}\nScope: {scope}\nSource: {source}\nMeasured: {measured}\n{statement}"


def store(*blocks: str, version: int = 1, max_id: int = 0) -> str:
    head = f"# Knowledge\nVersion: {version}. Gathered: 2026-09-27T09:12:03Z. Max id: K{max_id}.\n"
    return head + ("\n" + "\n\n".join(blocks) + "\n" if blocks else "")


class ParseAndRender(unittest.TestCase):
    def test_a_rendered_store_goes_round_without_losing_a_byte(self):
        text = store(entry(1), entry(2, scope=f"workspace:{SLOT}"), version=4, max_id=12)
        parsed = knowledge.parse(text)
        self.assertEqual(parsed["header"], {"version": 4, "gathered": "2026-09-27T09:12:03Z", "max_id": 12})
        self.assertEqual(knowledge.render(parsed["header"], parsed["entries"]), text)
        self.assertEqual(parsed["skipped"], [])

    def test_an_entry_carries_its_fields(self):
        [e] = knowledge.parse(store(entry(7), max_id=7))["entries"]
        self.assertEqual((e["id"], e["scope"], e["measured"]), (7, "tool:claude-agent-sdk 0.2.158", "2026-09-25"))
        self.assertEqual(e["sources"], [f"{SLOT}/0088_a-unit/spike.md ## U6"])
        self.assertEqual(e["statement"], "A measured fact.")

    def test_a_broken_entry_is_skipped_and_the_rest_is_read(self):
        no_scope = "## K2\nSource: x/0001_a/spike.md ## U1\nMeasured: 2026-01-01\nS."
        no_source = "## K3\nScope: tool:x\nMeasured: 2026-01-01\nS."
        no_statement = "## K4\nScope: tool:x\nSource: x/0001_a/spike.md ## U1\nMeasured: 2026-01-01"
        parsed = knowledge.parse(store(entry(1), no_scope, no_source, no_statement, entry(5), max_id=5))
        self.assertEqual([e["id"] for e in parsed["entries"]], [1, 5])
        self.assertEqual(len(parsed["skipped"]), 3)

    def test_a_line_outside_every_entry_is_skipped_since_render_would_drop_it(self):
        text = store(entry(1), max_id=1).replace(
            "Max id: K1.\n", "Max id: K1.\nRemoved K4 by hand on 2026-09-20.\n# Knowledge\n")
        parsed = knowledge.parse(text)
        self.assertEqual([e["id"] for e in parsed["entries"]], [1])
        self.assertEqual(parsed["skipped"], ["a line outside every entry: Removed K4 by hand on 2026-09-20.",
                                             "a line outside every entry: # Knowledge"])
        # A second header is a stray line too, not a header that replaces the first.
        twice = store(entry(1), max_id=1).replace("\n## K1", "Version: 9. Gathered: x. Max id: K9.\n\n## K1")
        self.assertEqual((knowledge.parse(twice)["header"]["max_id"], len(knowledge.parse(twice)["skipped"])), (1, 1))
        # The lines under a heading that is not an entry are that block's, named once.
        self.assertEqual(len(knowledge.parse(store(entry(1)) + "\n## Notes\none\ntwo\n")["skipped"]), 1)

    def test_a_header_is_found_only_before_the_first_block(self):
        self.assertTrue(knowledge.has_header(store(entry(1), max_id=1)))
        self.assertFalse(knowledge.has_header(entry(1)))
        self.assertFalse(knowledge.has_header(entry(1) + "\nVersion: 1. Gathered: x. Max id: K1.\n"))

    def test_a_ref_is_read_in_order_and_a_block_without_one_reads(self):
        block = entry(4, scope=f"workspace:{SLOT}").replace(
            "\nMeasured:", "\nRef: coscc/gather.py::batches\nRef: coscc/admit.py\nMeasured:")
        [e] = knowledge.parse(store(block, max_id=4))["entries"]
        self.assertEqual(e["refs"], ["coscc/gather.py::batches", "coscc/admit.py"])
        [bare] = knowledge.parse(store(entry(5), max_id=5))["entries"]
        self.assertEqual(bare["refs"], [])

    def test_an_entry_formatted_from_its_fields_goes_round(self):
        block = entry(4, scope=f"workspace:{SLOT}").replace(
            "\nMeasured:", "\nSource: x-0123456789ab/0001_a/review.md Round 1\nRef: a.py::f\nMeasured:")
        [e] = knowledge.parse(block)["entries"]
        self.assertEqual(knowledge.format_entry(e), block)
        [again] = knowledge.parse(knowledge.format_entry(e))["entries"]
        self.assertEqual({k: again[k] for k in ("id", "scope", "sources", "refs", "measured", "statement")},
                         {k: e[k] for k in ("id", "scope", "sources", "refs", "measured", "statement")})
        self.assertNotIn("Measured:", knowledge.format_entry({**e, "measured": ""}))

    def test_an_empty_or_headerless_text_reads(self):
        self.assertEqual(knowledge.parse(""), {"header": knowledge.empty_header(), "entries": [], "skipped": []})
        self.assertEqual([e["id"] for e in knowledge.parse(entry(3))["entries"]], [3])


class WhatAWorkspaceReceives(unittest.TestCase):
    def test_another_workspaces_entries_are_not_given(self):
        entries = knowledge.parse(store(entry(1), entry(2, scope=f"workspace:{SLOT}"),
                                        entry(3, scope=f"workspace:{OTHER}"), max_id=3))["entries"]
        self.assertEqual([e["id"] for e in knowledge.for_workspace(entries, SLOT)], [1, 2])
        self.assertEqual([e["id"] for e in knowledge.for_workspace(entries, OTHER)], [1, 3])

    def test_the_slice_and_its_record(self):
        section, record = knowledge.slice_for(store(entry(2), entry(1), max_id=2), SLOT)
        self.assertEqual(section, entry(1) + "\n\n" + entry(2))
        self.assertEqual(record, {"version": knowledge.version_of(section), "entries": 2,
                                  "bytes": len(section.encode("utf-8"))})
        self.assertEqual(len(record["version"]), 12)

    def test_nothing_applicable_is_an_empty_slice(self):
        empty = ("", {"version": knowledge.version_of(""), "entries": 0, "bytes": 0})
        self.assertEqual(knowledge.slice_for("", SLOT), empty)
        self.assertEqual(knowledge.slice_for(store(entry(1, scope=f"workspace:{OTHER}"), max_id=1), SLOT), empty)

    def test_a_store_over_the_cap_gives_the_newest_whole_entries_in_id_order(self):
        long = "x" * 500
        blocks = [entry(n, statement=long) for n in range(1, 40)]
        section, record = knowledge.slice_for(store(*blocks, max_id=39), SLOT)
        self.assertLessEqual(record["bytes"], CAP_BYTES)
        self.assertEqual(record["bytes"], len(section.encode("utf-8")))
        ids = [e["id"] for e in knowledge.parse(section)["entries"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids[-1], 39)
        self.assertEqual(ids, list(range(40 - len(ids), 40)))
        self.assertEqual(record["entries"], len(ids))
        for n in ids:
            self.assertIn(entry(n, statement=long), section)


class TheCheckOfAGatheredStore(unittest.TestCase):
    """R13, one test per refusal."""

    OLD = entry(1) + "\n\n" + entry(2)
    BATCH = [f"{SLOT}/0090_b-unit/review.md"]

    def check(self, new: str, dropped=None, old: str | None = None, max_id: int = 2, **kw) -> list[str]:
        return knowledge.validate(self.OLD if old is None else old, new, dropped or [], self.BATCH, SLOT, max_id, **kw)

    def test_a_good_store_passes(self):
        new = self.OLD + "\n\n" + entry(3, source=f"{SLOT}/0090_b-unit/review.md Round 2 F3")
        self.assertEqual(self.check(new), [])

    def test_over_the_cap(self):
        blocks = [entry(n, statement="y" * 500) for n in range(3, 25)]
        reasons = self.check("\n\n".join([self.OLD, *blocks]))
        self.assertTrue(any(f"over {CAP_BYTES}" in r for r in reasons), reasons)

    def test_over_the_cap_for_another_workspace(self):
        other = knowledge.parse("\n\n".join(entry(n, scope=f"workspace:{OTHER}", statement="z" * 500)
                                            for n in range(10, 24)))["entries"]
        new = self.OLD + "\n\n" + entry(3, statement="t" * 500)
        reasons = self.check(new, others=other)
        self.assertTrue(any(f"workspace:{OTHER} would receive" in r for r in reasons), reasons)

    def test_a_source_outside_the_store_and_the_batch(self):
        new = self.OLD + "\n\n" + entry(3, source=f"{SLOT}/0001_invented/spike.md ## U1")
        self.assertTrue(any("0001_invented/spike.md" in r for r in self.check(new)))

    def test_a_malformed_anchor(self):
        for bad in (f"{SLOT}/0090_b-unit/review.md ## U1", f"{SLOT}/0088_a-unit/spike.md Round 1",
                    f"{SLOT}/0090_b-unit/review.md", "/home/bd/x/spike.md ## U1"):
            with self.subTest(source=bad):
                self.assertTrue(self.check(self.OLD + "\n\n" + entry(3, source=bad)))

    def test_an_old_entry_renumbered_without_being_dropped(self):
        new = entry(1) + "\n\n" + entry(3)
        self.assertTrue(any("K2 is gone" in r for r in self.check(new)))

    def test_a_new_id_not_above_the_max(self):
        reasons = self.check(self.OLD + "\n\n" + entry(3), max_id=5)
        self.assertTrue(any("K3 is new but not above" in r for r in reasons), reasons)

    def test_a_new_id_another_workspace_holds(self):
        # A header below the ids the store holds would let K3 past the Max id check alone.
        other = knowledge.parse(entry(3, scope=f"workspace:{OTHER}"))["entries"]
        reasons = self.check(self.OLD + "\n\n" + entry(3), others=other)
        self.assertTrue(any("K3 is new but another workspace's entry already holds it" in r for r in reasons), reasons)
        self.assertEqual(self.check(self.OLD + "\n\n" + entry(4), others=other), [])

    def test_an_old_id_gone_and_not_dropped(self):
        self.assertTrue(any("K2 is gone" in r for r in self.check(entry(1))))
        self.assertEqual(self.check(entry(1), dropped=[{"id": "K2", "reason": "merged", "merged_into": "K1"}]), [])

    def test_a_drop_with_no_reason(self):
        self.assertTrue(self.check(entry(1), dropped=[{"id": "K2", "reason": ""}]))

    def test_another_workspaces_scope(self):
        new = self.OLD + "\n\n" + entry(3, scope=f"workspace:{OTHER}")
        self.assertTrue(any(f"workspace:{OTHER}" in r for r in self.check(new)))

    def test_a_models_measured_is_ignored(self):
        # `0108` R1: the code writes the date, so what the session wrote is not a reason.
        for bad in ("25/09/2026", "2026-9-25", "2026-13-01"):
            with self.subTest(measured=bad):
                self.assertEqual(self.check(self.OLD + "\n\n" + entry(3, measured=bad)), [])

    def test_a_tool_entry_carrying_a_ref(self):
        tool = entry(3).replace("\nMeasured:", "\nRef: coscc/gather.py\nMeasured:")
        self.assertIn("K3 is a tool: entry and carries Ref:", self.check(self.OLD + "\n\n" + tool))
        ws = entry(3, scope=f"workspace:{SLOT}").replace("\nMeasured:", "\nRef: coscc/gather.py\nMeasured:")
        self.assertEqual(self.check(self.OLD + "\n\n" + ws), [])

    def test_a_merge_into_an_older_entry(self):
        # `0108` R3: K2 cites a source newer than K1's, so it may not be merged into K1.
        old = entry(1) + "\n\n" + entry(2, source=f"{SLOT}/0090_b-unit/review.md Round 1")
        merged = [{"id": "K2", "reason": "same", "merged_into": "K1"}]
        dates = {f"{SLOT}/0088_a-unit/spike.md": "2026-09-23", f"{SLOT}/0090_b-unit/review.md": "2026-09-25"}
        reasons = self.check(entry(1), dropped=merged, old=old, dates=dates)
        self.assertTrue(any("K2" in r and "K1" in r and "older" in r for r in reasons), reasons)
        # Without dates R3 is not checked.
        self.assertEqual(self.check(entry(1), dropped=merged, old=old), [])

    def test_a_merge_into_an_entry_of_the_same_date(self):
        old = entry(1) + "\n\n" + entry(2, source=f"{SLOT}/0090_b-unit/review.md Round 1")
        merged = [{"id": "K2", "reason": "same", "merged_into": "K1"}]
        dates = {f"{SLOT}/0088_a-unit/spike.md": "2026-09-25", f"{SLOT}/0090_b-unit/review.md": "2026-09-25"}
        self.assertEqual(self.check(entry(1), dropped=merged, old=old, dates=dates), [])

    def test_one_entry_over_its_own_ceiling(self):
        reasons = self.check(self.OLD + "\n\n" + entry(3, statement="w" * ENTRY_BYTES))
        self.assertTrue(any(f"over {ENTRY_BYTES}" in r for r in reasons), reasons)

    def test_an_unreadable_block_is_a_reason(self):
        self.assertTrue(self.check(self.OLD + "\n\n## K3\nScope: tool:x\nMeasured: 2026-01-01\nS."))

    def test_words_outside_every_entry_are_a_reason(self):
        reasons = self.check("Here is the store you asked for.\n\n" + self.OLD)
        self.assertIn("a line outside every entry: Here is the store you asked for.", reasons)

    def test_the_check_touches_no_disk(self):
        with tempfile.TemporaryDirectory() as d:
            before = os.listdir(d)
            cwd = os.getcwd()
            os.chdir(d)
            try:
                self.check(self.OLD)
            finally:
                os.chdir(cwd)
            self.assertEqual(os.listdir(d), before)


class SavingIsAtomic(unittest.TestCase):
    def test_a_reader_sees_the_old_file_or_the_new_one(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "knowledge" / knowledge.STORE
            old, new = "A" * 200_000, "B" * 300_000
            knowledge.save(path, old)
            seen: set[str] = set()
            stop = threading.Event()

            def read() -> None:
                while not stop.is_set():
                    text = knowledge.load(path)
                    seen.add(text[:1] + str(len(text)))

            reader = threading.Thread(target=read)
            reader.start()
            try:
                for i in range(20):
                    knowledge.save(path, new if i % 2 == 0 else old)
            finally:
                stop.set()
                reader.join()
            for s in seen:
                self.assertIn(s, {"A200000", "B300000"})
            self.assertEqual(sorted(os.listdir(path.parent)), [knowledge.STORE])

    def test_a_failed_write_leaves_the_old_file_and_no_temporary(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / knowledge.STORE
            knowledge.save(path, "old")
            with mock.patch.object(knowledge.os, "fsync", side_effect=OSError("disk full")), \
                    self.assertRaises(OSError):
                knowledge.save(path, "new")
            self.assertEqual(knowledge.load(path), "old")
            self.assertEqual(os.listdir(d), [knowledge.STORE])


class ReadingForAStep(unittest.TestCase):
    def test_a_store_that_cannot_be_read_is_no_section_and_a_reason(self):
        with tempfile.TemporaryDirectory() as d:
            got = knowledge.for_step(d, SLOT)
            self.assertEqual(got["knowledge"], "")
            self.assertEqual(got["knowledge_record"]["entries"], 0)
            self.assertIn("FileNotFoundError", got["knowledge_record"]["error"])
            path = knowledge.path_of(d) / knowledge.STORE
            path.parent.mkdir(parents=True)
            path.write_bytes(b"\xff\xfe not utf-8")
            self.assertIn("UnicodeDecodeError", knowledge.for_step(d, SLOT)["knowledge_record"]["error"])

    def test_a_store_that_reads_gives_the_slice(self):
        with tempfile.TemporaryDirectory() as d:
            knowledge.save(knowledge.path_of(d) / knowledge.STORE, store(entry(1), max_id=1))
            got = knowledge.for_step(d, SLOT)
            self.assertEqual(got["knowledge"], entry(1))
            self.assertEqual(got["knowledge_record"]["entries"], 1)
            self.assertNotIn("error", got["knowledge_record"])


if __name__ == "__main__":
    unittest.main()
