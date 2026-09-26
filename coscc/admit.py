"""What the code decides about a gathered entry, after `knowledge.validate` and before the save.

`0108_the-knowledge-store-fills-with-stale-notes-about-old-code`. A session writes the
statement; everything that can be read instead of believed is read here: the date from the
run log (R1), a tool's version from the workspace's pins (R4), that a `Ref:` still exists on
`main` (R5), that no source says it was not run (R6), and the share of `tool:` entries (R7).
`admit` never fails a batch: what it cannot admit it drops, with a reason, into the batch's
`dropped` (R8). `check` is `coscc knowledge check` (R10), and reads the same way.

It does not import `coscc/gather.py`, so `gather` and `knowledge_cli` both can import it.
Everything but `_git` and what calls it is pure.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from coscc import knowledge, units
from coscc.gitops import child_env

# Chosen, not measured: each call reads one object or one tree of a local repository.
TIMEOUT = 60.0

STAGE_OF = {"spike.md": "spike", "review.md": "review"}

# R6. Compared after NFC and casefold; `build_prompt` copies them into the prompt (R9).
MARKERS = (
    "not run", "not measured", "not verified", "unverified", "derived",
    "không kiểm", "chưa kiểm", "không chạy", "chưa chạy", "không đo", "chưa đo",
)


class GitError(RuntimeError):
    """`git` could not be run, or did not finish. Not a command that answered no."""


def _git(path: str | os.PathLike[str] | None, *args: str) -> str | None:
    """stdout, or `None` when git ran and exited non-zero: "not there" is an answer, "could
    not ask" is `GitError`."""
    argv = ["git", *(["-C", str(path)] if path else []), *args]
    try:
        done = subprocess.run(argv, env=child_env(), capture_output=True, timeout=TIMEOUT)
    except (FileNotFoundError, PermissionError) as e:
        raise GitError(f"could not run git on PATH {child_env()['PATH']}: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {' '.join(args)} in {path} did not finish in {TIMEOUT:.0f}s") from e
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", "replace")


def git_runs() -> bool:
    try:
        return _git(None, "--version") is not None
    except GitError:
        return False


def workspaces(end_rows: Iterable[dict[str, Any]]) -> dict[str, str]:
    """R11. `{slot: path}` of every distinct `workspace` the rows carry that is an absolute
    path to a directory there now. A value already a slot names no path."""
    out: dict[str, str] = {}
    for w in sorted({str(r.get("workspace") or "") for r in end_rows}):
        if w and os.path.isabs(w) and Path(w).is_dir():
            out[units.slot(w)] = w
    return out


def main_of(path: str) -> tuple[str, str] | None:
    """`(sha, committer date)` of the local `main`, `None` when it has none."""
    sha = _git(path, "rev-parse", "--verify", "--quiet", "main^{commit}")
    if not sha or not sha.strip():
        return None
    line = _git(path, "log", "-1", "--format=%H %cI", sha.strip())
    if not line or len(line.split()) != 2:
        return None
    full, when = line.split()
    return full, when


def _utc(at: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(at or ""))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def date_sources(end_rows: Iterable[dict[str, Any]], found: Iterable[dict[str, str]]) -> dict[str, dict[str, str]]:
    """R1. `{label: {date, at, slot, path}}` for every source a `done` `end` row of its unit
    and stage dates: the latest such row's `at` in UTC, and its UTC day. `spike.md` is dated by
    `spike`, `review.md` by `review`. A source no row dates is not in the map.

    The run log records `workspace` as a path (`spike.md ## U1`), so it goes through
    `units.slot`; a value already a slot is compared as it is. `path` is `""` when
    `workspaces` cannot give one (R11)."""
    rows = [r for r in end_rows if r.get("kind") == "end"]
    paths = workspaces(rows)
    slots: dict[str, str] = {}
    latest: dict[tuple[str, str, str], datetime] = {}
    for r in rows:
        if r.get("outcome") != "done" or r.get("stage") not in STAGE_OF.values():
            continue
        w = str(r.get("workspace") or "")
        dt = _utc(r.get("at"))
        if not w or dt is None:
            continue
        if w not in slots:
            slots[w] = units.slot(w) if os.path.isabs(w) else w
        k = (slots[w], str(r.get("unit") or ""), str(r["stage"]))
        if k not in latest or dt > latest[k]:
            latest[k] = dt
    out: dict[str, dict[str, str]] = {}
    for s in found:
        dt = latest.get((s["slot"], s["unit"], STAGE_OF.get(s["file"], "")))
        if dt is not None:
            out[s["label"]] = {"date": dt.date().isoformat(), "at": dt.isoformat(),
                               "slot": s["slot"], "path": paths.get(s["slot"], "")}
    return out


