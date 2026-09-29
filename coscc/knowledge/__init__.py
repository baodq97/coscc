"""The knowledge store: what earlier units measured, for the stages that plan the next one.

One Markdown file under `COS_DATA_DIR`, in no repository, beside the manifest of its sources
and `HEALTH`: what the last check on a fetched `origin/main` found of each entry. A person can
edit it by hand; a block this module cannot read is skipped, never a reason to read nothing.

`coscc/knowledge/gather.py` writes it, only through `validate` and `save`. `Service.run_step`
reads it once per `spec`, `spike`, `plan` or `impl` step, only while `COS_KNOWLEDGE` is on and
the unit is in the `ON` arm, and hands the slice this workspace receives, less what is no
longer true of the step's `HEAD`, to `runner.compose_prompt`, which does not read the disk.

Every function here but `load`, `save` and `for_step` is pure.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from coscc.data import Data

# The stages that receive the store; `review` is deliberately not one.
STAGES = ("spec", "spike", "plan", "impl")

# The bytes of entries one workspace receives. Chosen, not measured.
CAP_BYTES = 8192
# The bytes of one entry. Chosen, not measured: a statement of one or two sentences and its
# fields fit several times over, and one entry cannot take the whole cap.
ENTRY_BYTES = 600

DIR = "knowledge"
STORE = "knowledge.md"
SOURCES = "sources.json"
# `{sha: {slot: sha}, at, entries: {"K<n>": "" | why}}`, rewritten whole by `save`.
HEALTH = "health.json"

# The `start` field every step carries while `COS_KNOWLEDGE` is on, and its
# two arms; `coscc/knowledge/measure.py` reads them back by these names.
TRIAL_FIELD = "knowledge_trial"
ON, OFF = "on", "off"

TITLE = "# Knowledge"
SOURCE_FILES = ("plan.md", "review.md", "spec.md", "spike.md")

_HEADER = re.compile(r"^Version:\s*(\d+)\.\s+Gathered:\s*(\S*?)\.\s+Max id:\s*K(\d+)\.\s*$")
_ENTRY_HEAD = re.compile(r"^## K(\d+)\s*$")
_SCOPE = re.compile(r"^(?:tool:\S+(?: \S.*)?|workspace:\S+)$")
_SOURCE = re.compile(
    r"^(?P<label>(?P<slot>[A-Za-z0-9._-]+)/(?P<unit>\d{4}_[a-z0-9]+(?:-[a-z0-9]+)*)/"
    r"(?P<file>spike\.md|review\.md|spec\.md|plan\.md))\s+(?P<anchor>\S.*?)\s*$"
)
_HEADING = re.compile(r"^## \S.*$")
_ANCHORS = {
    "spike.md": re.compile(r"^## U\d+$"),
    "review.md": re.compile(r"^Round \d+(?: F\d+)?$"),
    "spec.md": _HEADING,
    "plan.md": _HEADING,
}
_WANT = {"spike.md": "## U<n>", "review.md": "Round <n> or Round <n> F<n>",
         "spec.md": "## <heading>", "plan.md": "## <heading>"}
_FIELDS = ("Scope:", "Source:", "Ref:", "Measured:")


def path_of(data_dir: str | os.PathLike[str] | None) -> Path:
    """The store's directory, under the data root `coscc/data.py` resolves."""
    return Data(data_dir).root / DIR


def empty_header() -> dict[str, Any]:
    return {"version": 0, "gathered": "never", "max_id": 0}


