"""`0108` plan steps 2 and 3: what the code decides about a gathered entry, on a real git
repository built here with fixed dates. Nothing here opens a session."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import admit, knowledge, units
from coscc.journal import Journal


def lock(**versions: str) -> str:
    """A `uv.lock` holding one `[[package]]` per name."""
    return "version = 1\n" + "".join(
        f'\n[[package]]\nname = "{name}"\nversion = "{v}"\n' for name, v in versions.items())


def make_repo(path: Path, *commits: tuple[str, dict[str, str]]) -> Path:
    """A repository at `path` whose `main` holds one commit per `(ISO date, {file: text})`,
    committed at that date, each on top of the last."""
    path.mkdir(parents=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(path), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}

    def git(*args: str, **extra: str) -> None:
        subprocess.run(["git", "-C", str(path), *args], env={**env, **extra}, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    for when, files in commits:
        for name, text in files.items():
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(text, encoding="utf-8")
        git("add", "-A")
        git("-c", "commit.gpgsign=false", "commit", "-q", "-m", when,
            GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    return path


OLD = "2026-09-01T00:00:00+00:00"
NEW = "2026-09-20T00:00:00+00:00"
CODE = "def batches(found):\n    return found\n"


def entry(n: int, scope: str, *sources: str, refs: tuple[str, ...] = (), statement: str = "A fact.",
          measured: str = "") -> dict:
    return {"id": n, "scope": scope, "sources": list(sources), "refs": list(refs), "measured": measured,
            "statement": statement, "text": ""}


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = make_repo(
            self.root / "proj",
            (OLD, {".python-version": "3.14\n", "uv.lock": lock(reflex="0.9.11"), "coscc/gather.py": CODE}),
            (NEW, {"uv.lock": lock(reflex="0.9.12", **{"claude-agent-sdk": "0.2.159"})}),
        )
        self.slot = units.slot(self.repo)
        self.journal = Journal(self.root / "work", self.root / "data")

    def source(self, unit: str, file: str = "spike.md", text: str = "## U1\nmeasured\n") -> dict:
        return {"label": f"{self.slot}/{unit}/{file}", "slot": self.slot, "unit": unit, "file": file, "text": text}

    def ctx(self, *found: dict) -> dict:
        rows = self.journal.records(kind="end")
        return {"dates": admit.date_sources(rows, found), "texts": {s["label"]: s["text"] for s in found},
                "workspaces": admit.workspaces(rows), "git": admit.Reader()}


class DatesAndPins(Fixture):
    def test_an_end_row_by_path_or_by_slot_dates_its_source(self):
        a, b = self.source("0001_a"), self.source("0002_b", "review.md")
        self.journal.finished(str(self.repo), "0001_a", "spike", "done", at="2026-09-24T05:07:03+00:00")
        self.journal.finished(str(self.repo), "0001_a", "spike", "done", at="2026-09-23T01:00:00+07:00")
        self.journal.finished(self.slot, "0002_b", "review", "done", at="2026-09-25T23:30:00-02:00")
        dates = admit.date_sources(self.journal.records(kind="end"), [a, b])
        self.assertEqual(dates[a["label"]], {"date": "2026-09-24", "at": "2026-09-24T05:07:03+00:00",
                                             "slot": self.slot, "path": str(self.repo)})
        # The UTC day, not the local one.
        self.assertEqual(dates[b["label"]]["date"], "2026-09-26")

    def test_a_failed_row_or_another_stage_dates_nothing(self):
        a, b = self.source("0001_a"), self.source("0002_b", "review.md")
        self.journal.finished(str(self.repo), "0001_a", "spike", "failed", at=NEW)
        self.journal.finished(str(self.repo), "0002_b", "spike", "done", at=NEW)
        self.assertEqual(admit.date_sources(self.journal.records(kind="end"), [a, b]), {})

    def test_a_slot_is_a_path_only_when_a_row_gives_one(self):
        self.journal.finished(str(self.repo), "0001_a", "spike", "done")
        self.journal.finished("other-0123456789ab", "0001_a", "spike", "done")
        self.journal.finished(str(self.root / "gone"), "0001_a", "spike", "done")
        self.assertEqual(admit.workspaces(self.journal.records(kind="end")), {self.slot: str(self.repo)})

    def test_the_pin_is_the_one_main_had_when_the_source_ended(self):
        git = admit.Reader()
        between = git.commit_before(str(self.repo), "2026-09-10T00:00:00+00:00")
        self.assertEqual(git.pins(str(self.repo), between), {"python": "3.14", "reflex": "0.9.11"})
        self.assertIsNone(git.commit_before(str(self.repo), "2026-08-01T00:00:00+00:00"))
        self.assertEqual(git.pins(str(self.repo), "main"),
                         {"python": "3.14", "reflex": "0.9.12", "claude-agent-sdk": "0.2.159"})

    def test_a_name_locked_at_two_versions_has_no_pin(self):
        repo = make_repo(self.root / "two", (OLD, {"uv.lock": lock(reflex="1") + lock(reflex="2")[len("version = 1\n"):]}))
        self.assertEqual(admit.pins(str(repo), "main"), {})

    def test_a_ref_needs_its_path_on_main_and_its_symbol_in_it(self):
        git = admit.Reader()
        self.assertEqual(git.ref_exists(str(self.repo), "coscc/gather.py"), (True, ""))
        self.assertEqual(git.ref_exists(str(self.repo), "coscc/gather.py::batches"), (True, ""))
        self.assertFalse(git.ref_exists(str(self.repo), "coscc/gather.py::batch")[0])
        self.assertFalse(git.ref_exists(str(self.repo), "coscc/admit.py")[0])

    def test_no_git_on_path_is_an_error_not_an_answer(self):
        with mock.patch.dict("os.environ", {"PATH": str(self.root)}):
            self.assertFalse(admit.git_runs())
            with self.assertRaises(admit.GitError):
                admit.Reader().commit_before(str(self.repo), NEW)
        self.assertTrue(admit.git_runs())


class Admit(Fixture):
    def dated(self, *found: dict, at: str = "2026-09-25T00:00:00+00:00") -> dict:
        for s in found:
            self.journal.finished(str(self.repo), s["unit"], admit.STAGE_OF[s["file"]], "done", at=at)
        return self.ctx(*found)

    def label(self, s: dict, anchor: str = "## U1") -> str:
        return f"{s['label']} {anchor}"

    def test_the_date_and_the_version_are_the_codes(self):
        s = self.source("0001_a")
        ctx = self.dated(s)
        e = entry(1, "tool:Claude_Agent_SDK 9.9.9", self.label(s), measured="2020-01-01")
        [kept], dropped = admit.admit([e], [], ctx)
        self.assertEqual(dropped, [])
        self.assertEqual((kept["scope"], kept["measured"]), ("tool:claude-agent-sdk 0.2.159", "2026-09-25"))
        self.assertEqual(kept["text"], knowledge.format_entry(kept))
        self.assertIn("Measured: 2026-09-25", kept["text"])

    def test_the_newest_source_decides_the_commit(self):
        s = self.source("0001_a")
        ctx = self.dated(s, at="2026-09-10T00:00:00+00:00")
        [kept], _ = admit.admit([entry(1, "tool:reflex", self.label(s))], [], ctx)
        self.assertEqual(kept["scope"], "tool:reflex 0.9.11")

    def test_an_undated_entry_is_dropped(self):
        s = self.source("0001_a")
        kept, dropped = admit.admit([entry(1, "tool:python", self.label(s))], [], self.ctx(s))
        self.assertEqual((kept, dropped), ([], [{"id": "K1", "reason": "undated"}]))

    def test_a_tool_with_no_pin_is_dropped(self):
        # K5 and K16 of batch 2 (`spike.md ## U3`).
        s = self.source("0001_a")
        kept, dropped = admit.admit([entry(5, "tool:gh", self.label(s))], [], self.dated(s))
        self.assertEqual((kept, dropped), ([], [{"id": "K5", "reason": "unpinned"}]))

    def test_a_tool_whose_sources_have_no_path_is_dropped(self):
        s = self.source("0001_a")
        ctx = self.dated(s)
        ctx["workspaces"] = {}
        for d in ctx["dates"].values():
            d["path"] = ""
        self.assertEqual(admit.admit([entry(1, "tool:python", self.label(s))], [], ctx)[1],
                         [{"id": "K1", "reason": "unpinned"}])

    def test_a_statement_that_says_it_was_derived_is_dropped(self):
        # K2 of batch 2.
        s = self.source("0001_a")
        e = entry(2, "tool:python", self.label(s), statement="Python 3.14 does X (derived from semantics, not run).")
        self.assertEqual(admit.admit([e], [], self.dated(s))[1], [{"id": "K2", "reason": "not-measured"}])

    def test_a_source_that_says_it_did_not_check_the_subject_does_not_count(self):
        # K18 of batch 2: its one source says it did not run Reflex 0.9.12.
        s = self.source("0001_a", text="## U1\nKhông kiểm lúc chạy thật việc Reflex 0.9.12 đổi cách nạp trang.\n")
        other = self.source("0002_b", text="## U1\nKhông kiểm việc reflexive thứ khác.\n")
        ctx = self.dated(s, other)
        self.assertEqual(admit.admit([entry(18, "tool:reflex", self.label(s))], [], ctx)[1],
                         [{"id": "K18", "reason": "not-measured"}])
        # `reflexive` is not `reflex`: that source still counts.
        [kept], _ = admit.admit([entry(18, "tool:reflex", self.label(other))], [], ctx)
        self.assertEqual(kept["id"], 18)

    def test_a_workspace_entry_needs_every_ref_on_main(self):
        s = self.source("0001_a")
        ctx = self.dated(s)
        scope = f"workspace:{self.slot}"
        good = entry(1, scope, self.label(s), refs=("coscc/gather.py::batches",))
        bad = entry(2, scope, self.label(s), refs=("coscc/gather.py", "coscc/gone.py"))
        bare = entry(3, scope, self.label(s))
        tools = [entry(n, "tool:python", self.label(s)) for n in (4, 5, 6)]
        kept, dropped = admit.admit([good, bad, bare, *tools], [], ctx)
        self.assertEqual([e["id"] for e in kept], [1, 4, 5, 6])
        self.assertEqual(dropped, [{"id": "K2", "reason": "ref-missing"}, {"id": "K3", "reason": "ref-missing"}])
        self.assertIn("Ref: coscc/gather.py::batches", kept[0]["text"])

    def test_an_old_entry_of_another_workspace_is_checked_too(self):
        s = self.source("0001_a")
        ctx = self.dated(s)
        old = knowledge.parse(
            f"## K1\nScope: workspace:{self.slot}\nSource: {self.label(s)}\nRef: coscc/gone.py\nMeasured: 2026-09-01\nOld.\n\n"
            f"## K2\nScope: workspace:other-0123456789ab\nSource: {self.label(s)}\nRef: a.py\nMeasured: 2026-09-01\nOld.")["entries"]
        kept, dropped = admit.admit([entry(3, "tool:python", self.label(s))], old, ctx)
        self.assertEqual([e["id"] for e in kept], [3])
        self.assertEqual(dropped, [{"id": "K1", "reason": "ref-missing"}, {"id": "K2", "reason": "no-workspace"}])

    def test_an_entry_over_its_ceiling_once_written_is_dropped(self):
        s = self.source("0001_a")
        e = entry(1, "tool:python", self.label(s), statement="w" * (knowledge.ENTRY_BYTES - 80))
        self.assertEqual(admit.admit([e], [], self.dated(s))[1], [{"id": "K1", "reason": "too-big"}])

    def test_the_share_of_tool_entries_drops_the_oldest_workspace_entries_first(self):
        s = self.source("0001_a")
        ctx = self.dated(s)
        scope, ref = f"workspace:{self.slot}", ("coscc/gather.py",)
        old = knowledge.parse("\n\n".join(
            f"## K{n}\nScope: {scope}\nSource: {self.label(s)}\nRef: coscc/gather.py\nMeasured: {day}\nOld {n}."
            for n, day in ((1, "2026-09-20"), (2, "2026-09-10"), (3, "2026-09-10"))))["entries"]
        new = [entry(4, scope, self.label(s), refs=ref), entry(5, "tool:python", self.label(s)),
               entry(6, "tool:reflex", self.label(s))]
        kept, dropped = admit.admit(new, old, ctx)
        # Four `workspace:` against two `tool:`: K2 and K3 (oldest, lower id first) go.
        self.assertEqual([e["id"] for e in kept], [1, 4, 5, 6])
        self.assertEqual(dropped, [{"id": "K2", "reason": "ratio"}, {"id": "K3", "reason": "ratio"}])

    def test_a_store_with_no_tool_entry_keeps_no_workspace_entry(self):
        s = self.source("0001_a")
        e = entry(1, f"workspace:{self.slot}", self.label(s), refs=("coscc/gather.py",))
        self.assertEqual(admit.admit([e], [], self.dated(s)), ([], [{"id": "K1", "reason": "ratio"}]))

    def test_a_git_error_drops_the_entry_it_met_and_says_why(self):
        s = self.source("0001_a")
        ctx = self.dated(s)
        with mock.patch.object(admit, "_git", side_effect=admit.GitError("no git")):
            kept, dropped = admit.admit([entry(1, "tool:python", self.label(s))], [], ctx)
        self.assertEqual((kept, dropped), ([], [{"id": "K1", "reason": "unpinned", "detail": "no git"}]))


if __name__ == "__main__":
    unittest.main()
