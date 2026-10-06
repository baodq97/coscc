"""`GET /api/stream`: every bus event as a server-sent event, and nothing left behind."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from coscc.http import routes as api
from coscc.bus import Bus


class TheStreamForwardsTheBus(unittest.IsolatedAsyncioTestCase):
    async def test_an_event_arrives_as_data_and_closing_stops_the_watch(self):
        bus = Bus()
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(core=SimpleNamespace(bus=bus)))
        )
        response = await api.stream(request)
        self.assertEqual(response.media_type, "text/event-stream")
        body = response.body_iterator
        self.assertEqual(await anext(body), "retry: 1000\n: open\n\n")
        bus.publish(
            "unit.shipped",
            {"workspace": "/w/coscc", "unit": "0001_x", "sha": "abc", "at": "t"},
        )
        line = await anext(body)
        self.assertTrue(line.startswith("data: ") and line.endswith("\n\n"))
        self.assertEqual(
            json.loads(line[6:]),
            {
                "subject": "unit.shipped",
                "workspace": "/w/coscc",
                "unit": "0001_x",
                "sha": "abc",
                "at": "t",
            },
        )
        await body.aclose()
        self.assertEqual(bus._watchers, [])

    async def test_a_quiet_stream_says_it_is_alive(self):
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(core=SimpleNamespace(bus=Bus())))
        )
        old, api.STREAM_PING_SECONDS = api.STREAM_PING_SECONDS, 0.01
        try:
            body = (await api.stream(request)).body_iterator
            await anext(body)
            self.assertEqual(await anext(body), ": ping\n\n")
            await body.aclose()
        finally:
            api.STREAM_PING_SECONDS = old


class AStreamEnds(unittest.IsolatedAsyncioTestCase):
    async def test_it_ends_with_an_end_event_and_stops_its_watch(self):
        bus = Bus()
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(core=SimpleNamespace(bus=bus)))
        )
        old, api.STREAM_LIFETIME_SECONDS = api.STREAM_LIFETIME_SECONDS, 0.05
        try:
            lines = [line async for line in (await api.stream(request)).body_iterator]
        finally:
            api.STREAM_LIFETIME_SECONDS = old
        self.assertEqual(lines[-1], "event: end\ndata: {}\n\n")
        self.assertEqual(bus._watchers, [])
