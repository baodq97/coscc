from __future__ import annotations

import unittest
from unittest import mock

from coscc.bus import NAMES, Event
from coscc.config import Config
from coscc.agent.sessions import Sessions
from coscc.service import Service


def _service() -> Service:
    config = Config(workspaces=("/tmp",))
    return Service(config, Sessions(config))


class TheServiceWiresWhoListensToWhat(unittest.TestCase):
    def setUp(self):
        self.service = _service()
        self.ended = mock.patch.object(self.service.updater, "job_ended").start()
        self.nudged = mock.patch.object(self.service.autopilot, "nudge").start()
        self.addCleanup(mock.patch.stopall)

    def test_every_event_name_has_a_subscriber(self):
        for name in NAMES:
            with self.subTest(name=name):
                self.assertTrue(self.service.bus._handlers[name])

    def test_an_ended_step_wakes_the_updater_and_the_autopilot(self):
        self.service.bus.publish(Event("step.ended", "k", "u"))
        self.ended.assert_called_once_with()
        self.nudged.assert_called_once_with("k")

    def test_a_step_ended_by_the_app_going_down_wakes_only_the_updater(self):
        self.service.bus.publish(Event("step.ended", "k", "u", going_down=True))
        self.ended.assert_called_once_with()
        self.nudged.assert_not_called()

    def test_an_ended_integration_wakes_the_updater_and_the_autopilot(self):
        self.service.bus.publish(Event("integration.ended", "k", "u"))
        self.ended.assert_called_once_with()
        self.nudged.assert_called_once_with("k")

    def test_a_written_answer_wakes_only_the_autopilot(self):
        self.service.bus.publish(Event("answer.written", "k", "u"))
        self.ended.assert_not_called()
        self.nudged.assert_called_once_with("k")

    def test_a_saved_shortlist_and_a_moved_hold_wake_only_the_autopilot(self):
        for name in ("shortlist.saved", "hold.moved"):
            with self.subTest(name=name):
                self.nudged.reset_mock()
                self.service.bus.publish(Event(name, "k", "u"))
                self.nudged.assert_called_once_with("k")
        self.ended.assert_not_called()

    def test_a_refused_step_or_integration_wakes_the_updater_and_the_autopilot(self):
        # The autopilot reads a refusal of what it queued from its row, on the pass this wakes.
        for name in ("step.refused", "integration.refused"):
            with self.subTest(name=name):
                self.ended.reset_mock()
                self.nudged.reset_mock()
                self.service.bus.publish(Event(name, "k", "u"))
                self.ended.assert_called_once_with()
                self.nudged.assert_called_once_with("k")

    def test_the_other_endings_wake_only_the_updater(self):
        # `step.released` is gone (0150): a step that never ran ends its attempt `step.refused`.
        for name in ("integration.escalated", "retake.ended", "estimate.ended"):
            with self.subTest(name=name):
                self.ended.reset_mock()
                self.service.bus.publish(Event(name, "k", "u"))
                self.ended.assert_called_once_with()
        self.nudged.assert_not_called()
