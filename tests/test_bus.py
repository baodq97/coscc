from __future__ import annotations

import unittest
from typing import Any

from coscc.bus import NAMES, SCHEMAS, Bus

UNIT = {"workspace": "k", "unit": "0001_x"}
MOVED = {**UNIT, "going_down": False}


class HandlersHearTheEventsOfTheirName(unittest.TestCase):
    def test_handlers_run_in_the_order_they_subscribed(self):
        bus, seen = Bus(), []
        bus.subscribe("step.ended", lambda e: seen.append("first"))
        bus.subscribe("step.ended", lambda e: seen.append("second"))
        bus.publish("step.ended", MOVED)
        self.assertEqual(seen, ["first", "second"])

    def test_only_the_handlers_of_the_events_name_are_called(self):
        bus, seen = Bus(), []
        bus.subscribe("step.ended", lambda e: seen.append(e))
        bus.subscribe("answer.written", lambda e: seen.append(e))
        bus.publish("answer.written", UNIT)
        self.assertEqual([(e.name, e.payload) for e in seen], [("answer.written", UNIT)])

    def test_a_raising_handler_is_logged_and_the_next_still_runs(self):
        bus, seen = Bus(), []

        def boom(e):
            raise RuntimeError("no")

        bus.subscribe("step.ended", boom)
        bus.subscribe("step.ended", lambda e: seen.append("after"))
        with self.assertLogs("coscc.bus", "ERROR") as logged:
            bus.publish("step.ended", MOVED)
        self.assertEqual(seen, ["after"])
        self.assertIn("RuntimeError", "\n".join(logged.output))


class AWatcherHearsEverything(unittest.TestCase):
    def test_a_watcher_hears_every_name_after_the_subscribers_until_it_stops(self):
        bus, seen = Bus(), []
        stop = bus.watch(lambda e: seen.append(("watch", e.name)))
        bus.subscribe("step.ended", lambda e: seen.append(("sub", e.name)))
        bus.publish("step.ended", MOVED)
        bus.publish("answer.written", UNIT)
        stop()
        bus.publish("mode.set", UNIT)
        self.assertEqual(
            seen, [("sub", "step.ended"), ("watch", "step.ended"), ("watch", "answer.written")]
        )


class APayloadOutsideItsSchemaIsRefused(unittest.TestCase):
    def test_every_name_declares_a_payload(self):
        moves = [n for n in NAMES if n not in SCHEMAS]
        bus = Bus()
        publish: Any = bus.publish
        for name in moves:
            publish(name, MOVED)
        self.assertIn("step.ended", moves)

    def test_a_move_of_a_machine_no_name_declares_is_refused(self):
        publish: Any = Bus().publish
        with self.assertRaisesRegex(ValueError, "no bus subject"):
            publish("scan.ended", MOVED)

    def test_a_missing_an_extra_or_a_mistyped_field_is_refused_before_any_handler(self):
        for name, payload, says in (
            ("answer.written", {"workspace": "k"}, "carries"),
            ("answer.written", {**UNIT, "secret": "s3cr3t"}, "carries"),
            ("step.ended", UNIT, "carries"),
            ("step.ended", {**MOVED, "going_down": "no"}, "going_down is a bool"),
            ("unit.shipped", {**UNIT, "sha": "abc", "at": 1}, "at is a str"),
            ("nothing.happened", {}, "no bus subject"),
        ):
            with self.subTest(name=name, payload=payload):
                bus, seen = Bus(), []
                bus.watch(seen.append)
                publish: Any = bus.publish
                with self.assertRaises(ValueError) as refused:
                    publish(name, payload)
                self.assertIn(says, str(refused.exception))
                self.assertEqual(seen, [])

    def test_a_shipped_unit_carries_its_merge(self):
        bus, seen = Bus(), []
        bus.watch(seen.append)
        bus.publish("unit.shipped", {**UNIT, "sha": "abc", "at": "2026-10-06T00:00:00+00:00"})
        self.assertEqual(seen[0].payload["sha"], "abc")
