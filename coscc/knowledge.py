"""The knowledge store: what earlier units measured, for the stages that plan the next one.

`0090_agents-relearn-what-earlier-units-already-knew`. One Markdown file under
`COS_DATA_DIR`, in no repository (`spec.md` R5), beside the manifest of the sources it was
gathered from (R11) and the baseline `coscc knowledge measure` compares against (R15). A
person can read it and edit it by hand; a block this module cannot read is skipped, never a
reason to read nothing.

`coscc/gather.py` writes it, only through `validate` and `save`. `Service.run_step` reads it
once per `spec`, `spike` or `plan` step, only while `COS_KNOWLEDGE` is on, and hands the
slice this workspace receives to `runner.compose_prompt`, which does not read the disk.

Every function here but `load`, `save` and `for_step` is pure.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from coscc.data import Data

# `spec.md` R3, C3. The stages that receive the store; `review` is deliberately not one.
STAGES = ("spec", "spike", "plan")

# `spec.md` R6, C4: the bytes of entries one workspace receives. Chosen, not measured.
CAP_BYTES = 8192
# The bytes of one entry. Chosen, not measured: a statement of one or two sentences and its
# fields fit several times over, and one entry cannot take the whole cap.
ENTRY_BYTES = 600

DIR = "knowledge"
STORE = "knowledge.md"
SOURCES = "sources.json"
BASELINE = "baseline.json"

TITLE = "# Knowledge"
SOURCE_FILES = ("spike.md", "review.md")

_HEADER = re.compile(r"^Version:\s*(\d+)\.\s+Gathered:\s*(\S*?)\.\s+Max id:\s*K(\d+)\.\s*$")
_ENTRY_HEAD = re.compile(r"^## K(\d+)\s*$")
_SCOPE = re.compile(r"^(?:tool:\S+(?: \S.*)?|workspace:\S+)$")
_SOURCE = re.compile(
    r"^(?P<label>(?P<slot>[A-Za-z0-9._-]+)/(?P<unit>\d{4}_[a-z0-9]+(?:-[a-z0-9]+)*)/"
    r"(?P<file>spike\.md|review\.md))\s+(?P<anchor>\S.*?)\s*$"
)
_ANCHORS = {
    "spike.md": re.compile(r"^## U\d+$"),
    "review.md": re.compile(r"^Round \d+(?: F\d+)?$"),
}
_MEASURED = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FIELDS = ("Scope:", "Source:", "Measured:")


def path_of(data_dir: str | os.PathLike[str] | None) -> Path:
    """The store's directory, under the data root `coscc/data.py` resolves."""
    return Data(data_dir).root / DIR


def empty_header() -> dict[str, Any]:
    return {"version": 0, "gathered": "never", "max_id": 0}


def _entry(n: int, lines: list[str]) -> tuple[dict[str, Any] | None, str]:
    """One `## K<n>` block, or `None` and why it cannot be read."""
    scope, sources, measured, statement = "", [], "", []
    for line in lines[1:]:
        s = line.strip()
        if s.startswith("Scope:"):
            scope = s[len("Scope:"):].strip()
        elif s.startswith("Source:"):
            sources.append(s[len("Source:"):].strip())
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
        "id": n, "scope": scope, "sources": sources, "measured": measured,
        "statement": " ".join(statement), "text": text,
    }, ""


def parse(text: str) -> dict[str, Any]:
    """`{header, entries, skipped}`. A store with no header reads as `empty_header()`, and a
    block missing its scope, a source or a statement is in `skipped` with why (R5: the file
    may be edited by hand). So is every line outside the entries but the title and the
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
    """R6: every `tool:` entry, and the `workspace:` entries of this workspace only."""
    return [
        e for e in entries
        if e["scope"].startswith("tool:") or e["scope"] == f"workspace:{slot}"
    ]


def version_of(section: str) -> str:
    """R4: the first twelve hex of the sha256 of what went into the prompt."""
    return hashlib.sha256(section.encode("utf-8")).hexdigest()[:12]


def slice_for(text: str, slot: str) -> tuple[str, dict[str, Any]]:
    """`(section, record)`: the blocks this workspace receives, by id, never over
    `CAP_BYTES`, and `{version, entries, bytes}` for the `start` record (R4, R6).

    A store edited by hand past the cap gives the newest entries first, whole, and puts
    them back in id order. `validate` keeps a gathered store under it, so this is the
    guard for the file a person wrote."""
    applicable = sorted(for_workspace(parse(text)["entries"], slot), key=lambda e: e["id"])
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
    }


def label_of(source: str) -> str:
    """`<slot>/<unit>/<file>` of a `Source:` value, `""` when it has not that shape."""
    m = _SOURCE.match(source.strip())
    return m.group("label") if m else ""


def _source_problem(source: str) -> str:
    m = _SOURCE.match(source.strip())
    if not m:
        return "is not <workspace>/<unit>/<spike.md|review.md> <anchor>"
    if not _ANCHORS[m.group("file")].match(m.group("anchor")):
        want = "## U<n>" if m.group("file") == "spike.md" else "Round <n> or Round <n> F<n>"
        return f"has the anchor {m.group('anchor')!r}, not {want}"
    return ""


def _drop_id(item: dict[str, Any]) -> int | None:
    raw = str(item.get("id") or "").strip()
    m = re.fullmatch(r"K?(\d+)", raw)
    return int(m.group(1)) if m else None


def validate(
    old_slice: str,
    new_text: str,
    dropped: list[dict[str, Any]],
    batch_sources: Iterable[str],
    slot: str,
    max_id: int,
    others: Iterable[dict[str, Any]] = (),
) -> list[str]:
    """`spec.md` R13. Every reason `new_text` may not replace `old_slice`; `[]` is a pass.

    `old_slice` is the part of the store the gathering session was given, `new_text` what it
    gave back, `dropped` its `[{id, reason, merged_into?}]`, `batch_sources` the labels it
    read, `slot` the batch's workspace and `max_id` the store header's. `others` are the
    entries of the store it was not given — other workspaces' — so the cap is checked for
    what each of them would receive too, and no new entry takes one of their ids (R7)."""
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
        if not _MEASURED.match(e["measured"]):
            reasons.append(f"{k} has Measured: {e['measured']!r}, not YYYY-MM-DD")
        else:
            try:
                date.fromisoformat(e["measured"])
            except ValueError:
                reasons.append(f"{k} has Measured: {e['measured']!r}, which is not a date")
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
    """R8: a temporary file in the same directory, flushed to disk, then renamed over the
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


def for_step(data_dir: str | os.PathLike[str] | None, slot: str) -> dict[str, Any]:
    """`{knowledge, knowledge_record}` for `Runner.run`, read once. Never raises: a store
    that cannot be read (absent, refused, not UTF-8, a bug here) is no section and a record
    carrying `error`, and the step runs as it would have."""
    try:
        section, record = slice_for(load(path_of(data_dir) / STORE), slot)
    except Exception as e:  # noqa: BLE001 — recorded, never a reason to refuse the step
        return {
            "knowledge": "",
            "knowledge_record": {
                "version": version_of(""), "entries": 0, "bytes": 0,
                "error": f"{type(e).__name__}: {e}",
            },
        }
    return {"knowledge": section, "knowledge_record": record}
