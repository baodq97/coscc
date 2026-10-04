"""Codegraph's blocking half: installing the library, the Node bridge, and the text the
agent reads.

No `Ctx` here: everything runs in a thread the feature starts, and takes its
commands as arguments so a test hands in fakes.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TypedDict


Run = Callable[..., subprocess.CompletedProcess[str]]

VERSION = "1.6.1"
PACKAGE = "@colbymchenry/codegraph"
# The lowest Node that ran index and sync in the spike, and the library's own upper bound.
NODE_MIN = (22, 16, 0)
NODE_BELOW = (25, 0, 0)
# Seconds npm may take, and a version probe. Chosen, not measured.
NPM_TIMEOUT = 600
PROBE_TIMEOUT = 30
# The reason shows the end of npm's output: the error is there, the progress is not.
TAIL_CHARS = 300
BRIDGE_ENV = {"CODEGRAPH_NO_DAEMON": "1", "CODEGRAPH_TELEMETRY": "0", "DO_NOT_TRACK": "1"}
# These two build the index, under the engine's baseline compiler alone, as the spike ran them.
WRITE_OPS = ("index", "sync")
ARCH = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}

MANIFEST = {"name": "coscc-codegraph", "private": True, "dependencies": {PACKAGE: VERSION}}
PACKAGE_JSON = json.dumps(MANIFEST, indent=2) + "\n"

PACKAGE_LOCK = """{
  "name": "coscc-codegraph",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "coscc-codegraph",
      "dependencies": {
        "@colbymchenry/codegraph": "1.6.1"
      }
    },
    "node_modules/@colbymchenry/codegraph": {
      "version": "1.6.1",
      "resolved": "https://registry.npmjs.org/@colbymchenry/codegraph/-/codegraph-1.6.1.tgz",
      "integrity": "sha512-kdyJ7Y9Q7oAMioThrNlRvm/23JRMMTlfydqsUx07eYK6EZIKlOLPgX9yA/h0TGaorQ6sWkrvzqocpoY7DgFUQw==",
      "license": "MIT",
      "bin": {
        "codegraph": "npm-shim.js"
      },
      "optionalDependencies": {
        "@colbymchenry/codegraph-darwin-arm64": "1.6.1",
        "@colbymchenry/codegraph-darwin-x64": "1.6.1",
        "@colbymchenry/codegraph-linux-arm64": "1.6.1",
        "@colbymchenry/codegraph-linux-x64": "1.6.1",
        "@colbymchenry/codegraph-win32-arm64": "1.6.1",
        "@colbymchenry/codegraph-win32-x64": "1.6.1"
      }
    },
    "node_modules/@colbymchenry/codegraph-darwin-arm64": {
      "version": "1.6.1",
      "resolved": "https://registry.npmjs.org/@colbymchenry/codegraph-darwin-arm64/-/codegraph-darwin-arm64-1.6.1.tgz",
      "integrity": "sha512-PCyaJg40RDjuZIH42IaMZfpSFC8Of8INzGCQI0CvIL2l5JD+89oBzTmeeqoJCJPCVYEmUg0ZX2RWdH6nTbM7kA==",
      "cpu": [
        "arm64"
      ],
      "license": "MIT",
      "optional": true,
      "os": [
        "darwin"
      ]
    },
    "node_modules/@colbymchenry/codegraph-darwin-x64": {
      "version": "1.6.1",
      "resolved": "https://registry.npmjs.org/@colbymchenry/codegraph-darwin-x64/-/codegraph-darwin-x64-1.6.1.tgz",
      "integrity": "sha512-G+WMLhf67ocEKB9WF4W2DGOF/jkRfVDAgXMCvMCFvB9hI+A0lpZ2rUVSm58FQDq8NfX+V7KnTt17uymRlQbiOw==",
      "cpu": [
        "x64"
      ],
      "license": "MIT",
      "optional": true,
      "os": [
        "darwin"
      ]
    },
    "node_modules/@colbymchenry/codegraph-linux-arm64": {
      "version": "1.6.1",
      "resolved": "https://registry.npmjs.org/@colbymchenry/codegraph-linux-arm64/-/codegraph-linux-arm64-1.6.1.tgz",
      "integrity": "sha512-6/SAflhcucR+H3V7FlUKcCRxl4VzRkK9D+Z9GjWHw8E3Bs03Ssbfm6Wps7xR+EQ8h4UO4DMeXsPAsLWn4IbeTQ==",
      "cpu": [
        "arm64"
      ],
      "license": "MIT",
      "optional": true,
      "os": [
        "linux"
      ]
    },
    "node_modules/@colbymchenry/codegraph-linux-x64": {
      "version": "1.6.1",
      "resolved": "https://registry.npmjs.org/@colbymchenry/codegraph-linux-x64/-/codegraph-linux-x64-1.6.1.tgz",
      "integrity": "sha512-ekkXHFI71jtFzKxJiWVhu+St7S+lh4B9KZdMFzIyBeqyOwtTqdum/ivUQyJo7zwWAuf7VFykVBQairJLKieDQg==",
      "cpu": [
        "x64"
      ],
      "license": "MIT",
      "optional": true,
      "os": [
        "linux"
      ]
    },
    "node_modules/@colbymchenry/codegraph-win32-arm64": {
      "version": "1.6.1",
      "resolved": "https://registry.npmjs.org/@colbymchenry/codegraph-win32-arm64/-/codegraph-win32-arm64-1.6.1.tgz",
      "integrity": "sha512-FuGOkKtd3EjqHaW/W27hf8oXp+psHVQkzWj0iqzUPjn+VNfvv4t00an60UVtBKv6OlozK2QKbCdED4k7WZ7FhQ==",
      "cpu": [
        "arm64"
      ],
      "license": "MIT",
      "optional": true,
      "os": [
        "win32"
      ]
    },
    "node_modules/@colbymchenry/codegraph-win32-x64": {
      "version": "1.6.1",
      "resolved": "https://registry.npmjs.org/@colbymchenry/codegraph-win32-x64/-/codegraph-win32-x64-1.6.1.tgz",
      "integrity": "sha512-qUcpAR32HgKMcrkZAOAXwY6vCV9TztF1YE61/SGC6TRNqPkJGHMD22yNe2VmrCmSmPNf0/aQIWS/BKGJ3ZFDPA==",
      "cpu": [
        "x64"
      ],
      "license": "MIT",
      "optional": true,
      "os": [
        "win32"
      ]
    }
  }
}
"""

# One request in, one reply line out. It filters nothing (the caller judges what is fuzzy) and a
# query opens the index read-only.
BRIDGE_JS = r"""import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

