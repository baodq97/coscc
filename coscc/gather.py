"""`coscc knowledge gather`: turns what earlier units measured into the knowledge store.

`0090_agents-relearn-what-earlier-units-already-knew` R9-R14. A command run by hand at a
terminal and nothing else: not a stage, unknown to `cos.mjs`, never called by the autopilot,
and no route starts it (`coscc/knowledge_cli_test.py` holds that). Each batch is one
tool-less session under the grant `knowledge`, run through `precedent.ask` — the one way a
tool-less session is run here — and whatever it says reaches the store only through
`knowledge.validate` and `knowledge.save`.

**It spends quota, and nobody knows how much before it runs** (`spec.md` C7). Without
`--yes` it only says how many sources, how many batches and the most they may cost. The
ceiling is checked by the CLI after the turn has run, so a session can pass it; the run as a
whole is held to what it printed by what the sessions before cost (`0107` R7), so it can pass
that figure by one session's excess.

`0107`: a reply the check refuses is sent back to be repaired, at most `REPAIRS` times; and a
`--all` saves the store after every batch that passes and records how far it got in
`PROGRESS`, so the next `--all` goes on from the batch that failed instead of paying again
for the ones before it.

Sources (R10) are every `spike.md` and `review.md` under `<data>/units/<slot>/.cos/<unit>/`,
every slot the app ever wrote, a workspace since removed from the list included. The text
given to the session has its `## Answers` cut by `runner.strip_answers`, and is named by
`<slot>/<unit>/<file>`, never by an absolute path.

Since `0108`, what the session returns passes `admit.admit` after `validate`: the code writes
each entry's date and version, drops what it cannot read back from the run log or git, and
keeps the store at least half `tool:`. A gather that can read neither is refused (R12).
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

from coscc import admit, knowledge, units
from coscc.data import Data

# R12. Bytes of sources per batch. Chosen, not measured: a batch this size and a full store
# stay well under a model's context, and nobody has measured what a batch costs.
BATCH_BYTES = 65536

MODES = ("new", "all")
KIND = "knowledge"
REBUILT = "rebuilt by gather --all"
LOCK = ".lock"
# `0107` spec Design 3: how far an unfinished `--all` got, beside the store.
PROGRESS = "gather-all.json"

# `0107` R1. Repairs per batch, and how far under a limit the repair prompt aims. Both chosen;
# `0107` `spike.md ## U1` tried them on 3 trials of one batch with no other workspace: every
# trial was accepted within 2 repairs, and two accepted stores, at 7891 and 7730 bytes, landed
# above the 7372 aimed at and under 8192 only because of the margin — so keep the margin.
REPAIRS = 2
MARGIN = 0.10

# `0108` R9, said to the session as they are.
PROMPT_RULES = (
    "Prefer knowledge about tools and how to use a framework.",
    "Write a `workspace:` entry only for a pitfall the current code does not show. What the "
    "code says for itself, an agent can read.",
)


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


def load_progress(path: Path) -> dict[str, Any] | None:
    """`{started, done: {label: sha}, rebuilt_recorded}`; `None` when there is none. One that
    does not read is an error, as a manifest's is: read as none, the next `--all` would start
    over and pay again for every batch it had passed."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ValueError(f"{path} is not JSON ({e}); fix it, or delete it to start `gather --all` over") from e
    done = data.get("done") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or not isinstance(data.get("started"), str)
            or not isinstance(done, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in done.items())
            or not isinstance(data.get("rebuilt_recorded"), bool)):
        raise ValueError(f"{path} is not {{started, done: {{label: sha256}}, rebuilt_recorded}}; "
                         "fix it, or delete it to start `gather --all` over")
    return data


def save_progress(path: Path, progress: dict[str, Any]) -> None:
    knowledge.save(path, json.dumps(progress, indent=2, sort_keys=True) + "\n")


