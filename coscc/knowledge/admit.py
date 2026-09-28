"""What the code decides about a gathered entry, after `knowledge.validate` and before the save.

`0108_the-knowledge-store-fills-with-stale-notes-about-old-code`. A session writes the
statement; everything that can be read instead of believed is read here: the date from the
run log (R1), a tool's version from the workspace's pins (R4), that a `Ref:` still exists on
the ref the `Reader` names (R5), and that no source says it was not run (R6). `admit` never
fails a batch: what it cannot admit it drops, with a reason, into the batch's `dropped` (R8).

`0131`: the share of `tool:` entries is no longer a rule (R6), and no unit may be the only
source of more than `ONE_UNIT` entries (R5). The ref is `origin/main` just fetched by
`fresh_main` (R11), and `health` is what `coscc knowledge show`, `measure` and a gather write
to `knowledge.HEALTH` (R12).

It does not import `coscc/knowledge/gather.py`, so `gather` and `knowledge_cli` both can import it.
Everything but `_git` and what calls it is pure.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tomllib
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from coscc import knowledge, units
from coscc.git.gitops import child_env

# Chosen, not measured: each call reads one object or one tree of a local repository.
TIMEOUT = 60.0

STAGE_OF = {"spike.md": "spike", "review.md": "review", "spec.md": "spec", "plan.md": "plan"}

# `0131` R5. The entries whose every `Source:` is one unit, per unit. Chosen, not measured.
ONE_UNIT = 2

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


def _utc(at: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(at or ""))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def date_sources(end_rows: Iterable[dict[str, Any]], found: Iterable[dict[str, str]]) -> dict[str, dict[str, str]]:
    """R1. `{label: {date, at, slot, path}}` for every source a `done` `end` row of its unit
    and stage dates: the latest such row's `at` in UTC, and its UTC day. `spike.md` is dated by
    `spike`, `review.md` by `review`, and since `0131` `spec.md` by `spec` and `plan.md` by
    `plan`. A source no row dates is not in the map.

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
    """The git reads of one gather or one check, each asked once, all on `ref`: `main` as it
    was, `origin/main` after `fresh_main` (`0131` R11), `HEAD` for a step (R9)."""

    def __init__(self, ref: str = "main") -> None:
        self.ref = ref
        self._before: dict[tuple[str, str], str | None] = {}
        self._pins: dict[tuple[str, str], dict[str, str]] = {}
        self._tree: dict[str, set[str] | None] = {}
        self._show: dict[tuple[str, str], str | None] = {}

    def commit_before(self, path: str, at: str) -> str | None:
        """`git rev-list -1 --before=<at> <ref>`, `None` when no commit is that old."""
        if (path, at) not in self._before:
            out = _git(path, "rev-list", "-1", f"--before={at}", self.ref)
            self._before[(path, at)] = (out or "").strip() or None
        return self._before[(path, at)]

    def pins(self, path: str, commit: str) -> dict[str, str]:
        if (path, commit) not in self._pins:
            self._pins[(path, commit)] = pins(path, commit)
        return self._pins[(path, commit)]

    def ref_exists(self, path: str, ref: str) -> tuple[bool, str]:
        """R5. `<path>` is in `git ls-tree -r --name-only <ref>`, and `::<symbol>`, when
        there is one, matches `\\b<symbol>\\b` in `git show <ref>:<path>`."""
        file, _, symbol = ref.partition("::")
        file, symbol = file.strip(), symbol.strip()
        if path not in self._tree:
            listed = _git(path, "ls-tree", "-r", "--name-only", self.ref)
            self._tree[path] = set(listed.splitlines()) if listed is not None else None
        tree = self._tree[path]
        if tree is None:
            return False, f"{self.ref} cannot be read"
        if not file or file not in tree:
            return False, f"{file or '(no path)'} is not on {self.ref}"
        if symbol:
            if (path, file) not in self._show:
                self._show[(path, file)] = _git(path, "show", f"{self.ref}:{file}")
            text = self._show[(path, file)]
            if text is None or not re.search(r"\b" + re.escape(symbol) + r"\b", text):
                return False, f"{symbol} is not in {file} on {self.ref}"
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
    then, since `0131` R5, no unit the only source of more than `ONE_UNIT` entries across the
    store. The share of `tool:` entries (`0108` R7) is gone (`0131` R6). A `GitError` drops
    the entry it met, with `detail`, and never the batch."""
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

    # `0131` R5: of the entries one unit is the only source of, the newest `ONE_UNIT` stay,
    # by `Measured:`, a tie to the higher id.
    alone: dict[str, list[dict[str, Any]]] = {}
    for e in store:
        owners = {"/".join(label.split("/")[:2]) for label in (knowledge.label_of(s) for s in e["sources"])}
        if len(owners) == 1 and "" not in owners:
            alone.setdefault(owners.pop(), []).append(e)
    gone: set[int] = set()
    for owner in sorted(alone):
        ordered = sorted(alone[owner], key=lambda e: (e.get("measured") or "", e["id"]), reverse=True)
        for e in ordered[ONE_UNIT:]:
            drop(e, "one-unit")
            gone.add(id(e))
    return sorted((e for e in store if id(e) not in gone), key=lambda e: e["id"]), dropped


def _tool_verdict(e: dict[str, Any], mains: dict[str, str], git: Reader) -> str:
    rest = e["scope"][len("tool:"):].split()
    if len(rest) < 2 or not mains:
        return "cannot be checked"
    name, version = tool_name(e["scope"]), rest[1]
    found = {slot: git.pins(path, git.ref).get(name) for slot, path in sorted(mains.items())}
    unpinned = [slot for slot, v in found.items() if not v]
    if unpinned:
        return f"no pin in {', '.join(unpinned)}"
    if len(set(found.values())) > 1:
        return "versions differ: " + ", ".join(f"{slot}={v}" for slot, v in found.items())
    [pinned] = set(found.values())
    if pinned != version:
        return f"version {version}, {git.ref} has {pinned} in {', '.join(found)}"
    return ""


def _workspace_verdict(e: dict[str, Any], paths: dict[str, str], git: Reader) -> str:
    path = paths.get(e["scope"][len("workspace:"):])
    refs = e.get("refs") or []
    if not path or not refs:
        return "cannot be checked"
    missing = [r for r in refs if not git.ref_exists(path, r)[0]]
    return f"ref missing: {', '.join(missing)}" if missing else ""


def tool_paths(entries: Iterable[dict[str, Any]], paths: dict[str, str]) -> dict[str, str]:
    """The workspaces a `tool:` entry is checked in: those some `workspace:` entry names,
    or every one when none does, as `0108`'s `check` chose them."""
    wanted = sorted({e["scope"][len("workspace:"):] for e in entries if e["scope"].startswith("workspace:")})
    return {slot: paths[slot] for slot in wanted if slot in paths} if wanted else dict(paths)