def tool_name(scope: str) -> str:
    """R4. The name of a `tool:` scope, lower case, `_` read as `-`; a version is not kept."""
    rest = scope[len("tool:"):].split()
    return rest[0].lower().replace("_", "-") if rest else ""


def pins(path: str, commit: str) -> dict[str, str]:
    """R4. `{name: version}` at `commit`: `python` from `.python-version`, every package of
    `uv.lock` by its name as `tool_name` reads one. A name `uv.lock` holds at more than one
    version has no pin: which one is the tool's is not known. An absent file gives nothing."""
    out: dict[str, str] = {}
    lock = _git(path, "show", f"{commit}:uv.lock")
    if lock is not None:
        try:
            packages = tomllib.loads(lock).get("package") or []
        except tomllib.TOMLDecodeError:
            packages = []
        seen: dict[str, set[str]] = {}
        for p in packages:
            if isinstance(p, dict) and p.get("name") and p.get("version"):
                seen.setdefault(tool_name("tool:" + str(p["name"])), set()).add(str(p["version"]))
        out.update({name: next(iter(vs)) for name, vs in seen.items() if len(vs) == 1})
    python = _git(path, "show", f"{commit}:.python-version")
    if python is not None:
        first = next((line.strip() for line in python.splitlines() if line.strip()), "")
        if first:
            out["python"] = first
    return out


class Reader:
    """The git reads of one gather or one check, each asked once."""

    def __init__(self) -> None:
        self._before: dict[tuple[str, str], str | None] = {}
        self._pins: dict[tuple[str, str], dict[str, str]] = {}
        self._tree: dict[str, set[str] | None] = {}
        self._show: dict[tuple[str, str], str | None] = {}

    def commit_before(self, path: str, at: str) -> str | None:
        """`git rev-list -1 --before=<at> main`, `None` when no commit is that old."""
        if (path, at) not in self._before:
            out = _git(path, "rev-list", "-1", f"--before={at}", "main")
            self._before[(path, at)] = (out or "").strip() or None
        return self._before[(path, at)]

    def pins(self, path: str, commit: str) -> dict[str, str]:
        if (path, commit) not in self._pins:
            self._pins[(path, commit)] = pins(path, commit)
        return self._pins[(path, commit)]

    def ref_exists(self, path: str, ref: str) -> tuple[bool, str]:
        """R5. `<path>` is in `git ls-tree -r --name-only main`, and `::<symbol>`, when there
        is one, matches `\\b<symbol>\\b` in `git show main:<path>`."""
        file, _, symbol = ref.partition("::")
        file, symbol = file.strip(), symbol.strip()
        if path not in self._tree:
            listed = _git(path, "ls-tree", "-r", "--name-only", "main")
            self._tree[path] = set(listed.splitlines()) if listed is not None else None
        tree = self._tree[path]
        if tree is None:
            return False, "main cannot be read"
        if not file or file not in tree:
            return False, f"{file or '(no path)'} is not on main"
        if symbol:
            if (path, file) not in self._show:
                self._show[(path, file)] = _git(path, "show", f"main:{file}")
            text = self._show[(path, file)]
            if text is None or not re.search(r"\b" + re.escape(symbol) + r"\b", text):
                return False, f"{symbol} is not in {file} on main"
        return True, ""


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


_FOLDED = tuple(_fold(m) for m in MARKERS)


def unmeasured(text: str) -> bool:
    """R6. Whether `text` holds one of `MARKERS`."""
    folded = _fold(text)
    return any(m in folded for m in _FOLDED)


