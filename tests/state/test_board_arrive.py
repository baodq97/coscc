"""What the Board sends and when, as frames: the cards go first on an arrival, the first poll
repeats nothing, and `watch_board` sends only a board that changed.

Driven through Reflex's own event processor, with a namespace that keeps each delta it is
asked to send."""

from __future__ import annotations

import asyncio
import copy
import time
import unittest
from unittest import mock

from tests.state.test_state import _arrival, _Page, _studio, _until

# What a board read fills; a frame with none of them says nothing about the board.
BOARD_VARS = {"cards", "stages", "board_note", "branch", "recording", "guide_running"}


class _Frames:
    """The `event_namespace` of a processor: every delta it sends, as the set of var names it
    carries (a state's name and the `_rx_state_` marker taken off)."""

    def __init__(self):
        self.sent: list[dict] = []
        # When each frame of `sent` was handed over, on the monotonic clock.
        self.at: list[float] = []

    async def emit_update(self, update, token):
        if update.delta:
            self.sent.append(update.delta)
            self.at.append(time.monotonic())

    def names(self) -> list[set[str]]:
        return [
            {k.removesuffix("_rx_state_") for state in d.values() for k in state} for d in self.sent
        ]

    def carrying(self, name: str) -> list[dict]:
        """The vars of each frame that has `name`."""
        return [
            {k.removesuffix("_rx_state_"): v for state in d.values() for k, v in state.items()}
            for d in self.sent
            if any(k.removesuffix("_rx_state_") == name for state in d.values() for k in state)
        ]


class _Reads:
    """`SERVICE.board`: `held` and `new` answer `now`; `next` waits for a read the test puts."""

    def __init__(self, board: dict):
        self.now = board
        self.ended: asyncio.Queue = asyncio.Queue()
        self.asked: list[str] = []

    async def __call__(self, cwd, which="new"):
        self.asked.append(which)
        if which == "next":
            return await self.ended.get()
        return self.now


def _with_units(*names: str) -> dict:
    board = copy.deepcopy(_Page.BOARD)
    board["units"] = [{**board["units"][0], "name": n} for n in names]
    return board