def _entry(n: int, lines: list[str]) -> tuple[dict[str, Any] | None, str]:
    """One `## K<n>` block, or `None` and why it cannot be read. A block with no `Ref:`
    reads: a person editing by hand may leave it out, and `coscc/knowledge/admit.py` drops it."""
    scope, sources, refs, measured, statement = "", [], [], "", []
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("Scope:"):
            scope = s[len("Scope:"):].strip()
        elif s.startswith("Source:"):
            sources.append(s[len("Source:"):].strip())
        elif s.startswith("Ref:"):
            refs.append(s[len("Ref:"):].strip())
        elif s.startswith("Measured:"):
            measured = s[len("Measured:"):].strip()
        elif s:
            statement.append(s)
    if not scope:
        return None, f"K{n} has no Scope:"
    if not sources:
        return None, f"K{n} has no Source:"
    if not statement:
        return None, f"K{n} has no statement"
    text = "\n".join(lines).rstrip()
    return {
        "id": n, "scope": scope, "sources": sources, "refs": refs, "measured": measured,
        "statement": " ".join(statement), "text": text,
    }, ""


def format_entry(e: dict[str, Any]) -> str:
    """The block of an entry from its fields, in the grammar's order. `coscc/knowledge/admit.py` sets
    `text` to this once it has written the date and the version."""
    lines = [f"## K{e['id']}", f"Scope: {e['scope']}"]
    lines += [f"Source: {s}" for s in e["sources"]]
    lines += [f"Ref: {r}" for r in e.get("refs") or []]
    if e.get("measured"):
        lines.append(f"Measured: {e['measured']}")
    lines.append(e["statement"])
    return "\n".join(lines)


def parse(text: str) -> dict[str, Any]:
    """`{header, entries, skipped}`. A store with no header reads as `empty_header()`, and a
    block missing its scope, a source or a statement is in `skipped` with why. So is every line outside the entries but the title and the
    header: `render` writes back nothing else, so a line nobody names would be lost."""
    header = empty_header()
    entries: list[dict[str, Any]] = []
    skipped: list[str] = []
    block: list[str] | None = None
    number = 0
    titled = headed = foreign = False

    def close() -> None:
        if block is None:
            return
        found, why = _entry(number, block)
        if found is None:
            skipped.append(why)
        else:
            entries.append(found)

    for line in (text or "").splitlines():
        head = _ENTRY_HEAD.match(line.rstrip())
        if head:
            close()
            number, block, foreign = int(head.group(1)), [line.rstrip()], False
            continue
        if line.startswith("## "):
            close()
            block, foreign = None, True
            skipped.append(f"a block that is not an entry: {line.strip()[:80]}")
            continue
        if block is not None:
            block.append(line.rstrip())
            continue
        s = line.strip()
        if not s or foreign:  # the lines of a block already in `skipped`
            continue
        if s == TITLE and not titled and not headed:
            titled = True
            continue
        m = _HEADER.match(s)
        if m and not headed:
            header = {"version": int(m.group(1)), "gathered": m.group(2), "max_id": int(m.group(3))}
            headed = True
            continue
        skipped.append(f"a line outside every entry: {s[:80]}")
    close()
    return {"header": header, "entries": entries, "skipped": skipped}


def has_header(text: str) -> bool:
    """Whether a `Version: … Max id: K<n>.` line comes before the first block."""
    for line in (text or "").splitlines():
        if line.startswith("## "):
            return False
        if _HEADER.match(line.strip()):
            return True
    return False


def entries_text(entries: Iterable[dict[str, Any]]) -> str:
    """The blocks, in the order given, one blank line apart. `""` for none."""
    return "\n\n".join(e["text"] for e in entries)


def render(header: dict[str, Any], entries: Iterable[dict[str, Any]]) -> str:
    """The whole file: title, header, then every block by id."""
    head = (
        f"{TITLE}\nVersion: {header['version']}. Gathered: {header['gathered']}. "
        f"Max id: K{header['max_id']}.\n"
    )
    body = entries_text(sorted(entries, key=lambda e: e["id"]))
    return head + (f"\n{body}\n" if body else "")


def for_workspace(entries: Iterable[dict[str, Any]], slot: str) -> list[dict[str, Any]]:
    """Every `tool:` entry, and the `workspace:` entries of this workspace only."""
    return [
        e for e in entries
        if e["scope"].startswith("tool:") or e["scope"] == f"workspace:{slot}"
    ]