const node = (n) => ({
  id: n.id,
  name: n.name,
  kind: n.kind,
  file: n.filePath,
  start_line: n.startLine,
  end_line: n.endLine,
  signature: n.signature ?? "",
});

const numbers = (o) =>
  Object.fromEntries(Object.entries(o).filter(([, v]) => typeof v === "number"));

const edgeInfo = (e) => ({ line: e.line ?? null, resolution: e.metadata?.resolvedBy ?? "" });

const ops = {
  index: async (cg) => {
    await cg.indexAll();
    const stats = cg.getStats();
    return { files: stats.fileCount, nodes: stats.nodeCount };
  },
  sync: async (cg) => numbers(await cg.sync()),
  find: (cg, a) => cg.searchNodes(a.query, { limit: a.limit }).map((r) => node(r.node)),
  outline: (cg, a) => cg.getNodesInFiles(a.paths).map(node),
  callers: (cg, a) => {
    const targets = new Map(cg.getNodesByName(a.name).map((t) => [t.id, t]));
    const edges = cg.getIncomingEdgesTo([...targets.keys()], ["calls"]);
    const callers = cg.getNodesByIds([...new Set(edges.map((e) => e.source))]);
    return edges
      .filter((e) => callers.has(e.source) && targets.has(e.target))
      .map((e) => ({
        target: node(targets.get(e.target)),
        caller: node(callers.get(e.source)),
        ...edgeInfo(e),
      }));
  },
  impact: (cg, a) => {
    // An import edge points at the symbol imported, not at its file: ask for every node in it.
    const ids = cg.getNodesInFiles([a.path]).map((n) => n.id);
    const edges = cg.getIncomingEdgesTo(ids, ["imports"]);
    const sources = cg.getNodesByIds([...new Set(edges.map((e) => e.source))]);
    return edges
      .filter((e) => sources.has(e.source))
      .map((e) => ({ file: sources.get(e.source).filePath, ...edgeInfo(e) }));
  },
};