def health(entries: Iterable[dict[str, Any]], paths: dict[str, str], reader: Reader) -> dict[str, str]:
    """`0131` R12, R13. `{"K<n>": "" | why}`: whether each entry is still true of `reader.ref`
    in the workspaces `paths` names. What an entry points at is checked — a pinned version, a
    path, a name in a file — never its sentence. Raises `GitError`."""
    entries = list(entries)
    for_tools = tool_paths(entries, paths)
    return {
        f"K{e['id']}": (_tool_verdict(e, for_tools, reader) if e["scope"].startswith("tool:")
                        else _workspace_verdict(e, paths, reader))
        for e in entries
    }


def save_health(data_dir: str | os.PathLike[str] | None, shas: dict[str, str], found: dict[str, str]) -> None:
    """`0131` R12: the whole of `knowledge.HEALTH`, through `knowledge.save`."""
    record = {"sha": dict(sorted(shas.items())), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "entries": found}
    knowledge.save(knowledge.path_of(data_dir) / knowledge.HEALTH, json.dumps(record, indent=2, sort_keys=True) + "\n")


async def fresh_main(path: str | os.PathLike[str]) -> str:
    """`0131` R11. `origin/main` of `path`, just fetched, as a full SHA. The fetch goes
    through `fetches` — in the app its shared table, at a terminal a table of that process
    alone, which only the one retry of a ref-lock race covers (C8). Raises `GitError` when the
    fetch fails or no SHA reads: nothing is then checked on what the ref held before."""
    from coscc.git import fetches, gitops

    try:
        await fetches.fetch(Path(path))
    except gitops.GitError as e:
        raise GitError(f"git fetch origin main in {path} failed: {e}") from e
    sha = await asyncio.to_thread(_git, path, "rev-parse", "--verify", "--quiet", "origin/main^{commit}")
    if not sha or not sha.strip():
        raise GitError(f"{path} has no origin/main after the fetch")
    return sha.strip()
