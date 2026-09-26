"""`coscc/retake.py` (`0111`): the command stood in for by a short script in a temporary git
repository, so nothing is built and no browser opens."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from coscc import config, retake


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo(tmp: str) -> tuple[Path, str]:
    repo = Path(tmp) / "tree"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text(".screens/\n")
    (repo / "tracked.txt").write_text("one\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "first")
    return repo, _git(repo, "rev-parse", "HEAD")


def _writes(head: str, dirty: bool = False, code: int = 0, then: str = "") -> list[str]:
    """A stand-in for `capture_screens.py` that writes a manifest naming `head` and exits `code`."""
    body = (
        "import json, os, sys\n"
        "os.makedirs('.screens', exist_ok=True)\n"
        f"json.dump({{'head': {head!r}, 'dirty': {dirty!r}, 'addresses': ['/board'], 'hits': []}}, open('.screens/manifest.json', 'w'))\n"
        "print('a line of output')\n"
        f"{then}\n"
        f"sys.exit({code})\n"
    )
    return [sys.executable, "-c", body]


def _take(repo: Path, argv: list[str], timeout: float = retake.RETAKE_TIMEOUT) -> dict:
    return asyncio.run(retake.take(repo, ["/board"], data_dir=str(repo.parent / "data"), timeout=timeout, argv=argv))


class ARetakeIsJudgedOnWhatItLeft(unittest.TestCase):
    def test_exit_0_with_a_manifest_of_head_is_taken(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = _repo(tmp)
            result = _take(repo, _writes(head))
        self.assertEqual((result["code"], result["head_before_run"]), (0, head))
        self.assertEqual(result["manifest_after"]["head"], head)
        self.assertIn("a line of output", result["tail"])
        self.assertEqual(retake.judge(result), (True, ""))

    def test_exit_0_with_a_manifest_of_another_head_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = _repo(tmp)
            ok, detail = retake.judge(_take(repo, _writes("f" * 40)))
        self.assertFalse(ok)
        self.assertIn(f"not HEAD {head[:12]}", detail)

    def test_an_exit_other_than_0_fails_even_over_a_manifest_of_head(self):
        # `spike.md ## U1`, result 5: after a failed run the manifest on disk is the last one's.
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = _repo(tmp)
            (repo / ".screens").mkdir()
            (repo / ".screens" / "manifest.json").write_text(json.dumps({"head": head, "dirty": False}))
            result = _take(repo, [sys.executable, "-c", "print('port in use'); raise SystemExit(2)"])
        ok, detail = retake.judge(result)
        self.assertEqual(result["manifest_after"]["head"], head)
        self.assertFalse(ok)
        self.assertIn("exited 2", detail)
        self.assertIn("port in use", detail)

    def test_a_dirty_manifest_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = _repo(tmp)
            ok, detail = retake.judge(_take(repo, _writes(head, dirty=True)))
        self.assertFalse(ok)
        self.assertIn("tree changed while it ran", detail)

    def test_a_changed_git_status_fails_and_names_the_file(self):
        # `spike.md ## U1`, result 1: a failed build rewrote `reflex.lock/package.json`.
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = _repo(tmp)
            ok, detail = retake.judge(_take(repo, _writes(head, then="open('tracked.txt', 'w').write('two')")))
        self.assertFalse(ok)
        self.assertIn(" M tracked.txt", detail)

    def test_no_manifest_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = _repo(tmp)
            ok, detail = retake.judge(_take(repo, [sys.executable, "-c", "pass"]))
        self.assertFalse(ok)
        self.assertIn("no .screens/manifest.json", detail)


class ARetakePastItsTimeIsKilledWithEverythingItStarted(unittest.TestCase):
    def test_the_group_is_killed_and_the_code_is_124(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = _repo(tmp)
            pidfile = Path(tmp) / "child.pid"
            # A child that outlives its parent unless the whole group is killed.
            body = (
                "import subprocess, sys, time\n"
                f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
                "time.sleep(60)\n"
            )
            started = time.monotonic()
            result = _take(repo, [sys.executable, "-c", body], timeout=2.0)
            took = time.monotonic() - started
            child = int(pidfile.read_text())
        self.assertEqual(result["code"], 124)
        self.assertLess(took, 30)
        self.assertIn("did not finish in 2s", result["tail"])
        self.assertFalse(retake.judge(result)[0])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(child):
            time.sleep(0.1)
        self.assertFalse(_alive(child), "the capture's own child outlived it")

    def test_a_cancel_kills_the_group_and_is_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = _repo(tmp)
            pidfile = Path(tmp) / "leader.pid"
            body = f"import os, time\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n"

            async def go():
                task = asyncio.create_task(retake.take(repo, [], data_dir=tmp, argv=[sys.executable, "-c", body]))
                while not pidfile.exists() or not pidfile.read_text():
                    await asyncio.sleep(0.05)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            asyncio.run(go())
            self.assertFalse(_alive(int(pidfile.read_text())))


_CLEARS = (
    # What `clear_out` does before any browser opens (`scripts/capture_screens.py:392-396`),
    # and a first image of the new run.
    "import glob, os\n"
    "os.makedirs('.screens', exist_ok=True)\n"
    "[os.remove(f) for f in glob.glob('.screens/*.png') + glob.glob('.screens/manifest.json')]\n"
    "open('.screens/board-1440x900.png', 'w').write('new')\n"
)


class ARetakeThatFailsLeavesTheScreensItFound(unittest.TestCase):
    """Review round 1, F1: a run that fails after `clear_out` must not take `impl`'s evidence."""

    OLD = {"head": "a" * 40, "dirty": False, "addresses": ["/board"], "hits": []}

    def _old(self, repo: Path) -> dict[str, bytes]:
        (repo / ".screens").mkdir()
        (repo / ".screens" / "manifest.json").write_text(json.dumps(self.OLD))
        (repo / ".screens" / "board-1440x900.png").write_text("old")
        (repo / ".screens" / "board-390x844.png").write_text("old too")
        return self._screens(repo)

    @staticmethod
    def _screens(repo: Path) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in (repo / ".screens").iterdir()}

    def test_an_exit_past_clear_out_puts_them_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = _repo(tmp)
            before = self._old(repo)
            result = _take(repo, [sys.executable, "-c", _CLEARS + "raise SystemExit(1)"])
            self.assertFalse(retake.judge(result)[0])
            self.assertIsNone(result["manifest_after"])
            self.assertEqual(self._screens(repo), before)

    def test_a_timeout_past_clear_out_puts_them_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = _repo(tmp)
            before = self._old(repo)
            result = _take(repo, [sys.executable, "-c", _CLEARS + "import time; time.sleep(60)"], timeout=2.0)
            self.assertEqual(result["code"], 124)
            self.assertEqual(self._screens(repo), before)

    def test_a_cancel_past_clear_out_puts_them_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = _repo(tmp)
            before = self._old(repo)
            marker = Path(tmp) / "cleared"
            body = _CLEARS + f"open({str(marker)!r}, 'w').write('1')\nimport time; time.sleep(60)\n"

            async def go():
                task = asyncio.create_task(retake.take(repo, [], data_dir=tmp, argv=[sys.executable, "-c", body]))
                while not marker.exists():
                    await asyncio.sleep(0.05)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            asyncio.run(go())
            self.assertEqual(self._screens(repo), before)

    def test_a_retake_that_is_taken_keeps_what_it_wrote(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = _repo(tmp)
            self._old(repo)
            result = _take(repo, [sys.executable, "-c", _CLEARS + (
                "import json\n"
                f"json.dump({{'head': {head!r}, 'dirty': False, 'addresses': ['/board'], 'hits': []}}, open('.screens/manifest.json', 'w'))\n"
            )])
            self.assertTrue(retake.judge(result)[0])
            now = self._screens(repo)
        self.assertEqual(sorted(now), ["board-1440x900.png", "manifest.json"])
        self.assertEqual(now["board-1440x900.png"], b"new")
        self.assertEqual(json.loads(now["manifest.json"])["head"], head)

    def test_nothing_is_left_in_the_temporary_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = _repo(tmp)
            self._old(repo)
            made: list[str] = []
            real = tempfile.mkdtemp

            def mkdtemp(*a, **kw):
                made.append(real(*a, **kw))
                return made[-1]

            with mock.patch.object(retake.tempfile, "mkdtemp", mkdtemp):
                _take(repo, [sys.executable, "-c", _CLEARS + "raise SystemExit(1)"])
            self.assertEqual(len(made), 1)
            self.assertFalse(Path(made[0]).exists())


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return False
    return "\nState:\tZ" not in state


class TheEnvironmentIsTheAppsWithReflexBlanked(unittest.TestCase):
    def test_reflex_names_are_blank_and_the_apps_database_protected(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"__REFLEX_SKIP_COMPILE": "1", "__REFLEX_MOUNT_FRONTEND_COMPILED_APP": "1", "COS_PORT": "8790"},
        ):
            e = retake.env(tmp)
        self.assertEqual(e["__REFLEX_SKIP_COMPILE"], "")
        self.assertEqual(e["__REFLEX_MOUNT_FRONTEND_COMPILED_APP"], "")
        # Nothing else is narrowed: that is the environment the spike measured.
        self.assertEqual(e["COS_PORT"], "8790")
        self.assertEqual(e["PATH"], os.environ["PATH"])
        self.assertIn(str((Path(tmp) / "cos.db").resolve()), e[config.PROTECTED_DB_VAR])


