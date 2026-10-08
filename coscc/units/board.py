"""The stages of a workspace's work units, read without a second parser.

The rules that decide what a status means live in `coscc/loop/`; this module runs
`python -m coscc.loop <command>` and reads its JSON rather than re-reading the Markdown.

**Which copy it runs is the security decision here.** A workspace is a cloned repository, so
any code in it is a file that repository controls; running it would hand that repo everything
this process has. This module runs the loop that ships with the app, with this process's own
interpreter and `-P` (a workspace's `coscc/` is never put on `sys.path`), pointed at the
workspace's `.cos/` with `--root` (`coscc/loop/run.py` starts it). The board reports; it never writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from coscc.agent import pack
from coscc.loop import run
from coscc.store.db import in_thread
from coscc.units import guards, states

# The stop `e`, and the board's reason, of a unit `next` reads as merging with no `ship` running.
SHIP_UNRECORDED = "ship requested a merge and recorded no outcome"

# Turns a hung child into an error rather than bounding the work (like `store.LOCK_TIMEOUT`).
TIMEOUT = 10.0


def open_questions(unit_row: dict[str, Any]) -> list[dict[str, Any]]:
    """Every unanswered question of the counted artifact (the loop's `unitQuestions`): the stop `a`,
    and the `questions` record a step that ends `done` leaves."""
    return [
        q for q in unit_row.get("questions") or [] if q.get("counted") and not q.get("answered")
    ]


class Unavailable(Exception):
    """The board cannot be read, carrying a reason a caller can show verbatim."""


class Gate(tuple):
    """`gate`'s answer: `(open, what it said)`, and `reasons`, the codes beside the words,
    which the app branches on. `rebased`, `{reviewed, head}`, only when `ship` opened on a clean rebase;
    `lane`, `fast` only when the loop says the unit is in the fast lane, else `full`; `passed`, the
    findings naming a UI-standard criterion that the loop let through, `[]` when none."""

    reasons: tuple[str, ...]
    rebased: dict[str, str] | None
    lane: str
    passed: list[dict[str, str]]

    def __new__(
        cls,
        opened: bool,
        said: str,
        reasons: tuple[str, ...] = (),
        rebased: dict[str, str] | None = None,
        lane: str = "full",
        passed: Sequence[Mapping[str, str]] = (),
    ) -> "Gate":
        answer = super().__new__(cls, (opened, said))
        answer.reasons = tuple(reasons)
        answer.rebased = rebased
        answer.lane = lane
        answer.passed = [dict(f) for f in passed]
        return answer


def _lane(data: dict[str, Any]) -> str:
    return "fast" if "fast-lane" in (data.get("via") or ()) else "full"


# What a finding the gate let through carries: the one the loop names it by, the criterion it was
# raised under, where it points, its sentence, and the reason code for letting it through.
PASSED_KEYS = ("id", "criterion", "path", "lines", "text", "why")


def _passed(data: dict[str, Any]) -> list[dict[str, str]]:
    """`passed` of the gate's JSON as text fields; `[]` when absent (an older loop) or malformed."""
    got = data.get("passed")
    if not isinstance(got, list):
        return []
    return [
        {k: str(f.get(k) if f.get(k) is not None else "") for k in PASSED_KEYS}
        for f in got
        if isinstance(f, dict)
    ]


def _rebased(data: dict[str, Any]) -> dict[str, str] | None:
    got = data.get("rebased")
    if not isinstance(got, dict) or not all(
        isinstance(got.get(k), str) and got.get(k) for k in ("reviewed", "head")
    ):
        return None
    return {"reviewed": got["reviewed"], "head": got["head"]}


def _codes(data: dict[str, Any]) -> tuple[str, ...]:
    """`reasons` of a `gate --json` or `next` answer. A code outside `guards.REASONS` is this app's bug, refused here."""
    codes = tuple(str(c) for c in data.get("reasons") or ())
    unknown = [c for c in codes if c not in guards.REASONS]
    if unknown:
        raise Unavailable(
            f"the loop handed out a reason code the app does not know: {', '.join(unknown)}"
        )
    return codes


def _stage_rows(stages: list[dict[str, Any]], artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per stage, in order, whether or not the artifact exists; an absent one is `not started`."""
    rows = []
    for stage in stages:
        entry = artifacts.get(stage["file"]) or {}
        rows.append(
            {
                "stage": stage["name"],
                "file": stage["file"],
                "status": entry.get("status") or "not started",
                "optional": bool(stage.get("optional")),
                "skip_reason": entry.get("skipReason"),
            }
        )
    return rows


async def _run(argv: list[str], timeout: float, stdin: str | None = None) -> tuple[int, str, str]:
    """One `python -m coscc.loop` invocation (`argv` is the loop's own): its exit code and both
    streams, decoded.

    The exit code is left to the caller: `read` treats non-zero as a failure, `gate` treats
    exit 1 as the answer ("blocked, and here are the reasons"). A child that cannot start
    raises `OSError`, one that does not answer in `timeout` seconds `TimeoutError`.
    """
    got = await run.ask(argv, stdin=stdin, timeout=timeout)
    return got.code, got.out, got.err


# The snapshot of a store with no units, for a question that needs none: `stages`.
EMPTY_STATE: dict[str, Any] = {"workspace": "", "workspaces": [], "units": {}}


def _source(state: dict[str, Any] | None) -> tuple[list[str], str | None]:
    """A unit's metadata is the app's snapshot (`coscc/units/meta.py`), handed to the loop on
    stdin as `--state -`. Without one, the deciding commands refuse with exit 2."""
    if state is None:
        return [], None
    return ["--state", "-"], json.dumps(state, ensure_ascii=False)


async def _ask(argv: list[str], timeout: float, stdin: str | None) -> tuple[int, str, str]:
    """`_run`, handed stdin only when there is a snapshot to hand it."""
    return await (_run(argv, timeout) if stdin is None else _run(argv, timeout, stdin))


def _depends_on_of(u: dict[str, Any]) -> list[dict[str, Any]]:
    """Each `{ref, merged, why}` of the unit's dependencies, as the loop resolved it."""
    return [
        {"ref": str(d.get("ref") or ""), "merged": d.get("merged"), "why": str(d.get("why") or "")}
        for d in u.get("dependsOn") or []
        if isinstance(d, dict)
    ]


# The last text `status --json` printed for a root, with the key of what it was asked and what it
# read: one entry per root, replaced by the next answer. A failed or unparsable answer is not held.
_HELD: dict[str, tuple[str, str]] = {}


def _tree(top: Path) -> list[tuple[str, int, int]]:
    """Each directory (by name) and file (by `mtime_ns` and size) under `top`, in a fixed order; a
    `top` that is not there is empty."""
    out: list[tuple[str, int, int]] = []
    for here, dirs, files in os.walk(top):
        dirs.sort()
        out.append((here, 0, -1))
        for name in sorted(files):
            full = os.path.join(here, name)
            try:
                st = os.stat(full)
            except OSError:
                out.append((full, -1, -1))
                continue
            out.append((full, st.st_mtime_ns, st.st_size))
    return out


# A file written this recently may be written again within the same tick of its clock, keeping
# its `mtime_ns` and size: no answer that read it is held (git's "racily clean").
_SETTLED_NS = 2_000_000_000


def _read_key(path: Path, stdin: str | None) -> str | None:
    """What the child's `status` answer is a function of: the snapshot on stdin, every file under
    the root's `.cos/`, the built-in pack it reads (a new install under a running app changes it),
    and the one environment variable `harness.child_env` passes down; None while a file under
    `.cos/` changed within `_SETTLED_NS`. Run off the event loop."""
    cos = _tree(path / ".cos")
    since = time.time_ns() - _SETTLED_NS
    if any(mtime > since for _, mtime, _ in cos):
        return None
    seen = (
        stdin,
        os.environ.get("COS_REVIEW_ROUNDS"),
        cos,
        _tree(pack.BUILTIN),
    )
    return hashlib.sha256(repr(seen).encode()).hexdigest()


async def read(
    units_root: str | Path,
    timeout: float = TIMEOUT,
    state: dict[str, Any] | None = None,
    hold: bool = True,
) -> dict[str, Any]:
    """Every unit under `units_root`, each with its stages.

    `units_root` is the product's own store for the workspace, not the workspace itself.
    Raises `Unavailable` only when the answer is unknown (the loop cannot start, a child
    that failed or hung). A root with no `.cos/` is a known answer: no units.

    The loop's last answer for the root is held and given again, with no child started, while
    its key is the same (`_read_key`); every call parses the held text afresh, so what a caller
    changes in its answer is not in the next. `hold=False`, or a file just written, neither reads
    nor keeps one.
    """
    path = Path(units_root)
    source, stdin = _source(state)
    key = await in_thread(_read_key, path, stdin) if hold else None
    held = _HELD.get(str(path)) if key is not None else None
    out_text = held[1] if held and held[0] == key else None
    if out_text is None:
        try:
            code, out_text, err_text = await _ask(
                ["--root", str(path), *source, "status", "--json"], timeout, stdin
            )
        except TimeoutError:
            raise Unavailable(f"reading the board timed out after {timeout:.0f}s") from None
        except (OSError, ValueError) as e:
            raise Unavailable(f"could not run coscc.loop: {e}") from e

        if code != 0:
            raise Unavailable((err_text or out_text).strip() or f"the loop exited {code}")

    try:
        data = json.loads(out_text)
    except (json.JSONDecodeError, ValueError) as e:
        raise Unavailable(f"the loop did not return JSON: {e}") from e
    if key is not None:
        _HELD[str(path)] = (key, out_text)

    stages = data.get("stages") or []
    # The loop sends the stages of a process other than the default one under its ref.
    others = data.get("processes") or {}
    # The stages whose answered draft runs again, as the loop lists them; the app keeps no copy.
    after_answers = [str(s) for s in data.get("afterAnswers") or []]
    units = [
        {
            "name": u.get("name", ""),
            "number": u.get("number"),
            "slug": u.get("slug"),
            # The process the unit walks, `<pack>/<name>`, as its row records it.
            "process": str(u.get("process") or ""),
            # The rows of the unit's own process.
            "stages": _stage_rows(
                others.get(str(u.get("process") or "")) or stages, u.get("artifacts") or {}
            ),
            # Carried through rather than recomputed.
            "next": (u.get("next") or {}).get("action", ""),
            # The same answer as a stage name, read off the files alone, for the card's mode badge.
            "next_stage": str((u.get("next") or {}).get("stage") or ""),
            "blocked": bool((u.get("next") or {}).get("blocked")),
            # The stage whose column the unit sits in, and which rule of `decide` answered `next`.
            "at": str(u.get("at") or ""),
            "why": str((u.get("next") or {}).get("why") or ""),
            "problems": u.get("problems") or [],
            # `pre-intent` or `started`, decided by the loop's `readUnit`.
            "phase": u.get("phase") or "",
            # The intent's `Type:`, as the loop read it.
            "type": str(u.get("type") or ""),
            # Which items under `## Open questions` a person has answered, and how many are still open; copied, never recounted.
            "questions": _questions_of(u),
            # Every answer in force, off `artifacts[*].questions[].answer`.
            "answers": _answers_of(u),
            "open": int(u.get("open") or 0),
            "counted": u.get("counted") or "",
            # The pull request the app recorded and the review rounds, verbatim, so the round a comment carries is the one the gate counted.
            "pr": _pr_of(u),
            "rounds": _rounds_of(u),
            # The findings the last review round confirmed need a person, each `{id, reason, answered}`.
            "person_findings": _person_findings_of(u),
            # Whether the unit sits between `pr` and `ship`; the integration step reads this, not `pr.md`.
            "between_pr_and_ship": bool(u.get("betweenPrAndShip")),
            # The ids `next` says a person is awaited on.
            "waiting": [str(x) for x in ((u.get("next") or {}).get("waiting") or [])],
            # The hold a person set (`{state, reason, by, date}`, or None) and the moves allowed from it.
            "hold": u.get("hold") or None,
            "hold_moves": [str(x) for x in u.get("holdMoves") or []],
            # Whether the unit used its review rounds with findings still open, and how many rounds a person granted it.
            "more_rounds": bool(u.get("moreRounds")),
            "rounds_granted": int(
                ((u.get("artifacts") or {}).get(states.first_file(kind="review")) or {}).get(
                    "roundsGranted"
                )
                or 0
            ),
            # On each row too, so whoever holds one row from this read sees the same list.
            "after_answers": list(after_answers),
            # The idea the unit was opened from, and each dependency as the loop resolved it.
            "idea": str(u.get("idea") or ""),
            "depends_on": _depends_on_of(u),
        }
        for u in data.get("units") or []
    ]

    return {
        "workspace": str(path),
        "stages": [s["name"] for s in stages],
        "after_answers": after_answers,
        "units": units,
        "count": len(units),
        # A healthy workspace can hold no units; that is not a failed read.
        "empty_because": None if units else _why_empty(path),
    }


async def stages(timeout: float = TIMEOUT) -> list[str]:
    """The stage names the loop defines, in its order, with no workspace needed.

    Asks the loop at an empty temporary root: `status --json` sends the stage list whether
    or not any unit exists. Raises `Unavailable` as `read` does.
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="coscc-stages-") as empty:
        data = await read(empty, timeout, state=EMPTY_STATE, hold=False)
    return list(data["stages"])


# The `review` gate asks GitHub for the pull request's checks, so this call waits on a network.
GATE_TIMEOUT = 30.0


async def gate(
    units_root: str | Path,
    unit: str,
    stage: str,
    repo: str | Path | None = None,
    timeout: float = GATE_TIMEOUT,
    state: dict[str, Any] | None = None,
) -> Gate:
    """Ask `python -m coscc.loop gate --json` whether one stage of one unit may proceed.

    `repo` is the git checkout the unit's code lives in, passed as `--repo`; it is not
    `units_root`, which has no git. Without it the `review` and `ship` gates stay closed.

    Returns `(open, what it said)`, with the codes as `.reasons` (`Gate`). Exit 0 is open;
    exit 1 is blocked and carries the reasons; exit 2 is misuse, reported with what the
    loop printed.
    """
    path = Path(units_root)
    try:
        source, stdin = _source(state)
        argv = ["--root", str(path), *source, "gate", unit, stage, "--json"]
        if repo is not None:
            argv += ["--repo", str(Path(repo).expanduser().resolve())]
        code, out_text, err_text = await _ask(argv, timeout, stdin)
    except TimeoutError:
        raise Unavailable(f"asking the gate timed out after {timeout:.0f}s") from None
    except (OSError, ValueError) as e:
        raise Unavailable(f"could not run coscc.loop: {e}") from e

    if code in (0, 1):
        try:
            data = json.loads(out_text)
            lines = [str(line) for line in data["lines"]]
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            raise Unavailable(f"the loop did not return JSON: {e}") from e
        said = "\n".join(lines).strip()
        return Gate(
            code == 0,
            said or f"the gate exited {code} and said nothing",
            _codes(data),
            _rebased(data),
            _lane(data),
            _passed(data),
        )
    said = (out_text + err_text).strip()
    return Gate(False, said or f"the gate exited {code} and said nothing")


async def next_step(
    units_root: str | Path,
    unit: str,
    repo: str | Path | None = None,
    timeout: float = GATE_TIMEOUT,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the loop's `next` which one stage the run button may offer for `unit`.

    Returns `{"stage": <name or "">, "action": <why>, "blocked": <bool>}`; nothing here reads
    `action` to decide anything. `repo` is passed as `--repo` as `gate` does. It costs up to
    two `gh` calls, so only the open unit asks and `read` does not.
    """
    path = Path(units_root)
    try:
        source, stdin = _source(state)
        argv = ["--root", str(path), *source, "next", unit]
        if repo is not None:
            argv += ["--repo", str(Path(repo).expanduser().resolve())]
        code, out_text, err_text = await _ask(argv, timeout, stdin)
    except TimeoutError:
        raise Unavailable(f"asking what comes next timed out after {timeout:.0f}s") from None
    except (OSError, ValueError) as e:
        raise Unavailable(f"could not run coscc.loop: {e}") from e

    if code != 0:
        raise Unavailable((err_text or out_text).strip() or f"the loop exited {code}")
    try:
        data = json.loads(out_text)
    except (json.JSONDecodeError, ValueError) as e:
        raise Unavailable(f"the loop did not return JSON: {e}") from e
    return {
        "unit": str(data.get("unit") or unit),
        "stage": str(data.get("stage") or ""),
        "action": str(data.get("action") or ""),
        "blocked": bool(data.get("blocked")),
        # Present only when a person is awaited.
        "waiting": [str(x) for x in data.get("waiting") or []],
        # Present only when the last review round left out an earlier finding.
        "dropped": [str(x) for x in data.get("dropped") or []],
        # Present only when the unit is held.
        "hold": data.get("hold") or None,
        # Present only when a draft's questions are all answered; only the autopilot reads it.
        "rerun": str(data.get("rerun") or ""),
        # Present only when `impl` left its file a draft asking nothing; only the autopilot reads it.
        "continue": str(data.get("continue") or ""),
        # `dependency` only when `impl` waits on a unit not merged; else "".
        "why": str(data.get("why") or ""),
        # The codes of what settled the answer; the autopilot reads these, never `action`.
        "reasons": list(_codes(data)),
    }


async def screens(
    units_root: str | Path,
    unit: str,
    repo: str | Path,
    timeout: float = GATE_TIMEOUT,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the loop's `screens` whether `unit`'s screenshots in `repo` must be taken again.

    `repo` is the unit's worktree. Returns `{unit, ui, manifest, rewritten, retake, why}` as
    the loop printed it. Any exit but 0 raises `Unavailable`. It asks `git` only, never `gh`.
    """
    path = Path(units_root)
    try:
        source, stdin = _source(state)
        argv = [
            "--root",
            str(path),
            *source,
            "screens",
            unit,
            "--repo",
            str(Path(repo).expanduser().resolve()),
        ]
        code, out_text, err_text = await _ask(argv, timeout, stdin)
    except TimeoutError:
        raise Unavailable(f"asking about the screenshots timed out after {timeout:.0f}s") from None
    except (OSError, ValueError) as e:
        raise Unavailable(f"could not run coscc.loop: {e}") from e

    if code != 0:
        raise Unavailable((err_text or out_text).strip() or f"the loop exited {code}")
    try:
        return json.loads(out_text)
    except (json.JSONDecodeError, ValueError) as e:
        raise Unavailable(f"the loop did not return JSON: {e}") from e


async def rerun(
    units_root: str | Path,
    unit: str,
    stage: str | None = None,
    timeout: float = TIMEOUT,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the loop's `rerun` which accepted stages of `unit` may run again, or, with `stage`,
    which records running it makes stale.

    Returns `{unit, offers, why}` without `stage`, `{unit, stage, later, stale}` with one, and
    `{"error": <what the loop said>, "code": n}` when it exits non-zero (exit 1 is "not
    offered", an answer). Takes no `--repo`. Raises `Unavailable` as `read` does.
    """
    path = Path(units_root)
    try:
        source, stdin = _source(state)
        argv = ["--root", str(path), *source, "rerun", unit] + ([stage] if stage else [])
        code, out_text, err_text = await _ask(argv, timeout, stdin)
    except TimeoutError:
        raise Unavailable(f"asking what may run again timed out after {timeout:.0f}s") from None
    except (OSError, ValueError) as e:
        raise Unavailable(f"could not run coscc.loop: {e}") from e

    if code != 0:
        return {
            "error": (err_text or out_text).strip() or f"the loop exited {code}",
            "code": code,
        }
    try:
        return json.loads(out_text)
    except (json.JSONDecodeError, ValueError) as e:
        raise Unavailable(f"the loop did not return JSON: {e}") from e


def _answer_of(unit: dict[str, Any], artifact: str, n: Any) -> dict[str, Any] | None:
    for q in ((unit.get("artifacts") or {}).get(artifact) or {}).get("questions") or []:
        if isinstance(q, dict) and q.get("n") == n and isinstance(q.get("answer"), dict):
            return q["answer"]
    return None


def _questions_of(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """`questions` as the loop sent them, each with the answer in force's `by` and `name`, `""`
    when none."""
    out = []
    for q in unit.get("questions") or []:
        if not isinstance(q, dict):
            continue
        answer = _answer_of(unit, str(q.get("artifact") or ""), q.get("n")) or {}
        out.append(
            {
                **q,
                "recommendation": str(q.get("recommendation") or ""),
                "by": str(answer.get("by") or ""),
                "name": str(answer.get("name") or ""),
            }
        )
    return out


def _answers_of(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """`[{artifact, n, question, by, name, date, via, text}]`, one per question with an answer in
    force, in the order of `artifacts`. `by` is `person` or `delegated`, as the answer was sent."""
    out = []
    for artifact, a in (unit.get("artifacts") or {}).items():
        for q in (a or {}).get("questions") or []:
            answer = q.get("answer") if isinstance(q, dict) and q.get("answered") else None
            if not isinstance(answer, dict):
                continue
            out.append(
                {
                    "artifact": str(artifact),
                    "n": q.get("n"),
                    "question": str(q.get("text") or ""),
                    "by": str(answer.get("by") or ""),
                    "name": str(answer.get("name") or ""),
                    "date": str(answer.get("date") or ""),
                    "via": str(answer.get("via") or ""),
                    "text": str(answer.get("text") or ""),
                }
            )
    return out


def _person_findings_of(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """`[{id, reason, answered}]` as the loop's `readUnit` put them in `personFindings`."""
    out = []
    for p in unit.get("personFindings") or []:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        out.append(
            {
                "id": str(p["id"]),
                "reason": str(p.get("reason") or ""),
                "answered": bool(p.get("answered")),
            }
        )
    return out


def _pr_of(unit: dict[str, Any]) -> dict[str, Any] | None:
    """`{url, number}` of the pull request the app recorded, as the loop read it, or None."""
    pr = ((unit.get("artifacts") or {}).get(states.first_file(action="open-pr")) or {}).get("pr")
    if not isinstance(pr, dict) or not pr.get("url"):
        return None
    return {"url": str(pr["url"]), "number": pr.get("number")}


def _rounds_of(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """Each review round: its number, verdict and text, as the loop built them from the rows.

    `findings` and `findings_open` are counted off the round's own list; `dropped` and
    `unfinished` are carried as the loop set them.
    """
    review = ((unit.get("artifacts") or {}).get(states.first_file(kind="review")) or {}).get(
        "review"
    ) or {}
    out = []
    for r in review.get("rounds") or []:
        if not isinstance(r, dict):
            continue
        found = [f for f in r.get("findings") or [] if isinstance(f, dict)]
        out.append(
            {
                "n": r.get("n"),
                "verdict": r.get("verdict"),
                "text": r.get("text") or "",
                "findings": len(found),
                "findings_open": sum(1 for f in found if f.get("label") == "open"),
                # The ids an impl may claim only a person can close, off the same list.
                "open_ids": [str(f.get("id")) for f in found if f.get("label") == "open"],
                "dropped": [str(x) for x in r.get("dropped") or []],
                "unfinished": bool(r.get("unfinished")),
                "screens": r.get("screens") if isinstance(r.get("screens"), dict) else None,
            }
        )
    return out


def _why_empty(path: Path) -> str:
    if not path.exists():
        return f"no such directory: {path}"
    if not (path / ".cos").exists():
        return "this workspace has no .cos/ — nothing here runs the loop yet"
    return "the .cos/ directory is there but holds no work units"


# -- the state a card shows ------------------------------------------------------------


def attention_reason(unit: dict[str, Any]) -> str:
    """What a unit waits on, `""` when it waits on nothing named here. The board's state is
    `unit_state`'s."""
    # `next`'s code, never its words. `dependency` is the one `why` whose words began `waiting`.
    why = str(unit.get("why") or "")
    rows = unit.get("stages") or []
    if unit.get("phase") == "pre-intent" or why in ("finished", "rejected"):
        return ""
    if not (
        unit.get("problems") or any(r.get("status") in ("draft", "changes-requested") for r in rows)
    ):
        return ""
    waiting = any(not p.get("answered") for p in unit.get("person_findings") or [])
    if unit.get("problems") or waiting or why == "dependency":
        return "Needs a person"
    draft = next((r for r in rows if r.get("status") == "draft"), None)
    if draft is not None:
        # A `ship.md` a refused merge left is worked by `next`, not accepted; accepting it
        # reads the unit as finished with its pull request open.
        if unit.get("why") == "ship-refused":
            return ""
        # A merge asked for and not recorded.
        if unit.get("why") == "ship-merging":
            return SHIP_UNRECORDED
        # A fact, not an order: no button accepts a draft; its stage's next run does.
        return f"{draft.get('stage')}.md is a draft"
    return "Changes requested"


# The nine states and their labels, in the order their rules are tried. No label reads as
# approval: `Done` comes from `next.why = finished` alone. `running` and `starting` are only
# laid over (`shown_state`), never decided by `unit_state`.
STATE_LABEL = {
    "done": "Done",
    "dropped": "Dropped",
    "paused": "Paused",
    "running": "Running",
    "starting": "Starting",
    "needs-you": "Needs you",
    "error": "Error",
    "awaiting": "Awaiting CI/merge",
    "ready": "Ready",
}
# One colour per state, none shared, in place of the lane colours.
STATE_COLOR = {
    "done": "grass",
    "dropped": "bronze",
    "paused": "plum",
    "running": "iris",
    "starting": "blue",
    "needs-you": "amber",
    "error": "red",
    "awaiting": "cyan",
    "ready": "gray",
}
# The states `Running` is never laid over, and the reason is never shown beside.
COLLAPSED_STATES = ("done", "paused", "dropped")
# The states the board folds into a closed group at its foot rather than a stage lane: a
# paused unit stays in its lane, since it waits on a person.
FOLDED_STATES = ("done", "dropped")


def _state(state: str, label: str = "") -> dict[str, str]:
    return {"state": state, "label": label or STATE_LABEL[state], "color": STATE_COLOR[state]}


def paused_label(p: Mapping[str, Any]) -> str:
    """What a card says of a run held at a ceiling: which ceiling, and how much of it was spent."""
    if p.get("ceiling") == "turns":
        return f"Paused at {p.get('turns')} of {p.get('max_turns')} turns"
    return f"Paused at ${float(p.get('usd') or 0):.2f} of ${float(p.get('max_usd') or 0):.2f}"


def unit_state(
    unit: dict[str, Any], last_end: dict[str, Any] | None, ci: dict[str, Any] | None
) -> dict[str, str]:
    """The one state `Core.board` decides for a unit: rules 1-3 and 5-8.

    `unit` is the board's dict with `integration` attached; `last_end` the unit's latest ended
    run-log row; `ci` the held answer of `integrate.required_checks` for the pull request's
    head, or None. `Running` is the page's to lay over this (`shown_state`).
    """
    why = str(unit.get("why") or "")
    hold = (unit.get("hold") or {}).get("state")
    if why == "finished":
        return _state("done")
    if hold == "dropped" or why == "rejected":
        if why == "rejected":
            stage = next(
                (r.get("stage") for r in unit.get("stages") or [] if r.get("status") == "rejected"),
                "",
            )
            return _state("dropped", f"Dropped — {stage} rejected")
        return _state("dropped")
    if hold == "paused":
        return _state("paused")
    if unit.get("paused"):
        return _state("needs-you", paused_label(unit["paused"]))
    if int(unit.get("open") or 0) > 0 or why in ("needs-person", "awaits-person"):
        return _state("needs-you")
    # The buckets `integrate.classify` reads as red. A held answer that is `gh`'s error has no
    # `checks`, and reads as not read.
    red = [
        str(c.get("name") or "")
        for c in (ci or {}).get("checks") or []
        if c.get("bucket") in ("fail", "cancel")
    ]
    ended = last_end.get("outcome") if last_end and last_end.get("stage") == unit.get("at") else ""
    # A `session-limit` end is a wait for the account's reset, never an error.
    failed = ended == "failed"
    # `ship-merging` with no `ship` running is a merge nothing will record; `shown_state` lays
    # `Running` over it while one runs.
    if (
        unit.get("problems")
        or why in ("unreadable", "ship-merging")
        or failed
        or red
        or (unit.get("integration") or {}).get("state") == "red-after-integration"
    ):
        return _state("error")
    # `impl` waits on another unit's merge: a wait, not a stage to run.
    if why == "dependency":
        return _state("awaiting", "Awaiting a dependency")
    # A `review` or `ship` made stale by a rerun waits on CI as a missing one does; a stale
    # `pr.md` in the window is a stage to run, not a wait.
    due = why == "missing" or (
        why == "stale"
        and unit.get("at")
        in states.states_where(kind="review") + states.states_where(action="merge")
    )
    # Only a wait on CI, a merge or a dependency is `awaiting`: a ship gate shut for another reason
    # (`review-incomplete`) has a stage to run.
    if unit.get("between_pr_and_ship") and due:
        return _state("awaiting")
    if ended == "session-limit":
        return _state("ready", "Paused at the session limit")
    return _state("ready")


def shown_state(
    decided: dict[str, Any], running_rows: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """`Running` while `Board.running` lists a `running` or `ending` attempt of the unit, and
    `Starting` while it lists only `queued` or `preparing` ones, below rules 1-3 and above the
    rest. `running_rows` is that answer's `running` entry for the unit; a row's `state` is its
    attempt's."""
    if not running_rows or decided.get("state") in COLLAPSED_STATES:
        return decided
    if all(r.get("state") in ("queued", "preparing") for r in running_rows):
        return _state("starting")
    return _state("running")