def arm(unit: str) -> str:
    """`ON` when the first byte of the SHA-256 of `"knowledge:" + unit` is even.
    The prefix keeps it apart from `efforttrial.arm`, which hashes the bare name."""
    return ON if hashlib.sha256(("knowledge:" + unit).encode("utf-8")).digest()[0] % 2 == 0 else OFF


def version_of(section: str) -> str:
    """The first twelve hex of the sha256 of what went into the prompt."""
    return hashlib.sha256(section.encode("utf-8")).hexdigest()[:12]


def slice_for(text: str, slot: str) -> tuple[str, dict[str, Any]]:
    """`(section, record)`: the blocks this workspace receives, by id, never over
    `CAP_BYTES`, and `{version, entries, bytes}` for the `start` record.

    A store edited by hand past the cap gives the newest entries first, whole, and puts
    them back in id order. `validate` keeps a gathered store under it, so this is the
    guard for the file a person wrote."""
    section, record, _ = _slice(for_workspace(parse(text)["entries"], slot))
    return section, record


def _slice(entries: Iterable[dict[str, Any]]) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """`slice_for` of entries already chosen for a workspace, and the ones it carries."""
    applicable = sorted(entries, key=lambda e: e["id"])
    chosen = applicable
    if len(entries_text(chosen).encode("utf-8")) > CAP_BYTES:
        chosen, used = [], 0
        for e in sorted(applicable, key=lambda e: e["id"], reverse=True):
            size = len(e["text"].encode("utf-8")) + (2 if chosen else 0)
            if used + size <= CAP_BYTES:
                chosen.append(e)
                used += size
        chosen.sort(key=lambda e: e["id"])
    section = entries_text(chosen)
    return section, {
        "version": version_of(section),
        "entries": len(chosen),
        "bytes": len(section.encode("utf-8")),
    }, chosen


def label_of(source: str) -> str:
    """`<slot>/<unit>/<file>` of a `Source:` value, `""` when it has not that shape."""
    m = _SOURCE.match(source.strip())
    return m.group("label") if m else ""


def parts_of(source: str) -> dict[str, str] | None:
    """`{slot, unit, file, anchor}` of a `Source:` value, `None` when it has not that shape."""
    m = _SOURCE.match(source.strip())
    return {k: m.group(k) for k in ("slot", "unit", "file", "anchor")} if m else None


def _source_problem(source: str) -> str:
    m = _SOURCE.match(source.strip())
    if not m:
        return "is not <workspace>/<unit>/<spec.md|plan.md|spike.md|review.md> <anchor>"
    if not _ANCHORS[m.group("file")].match(m.group("anchor")):
        return f"has the anchor {m.group('anchor')!r}, not {_WANT[m.group('file')]}"
    return ""


def _drop_id(item: dict[str, Any]) -> int | None:
    raw = str(item.get("id") or "").strip()
    m = re.fullmatch(r"K?(\d+)", raw)
    return int(m.group(1)) if m else None


def date_of(e: dict[str, Any], dates: dict[str, str]) -> str:
    """The latest date of the sources an entry cites, `""` when none has one."""
    return max((dates.get(label_of(s), "") for s in e["sources"]), default="")


