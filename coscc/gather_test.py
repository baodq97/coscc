"""`0090` plan step 6: `coscc knowledge gather`, with a stand-in for the session.

Nothing here opens a session. The stand-in returns a reply built in advance, the way
`coscc/precedent_test.py` stands in for Jera's.

Since `0108` each workspace is a real git repository, built once for the module, and each
source has the `done` `end` row that dates it: `admit` reads both.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import admit, gather, knowledge, units
from coscc.admit_test import lock, make_repo
from coscc.journal import Journal

# Set by `setUpModule`: the slots of two repositories, `other-` sorting before `proj-`.
A = B = ""
REPOS: dict[str, Path] = {}
AT = "2026-09-25T00:00:00+00:00"


def setUpModule():
    global A, B, _REPOS_DIR
    _REPOS_DIR = tempfile.TemporaryDirectory()
    for name in ("proj", "other"):
        repo = make_repo(Path(_REPOS_DIR.name) / name, ("2026-09-01T00:00:00+00:00", {
            ".python-version": "3.14\n", "uv.lock": lock(**{"claude-agent-sdk": "0.2.158"}), "README.md": "x\n"}))
        REPOS[units.slot(repo)] = repo
    A, B = sorted(REPOS, reverse=True)


def tearDownModule():
    _REPOS_DIR.cleanup()


class Replies:
    """Each call takes the next reply; `prompts` keeps what was sent. `cost=None` reports
    none, as a session that broke may not."""

    def __init__(self, *replies: str, cost: float | None = 0.25):
        self.replies, self.cost = list(replies), cost
        self.prompts: list[str] = []
        self.cwds: list[str] = []
        self.membership = lambda d: True

    async def stream(self, cwd, text, session_id=None, **kw):
        self.prompts.append(text)
        self.cwds.append(cwd)
        yield ("chunk", self.replies.pop(0))
        cost = {"turns": 1} if self.cost is None else {"cost_usd": self.cost, "turns": 1}
        yield ("done", {"session_id": f"s{len(self.prompts)}", "cost": cost})


def reply(store: str, dropped=None) -> str:
    return "```json\n" + json.dumps({"store": store, "dropped": dropped or []}) + "\n```"


def entry(n: int, source: str, scope: str = "tool:claude-agent-sdk 0.2.158", ref: str = "README.md") -> str:
    """A `workspace:` entry carries `Ref: <ref>`, a file both repositories hold (`0108` R5)."""
    refs = f"Ref: {ref}\n" if scope.startswith("workspace:") else ""
    return f"## K{n}\nScope: {scope}\nSource: {source}\n{refs}Measured: 2026-09-25\nA fact {n}."


def sized(first: int, total: int, source: str) -> str:
    """Entries from `K<first>` whose text `validate` measures at exactly `total` bytes, each
    of them 500 bytes but the last, so the only reason to refuse them is the store's cap."""
    n = total // 502 + 1
    blocks = [entry(first + k, source) for k in range(n)]
    blocks = [b + "x" * (500 - len(b)) for b in blocks[:-1]] + [blocks[-1]]
    blocks[-1] += "x" * (total - len("\n\n".join(blocks)))
    assert all(len(b) <= knowledge.ENTRY_BYTES for b in blocks)
    return "\n\n".join(blocks)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.work = self.root / "work"
        self.work.mkdir()
        self.journal = Journal(self.work, self.data)
        self.said: list[str] = []
        self.dir = knowledge.path_of(str(self.data))
        # The run log names both repositories, so a slot with no source still has a path.
        for repo in REPOS.values():
            self.journal.finished(str(repo), "0000_known", "plan", "done", at=AT)

    def unit(self, slot: str, unit: str, dated: str = AT, **files: str) -> Path:
        """The files, and for each `spike.md` or `review.md` the `done` `end` row that dates it
        at `dated`; `dated=""` writes none."""
        d = self.data / "units" / slot / ".cos" / unit
        d.mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            (d / name.replace("_", ".")).write_text(body, encoding="utf-8")
            stage = name.split("_")[0]
            if dated and stage in ("spike", "review"):
                self.journal.finished(str(REPOS[slot]), unit, stage, "done", at=dated)
        return d

    def run_gather(self, sessions, mode="new") -> int:
        return asyncio.run(gather.gather(str(self.data), self.journal, sessions, "m", mode, self.said.append))

    def rows(self) -> list[dict]:
        return self.journal.records(kind="knowledge")

    def store(self) -> str:
        try:
            return knowledge.load(self.dir / knowledge.STORE)
        except FileNotFoundError:
            return ""

    def manifest(self) -> dict:
        return gather.load_manifest(self.dir / knowledge.SOURCES)


