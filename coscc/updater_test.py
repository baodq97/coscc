"""`coscc/updater.py`: waiting, cutting, the apply sequence, and what a restart reports.

`.cos/0068_updating-the-app-is-a-manual-reinstall` plan step 7. The service is a stand-in
that lists jobs from a Python list and records what was cut; nothing goes to the network,
no session is opened, and the trial run is replaced where a test says so.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from coscc import update, updater
from coscc.config import Config

SHA = "0123456789abcdef" * 2 + "01234567"


class _Journal:
    def __init__(self, rows):
        self.rows = rows

    def append(self, record):
        self.rows.append(record)
        return record


class _Sessions:
    def __init__(self):
        self.closed = 0

    async def close_all(self):
        self.closed += 1


class StandIn:
    """What `Updater` asks of `Service`, and nothing else."""

    def __init__(self):
        self.jobs: list[dict] = []
        self.cut: list[tuple[dict, str]] = []
        self.rows: list[dict] = []
        self.store = None
        self.sessions = _Sessions()
        self.shut = 0

    def _journal(self):
        return _Journal(self.rows)

    def _update_jobs(self):
        return list(self.jobs)

    async def _update_cut(self, job, by):
        self.cut.append((job, by))
        self.jobs = [j for j in self.jobs if j["id"] != job["id"]]
        return True

    async def shutdown(self):
        self.shut += 1

    def events(self):
        return [r["event"] for r in self.rows]


def _wheel(directory: Path, version: str, body: bytes = b"wheel", sums: bool = True) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / update.wheel_name(version)
    wheel.write_bytes(body)
    if sums:
        (directory / update.SUMS).write_text(f"{hashlib.sha256(body).hexdigest()}  {wheel.name}\n")
    return wheel


def _offline(url):
    raise OSError("offline")


def _serving(files):
    def opener(url):
        if url not in files:
            raise OSError("offline")
        return io.BytesIO(files[url])

    return opener


def _running_release(body: bytes = b"old wheel") -> dict[str, bytes]:
    """The running `0.12.0`'s own release files, as `_ensure_current` asks for them."""
    wheel_url, sums_url = update.release_urls("v0.12.0")
    return {wheel_url: body, sums_url: f"{hashlib.sha256(body).hexdigest()}  {update.wheel_name('0.12.0')}\n".encode()}


STEP = {"kind": "step", "id": "step:/w:0001_a", "workspace": "/w", "unit": "0001_a", "stage": "impl", "started": "t"}
CHAT = {"kind": "chat", "id": "chat:t1", "turn": "t1", "session_id": "s1", "workspace": "/w", "started": "t"}
INTEGRATION = {"kind": "integration", "id": "integration:/w:0002_b", "workspace": "/w", "unit": "0002_b",
               "stage": "integrate", "started": "t"}


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.config = Config(data_dir=str(self.data), update_check=False)
        self.service = StandIn()
        self.me = {
            "version": "0.12.0", "commit": SHA, "commit_label": SHA, "install": "package",
            "shape": "service", "reason": "", "build_id": f"0.12.0+{SHA}", "uv": "/nonexistent/uv",
            "tool_dir": str(Path(self.tmp.name) / "tools"), "bin_dir": str(Path(self.tmp.name) / "bin"),
        }
        self.root = self.data / "updates"
        self.applied: list[tuple[str, str]] = []

    def tearDown(self):
        update.SERVER.server = None
        update.SERVER.handoff = None
        self.tmp.cleanup()

    def make(self, record_apply=True):
        u = updater.Updater(self.config, self.service, me=dict(self.me), start=True)
        if record_apply:
            async def fake_apply(channel, by):
                self.applied.append((channel, by))
            u._apply = fake_apply  # type: ignore[method-assign]
        u.release = {"state": "ready", "version": "0.13.0"}
        return u

    async def settle(self):
        for _ in range(5):
            await asyncio.sleep(0)


class NothingRunningAppliesAtOnce(_Base):
    async def test_r7(self):
        u = self.make()
        await u.apply("release", "wait", "an")
        await self.settle()
        self.assertEqual(self.applied, [("release", "an")])

    async def test_a_name_is_required(self):
        with self.assertRaises(updater.Refused):
            await self.make().apply("release", "wait", "  ")

    async def test_a_channel_with_nothing_ready_refuses(self):
        u = self.make()
        with self.assertRaises(updater.Refused):
            await u.apply("local", "wait", "an")


