"""Tests for locating a bundle and writing its address. None of these run a real build.

The fixture is the real chunk, copied byte for byte from a build made 2026-09-22 and then
given a different hash in its filename -- because the thing most likely to break this
module is a change in what Reflex emits, and a hand-simplified fixture would keep passing
through exactly that change.

`test_scanning_only_the_plain_files_misses_a_stale_sidecar` is the one to read first. It
does not test `frontend.py` at all; it demonstrates the failure `plan.md` risk 1 names, so
that the scan which prevents it cannot be quietly weakened later.
"""

from __future__ import annotations

import gzip
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coscc import frontend

# Copied from `.web/build/client/assets/reflex-env-C1wtcuvo.js`, 2026-09-22.
CHUNK = (
    "var e={PING:`http://127.0.0.1:8790/ping`,EVENT:`ws://127.0.0.1:8790/_event`,"
    "UPLOAD:`http://127.0.0.1:8790/_upload`,"
    "AUTH_CODESPACE:`http://127.0.0.1:8790/auth-codespace`,"
    "HEALTH:`http://127.0.0.1:8790/_health`,"
    "ALL_ROUTES:`http://127.0.0.1:8790/_all_routes`,"
    "TRANSPORT:`websocket`,TEST_MODE:!1};export{e as t};"
)


def _bundle(root: Path, *, sidecar: bool = True) -> Path:
    """A bundle shaped like the real one: index, an env chunk, and its `.gz` sidecar."""
    static = root / "build" / "client"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html></html>")
    chunk = static / "assets" / "reflex-env-Tst00000.js"
    chunk.write_text(CHUNK)
    if sidecar:
        chunk.with_name(chunk.name + ".gz").write_bytes(gzip.compress(CHUNK.encode()))
    return static


class WritingTheAddress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.static = _bundle(Path(self.tmp.name) / "_web")
        self.chunk = self.static / "assets" / "reflex-env-Tst00000.js"
        self.addCleanup(self.tmp.cleanup)

    def test_it_writes_both_the_chunk_and_its_sidecar(self):
        self.assertEqual(frontend.rewrite_address(self.static, "0.0.0.0", 9001), 2)

    def test_the_new_address_replaces_every_occurrence(self):
        frontend.rewrite_address(self.static, "0.0.0.0", 9001)
        self.assertEqual(
            frontend.addresses(self.static),
            {"http://0.0.0.0:9001", "ws://0.0.0.0:9001"},
        )

    def test_the_websocket_scheme_survives(self):
        # Turning `ws://` into `http://` would leave every HTTP route healthy and kill
        # only the event socket -- the failure no HTTP check can see.
        frontend.rewrite_address(self.static, "example.internal", 80)
        self.assertIn("ws://example.internal:80/_event", self.chunk.read_text())

    def test_the_sidecar_is_rebuilt_rather_than_left_behind(self):
        frontend.rewrite_address(self.static, "0.0.0.0", 9001)
        sidecar = self.chunk.with_name(self.chunk.name + ".gz")
        served = gzip.decompress(sidecar.read_bytes()).decode()
        self.assertNotIn("127.0.0.1:8790", served)
        self.assertEqual(served, self.chunk.read_text())

    def test_scanning_only_the_plain_files_misses_a_stale_sidecar(self):
        # Not a test of `frontend.py`. It pins the reason `addresses()` decompresses:
        # rewrite the chunk, leave the sidecar alone, and a plain-file scan reports a
        # clean bundle while every browser is still served the old address.
        self.chunk.write_text(CHUNK.replace("127.0.0.1:8790", "0.0.0.0:9001"))
        plain = {
            m.group(0)
            for path in self.static.rglob("*.js")
            for m in frontend._AUTHORITY.finditer(path.read_text())
        }
        self.assertEqual(plain, {"http://0.0.0.0:9001", "ws://0.0.0.0:9001"})
        self.assertIn("http://127.0.0.1:8790", frontend.addresses(self.static))

    def test_a_bundle_with_no_sidecar_writes_one_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        static = _bundle(Path(tmp.name) / "_web", sidecar=False)
        self.assertEqual(frontend.rewrite_address(static, "0.0.0.0", 9001), 1)


class WhenTheChunkIsNotWhereWeLook(unittest.TestCase):
    def test_it_raises_rather_than_serving_the_wrong_address(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        static = Path(tmp.name) / "build" / "client"
        (static / "assets").mkdir(parents=True)
        (static / "index.html").write_text("<html></html>")
        (static / "assets" / "reflex-something-else.js").write_text(CHUNK)
        with self.assertRaises(frontend.NoEnvChunk) as caught:
            frontend.rewrite_address(static, "0.0.0.0", 9001)
        # The message has to name the pattern; a future Reflex renaming this chunk is the
        # expected cause, and the reader needs to know what was looked for.
        self.assertIn("reflex-env-", str(caught.exception))


class WhichBundleIsUsed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_a_checkout_uses_dot_web(self):
        with mock.patch.object(frontend, "PACKAGE_WEB", self.root / "absent"):
            self.assertFalse(frontend.is_packaged())
            self.assertEqual(frontend.web_dir(self.root), self.root / ".web")

    def test_a_packaged_install_uses_its_own_copy(self):
        packaged = self.root / "_web"
        _bundle(packaged)
        with mock.patch.object(frontend, "PACKAGE_WEB", packaged):
            self.assertTrue(frontend.is_packaged())
            self.assertEqual(frontend.web_dir(self.root), packaged)
            self.assertEqual(
                frontend.static_dir(self.root), packaged / "build" / "client"
            )


class TheCheckThatVerify0011Asserts(unittest.TestCase):
    """`addresses()` is too blunt to assert against; `event_addresses()` is exact."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.static = _bundle(Path(self.tmp.name) / "_web")
        self.addCleanup(self.tmp.cleanup)

    def test_a_rewritten_bundle_holds_exactly_one_socket_address(self):
        frontend.rewrite_address(self.static, "0.0.0.0", 9001)
        self.assertEqual(frontend.event_addresses(self.static), {"ws://0.0.0.0:9001"})

    def test_other_peoples_urls_do_not_disturb_it(self):
        # Measured on the real bundle 2026-09-22: eleven of the thirteen authorities it
        # carries belong to somebody else. None of them is a websocket.
        (self.static / "assets" / "vendor.js").write_text(
            "see https://react.dev and http://localhost:3000"
        )
        frontend.rewrite_address(self.static, "0.0.0.0", 9001)
        self.assertEqual(frontend.event_addresses(self.static), {"ws://0.0.0.0:9001"})


if __name__ == "__main__":
    unittest.main()