async function main() {
  let text = "";
  for await (const chunk of process.stdin) text += chunk;
  const { op, root, args } = JSON.parse(text);
  if (!Object.hasOwn(ops, op)) throw new Error(`unknown op: ${op}`);
  const { CodeGraph } = require("@colbymchenry/codegraph");
  const write = op === "index" || op === "sync";
  const cg =
    op === "index" && !CodeGraph.isInitialized(root)
      ? await CodeGraph.init(root)
      : await CodeGraph.open(root, write ? undefined : { readOnly: true });
  try {
    return await ops[op](cg, args ?? {});
  } finally {
    cg.close();
  }
}

let reply;
let code = 0;
try {
  reply = { ok: true, result: await main() };
} catch (e) {
  reply = { ok: false, error: String(e?.message ?? e) };
  code = 1;
}
process.stdout.write(JSON.stringify(reply) + "\n", () => process.exit(code));
"""


class BridgeError(RuntimeError):
    """The bridge did not give a result: it timed out, died, or said why it could not."""


SHA = re.compile(r"\b[0-9a-f]{40}\b")


def failure_sentence(error: BaseException, *hide: str) -> str:
    """One line of what went wrong, with no path and no full SHA: it is shown to a person."""
    text = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    for secret in filter(None, hide):
        text = text.replace(secret, "the workspace")
    text = SHA.sub("main", text)[:200].rstrip(" .")
    return f"The code index could not be brought up to date: {text}."


def ago(at: str) -> str:
    """How long since `at`, an ISO time, for a person."""
    try:
        then = datetime.fromisoformat(at)
    except ValueError:
        return "a while ago"
    s = max(0.0, (datetime.now(timezone.utc) - then).total_seconds())
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    return then.strftime("%b %-d")


def binary_path(home: Path) -> Path:
    """Where the library's own Node binary lands on this machine."""
    machine = platform.machine().lower()
    arch = ARCH.get(machine, machine)
    name = "node.exe" if sys.platform == "win32" else "node"
    return home / "node_modules" / f"{PACKAGE}-{sys.platform}-{arch}" / name


def installed(home: Path, *, run: Run = subprocess.run) -> Path | str:
    """The checked Node binary of the install in `home`, or the one sentence saying why not."""
    try:
        lock_ok = (home / "package-lock.json").read_text() == PACKAGE_LOCK
        pinned = json.loads((home / "node_modules" / PACKAGE / "package.json").read_text())
    except OSError, ValueError:
        return "The codegraph library is not installed."
    if not lock_ok or not isinstance(pinned, dict) or pinned.get("version") != VERSION:
        return f"The installed codegraph is not the pinned version {VERSION}."
    binary = binary_path(home)
    if not binary.is_file():
        return "The codegraph install has no Node binary for this machine."
    try:
        done = run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=PROBE_TIMEOUT
        )
    except OSError, subprocess.SubprocessError:
        done = None
    if done is None or done.returncode != 0:
        return "The Node binary that came with codegraph does not run on this machine."
    found = tuple(map(int, re.findall(r"\d+", done.stdout)[:3]))
    if not NODE_MIN <= found < NODE_BELOW:
        shown = ".".join(map(str, found)) if found else "an unknown version"
        want = f"{'.'.join(map(str, NODE_MIN))} up to {NODE_BELOW[0]}"
        return f"The Node that came with codegraph is {shown}; codegraph needs {want}."
    return binary


