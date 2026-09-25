"""`coscc knowledge gather`: turns what earlier units measured into the knowledge store.

`0090_agents-relearn-what-earlier-units-already-knew` R9-R14. A command run by hand at a
terminal and nothing else: not a stage, unknown to `cos.mjs`, never called by the autopilot,
and no route starts it (`coscc/knowledge_cli_test.py` holds that). Each batch is one
tool-less session under the grant `knowledge`, run through `precedent.ask` — the one way a
tool-less session is run here — and whatever it says reaches the store only through
`knowledge.validate` and `knowledge.save`.

**It spends quota, and nobody knows how much before it runs** (`spec.md` C7). Without
`--yes` it only says how many sources, how many batches and the most they may cost. The
ceiling is checked by the CLI after the turn has run, so a batch can pass it; and a
`--all` that fails at its last batch has paid for every batch before and changed nothing.

Sources (R10) are every `spike.md` and `review.md` under `<data>/units/<slot>/.cos/<unit>/`,
every slot the app ever wrote, a workspace since removed from the list included. The text
given to the session has its `## Answers` cut by `runner.strip_answers`, and is named by
`<slot>/<unit>/<file>`, never by an absolute path.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from coscc import knowledge, units
from coscc.data import Data

# R12. Bytes of sources per batch. Chosen, not measured: a batch this size and a full store
# stay well under a model's context, and nobody has measured what a batch costs.
BATCH_BYTES = 65536

MODES = ("new", "all")
KIND = "knowledge"
REBUILT = "rebuilt by gather --all"
LOCK = ".lock"


class Refused(Exception):
    """A gather that must not start: exit 2, nothing spent."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sources(data_dir: str | os.PathLike[str] | None) -> list[dict[str, str]]:
    """R10. `[{label, slot, unit, file, text, sha}]`, by slot, unit, file. `text` is the file
    with its `## Answers` cut; `sha` is of that text, so an answer added later does not make
    a source new again (R11)."""
    from coscc.runner import strip_answers

    root = Data(data_dir).root / units.UNITS_DIR
    found: list[dict[str, str]] = []
    if not root.is_dir():
        return found
    for slot_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        cos = slot_dir / units.COS_DIR
        if not cos.is_dir():
            continue
        for unit_dir in sorted(p for p in cos.iterdir() if p.is_dir() and units.UNIT_RE.fullmatch(p.name)):
            for name in sorted(knowledge.SOURCE_FILES):
                path = unit_dir / name
                if not path.is_file():
                    continue
                try:
                    text = strip_answers(path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                found.append({
                    "label": f"{slot_dir.name}/{unit_dir.name}/{name}", "slot": slot_dir.name,
                    "unit": unit_dir.name, "file": name, "text": text, "sha": digest(text),
                })
    return found


def load_manifest(path: Path) -> dict[str, str]:
    """R11. `{label: sha}`; `{}` when there is none. A manifest that does not read is an
    error, not an empty one: reading it as empty would send every source again."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError(f"{path} is not a JSON object of label to sha256")
    return data


def batches(found: list[dict[str, str]], cap: int = BATCH_BYTES) -> list[list[dict[str, str]]]:
    """R12. By slot, then `(unit, file)`, filled up to `cap` bytes. A source over `cap` is a
    batch of its own and goes whole: cutting it would cut a measurement."""
    out: list[list[dict[str, str]]] = []
    for slot in sorted({s["slot"] for s in found}):
        current: list[dict[str, str]] = []
        used = 0
        for s in sorted((s for s in found if s["slot"] == slot), key=lambda s: (s["unit"], s["file"])):
            size = len(s["text"].encode("utf-8"))
            if current and used + size > cap:
                out.append(current)
                current, used = [], 0
            current.append(s)
            used += size
        if current:
            out.append(current)
    return out


def build_prompt(slot: str, max_id: int, current: str, batch: list[dict[str, str]]) -> str:
    """The session's whole input (spec Design 3): the grammar, the batch's workspace, the ids
    it may take, the part of the store that workspace receives, and the sources by label."""
    lines = [
        "You maintain a store of knowledge that earlier units of work measured, so the stages "
        "that plan the next unit do not measure it again. You have no tools and one turn; "
        "everything you may use is in this prompt.",
        "",
        "Below are the part of the store this workspace receives, and new sources: `spike.md` "
        "files, whose `## U<n>` sections record a measurement and its verdict, and `review.md` "
        "files, whose `Round <n>` findings `F<n>` record what a review found. Rewrite the store "
        "with what the sources measured:",
        "- One entry per behaviour, stated in general terms. Never retell one run: say what is "
        "true of the tool or the codebase, and under which versions.",
        "- Merge entries that say the same thing. Replace an entry when a newer source measured "
        "something different, and cite the newer source.",
        "- Keep only what a later unit could use to avoid measuring again. A finding that was "
        "fixed in the code is not knowledge unless the pitfall can recur.",
        "- Keep each entry under "
        f"{knowledge.ENTRY_BYTES} bytes, and the whole store you return under {knowledge.CAP_BYTES} bytes.",
        "",
        "Every entry has exactly this shape, in English:",
        "```",
        "## K<n>",
        "Scope: tool:<name>[ <versions>]   or   workspace:" + slot,
        "Source: <workspace>/<unit>/<spike.md|review.md> <anchor>",
        "Measured: YYYY-MM-DD",
        "<one or two sentences>",
        "```",
        f"- `workspace:` may only be `workspace:{slot}`, this batch's workspace. Use it for a "
        "fact about this codebase; use `tool:` for a fact about a tool, a library or a model.",
        "- `Source:` is repeated for each source, copied from a label below or from an entry "
        "already in the store, followed by `## U<n>` for a `spike.md` or `Round <n>` or "
        "`Round <n> F<n>` for a `review.md`. Cite nothing else.",
        "- `Measured:` is the date the source measured it.",
        f"- Keep an existing entry's id. A new entry takes a new id from K{max_id + 1} upward; "
        "an id is never used again, not even one you drop.",
        "- Every existing entry you remove or merge goes into `dropped`, with a one-line "
        "`reason`, and `merged_into` when it was merged.",
        "",
        "Reply with one JSON block and nothing else:",
        "```json",
        '{"store": "## K1\\nScope: ...\\n...", "dropped": [{"id": "K3", "reason": "...", "merged_into": "K1"}]}',
        "```",
        "`store` holds every entry you keep or add, and nothing else — no title, no header.",
        "",
        f"## The store this workspace receives (Max id: K{max_id})",
        "",
        current.strip() or "(empty)",
        "",
        "## Sources",
    ]
    for s in batch:
        lines += ["", f"### {s['label']}", "", s["text"].strip()]
    return "\n".join(lines) + "\n"


_JSON_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def read_reply(reply: str) -> tuple[str, list[dict[str, Any]], str]:
    """`(store, dropped, failure)`: the first fenced block, or the whole reply, that parses
    as `{"store": str, "dropped": [object]}`. `failure` is `""` when one did."""
    for raw in [m.group(1) for m in _JSON_BLOCK.finditer(reply or "")] + [reply or ""]:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("store"), str):
            continue
        dropped = data.get("dropped", [])
        if not isinstance(dropped, list) or not all(isinstance(d, dict) for d in dropped):
            return "", [], "the reply's `dropped` is not a list of objects"
        return data["store"], dropped, ""
    return "", [], "the reply holds no JSON object with a `store` string"


def _costs(end: dict[str, Any]) -> dict[str, Any]:
    cost = end.get("cost") or {}
    return dict(cost) if isinstance(cost, dict) else {}


class _Lock:
    """One gather at a time, per store: a second is refused, never queued."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / LOCK
        self.fd: int | None = None

    def __enter__(self) -> "_Lock":
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise Refused(f"another gather holds {self.path}; wait for it to end")
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)


