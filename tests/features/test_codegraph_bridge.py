"""Tests for `call` and the bridge in `coscc/features/codegraph/install.py`.

Two halves. `call` is run against shell scripts standing in for the Node binary, to show what it
sends, what it kills and what it refuses. The bridge source is run for real under the `node` on
PATH, against a fake library in the home's `node_modules` and a preload that makes any network
use throw and be written down."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from coscc.features.codegraph.graph import BRIDGE_JS, BridgeError, call

NODE = shutil.which("node")
WAIT = 10.0


def alive(pid: int) -> bool:
    """Running; a killed process nobody has reaped yet (a zombie) is not."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except ProcessLookupError:
        # Reaped between the signal and the read.
        return False
    except FileNotFoundError:
        return not Path("/proc/self").exists()


def until(check, what: str) -> None:
    """Poll `check` up to WAIT seconds: the effect is waited for, never slept past."""
    end = time.monotonic() + WAIT
    while not check():
        if time.monotonic() > end:
            raise AssertionError(f"never happened: {what}")
        time.sleep(0.02)


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Work(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.home = self.dir / "home"
        self.home.mkdir()
        self.pids = self.dir / "pids"
        self.addCleanup(self.reap)

    def reap(self):
        """A test that fails must not leave its `sleep` behind."""
        if self.pids.exists():
            for line in self.pids.read_text().split():
                try:
                    os.kill(int(line), 9)
                except ProcessLookupError:
                    pass


class AFakeBinary(Work):
    def fake(self, reply: str, tail: str = "") -> Path:
        return script(
            self.dir / "node",
            f"""cat > "{self.dir}/stdin"
echo "$@" > "{self.dir}/argv"
echo "$CODEGRAPH_NO_DAEMON $CODEGRAPH_TELEMETRY $DO_NOT_TRACK" > "{self.dir}/env"
sleep 30 >/dev/null 2>&1 &
echo $! >> "{self.pids}"
echo '{reply}'
{tail}
""",
        )

    def test_call_returns_the_result_and_no_child_outlives_it(self):
        binary = self.fake('{"ok": true, "result": {"files": 2}}')
        got = call(self.home, binary, "find", "/repo", {"query": "q", "limit": 5}, 10)
        self.assertEqual(got, {"files": 2})
        pid = int(self.pids.read_text().split()[0])
        until(lambda: not alive(pid), "the grandchild is killed with the call")

    def test_it_sends_one_json_request_on_stdin(self):
        binary = self.fake('{"ok": true, "result": null}')
        call(self.home, binary, "find", "/repo", {"query": "q", "limit": 5}, 10)
        self.assertEqual(
            json.loads((self.dir / "stdin").read_text()),
            {"op": "find", "root": "/repo", "args": {"query": "q", "limit": 5}},
        )

    def test_the_bridge_runs_by_its_path_in_the_home(self):
        binary = self.fake('{"ok": true, "result": null}')
        call(self.home, binary, "find", "/repo", {}, 10)
        self.assertEqual((self.dir / "argv").read_text().split(), [str(self.home / "bridge.mjs")])

    def test_writing_ops_get_the_baseline_compiler_flag_and_queries_do_not(self):
        binary = self.fake('{"ok": true, "result": null}')
        for op, flag in (("index", True), ("sync", True), ("find", False), ("impact", False)):
            with self.subTest(op=op):
                call(self.home, binary, op, "/repo", {}, 10)
                argv = (self.dir / "argv").read_text().split()
                self.assertEqual("--liftoff-only" in argv, flag)
                self.assertEqual(argv[-1], str(self.home / "bridge.mjs"))

    def test_the_library_is_told_not_to_start_a_daemon_nor_report(self):
        binary = self.fake('{"ok": true, "result": null}')
        call(self.home, binary, "find", "/repo", {}, 10)
        self.assertEqual((self.dir / "env").read_text().split(), ["1", "0", "1"])

    def test_a_call_that_runs_out_of_time_raises_and_leaves_nothing_alive(self):
        binary = script(
            self.dir / "node",
            f'sleep 30 &\necho $! >> "{self.pids}"\nsleep 30 &\necho $! >> "{self.pids}"\nwait\n',
        )
        started = time.monotonic()
        with self.assertRaises(BridgeError) as caught:
            call(self.home, binary, "index", "/repo", {}, 1.0)
        self.assertLess(time.monotonic() - started, 8)
        self.assertIn("1 seconds", str(caught.exception))
        until(
            lambda: self.pids.exists() and len(self.pids.read_text().split()) == 2,
            "children started",
        )
        for pid in map(int, self.pids.read_text().split()):
            until(lambda pid=pid: not alive(pid), f"child {pid} is killed")

    def test_a_reply_that_is_not_json_raises(self):
        binary = self.fake("this is not json")
        with self.assertRaises(BridgeError):
            call(self.home, binary, "find", "/repo", {}, 10)

    def test_no_reply_at_all_raises(self):
        binary = script(self.dir / "node", "cat >/dev/null\n")
        with self.assertRaises(BridgeError):
            call(self.home, binary, "find", "/repo", {}, 10)

    def test_a_reply_that_says_not_ok_raises_with_its_error(self):
        binary = self.fake('{"ok": false, "error": "no index here"}', "exit 1")
        with self.assertRaises(BridgeError) as caught:
            call(self.home, binary, "find", "/repo", {}, 10)
        self.assertIn("no index here", str(caught.exception))

    def test_a_non_zero_exit_raises_with_the_end_of_stderr(self):
        binary = script(self.dir / "node", "cat >/dev/null\necho 'it broke badly' >&2\nexit 7\n")
        with self.assertRaises(BridgeError) as caught:
            call(self.home, binary, "find", "/repo", {}, 10)
        self.assertIn("7", str(caught.exception))
        self.assertIn("it broke badly", str(caught.exception))

    def test_a_good_reply_with_a_bad_exit_still_raises(self):
        binary = self.fake('{"ok": true, "result": 1}', "exit 2")
        with self.assertRaises(BridgeError):
            call(self.home, binary, "find", "/repo", {}, 10)

    def test_a_binary_that_cannot_start_is_not_hidden(self):
        with self.assertRaises(OSError):
            call(self.home, self.dir / "missing", "find", "/repo", {}, 10)


FAKE_LIBRARY = r"""
const fs = require("node:fs");
const log = (entry) => fs.appendFileSync(process.env.CALLS, JSON.stringify(entry) + "\n");

const N1 = { id: "n1", name: "parse", kind: "function", filePath: "src/a.py", startLine: 3,
             endLine: 9, signature: "def parse()" };
const NO_SIG = { id: "n2", name: "Thing", kind: "class", filePath: "src/b.py", startLine: 1,
                 endLine: 2 };
const FILE = { id: "f1", name: "a.py", kind: "file", filePath: "src/a.py", startLine: 1,
               endLine: 40 };
const CALLER = { id: "c1", name: "run", kind: "function", filePath: "src/c.py", startLine: 10,
                 endLine: 20, signature: "def run()" };
const IMPORTER = { id: "s1", name: "c.py", kind: "file", filePath: "src/c.py", startLine: 1,
                   endLine: 30 };

class CodeGraph {
  static isInitialized(root) { return root.endsWith("inited"); }
  static async init(root) { log({ fn: "init", root }); return new CodeGraph(); }
  static async open(root, options) {
    log({ fn: "open", root, options: options ?? null });
    if (root === "boom") throw new Error("no index at boom");
    return new CodeGraph();
  }
  close() { log({ fn: "close" }); }
  async indexAll() { return { filesIndexed: 3 }; }
  getStats() { return { fileCount: 3, nodeCount: 9, edgeCount: 4, nodesByKind: { file: 3 } }; }
  async sync() {
    return { filesChecked: 2, filesAdded: 1, changedFilePaths: ["x"], durationMs: 5 };
  }
  searchNodes(query, options) {
    log({ fn: "searchNodes", query, options });
    return [{ node: N1, score: 1 }, { node: NO_SIG, score: 0.5 }];
  }
  getNodesInFiles(paths) { log({ fn: "getNodesInFiles", paths }); return [FILE, N1]; }
  getNodesByName(name) { log({ fn: "getNodesByName", name }); return [N1]; }
  getIncomingEdgesTo(ids, kinds) {
    log({ fn: "getIncomingEdgesTo", ids, kinds });
    if (kinds[0] === "imports") {
      return [{ source: "s1", target: "f1", kind: "imports", line: 3,
                metadata: { resolvedBy: "import" } },
              { source: "gone", target: "f1", kind: "imports" }];
    }
    return [{ source: "c1", target: "n1", kind: "calls", line: 12,
              metadata: { resolvedBy: "fuzzy" } },
            { source: "c1", target: "n1", kind: "calls" },
            { source: "gone", target: "n1", kind: "calls", line: 1 }];
  }
  getNodesByIds(ids) {
    log({ fn: "getNodesByIds", ids });
    return new Map([CALLER, IMPORTER].filter((n) => ids.includes(n.id)).map((n) => [n.id, n]));
  }
  getFileNodes(paths) { log({ fn: "getFileNodes", paths }); return [FILE]; }
}

module.exports = { CodeGraph };
"""

# Replaces every way out to a network that the library or Node could use, with a function that
# writes down the attempt and throws.
NO_NETWORK = r"""
const fs = require("node:fs");
const net = require("node:net");
const dns = require("node:dns");
const http = require("node:http");
const https = require("node:https");

const trip = (what) => (...args) => {
  fs.appendFileSync(process.env.NETLOG, what + "\n");
  throw new Error("network use: " + what);
};

globalThis.fetch = trip("fetch");
net.connect = trip("net.connect");
net.createConnection = trip("net.createConnection");
net.Socket.prototype.connect = trip("net.Socket.connect");
dns.lookup = trip("dns.lookup");
dns.promises.lookup = trip("dns.promises.lookup");
http.request = trip("http.request");
http.get = trip("http.get");
https.request = trip("https.request");
https.get = trip("https.get");
"""

NODE_1 = {
    "id": "n1",
    "name": "parse",
    "kind": "function",
    "file": "src/a.py",
    "start_line": 3,
    "end_line": 9,
    "signature": "def parse()",
}
NODE_2 = {
    "id": "n2",
    "name": "Thing",
    "kind": "class",
    "file": "src/b.py",
    "start_line": 1,
    "end_line": 2,
    "signature": "",
}
FILE_NODE = {
    "id": "f1",
    "name": "a.py",
    "kind": "file",
    "file": "src/a.py",
    "start_line": 1,
    "end_line": 40,
    "signature": "",
}
CALLER_NODE = {
    "id": "c1",
    "name": "run",
    "kind": "function",
    "file": "src/c.py",
    "start_line": 10,
    "end_line": 20,
    "signature": "def run()",
}


class TheBridgeSource(Work):
    """Runs the bridge with the `node` on PATH, which `npm test` itself needs."""

    def setUp(self):
        super().setUp()
        lib = self.home / "node_modules" / "@colbymchenry" / "codegraph"
        lib.mkdir(parents=True)
        (lib / "package.json").write_text('{"name": "@colbymchenry/codegraph", "main": "index.js"}')
        (lib / "index.js").write_text(FAKE_LIBRARY)
        (self.home / "bridge.mjs").write_text(BRIDGE_JS)
        self.guard = self.dir / "no_network.cjs"
        self.guard.write_text(NO_NETWORK)
        self.calls = self.dir / "calls.jsonl"
        self.netlog = self.dir / "netlog"
        env = {
            "CALLS": str(self.calls),
            "NETLOG": str(self.netlog),
            "NODE_OPTIONS": f"--import={self.guard}",
        }
        patch = mock.patch.dict(os.environ, env)
        patch.start()
        self.addCleanup(patch.stop)

    def ask(self, op: str, args: dict[str, object] | None = None, root: str = "/repo") -> object:
        assert NODE
        return call(self.home, Path(NODE), op, root, args or {}, 60)

    def logged(self) -> list[dict]:
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def opened(self) -> list[dict]:
        return [c for c in self.logged() if c["fn"] in ("open", "init")]

    def test_the_guard_itself_records_and_throws(self):
        assert NODE
        proc = subprocess.run(
            [
                NODE,
                "-e",
                "fetch('http://example.invalid').then(()=>process.exit(0),()=>process.exit(3))",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.netlog.read_text().split(), ["fetch"])

    def test_find_gives_nodes_in_the_agreed_shape(self):
        got = self.ask("find", {"query": "pars", "limit": 7})
        self.assertEqual(got, [NODE_1, NODE_2])
        self.assertIn(
            {"fn": "searchNodes", "query": "pars", "options": {"limit": 7}}, self.logged()
        )

    def test_outline_passes_a_file_level_node_through(self):
        got = self.ask("outline", {"paths": ["src/a.py"]})
        self.assertEqual(got, [FILE_NODE, NODE_1])
        self.assertIn({"fn": "getNodesInFiles", "paths": ["src/a.py"]}, self.logged())

    def test_callers_pair_each_target_with_its_caller_and_keep_fuzzy_edges(self):
        got = self.ask("callers", {"name": "parse"})
        self.assertEqual(
            got,
            [
                {"target": NODE_1, "caller": CALLER_NODE, "line": 12, "resolution": "fuzzy"},
                {"target": NODE_1, "caller": CALLER_NODE, "line": None, "resolution": ""},
            ],
        )
        self.assertIn(
            {"fn": "getIncomingEdgesTo", "ids": ["n1"], "kinds": ["calls"]}, self.logged()
        )

    def test_impact_asks_for_imports_of_the_file_node_and_of_every_symbol_in_it(self):
        got = self.ask("impact", {"path": "src/a.py"})
        self.assertEqual(got, [{"file": "src/c.py", "line": 3, "resolution": "import"}])
        self.assertIn({"fn": "getNodesInFiles", "paths": ["src/a.py"]}, self.logged())
        self.assertIn(
            {"fn": "getIncomingEdgesTo", "ids": ["f1", "n1"], "kinds": ["imports"]}, self.logged()
        )

    def test_index_of_a_new_root_inits_it_for_writing_and_counts(self):
        self.assertEqual(self.ask("index", root="/repo/fresh"), {"files": 3, "nodes": 9})
        self.assertEqual(self.opened(), [{"fn": "init", "root": "/repo/fresh"}])

    def test_index_of_a_known_root_opens_it_for_writing(self):
        self.assertEqual(self.ask("index", root="/repo/inited"), {"files": 3, "nodes": 9})
        self.assertEqual(self.opened(), [{"fn": "open", "root": "/repo/inited", "options": None}])

    def test_sync_opens_for_writing_and_gives_only_numbers(self):
        got = self.ask("sync")
        self.assertEqual(got, {"filesChecked": 2, "filesAdded": 1, "durationMs": 5})
        self.assertEqual(self.opened(), [{"fn": "open", "root": "/repo", "options": None}])

    def test_every_query_opens_read_only(self):
        queries = {
            "find": {"query": "q", "limit": 1},
            "outline": {"paths": ["src/a.py"]},
            "callers": {"name": "parse"},
            "impact": {"path": "src/a.py"},
        }
        for op, args in queries.items():
            with self.subTest(op=op):
                self.calls.unlink(missing_ok=True)
                self.ask(op, args)
                self.assertEqual(
                    self.opened(), [{"fn": "open", "root": "/repo", "options": {"readOnly": True}}]
                )

    def test_every_op_closes_what_it_opened(self):
        for op in ("index", "sync", "find", "outline", "callers", "impact"):
            with self.subTest(op=op):
                self.calls.unlink(missing_ok=True)
                self.ask(op, {"query": "q", "limit": 1, "paths": [], "name": "n", "path": "p"})
                self.assertEqual([c["fn"] for c in self.logged()].count("close"), 1)

    def test_an_unknown_op_is_refused_before_anything_opens(self):
        with self.assertRaises(BridgeError) as caught:
            self.ask("drop")
        self.assertIn("unknown op", str(caught.exception))
        self.assertEqual(self.opened(), [])

    def test_a_library_error_comes_back_as_a_bridge_error(self):
        with self.assertRaises(BridgeError) as caught:
            self.ask("find", {"query": "q", "limit": 1}, root="boom")
        self.assertIn("no index at boom", str(caught.exception))

    def test_a_library_that_is_missing_comes_back_as_a_bridge_error(self):
        shutil.rmtree(self.home / "node_modules")
        with self.assertRaises(BridgeError):
            self.ask("find", {"query": "q", "limit": 1})

    def test_no_op_touches_the_network(self):
        for op in ("index", "sync", "find", "outline", "callers", "impact"):
            self.ask(op, {"query": "q", "limit": 1, "paths": ["p"], "name": "n", "path": "p"})
        self.assertFalse(
            self.netlog.exists(), self.netlog.read_text() if self.netlog.exists() else ""
        )


if __name__ == "__main__":
    unittest.main()