def install(
    home: Path, *, which: Callable[[str], str | None] = shutil.which, run: Run = subprocess.run
) -> Path | str:
    """Install the pinned library into `home`; the checked Node binary or why it cannot be."""
    if which("npm") is None:
        return "npm is not installed, so codegraph cannot be installed."
    home.mkdir(parents=True, exist_ok=True)
    (home / "package.json").write_text(PACKAGE_JSON)
    (home / "package-lock.json").write_text(PACKAGE_LOCK)
    (home / "bridge.mjs").write_text(BRIDGE_JS)
    argv = ["npm", "ci", "--prefix", str(home), "--ignore-scripts", "--no-audit", "--no-fund"]
    try:
        done = run(argv, capture_output=True, text=True, timeout=NPM_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"npm could not run: {str(exc)[-TAIL_CHARS:]}"
    if done.returncode != 0:
        tail = f"{done.stdout or ''}{done.stderr or ''}".strip()[-TAIL_CHARS:]
        return f"npm could not install codegraph: {tail}"
    return installed(home, run=run)


def call(
    home: Path, binary: Path, op: str, root: str, args: Mapping[str, object], timeout: float
) -> object:
    """Run one op in the bridge and return its result, or raise `BridgeError`."""
    argv = [str(binary), *(["--liftoff-only"] if op in WRITE_OPS else []), str(home / "bridge.mjs")]
    request = json.dumps({"op": op, "root": root, "args": dict(args)})
    pipe = subprocess.PIPE
    env = {**os.environ, **BRIDGE_ENV}
    with subprocess.Popen(
        argv, stdin=pipe, stdout=pipe, stderr=pipe, text=True, env=env, start_new_session=True
    ) as proc:
        try:
            out, err = proc.communicate(request, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise BridgeError(f"codegraph {op} took longer than {timeout:g} seconds.") from None
        finally:
            # A library worker must not outlive its call: the whole process group goes.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
    try:
        reply = json.loads(out.strip().splitlines()[-1])
    except ValueError, IndexError:
        reply = None
    if isinstance(reply, dict) and reply.get("ok") is False:
        raise BridgeError(f"codegraph {op} failed: {reply.get('error', 'no reason given')}")
    if proc.returncode != 0:
        tail = err.strip()[-TAIL_CHARS:]
        raise BridgeError(f"codegraph {op} exited with {proc.returncode}: {tail}")
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise BridgeError(f"codegraph {op} gave a reply that is not JSON.")
    return reply.get("result")


# Characters a tool result and a map may take. Chosen, not measured: a tool result is read once
# in a turn, a map sits in the prompt of every run.
TOOL_BUDGET = 4000
MAP_BUDGET = 6000
# How many matches a name search asks the index for, and how many symbols a map looks callers up
# for (one bridge call each). Chosen, not measured.
FIND_LIMIT = 20
MAX_SYMBOLS = 30
MAX_FILES = 50
# Callers listed under one symbol of a map before "and N more".
CALLERS_PER_SYMBOL = 5
# A signature longer than this is cut: it is a pointer, the code is for Read.
SIGNATURE_CHARS = 160

CHANGED = "changed in this unit: Read for current lines"
# What marks a query as a path rather than a name: a slash, or one of these endings.
SOURCE_SUFFIXES = (
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt",
    ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".scala", ".lua", ".sh",
)  # fmt: skip

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_TEST_BASENAME = re.compile(r"^(test_.*|.*_test\..*|.*\.test\..*|.*\.spec\..*)$")


class Node(TypedDict):
    id: str
    name: str
    kind: str
    file: str
    start_line: int
    end_line: int
    signature: str


class Call(TypedDict):
    target: Node
    caller: Node
    line: int | None


Ask = Callable[[str, Mapping[str, object]], object]
Hunks = Mapping[str, Sequence[tuple[int, int]]]


class GitError(Exception):
    """git did not answer: not started, or it refused (an unknown commit, not a repository)."""


def _git(run: Run, tree: str, *args: str) -> str:
    argv = ["git", "-C", tree, *args]
    try:
        done = run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
        )
    except OSError as err:
        raise GitError(f"git could not start: {err}") from err
    if done.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def changed_files(tree: str, sha: str, run: Run = subprocess.run) -> set[str]:
    """Paths the unit differs from `sha` in: tracked ones, committed or not, and untracked ones.

    Renames are listed as a delete and an add so the old path, which the index knows, is there.
    """
    tracked = _git(run, tree, "diff", "--name-only", "--no-renames", "-z", sha)
    untracked = _git(run, tree, "ls-files", "-o", "--exclude-standard", "-z")
    return {p for p in (tracked + "\0" + untracked).split("\0") if p}


def old_hunks(tree: str, sha: str, run: Run = subprocess.run) -> dict[str, list[tuple[int, int]]]:
    """Per old-side path, the line ranges of `sha` that the unit's work touches.

    A pure insertion has no old lines: the line it follows stands for it, so the symbol around the
    insertion point counts. A file that did not exist on `sha` has no old side and is left out.
    """
    text = _git(
        run, tree, "-c", "core.quotepath=off", "diff", "-U0", "--no-renames", "--no-color", sha
    )
    out: dict[str, list[tuple[int, int]]] = {}
    path: str | None = None
    left_old = left_new = 0
    # Split on "\n" only: str.splitlines would also break inside a line of code.
    for line in text.split("\n"):
        if left_old > 0 or left_new > 0:
            # Inside a hunk a removed line may read `--- x`: count lines, do not trust prefixes.
            if line.startswith("-"):
                left_old -= 1
            elif line.startswith("+"):
                left_new -= 1
            elif line.startswith(" "):
                left_old -= 1
                left_new -= 1
            continue
        if line.startswith("--- "):
            old = line[4:].split("\t")[0]
            path = None if old == "/dev/null" else old.removeprefix("a/")
            continue
        m = _HUNK.match(line)
        if m is None:
            continue
        start = int(m.group(1))
        count = 1 if m.group(2) is None else int(m.group(2))
        left_old = count
        left_new = 1 if m.group(4) is None else int(m.group(4))
        if path is not None:
            out.setdefault(path, []).append((start, start if count == 0 else start + count - 1))
    return out


def _int(raw: object) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _node(raw: object) -> Node | None:
    """A symbol of the index; a file-level node, an import statement (it spends the budget on
    what the file's head already says) and anything malformed is None."""
    if not isinstance(raw, Mapping):
        return None
    name, kind, file = raw.get("name"), raw.get("kind"), raw.get("file")
    start, end, sig = _int(raw.get("start_line")), _int(raw.get("end_line")), raw.get("signature")
    if not (isinstance(name, str) and isinstance(kind, str) and isinstance(file, str)):
        return None
    if kind in ("file", "import") or start is None or end is None:
        return None
    return Node(
        id=str(raw.get("id", "")),
        name=name,
        kind=kind,
        file=file,
        start_line=start,
        end_line=end,
        signature=sig if isinstance(sig, str) else "",
    )


def _list(ask: Ask, op: str, args: Mapping[str, object]) -> list[object]:
    got = ask(op, args)
    return got if isinstance(got, list) else []


def _symbols(raw: list[object]) -> list[Node]:
    """The symbols of a `find` or `outline` answer; the malformed and file-level ones are gone."""
    return [n for item in raw if (n := _node(item)) is not None]


def _calls(raw: list[object]) -> list[Call]:
    """The sure call edges of a `callers` answer: no guessed edge, no file-level end."""
    out: list[Call] = []
    for item in raw:
        if not isinstance(item, Mapping) or not isinstance(item.get("resolution"), str):
            continue
        target, caller = _node(item.get("target")), _node(item.get("caller"))
        if item["resolution"] != "fuzzy" and target is not None and caller is not None:
            out.append(Call(target=target, caller=caller, line=_int(item.get("line"))))
    return out


def _imports(raw: list[object]) -> dict[str, int | None]:
    """The sure importers of an `impact` answer: file to line, each file once."""
    out: dict[str, int | None] = {}
    for item in raw:
        if not isinstance(item, Mapping) or not isinstance(item.get("resolution"), str):
            continue
        file = item.get("file")
        if item["resolution"] != "fuzzy" and isinstance(file, str):
            out.setdefault(file, _int(item.get("line")))
    return out


def _callers_of(ask: Ask, node: Node) -> list[Call]:
    """The sure callers of this very symbol: a name is shared, so edges to others are dropped."""
    edges = _calls(_list(ask, "callers", {"name": node["name"]}))
    return [
        e
        for e in edges
        if e["target"]["file"] == node["file"] and e["target"]["name"] == node["name"]
    ]


def _names_path(query: str) -> bool:
    """A query that is a path, not a name: no blanks, and a slash or a source ending."""
    return not any(c.isspace() for c in query) and ("/" in query or query.endswith(SOURCE_SUFFIXES))


def _inside(tree: str, raw: str) -> str | None:
    """`raw` as a path relative to `tree`, or None when it leads out (`..`, absolute, a symlink)."""
    try:
        root = Path(tree).resolve()
        rel = (root / raw).resolve().relative_to(root)  # an absolute `raw` replaces root
    except OSError, ValueError:
        return None
    return rel.as_posix()


def _is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return (
        any(d in ("tests", "test") for d in p.parts[:-1])
        or _TEST_BASENAME.match(p.name) is not None
    )


def _head(sha: str) -> str:
    return f"Index of main at {sha[:12]}. Line numbers are main's, not this unit's worktree."


def _more(count: int) -> str:
    return f"… {count} more not shown."


def _file_line(file: str, changed: set[str]) -> str:
    return f"{file} ({CHANGED})" if file in changed else file


def _symbol_line(node: Node) -> str:
    sig = " ".join(node["signature"].split())
    sig = sig if len(sig) <= SIGNATURE_CHARS else sig[: SIGNATURE_CHARS - 1] + "…"
    tail = f": {sig}" if sig and sig != node["name"] else ""
    return f"  {node['kind']} {node['name']} L{node['start_line']}-{node['end_line']}{tail}"


def _call_line(call: Call, changed: set[str]) -> str:
    c = call["caller"]
    where = f"{_file_line(c['file'], changed)}: in {c['name']} ({c['kind']}) L{c['start_line']}-{c['end_line']}"
    at = f", call at line {call['line']}" if call["line"] is not None else ""
    return where + at


def _caller_lines(edges: list[Call], changed: set[str]) -> list[str]:
    lines = ["    " + _call_line(e, changed) for e in edges[:CALLERS_PER_SYMBOL]]
    if len(edges) > CALLERS_PER_SYMBOL:
        lines.append(f"    … and {len(edges) - CALLERS_PER_SYMBOL} more callers")
    return lines


def _grouped(
    nodes: list[Node], changed: set[str], detail: Callable[[Node], list[str]]
) -> list[str]:
    """One item per symbol, files in the order they first appear; a file's first item carries its name."""
    by_file: dict[str, list[Node]] = {}
    for node in nodes:
        by_file.setdefault(node["file"], []).append(node)
    items: list[str] = []
    for file, group in by_file.items():
        for i, node in enumerate(sorted(group, key=lambda n: (n["start_line"], n["end_line"]))):
            lines = [_file_line(file, changed)] if i == 0 else []
            lines.append(_symbol_line(node))
            lines.extend(detail(node))
            items.append("\n".join(lines))
    return items


def _cut(head: str, items: list[str], budget: int) -> str:
    """`head` and as many whole items as fit, then how many did not; the total is within `budget`."""
    text = head
    for i, item in enumerate(items):
        after = len(items) - i - 1
        # Room for the "more" line is kept back for as long as something would be left out.
        keep = len(_more(after)) + 1 if after else 0
        if len(text) + 1 + len(item) + keep > budget:
            return f"{text}\n{_more(len(items) - i)}"
        text += "\n" + item
    return text


def _tool(sha: str, items: list[str], empty: str) -> str:
    return _cut(_head(sha), items, TOOL_BUDGET) if items else f"{_head(sha)}\n{empty}"


def _map(sha: str, items: list[str]) -> str:
    return _cut(_head(sha), items, MAP_BUDGET) if items else ""


def find(ask: Ask, sha: str, changed: set[str], tree: str, query: str) -> str:
    """Entry points for a name, or the outline of one file when the query names a path."""
    q = query.strip()
    if _names_path(q):
        rel = _inside(tree, q)
        if rel is None:
            return f"{_head(sha)}\nRefused: {q} is outside the repository."
        nodes = _symbols(_list(ask, "outline", {"paths": [rel]}))
        empty = f"No symbols indexed for {rel} on main (a file the unit added is not there)."
    else:
        nodes = _symbols(_list(ask, "find", {"query": q, "limit": FIND_LIMIT}))
        empty = f"No symbol on main matches {q!r}."
    return _tool(sha, _grouped(nodes, changed, lambda _: []), empty)


def callers(ask: Ask, sha: str, changed: set[str], symbol: str) -> str:
    """The direct call sites of a symbol name: the file and the symbol each call sits in."""
    edges = _calls(_list(ask, "callers", {"name": symbol.strip()}))
    items = [_call_line(e, changed) for e in edges]
    return _tool(sha, items, f"No direct caller of {symbol.strip()!r} on main.")


def impact(ask: Ask, sha: str, changed: set[str], tree: str, path: str) -> str:
    """The files that import `path` directly, tests apart. Dependents of dependents are not followed."""
    rel = _inside(tree, path.strip())
    if rel is None:
        return f"{_head(sha)}\nRefused: {path.strip()} is outside the repository."
    # The engine lists the file's own imports among its importers.
    found = {f: n for f, n in _imports(_list(ask, "impact", {"path": rel})).items() if f != rel}
    sections = (
        ("Imported directly by:", {f: n for f, n in found.items() if not _is_test(f)}),
        ("Tests importing it:", {f: n for f, n in found.items() if _is_test(f)}),
    )
    items: list[str] = []
    for title, group in sections:
        for i, (file, line) in enumerate(group.items()):
            row = f"  {_file_line(file, changed)}" + (f" (line {line})" if line is not None else "")
            items.append(f"{title}\n{row}" if i == 0 else row)
    return _tool(sha, items, f"No file on main imports {rel} directly.")


def _clean(files: list[str]) -> list[str]:
    """Repo-relative paths only, each once: the index knows no other kind."""
    out: dict[str, None] = {}
    for raw in files:
        p = PurePosixPath(raw.strip().removeprefix("./"))
        if raw.strip() and not p.is_absolute() and ".." not in p.parts:
            out[p.as_posix()] = None
    return list(out)[:MAX_FILES]


def impl_map(ask: Ask, sha: str, changed: set[str], files: list[str]) -> str:
    """For the files a plan names: what is in each, and who calls it. "" when the index has nothing."""
    paths = _clean(files)
    if not paths:
        return ""
    nodes = _symbols(_list(ask, "outline", {"paths": paths}))
    # One bridge call per symbol: only the first few dozen are asked about.
    asked = {id(n) for n in nodes[:MAX_SYMBOLS]}

    def detail(node: Node) -> list[str]:
        return _caller_lines(_callers_of(ask, node), changed) if id(node) in asked else []

    return _map(sha, _grouped(nodes, changed, detail))


def review_map(ask: Ask, sha: str, changed: set[str], hunks: Hunks) -> str:
    """Symbols the diff touches that something outside the unit's files calls: the sites a review misses.

    A touched symbol nobody outside calls is left out: the diff already shows it.
    """
    paths = _clean([p for p, ranges in hunks.items() if ranges])
    if not paths:
        return ""
    nodes = [
        n
        for n in _symbols(_list(ask, "outline", {"paths": paths}))
        if any(a <= n["end_line"] and b >= n["start_line"] for a, b in hunks.get(n["file"], ()))
    ]
    kept: list[Node] = []
    outside: dict[int, list[Call]] = {}
    for node in nodes[:MAX_SYMBOLS]:
        edges = [e for e in _callers_of(ask, node) if e["caller"]["file"] not in changed]
        if edges:
            kept.append(node)
            outside[id(node)] = edges

    return _map(sha, _grouped(kept, changed, lambda n: _caller_lines(outside[id(n)], changed)))