def validate(
    old_slice: str,
    new_text: str,
    dropped: list[dict[str, Any]],
    batch_sources: Iterable[str],
    slot: str,
    max_id: int,
    others: Iterable[dict[str, Any]] = (),
    dates: dict[str, str] | None = None,
) -> list[str]:
    """Every reason `new_text` may not replace `old_slice`; `[]` is a pass.

    `old_slice` is the part of the store the gathering session was given, `new_text` what it
    gave back, `dropped` its `[{id, reason, merged_into?}]`, `batch_sources` the labels it
    read, `slot` the batch's workspace and `max_id` the store header's. `others` are the
    entries of the store it was not given — other workspaces' — so the cap is checked for
    what each of them would receive too, and no new entry takes one of their ids.

    A `Measured:` the session wrote is not read: the code writes it. With `dates`, label to
    `YYYY-MM-DD`, an entry merged into one whose sources are older is a reason; without it
    that is not checked, and `coscc/knowledge/gather.py` always passes it."""
    others = list(others)
    old = parse(old_slice)["entries"]
    new = parse(new_text)
    reasons = list(new["skipped"])
    old_ids = {e["id"]: e for e in old}
    other_ids = {e["id"] for e in others}
    known = set(batch_sources)
    for e in old:
        known |= {label_of(s) for s in e["sources"]}
    known.discard("")

    seen: set[int] = set()
    for e in new["entries"]:
        k = f"K{e['id']}"
        if e["id"] in seen:
            reasons.append(f"{k} appears twice")
        seen.add(e["id"])
        if e["id"] not in old_ids and e["id"] <= max_id:
            reasons.append(f"{k} is new but not above the store's Max id K{max_id}: an id is never used again")
        if e["id"] not in old_ids and e["id"] in other_ids:
            reasons.append(f"{k} is new but another workspace's entry already holds it")
        if not _SCOPE.match(e["scope"]):
            reasons.append(f"{k} has the scope {e['scope']!r}, not tool:<name>[ <versions>] or workspace:<key>")
        elif e["scope"].startswith("workspace:") and e["scope"] != f"workspace:{slot}":
            reasons.append(f"{k} is scoped to {e['scope']}, and this batch is workspace:{slot}")
        for s in e["sources"]:
            problem = _source_problem(s)
            if problem:
                reasons.append(f"{k}'s source {s!r} {problem}")
            elif label_of(s) not in known:
                reasons.append(f"{k} cites {label_of(s)}, which is neither in the store nor in this batch")
        if e["scope"].startswith("tool:") and e["refs"]:
            reasons.append(f"{k} is a tool: entry and carries Ref:")
        if len(e["text"].encode("utf-8")) > ENTRY_BYTES:
            reasons.append(f"{k} is {len(e['text'].encode('utf-8'))} bytes, over {ENTRY_BYTES}")

    named: dict[int, dict[str, Any]] = {}
    for item in dropped or []:
        n = _drop_id(item) if isinstance(item, dict) else None
        if n is None:
            reasons.append(f"a dropped item names no id: {str(item)[:80]}")
            continue
        if not str(item.get("reason") or "").strip():
            reasons.append(f"K{n} is dropped with no reason")
        if n not in old_ids:
            reasons.append(f"K{n} is dropped but the store it was given did not hold it")
        if n in seen:
            reasons.append(f"K{n} is dropped and kept")
        named[n] = item
    for n in old_ids:
        if n not in seen and n not in named:
            reasons.append(f"K{n} is gone and not in the dropped list")
    if dates is not None:
        kept = {e["id"]: e for e in new["entries"]}
        for n, item in sorted(named.items()):
            into = _drop_id({"id": item.get("merged_into")}) if item.get("merged_into") else None
            if n not in old_ids or into not in kept:
                continue
            was, now = date_of(old_ids[n], dates), date_of(kept[into], dates)
            if not now or now < was:
                reasons.append(f"K{n} is merged into K{into}, whose sources ({now or 'no date'}) "
                               f"are older than K{n}'s ({was or 'no date'})")

    size = len(entries_text(new["entries"]).encode("utf-8"))
    if size > CAP_BYTES:
        reasons.append(f"workspace:{slot} would receive {size} bytes, over {CAP_BYTES}")
    tools = [e for e in new["entries"] if e["scope"].startswith("tool:")]
    by_slot: dict[str, list[dict[str, Any]]] = {}
    for e in others:
        if e["scope"].startswith("workspace:"):
            by_slot.setdefault(e["scope"][len("workspace:"):], []).append(e)
    for other, own in sorted(by_slot.items()):
        size = len(entries_text(tools + own).encode("utf-8"))
        if size > CAP_BYTES:
            reasons.append(f"workspace:{other} would receive {size} bytes, over {CAP_BYTES}")
    return reasons