class ItWaits(_Base):
    async def test_r9_pending_then_applies_once_the_last_job_ends(self):
        self.service.jobs = [STEP]
        u = self.make()
        status = await u.apply("release", "wait", "an")
        self.assertEqual(status["state"], "pending")
        self.assertEqual([j["id"] for j in status["pending"]["waiting"]], [STEP["id"]])
        self.assertEqual(status["warning"], updater.WAITING_WARNING)
        self.assertIn("pending", self.service.events())
        # A new job is not refused while waiting.
        u.refuse_while_updating()
        self.service.jobs = []
        u.job_ended()
        await self.settle()
        self.assertEqual(self.applied, [("release", "an")])

    async def test_a_job_whose_end_was_not_told_still_clears_on_the_next_status(self):
        self.service.jobs = [STEP]
        u = self.make()
        await u.apply("release", "wait", "an")
        self.service.jobs = []
        u.status()
        await self.settle()
        self.assertEqual(self.applied, [("release", "an")])

    async def test_cancel_is_recorded_and_nothing_applies(self):
        self.service.jobs = [STEP]
        u = self.make()
        await u.apply("release", "wait", "an")
        u.cancel("bo")
        self.assertEqual(u.state, "idle")
        self.assertEqual(self.service.rows[-1]["event"], "cancelled")
        self.assertEqual(self.service.rows[-1]["by"], "bo")
        self.service.jobs = []
        u.job_ended()
        await self.settle()
        self.assertEqual(self.applied, [])


class ApplyNowCutsWhatItListed(_Base):
    async def test_the_token_changes_with_the_list_and_an_old_one_is_refused(self):
        self.service.jobs = [STEP]
        u = self.make()
        first = u.cut_list()
        self.service.jobs = [STEP, CHAT]
        second = u.cut_list()
        self.assertNotEqual(first["token"], second["token"])
        with self.assertRaises(updater.Stale) as caught:
            await u.apply("release", "now", "an", first["token"])
        self.assertEqual(caught.exception.listing["token"], second["token"])
        self.assertEqual(self.service.cut, [])

    async def test_another_stage_on_the_same_unit_is_another_list(self):
        # Review round 1, F2: the step id names only the unit, so the stage and start count.
        self.service.jobs = [STEP]
        u = self.make()
        shown = u.cut_list()
        self.service.jobs = [{**STEP, "stage": "review", "started": "t2"}]
        with self.assertRaises(updater.Stale):
            await u.apply("release", "now", "an", shown["token"])
        self.assertEqual(self.service.cut, [])
        self.service.jobs = [{**STEP, "started": "t2"}]
        self.assertNotEqual(u.cut_list()["token"], shown["token"])

    async def test_steps_and_chats_are_cut_and_integrations_waited_for(self):
        self.service.jobs = [STEP, CHAT, INTEGRATION]
        u = self.make()
        listing = u.cut_list()
        actions = {i["kind"]: i["action"] for i in listing["items"]}
        self.assertEqual(actions, {"step": "sẽ bị dừng", "chat": "sẽ bị dừng", "integration": "sẽ chờ"})
        status = await u.apply("release", "now", "an", listing["token"])
        self.assertEqual([(j["kind"], by) for j, by in self.service.cut], [("step", "an"), ("chat", "an")])
        cuts = [r for r in self.service.rows if r["event"] == "cut"]
        self.assertEqual([c["stopped_by"] for c in cuts], ["an", "an"])
        self.assertEqual(status["state"], "pending")
        self.service.jobs = []
        u.job_ended()
        await self.settle()
        self.assertEqual(self.applied, [("release", "an")])


class TheWindowRefusesNewWork(_Base):
    async def test_r11(self):
        u = self.make()
        u.window = True
        with self.assertRaises(updater.Updating):
            u.refuse_while_updating()
        with self.assertRaises(updater.Updating):
            await u.apply("release", "wait", "an")
        with self.assertRaises(updater.Updating):
            u.build_local("an")


