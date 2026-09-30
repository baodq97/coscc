from __future__ import annotations

import unittest

from coscc.bus import Bus, Event


class HandlersHearTheEventsOfTheirName(unittest.TestCase):
    def test_handlers_run_in_the_order_they_subscribed(self):
        bus, seen = Bus(), []
        bus.subscribe("step.ended", lambda e: seen.append("first"))
        bus.subscribe("step.ended", lambda e: seen.append("second"))
        bus.publish(Event("step.ended"))
        self.assertEqual(seen, ["first", "second"])

    def test_only_the_handlers_of_the_events_name_are_called(self):
        bus, seen = Bus(), []
        bus.subscribe("step.ended", lambda e: seen.append(e))
        bus.subscribe("answer.written", lambda e: seen.append(e))
        event = Event("answer.written", "k", "0001_x")
        bus.publish(event)
        self.assertEqual(seen, [event])

    def test_a_raising_handler_is_logged_and_the_next_still_runs(self):
        bus, seen = Bus(), []

        def boom(e):
            raise RuntimeError("no")

        bus.subscribe("step.ended", boom)
        bus.subscribe("step.ended", lambda e: seen.append("after"))
        with self.assertLogs("coscc.bus", "ERROR") as logged:
            bus.publish(Event("step.ended"))
        self.assertEqual(seen, ["after"])
        self.assertIn("RuntimeError", "\n".join(logged.output))

    def test_each_event_is_logged_at_debug(self):
        with self.assertLogs("coscc.bus", "DEBUG") as logged:
            Bus().publish(Event("answer.written", "k", "u"))
        self.assertIn("answer.written k u", logged.output[0])
