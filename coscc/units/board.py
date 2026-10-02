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

import json
from pathlib import Path
from typing import Any

from coscc.loop import run
from coscc.units import guards

# Turns a hung child into an error rather than bounding the work (like `store.LOCK_TIMEOUT`).
TIMEOUT = 10.0


class Unavailable(Exception):
    """The board cannot be read, carrying a reason a caller can show verbatim."""


class Gate(tuple):
    """`gate`'s answer: `(open, what it said)`, and `reasons`, the codes beside the words,
    which the app branches on. `rebased`, `{reviewed, head}`, only when `ship` opened on a clean rebase."""

    reasons: tuple[str, ...]
    rebased: dict[str, str] | None

    def __new__(
        cls,
        opened: bool,
        said: str,
        reasons: tuple[str, ...] = (),
        rebased: dict[str, str] | None = None,
    ) -> "Gate":
        answer = super().__new__(cls, (opened, said))
        answer.reasons = tuple(reasons)
        answer.rebased = rebased
        return answer


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
EMPTY_STATE: dict[str, Any] = {"workspace": "", "workspaces": [], "units": {}, "ideas": {}}


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
    """Each `{ref, merged, why}` of the unit's `Depends on:`, as the loop resolved it."""
    return [
        {"ref": str(d.get("ref") or ""), "merged": d.get("merged"), "why": str(d.get("why") or "")}
        for d in u.get("dependsOn") or []
        if isinstance(d, dict)
    ]


def _ideas_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The store's ideas, `{id, title, status, units: [{ref, depends_on}], problems}`."""
    return [
        {
            "id": str(i.get("id") or ""),
            "title": str(i.get("title") or ""),
            "status": str(i.get("status") or ""),
            "units": [
                {
                    "ref": str(x.get("ref") or ""),
                    "depends_on": [str(d) for d in x.get("dependsOn") or []],
                }
                for x in i.get("units") or []
            ],
            "problems": [str(p) for p in i.get("problems") or []],
        }
        for i in data.get("ideas") or []
    ]