def batches(found: list[dict[str, str]], cap: int = BATCH_BYTES,
            dates: dict[str, dict[str, str]] | None = None) -> list[list[dict[str, str]]]:
    """R12. By slot, then newest date first (`0108` R2), then `(unit, file)`, filled up to
    `cap` bytes; a source `dates` does not date goes last. A source over `cap` is a batch of
    its own and goes whole: cutting it would cut a measurement."""
    out: list[list[dict[str, str]]] = []
    for slot in sorted({s["slot"] for s in found}):
        current: list[dict[str, str]] = []
        used = 0
        ordered = sorted((s for s in found if s["slot"] == slot), key=lambda s: (s["unit"], s["file"]))
        ordered.sort(key=lambda s: (dates or {}).get(s["label"], {}).get("date", ""), reverse=True)
        for s in ordered:
            size = len(s["text"].encode("utf-8"))
            if current and used + size > cap:
                out.append(current)
                current, used = [], 0
            current.append(s)
            used += size
        if current:
            out.append(current)
    return out


def _grammar(slot: str, max_id: int) -> list[str]:
    """The shape of an entry and the rules for its ids, as both prompts give them."""
    return [
        "Every entry has exactly this shape, in English:",
        "```",
        "## K<n>",
        "Scope: tool:<name>   or   workspace:" + slot,
        "Source: <workspace>/<unit>/<spike.md|review.md> <anchor>",
        "Ref: <path>[::<symbol>]",
        "<one or two sentences>",
        "```",
        f"- `workspace:` may only be `workspace:{slot}`, this batch's workspace. Use it for a "
        "fact about this codebase; use `tool:` for a fact about a tool, a library or a model.",
        "- `Scope: tool:<name>` carries no version, and a `tool:` entry has no `Ref:`.",
        "- A `workspace:` entry carries at least one `Ref:`: a file's path from the repository's "
        "root, optionally followed by `::` and a name that file holds. An entry whose `Ref:` is "
        "not on `main` is dropped.",
        "- Write no `Measured:` line and no version: the code writes the date from the run log "
        "and the version from the workspace's pins.",
        "- `Source:` is repeated for each source, copied from a label below or from an entry "
        "already in the store, followed by `## U<n>` for a `spike.md` or `Round <n>` or "
        "`Round <n> F<n>` for a `review.md`. Cite nothing else.",
        "- Copy a source's label without the parenthesis after it.",
        f"- Keep an existing entry's id. A new entry takes a new id from K{max_id + 1} upward; "
        "an id is never used again, not even one you drop.",
        "- Every existing entry you remove or merge goes into `dropped`, with a one-line "
        "`reason`, and `merged_into` when it was merged.",
    ]


_REPLY_SHAPE = [
    "Reply with one JSON block and nothing else:",
    "```json",
    '{"store": "## K1\\nScope: ...\\n...", "dropped": [{"id": "K3", "reason": "...", "merged_into": "K1"}]}',
    "```",
    "`store` holds every entry you keep or add, and nothing else — no title, no header.",
]


def _bytes(entries: list[dict[str, Any]]) -> int:
    """What `knowledge.validate` measures: the entries one blank line apart, in UTF-8."""
    return len(knowledge.entries_text(entries).encode("utf-8"))


def build_prompt(slot: str, max_id: int, current: str, batch: list[dict[str, str]],
                 dates: dict[str, dict[str, str]] | None = None) -> str:
    """The session's whole input (spec Design 3): the grammar, the batch's workspace, the ids
    it may take, the part of the store that workspace receives, and the sources by label,
    each with the date the run log gives it (`0108` R2, R9)."""
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
        "true of the tool or the codebase.",
        "- Merge entries that say the same thing. Replace an entry when a newer source measured "
        "something different, and cite the newer source.",
        "- Keep only what a later unit could use to avoid measuring again. A finding that was "
        "fixed in the code is not knowledge unless the pitfall can recur.",
        # `0107` R3: the size it starts from, measured as `validate` measures it.
        f"- The part of the store below is {len(current.encode('utf-8'))} bytes now; keep each "
        f"entry under {knowledge.ENTRY_BYTES} bytes, and the whole store you return under "
        f"{knowledge.CAP_BYTES} bytes.",
        *[f"- {rule}" for rule in PROMPT_RULES],
        "- Do not include anything a source itself says was not run, not measured or only "
        "derived, in any language. An entry, or a source, holding one of these is dropped: "
        + ", ".join(admit.MARKERS) + ".",
        "",
        *_grammar(slot, max_id),
        "",
        *_REPLY_SHAPE,
        "",
        f"## The store this workspace receives (Max id: K{max_id})",
        "",
        current.strip() or "(empty)",
        "",
        "## Sources",
    ]
    for s in batch:
        day = (dates or {}).get(s["label"], {}).get("date", "")
        lines += ["", f"### {s['label']} " + (f"(measured {day})" if day else "(measured: no date)"),
                  "", s["text"].strip()]
    return "\n".join(lines) + "\n"