class TheSequence(_Base):
    """R12 steps 1 to 7, the trial replaced where the test says so."""

    class Server:
        should_exit = False

    def setUp(self):
        super().setUp()
        self.server = self.Server()
        update.SERVER.register(self.server)

    def make_real(self, trial=""):
        u = self.make(record_apply=False)

        async def fake_trial(target, log):
            if callable(trial):
                return trial()
            return trial

        u._trial = fake_trial  # type: ignore[method-assign]
        return u

    async def run_apply(self, u):
        await u.apply("release", "wait", "an")
        await u._apply_task

    async def test_a_target_that_no_longer_matches_stops_before_anything(self):
        _wheel(self.root / "release", "0.13.0", sums=False)
        (self.root / "release" / update.SUMS).write_text("0" * 64 + "  coscc-0.13.0-py3-none-any.whl\n")
        _wheel(self.root / "current", "0.12.0")
        u = self.make_real()
        await self.run_apply(u)
        self.assertEqual(u.state, "idle")
        self.assertIn("checksum", u.error["message"])
        self.assertFalse(self.server.should_exit)
        self.assertEqual(self.service.shut, 0)

    async def test_an_empty_current_stops_before_anything(self):
        _wheel(self.root / "release", "0.13.0")
        u = self.make_real()
        u._opener = _offline
        await self.run_apply(u)
        self.assertIn("current", u.error["message"])
        self.assertFalse(self.server.should_exit)
        self.assertFalse(u._fetch_lock.locked())

    async def test_an_empty_current_is_filled_by_the_apply_when_the_checker_never_ran(self):
        # Review round 1, F1: `COS_UPDATE_CHECK=0`, so nothing but step 1 fills `current/`.
        self.assertFalse(self.config.update_check)
        _wheel(self.root / "release", "0.13.0")
        u = self.make_real()
        self.assertEqual(u.status()["release"]["state"], "ready")
        u._opener = _serving(_running_release())
        await self.run_apply(u)
        self.assertIsNone(u.error)
        self.assertTrue(self.server.should_exit)
        self.assertEqual(update.verified_wheel(self.root / "current")["version"], "0.12.0")
        self.assertEqual(update.take_handoff().current_version, "0.12.0")

    async def test_a_local_builds_leftovers_in_current_do_not_block_a_release(self):
        # Review round 2, F4: a local build applied from the board, then `install.sh` at a
        # terminal brought 0.12.0; `current/` still holds the local wheel and its manifest.
        current = self.root / "current"
        _wheel(current, "0.11.0+gabcdef0", body=b"local", sums=False)
        update.write_json(current / update.MANIFEST, {"version": "0.11.0+gabcdef0", "commit": "c" * 40,
                                                      "sha256": hashlib.sha256(b"local").hexdigest()})
        _wheel(self.root / "release", "0.13.0")
        u = self.make_real()
        u._opener = _serving(_running_release())
        await self.run_apply(u)
        self.assertIsNone(u.error)
        self.assertEqual(u._current_matches()["version"], "0.12.0")
        self.assertFalse((current / update.MANIFEST).exists())

    async def test_a_local_build_with_no_current_is_not_offered(self):
        # Review round 1, F1: a local build has no release to fetch itself back from.
        self.me["version"] = "0.12.0+gabcdef0"
        _wheel(self.root / "release", "0.13.0")
        u = self.make_real()
        status = u.status()
        self.assertEqual(status["release"]["state"], "blocked")
        self.assertIn("không có đường quay về", status["release"]["reason"])
        with self.assertRaises(updater.Refused):
            await u.apply("release", "wait", "an")
        self.assertIsNone(u._apply_task)
        _wheel(self.root / "current", "0.12.0+gabcdef0")
        self.assertEqual(u.status()["release"]["state"], "ready")

    async def test_the_lock_is_held_after_the_hand_off_and_released_after_a_failure(self):
        # Review round 1, F3: a check cannot replace the wheel `finish` was handed.
        _wheel(self.root / "release", "0.13.0")
        _wheel(self.root / "current", "0.12.0")
        u = self.make_real("the trial failed")
        await self.run_apply(u)
        self.assertFalse(u._fetch_lock.locked())
        u = self.make_real()
        await self.run_apply(u)
        self.assertTrue(u._fetch_lock.locked())

    async def test_no_server_refuses_at_step_one(self):
        update.SERVER.server = None
        _wheel(self.root / "release", "0.13.0")
        _wheel(self.root / "current", "0.12.0")
        u = self.make_real()
        await self.run_apply(u)
        self.assertIn("uvicorn.Server", u.error["message"])

    async def test_a_failed_trial_stops_and_shows_its_log(self):
        _wheel(self.root / "release", "0.13.0")
        _wheel(self.root / "current", "0.12.0")
        u = self.make(record_apply=False)  # the real trial, with a `uv` that is not there
        await self.run_apply(u)
        self.assertEqual(u.state, "idle")
        self.assertIn("cài thử", u.error["message"])
        self.assertIn("tool install", u.error["log_tail"])
        self.assertFalse(self.server.should_exit)
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    async def test_a_job_begun_during_the_trial_goes_back_to_waiting(self):
        _wheel(self.root / "release", "0.13.0")
        _wheel(self.root / "current", "0.12.0")

        def begin_a_step():
            self.service.jobs = [STEP]
            return ""

        u = self.make_real(begin_a_step)
        await self.run_apply(u)
        self.assertEqual(u.state, "pending")
        self.assertIn("trong lúc chạy thử", u.pending["reason"])
        self.assertFalse(u.window)
        self.assertFalse(self.server.should_exit)

    async def test_the_whole_sequence_hands_off_and_stops_the_server(self):
        target = _wheel(self.root / "release", "0.13.0")
        current = _wheel(self.root / "current", "0.12.0", body=b"old")
        self.data.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.data / "cos.db")
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()
        u = self.make_real()
        await self.run_apply(u)
        self.assertIsNone(u.error)
        self.assertTrue(u.window)
        self.assertTrue(self.server.should_exit)
        self.assertEqual((self.service.shut, self.service.sessions.closed), (1, 1))
        backup = sqlite3.connect(self.root / "cos.db.bak")
        self.assertEqual(backup.execute("SELECT x FROM t").fetchall(), [(1,)])
        backup.close()
        h = update.take_handoff()
        self.assertEqual((h.target_wheel, h.current_wheel), (str(target), str(current)))
        self.assertEqual((h.target_version, h.from_version), ("0.13.0", "0.12.0"))
        self.assertEqual((h.tool_dir, h.bin_dir), (self.me["tool_dir"], self.me["bin_dir"]))
        log = Path(h.log).read_text(encoding="utf-8")
        self.assertIn("systemctl --user stop coscc", log)
        self.assertIn("applying", self.service.events())