class TheRecordAndTheSection(unittest.TestCase):
    OLD = {"head": "a" * 40, "addresses": ["/board"], "hits": [{"address": "/board", "size": "390x844", "kind": "path", "snippet": "/tmp/one"}]}
    NEW = {"head": "b" * 40, "dirty": False, "addresses": ["/board"], "hits": []}

    def test_a_taken_record_carries_both_heads(self):
        result = {"code": 0, "seconds": 20.1, "manifest_after": self.NEW}
        r = retake.record("/w", "0001_x", self.OLD, result, True, "", "autopilot")
        self.assertEqual(r, {
            "kind": "screens", "workspace": "/w", "unit": "0001_x", "stage": "review",
            "head_before": "a" * 40, "head_after": "b" * 40, "addresses": ["/board"],
            "code": 0, "seconds": 20.1, "outcome": "taken", "detail": "", "started_by": "autopilot",
        })

    def test_a_failed_record_has_no_head_after(self):
        result = {"code": 2, "seconds": 0.4, "manifest_after": self.OLD}
        r = retake.record("/w", "0001_x", self.OLD, result, False, "capture_screens.py exited 2", "person")
        self.assertEqual((r["outcome"], r["head_after"], r["detail"]), ("failed", "", "capture_screens.py exited 2"))

    def test_the_section_names_both_heads_who_took_them_and_every_hit(self):
        text = retake.describe_for_review(self.OLD, self.NEW)
        self.assertTrue(text.startswith("# The screenshots, taken again\n"))
        self.assertIn("a" * 40, text)
        self.assertIn("b" * 40, text)
        self.assertIn("the app — not `impl`, and not a person", text)
        self.assertIn("- `/board` — 390x844 — path — /tmp/one", text)
        self.assertIn("Now, at `bbbbbbb`:\n\n- none", text)


if __name__ == "__main__":
    unittest.main()