class TheSources(Fixture):
    def test_only_spike_and_review_of_units_with_their_answers_cut(self):
        self.unit(A, "0001_a", spike_md="# Spike\n## U1\nmeasured\n\n## Answers\n\n### Câu 1\nsecret\n",
                  review_md="# Review\nRound 1\n", plan_md="# Plan\n", intent_md="# Intent\n")
        (self.data / "units" / A / ".cos" / "notaunit").mkdir()
        (self.data / "units" / A / ".cos" / "notaunit" / "spike.md").write_text("x")
        found = gather.sources(str(self.data))
        self.assertEqual([s["label"] for s in found], [f"{A}/0001_a/review.md", f"{A}/0001_a/spike.md"])
        spike = found[1]
        self.assertNotIn("## Answers", spike["text"])
        self.assertNotIn("secret", spike["text"])
        self.assertEqual(spike["sha"], gather.digest(spike["text"]))

    def test_batches_split_by_workspace_and_size_and_a_big_source_goes_whole(self):
        found = [
            {"slot": A, "unit": "0002_b", "file": "spike.md", "text": "x" * 40, "label": "a2"},
            {"slot": A, "unit": "0001_a", "file": "spike.md", "text": "x" * 40, "label": "a1"},
            {"slot": A, "unit": "0003_c", "file": "review.md", "text": "x" * 500, "label": "a3"},
            {"slot": B, "unit": "0001_a", "file": "spike.md", "text": "x" * 10, "label": "b1"},
        ]
        parts = gather.batches(found, cap=100)
        # `other-` sorts before `proj-`.
        self.assertEqual([[s["label"] for s in b] for b in parts], [["b1"], ["a1", "a2"], ["a3"]])

    def test_the_first_batch_of_a_workspace_holds_its_newest_source(self):
        # `0108` R2: newest first, then `(unit, file)`; an undated source goes last.
        found = [
            {"slot": A, "unit": f"000{n}_x", "file": "spike.md", "text": "x" * 40, "label": f"a{n}"}
            for n in range(1, 6)
        ]
        dates = {"a1": {"date": "2026-09-23"}, "a2": {"date": "2026-09-26"}, "a4": {"date": "2026-09-26"},
                 "a5": {"date": "2026-09-24"}}
        parts = gather.batches(found, cap=100, dates=dates)
        self.assertEqual([[s["label"] for s in b] for b in parts], [["a2", "a4"], ["a5", "a1"], ["a3"]])

    def test_the_prompt_dates_each_source_and_says_what_is_not_knowledge(self):
        # `0108` R9, held word for word.
        batch = [{"label": f"{A}/0001_a/spike.md", "text": "x"}, {"label": f"{A}/0002_b/review.md", "text": "y"}]
        prompt = gather.build_prompt(A, 3, "", batch, {f"{A}/0001_a/spike.md": {"date": "2026-09-24"}})
        self.assertIn(f"### {A}/0001_a/spike.md (measured 2026-09-24)\n", prompt)
        self.assertIn(f"### {A}/0002_b/review.md (measured: no date)\n", prompt)
        for line in (
            "- Prefer knowledge about tools and how to use a framework.",
            "- Write a `workspace:` entry only for a pitfall the current code does not show. What the "
            "code says for itself, an agent can read.",
            "- Do not include anything a source itself says was not run, not measured or only "
            "derived, in any language. An entry, or a source, holding one of these is dropped: "
            "not run, not measured, not verified, unverified, derived, không kiểm, chưa kiểm, "
            "không chạy, chưa chạy, không đo, chưa đo.",
            "Scope: tool:<name>   or   workspace:" + A,
            "Ref: <path>[::<symbol>]",
            "- `Scope: tool:<name>` carries no version, and a `tool:` entry has no `Ref:`.",
            "- A `workspace:` entry carries at least one `Ref:`: a file's path from the repository's "
            "root, optionally followed by `::` and a name that file holds. An entry whose `Ref:` is "
            "not on `main` is dropped.",
            "- Write no `Measured:` line and no version: the code writes the date from the run log "
            "and the version from the workspace's pins.",
            "- Copy a source's label without the parenthesis after it.",
        ):
            with self.subTest(line=line[:40]):
                self.assertIn(line + "\n", prompt)
        self.assertNotIn("Measured: YYYY-MM-DD", prompt)