def load(path: str | os.PathLike[str]) -> str:
    """The store's text, read once. Raises `OSError` or `UnicodeDecodeError`."""
    return Path(path).read_bytes().decode("utf-8")


def save(path: str | os.PathLike[str], text: str) -> None:
    """A temporary file in the same directory, flushed to disk, then renamed over the
    store, so a reader gets the old file or the new one and never half of either."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def for_step(data_dir: str | os.PathLike[str] | None, slot: str,
             worktree: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """`{knowledge, knowledge_record}` for `Runner.run`, read once. Never raises: a store
    that cannot be read (absent, refused, not UTF-8, a bug here) is no section and a record
    carrying `error`, and the step runs as it would have.

    Each entry is checked on `HEAD` of `worktree`, the tree the step works on,
    and one no longer true of it is withheld: a `workspace:` entry whose `Ref:` is not there, a
    `tool:` entry whose version is not the pin there. The record says which were carried
    (`ids`), which withheld and why (`withheld`) and the `HEAD` read (`head`, `""` for none).
    When git cannot answer, every `workspace:` entry is withheld with its reason and every
    `tool:` entry is carried."""
    try:
        applicable = for_workspace(parse(load(path_of(data_dir) / STORE))["entries"], slot)
        kept, withheld, head = _checked(applicable, str(worktree or ""))
        section, record, chosen = _slice(kept)
    except Exception as e:  # noqa: BLE001 — recorded, never a reason to refuse the step
        return {
            "knowledge": "",
            "knowledge_record": {
                "version": version_of(""), "entries": 0, "bytes": 0,
                "error": f"{type(e).__name__}: {e}",
            },
        }
    record.update(ids=[f"K{e['id']}" for e in chosen], withheld=withheld, head=head)
    return {"knowledge": section, "knowledge_record": record}


def _checked(entries: list[dict[str, Any]], worktree: str
             ) -> tuple[list[dict[str, Any]], list[dict[str, str]], str]:
    """`(kept, withheld, head)` of `for_step`, against `HEAD` of `worktree`."""
    # Here, not at the top: `admit` imports this module.
    from coscc.knowledge import admit

    reader = admit.Reader("HEAD")
    broken = ""
    head = ""
    try:
        head = ((admit._git(worktree, "rev-parse", "--verify", "HEAD^{commit}") or "").strip()
                if worktree else "")
        if not head:
            broken = f"HEAD of {worktree or 'the step'} cannot be read"
    except admit.GitError as e:
        broken = str(e)
    pinned: dict[str, str] | None = None
    kept: list[dict[str, Any]] = []
    withheld: list[dict[str, str]] = []

    def hold(e: dict[str, Any], why: str) -> None:
        withheld.append({"id": f"K{e['id']}", "reason": why})

    for e in entries:
        if e["scope"].startswith("tool:"):
            rest = e["scope"][len("tool:"):].split()
            if len(rest) < 2 or broken:
                kept.append(e)
                continue
            try:
                if pinned is None:
                    pinned = reader.pins(worktree, "HEAD")
            except admit.GitError:
                kept.append(e)
                continue
            found = pinned.get(admit.tool_name(e["scope"]))
            if not found:
                hold(e, "no pin")
            elif found != rest[1]:
                hold(e, f"version {rest[1]}, HEAD has {found}")
            else:
                kept.append(e)
            continue
        if broken:
            hold(e, broken)
            continue
        refs = e.get("refs") or []
        if not refs:
            hold(e, "no Ref:")
            continue
        try:
            missing = [why for ok, why in (reader.ref_exists(worktree, r) for r in refs) if not ok]
        except admit.GitError as err:
            hold(e, str(err))
            continue
        if missing:
            hold(e, "; ".join(missing))
        else:
            kept.append(e)
    return kept, withheld, head