class WhatARestartReports(_Base):
    def write_last(self, result):
        self.root.mkdir(parents=True, exist_ok=True)
        log = self.root / "logs" / "x.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("line one\nline two\n")
        (self.root / "last.json").write_text(json.dumps({
            "from": "0.12.0", "to": "0.13.0", "result": result, "log": str(log), "finished_at": "t",
        }))

    def test_one_result_row_and_the_file_is_renamed(self):
        self.write_last("failed")
        u = self.make()
        self.assertEqual(self.service.events(), ["result"])
        self.assertEqual(self.service.rows[0]["result"], "failed")
        self.assertFalse((self.root / "last.json").exists())
        self.assertTrue((self.root / "last-reported.json").exists())
        self.assertIn("line two", u.last["log_tail"])
        self.make()
        self.assertEqual(self.service.events(), ["result"])

    def test_applied_moves_the_wheel_into_current(self):
        self.write_last("applied")
        _wheel(self.root / "release", "0.13.0")
        _wheel(self.root / "current", "0.12.0")
        self.make()
        self.assertEqual(update.verified_wheel(self.root / "current")["version"], "0.13.0")
        self.assertFalse((self.root / "release").exists())

    def test_nothing_happens_where_updates_are_not_available(self):
        self.write_last("failed")
        self.me["shape"] = "unavailable"
        self.me["reason"] = "x"
        u = self.make()
        self.assertEqual(self.service.rows, [])
        self.assertEqual(set(u.status()), {"version", "commit", "commit_label", "install", "shape", "reason", "build_id"})
        with self.assertRaises(updater.NotHere):
            u.cut_list()