class AGather(Fixture):
    def setUp(self):
        super().setUp()
        self.unit(A, "0001_a", spike_md="# Spike\n## U1\nthe SDK passes no flag\n\n## Answers\n\n### Câu 1\nsecret\n")
        self.src = f"{A}/0001_a/spike.md ## U1"

    def test_a_good_reply_writes_the_store_the_manifest_and_a_done_row(self):
        s = Replies(reply(entry(1, self.src)))
        self.assertEqual(self.run_gather(s), 0)
        parsed = knowledge.parse(self.store())
        self.assertEqual(parsed["header"]["version"], 1)
        self.assertEqual(parsed["header"]["max_id"], 1)
        self.assertEqual([e["id"] for e in parsed["entries"]], [1])
        self.assertEqual(self.manifest(), {f"{A}/0001_a/spike.md": gather.sources(str(self.data))[0]["sha"]})
        [row] = self.rows()
        self.assertEqual((row["workspace"], row["mode"], row["batch"], row["of"], row["outcome"]), (A, "new", 1, 1, "done"))
        self.assertEqual((row["sources"], row["labels"], row["entries_before"], row["entries_after"]),
                         (1, [f"{A}/0001_a/spike.md"], 0, 1))
        self.assertEqual((row["cost_usd"], row["session_id"], row["model"]), (0.25, "s1", "m"))
        self.assertEqual(s.cwds, [str(self.dir)])

    def test_what_is_sent_holds_no_answers_and_no_absolute_path(self):
        s = Replies(reply(entry(1, self.src)))
        self.run_gather(s)
        [prompt] = s.prompts
        self.assertNotIn("## Answers", prompt)
        self.assertNotIn("secret", prompt)
        self.assertNotIn(str(self.root), prompt)
        self.assertIn(f"### {A}/0001_a/spike.md", prompt)

    def assert_refused(self, s: Replies, why: str) -> None:
        store, manifest = self.store(), self.manifest()
        self.assertEqual(self.run_gather(s), 1)
        self.assertEqual((self.store(), self.manifest()), (store, manifest))
        row = self.rows()[-1]
        self.assertEqual(row["outcome"], "failed")
        self.assertIn(why, row["reason"])
        self.assertEqual(row["dropped"], [])

    def test_an_invented_source_is_refused(self):
        self.assert_refused(Replies(reply(entry(1, f"{A}/0009_invented/spike.md ## U1"))), "0009_invented")

    def test_an_id_used_again_is_refused(self):
        self.run_gather(Replies(reply(entry(1, self.src))))
        self.unit(A, "0002_b", review_md="# Review\n## Round 1\nF1 x\n")
        new = f"{A}/0002_b/review.md Round 1 F1"
        # K1 dropped, and a new entry takes K1 again.
        self.assert_refused(Replies(reply(entry(1, new), [{"id": "K1", "reason": "old"}])), "K1")

    def test_a_reply_without_json_is_refused(self):
        self.assert_refused(Replies("I could not do it."), "no JSON")

    def test_a_second_gather_sends_nothing_unchanged(self):
        self.run_gather(Replies(reply(entry(1, self.src))))
        spike = self.data / "units" / A / ".cos" / "0001_a" / "spike.md"
        spike.write_text(spike.read_text() + "\n### Câu 2\nanother answer\n", encoding="utf-8")
        s = Replies()
        self.assertEqual(self.run_gather(s), 0)
        self.assertEqual(s.prompts, [])
        self.assertEqual(len(self.rows()), 1)

    def test_a_changed_source_is_sent_again_with_the_store(self):
        self.run_gather(Replies(reply(entry(1, self.src))))
        spike = self.data / "units" / A / ".cos" / "0001_a" / "spike.md"
        spike.write_text("# Spike\n## U1\nmeasured again, differently\n", encoding="utf-8")
        s = Replies(reply(entry(1, self.src)))
        self.assertEqual(self.run_gather(s), 0)
        self.assertIn("## K1", s.prompts[0])
        self.assertIn("Max id: K1", s.prompts[0])
        self.assertIn("from K2 upward", s.prompts[0])
        self.assertEqual(knowledge.parse(self.store())["header"]["version"], 2)

    def test_the_session_may_run_only_in_the_stores_directory(self):
        s = Replies(reply(entry(1, self.src)))
        self.run_gather(s)
        self.assertTrue(s.membership(str(self.dir)))
        self.assertFalse(s.membership(str(self.root)))
        self.assertFalse(s.membership(str(self.data / "units")))

    def test_a_second_gather_at_the_same_time_is_refused(self):
        with gather._Lock(self.dir):
            with self.assertRaises(gather.Refused):
                self.run_gather(Replies())

    def test_a_store_with_a_block_it_cannot_read_is_refused_before_a_session(self):
        # Every save renders only what `parse` read, so gathering would delete these (R5).
        written = knowledge.render(knowledge.empty_header(), []) + (
            "\n## K1\nScope: tool:x\nMeasured: 2026-09-25\nwritten by hand, no source\n"
            "\n## Notes\nkept by a person\n")
        knowledge.save(self.dir / knowledge.STORE, written)
        for mode in gather.MODES:
            with self.subTest(mode=mode):
                s = Replies(reply(entry(2, self.src)))
                with self.assertRaises(gather.Refused) as refused:
                    self.run_gather(s, mode)
                self.assertIn("K1 has no Source:", str(refused.exception))
                self.assertIn("## Notes", str(refused.exception))
                self.assertEqual((s.prompts, self.rows(), self.store()), ([], [], written))

    def test_a_note_under_the_header_is_refused_before_a_session(self):
        written = knowledge.render({"version": 1, "gathered": "never", "max_id": 1}, []).replace(
            "Max id: K1.\n", "Max id: K1.\nRemoved K4 by hand on 2026-09-20.\n") + "\n" + entry(1, self.src) + "\n"
        knowledge.save(self.dir / knowledge.STORE, written)
        s = Replies(reply(entry(2, self.src)))
        with self.assertRaises(gather.Refused) as refused:
            self.run_gather(s)
        self.assertIn("Removed K4 by hand", str(refused.exception))
        self.assertEqual((s.prompts, self.rows(), self.store()), ([], [], written))

    def test_new_ids_start_above_every_id_the_store_holds_whatever_its_header_says(self):
        # B's K5, under a header edited down to K2.
        held = entry(5, f"{B}/0001_a/spike.md ## U1", scope=f"workspace:{B}")
        knowledge.save(self.dir / knowledge.STORE, "# Knowledge\nVersion: 3. Gathered: never. Max id: K2.\n\n" + held + "\n")
        refused = Replies(reply(entry(5, self.src)))
        self.assertEqual(self.run_gather(refused), 1)
        self.assertIn("from K6 upward", refused.prompts[0])
        self.assertIn("K5 is new but not above the store's Max id K5", self.rows()[-1]["reason"])
        self.assertEqual(self.run_gather(Replies(reply(entry(6, self.src)))), 0)
        parsed = knowledge.parse(self.store())
        self.assertEqual(([e["id"] for e in parsed["entries"]], parsed["header"]["max_id"]), ([5, 6], 6))

    def test_a_store_holding_entries_under_no_header_is_refused_before_a_session(self):
        # K3 was given out and dropped; only the lost header knew, so K3 could be taken again.
        written = entry(1, self.src) + "\n\n" + entry(2, self.src) + "\n"
        knowledge.save(self.dir / knowledge.STORE, written)
        s = Replies(reply(entry(3, self.src)))
        with self.assertRaises(gather.Refused) as refused:
            self.run_gather(s)
        self.assertIn("no `Version: … Max id: K<n>.` line", str(refused.exception))
        self.assertEqual((s.prompts, self.rows(), self.store()), ([], [], written))

    def test_new_ids_start_above_every_id_a_gather_dropped_whatever_the_header_says(self):
        self.run_gather(Replies(reply(entry(1, self.src) + "\n\n" + entry(2, self.src) + "\n\n" + entry(3, self.src))))
        self.unit(A, "0002_b", review_md="# Review\n## Round 1\nF1 x\n")
        self.assertEqual(self.run_gather(Replies(reply(
            entry(1, self.src) + "\n\n" + entry(2, self.src), [{"id": "K3", "reason": "wrong"}]))), 0)
        # A person edits the header down to K2, below the dropped K3.
        knowledge.save(self.dir / knowledge.STORE, self.store().replace("Max id: K3.", "Max id: K2."))
        self.unit(A, "0003_c", review_md="# Review\n## Round 1\nF1 y\n")
        s = Replies(reply(entry(1, self.src) + "\n\n" + entry(2, self.src) + "\n\n" + entry(3, f"{A}/0003_c/review.md Round 1 F1")))
        self.assertEqual(self.run_gather(s), 1)
        self.assertIn("from K4 upward", s.prompts[0])
        self.assertIn("K3 is new but not above the store's Max id K3", self.rows()[-1]["reason"])

    def test_the_highest_dropped_id_counts_only_done_rows(self):
        self.journal.append({"kind": gather.KIND, "outcome": "done", "dropped": [{"id": "K9", "reason": "x"}, "junk"]})
        self.journal.append({"kind": gather.KIND, "outcome": "failed", "dropped": [{"id": "K40", "reason": "x"}]})
        self.assertEqual(gather.dropped_max(self.journal), 9)

    def test_an_undated_entry_is_dropped_and_its_row_says_so(self):
        self.unit(A, "0002_b", dated="", review_md="# Review\n## Round 1\nF1 x\n")
        s = Replies(reply(entry(1, f"{A}/0002_b/review.md Round 1 F1")))
        self.assertEqual(self.run_gather(s), 0)
        self.assertIn("(measured: no date)", s.prompts[0])
        self.assertEqual(knowledge.parse(self.store())["entries"], [])
        [row] = self.rows()
        self.assertEqual((row["outcome"], row["entries_after"], row["dropped"]),
                         ("done", 0, [{"id": "K1", "reason": "undated"}]))

    def test_an_id_admit_dropped_is_not_given_out_again(self):
        self.unit(A, "0002_b", dated="", review_md="# Review\n## Round 1\nF1 x\n")
        self.assertEqual(self.run_gather(Replies(reply(entry(1, f"{A}/0002_b/review.md Round 1 F1")))), 0)
        self.assertEqual(knowledge.parse(self.store())["header"]["max_id"], 1)
        self.unit(A, "0003_c", review_md="# Review\n## Round 1\nF1 y\n")
        s = Replies(reply(entry(1, f"{A}/0003_c/review.md Round 1 F1")))
        self.assertEqual(self.run_gather(s), 1)
        self.assertIn("from K2 upward", s.prompts[0])
        self.assertIn("K1 is new but not above the store's Max id K1", self.rows()[-1]["reason"])

    def test_a_merge_into_an_entry_of_older_sources_fails_the_batch(self):
        self.unit(A, "0002_b", dated="2026-09-20T00:00:00+00:00", review_md="# Review\n## Round 1\nF1 x\n")
        old_src = f"{A}/0002_b/review.md Round 1 F1"
        self.assertEqual(self.run_gather(Replies(reply(entry(1, old_src) + "\n\n" + entry(2, self.src)))), 0)
        self.unit(A, "0003_c", review_md="# Review\n## Round 1\nF1 y\n")
        s = Replies(reply(entry(1, old_src), [{"id": "K2", "reason": "same", "merged_into": "K1"}]))
        self.assertEqual(self.run_gather(s), 1)
        self.assertIn("K2 is merged into K1", self.rows()[-1]["reason"])

    def test_a_run_log_or_a_git_it_cannot_read_is_refused_before_a_session(self):
        class Broken:
            def records(self, **kw):
                raise OSError("disk gone")

        s = Replies(reply(entry(1, self.src)))
        with self.assertRaises(gather.Refused) as refused:
            asyncio.run(gather.gather(str(self.data), Broken(), s, "m", "new", self.said.append))
        self.assertIn("the run log cannot be read", str(refused.exception))
        with mock.patch.object(admit, "git_runs", return_value=False):
            with self.assertRaises(gather.Refused) as refused:
                self.run_gather(s)
        self.assertIn("git cannot be run", str(refused.exception))
        self.assertEqual((s.prompts, self.rows(), self.store()), ([], [], ""))

    def test_a_store_holding_an_id_twice_is_refused_before_a_session(self):
        knowledge.save(self.dir / knowledge.STORE, entry(3, self.src) + "\n\n"
                       + entry(3, f"{B}/0001_a/spike.md ## U1", scope=f"workspace:{B}") + "\n")
        s = Replies(reply(entry(4, self.src)))
        with self.assertRaises(gather.Refused) as refused:
            self.run_gather(s)
        self.assertIn("K3 more than once", str(refused.exception))
        self.assertEqual(s.prompts, [])