def plan_of(data_dir: str | os.PathLike[str] | None, mode: str) -> dict[str, Any]:
    """What a gather would do: `{sources, batches, ceiling_usd}`, reading and spending nothing
    beyond the files."""
    from coscc.policy import grant_for

    if mode not in MODES:
        raise Refused(f"mode must be one of {', '.join(MODES)}")
    directory = knowledge.path_of(data_dir)
    found = sources(data_dir)
    if mode == "new":
        seen = load_manifest(directory / knowledge.SOURCES)
        found = [s for s in found if seen.get(s["label"]) != s["sha"]]
    parts = batches(found)
    return {
        "sources": found,
        "batches": parts,
        "ceiling_usd": round(len(parts) * float(grant_for("knowledge").max_budget_usd), 2),
    }


async def gather(
    data_dir: str | os.PathLike[str] | None,
    journal: Any,
    sessions: Any,
    model: str | None,
    mode: str,
    say: Callable[[str], None] = print,
) -> int:
    """Run every batch. `0` done (or nothing new), `1` a batch failed and says why, `2` refused.

    `journal` gets one `kind='knowledge'` row per batch that ran (R14). In `new` mode every
    batch that passes is written as it passes; in `all` mode nothing is written until every
    batch has passed, and then the store and the manifest are replaced whole (R7, R11)."""
    from coscc import precedent
    from coscc.policy import grant_for

    directory = knowledge.path_of(data_dir)
    with _Lock(directory):
        planned = plan_of(data_dir, mode)
        parts = planned["batches"]
        if not parts:
            say("coscc knowledge gather: no new or changed source; nothing to do")
            return 0
        store_path = directory / knowledge.STORE
        manifest_path = directory / knowledge.SOURCES
        try:
            text = knowledge.load(store_path)
        except FileNotFoundError:
            text = ""
        manifest = load_manifest(manifest_path)
        before = knowledge.parse(text)
        header = dict(before["header"])
        # `all` starts from nothing and keeps `Max id` (R7); `new` from the store as it is.
        entries = [] if mode == "all" else list(before["entries"])
        rebuilt = [{"id": f"K{e['id']}", "reason": REBUILT} for e in before["entries"]] if mode == "all" else []
        grant = grant_for("knowledge")
        cwd = str(directory)
        # The one session this process opens runs here, with no tool; nothing else is a member.
        if hasattr(sessions, "membership"):
            sessions.membership = lambda d: Path(d).expanduser().resolve() == directory.resolve()

        for i, batch in enumerate(parts, 1):
            slot = batch[0]["slot"]
            labels = [s["label"] for s in batch]
            mine = knowledge.for_workspace(entries, slot)
            others = [e for e in entries if e not in mine]
            old_slice = knowledge.entries_text(sorted(mine, key=lambda e: e["id"]))
            prompt = build_prompt(slot, header["max_id"], old_slice, batch)
            say(f"batch {i}/{len(parts)}: {slot}, {len(batch)} source(s)")
            reply, end, failure = await precedent.ask(sessions, cwd, prompt, grant, model, None)
            store, dropped, reason = "", [], failure
            if not reason:
                store, dropped, reason = read_reply(reply)
            if not reason:
                reasons = knowledge.validate(
                    old_slice, store, dropped, labels, slot, header["max_id"], others=others)
                reason = "; ".join(reasons)
            new = knowledge.parse(store)["entries"] if not reason else []
            after = sorted(others + new, key=lambda e: e["id"]) if not reason else entries
            row = {
                "kind": KIND, "workspace": slot, "mode": mode, "batch": i, "of": len(parts),
                "sources": len(batch), "labels": labels,
                "entries_before": len(before["entries"]) if (mode == "all" and i == 1) else len(entries),
                "entries_after": len(after),
                "dropped": [] if reason else (rebuilt if i == 1 else []) + dropped,
                "outcome": "failed" if reason else "done", "reason": reason,
                "session_id": str(end.get("session_id") or ""), "model": model or "",
                **_costs(end),
            }
            try:
                journal.append(row)
            except Exception as e:  # noqa: BLE001 — the money is spent; say so and go on
                say(f"batch {i}/{len(parts)}: its run-log row was not written ({type(e).__name__}: {e})")
            if reason:
                say(f"batch {i}/{len(parts)} failed, nothing written: {reason}")
                if not reason.startswith("the session") and reply:
                    say(f"the reply ended: {reply[-2000:]}")
                return 1
            entries = after
            header["max_id"] = max([header["max_id"]] + [e["id"] for e in new])
            if mode == "new":
                header = {**header, "version": header["version"] + 1, "gathered": _now()}
                knowledge.save(store_path, knowledge.render(header, entries))
                manifest.update({s["label"]: s["sha"] for s in batch})
                knowledge.save(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            say(f"batch {i}/{len(parts)} done: {len(entries)} entries, cost {row.get('cost_usd', 'unknown')}")

        if mode == "all":
            header = {**header, "version": header["version"] + 1, "gathered": _now()}
            knowledge.save(store_path, knowledge.render(header, entries))
            fresh = {s["label"]: s["sha"] for s in planned["sources"]}
            knowledge.save(manifest_path, json.dumps(fresh, indent=2, sort_keys=True) + "\n")
        say(f"coscc knowledge gather: {store_path} is version {header['version']}, {len(entries)} entries")
        return 0