def build_repair_prompt(slot: str, max_id: int, old_slice: str, store: str,
                        dropped: list[dict[str, Any]], reasons: list[str],
                        others: list[dict[str, Any]]) -> str:
    """`0107` R2: a refused reply sent back with every reason, the bytes it holds and the
    limits that bind it (spec Design 2). No source goes with it: the refused store already
    cites them, and its answer is checked against the batch's labels all the same."""
    kept = knowledge.parse(store)["entries"]
    tools = [e for e in kept if e["scope"].startswith("tool:")]
    held: dict[str, list[dict[str, Any]]] = {}
    for e in others:
        if e["scope"].startswith("workspace:"):
            held.setdefault(e["scope"], []).append(e)
    # `validate` adds each other workspace's own entries to these `tool:` ones, a blank line apart.
    limit = knowledge.CAP_BYTES
    tool_limit = limit - max((_bytes(own) + 2 for own in held.values()), default=0)
    old_ids = sorted(e["id"] for e in knowledge.parse(old_slice)["entries"])
    lines = [
        "You maintain a store of knowledge; you have no tools and one turn. Your previous reply "
        "was refused. Return the whole store again, and the whole `dropped` list, both counted "
        "against the store you were first given, not against your previous reply.",
        "",
        "Why it was refused, word for word:",
        *[f"- {r}" for r in reasons],
        "",
        f"The refused store's entries total {_bytes(kept)} bytes (UTF-8, one blank line apart); "
        f"its `tool:` entries total {_bytes(tools)} bytes.",
        f"Limits: the whole store at most {limit} bytes; its `tool:` entries at most {tool_limit} "
        f"bytes. Aim for at most {int(limit * (1 - MARGIN))} bytes for the whole store and "
        f"{int(tool_limit * (1 - MARGIN))} for its `tool:` entries: you cannot count bytes exactly.",
        f"Each entry stays under {knowledge.ENTRY_BYTES} bytes too, so merging entries into long "
        "ones is refused as well. Merge what says the same thing; shorten what says too much.",
        "",
        ("The store you were first given held " + ", ".join(f"K{n}" for n in old_ids)
         + ". Each of those you remove or merge goes into `dropped`; every other id is new and "
         "never goes into `dropped`.") if old_ids else
        "The store you were first given held no entry: every id is new, and `dropped` is empty.",
        "",
        *_grammar(slot, max_id),
        "",
        *_REPLY_SHAPE,
        "",
        "## The refused store",
        "",
        store.strip() or "(empty)",
        "",
        "## Its dropped list",
        "",
        json.dumps(dropped, ensure_ascii=False),
    ]
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