class AllRebuilds(Fixture):
    # Batch 1 is B's (`other-` sorts first), batch 2 A's, which is given the tool entry K8.
    def setUp(self):
        super().setUp()
        # Here, not on the class: `A` and `B` are set by `setUpModule`, after import.
        self.B_SRC = f"{B}/0001_a/spike.md ## U1"
        self.A_SRC = f"{A}/0001_a/spike.md ## U1"
        self.unit(A, "0001_a", spike_md="# Spike\n## U1\nA measured x\n")
        self.unit(B, "0001_a", spike_md="# Spike\n## U1\nB measured y\n")
        old = knowledge.render({"version": 3, "gathered": "2026-09-01T00:00:00Z", "max_id": 7},
                               knowledge.parse(entry(7, self.A_SRC))["entries"])
        knowledge.save(self.dir / knowledge.STORE, old)
        self.old = old

    def batch_one(self) -> str:
        return reply(entry(8, self.B_SRC))

    def batch_two(self) -> str:
        return reply(entry(8, self.B_SRC) + "\n\n" + entry(9, self.A_SRC, scope=f"workspace:{A}"))

    def refused(self) -> str:
        # K1 is at or below the old store's Max id K7.
        return reply(entry(1, self.B_SRC))

    def progress(self) -> dict | None:
        return gather.load_progress(self.dir / gather.PROGRESS)

    def half_done(self) -> Replies:
        """`0107` R4: batch 1 passes, batch 2 is refused, and so are both its repairs."""
        s = Replies(self.batch_one(), self.refused(), self.refused(), self.refused())
        self.assertEqual(self.run_gather(s, "all"), 1)
        return s

    def test_it_keeps_max_id_and_rebuilds_the_store_batch_by_batch(self):
        s = Replies(self.batch_one(), self.batch_two())
        self.assertEqual(self.run_gather(s, "all"), 0)
        parsed = knowledge.parse(self.store())
        self.assertEqual([e["id"] for e in parsed["entries"]], [8, 9])
        # One version per save, as `new` has always counted them: 3, then one per batch.
        self.assertEqual((parsed["header"]["version"], parsed["header"]["max_id"]), (5, 9))
        first, second = self.rows()
        self.assertEqual(first["dropped"], [{"id": "K7", "reason": gather.REBUILT}])
        self.assertEqual((first["mode"], first["entries_before"], second["entries_after"]), ("all", 1, 2))
        self.assertEqual(set(self.manifest()), {f"{A}/0001_a/spike.md", f"{B}/0001_a/spike.md"})

    def test_an_id_at_or_below_the_old_max_is_refused(self):
        s = Replies(self.refused(), self.refused(), self.refused())
        self.assertEqual(self.run_gather(s, "all"), 1)
        self.assertEqual(self.store(), self.old)
        self.assertEqual([(r["outcome"], r["attempt"]) for r in self.rows()],
                         [("failed", 1), ("failed", 2), ("failed", 3)])

    def test_each_batch_prompt_says_how_many_bytes_its_store_holds(self):
        s = Replies(self.batch_one(), self.batch_two())
        self.run_gather(s, "all")
        self.assertIn("The part of the store below is 0 bytes now", s.prompts[0])
        # What batch 1 wrote and batch 2 is given, measured as `validate` measures it.
        given = len(knowledge.entries_text(knowledge.parse(entry(8, self.B_SRC))["entries"]).encode("utf-8"))
        self.assertIn(f"The part of the store below is {given} bytes now", s.prompts[1])

    def test_a_failure_at_batch_two_keeps_what_batch_one_wrote(self):
        # `0107` R4 turns the old `..._leaves_the_store_as_it_was` round: what passed is kept.
        self.half_done()
        text = self.store()
        parsed = knowledge.parse(text)
        self.assertEqual(([e["id"] for e in parsed["entries"]], parsed["skipped"]), ([8], []))
        for slot in (A, B):
            self.assertLessEqual(knowledge.slice_for(text, slot)[1]["bytes"], knowledge.CAP_BYTES)
        found = {s["label"]: s["sha"] for s in gather.sources(str(self.data))}
        self.assertEqual(self.progress()["done"], {f"{B}/0001_a/spike.md": found[f"{B}/0001_a/spike.md"]})
        self.assertEqual(self.manifest(), {})
        self.assertEqual([r["outcome"] for r in self.rows()], ["done", "failed", "failed", "failed"])

    def test_a_failed_new_says_the_batches_before_it_are_kept(self):
        # `0107` review F2: "nothing written" was true of the batch, not of the run.
        def failed() -> str:
            return [line for line in self.said if " failed, " in line][-1]

        self.assertEqual(self.run_gather(Replies(self.refused(), self.refused(), self.refused()), "new"), 1)
        self.assertTrue(failed().startswith("batch 1/2 failed, nothing of it written: "), failed())
        self.assertEqual(self.manifest(), {})
        # `new` keeps the old store, so batch 1 has to hand K7 back beside its own K8.
        keep = reply(entry(7, self.A_SRC) + "\n\n" + entry(8, self.B_SRC))
        s = Replies(keep, self.refused(), self.refused(), self.refused())
        self.assertEqual(self.run_gather(s, "new"), 1)
        self.assertTrue(failed().startswith(
            "batch 2/2 failed, nothing of it written, the batches before it are kept: "), failed())
        self.assertEqual(list(self.manifest()), [f"{B}/0001_a/spike.md"])

    def test_a_second_all_resumes_at_the_batch_that_failed(self):
        self.half_done()
        s = Replies(self.batch_two())
        self.assertEqual(self.run_gather(s, "all"), 0)
        [prompt] = s.prompts
        self.assertNotIn(f"### {B}/0001_a/spike.md", prompt)
        self.assertIn(f"### {A}/0001_a/spike.md", prompt)
        self.assertIn("## K8", prompt)
        self.assertEqual([e["id"] for e in knowledge.parse(self.store())["entries"]], [8, 9])
        last = self.rows()[-1]
        self.assertEqual((last["mode"], last["resumed"], last["outcome"], last["dropped"]), ("all", True, "done", []))
        self.assertTrue(all(r["mode"] == "all" for r in self.rows()))

    def test_the_store_is_saved_before_the_progress_that_records_it(self):
        # The other way round, a batch could be marked passed that the store does not hold.
        saved: list[str] = []
        real = knowledge.save
        with mock.patch.object(knowledge, "save", lambda p, t: (saved.append(Path(p).name), real(p, t))):
            self.run_gather(Replies(self.batch_one(), self.batch_two()), "all")
        self.assertEqual(saved, [knowledge.STORE, gather.PROGRESS, knowledge.STORE, gather.PROGRESS, knowledge.SOURCES])

    def test_a_finished_resume_writes_the_manifest_and_removes_the_progress(self):
        self.half_done()
        self.run_gather(Replies(self.batch_two()), "all")
        self.assertIsNone(self.progress())
        self.assertEqual(self.manifest(), {s["label"]: s["sha"] for s in gather.sources(str(self.data))})
        self.assertIn("is version 5, 2 entries", self.said[-1])

    def test_a_resume_with_nothing_left_only_finishes(self):
        # The last batch passed, and the process died before the manifest or the unlink.
        every = {s["label"]: s["sha"] for s in gather.sources(str(self.data))}
        gather.save_progress(self.dir / gather.PROGRESS, {"started": "t", "done": every, "rebuilt_recorded": True})
        s = Replies()
        self.assertEqual(self.run_gather(s, "all"), 0)
        self.assertEqual((s.prompts, self.rows(), self.store()), ([], [], self.old))
        self.assertIsNone(self.progress())
        self.assertEqual(self.manifest(), every)

    def test_the_rebuilt_list_goes_with_the_first_save_and_never_again(self):
        # 1: batch 1 refused outright. Nothing is saved, so nothing is recorded as rebuilt.
        self.assertEqual(self.run_gather(Replies(self.refused(), self.refused(), self.refused()), "all"), 1)
        self.assertEqual([r["dropped"] for r in self.rows()], [[], [], []])
        self.assertIsNone(self.progress())
        self.assertEqual(self.store(), self.old)
        # 2: a new `--all` from the same old store; its first save names what it replaced.
        self.half_done()
        done = [r for r in self.rows() if r["outcome"] == "done"]
        self.assertEqual([(r["batch"], r["dropped"]) for r in done], [(1, [{"id": "K7", "reason": gather.REBUILT}])])
        # 3: the resume names it again nowhere.
        self.assertEqual(self.run_gather(Replies(self.batch_two()), "all"), 0)
        rebuilt = [r for r in self.rows() if any(d.get("reason") == gather.REBUILT for d in r["dropped"])]
        self.assertEqual(len(rebuilt), 1)

    def test_a_refused_reply_is_repaired_and_the_batch_passes(self):
        over = reply(sized(8, 8858, self.B_SRC))
        s = Replies(over, self.batch_one(), self.batch_two())
        self.assertEqual(self.run_gather(s, "all"), 0)
        self.assertEqual([e["id"] for e in knowledge.parse(self.store())["entries"]], [8, 9])
        rows = self.rows()
        self.assertEqual([(r["batch"], r["attempt"], r["outcome"]) for r in rows],
                         [(1, 1, "failed"), (1, 2, "done"), (2, 1, "done")])
        self.assertEqual(rows[0]["reason"], f"workspace:{B} would receive 8858 bytes, over 8192")
        self.assertTrue(all(r["mode"] == "all" and r["resumed"] is False for r in rows))
        self.assertIn("batch 1/2: refused, repair 1/2: ", "\n".join(self.said))

    def test_a_batch_that_was_repaired_says_what_all_its_sessions_cost(self):
        s = Replies(reply(sized(8, 8858, self.B_SRC)), self.batch_one(), self.batch_two())
        self.assertEqual(self.run_gather(s, "all"), 0)
        done = [line for line in self.said if " done: " in line]
        self.assertEqual(done, [
            "batch 1/2 done: 1 entries, 2 session(s), cost $0.50; $0.50 counted against the $4.00 printed",
            "batch 2/2 done: 2 entries, 1 session(s), cost $0.25; $0.75 counted against the $4.00 printed",
        ])

    def test_a_batch_line_names_the_sessions_that_reported_no_cost(self):
        s = Replies(self.batch_one(), self.batch_two(), cost=None)
        self.assertEqual(self.run_gather(s, "all"), 0)
        self.assertIn("batch 1/2 done: 1 entries, 1 session(s), cost $0.00, and 1 that reported no cost; "
                      "$2.00 counted against the $4.00 printed", self.said)

    def test_the_repair_prompt_carries_the_reasons_and_the_bytes_and_no_source(self):
        s = Replies(reply(sized(8, 8858, self.B_SRC)), self.batch_one(), self.batch_two())
        self.run_gather(s, "all")
        first, repair = s.prompts[0], s.prompts[1]
        self.assertIn("B measured y", first)
        self.assertIn("would receive 8858 bytes, over 8192", repair)
        self.assertIn("entries total 8858 bytes", repair)
        self.assertIn("7372", repair)
        self.assertIn("## K8", repair)
        self.assertNotIn("B measured y", repair)
        self.assertNotIn(f"### {B}/0001_a/spike.md", repair)

    def test_no_session_opens_past_the_ceiling(self):
        with self.subTest(batches=2):
            # $4.00: batch 1 at $1.50, a repair at $3.00, and a second repair could reach $5.00.
            s = Replies(self.refused(), self.refused(), cost=1.5)
            self.assertEqual(self.run_gather(s, "all"), 1)
            self.assertEqual(len(s.prompts), 2)
            self.assertIn("$3.00 spent", self.said[-1])
            self.assertIn("past the $4.00 printed", self.said[-1])
        shutil.rmtree(self.data / "units" / B)
        with self.subTest(batches=1):
            s = Replies(reply(entry(1, self.A_SRC)), cost=1.5)
            self.assertEqual(self.run_gather(s, "all"), 1)
            self.assertEqual(len(s.prompts), 1)
            self.assertIn("past the $2.00 printed", self.said[-1])

    def test_a_session_with_no_cost_counts_as_the_grant_ceiling(self):
        s = Replies(self.refused(), self.refused(), self.refused(), cost=None)
        self.assertEqual(self.run_gather(s, "all"), 1)
        # $2.00 + $2.00 reaches the $4.00 printed; a third session could pass it.
        self.assertEqual(len(s.prompts), 2)
        self.assertNotIn("cost_usd", self.rows()[0])
        self.assertIn("$4.00 spent", self.said[-1])

    # `0107` plan step 1: what `plan_of` makes of a progress record written by hand.

    def write(self, text: str) -> None:
        knowledge.save(self.dir / gather.PROGRESS, text)

    def test_all_leaves_out_what_passed_and_sends_again_what_changed(self):
        found = {s["label"]: s["sha"] for s in gather.sources(str(self.data))}
        a, b = f"{A}/0001_a/spike.md", f"{B}/0001_a/spike.md"
        gather.save_progress(self.dir / gather.PROGRESS,
                             {"started": "t", "done": {b: found[b], a: "an older sha"}, "rebuilt_recorded": True})
        planned = gather.plan_of(str(self.data), "all")
        self.assertEqual([s["label"] for s in planned["sources"]], [a])
        self.assertEqual((planned["resumed"], planned["passed"], planned["ceiling_usd"]), (True, 2, 2.0))

    def test_new_is_refused_and_names_the_record(self):
        gather.save_progress(self.dir / gather.PROGRESS, {"started": "t", "done": {}, "rebuilt_recorded": True})
        s = Replies(self.batch_one())
        with self.assertRaises(gather.Refused) as refused:
            self.run_gather(s, "new")
        self.assertIn(str(self.dir / gather.PROGRESS), str(refused.exception))
        self.assertEqual(s.prompts, [])

    def test_a_record_that_does_not_read_is_an_error_not_an_empty_one(self):
        for text in ("{not json", "[]", '{"started": "t", "done": {"x": 1}, "rebuilt_recorded": true}',
                     '{"started": "t", "done": {}}'):
            with self.subTest(text=text):
                self.write(text)
                with self.assertRaises(ValueError) as failed:
                    gather.plan_of(str(self.data), "all")
                self.assertIn(str(self.dir / gather.PROGRESS), str(failed.exception))


if __name__ == "__main__":
    unittest.main()
