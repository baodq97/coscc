"""Tests for `BacklogMixin` in `coscc/state_backlog.py`, split from `coscc/state_test.py` (`0095`).
"""

from __future__ import annotations

import unittest

from coscc.state_test import _arrival, _key, _processor


class TheBacklogPanelIsCopied(unittest.TestCase):
    """`0074`. The card's `#n` and the panel's lines are the board's `backlog`, copied."""

    BOARD = {"stages": [], "recording": True, "units": [
        {"name": "0009_x", "stages": [], "phase": "idea", "next": "", "blocked": True, "problems": [],
         "backlog": {"rank": 1, "value": 4, "effort": "S", "effort_source": "guess", "relations": []}},
        {"name": "0010_y", "stages": [], "phase": "idea", "next": "", "blocked": True, "problems": []},
    ], "backlog": {
        "backlog": ["0009_x", "0010_y"], "measured_count": 2, "terciles": None, "undiscriminating": True,
        "shortlist": [{"rank": 1, "unit": "0009_x", "computed": 1, "drift": False, "in_backlog": True,
                       "warnings": ["bị 0010_y thay thế"], "agent_differs": None,
                       "estimate": {"value": 4, "effort": "S", "effort_source": "guess", "basis": "bớt chi phí",
                                    "effort_basis": "", "by": "agent:s"}}],
        "shortlist_record": {"at": "t", "n": 1, "by": "Leif", "reason": "r"},
        "order": [], "unestimated": ["0010_y"], "warnings": [], "problems": [], "suggested": ["0009_x"],
        "history": {}, "propose_warning": "paid",
    }}

    def test_the_view_copies_what_the_service_decided(self):
        from coscc.state import backlog_view

        view = backlog_view(self.BOARD)
        [row] = view["backlog_rows"]
        self.assertEqual((row.unit, row.value, row.effort, row.warnings), ("0009_x", "4", "S (guess)", "bị 0010_y thay thế"))
        self.assertEqual(row.by, "agent")  # `0082` D68: the session id stays in the API
        self.assertIn("picks nothing out", view["backlog_note"])
        self.assertEqual(view["propose_warning"], "paid")
        self.assertEqual(view["backlog_measured"], "")

    def test_0092_the_units_left_out_for_an_unknown_cost_are_counted(self):
        from coscc.state import backlog_view

        view = backlog_view({**self.BOARD, "backlog": {**self.BOARD["backlog"], "undetermined_count": 3}})
        self.assertEqual(view["backlog_measured"], "2 finished units measured; 3 left out, cost unknown.")

    def test_f3_no_saved_shortlist_leaves_the_empty_row_to_say_so(self):
        from coscc.state import backlog_view

        board = {**self.BOARD, "backlog": {**self.BOARD["backlog"], "shortlist": [], "shortlist_record": None}}
        view = backlog_view(board)
        self.assertEqual(view["backlog_recorded"], "")

    def test_f4_the_rest_carries_its_computed_place_or_none(self):
        from coscc.state import backlog_view

        order = [{"unit": "0011_z", "computed": 2, "estimate": {"value": 3, "effort": "M"}, "agent_differs": None}]
        view = backlog_view({**self.BOARD, "backlog": {**self.BOARD["backlog"], "order": order}})
        self.assertEqual([(r.unit, r.rank) for r in view["backlog_rest"]], [("0011_z", 2), ("0010_y", 0)])

    def test_r9_a_relation_reads_from_both_sides(self):
        from coscc.state import _relations_text

        self.assertEqual(_relations_text([{"type": "thay thế", "other": "0010_y", "direction": "in"},
                                          {"type": "trùng", "other": "0011_z", "direction": "out"}]),
                         "replaced by 0010_y; duplicates 0011_z")
        self.assertEqual(_relations_text(None), "")

    def test_the_card_carries_its_rank(self):
        import asyncio
        from unittest import mock

        from coscc import state as page

        token = "state-test-backlog"

        async def branch_here(cwd):
            return {"branch": "main"}

        async def read_board(cwd):
            return self.BOARD

        async def go():
            manager, processor, _ = _processor(token)
            arrive = _arrival(manager, processor, token)
            async with processor:
                async with manager.modify_state(_key(token)) as root:
                    studio = await root.get_state(page.StudioState)
                    studio.workspaces = [page.Workspace(id="/a", name="a"), page.Workspace(id="/b", name="b")]
                    studio.cwd = studio._read_cwd = "/a"
                    studio.screen = "sessions"
                    studio._loaded_sid = "s1"
                await arrive("/sessions?ws=b", settle=0.05)
                async with manager.modify_state(_key(token)) as root:
                    studio = await root.get_state(page.StudioState)
                    return [(u.id, u.shortlist_rank) for u in studio.cards], list(studio.backlog_unestimated)

        with (
            mock.patch.object(page.SERVICE, "running", lambda cwd: {"running": {}, "unknown_end": {}}),
            mock.patch.object(page.SERVICE, "branch_here", branch_here),
            mock.patch.object(page.SERVICE, "board", read_board),
            mock.patch.object(page.SERVICE, "sessions_for", lambda cwd, limit: {"sessions": []}),
            mock.patch.object(page.SERVICE, "activity_and_usage",
                              mock.Mock(side_effect=page.Invalid("not here"))),
        ):
            cards, unestimated = asyncio.run(go())
        self.assertEqual(cards, [("0009_x", 1), ("0010_y", 0)])
        self.assertEqual(unestimated, ["0010_y"])
