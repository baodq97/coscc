"""Tests for the startup banner. Nothing here starts a server.

The banner is the only place a person is told what binding `0.0.0.0` costs them, and
`0011`'s `spec.md` R5 makes that a requirement rather than a courtesy. A test is what
stops it from being quietly shortened later -- there is no other check in this repository
that would notice its absence.
"""

from __future__ import annotations

import unittest

from coscc import run
from coscc.config import Config


class WhatStartupSays(unittest.TestCase):
    def test_the_default_is_no_longer_loopback(self):
        # If this ever goes back, the warning below stops being reachable by default and
        # the test that checks it stops meaning anything.
        self.assertEqual(Config().host, "0.0.0.0")

    def test_binding_every_interface_says_there_is_no_login(self):
        lines = run.banner(Config(host="0.0.0.0", port=8790))
        text = "\n".join(lines)
        self.assertIn("no login", text)
        self.assertIn("quota", text)
        self.assertIn("COS_HOST=127.0.0.1", text)

    def test_a_named_interface_gets_the_same_warning(self):
        # The check is "not loopback", not "is 0.0.0.0" -- someone binding one real
        # interface is exposed the same way and must be told the same thing.
        text = "\n".join(run.banner(Config(host="192.168.1.10", port=8790)))
        self.assertIn("no login", text)

    def test_loopback_is_not_warned_about(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                text = "\n".join(run.banner(Config(host=host, port=8790)))
                self.assertNotIn("no login", text)

    def test_the_address_is_always_the_first_line(self):
        first = run.banner(Config(host="0.0.0.0", port=9001))[0]
        self.assertEqual(first, "coscc on http://0.0.0.0:9001")


if __name__ == "__main__":
    unittest.main()