async def read(
    units_root: str | Path, timeout: float = TIMEOUT, state: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Every unit under `units_root`, each with its stages.

    `units_root` is the product's own store for the workspace, not the workspace itself.
    Raises `Unavailable` only when the answer is unknown (the loop cannot start, a child
    that failed or hung). A root with no `.cos/` is a known answer: no units.
    """
    path = Path(units_root)
    source, stdin = _source(state)
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

    stages = data.get("stages") or []
    # The stages whose answered draft runs again, as the loop lists them; the app keeps no copy.
    after_answers = [str(s) for s in data.get("afterAnswers") or []]
    units = [
        {
            "name": u.get("name", ""),
            "number": u.get("number"),
            "slug": u.get("slug"),
            "stages": _stage_rows(stages, u.get("artifacts") or {}),
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
            # The pull request `pr.md` names and the rounds `review.md` holds, verbatim, so the round a comment carries is the one the gate counted.
            "pr": _pr_of(u),
            "rounds": _rounds_of(u),
            # The findings the last review round confirmed need a person, each `{id, reason, answered}`.
            "person_findings": _person_findings_of(u),
            # Whether the unit sits between `pr` and `ship`; the integration step reads this, not `pr.md`.
            "between_pr_and_ship": bool(u.get("betweenPrAndShip")),
            # The ids `next` says a person is awaited on.
            "waiting": [str(x) for x in ((u.get("next") or {}).get("waiting") or [])],
            # The outcome deadline and the last valid `### Outcome` block, as the loop's `unitOutcome` read them.
            "outcome": _outcome_of(u),
            # The hold a person set (`{state, reason, by, date}`, or None) and the moves allowed from it.
            "hold": u.get("hold") or None,
            "hold_moves": [str(x) for x in u.get("holdMoves") or []],
            # Whether the unit used its review rounds with findings still open, and how many rounds a person granted it.
            "more_rounds": bool(u.get("moreRounds")),
            "rounds_granted": int(
                ((u.get("artifacts") or {}).get("review.md") or {}).get("roundsGranted") or 0
            ),
            # On each row too, so whoever holds one row from this read sees the same list.
            "after_answers": list(after_answers),
            # The idea the unit was opened from, its `Repo:`, and each dependency as the loop resolved it.
            "idea": str(u.get("idea") or ""),
            "repo": str(u.get("repo") or ""),
            "depends_on": _depends_on_of(u),
        }
        for u in data.get("units") or []
    ]

    return {
        "workspace": str(path),
        "stages": [s["name"] for s in stages],
        "after_answers": after_answers,
        "units": units,
        "ideas": _ideas_of(data),
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
        data = await read(empty, timeout, state=EMPTY_STATE)
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


async def pr_text(
    units_root: str | Path, unit: str, timeout: float = TIMEOUT, state: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Ask the loop's `pr-text` for the title and body `unit`'s `pr.md` puts on its pull request.

    Returns `{unit, title, body, url, scope, status}` on exit 0, and
    `{"error": <what the loop said>, "code": n}` otherwise (exit 1 is an answer, not a failure).
    Raises `Unavailable` as `read` does.
    """
    path = Path(units_root)
    try:
        source, stdin = _source(state)
        code, out_text, err_text = await _ask(
            ["--root", str(path), *source, "pr-text", unit], timeout, stdin
        )
    except TimeoutError:
        raise Unavailable(f"reading pr.md timed out after {timeout:.0f}s") from None
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


async def rerun(
    units_root: str | Path,
    unit: str,
    stage: str | None = None,
    timeout: float = TIMEOUT,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the loop's `rerun` which accepted stages of `unit` may run again, or, with `stage`,
    for the `### Rerun` block to append before running it.

    Returns `{unit, offers, why}` without `stage`, `{unit, stage, later, block}` with one, and
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
    """`questions` as the loop sent them, each with `by` added: who gave the answer in force, `""` when none."""
    out = []
    for q in unit.get("questions") or []:
        if not isinstance(q, dict):
            continue
        answer = _answer_of(unit, str(q.get("artifact") or ""), q.get("n"))
        out.append({**q, "by": str((answer or {}).get("by") or "")})
    return out


def _answers_of(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """`[{artifact, n, question, by, date, via, text, authority}]`, one per question with an
    answer in force, in the order of `artifacts`. `authority` is the app's; `""` where it gave none."""
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
                    "date": str(answer.get("date") or ""),
                    "via": str(answer.get("via") or ""),
                    "text": str(answer.get("text") or ""),
                    "authority": str(answer.get("authority") or ""),
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


_OUTCOME_FIELDS = (
    ("deadline", "deadline"),
    ("result", "result"),
    ("by", "by"),
    ("date", "date"),
    ("measured_by", "measuredBy"),
    ("source", "source"),
    ("reason", "reason"),
    ("note", "note"),
)


def _outcome_of(unit: dict[str, Any]) -> dict[str, Any] | None:
    """The loop's `unitOutcome`, keys in this file's snake_case, or None when it sent none."""
    o = unit.get("outcome")
    if not isinstance(o, dict):
        return None
    out: dict[str, Any] = {
        mine: (str(o[theirs]) if o.get(theirs) is not None else None)
        for mine, theirs in _OUTCOME_FIELDS
    }
    out["invalid"] = int(o.get("invalid") or 0)
    return out


def _pr_of(unit: dict[str, Any]) -> dict[str, Any] | None:
    """`{url, number}` from `pr.md`'s `PR:` line as the loop's `parsePr` read it, or None."""
    pr = ((unit.get("artifacts") or {}).get("pr.md") or {}).get("pr")
    if not isinstance(pr, dict) or not pr.get("url"):
        return None
    return {"url": str(pr["url"]), "number": pr.get("number")}


def _rounds_of(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """Each round of `review.md`: its number, verdict and text, as the loop split them.

    `findings` and `findings_open` are counted off `parseReview`'s own list; `dropped` and
    `unfinished` are carried as it set them.
    """
    review = ((unit.get("artifacts") or {}).get("review.md") or {}).get("review") or {}
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
                # What `coscc/units/prose_import.py` reads a round only the prose holds from, once.
                "reviewed": r.get("reviewed"),
                "found": [
                    {
                        "id": f.get("id"),
                        "label": f.get("label"),
                        "fixed_by": f.get("fixedBy"),
                        "text": f.get("text"),
                    }
                    for f in found
                ],
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
