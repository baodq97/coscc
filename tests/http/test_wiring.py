from __future__ import annotations

import unittest
from unittest import mock

from coscc.bus import NAMES
from coscc.config import Config
from coscc.agent.sessions import Sessions
from coscc.http.app import Core


def _core() -> Core:
    config = Config(workspaces=("/tmp",))
    return Core(config, Sessions(config))


class TheServiceWiresWhoListensToWhat(unittest.TestCase):
    def setUp(self):
        self.core = _core()
        self.ended = mock.patch.object(self.core.updater, "job_ended").start()
        self.nudged = mock.patch.object(self.core.autopilot, "nudge").start()
        self.addCleanup(mock.patch.stopall)

    def test_every_event_name_has_a_subscriber(self):
        # An agent run's facts and a board read only tell the page to read again: `/api/stream`
        # relays them (`test_routes_stream`); the run holds the updater itself.
        for name in NAMES:
            if name in ("agent-run.started", "agent-run.ended", "board.read"):
                continue
            with self.subTest(name=name):
                self.assertTrue(self.core.bus._handlers[name])

    def test_a_step_ended_by_the_app_going_down_wakes_only_the_updater(self):
        self.core.bus.publish("step.ended", {"workspace": "k", "unit": "u", "going_down": True})
        self.ended.assert_called_once_with()
        self.nudged.assert_not_called()

    def test_a_written_answer_wakes_only_the_autopilot(self):
        self.core.bus.publish("answer.written", {"workspace": "k", "unit": "u"})
        self.ended.assert_not_called()
        self.nudged.assert_called_once_with("k")

    def test_a_refused_step_or_integration_wakes_the_updater_and_the_autopilot(self):
        # The autopilot reads a refusal of what it queued from its row, on the pass this wakes.
        for name in ("step.refused", "integration.refused"):
            with self.subTest(name=name):
                self.ended.reset_mock()
                self.nudged.reset_mock()
                self.core.bus.publish(name, {"workspace": "k", "unit": "u", "going_down": False})
                self.ended.assert_called_once_with()
                self.nudged.assert_called_once_with("k")