def subjects(e: dict[str, Any]) -> list[str]:
    """R6. A `tool:` entry's tool name; a `workspace:` entry's `Ref:` paths and symbols."""
    if e["scope"].startswith("tool:"):
        return [tool_name(e["scope"])]
    out: list[str] = []
    for ref in e.get("refs") or []:
        file, _, symbol = ref.partition("::")
        out += [x.strip() for x in (file, symbol) if x.strip()]
    return out


def counted(e: dict[str, Any], texts: dict[str, str]) -> list[str]:
    """R6. The labels an entry cites that still count for it: a source with a line holding
    both a marker and one of the entry's subjects does not, nor one `texts` no longer holds."""
    patterns = [re.compile(r"(?<![\w-])" + re.escape(_fold(s)) + r"(?![\w-])") for s in subjects(e) if s]
    out = []
    for label in dict.fromkeys(knowledge.label_of(s) for s in e["sources"]):
        if label not in texts:
            continue
        if any(unmeasured(line) and any(p.search(_fold(line)) for p in patterns)
               for line in texts[label].splitlines()):
            continue
        out.append(label)
    return out


def admit(new: list[dict[str, Any]], others: list[dict[str, Any]], ctx: dict[str, Any]
          ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """`(entries, dropped)`: the store after this batch, by id, and what the code dropped.

    `new` are the entries the batch's session returned and `validate` passed, `others` the
    store's entries it was not given. `ctx` carries `dates` (`date_sources`), `texts` (label
    to the source's text with `## Answers` cut), `workspaces` (`workspaces`) and `git` (a
    `Reader`). In the order of `spec.md` Design 5: the date (R1), the version (R4), the size,
    the refs of every `workspace:` entry old and new (R5, R11), what was not measured (R6),
    then the share of `tool:` entries across the store (R7). A `GitError` drops the entry it
    met, with `detail`, and never the batch."""
    dates: dict[str, dict[str, str]] = ctx["dates"]
    texts: dict[str, str] = ctx["texts"]
    paths: dict[str, str] = ctx["workspaces"]
    git: Reader = ctx["git"]
    dropped: list[dict[str, str]] = []

    def drop(e: dict[str, Any], reason: str, detail: str = "") -> None:
        dropped.append({"id": f"K{e['id']}", "reason": reason, **({"detail": detail} if detail else {})})

    fresh: list[dict[str, Any]] = []
    for e in new:
        e = dict(e)
        dated = [dates[label] for label in (knowledge.label_of(s) for s in e["sources"]) if label in dates]
        if not dated:
            drop(e, "undated")
            continue
        e["measured"] = max(d["date"] for d in dated)
        if e["scope"].startswith("tool:"):
            name = tool_name(e["scope"])
            located = [d for d in dated if d["path"]]
            if not located:
                drop(e, "unpinned")
                continue
            newest = max(located, key=lambda d: d["at"])
            try:
                commit = git.commit_before(newest["path"], newest["at"])
                version = git.pins(newest["path"], commit).get(name) if commit else None
            except GitError as err:
                drop(e, "unpinned", str(err))
                continue
            if not version:
                drop(e, "unpinned")
                continue
            e["scope"] = f"tool:{name} {version}"
        e["text"] = knowledge.format_entry(e)
        if len(e["text"].encode("utf-8")) > knowledge.ENTRY_BYTES:
            drop(e, "too-big")
            continue
        fresh.append(e)

    ids = {e["id"] for e in fresh}
    kept: list[dict[str, Any]] = []
    for e in others + fresh:
        if not e["scope"].startswith("workspace:"):
            kept.append(e)
            continue
        path = paths.get(e["scope"][len("workspace:"):])
        if not path:
            drop(e, "no-workspace")
            continue
        refs = e.get("refs") or []
        try:
            missing = not refs or not all(git.ref_exists(path, r)[0] for r in refs)
        except GitError as err:
            drop(e, "ref-missing", str(err))
            continue
        if missing:
            drop(e, "ref-missing")
            continue
        kept.append(e)

    store: list[dict[str, Any]] = []
    for e in kept:
        if e["id"] in ids and (unmeasured(e["statement"]) or not counted(e, texts)):
            drop(e, "not-measured")
            continue
        store.append(e)

    tools = sum(1 for e in store if e["scope"].startswith("tool:"))
    spaces = sorted((e for e in store if e["scope"].startswith("workspace:")),
                    key=lambda e: (e.get("measured") or "", e["id"]))
    cut = spaces[:max(0, len(spaces) - tools)]
    for e in cut:
        drop(e, "ratio")
    gone = {id(e) for e in cut}
    return sorted((e for e in store if id(e) not in gone), key=lambda e: e["id"]), dropped


def _tool_verdict(e: dict[str, Any], mains: dict[str, str], git: Reader) -> str:
    rest = e["scope"][len("tool:"):].split()
    if len(rest) < 2 or not mains:
        return "cannot be checked"
    name, version = tool_name(e["scope"]), rest[1]
    found = {slot: git.pins(path, "main").get(name) for slot, path in sorted(mains.items())}
    unpinned = [slot for slot, v in found.items() if not v]
    if unpinned:
        return f"no pin in {', '.join(unpinned)}"
    if len(set(found.values())) > 1:
        return "versions differ: " + ", ".join(f"{slot}={v}" for slot, v in found.items())
    [pinned] = set(found.values())
    if pinned != version:
        return f"version {version}, main has {pinned} in {', '.join(found)}"
    return ""


def _workspace_verdict(e: dict[str, Any], paths: dict[str, str], git: Reader) -> str:
    path = paths.get(e["scope"][len("workspace:"):])
    refs = e.get("refs") or []
    if not path or not refs:
        return "cannot be checked"
    missing = [r for r in refs if not git.ref_exists(path, r)[0]]
    return f"ref missing: {', '.join(missing)}" if missing else ""


def check(data_dir: str | os.PathLike[str] | None, say: Callable[[str], None]) -> int:
    """R10. `0` every entry passes and the share of `tool:` entries is at least half, `1` a
    failure, `2` the store, the run log or `git` cannot be read. Opens no session and writes
    nothing: the run log is read by `measure.read_rows` at `mode=ro`, not by a `Journal`,
    whose `records` may import an old JSONL."""
    import sqlite3

    from coscc.data import Data
    from coscc.measure import read_rows

    path = knowledge.path_of(data_dir) / knowledge.STORE
    try:
        text = knowledge.load(path)
    except (OSError, UnicodeDecodeError) as e:
        say(f"coscc knowledge check: the store cannot be read: {type(e).__name__}: {e}")
        return 2
    db = Data(data_dir).db_path
    try:
        if not db.is_file():
            raise FileNotFoundError(str(db))
        rows = [r for r in read_rows(db) if r.get("kind") == "end"]
    except (OSError, sqlite3.Error) as e:
        say(f"coscc knowledge check: the run log cannot be read: {type(e).__name__}: {e}")
        return 2
    if not git_runs():
        say("coscc knowledge check: git cannot be run")
        return 2

    parsed = knowledge.parse(text)
    entries = parsed["entries"]
    paths = workspaces(rows)
    wanted = sorted({e["scope"][len("workspace:"):] for e in entries if e["scope"].startswith("workspace:")})
    for_tools = {slot: paths[slot] for slot in wanted if slot in paths} if wanted else dict(paths)
    git = Reader()
    try:
        for slot, where in sorted(for_tools.items()):
            head = main_of(where)
            if head is None:
                say(f"coscc knowledge check: {where} has no main")
                return 2
            say(f"main of {slot}: {head[0][:12]} {head[1]}")
        failed = False
        for why in parsed["skipped"]:
            say(f"{why}: fail: cannot be checked")
            failed = True
        for e in entries:
            if e["scope"].startswith("tool:"):
                verdict = _tool_verdict(e, for_tools, git)
            else:
                verdict = _workspace_verdict(e, paths, git)
            say(f"K{e['id']} {e['scope']}: " + (f"fail: {verdict}" if verdict else "pass"))
            failed = failed or bool(verdict)
    except GitError as e:
        say(f"coscc knowledge check: {e}")
        return 2

    tools, n = sum(1 for e in entries if e["scope"].startswith("tool:")), len(entries)
    say(f"checked on {datetime.now(timezone.utc).date().isoformat()}")
    say(f"tool: {tools}/{n} = {round(100 * tools / n) if n else 0}% (needs >= 50%)")
    return 1 if failed or not n or tools * 2 < n else 0