def read_store(directory: Path) -> tuple[str, dict[str, Any]]:
    """`(text, parsed)` of the store, `("", parse(""))` when there is none.

    A store holding a block `parse` skips is refused: every save renders only the entries it
    read, so gathering over it would delete what a person wrote (R5), and delete it without
    naming it in any `dropped` (R13)."""
    try:
        text = knowledge.load(directory / knowledge.STORE)
    except FileNotFoundError:
        text = ""
    parsed = knowledge.parse(text)
    if parsed["skipped"]:
        raise Refused(
            f"{directory / knowledge.STORE} holds blocks that cannot be read, and gathering would "
            "delete them: " + "; ".join(parsed["skipped"]) + " — fix or remove each by hand first")
    ids = [e["id"] for e in parsed["entries"]]
    twice = sorted({n for n in ids if ids.count(n) > 1})
    if twice:
        raise Refused(
            f"{directory / knowledge.STORE} holds " + ", ".join(f"K{n}" for n in twice)
            + " more than once; an id names one entry (R7) — renumber or merge them by hand first")
    if parsed["entries"] and not knowledge.has_header(text):
        # The header is the one record of ids given out and since dropped; without it a new
        # entry could take one of them again (R7).
        raise Refused(
            f"{directory / knowledge.STORE} holds entries but no `Version: … Max id: K<n>.` line, "
            "so the ids given out before cannot be known — put it back by hand first")
    return text, parsed


def dropped_max(journal: Any) -> int:
    """The highest id a `done` knowledge row of the run log names as dropped, `0` for none.

    A header edited below an id given out and since dropped cannot be caught from the store
    alone; this catches the ids a gather dropped. Rows under another `COS_WORKING_DIR` are
    not read, and an entry deleted by hand is in no row."""
    try:
        rows = journal.records(kind=KIND)
    except Exception as e:  # noqa: BLE001 — refused before anything is spent
        raise Refused(f"the run log cannot be read, so the ids dropped before are unknown: "
                      f"{type(e).__name__}: {e}") from e
    ids = [0]
    for row in rows:
        if row.get("outcome") != "done" or not isinstance(row.get("dropped"), list):
            continue
        for item in row["dropped"]:
            m = re.fullmatch(r"K?(\d+)", str(item.get("id") or "").strip()) if isinstance(item, dict) else None
            if m:
                ids.append(int(m.group(1)))
    return max(ids)


