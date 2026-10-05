from __future__ import annotations

import unittest
from unittest import mock

from coscc.bus import NAMES, Event
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
        for name in NAMES:
            with self.subTest(name=name):
                self.assertTrue(self.core.bus._handlers[name])

    def test_a_step_ended_by_the_app_going_down_wakes_only_the_updater(self):
        self.core.bus.publish(Event("step.ended", "k", "u", going_down=True))
        self.ended.assert_called_once_with()
        self.nudged.assert_not_called()

    def test_a_written_answer_wakes_only_the_autopilot(self):
        self.core.bus.publish(Event("answer.written", "k", "u"))
        self.ended.assert_not_called()
        self.nudged.assert_called_once_with("k")