class TheBoardArrives(unittest.TestCase):
    """One tab, `/board?ws=a`, first arrival on socket `s1`."""

    def _run(self, scenario):
        from reflex.istate.manager.memory import StateManagerMemory
        from reflex_base.event.processor import BaseStateEventProcessor

        from coscc import state as page

        token = "state-test-board-arrive"
        fake, frames = _Page(), _Frames()
        reads = _Reads(_with_units("0009_x"))

        async def go():
            manager = StateManagerMemory()
            processor = BaseStateEventProcessor().configure(
                state_manager=manager, event_namespace=frames
            )
            arrive = _arrival(manager, processor, token)
            async with processor:
                await scenario(arrive, manager, token, fake, frames, reads)
                await arrive("/sessions?ws=a", "s9")  # leaves the Board: both loops end
                await _until(lambda: token not in page._WATCHING, "the board watch to end")
                await _until(lambda: token not in page.ATTEMPT_WAKES, "the attempt watch to end")

        with (
            fake.patches(),
            mock.patch.object(page.app.SERVICE, "board", reads),
            mock.patch.object(page, "BOARD_WAIT", 0.05),
        ):
            asyncio.run(go())
        return frames

    def test_the_cards_are_sent_before_models_backlog_and_update(self):
        async def scenario(arrive, manager, token, fake, frames, reads):
            await arrive("/board?ws=a", "s1")

        frames = self._run(scenario)
        names = frames.names()
        first = {n: next((i for i, f in enumerate(names) if n in f), None) for n in ("cards",)}
        later = [
            next((i for i, f in enumerate(names) if n in f), None)
            for n in ("model_problems", "upd_version")
        ]
        self.assertIsNotNone(first["cards"])
        self.assertTrue(all(i is not None and first["cards"] < i for i in later), (first, later))
        # That frame is the loaded board, not the emptied one a read starts with.
        self.assertIn("0009_x", str(frames.carrying("cards")[0]["cards"]))

    def test_the_first_poll_does_not_send_the_cards_again(self):
        async def scenario(arrive, manager, token, fake, frames, reads):
            await arrive("/board?ws=a", "s1")
            sent = len(frames.sent)
            # The arrival asked for the running list once; the poll's asks come after it.
            await _until(lambda: fake.calls["update_status"] >= 3, "two polls")
            self.assertEqual(
                [f for f in frames.names()[sent:] if f & BOARD_VARS],
                [],
            )

        self._run(scenario)

    def test_a_read_of_the_same_board_sends_nothing_and_a_changed_one_sends_the_cards(self):
        async def scenario(arrive, manager, token, fake, frames, reads):
            await arrive("/board?ws=a", "s1")
            await _until(lambda: "next" in reads.asked, "the watch to wait")
            sent = len(frames.sent)
            await reads.ended.put({**copy.deepcopy(reads.now), "read_at": "2026-10-02T10:00:00Z"})
            await _until(lambda: reads.ended.empty(), "the watch to take the read")
            await asyncio.sleep(0.2)
            self.assertEqual([f for f in frames.names()[sent:] if f & BOARD_VARS], [])

            await reads.ended.put(_with_units("0009_x", "0010_y"))
            await _until(
                lambda: any("0010_y" in str(f["cards"]) for f in frames.carrying("cards")),
                "the changed board's cards",
            )
            studio = await _studio(manager, token)
            self.assertEqual([c.id for c in studio.cards], ["0009_x", "0010_y"])

        self._run(scenario)

    def test_a_click_reaches_the_open_tab_within_a_second_without_a_board_read(self):
        from coscc import state as page
        from coscc.bus import Event

        def row(**fields) -> dict:
            return {
                "unit": "0010_y",
                "stage": "impl",
                "started_at": "2026-10-02T10:00:00Z",
                "state": "queued",
                "stopping": False,
                "run": None,
                "kind": "step",
                **fields,
            }

        listed: list[dict] = []
        moves = (
            ("step.queued", row()),
            ("step.preparing", row(state="preparing")),
            ("step.stop-asked", row(state="preparing", stopping=True)),
        )

        async def scenario(arrive, manager, token, fake, frames, reads):
            def when_shown(since: int, want: dict) -> float | None:
                """When the first frame after the first `since` carries the row as `want`."""
                for at, delta in zip(frames.at[since:], frames.sent[since:]):
                    for state in delta.values():
                        for key, rows in state.items():
                            if key.removesuffix("_rx_state_") == "running_steps" and any(
                                r.state == want["state"] and r.stopping == want["stopping"]
                                for r in rows
                            ):
                                return at
                return None

            await arrive("/board?ws=a", "s1")
            await _until(lambda: token in page.ATTEMPT_WAKES, "the attempt watch to wait")
            await asyncio.sleep(0.2)
            first = len(frames.sent)
            with mock.patch.object(
                page.app.SERVICE.steps, "running_steps", lambda cwd: list(listed)
            ):
                for name, want in moves:
                    listed[:] = [want]
                    since = len(frames.sent)
                    published = time.monotonic()
                    page.app.SERVICE.bus.publish(Event(name, "/a", "0010_y"))
                    await _until(lambda: when_shown(since, want) is not None, f"the {name} frame")
                    self.assertLess(when_shown(since, want) - published, 1.0, name)
            # No board read ended, and no card was sent, for any of the three.
            self.assertTrue(reads.ended.empty())
            self.assertEqual([f for f in frames.names()[first:] if f & BOARD_VARS], [])
            studio = await _studio(manager, token)
            self.assertEqual(
                [(r.unit, r.state, r.stopping) for r in studio.running_steps],
                [("0010_y", "preparing", True)],
            )

        self._run(scenario)