def plan_of(data_dir: str | os.PathLike[str] | None, mode: str, journal: Any = None) -> dict[str, Any]:
    """What a gather would do: `{sources, batches, ceiling_usd, store, dates, every, end_rows,
    resumed, passed, progress}`, reading and spending nothing beyond the files, the run log
    and git. `store` is `read_store`'s, so it refuses what that refuses.

    With `journal`, a run log that cannot be read or a `git` that cannot be run is refused
    here, before any session (`0108` R12): every entry of every batch would be dropped.
    `dates` covers every source, `every`, not only those sent.

    While a `--all` is unfinished (`0107` R5, R11), `new` is refused, and `all` leaves out
    every source its progress record passed at the sha it has now; `ceiling_usd` is then that
    of the batches left (R6)."""
    from coscc.policy import grant_for

    if mode not in MODES:
        raise Refused(f"mode must be one of {', '.join(MODES)}")
    directory = knowledge.path_of(data_dir)
    store = read_store(directory)
    end_rows: list[dict[str, Any]] = []
    if journal is not None:
        try:
            end_rows = journal.records(kind="end")
        except Exception as e:  # noqa: BLE001 — refused before anything is spent
            raise Refused(f"the run log cannot be read, so no source can be dated: "
                          f"{type(e).__name__}: {e}") from e
        if not admit.git_runs():
            raise Refused("git cannot be run, so no version and no Ref: can be read")
    every = sources(data_dir)
    dates = admit.date_sources(end_rows, every)
    found = every
    progress_path = directory / PROGRESS
    progress = None
    if mode == "new":
        if progress_path.exists():
            raise Refused(f"an unfinished `gather --all` holds {progress_path}; run `gather --all` to "
                          "finish it, or delete that file to start over")
        seen = load_manifest(directory / knowledge.SOURCES)
        found = [s for s in found if seen.get(s["label"]) != s["sha"]]
    else:
        progress = load_progress(progress_path)
        if progress is not None:
            found = [s for s in found if progress["done"].get(s["label"]) != s["sha"]]
    parts = batches(found, dates=dates)
    return {
        "sources": found,
        "batches": parts,
        "ceiling_usd": round(len(parts) * float(grant_for("knowledge").max_budget_usd), 2),
        "store": store,
        "dates": dates,
        "every": every,
        "end_rows": end_rows,
        "resumed": progress is not None,
        "passed": len(progress["done"]) if progress is not None else 0,
        "progress": progress,
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

    `journal` gets one `kind='knowledge'` row per session that ran (R14, `0107` R8). Every
    batch that passes is written as it passes, in both modes (`0107` R4). In `all` mode the
    store is rebuilt from nothing, keeping `Max id` (R7); the progress record says which
    sources passed, so a run that stops goes on from there when run again (`0107` R5), and
    the manifest is replaced only when the last batch has passed (`0107` R10).
    Its `dropped` holds what the session dropped and what `admit` dropped (`0108` R8)."""
    from coscc import precedent
    from coscc.policy import grant_for

    directory = knowledge.path_of(data_dir)
    with _Lock(directory):
        planned = plan_of(data_dir, mode, journal)
        parts = planned["batches"]
        resumed = planned["resumed"]
        store_path = directory / knowledge.STORE
        manifest_path = directory / knowledge.SOURCES
        progress_path = directory / PROGRESS
        _, before = planned["store"]
        header = dict(before["header"])

        def finish(progress: dict[str, Any], version: int, count: int) -> int:
            # `0107` R10. The manifest is every source of the rebuild, the runs before this one
            # included. Written before the record goes, so a process that dies between the
            # two finishes on the next run, spending nothing.
            knowledge.save(manifest_path, json.dumps(progress["done"], indent=2, sort_keys=True) + "\n")
            progress_path.unlink(missing_ok=True)
            say(f"coscc knowledge gather: {store_path} is version {version}, {count} entries")
            return 0

        if not parts:
            if resumed:
                # The last batch passed and the run stopped before it could clean up.
                return finish(planned["progress"], header["version"], len(before["entries"]))
            say("coscc knowledge gather: no new or changed source; nothing to do")
            return 0
        manifest = load_manifest(manifest_path)
        # A header edited down by hand is not the highest id given out; new ids start above
        # it, every id the store holds and every id a gather dropped (R7).
        header["max_id"] = max([header["max_id"], dropped_max(journal)] + [e["id"] for e in before["entries"]])
        # A new `all` starts from nothing and keeps `Max id` (R7); `new`, and an `all` going
        # on, from the store as it is. The entries it replaces are named once, with the
        # first save of the rebuild (`0107` R9).
        fresh = mode == "all" and not resumed
        entries = [] if fresh else list(before["entries"])
        rebuilt = [{"id": f"K{e['id']}", "reason": REBUILT} for e in before["entries"]] if fresh else []
        progress = planned["progress"] or {"started": _now(), "done": {}, "rebuilt_recorded": False}
        dates = {label: d["date"] for label, d in planned["dates"].items()}
        ctx = {
            "dates": planned["dates"], "texts": {s["label"]: s["text"] for s in planned["every"]},
            "workspaces": admit.workspaces(planned["end_rows"]), "git": admit.Reader(),
        }
        grant = grant_for("knowledge")
        cwd = str(directory)
        ceiling = planned["ceiling_usd"]
        spent = 0.0
        # The one session this process opens runs here, with no tool; nothing else is a member.
        if hasattr(sessions, "membership"):
            sessions.membership = lambda d: Path(d).expanduser().resolve() == directory.resolve()

        for i, batch in enumerate(parts, 1):
            slot = batch[0]["slot"]
            labels = [s["label"] for s in batch]
            mine = knowledge.for_workspace(entries, slot)
            others = [e for e in entries if e not in mine]
            old_slice = knowledge.entries_text(sorted(mine, key=lambda e: e["id"]))
            prompt = build_prompt(slot, header["max_id"], old_slice, batch, planned["dates"])
            say(f"batch {i}/{len(parts)}: {slot}, {len(batch)} source(s)")
            # What every session of this batch reported, repairs included, for its "done" line.
            paid_batch, unpaid_batch = 0.0, 0
            for attempt in range(1, REPAIRS + 2):
                # `0107` R7: no session opens that could take the run past what it printed.
                if spent + grant.max_budget_usd > ceiling:
                    say(f"batch {i}/{len(parts)}: no further session opened: ${spent:.2f} spent, and one "
                        f"more may cost ${grant.max_budget_usd:.2f}, past the ${ceiling:.2f} printed; "
                        "run it again to go on from this batch")
                    return 1
                reply, end, failure = await precedent.ask(sessions, cwd, prompt, grant, model, None)
                costs = _costs(end)
                paid = costs.get("cost_usd")
                spent += float(paid) if isinstance(paid, (int, float)) else float(grant.max_budget_usd)
                if isinstance(paid, (int, float)):
                    paid_batch += float(paid)
                else:
                    unpaid_batch += 1
                store, dropped, reason, reasons = "", [], failure, []
                if not reason:
                    store, dropped, reason = read_reply(reply)
                if not reason:
                    reasons = knowledge.validate(
                        old_slice, store, dropped, labels, slot, header["max_id"], others=others, dates=dates)
                    reason = "; ".join(reasons)
                new = knowledge.parse(store)["entries"] if not reason else []
                after, admitted = entries, []
                if not reason:
                    # Before `admit`, so an id it drops is never given out again (`0108` R8).
                    header["max_id"] = max([header["max_id"]] + [e["id"] for e in new])
                    after, admitted = admit.admit(new, others, ctx)
                row = {
                    "kind": KIND, "workspace": slot, "mode": mode, "batch": i, "of": len(parts),
                    "attempt": attempt, "resumed": resumed,
                    "sources": len(batch), "labels": labels,
                    "entries_before": len(before["entries"]) if rebuilt else len(entries),
                    "entries_after": len(after),
                    "dropped": [] if reason else rebuilt + dropped + admitted,
                    "outcome": "failed" if reason else "done", "reason": reason,
                    "session_id": str(end.get("session_id") or ""), "model": model or "",
                    **costs,
                }
                try:
                    journal.append(row)
                except Exception as e:  # noqa: BLE001 — the money is spent; say so and go on
                    say(f"batch {i}/{len(parts)}: its run-log row was not written ({type(e).__name__}: {e})")
                if not reason:
                    break
                # Only a reply that read and was refused is repaired; a broken session or a
                # reply with no JSON fails the batch as it did (`0107` spec Design 1).
                if not reasons or attempt > REPAIRS:
                    kept = "the batches before it are kept" if mode == "all" else "nothing written"
                    say(f"batch {i}/{len(parts)} failed, {kept}: {reason}")
                    if not reason.startswith("the session") and reply:
                        say(f"the reply ended: {reply[-2000:]}")
                    return 1
                say(f"batch {i}/{len(parts)}: refused, repair {attempt}/{REPAIRS}: {reason}")
                prompt = build_repair_prompt(slot, header["max_id"], old_slice, store, dropped, reasons, others)
            entries, rebuilt = after, []
            header = {**header, "version": header["version"] + 1, "gathered": _now()}
            # The store before the record of it (`0107` spec Design 3): the other way round,
            # a batch could be marked passed that the store does not hold.
            knowledge.save(store_path, knowledge.render(header, entries))
            if mode == "new":
                manifest.update({s["label"]: s["sha"] for s in batch})
                knowledge.save(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            else:
                progress = {**progress, "done": {**progress["done"], **{s["label"]: s["sha"] for s in batch}},
                            "rebuilt_recorded": True}
                save_progress(progress_path, progress)
            unpaid = f", and {unpaid_batch} that reported no cost" if unpaid_batch else ""
            say(f"batch {i}/{len(parts)} done: {len(entries)} entries, {attempt} session(s), "
                f"cost ${paid_batch:.2f}{unpaid}; ${spent:.2f} counted against the ${ceiling:.2f} printed")

        if mode == "all":
            return finish(progress, header["version"], len(entries))
        say(f"coscc knowledge gather: {store_path} is version {header['version']}, {len(entries)} entries")
        return 0
