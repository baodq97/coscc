"""Tests for `Notices` in `coscc/features/notices.py`.

The generator is read directly, with `beat` shortened: `httpx.ASGITransport` collects a whole
response body, so a stream that lasts `notices.LIFETIME_SECONDS` is read through it only with
that shortened (`test_api.py`)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from coscc import auth, plugin
from coscc.features import notices
from coscc.config import Config
from coscc.store.journal import BELL, Journal
from coscc.service import Service
from coscc.kernel import Invalid
from coscc.agent.sessions import Sessions

BEAT = 0.3


def stop(key: str, unit: str = "0001_a", kind: str = "a") -> dict:
    return {
        "kind": "autopilot-stop",
        "workspace": key,
        "unit": unit,
        "stage": "",
        "stop": kind,
        "reason": "r",
    }


class FollowingNotices(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.ws, self.other = root / "work" / "proj", root / "work" / "other"
        self.ws.mkdir(parents=True)
        self.other.mkdir(parents=True)
        self.config = Config(
            workspaces=(str(self.ws), str(self.other)),
            working_dir=str(root / "work"),
            data_dir=str(root / "data"),
        )
        self.service = Service(self.config, Sessions(self.config))
        self.feed = notices.Notices(plugin.ctx_of(self.service))
        self.key = self.service.ws.key(str(self.ws))
        self.journal = Journal(self.config.working_dir, self.config.data_dir)
        self.streams = []

    async def asyncTearDown(self):
        for s in self.streams:
            await s.aclose()

    def follow(self, after=None, workspace="", beat=BEAT, lifetime=None):
        s = self.feed.follow_notices(
            self.feed.notice_scope(workspace), after, beat=beat, lifetime=lifetime
        )
        self.streams.append(s)
        return s

    async def lines(self, stream, n: int, within: float = 5.0) -> list[dict]:
        return [await asyncio.wait_for(stream.__anext__(), within) for _ in range(n)]

    async def notices(self, stream, n: int, within: float = 5.0) -> list[dict]:
        """The next `n` notice lines, skipping beats."""
        out, deadline = [], time.monotonic() + within
        while len(out) < n:
            line = await asyncio.wait_for(
                stream.__anext__(), max(0.01, deadline - time.monotonic())
            )
            if line["type"] == "notice":
                out.append(line)
        return out

    def append(self, record: dict) -> int:
        self.journal.append(record)
        return self.journal.last_id()

    async def test_without_after_the_first_line_is_head_and_nothing_older_follows(self):
        old = self.append(stop(self.key))
        s = self.follow()
        [head] = await self.lines(s, 1)
        self.assertEqual(head, {"type": "head", "id": old})
        new = self.append(stop(self.key, "0002_b"))
        [line] = await self.notices(s, 1)
        self.assertEqual((line["id"], line["unit"]), (new, "0002_b"))

    async def test_with_after_every_notice_past_it_comes_first_in_id_order_once(self):
        first = self.append(stop(self.key, "0001_a"))
        ids = [self.append(stop(self.key, u)) for u in ("0002_b", "0003_c")]
        self.append(
            {
                "kind": "start",
                "workspace": self.key,
                "unit": "0003_c",
                "stage": "spec",
                "mode": "manual",
            }
        )
        s = self.follow(after=first)
        got = await self.notices(s, 2)
        self.assertEqual([n["id"] for n in got], ids)
        later = self.append(stop(self.key, "0004_d"))
        [line] = await self.notices(s, 1)
        self.assertEqual(line["id"], later)

    async def test_an_after_past_every_row_is_a_head_and_what_lands_next_arrives(self):
        # A cursor kept from a run log since deleted or replaced.
        now = self.append(stop(self.key))
        s = self.follow(after=now + 1000)
        [head] = await self.lines(s, 1)
        self.assertEqual(head, {"type": "head", "id": now})
        new = self.append(stop(self.key, "0002_b"))
        [line] = await self.notices(s, 1)
        self.assertEqual(line["id"], new)

    async def test_an_after_at_the_last_row_is_no_head(self):
        now = self.append(stop(self.key))
        s = self.follow(after=now)
        new = self.append(stop(self.key, "0002_b"))
        [line] = await self.lines(s, 1)
        self.assertEqual((line["type"], line["id"]), ("notice", new))

    async def test_after_zero_replays_the_whole_history(self):
        ids = [self.append(stop(self.key, u)) for u in ("0001_a", "0002_b")]
        got = await self.notices(self.follow(after=0), 2)
        self.assertEqual([n["id"] for n in got], ids)

    async def test_an_append_in_this_process_arrives_within_five_seconds(self):
        s = self.follow(beat=60)
        await self.lines(s, 1)
        began = time.monotonic()
        # From another thread, as a step's journal writes can be.
        await asyncio.to_thread(self.append, stop(self.key))
        await self.notices(s, 1, within=5)
        self.assertLess(time.monotonic() - began, 5)

    async def test_a_row_another_process_wrote_arrives_at_the_next_beat(self):
        s = self.follow(beat=1.0)
        await self.lines(s, 1)
        record = {"v": 1, "at": "2026-09-27T10:00:00Z", **stop(self.key)}
        conn = sqlite3.connect(Path(self.config.data_dir) / "cos.db")
        try:
            conn.execute(
                "INSERT INTO runs (at, root, workspace, unit, stage, kind, record) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record["at"],
                    self.journal._root,
                    self.key,
                    "0001_a",
                    "",
                    "autopilot-stop",
                    json.dumps(record),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        began = time.monotonic()
        [line] = await self.notices(s, 1, within=5)
        self.assertEqual(line["record"], record)
        self.assertLess(time.monotonic() - began, 1.0 + 1.0)

    async def test_a_beat_comes_when_nothing_happens(self):
        s = self.follow()
        head, beat = await self.lines(s, 2, within=BEAT * 5)
        self.assertEqual(beat, {"type": "beat", "id": head["id"]})

    async def test_a_stream_ends_once_its_lifetime_is_over_and_nothing_is_skipped(self):
        # The login door is asked once per request, so the stream ends.
        s = self.follow(beat=60, lifetime=0.5)
        await self.lines(s, 1)
        began = time.monotonic()
        mine = self.append(stop(self.key))
        [line] = await self.notices(s, 1)
        self.assertEqual(line["id"], mine)
        with self.assertRaises(StopAsyncIteration):
            await asyncio.wait_for(s.__anext__(), 3)
        self.assertLess(time.monotonic() - began, 0.5 + 1.0)
        # Written between two streams: the next, with `after`, hands it over.
        between = self.append(stop(self.key, "0002_b"))
        [again] = await self.notices(self.follow(after=mine), 1)
        self.assertEqual(again["id"], between)

    def test_a_stream_lasts_no_longer_than_the_guard_allows(self):
        self.assertLessEqual(notices.LIFETIME_SECONDS, auth.STREAM_SECONDS)
        self.assertLess(notices.BEAT_SECONDS, notices.LIFETIME_SECONDS)

    async def test_one_workspace_sees_only_its_own(self):
        other = self.service.ws.key(str(self.other))
        self.append(stop(other))
        mine = self.append(stop(self.key))
        got = await self.notices(self.follow(after=0, workspace=str(self.ws)), 1)
        self.assertEqual([n["id"] for n in got], [mine])
        everything = await self.notices(self.follow(after=0), 2)
        self.assertEqual({n["workspace"] for n in everything}, {self.key, other})

    async def test_a_workspace_with_notices_off_is_passed_over_and_another_still_arrives(self):
        ctx = plugin.ctx_of(self.service)
        plugin.set_state(self.service, ctx, [notices.FEATURE], "notices", str(self.other), "off")
        other = self.service.ws.key(str(self.other))
        self.append(stop(other))
        mine = self.append(stop(self.key))
        got = await self.notices(self.follow(after=0), 1)
        self.assertEqual([n["id"] for n in got], [mine])
        later = self.append(stop(other, "0002_b"))
        last = self.append(stop(self.key, "0003_c"))
        [line] = await self.notices(self.follow(after=mine), 1)
        self.assertEqual(line["id"], last)
        self.assertGreater(later, mine)

    async def test_a_ship_still_merging_makes_no_ship_refused_notice(self):
        """`after_end` of a `ship` the loop reads `ship-merging` writes no `ship` record, so no
        notice says the unit did not merge; one read `ship-refused` still does."""
        from coscc.units import board as board_reader

        for why, unit in (("ship-merging", "0001_a"), ("ship-refused", "0002_b")):

            async def read(root, timeout=None, state=None, why=why, unit=unit):
                return {"units": [{"name": unit, "why": why, "questions": []}]}

            with mock.patch.object(board_reader, "read", read):
                await self.service.steps.after_end(str(self.ws), unit, "ship", self.key)
        last = self.append(stop(self.key, "0003_c"))
        got = await self.notices(self.follow(after=0), 2)
        self.assertEqual(
            [(n["kind"], n["unit"]) for n in got],
            [("ship-refused", "0002_b"), ("autopilot-stop", "0003_c")],
        )
        self.assertEqual(got[-1]["id"], last)

    async def test_following_writes_nothing_to_the_run_log(self):
        self.append(stop(self.key))
        before = self.journal.last_id()
        s = self.follow(after=0)
        await self.lines(s, 3, within=BEAT * 10)
        self.assertEqual(self.journal.last_id(), before)

    async def test_an_unknown_workspace_is_refused(self):
        for workspace in ("/etc", str(self.ws.parent)):
            with self.assertRaises(Invalid):
                self.feed.notice_scope(workspace)

    async def test_a_closed_stream_leaves_no_ticket_behind(self):
        before = len(BELL)
        s = self.follow(beat=60)
        await self.lines(s, 1)
        task = asyncio.ensure_future(s.__anext__())
        # The stream has hung its ticket on the bell and waits.
        for _ in range(500):
            if len(BELL) == before + 1:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(len(BELL), before + 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await s.aclose()
        self.assertEqual(len(BELL), before)


if __name__ == "__main__":
    unittest.main()
