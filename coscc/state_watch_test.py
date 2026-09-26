"""Tests for `WatchMixin` in `coscc/state_watch.py`, split from `coscc/state_test.py` (`0095`).
"""

from __future__ import annotations

import unittest

from coscc.state_test import _key, _processor, _somewhere, _studio


class TheWatchPaneKeepsAWindow(unittest.TestCase):
    """`0073` plan step 7. Driven through Reflex's event processor with `events_page` and
    `follow_events` answered from memory: the list never passes `WATCH_WINDOW`, it drops
    from the top while following, counts what it cannot take while scrolled up, and the
    whole of an opened event is never in it."""

    @staticmethod
    def ev(seq, text="x"):
        return {"run": "r", "seq": seq, "at": 1_700_000_000_000 + seq, "kind": "text", "text": text}

    def patches(self, total_before: int, batches: list[list[int]]):
        import contextlib
        from unittest import mock

        from coscc import state as page

        test = self
        stored = {n: self.ev(n) for n in range(1, total_before + 1)}
        stored[5] = self.ev(5, "long " * 1000)

        def events_page(cwd, unit, run, before=None, limit=200, seq=None):
            if seq is not None:
                return {"status": "running", "events": [stored[seq]], "has_older": False}
            below = [stored[n] for n in sorted(stored) if before is None or n < before]
            found = below[-limit:]
            return {"status": "running", "events": found, "has_older": len(below) > len(found),
                    "events_lost": 0, "last_at": None, "purged_at": None}

        async def follow_events(cwd, unit, run, after=0, gather=0.0):
            test.followed_after = after
            for batch in batches:
                for n in batch:
                    stored[n] = test.ev(n)
                yield ("events", [stored[n] for n in batch])

        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(page.SERVICE, "events_page", events_page))
        stack.enter_context(mock.patch.object(page.SERVICE, "follow_events", follow_events))
        return stack

    def test_following_keeps_the_last_window_and_an_opened_event_stays_apart(self):
        import asyncio

        from coscc import state as page

        token = "state-test-watch"

        async def go():
            manager, processor, fire = _processor(token)
            async with processor:
                await _somewhere(manager, token)
                await fire("open_watch", run="r", title="0009_x · impl", unit="0009_x")
                await asyncio.sleep(0.3)
                studio = await _studio(manager, token)
                seqs = [e.seq for e in studio.watch_events]
                bodies = max(len(e.body) for e in studio.watch_events)
                await fire("watch_expand", seq=5)
                studio = await _studio(manager, token)
                return seqs, bodies, studio.watch_open_text, studio.watch_has_older, studio.watch_status

        with self.patches(600, [list(range(601, 701)), list(range(701, 901))]):
            seqs, bodies, opened, older, status = asyncio.run(go())
        self.assertEqual(self.followed_after, 600)
        self.assertEqual(len(seqs), page.WATCH_WINDOW)
        self.assertEqual(seqs, list(range(501, 901)))
        self.assertTrue(older)
        self.assertEqual(status, "running")
        self.assertLessEqual(bodies, page.events_mod.COLLAPSE_CHARS)
        self.assertEqual(len(opened), len("long " * 1000))

    def test_scrolled_up_and_full_new_events_are_counted_not_added(self):
        import asyncio

        from coscc import state as page

        token = "state-test-watch-up"

        async def go():
            manager, processor, _ = _processor(token)
            async with processor:
                async with manager.modify_state(_key(token)) as root:
                    studio = await root.get_state(page.StudioState)
                    studio.watch_following = False
                    studio.watch_events = [page.WatchEvent(seq=n) for n in range(1, 391)]
                    studio._watch_take([page.WatchEvent(seq=n) for n in range(391, 411)])
                    return len(studio.watch_events), studio.watch_pending, studio.watch_events[-1].seq

        self.assertEqual(asyncio.run(go()), (page.WATCH_WINDOW, 10, 400))

    def test_a_batch_already_in_the_last_page_is_not_shown_twice(self):
        """`review.md` F1: *Jump to latest* read the last page while a batch the follower had
        yielded waited for the state; applied after, it adds nothing it already shows."""
        import asyncio

        from coscc import state as page

        token = "state-test-watch-twice"

        async def go():
            manager, processor, _ = _processor(token)
            async with processor:
                async with manager.modify_state(_key(token)) as root:
                    studio = await root.get_state(page.StudioState)
                    studio.watch_following = True
                    studio.watch_events = [page.WatchEvent(seq=n) for n in range(1, 211)]
                    studio._watch_take([page.WatchEvent(seq=n) for n in range(201, 216)])
                    return [e.seq for e in studio.watch_events]

        self.assertEqual(asyncio.run(go()), list(range(1, 216)))

    def test_older_pages_past_the_window_say_newer_rows_left_and_about_to_the_end_returns(self):
        """`review.md` F2: an ended step of 654 events, scrolled up twice. The newest rows
        leave the list, the pane says so, a live batch is not appended after the gap, and
        *Jump to latest* reads the last page again, `end` included."""
        import asyncio

        from coscc import state as page

        token = "state-test-watch-newer"

        async def go():
            manager, processor, fire = _processor(token)
            async with processor:
                async with manager.modify_state(_key(token)) as root:
                    studio = await root.get_state(page.StudioState)
                    studio.watch_run, studio.watch_unit = "r", "0009_x"
                    studio.watch_events = [page.WatchEvent(seq=n) for n in range(455, 655)]
                    studio.watch_has_older, studio.watch_status = True, "ended"
                await fire("watch_older")
                await fire("watch_older")
                studio = await _studio(manager, token)
                seqs, newer = [e.seq for e in studio.watch_events], studio.watch_has_newer
                async with manager.modify_state(_key(token)) as root:
                    studio = await root.get_state(page.StudioState)
                    studio._watch_take([page.WatchEvent(seq=655)])
                    pending = studio.watch_pending
                await fire("watch_live")
                studio = await _studio(manager, token)
                return seqs, newer, pending, [e.seq for e in studio.watch_events], studio.watch_has_newer

        with self.patches(654, []):
            seqs, newer, pending, live, after = asyncio.run(go())
        self.assertEqual(seqs, list(range(55, 455)))
        self.assertTrue(newer)
        self.assertEqual(pending, 1)
        self.assertEqual(live, list(range(455, 655)))
        self.assertFalse(after)

    def test_the_four_notes_read_as_r13_says(self):
        """`0073` R13's four notes, in English since `0089` R14 (S6)."""
        from coscc import state as page
        from coscc.screens_test import VIETNAMESE

        self.assertEqual(page.NO_RUN_NOTE, "no event stream: this step ran before events were recorded")
        purged = page._watch_note({"status": "purged", "purged_at": "2026-10-01T00:00:00+00:00"})
        self.assertTrue(purged.startswith("events purged ("), purged)
        self.assertNotIn("2026-10-01T", purged)
        unknown = page._watch_note({"status": "ended-unknown", "last_at": 1_700_000_000_000})
        self.assertIn("the app stopped while this step ran; no events after ", unknown)
        none = page._watch_note({"status": "none"})
        self.assertEqual(none, "no events were stored for this run")
        lost = page._watch_note({"status": "ended", "events_lost": 3})
        self.assertEqual(lost, "3 events missing")
        for note in (page.NO_RUN_NOTE, purged, unknown, none, lost):
            self.assertIsNone(VIETNAMESE.search(note), note)