class TheChecker(_Base):
    """R3 to R5, one check at a time, with an opener serving bytes from a dict."""

    def opener_for(self, wheel=b"new wheel", sums_for=b"new wheel", latest=None):
        tag = "v0.13.0"
        base = f"{update.DOWNLOAD_PREFIX}{tag}/"
        name = update.wheel_name("0.13.0")
        latest = latest if latest is not None else json.dumps({
            "tag_name": tag,
            "assets": [{"name": name, "browser_download_url": base + name},
                       {"name": "SHA256SUMS", "browser_download_url": base + "SHA256SUMS"}],
        }).encode()
        cur = update.release_urls("v0.12.0")
        files = {
            update.LATEST_API: latest,
            base + name: wheel,
            base + "SHA256SUMS": f"{hashlib.sha256(sums_for).hexdigest()}  {name}\n".encode(),
            cur[0]: b"old wheel",
            cur[1]: f"{hashlib.sha256(b'old wheel').hexdigest()}  {update.wheel_name('0.12.0')}\n".encode(),
        }

        def opener(url):
            import io
            if url not in files:
                raise OSError("offline")
            return io.BytesIO(files[url])

        return opener

    def checker(self, opener):
        return updater.Updater(self.config, self.service, me=dict(self.me), opener=opener,
                               check_tag=lambda t: "release", start=False)

    def test_a_new_release_is_found_downloaded_and_ready(self):
        u = self.checker(self.opener_for())
        u.check_once()
        self.assertEqual(u.release["state"], "ready")
        self.assertEqual(u.release["version"], "0.13.0")
        self.assertEqual(self.service.events(), ["found", "downloaded"])
        self.assertTrue(u.checked_at)
        # R5: the running release's own wheel is kept for going back.
        self.assertEqual(update.verified_wheel(self.root / "current")["version"], "0.12.0")

    def test_a_checksum_that_does_not_match_is_an_error_and_nothing_is_kept(self):
        u = self.checker(self.opener_for(wheel=b"swapped"))
        u.check_once()
        self.assertEqual((u.release["state"], u.release["reason"]), ("error", "lỗi checksum"))
        self.assertEqual(self.service.events(), ["found", "checksum-failed"])
        self.assertEqual(list((self.root / "release").glob("*.whl")), [])

    def test_offline_says_nothing_and_keeps_the_state(self):
        def offline(url):
            raise OSError("offline")

        u = self.checker(offline)
        u.release = {"state": "up-to-date"}
        u.check_once()
        self.assertEqual((u.release, u.checked_at, self.service.rows), ({"state": "up-to-date"}, "", []))

    def test_a_body_that_is_not_json_is_offline_too(self):
        u = self.checker(self.opener_for(latest=b"<html>rate limited</html>"))
        u.check_once()
        self.assertEqual((u.checked_at, self.service.rows), ("", []))

    def test_nothing_is_fetched_while_an_update_waits_or_applies(self):
        # Review round 1, F3: the wheel an apply read stays where it read it.
        old = _wheel(self.root / "release", "0.12.5")
        for state in ("pending", "applying"):
            u = self.checker(self.opener_for())
            u.state = state
            u.check_once()
            self.assertEqual(list((self.root / "release").glob("*.whl")), [old])
            self.assertEqual(self.service.rows, [])
        u = self.checker(self.opener_for())
        u._fetch_lock.acquire()
        u.check_once()
        self.assertEqual(list((self.root / "release").glob("*.whl")), [old])
        self.assertFalse((self.root / "current").exists())


class TheLocalChannel(_Base):
    def test_a_local_build_is_ready_only_when_it_differs_and_is_not_lower(self):
        me = self.me
        ready = updater.Updater._local_ready
        self.assertTrue(ready({"version": "0.12.0+gabcdef0", "commit": "abcdef0" + "0" * 33}, me))
        self.assertFalse(ready({"version": "0.12.0", "commit": SHA}, me))
        self.assertFalse(ready({"version": "0.11.0+gabcdef0", "commit": "x"}, me))

    def test_unset_means_no_button(self):
        u = self.make()
        self.assertEqual(u.local["state"], "unconfigured")
        with self.assertRaises(updater.Refused):
            u.build_local("an")


class ItIsNotAnApproval(unittest.TestCase):
    def test_r16_no_gate_no_next_no_step(self):
        source = Path(updater.__file__).read_text(encoding="utf-8") + Path(update.__file__).read_text(encoding="utf-8")
        for word in ("run_step", "\"gate\"", "'gate'", "\"next\"", "next_step"):
            self.assertNotIn(word, source)


if __name__ == "__main__":
    unittest.main()
