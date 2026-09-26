"""The only place business logic lives.

`spec.md` R10: the page and the JSON API are two entry points to one capability, and two
implementations of one capability is the surest way to have one of them fixed and the other
not. So neither an HTTP route nor a Reflex event handler may decide anything — they
translate a request into a call here, and a result back into their own shape.

The rule that makes this checkable: nothing in this module imports a web framework, and
nothing above it branches on business state. A conditional in a route is a bug in this
file, not in the route.

`Invalid` is how this layer refuses. Callers map it to their own vocabulary — 400 for
HTTP, an error banner for the page — and neither gets to invent a different reason.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from coscc import agents, autopilot, backlog
from coscc import board as board_reader
from coscc import drift, events, fetches, gitops
from coscc import harness, integrate, knowledge
from coscc import hold as hold_rules
from coscc import present, prcomment, priorfindings, prsync, retake, spend
from coscc import precedent as precedent_mod
from coscc import sessions as reader
from coscc.board import Unavailable
from coscc.config import Config
from coscc.data import Data, now as _now
from coscc.gitops import GitError
from coscc.history import UNKNOWN, BadTransition, History, settled_edits
from coscc.journal import (
    COST_FIELDS,
    COST_USD,
    BadRecord,
    Busy,
    Journal,
    add_cost,
    last_runs,
    timelines_of,
    totals_of,
    zero_cost,
)
from coscc.policy import GRANTS, NOVEL_CEILINGS, PROSE_STAGES, TERMINAL_ONLY, grant_for, grant_for_step
from coscc import labels, models
from coscc.run import LOOPBACK
from coscc.runner import (
    CEILING_MARKERS, SESSIONS_PER_STEP, STATUS_RE, Denials, RunError, Runner, answers_section, describe_attempt,
    permission_gate,
)
from coscc.sessions import Sessions, StepHandle
from coscc.store import BadName, Store, require_name
from coscc import steps as steps_mod
from coscc import units, updater as updater_mod, worktrees
from coscc.units import BadUnit, CannotCreate

# The eight stage names, in stage order. Taken from the stage list the board reports rather
# than written again here would be better; the board read is async and this method is not,
# so the names are repeated and this comment is the warning.
STAGE_FILES = ("idea", "intent", "spec", "plan", "impl", "pr", "review", "ship")

# Where a unit's branch is cut from: the trunk as this remote has it. Constants, not
# request fields — a caller cannot point the fetch at another remote or another branch.
BRANCH_REMOTE = "origin"
BRANCH_TRUNK = gitops.TRUNK

# `0054` R6. The longest note a rerun takes, in characters. Chosen by the spec, not measured.
RERUN_NOTE_MAX = 4000

# `0111` R5. What the page says when a retake of the screenshots refuses `review`: one
# sentence, no commit, path or log line (S1, S3, S6). The rest is in the `screens` record.
RETAKE_REFUSED = "The screenshots could not be taken again after the branch was rewritten, so review did not start."


def _answers_kept(path: Path, before: bytes) -> bool:
    """`0054` R8. Whether `path` still ends with the `## Answers` section it had, `before`,
    byte for byte. A `pr` step writes `pr.md` itself, so nothing else guards that section."""
    try:
        return path.read_bytes().endswith(before)
    except OSError:
        return False


# `0051` spec, answer 4: an `ended, unknown` row stops being shown this long after it began,
# unless a later `start` of the same unit retired it first.
UNKNOWN_END_FOR = timedelta(hours=24)


class Invalid(Exception):
    """A request this layer refuses, carrying a reason a caller can show verbatim."""


class Updating(Invalid):
    """`0068` R11: refused because the app is in the seconds before it restarts. A 503."""


class NotUpdatable(Invalid):
    """`0068` R2: this install is not the shape an update can be applied to. A 409."""


class StaleCutList(Invalid):
    """`0068` R10: the list a person confirmed is not the list running now."""

    def __init__(self, message: str, listing: dict[str, Any]):
        super().__init__(message)
        self.listing = listing


def _as_invalid(e: updater_mod.Refused) -> Invalid:
    if isinstance(e, updater_mod.Stale):
        return StaleCutList(str(e), e.listing)
    if isinstance(e, updater_mod.Updating):
        return Updating(str(e))
    if isinstance(e, updater_mod.NotHere):
        return NotUpdatable(str(e))
    return Invalid(str(e))


def _whole_at_least_one(value: Any) -> bool:
    """`max_parallel`: an int, not a bool, 1 or more (`0043` R2)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _positive_number(value: Any) -> bool:
    """`daily_cap_usd`: a finite number above 0, not a bool (`0043` R2)."""
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value > 0
    )


def _younger_than(at: str, oldest: datetime) -> bool:
    """Whether a run-log `at` is after `oldest`. One that will not parse is not shown."""
    try:
        when = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when > oldest


def _attach_comment_state(units_: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    """`0021` D4. Give every review round a `comment`: on the pull request, or not and why.

    Read off the run log, never stored beside the round: a `posted` or `already` row for
    the round means it is there. Anything else -- including a round written at a terminal,
    which has no row at all -- is *not on the PR*, with the latest failure's reason if any.
    """
    posted: dict[tuple[str, Any], str] = {}
    failed: dict[tuple[str, Any], str] = {}
    for r in records:
        k = (str(r.get("unit") or ""), r.get("round"))
        if r.get("outcome") in ("posted", "already"):
            posted[k] = str(r.get("comment_url") or "")
        elif r.get("outcome") == "failed":
            failed[k] = str(r.get("detail") or "")
    for u in units_:
        for rnd in u.get("rounds") or []:
            k = (u["name"], rnd.get("n"))
            rnd["comment"] = (
                {"posted": True, "url": posted[k], "reason": None}
                if k in posted
                else {"posted": False, "url": "", "reason": failed.get(k)}
            )


def _attach_precedent(units_: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    """`0044` R10. On each question: `by_jera`, its `cites` and the words it `said` when the
    answer in force is Jera's, and — while it is still unanswered — `needs_person`,
    `proposal` and `reason` from the last `precedent` row for it. Display only: `cos.mjs` never sees any of this (R9)."""
    last: dict[tuple[str, str, Any], dict[str, Any]] = {}
    for r in rows:
        last[(str(r.get("unit") or ""), str(r.get("artifact") or ""), r.get("n"))] = r
    for unit in units_:
        answers = {(a["artifact"], a["n"]): a for a in unit.get("answers") or []}
        for q in unit.get("questions") or []:
            jera = bool(q.get("answered")) and precedent_mod.is_jera(q.get("by"))
            said = answers.get((q.get("artifact"), q.get("n"))) or {}
            row = last.get((unit["name"], str(q.get("artifact") or ""), q.get("n"))) or {}
            waiting = not q.get("answered") and row.get("verdict") == precedent_mod.PERSON
            q["by_jera"] = jera
            q["cites"] = precedent_mod.cites_of(str(said.get("text") or "")) if jera else []
            q["said"] = precedent_mod.words_of(str(said.get("text") or "")) if jera else ""
            q["needs_person"] = waiting
            q["proposal"] = str(row.get("text") or "") if waiting else ""
            q["reason"] = str(row.get("reason") or "") if waiting else ""


def step_cwd(stage: str, work: str, directory: Path, spike_dir: str | None = None) -> str:
    """Where a step's session runs. The unit's worktree, except for `ship` and `spike`.

    `spike` (`0039` R11) runs in `spike_dir`, a throwaway directory under the data root:
    its probe code must never land in the worktree whose branch it would then ride.

    `ship` runs `gh pr merge --squash --delete-branch`, and inside a worktree that command
    fails after it has already merged. Measured 2026-09-23 on `baodq97/coscc-proof` with gh
    2.93.0, `main` checked out at the root and the branch in a worktree: the pull request
    went to `MERGED`, then gh tried to switch the worktree to `main`, git answered
    `fatal: 'main' is already used by worktree`, and gh exited 1 -- with the remote branch
    and the local branch both left behind. A step reading that exit code reports a failed
    merge for a pull request that merged.

    The same command with the pull request's URL, run from a directory that is not a git
    checkout, exited 0, merged, and deleted the remote branch. The unit's directory in the
    store is such a directory -- the store has no git (`coscc/units.py`) -- and `ship` writes
    `ship.md` there anyway. The worktree and the local branch are then removed by
    `worktrees.remove_if_finished`, which already waits for GitHub to say `MERGED`.

    The gates still read `work`: only the session moves.
    """
    if stage == "spike" and spike_dir:
        return spike_dir
    return str(directory) if stage == "ship" else work


def describe_base(base: dict[str, Any] | None) -> str:
    """The one sentence saying a step's base may be stale, or `""` when it is fresh.

    `0030_a-unit-branch-starts-from-a-stale-main`. `state.py` and this module's own prompt
    (`runner.build_prompt`, *The base this step runs on*) both call this rather than each
    writing the sentence its own way — the same reason `.claude/CLAUDE.md` gives for
    `cos.mjs` being the one place the loop is defined, at a much smaller scale.
    """
    if not base or base.get("fresh", True):
        return ""
    sha = base.get("sha") or "?"
    ref = base.get("ref") or f"{BRANCH_REMOTE}/{BRANCH_TRUNK}"
    reason = base.get("reason") or ""
    return f"This step ran on {ref} at {sha}, which may be stale: {reason}"


def integration_since_review(journal: Journal, key: str, unit: str) -> dict[str, Any] | None:
    """`0035` R10: the latest `pushed` integration recorded after the last `review` step
    that ended `done`, or None. Read by id order, which is the order the rows were written."""
    try:
        rows = journal.records(key, unit)
    except Busy:
        return None
    found = None
    for rec in rows:
        if rec.get("kind") == "integration" and rec.get("outcome") == "pushed":
            found = rec
        elif rec.get("kind") == "end" and rec.get("stage") == "review" and rec.get("outcome") == "done":
            found = None
    return found


# `0047`. The three results a `### Outcome` block may carry, as a person types them, and the
# word `cos.mjs` `parseOutcome` reads each one as.
OUTCOME_RESULTS = {"đạt": "met", "trượt": "missed", "không đo được": "unmeasurable"}
# `0047` spec, Answers, câu 5: a line for the board to show on a missed outcome, no action.
MISSED_HINT = "cân nhắc bỏ hoặc làm lại"


def outcome_label(outcome: dict[str, Any] | None, today: date, finished: bool = False) -> dict[str, Any] | None:
    """`0047` R8. What the board shows for one unit's outcome, or None for no label.

    `outcome` is what `board._outcome_of` copied from `cos.mjs`; nothing here reads a block.
    `today` is a parameter so the deadline branch is testable without a clock. `counted` is
    whether the unit has a result in the sense of the intent's outcome: `không đo được` is
    shown on its own but is not one (`0047` intent, Answers, câu 3). `form` is whether the
    board offers to record one — only on a finished unit, the one `record_outcome` accepts.
    """
    if not outcome:
        return None
    deadline = outcome.get("deadline")
    result = outcome.get("result")
    if result == "met":
        kind, text, color, counted = "met", "đạt", "grass", True
    elif result == "missed":
        kind, text, color, counted = "missed", "trượt", "red", True
    elif result == "unmeasurable":
        kind, text, color, counted = "unmeasurable", "không đo được", "amber", False
    elif not deadline:
        return None
    elif date.fromisoformat(deadline) <= today:
        kind, text, color, counted = "due", "tới hạn — chưa đo", "amber", False
    else:
        kind, text, color, counted = "pending", "chưa tới hạn", "gray", False
    return {
        "kind": kind,
        "text": text,
        "color": color,
        "counted": counted,
        "hint": MISSED_HINT if kind == "missed" else "",
        "deadline": deadline,
        "by": outcome.get("by"),
        "date": outcome.get("date"),
        "measured_by": outcome.get("measured_by"),
        "source": outcome.get("source"),
        "reason": outcome.get("reason"),
        "note": outcome.get("note"),
        "invalid": int(outcome.get("invalid") or 0),
        "form": bool(finished),
        # `0082` D22: what the page shows. `text` and `kind` stay as they were for the API.
        "label": present.OUTCOME_LABEL[kind],
        "hint_label": MISSED_HINT_LABEL if kind == "missed" else "",
    }


# `0082` R3, `spec.md ## Answers, câu 1`. The word every record that used to carry a typed
# name gets when the request names nobody. It is not an identity: the one password of
# `0070` names nobody, so this says only that someone holding it or a live session acted.
OWNER = "owner"

# `0082` D22. `MISSED_HINT` in the page's language; the stored word is unchanged.
MISSED_HINT_LABEL = "Consider dropping or redoing it."

# `0082` R8, `spec.md ## Answers, câu 5`. The one sentence the page keeps beside each action
# whose effect costs money or leaves this machine. The full warnings stay in `policy.py`,
# in the API under their old fields, and in `.claude/rules/coscc-app.md`.
CONSEQUENCE = {
    "run": "Runs a real Claude session and spends account quota.",
    "pr": "Pushes and opens a pull request with this machine's gh login, and spends quota.",
    "ship": "Merges the pull request with this machine's gh login, and spends quota.",
    "integrate": "Rebases this pull request with this machine's gh login; a conflict opens a paid session.",
    "estimate": "Opens one paid session that proposes estimates.",
    "precedent": "Opens one paid session; its answers reach later stages as decided.",
    "drop": "Closes this unit's open pull request with this machine's gh login.",
    "apply-now": "Stops every running step and chat turn, then restarts the app.",
}


def consequence(stage: str) -> str:
    """The sentence for running `stage`, `CONSEQUENCE["run"]` when it has none of its own."""
    return CONSEQUENCE.get(stage, CONSEQUENCE["run"])


def answerable(unit: dict[str, Any]) -> bool:
    """`0082` R11. Whether the board invites an answer on this unit: not once it is finished,
    closed or dropped. The answer route itself is unchanged."""
    action = str(unit.get("next") or "")
    dropped = (unit.get("hold") or {}).get("state") == "dropped"
    return not (action == "finished" or action.startswith("closed") or dropped)


def attention_reason(unit: dict[str, Any]) -> str:
    """`0082` R12. What a unit waits on, `""` when it waits on nothing named here.

    Its condition is the rule of the lane *Needs you* had until `0100`, kept so the words
    stay those `0082` wrote (`0100` R12). The board's state is `unit_state`'s, not this;
    since `0100` this shows in the unit's dialog beside it (spec C3), through
    `reason_beside`."""
    action = str(unit.get("next") or "")
    rows = unit.get("stages") or []
    if unit.get("phase") == "pre-intent" or action == "finished" or action.startswith("closed"):
        return ""
    if not (unit.get("problems") or any(r.get("status") in ("draft", "changes-requested") for r in rows)):
        return ""
    waiting = any(not p.get("answered") for p in unit.get("person_findings") or [])
    if unit.get("problems") or waiting or action.startswith("waiting"):
        return "Needs a person"
    draft = next((r for r in rows if r.get("status") == "draft"), None)
    if draft is not None:
        return f"Accept {draft.get('stage')}.md"
    return "Changes requested"


# `0100` R3, R9. The eight states and their labels, in the order their rules are tried. No
# label reads as approval (R13): `Done` comes from `next.why = finished` alone.
STATE_LABEL = {
    "done": "Done",
    "dropped": "Dropped",
    "paused": "Paused",
    "running": "Running",
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
    "needs-you": "amber",
    "error": "red",
    "awaiting": "cyan",
    "ready": "gray",
}
# The states the board folds into a closed group at its foot rather than a stage column (R8).
COLLAPSED_STATES = ("done", "paused", "dropped")

# `0100` R6. Seconds a held CI answer is trusted before a board read asks `gh` again, in the
# background. Chosen, not measured.
CI_REFRESH = 60.0


def _state(state: str, label: str = "", ci: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"state": state, "label": label or STATE_LABEL[state], "color": STATE_COLOR[state], "ci": ci}


def unit_state(
    unit: dict[str, Any], last_end: dict[str, Any] | None, ci: dict[str, Any] | None
) -> dict[str, Any]:
    """`0100` R3, R5. The one state `Service.board` decides for a unit: rules 1–3 and 5–8.

    `unit` is the board's dict with `integration` attached; `last_end` the unit's latest
    ended run-log row, from the read `board` already made; `ci` the held answer of
    `integrate.required_checks` for the pull request's head as it is now, or None. Rule 4,
    `Running`, is the page's to lay over this (`shown_state`). `ci` in the answer is what
    the dialog's CI line says (R7), None where there is no line to show.
    """
    why = str(unit.get("why") or "")
    hold = (unit.get("hold") or {}).get("state")
    if why == "finished":
        return _state("done")
    if hold == "dropped" or why == "rejected":
        if why == "rejected":
            stage = next((r.get("stage") for r in unit.get("stages") or [] if r.get("status") == "rejected"), "")
            return _state("dropped", f"Dropped — {stage} rejected")
        return _state("dropped")
    if hold == "paused":
        return _state("paused")
    if int(unit.get("open") or 0) > 0 or why in ("needs-person", "awaits-person"):
        return _state("needs-you")
    # R5(d). The buckets `integrate.classify` reads as red (`coscc/integrate.py:52`). A held
    # answer that is `gh`'s error has no `checks`, and reads as not read (R7).
    red = [str(c.get("name") or "") for c in (ci or {}).get("checks") or [] if c.get("bucket") in ("fail", "cancel")]
    line = None
    if ci is not None:
        line = {"read": "checks" in ci, "red": red, "at": str(ci.get("at") or "")}
    failed = (
        last_end is not None
        and last_end.get("outcome") in ("failed", "exhausted")
        and last_end.get("stage") == unit.get("at")
    )
    if (
        unit.get("problems") or why == "unreadable" or failed or red
        or (unit.get("integration") or {}).get("state") == "red-after-integration"
    ):
        return _state("error", ci=line if red else None)
    # `0054`. A `review` or `ship` made stale by a rerun waits on CI as a missing one does
    # (`cos.mjs` `nextStep`); a stale `pr.md` in the window is a stage to run, not a wait.
    due = why == "missing" or (why == "stale" and unit.get("at") in ("review", "ship"))
    if unit.get("between_pr_and_ship") and due:
        return _state("awaiting", ci=line or {"read": False, "red": [], "at": ""})
    return _state("ready")


def shown_state(decided: dict[str, Any], running_rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    """`0100` Design 5. Rule 4: `Running` while `Service.running` lists a session of the unit,
    below rules 1–3 and above the rest. `running_rows` is that answer's `running` entry for
    the unit — never `unknown_end` (spec, Out of scope)."""
    if running_rows and decided.get("state") not in COLLAPSED_STATES:
        return _state("running")
    return decided


def reason_beside(reason: str, state: str) -> str:
    """`0100` review F1. `attention_reason` as the dialog shows it beside the state shown,
    `""` where the two would disagree: any reason beside a collapsed state, and "Needs a
    person" beside any state but *Needs you* (`intent.md ## Constraints`). Its words stay
    those `0082` wrote (R12)."""
    if state in COLLAPSED_STATES or (reason == "Needs a person" and state != "needs-you"):
        return ""
    return reason


# `0082` R9. The release channel's state in plain words; `{v}` is the offered version.
_RELEASE_LINE = {
    "ready": "Version {v} is ready to apply.",
    "up-to-date": "This is the latest release.",
    "off": "Release checks are off.",
    "unavailable": "Releases could not be checked.",
    "downloading": "Downloading version {v}.",
    "error": "The last download failed.",
    "blocked": "Updates are held after a failed update.",
}
_LOCAL_LINE = {
    "unconfigured": "Local builds are not set up.",
    "ready": "A local build ({v}) is ready to apply.",
    "building": "A local build is running.",
    "error": "The last local build failed.",
    "blocked": "Local builds are held after a failed update.",
}


def update_words(status: dict[str, Any]) -> dict[str, Any]:
    """`0082` R9. What the Updates section says and which buttons it shows, from
    `Updater.status`. A button that could not be used is not listed; nothing names an
    environment variable."""
    if status.get("shape") != "service":
        return {"line": "Updates apply only to an install made by install.sh.", "local_line": "", "actions": []}
    state = status.get("state") or ""
    if state == "applying":
        return {"line": "Updating now.", "local_line": "", "actions": []}
    release, local = status.get("release") or {}, status.get("local") or {}
    rs, ls = release.get("state") or "", local.get("state") or ""
    line = _RELEASE_LINE.get(rs, "").format(v=release.get("version") or "")
    local_line = _LOCAL_LINE.get(ls, "").format(v=local.get("version") or "")
    actions: list[str] = []
    if state == "pending":
        line = "An update waits for the running work to finish."
        actions.append("cancel")
    else:
        for channel, ready in (("release", rs == "ready"), ("local", ls == "ready")):
            if ready:
                actions += [f"apply-{channel}", f"now-{channel}"]
    if ls not in ("unconfigured", "building", "blocked", ""):
        actions.append("build-local")
    return {"line": line, "local_line": local_line, "actions": actions}


@dataclass
class Service:
    config: Config
    sessions: Sessions
    store: Store | None = field(default=None, init=False)
    # `0016`. Held across read-check-append so two answers arriving together cannot
    # interleave their blocks. The page and the API share this instance (`state.py`
    # takes `API.state.service`), so one lock covers both.
    _answer_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    # `0021`. Held across read-comments-then-post, so two presses of *Post to PR* for one
    # round run one after the other and the second finds the first's marker. One process
    # only, like `pull` (`.claude/rules/coscc-app.md`).
    _comment_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    # `0111` R3. Held across one retake of a unit's screenshots, for the whole app: every
    # capture binds `127.0.0.1:18783` (`scripts/capture_screens.py:112`), so two at once fail.
    # A capture a session runs does not take it (spec C1). One process only, like `pull`.
    _screens_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    # `0111` review round 1, F3. The retake running now, if any, `{workspace, unit, started}`
    # by an id that never leaves this process: read only by `_update_jobs`.
    _retakes: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    # `0017` R8. Per workspace, created on first use.
    _create_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)
    # `0035` R12. `(journal key, unit)` for every step, integration or hold holding its unit
    # now, and one lock per workspace held across an integration's check-and-mark. Since
    # `0050` each holds a `Mark` saying what and since when, and a step takes its own before
    # its first `await` (`_take`). One process only, like `pull`.
    _active: dict[tuple[str, str], steps_mod.Mark] = field(default_factory=dict, init=False, repr=False)
    _integrate_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)
    # `0051` R1. What is running now, for the board to show: one entry per step or
    # integration, keyed by an id that never leaves this process. Added and removed beside
    # `_active`, read only by `running`. Display only: `_active` still does the refusing.
    _running: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    # `0100` R6. By `(journal key, unit)`: the last answer of `integrate.required_checks`,
    # `{head, checks | error, at}`, and the one background ask running for it. Memory only,
    # gone on a restart, and never waited on by a board read. One process only, like `pull`.
    _ci: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _ci_asks: dict[tuple[str, str], asyncio.Task] = field(default_factory=dict, init=False, repr=False)
    # `0034`. The board steps running now, each as its own task, so a reader that goes
    # away does not take the step with it and a Stop has something to cancel.
    steps: steps_mod.Registry = field(default_factory=lambda: steps_mod.Registry(), init=False, repr=False)
    # `0073`. The recorder of every board step running in this process, by `run`: what
    # `events_page` reads and `follow_events` subscribes to. A step leaves it when `_drive`
    # ends, after its recorder has written what it holds; from then the tables answer.
    _recorders: dict[str, events.Recorder] = field(default_factory=dict, init=False, repr=False)
    # `0043`. The autopilot, per journal key: the lock every pass holds (the spec's
    # *Tuần tự hóa*), the poll loop of a workspace that has it on, the workspace directory
    # it was turned on for, the reader of each step it started, the stops the last pass
    # found by unit, and the passes scheduled but not yet run. One process only, like `pull`.
    _autopilot_locks: dict[str, asyncio.Lock] = field(default_factory=dict, init=False, repr=False)
    _autopilot_tasks: dict[str, asyncio.Task] = field(default_factory=dict, init=False, repr=False)
    _autopilot_cwd: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _autopilot_runs: dict[str, dict[str, tuple[str, asyncio.Task]]] = field(default_factory=dict, init=False, repr=False)
    _autopilot_stops: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict, init=False, repr=False)
    _autopilot_pending: set[asyncio.Task] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        # No working folder means no store, and the app behaves as it did before one existed.
        # That is what keeps `scripts/verify_0001.py` running unchanged (`spec.md` R6).
        self.store = (
            Store(self.config.working_dir, self.config.data_dir)
            if self.config.working_dir
            else None
        )
        # One question, asked in two places. See `Sessions.membership`.
        self.sessions.membership = self._is_member
        # `0068`. Told of every step, integration and chat turn that ends (R9).
        self.updater = updater_mod.Updater(self.config, self)
        self.sessions.on_turn_end = self.updater.job_ended

    # -- workspaces ---------------------------------------------------------

    def workspaces(self) -> dict[str, Any]:
        """Both sources, with the count the app could not answer before the store existed.

        `source` is carried per entry rather than merged away: an env workspace cannot be
        renamed or removed from here, and a caller has to be able to tell.
        """
        rows: list[dict[str, Any]] = []
        for path in self.config.workspaces:
            rows.append(
                {
                    "name": Path(path).name,
                    "path": path,
                    "label": "",
                    "source": "env",
                    "missing": not Path(path).expanduser().is_dir(),
                }
            )
        if self.store is not None:
            for entry in self.store.entries():
                target = self.store.path_of(entry.name)
                rows.append(
                    {
                        "name": entry.name,
                        "path": str(target),
                        "label": entry.label,
                        "source": "store",
                        "missing": not target.is_dir(),
                    }
                )
        return {
            "working_dir": self.config.working_dir,
            "count": len(rows),
            "workspaces": rows,
            # Kept so the original shape still reads: it only ever asked for paths.
            "paths": [r["path"] for r in rows],
        }

    # -- changing the list --------------------------------------------------

    def _store_or_refuse(self) -> Store:
        if self.store is None:
            raise Invalid(
                "no working folder configured; set COS_WORKING_DIR and restart "
                "(it is deliberately not settable over HTTP)"
            )
        return self.store

    def _name_or_refuse(self, name: str) -> str:
        try:
            return require_name(name)
        except BadName as e:
            raise Invalid(str(e)) from e

    def _row(self, name: str, label: str) -> dict[str, Any]:
        store = self._store_or_refuse()
        target = store.path_of(name)
        return {
            "name": name,
            "path": str(target),
            "label": label,
            "source": "store",
            "missing": not target.is_dir(),
        }

    async def add_workspace(
        self, name: str, label: str = "", repo_url: str | None = None
    ) -> dict[str, Any]:
        """Add by adopting a directory already under the root, or by cloning into it.

        Both are `intent.md`'s scope line, where "thêm" and "clone" are separate entries.

        Order matters and is the whole of `spec.md` R16: clone into a temp directory,
        rename into place, and only then write the store. The worst state a failure can
        leave is a temp directory nobody cleaned — never a listed workspace that does not
        work.
        """
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        if any(e.name == name for e in store.entries()):
            raise Invalid(f"workspace already exists: {name}")

        target = store.path_of(name)
        if repo_url:
            if target.exists():
                raise Invalid(f"directory already exists: {target}")
            store.working_dir.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(dir=store.working_dir, prefix=".cos-clone-"))
            try:
                await gitops.clone(repo_url, staging / name)
                os.replace(staging / name, target)
            except GitError as e:
                raise Invalid(str(e)) from e
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        elif not target.is_dir():
            raise Invalid(f"no such directory under the working folder: {target}")

        entry = store.add(name, label)
        return self._row(entry.name, entry.label)

    def set_label(self, name: str, label: str) -> dict[str, Any]:
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        try:
            entry = store.set_label(name, label)
        except KeyError as e:
            raise Invalid(f"no such workspace: {name}") from e
        return self._row(entry.name, entry.label)

    def remove_workspace(self, name: str) -> dict[str, Any]:
        """Drops the entry only. The directory stays — `spec.md` R18 and C6."""
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        try:
            store.remove(name)
        except KeyError as e:
            raise Invalid(f"no such workspace: {name}") from e
        return {"removed": name, "count": len(self.workspaces()["workspaces"])}

    async def pull_workspace(self, name: str) -> dict[str, Any]:
        """Fast-forward only. A failure comes back with its output — `spec.md` R20.

        Refused outright while a session is live here (`spec.md` R6). The refusal is an
        `Invalid` like every other reason a pull fails, so it reaches the page through the
        path R20 already built rather than through one of its own — R8.
        """
        store = self._store_or_refuse()
        self._name_or_refuse(name)
        if not any(e.name == name for e in store.entries()):
            raise Invalid(f"no such workspace: {name}")
        target = store.path_of(name)
        if not target.is_dir():
            raise Invalid(f"workspace directory is missing: {target}")
        # R6, and this has to come before `gitops`: a fast-forward rewrites files
        # under a turn that is already reading them, and the turn cannot be told. The
        # answer covers this process only (`spec.md` C2) — a second app holding a session
        # here is not seen, and the pull will go ahead.
        live = self.sessions.live_in(str(target))
        if live:
            raise Invalid(
                f"workspace {name} has {len(live)} live session(s) — "
                "pull would change files under them. Finish or reload, then try again."
            )
        try:
            output = await gitops.pull(target)
        except GitError as e:
            raise Invalid(str(e)) from e
        return {"name": name, "output": output}

    # -- board --------------------------------------------------------------

    def _journal(self) -> Journal | None:
        """The run log, or `None` when there is no working folder to keep it in.

        Unset `COS_WORKING_DIR` and the app behaves as it did before the store — which now also means
        the board is read-only: there is nowhere to record a mode, so every step reads
        `manual` and nothing can be started. That is the safe direction to fail in.
        """
        return (
            Journal(self.config.working_dir, self.config.data_dir)
            if self.config.working_dir
            else None
        )

    # `0050`. Check-and-mark with no `await` in any of these, so nothing on the event loop
    # can come between the look and the write.
    def _busy(self, key: str, unit: str) -> str:
        """What holds this unit, in the one sentence every refusal carries, or `""`."""
        mark = self._active.get((key, unit))
        return steps_mod.describe(unit, mark) if mark is not None else ""

    def _take(self, key: str, unit: str, kind: str, stage: str = "") -> steps_mod.Mark:
        said = self._busy(key, unit)
        if said:
            raise Invalid(said)
        mark = steps_mod.Mark(kind, stage, "preparing" if kind == "step" else "")
        self._active[(key, unit)] = mark
        return mark

    def _release(self, key: str, unit: str, mark: steps_mod.Mark) -> None:
        """Only this mark: a refused or late caller never frees a unit someone else holds."""
        if self._active.get((key, unit)) is mark:
            del self._active[(key, unit)]

    @staticmethod
    def _journal_key(cwd: str) -> str:
        """How a workspace is named in the journal.

        The resolved path, not a store name: an env-declared workspace has no name at all
        (`config.is_workspace`), and a path is the one identifier both kinds have. The
        cost is that moving a workspace detaches its history from it.
        """
        return str(Path(cwd).expanduser().resolve())

    def _app_identity(self) -> dict[str, str]:
        """`0094` R13: the running build's version and commit, for a step's `start` row.

        `Updater.me` is `update.identity`, computed once and kept. Anything failing is two
        empty strings, which `verify_0094 --measure` counts apart; it never stops a step.
        """
        try:
            me = self.updater.me()
            return {"version": str(me.get("version") or ""), "commit": str(me.get("commit") or "")}
        except Exception:  # noqa: BLE001 — a record field, never a reason to refuse a step
            return {"version": "", "commit": ""}

    def _units_root(self, cwd: str) -> Path:
        """Where this workspace's units live. One question, asked of one module.

        `coscc/units.py` owns the answer; this is the only place in the service that asks.
        """
        return units.root(cwd, self.config.data_dir)

    def _unit_dir(self, cwd: str, unit: str) -> Path:
        try:
            return units.unit_dir(cwd, unit, self.config.data_dir)
        except BadUnit as e:
            raise Invalid(str(e)) from e

    async def board(self, cwd: str) -> dict[str, Any]:
        """Every unit in this workspace, each with its eight stages, modes and cost.

        The status of a stage comes from the artifact and the mode comes from the journal,
        and they are joined here rather than stored together. Storing them together is how
        a board starts disagreeing with the files it claims to describe.
        """
        self._workspace_or_refuse(cwd)
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e

        journal = self._journal()
        key = self._journal_key(cwd)
        modes: dict[tuple[str, str], str] = {}
        timelines: dict[str, list[dict[str, Any]]] = {}
        comments: list[dict[str, Any]] = []
        ranking: list[dict[str, Any]] = []
        if journal is not None:
            try:
                modes = journal.modes(key)
                # One read for every unit's cost, comment attempts (`0021` D4) and, since
                # `0074`, the backlog's records. Asking `totals` per unit re-scanned the
                # working folder N times for the rows this already has.
                rows = journal.records(key)
            except Busy as e:
                raise Invalid(str(e)) from e
            timelines = timelines_of(rows)
            comments = [r for r in rows if r.get("kind") == "pr-comment"]
            ranking = [r for r in rows if r.get("kind") in backlog.KINDS]
            verdicts = [r for r in rows if r.get("kind") == "precedent"]
        else:
            verdicts = []
        _attach_comment_state(data["units"], comments)
        _attach_precedent(data["units"], verdicts)
        # `0074`. Display only: nothing below reads it, and `next`/`blocked` are untouched.
        data["backlog"] = {
            **backlog.fold(
                data["units"], ranking, backlog.measured(timelines, data["units"]),
                backlog.undetermined(timelines, data["units"]),
            ),
            "propose_warning": grant_for("estimate").warning,
            "propose_consequence": CONSEQUENCE["estimate"],
        }
        per_unit = data["backlog"].pop("per_unit")
        for unit in data["units"]:
            unit["backlog"] = per_unit.get(unit["name"]) or {
                "rank": None, "value": None, "effort": None, "effort_source": None, "relations": [],
            }

        for unit in data["units"]:
            unit_last_runs = last_runs(timelines.get(unit["name"], []))
            for row in unit["stages"]:
                # `manual` is the default because starting work is a decision someone has
                # to make, not one an unset value should make for them.
                row["mode"] = modes.get((unit["name"], row["stage"]), "manual")
                # The mode is a label since `0020`; the grant follows the stage alone.
                grant = grant_for(row["stage"])
                # Carried to the page so `spec.md` C4 can be met where the button is: what
                # a step will be allowed to do has to be readable before it is started.
                row["grants"] = list(grant.tools)
                row["warning"] = grant.warning
                row["consequence"] = consequence(row["stage"])
                # `0019` plan step 6 / `spec.md` R5. From the same `timelines` read above —
                # no second scan of the run log. `status` (and the lanes) stays read from
                # the artifact alone (C6); this is a second, separate field.
                row["last_run"] = unit_last_runs.get(row["stage"])
            unit["cost"] = (
                totals_of(timelines.get(unit["name"], [])) if journal is not None else {}
            )
            # `0047` R8, R9. A label and nothing else: a deadline passing writes no row and
            # starts no step.
            unit["outcome_label"] = outcome_label(
                unit.get("outcome"), date.today(), finished=unit.get("next") == "finished"
            )
            # `0082` R11, R12. Decided here so the page only shows them.
            unit["answerable"] = answerable(unit)
            unit["attention_reason"] = attention_reason(unit)

        await self._attach_worktrees(cwd, data["units"])
        asks = await self._attach_integration(cwd, data["units"], journal, key)
        for unit in data["units"]:
            # `0100` R3. From the timelines read above: no second scan of the run log.
            ended = [r for r in timelines.get(unit["name"], []) if r.get("ended") is not None]
            unit["state"] = unit_state(unit, ended[-1] if ended else None, unit.pop("ci_held", None))

        data["recording"] = journal is not None
        # `0043` R9. Display only: the page shows it and decides nothing from it.
        data["autopilot"] = self._autopilot_block(key)
        data["read_only_because"] = (
            None if journal is not None
            else "no working folder is set, so nothing can be recorded — set COS_WORKING_DIR"
        )
        if not data["units"]:
            # `0001_product-describes-a-state-it-is-not-in` R6, R7, R8. The board reads the
            # store, and a host repository can have a `.cos/` full of units the store never
            # heard of. The page has to be able to say which directory it read and how many
            # units sit in the one it did not. Counted on every call: R8 forbids a cache.
            data["empty"] = {
                "store": str(self._units_root(cwd)),
                "host": units.key(cwd),
                "host_units": units.host_unit_count(cwd),
            }
        # `0100` R6. Started last and never awaited: their answers count from the next read.
        self._ask_ci(asks)
        return data

    def _mark_running(self, key: str, unit: str, stage: str, kind: str) -> str:
        """`0051` R1. Put one entry in `_running` and return its id, for the `finally` to pop.

        `started` is stamped by the same clock `Journal.append` uses, so it reads like an
        `at`. `turns` and `cost_usd` stay `None` while the session runs (R5).
        """
        rid = uuid.uuid4().hex
        self._running[rid] = {
            "workspace": key, "unit": unit, "stage": stage, "started": _now(),
            "kind": kind, "turns": None, "cost_usd": None,
        }
        return rid

    def running(self, cwd: str) -> dict[str, Any]:
        """`0051` R2. What has an agent working in this workspace now, and what ended unseen.

        `running` is `_running` for this workspace, one element per entry, by unit.
        `unknown_end` is every `start` the run log holds without an `end` that no entry
        accounts for (R6): the unit has nothing running here, no later `start` of the unit
        retired it, and it is younger than `UNKNOWN_END_FOR` (spec, answer 4). Matched by
        unit, not by session: `_active` allows one per unit per process, so a unit with an
        entry has no other `start` open in this process — only one another process wrote,
        and that one is shown as ended (spec C2, answer 3).

        Reads memory and the run log, nothing else: no `git`, no `gh`, no `cos.mjs`, and
        writes nothing. A busy run log is a `note`, not a refusal — the board asks this
        every few seconds, and a lock someone else holds must not break the board.
        """
        self._workspace_or_refuse(cwd)
        key = self._journal_key(cwd)
        running: dict[str, list[dict[str, Any]]] = {}
        for entry in self._running.values():
            if entry["workspace"] != key:
                continue
            kind = entry["kind"]
            agent = None if kind == "rebase" else agents.agent_for(entry["stage"])
            running.setdefault(entry["unit"], []).append({
                "kind": kind, "stage": entry["stage"], "agent": agent,
                "started": entry["started"], "turns": entry["turns"], "cost_usd": entry["cost_usd"],
                # `0073` R1. A board step's events; `""` for an integration or an estimate.
                "run": entry.get("run", ""),
            })
        out: dict[str, Any] = {"running": running, "unknown_end": {}}
        journal = self._journal()
        if journal is None:
            return out
        try:
            opened = journal.open_starts(key)
        except Busy as e:
            out["note"] = str(e)
            return out
        oldest = datetime.now(timezone.utc) - UNKNOWN_END_FOR
        for unit, found in opened.items():
            if unit in running:
                continue
            rows = [
                {"stage": r["stage"], "started": r["started"]}
                for r in found["open"]
                if r.get("started") and r["started"] == found["last_start"]
                and _younger_than(r["started"], oldest)
            ]
            if rows:
                out["unknown_end"][unit] = rows
        return out

    async def _attach_worktrees(self, cwd: str, units_: list[dict[str, Any]]) -> None:
        """`0017`. Give every unit `worktree: {path, branch, prepare}`, or `None`.

        One `git worktree list` for the whole board. A `finished` unit that still has a tree
        is cleaned up here (R10), so a unit shipped at a terminal is cleaned up too — at the
        cost of a `gh pr view` (up to 30s) on **every** board read for as long as the tree
        stays: once, when the removal succeeds; on each read after, when it does not
        (`gh` failing, the pull request not merged, the local branch off the merged head).
        Nothing remembers a refusal, so a transient `gh` error is retried rather than
        believed. A dirty tree is refused before `gh` is asked. (Plan Risk 7; `0017`
        review F4 — this docstring said "the first time" until then.)
        """
        root = Path(cwd).expanduser().resolve()
        try:
            listed = {
                str(Path(t["path"]).resolve()): t
                for t in await gitops.worktree_list(root)
            } if (root / ".git").exists() else {}
        except GitError:
            listed = {}
        for u in units_:
            u["worktree"] = None
            try:
                where = worktrees.path(cwd, u["name"], self.config.data_dir)
            except BadUnit:
                continue
            found = listed.get(str(where))
            if found is None:
                continue
            if u.get("next") == "finished":
                done = await worktrees.remove_if_finished(cwd, u["name"], u, self.config.data_dir)
                if done.get("removed"):
                    continue
            u["worktree"] = {
                "path": str(where),
                "branch": found.get("branch") or "",
                "prepare": worktrees.read_prepare(where),
            }

    # -- integration (`0035`) -------------------------------------------------

    async def _attach_integration(
        self, cwd: str, units_: list[dict[str, Any]], journal: Journal | None, key: str
    ) -> list[tuple[tuple[str, str], str, int, str]]:
        """R1/R2. Give every unit `integration: {...}` when it sits in the window, else None.

        **Reads only.** One `gh pr list` for the workspace (up to `integrate.GH_TIMEOUT`),
        `git` counts against the `origin/main` the last fetch brought — no fetch here — and
        `gh pr checks` only for a unit whose head is the one its last integration pushed.
        Nothing here writes a record, calls `update-branch` or opens a session.

        `0100` R6. Also gives each unit in the window `ci_held`, the held CI answer when it
        is for the head `gh pr list` just returned, and returns the CI asks `board` starts
        once it has answered: `(slot, tree, pr number, head)` for each unit with no answer
        for that head, or one older than `CI_REFRESH`, and no ask already running.
        """
        for u in units_:
            u["integration"] = None
        window = [u for u in units_ if u.get("between_pr_and_ship") and u.get("pr")]
        if not window:
            return []
        root = Path(cwd).expanduser().resolve()
        last = self._last_integrations(journal, key)
        try:
            prs: list[dict[str, Any]] | str = await integrate.open_prs(str(root))
        except integrate.IntegrateError as e:
            prs = str(e)
        asks: list[tuple[tuple[str, str], str, int, str]] = []
        oldest = datetime.fromisoformat(_now()) - timedelta(seconds=CI_REFRESH)
        for u in window:
            info = await self._integration_of(root, u, prs, last.get(u["name"]))
            if info is not None:
                u["integration"] = info
            number = (u.get("pr") or {}).get("number")
            row = next((r for r in prs if r.get("number") == number), None) if isinstance(prs, list) else None
            if row is None:
                continue
            slot, head = (key, u["name"]), str(row.get("headRefOid") or "")
            held = self._ci.get(slot)
            if held is not None and held.get("head") == head:
                u["ci_held"] = held
            if slot in self._ci_asks:
                continue
            if held is None or held.get("head") != head or not _younger_than(held.get("at") or "", oldest):
                asks.append((slot, str(root), int(number), head))
        return asks

    def _ask_ci(self, asks: list[tuple[tuple[str, str], str, int, str]]) -> None:
        """`0100` R6. One background `gh pr checks` per ask, none awaited. Its answer, or
        `gh`'s error, is held with the time it was read, so an error is not asked again
        before `CI_REFRESH` either. Writes no run-log record."""
        for slot, tree, number, head in asks:
            if slot in self._ci_asks:
                continue

            async def ask(slot=slot, tree=tree, number=number, head=head) -> None:
                try:
                    answer: dict[str, Any] = {"checks": await integrate.required_checks(tree, number)}
                except integrate.IntegrateError as e:
                    answer = {"error": str(e)}
                self._ci[slot] = {"head": head, **answer, "at": _now()}

            task = asyncio.get_running_loop().create_task(ask())
            self._ci_asks[slot] = task
            # Removed however it ends — cancelled included — or the unit is never asked again.
            task.add_done_callback(lambda t, slot=slot: self._ci_asks.pop(slot, None) if self._ci_asks.get(slot) is t else None)

    @staticmethod
    def _last_integrations(journal: Journal | None, key: str) -> dict[str, dict[str, Any]]:
        if journal is None:
            return {}
        try:
            rows = journal.records(key, kind="integration")
        except Busy:
            return {}
        return {str(r.get("unit")): r for r in rows}

    async def _integration_of(
        self, root: Path, u: dict[str, Any], prs: list[dict[str, Any]] | str,
        last_record: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """One unit's state. None when its pull request is not among the open ones."""
        number = (u.get("pr") or {}).get("number")
        if isinstance(prs, str):
            pr_row: dict[str, Any] | str = prs
        else:
            match = next((r for r in prs if r.get("number") == number), None)
            if match is None:
                return None
            pr_row = match
        origin_sha = ""
        missing: int | str = 0
        if isinstance(pr_row, dict):
            try:
                origin_sha = await gitops.rev_parse(root, "refs/remotes/origin/main")
                head = str(pr_row.get("headRefOid") or "")
                if not await gitops.has_commit(root, head):
                    missing = f"the pull request's head {head[:7]} is not here: fetch, then ask again"
                else:
                    missing = await gitops.count_missing(root, head, origin_sha)
            except GitError as e:
                missing = str(e)
        checks: list[dict[str, Any]] | str | None = None
        if integrate.needs_checks(pr_row, last_record):
            try:
                checks = await integrate.required_checks(str(root), int(number))
            except integrate.IntegrateError as e:
                checks = str(e)
        verdict = integrate.classify(pr_row, missing, origin_sha, last_record, checks)
        state = verdict["state"]
        review_status = next((r.get("status") or "" for r in u.get("stages") or [] if r.get("stage") == "review"), "")
        gebo = state in integrate.GEBO_STATES
        # `0052`: a `current` unit also has the button, since the count may be against a
        # stale `origin/main` and only a press fetches (R3). Both mechanical states may fall
        # to Gebo when GitHub refuses the rebase (spec, answer 1), and the page says so.
        fallback = state in ("current", "behind")
        return {
            "state": state,
            "reason": verdict.get("reason", ""),
            "behind": missing if isinstance(missing, int) else None,
            "origin_sha": origin_sha,
            "pr_head": pr_row.get("headRefOid", "") if isinstance(pr_row, dict) else "",
            "mode": "agent" if gebo else ("mechanical" if fallback else ""),
            "button": state in integrate.BUTTON_STATES or state == "current",
            "needs_person": list((last_record or {}).get("needs_person") or [])
            if (last_record or {}).get("outcome") == "needs-person" else [],
            "warnings": integrate.warnings(
                u.get("rounds") or [], review_status, gebo or fallback, grant_for("integrate").warning,
                fallback=fallback,
            ),
            "consequence": CONSEQUENCE["integrate"],
        }

    async def integrate(
        self, cwd: str, unit: str, started_by: str = "person",
    ) -> AsyncIterator[tuple[str, Any]]:
        """`0035`. Integrate one unit, on a person's request. Streams like `run_step`.

        `0043`: or on the autopilot's, which passes `started_by="autopilot"`; every record
        this writes carries it (R3). No route passes it.

        Refuses before anything changes (R12), and every refusal, push or failure leaves one
        `integration` record (R9). `behind` goes the mechanical road (R4); `conflicting`
        and `red-after-integration` open Gebo (R5).

        `0052`: a press inside the window fetches `origin/main` first, through the `0048`
        coordinator and before the lock, so the count is against the trunk as it is now; a
        failed fetch goes on with the ref it has and says so. It also reads GitHub's
        `mergeStateStatus`, only to record it. A mechanical road whose `update-branch`
        exits non-zero opens Gebo with that code and gh's words.
        """
        try:
            integrate.check_started_by(started_by)
        except ValueError as e:
            raise Invalid(str(e)) from e
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so an integration cannot be recorded — set COS_WORKING_DIR")
        if not unit:
            raise Invalid("name a work unit")
        directory = self._unit_dir(cwd, unit)
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e
        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Invalid(f"no such work unit in this workspace: {unit}")
        key = self._journal_key(cwd)
        root = Path(cwd).expanduser().resolve()
        last = self._last_integrations(journal, key).get(unit)
        info = None
        pr = (found.get("pr") or {}).get("number")
        # Spread into every record this press writes (`0052` R5; `started_by`, `0043` R3).
        seen: dict[str, Any] = {"fetch": None, "merge_state": "", "started_by": started_by}
        if found.get("between_pr_and_ship") and found.get("pr"):
            try:
                seen["fetch"] = await fetches.fetch(root, BRANCH_REMOTE, BRANCH_TRUNK)
            except GitError as e:
                seen["fetch"] = {"outcome": "failed", "detail": str(e)}
            try:
                prs: list[dict[str, Any]] | str = await integrate.open_prs(str(root))
            except integrate.IntegrateError as e:
                prs = str(e)
            info = await self._integration_of(root, found, prs, last)
            if info is not None and seen["fetch"]["outcome"] == "failed":
                note = integrate.origin_note(info["origin_sha"], seen["fetch"])
                info["reason"] = f"{info['reason']}; {note}" if info.get("reason") else note
            if pr is not None:
                seen["merge_state"] = await integrate.merge_state(str(root), int(pr))
        state = (info or {}).get("state", "")
        pr_head = (info or {}).get("pr_head", "")
        origin_sha = (info or {}).get("origin_sha", "")
        try:
            branch = units.branch_name(cwd, unit, self.config.data_dir)
        except (CannotCreate, BadUnit):
            branch = ""
        tree_found = None
        try:
            tree_found = await worktrees.find(cwd, unit, self.config.data_dir)
        except (GitError, BadUnit):
            tree_found = None
        tree = Path(tree_found["path"]) if tree_found else None

        def write(rec: dict[str, Any]) -> dict[str, Any]:
            try:
                return journal.append(rec)
            except (BadRecord, Busy):
                return rec

        lock = self._integrate_locks.setdefault(key, asyncio.Lock())
        async with lock:
            clean = on_branch = None
            local_head = ""
            if tree is not None:
                try:
                    clean = await gitops.is_clean(tree)
                    on_branch = bool(branch) and (await gitops.current_branch(tree)) == branch
                    local_head, _ = await gitops.head_and_branch(tree)
                except GitError:
                    clean = on_branch = None
            reason = integrate.refusal(
                in_window=info is not None, busy=self._busy(key, unit),
                clean=clean, branch_ok=on_branch, local_head=local_head, pr_head=pr_head, state=state,
                origin=integrate.origin_note(origin_sha, seen["fetch"]),
            )
            if reason:
                write(integrate.record(
                    workspace=key, unit=unit, pr=pr, mode=(info or {}).get("mode") or "mechanical",
                    head_before=pr_head, head_after="", origin_sha=origin_sha, outcome="refused",
                    detail=reason, **seen,
                ))
                raise Invalid(reason)
            mark = self._take(key, unit, "integrate")
            # `0051` spec, answer 1: Gebo shows as running under its agent name; a mechanical
            # rebase has no agent and shows as rebasing. The same condition as below.
            rid = self._mark_running(key, unit, "integrate", "rebase" if state == "behind" else "gebo")
        try:
            assert tree is not None
            refused_update = None
            if state == "behind":
                rec, refused_update = await self._integrate_mechanical(
                    key, unit, int(pr), tree, branch, pr_head, origin_sha, seen,
                )
                if rec is not None:
                    write(rec)
                    yield ("done", {"integration": rec})
                    return
                # `0052`, spec answer 1: GitHub refused the rebase, and the press agreed to
                # Gebo for that. The board shows Gebo from here on, not a rebase.
                self._running[rid]["kind"] = "gebo"
            async for item in self._integrate_gebo(
                cwd, key, unit, directory, found, data, info, int(pr), tree, branch, pr_head, origin_sha,
                journal, write, seen, refused_update,
            ):
                yield item
        finally:
            self._release(key, unit, mark)
            self._running.pop(rid, None)
            self.updater.job_ended()
            # `0043` R5 a.
            self._autopilot_nudge(key)

    async def _integrate_mechanical(
        self, key: str, unit: str, pr: int, tree: Path, branch: str, head_before: str, origin_sha: str,
        seen: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """R4. GitHub rebases, the local branch follows. No session.

        `(record, None)`, or `(None, {code, said})` when `update-branch` exited non-zero and
        the pull request's head is still `head_before` — the caller then opens Gebo (`0052`).
        A `gh` that could not run or did not answer in time, and a head that has not moved
        yet, stay `failed`: there is no exit code to go on, and GitHub may still be
        rebasing, which a Gebo session would race.
        """
        base = dict(workspace=key, unit=unit, pr=pr, mode="mechanical", head_before=head_before,
                    origin_sha=origin_sha, **seen)
        try:
            code, said = await integrate.update_branch(str(tree), pr)
        except integrate.IntegrateError as e:
            return integrate.record(**base, head_after="", outcome="failed", detail=str(e)), None
        if code != 0:
            refused = {"code": code, "said": said or "gh refused"}
            # `0052` review F1: a non-zero exit does not rule out that GitHub took the command.
            # Gebo's lease would then refuse its push while the head read afterwards counted as
            # Gebo's — so the head is read once, and a moved one opens no session.
            try:
                head_after = await integrate.pr_head(str(tree), pr)
                unread = "it came back empty"
            except integrate.IntegrateError as e:
                head_after, unread = "", str(e)
            if head_after == head_before:
                return None, refused
            if not head_after:
                return integrate.record(
                    **base, head_after="", outcome="failed", update_branch=refused,
                    detail=f"gh pr update-branch exited {code}, and the pull request's head could not be "
                           f"read to rule out a rebase on GitHub's side, so no session was opened: {unread}",
                ), None
            base["update_branch"] = refused
            said = f"gh pr update-branch exited {code}, but the pull request's head moved; no session was opened"
        else:
            head_after = head_before
            for attempt in range(integrate.POLL_TRIES):
                try:
                    head_after = await integrate.pr_head(str(tree), pr)
                except integrate.IntegrateError:
                    head_after = head_before
                if head_after and head_after != head_before:
                    break
                if attempt + 1 < integrate.POLL_TRIES:
                    await asyncio.sleep(integrate.POLL_DELAY)
        if not head_after or head_after == head_before:
            return integrate.record(
                **base, head_after="", outcome="failed",
                detail="GitHub accepted the command but the head has not changed yet",
            ), None
        try:
            await gitops.reset_branch_to(tree, branch, head_before, head_after)
            detail = said
        except GitError as e:
            # The push happened on GitHub's side either way; the local tree is behind it.
            detail = f"pushed on GitHub, but the local branch was not moved: {e}"
        return integrate.record(**base, head_after=head_after, outcome="pushed", detail=detail), None

    async def _integrate_gebo(
        self, cwd: str, key: str, unit: str, directory: Path, found: dict[str, Any],
        data: dict[str, Any], info: dict[str, Any], pr: int, tree: Path, branch: str,
        head_before: str, origin_sha: str, journal: Journal, write: Any,
        seen: dict[str, Any], refused_update: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """R5–R8. One Gebo session; the outcome is read from GitHub afterwards.

        `refused_update`: the `update-branch` refusal that opened it (`0052`), carried into
        the prompt and the press's one record.
        """
        root = Path(cwd).expanduser().resolve()
        rel = await self._related(root, unit, data, head_before, origin_sha)
        units_root = self._units_root(cwd)
        # `0094` R14: by path; Gebo reads what it needs of them (`integrate.read_paths`).
        own = {}
        for name in ("intent.md", "spec.md", "plan.md", "impl.md"):
            path = Path(directory).resolve() / name
            if path.exists():
                own[name] = path
        try:
            skill = harness.read_skill("integrate")
        except harness.MissingRules as e:
            raise Invalid(f"the integrate skill could not be read: {e}") from e
        prompt = integrate.build_prompt(
            skill=skill, unit=unit, branch=branch, pr=pr, state=info["state"], reason=info.get("reason", ""),
            head_before=head_before, origin_sha=origin_sha, rel=rel, units_root=units_root, own_paths=own,
            refused_update=refused_update,
        )
        grant = grant_for("integrate")
        model, model_source = self._model_for("impl")
        app = self._app_identity()
        try:
            journal.started(key, unit, "integrate", "manual", started_by=seen["started_by"],
                            prompt_chars=len(prompt), granted=list(grant.tools),
                            max_turns=grant.max_turns, head=head_before, model=model, model_source=model_source,
                            pointed=list(own), app_version=app["version"], app_commit=app["commit"],
                            # `0093` R8: what opened this session, for *Integrate for a conflict*.
                            integrate_state=info["state"])
        except (BadRecord, Busy):
            pass
        end: dict[str, Any] = {}
        failure = ""
        try:
            async for kind, payload in integrate.run_gebo(
                self.sessions, tree=str(tree), workspace=cwd, prompt=prompt, grant=grant,
                read_also=integrate.read_paths(units_root, unit, rel), lease=(branch, head_before), model=model,
            ):
                if kind == "chunk":
                    yield ("chunk", payload)
                else:
                    end = payload
        except Exception as e:  # noqa: BLE001 — recorded, never swallowed silently
            failure = f"the session failed: {e}"
        details = [failure] if failure else []
        try:
            if await gitops.rebase_in_progress(tree):
                await gitops.abort_rebase(tree)
                details.append("the session left a rebase in progress; the app aborted it")
        except GitError as e:
            details.append(f"could not check for a stopped rebase: {e}")
        try:
            head_now = await integrate.pr_head(str(tree), pr)
        except integrate.IntegrateError as e:
            head_now = head_before
            details.append(f"could not read the pull request's head afterwards: {e}")
        reply = str(end.get("reply") or "")
        outcome = integrate.outcome_of_session(head_before, head_now, reply)
        if outcome == "pushed":
            # `0052` review round 2, F1: a head that moved is Gebo's push only if Gebo's tree
            # ends on it. A GitHub rebase finishing late, which the lease then refused Gebo's
            # push over, is not — the tree follows it as on the mechanical road.
            try:
                local_head, _ = await gitops.head_and_branch(tree)
            except GitError as e:
                local_head = ""
                details.append(f"could not read the tree's HEAD afterwards: {e}")
            if local_head != head_now:
                outcome = "failed"
                details.append(
                    f"the pull request's head moved to {head_now[:7]}, but this session's tree is at "
                    f"{local_head[:7] or 'an unread HEAD'}, so the push was not this session's"
                )
                try:
                    await gitops.reset_branch_to(tree, branch, local_head, head_now)
                    details.append(f"the local branch was moved to {head_now[:7]}")
                except GitError as e:
                    details.append(f"the local branch was not moved: {e}")
        try:
            journal.finished(
                key, unit, "integrate", "done" if outcome in ("pushed", "needs-person") else "failed",
                session_id=end.get("session_id", ""), detail="; ".join(details) or None,
                denials=end.get("denials", 0), denied=end.get("denied"),
                models_used=end.get("models_used") or None, **(end.get("cost") or {}),
            )
        except (BadRecord, Busy):
            pass
        rec = write(integrate.record(
            workspace=key, unit=unit, pr=pr, mode="agent", head_before=head_before, head_after=head_now,
            origin_sha=origin_sha, outcome=outcome, related_=rel, report=reply,
            needs_person=integrate.parse_needs_person(reply), detail="; ".join(details),
            update_branch=refused_update, **seen,
        ))
        yield ("done", {"integration": rec})

    async def _related(
        self, root: Path, unit: str, data: dict[str, Any], head: str, origin_sha: str
    ) -> dict[str, list[dict[str, Any]]]:
        """R7, from git. A failure leaves a list empty rather than stopping the step."""
        try:
            base = await gitops.merge_base_of(root, head, origin_sha)
            mine = await gitops.files_between(root, base, head)
            commits = []
            for c in await gitops.commits_between(root, base, origin_sha):
                commits.append({**c, "files": await gitops.files_of_commit(root, c["sha"])})
        except GitError:
            return {"merged": [], "open": []}
        try:
            prs = await integrate.open_prs(str(root))
        except integrate.IntegrateError:
            prs = []
        heads = {r.get("number"): str(r.get("headRefOid") or "") for r in prs}
        others = []
        for u in data["units"]:
            if u["name"] == unit or not u.get("between_pr_and_ship") or not u.get("pr"):
                continue
            other_head = heads.get(u["pr"].get("number"))
            if other_head is None:
                continue
            files = None
            try:
                if await gitops.has_commit(root, other_head):
                    their_base = await gitops.merge_base_of(root, other_head, origin_sha)
                    files = await gitops.files_between(root, their_base, other_head)
            except GitError:
                files = None
            others.append({"unit": u["name"], "files": files})
        return integrate.related(commits, mine, data["units"], others, unit)

    async def _cleanup(self, cwd: str, unit: str) -> dict[str, Any]:
        """R10 after a `ship` step. Never raises; says what it did or why not."""
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            return {"removed": False, "reason": str(e)}
        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            return {"removed": False, "reason": "unit not on the board"}
        return await worktrees.remove_if_finished(cwd, unit, found, self.config.data_dir)

    async def next_step(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0024`. The one stage the run button may offer, and why -- `cos.mjs next`'s answer.

        Read with the same store and the same `repo=cwd` that `run_step` hands the gate, so
        the stage offered and the gate that will be asked read one checkout (`0024` spec,
        *Repo mà `cos.mjs` đọc*). Nothing here chooses a stage.
        """
        self._workspace_or_refuse(cwd)
        if not unit:
            raise Invalid("name a work unit")
        self._unit_dir(cwd, unit)
        # `0045` R15. Asked first with no `--repo`, which reads files only: a held unit is
        # answered here, before `_worktree` could reopen the tree a drop just removed.
        try:
            held = await board_reader.next_step(self._units_root(cwd), unit, repo=None)
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if held.get("hold"):
            return {
                "cwd": cwd, "unit": unit, **{k: held[k] for k in ("stage", "action", "blocked")},
                "waiting": [], "hold": held["hold"],
            }
        # `0017`. The unit's worktree is the checkout its branch and pull request are read
        # from. None when there is none to open, and `cos.mjs` then keeps `review` and
        # `ship` closed rather than read the workspace's branch, which is not this unit's.
        # A workspace that is not a git repository has no worktrees, and is read as it
        # always was — the same fallback `run_step` takes, so the two read one checkout.
        if (Path(cwd).expanduser().resolve() / ".git").exists():
            tree = await self._worktree(cwd, unit)
            repo = tree["path"] if tree else None
        else:
            repo = cwd
        try:
            found = await board_reader.next_step(self._units_root(cwd), unit, repo=repo)
        except Unavailable as e:
            raise Invalid(str(e)) from e
        return {
            "cwd": cwd,
            "unit": unit,
            **{k: found[k] for k in ("stage", "action", "blocked")},
            # `0028`. The findings a person is awaited on, copied from `cos.mjs next`.
            "waiting": list(found.get("waiting") or []),
            # `0106`. The stage a fully answered draft would run again; only the autopilot
            # reads it.
            "rerun": str(found.get("rerun") or ""),
        }

    async def rerun_offers(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0054` R1, R2. The accepted stages `unit` may run again, each with the stages that
        then run again after it -- `cos.mjs rerun`'s answer, copied: `{unit, offers: [{stage,
        later}], why}`. Files only: no worktree is opened and no `gh` is asked. Nothing here
        chooses a stage."""
        self._workspace_or_refuse(cwd)
        if not unit:
            raise Invalid("name a work unit")
        self._unit_dir(cwd, unit)
        try:
            found = await board_reader.rerun(self._units_root(cwd), unit)
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if "error" in found:
            raise Invalid(str(found["error"]))
        return found

    async def set_mode(self, cwd: str, unit: str, stage: str, mode: str) -> dict[str, Any]:
        """Choose how one step runs. Validated against the board, not against a second list."""
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a mode cannot be recorded — set COS_WORKING_DIR"
            )

        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e

        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            raise Invalid(f"no such work unit in this workspace: {unit}")
        if stage not in data["stages"]:
            raise Invalid(f"no such stage: {stage} (use one of {', '.join(data['stages'])})")

        try:
            journal.set_mode(self._journal_key(cwd), unit, stage, mode)
        except BadRecord as e:
            raise Invalid(str(e)) from e
        except Busy as e:
            raise Invalid(str(e)) from e
        return {"cwd": cwd, "unit": unit, "stage": stage, "mode": mode}

    async def run_step(
        self, cwd: str, unit: str, stage: str, started_by: str = "person",
        rerun: bool = False, note: str = "",
    ) -> AsyncIterator[tuple[str, Any]]:
        """Run one step of one unit, streaming the reply as it arrives.

        Everything this needs — the stage order, the artifact filename, the mode — comes
        from one board read, so a step cannot run against a different idea of the unit
        than the one the page is showing.

        `started_by` (`0043` R3) is `autopilot` only when the autopilot calls this; no route
        passes it, so a request cannot say it is the autopilot.

        `rerun` (`0054` R6) runs an accepted stage again, with a person's `note`. Whether the
        stage may, and the `### Rerun` block appended to `intent.md` before the session
        starts, are `cos.mjs rerun`'s. Refused for the autopilot and for a note over
        `RERUN_NOTE_MAX`; an empty note is not refused (`spec.md ## Answers, câu 2`).
        """
        try:
            integrate.check_started_by(started_by)
        except ValueError as e:
            raise Invalid(str(e)) from e
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        journal = self._journal()
        if journal is None:
            raise Invalid(
                "no working folder is set, so a run cannot be recorded — set COS_WORKING_DIR"
            )

        # `0050` R4. The unit is held from here, before the first `await`: a second request
        # for any stage of it is refused before it reads the board, opens a worktree, runs
        # the gate or fetches -- not after all of that, as it was (`spike.md ## U1`). Until
        # the step is handed to `_drive` the mark is this frame's to return, on every road
        # out: a refusal, an exception, or a cancel when the client goes away (R5).
        key = self._journal_key(cwd)
        mark = self._take(key, unit, "step", stage)
        handed = False
        running: steps_mod.Running | None = None
        rid: str | None = None
        try:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            row = next((r for r in found["stages"] if r["stage"] == stage), None)
            if row is None:
                raise Invalid(f"no such stage: {stage} (use one of {', '.join(data['stages'])})")
            # `0045` R4/R15. `cos.mjs`'s own field, read before any worktree is opened — the gate
            # below would refuse too, but only after `_worktree` had reopened a dropped tree.
            held = found.get("hold")
            if held:
                raise Invalid(f"{unit} is {held.get('state')}: {held.get('reason')} — nothing runs on it")

            # `0054` R6. Before a worktree is opened or the gate asked. Whether `stage` may run
            # again, and the block that says so, are `cos.mjs`'s; its refusal is passed on.
            note = str(note or "").strip()
            rerun_block = ""
            if rerun:
                if started_by != "person":
                    raise Invalid("a stage is run again only by a person, from the board, never by the autopilot")
                if len(note) > RERUN_NOTE_MAX:
                    raise Invalid(f"the note is {len(note)} characters, over the {RERUN_NOTE_MAX} a rerun takes")
                try:
                    asked = await board_reader.rerun(self._units_root(cwd), unit, stage)
                except Unavailable as e:
                    raise Invalid(str(e)) from e
                if "error" in asked:
                    raise Invalid(str(asked["error"]))
                rerun_block = str(asked.get("block") or "")

            # `.claude/CLAUDE.md` invariant 2: *"Ask `cos.mjs gate` before a stage and stop
            # when it exits non-zero."* Until 2026-09-23 this app did neither. It read the
            # board, found the row, and started the session -- so the board would run `ship`
            # on a unit whose `intent.md` was still a draft, and the only thing standing
            # between it and that was a sentence in a skill file addressed to a session that
            # often has no way to run a command.
            #
            # Asked here rather than in `Runner` because a refusal must arrive before any
            # money is spent, and `run_step` is the last place that is still true.
            # `0017`. Every step runs in the unit's own worktree. A workspace that is not a git
            # repository has none, and its steps run where they always did — there is no
            # branch there for another unit to take away.
            is_repo = (Path(cwd).expanduser().resolve() / ".git").exists()
            tree = await self._worktree(cwd, unit, strict=True) if is_repo else None
            if is_repo and tree is None:
                try:
                    tree = {"path": (await worktrees.ensure(cwd, unit, None, self.config.data_dir))["path"]}
                except (GitError, BadUnit) as e:
                    raise Invalid(f"{unit} has no worktree and one could not be opened: {e}") from e
            work = tree["path"] if tree else cwd
            # `0039` R13. A spike is watched through the worktree's `HEAD` and `git status`;
            # with no git there is nothing to watch, so it does not run at all.
            if stage == "spike" and tree is None:
                raise Invalid("spike needs a git worktree to watch, and this workspace is not a git repository")
            # `0030_a-unit-branch-starts-from-a-stale-main` R1/R4/R5. A tree already on its
            # branch carries whatever `_worktree` read when it was opened onto it (or nothing,
            # when it was already there before this call); a tree still detached is refreshed
            # now, on the spot, because a session about to run on it is about to read it.
            base: dict[str, Any] | None = None
            if tree is not None:
                if tree.get("branch"):
                    base = tree.get("base")
                else:
                    base = await worktrees.refresh_base(cwd, unit, self.config.data_dir)
            try:
                # `work` is the checkout the `review` and `ship` gates read git and the pull
                # request from (`0015`). The store has no git to read.
                allowed, said = await board_reader.gate(
                    self._units_root(cwd), unit, stage, repo=work
                )
            except Unavailable as e:
                raise Invalid(str(e)) from e
            if not allowed:
                raise Invalid(said)

            if stage == "impl" and tree is not None:
                # R6. A tree that cannot run its tests turns every `impl` red from the start, so
                # the step is not started on one. Tried once more first: a network blip is the
                # ordinary reason, and the page has nothing better to offer than *try again*.
                prepared = worktrees.read_prepare(Path(work))
                if not (prepared or {}).get("ok"):
                    prepared = await worktrees.prepare(Path(work), cwd, data_dir=self.config.data_dir)
                if not prepared.get("ok"):
                    raise Invalid(worktrees.describe_failure(prepared))

            # `0111` R1-R7. A UI unit whose branch was rewritten since `impl` took its
            # screenshots has them taken again, here, before any money is spent; a retake that
            # fails refuses the step, and no round is spent on a stale manifest.
            screens_note = ""
            if stage == "review" and tree is not None:
                screens_note = await self._retake_screens(cwd, key, journal, unit, work, started_by)

            directory = self._unit_dir(cwd, unit)
            mode = journal.modes(key).get((unit, stage), "manual")
            # `0021` D3. The rounds `review.md` held before this step, so that the ones it adds
            # can be told apart afterwards. Taken from the board already read above.
            rounds_before = (
                {r.get("n") for r in found.get("rounds") or []}
                if row["file"] == "review.md" else None
            )
            # `0004_no-setting-says-which-model-runs-a-stage`. Resolved after the gate, so a
            # refused step reads nothing more. `stage` was checked against the board above.
            # `0033`: with the plan's label, the effort and, for `impl`, which run this is.
            # `0019` plan step 6 / `spec.md` R6. Read after the gate, before any money is
            # spent — the same place `model` is resolved. `Runner` does not read the run log
            # itself; `build_prompt` only places what it is handed, the same as `base_note`.
            try:
                config = self._stage_config(stage, list(data["stages"]), directory, journal, key, unit)
                failed = journal.failed_attempts(key, unit, stage)
            except Busy as e:
                raise Invalid(str(e)) from e
            end_fields = None
            if rounds_before is not None:
                async def end_fields() -> dict[str, Any]:
                    return await self._findings_added(cwd, unit, rounds_before)
            # `0035` R10. The integration pushed since the last review round, for `review` only.
            integration_note = ""
            if stage == "review":
                since = integration_since_review(journal, key, unit)
                integration_note = integrate.describe_for_review(since) if since else ""
            # `0042`. Which files the plan names `main` changed since the plan ran, for `impl`
            # only. Unlike `failed_attempts` above, nothing here may refuse the step (R8): a
            # busy run log, an unreadable `plan.md` or a bug in `drift.py` is "could not check".
            plan_drift: dict[str, Any] | None = None
            if stage in ("impl", "implement"):
                try:
                    plan_drift = await drift.compute(
                        journal.records(key, unit),
                        (directory / "plan.md").read_text(encoding="utf-8"),
                        tree["path"] if tree else None,
                    )
                except Exception as e:  # noqa: BLE001 — R8, recorded as the reason
                    plan_drift = {
                        "plan_sha": None, "main_sha": None, "files": None,
                        "checked": False, "reason": str(e) or type(e).__name__,
                    }
            # `0110` R6/R7. What the reviews of the units the board above read as finished said
            # about the files the plan names, for `impl` only; every other stage is handed no
            # key. Like `plan_drift`, nothing in `for_step` may refuse the step.
            prior_kw: dict[str, Any] = {}
            if stage in ("impl", "implement"):
                prior_kw = priorfindings.for_step(
                    units.cos_dir(cwd, self.config.data_dir),
                    [u["name"] for u in data["units"] if u.get("next") == "finished"],
                    directory / "plan.md",
                    unit,
                )
            # `0074` R14. Where the unit stood in the shortlist in effect as it started, for the
            # outcome's measurement. Like `plan_drift`, nothing here may refuse the step.
            try:
                shortlist = backlog.stamp(journal.records(key, kind="shortlist"), unit)
            except Exception as e:  # noqa: BLE001 — recorded as the reason
                shortlist = {"rank": None, "of": None, "record": None, "error": str(e) or type(e).__name__}
            # `0041` R2. The unit's open pull request, for `pr` only, after the gate and before
            # any money is spent. One `gh pr list`, up to `integrate.GH_TIMEOUT`; a lookup that
            # fails still starts the step, and its prompt says so.
            pr_note, pr_before = "", None
            if stage == "pr":
                if tree is not None:
                    lookup = await integrate.pr_for_branch(work, tree.get("branch") or "")
                else:
                    lookup = {"state": "unknown", "reason": "this workspace is not a git checkout"}
                # `None` when the lookup could not answer, so `_sync_pr` does not read that as
                # "no pull request" (`0055` review F2); the `start` record still gets `""`.
                pr_note = integrate.describe_pr_lookup(lookup)
                pr_before = None if lookup.get("state") == "unknown" else lookup.get("url", "")
            # `0090` R1-R4. The store, read once, only with the flag on and only for the stages
            # that receive it; off, nothing is read and `Runner.run` is handed no key at all, so
            # its prompt and its `start` record are what they were (R2). A store that cannot be
            # read never refuses the step (`knowledge.for_step`).
            knowledge_kw: dict[str, Any] = {}
            if self.config.knowledge and stage in knowledge.STAGES:
                knowledge_kw = knowledge.for_step(self.config.data_dir, units.slot(cwd))
            # `0054` R3, R8. After the last refusal that reads nothing more, before any money
            # is spent. `pr.md`'s `## Answers` is read first: the `pr` session writes that file
            # itself, so only a comparison afterwards can tell whether the section survived.
            answers_before: bytes | None = None
            if rerun:
                if stage == "pr":
                    try:
                        answers_before = answers_section((directory / "pr.md").read_bytes())
                    except OSError:
                        answers_before = None
                await self._append_to_answers(directory / "intent.md", "\n" + rerun_block, "a rerun")
            if answers_before is not None:
                kept_from = answers_before

                async def end_fields() -> dict[str, Any]:
                    return {"answers_kept": _answers_kept(directory / "pr.md", kept_from)}
            runner = Runner(self.sessions, journal, app=self._app_identity())
            # `0034` R11. The registry is what the page lists and what a Stop finds; the mark
            # taken above is what everything else asks. The same start time for both, and no
            # `await` between the listing and the phase (`0050` R3).
            try:
                running = self.steps.claim(key, unit, stage, started_at=mark.started_at)
            except steps_mod.Busy as e:
                raise Invalid(str(e)) from e
            mark.phase = "running"
            # `0039` R12: emptied before the step, whatever an earlier one left, and removed
            # after it however it ends -- in `_drive`, so a client that drops the stream no
            # longer decides when (`0034`).
            scratch = units.spike_dir(cwd, unit, self.config.data_dir) if stage == "spike" else None
            rid = self._mark_running(key, unit, stage, "step")
            queue: asyncio.Queue = asyncio.Queue()
            running.listeners.add(queue)
            # `0073` R1. The step's `run` and recorder, from here to the task with no `await`
            # between, so every list that names the step names its `run` too.
            run = uuid.uuid4().hex
            recorder = events.Recorder(
                run, Data(self.config.data_dir), str(journal.working_dir), key, unit, stage,
            )
            running.run = run
            running.handle.recorder = recorder
            self._recorders[run] = recorder
            self._running[rid]["run"] = run
            running.task = asyncio.create_task(self._drive(
                running, mark, runner, cwd, unit, stage, row["file"], directory, tree, base, rounds_before,
                rid, scratch,
                dict(
                    workspace=cwd,
                    directory=directory,
                    journal_key=key,
                    unit=unit,
                    stage=stage,
                    artifact=row["file"],
                    stages=list(data["stages"]),
                    mode=mode,
                    gate_said=said,
                    cwd=step_cwd(stage, work, directory, str(scratch) if scratch else None),
                    base=base,
                    base_note=describe_base(base),
                    last_attempt=describe_attempt(failed) if failed else "",
                    integration_note=integration_note,
                    screens_note=screens_note,
                    plan_drift=plan_drift,
                    drift_note=drift.describe(plan_drift) if plan_drift is not None else "",
                    shortlist=shortlist,
                    end_fields=end_fields,
                    pr_note=pr_note,
                    pr_before=pr_before,
                    **knowledge_kw,
                    **prior_kw,
                    **config,
                    # Only named for a spike, so a stand-in `run` without it keeps working.
                    **({"watch": work} if scratch is not None else {}),
                    # The same: `Runner.run` writes `person` when it is not named.
                    **({"started_by": started_by} if started_by != "person" else {}),
                    # `0054`. The same again: only a rerun names them.
                    **({"rerun": True, "rerun_note": note} if rerun else {}),
                ),
                answers_before=answers_before,
            ))
            running.task.add_done_callback(
                lambda _task: self._never_driven(running, mark, rid)
            )
            handed = True
        finally:
            if not handed:
                # `0050` review round 1, F1. Past `claim`, the listing and the `0051` entry
                # are this frame's to return too, or `/api/board/steps` keeps a step that
                # never started and the next request gets past the mark to `claim` again.
                self._release(key, unit, mark)
                if rid is not None:
                    self._running.pop(rid, None)
                if running is not None:
                    self.steps.release(running)
                    self.updater.job_ended()
        # `0034` R3/R4. Only the reader lives here. A reader that goes away -- a closed
        # tab, a dropped NDJSON client -- takes its queue with it and nothing else: the
        # step runs on to its own end in `_drive`. Stopping it is `stop_step`, and only that.
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "raise":
                    raise payload
                yield (kind, payload)
                if kind == "done":
                    return
        finally:
            running.listeners.discard(queue)

    async def _retake_screens(
        self, cwd: str, key: str, journal: Journal, unit: str, work: str, started_by: str,
    ) -> str:
        """`0111`. Ask `cos.mjs screens`; when it says to, take the screenshots again under
        `_screens_lock`, judge the result (R4) and record it (R6). Returns the section for the
        `review` prompt (R7), `""` when nothing was taken. A retake that fails raises
        `Invalid` with `RETAKE_REFUSED`; what went wrong is only in its record (R5). No tracked
        file is put back; `.screens/` is, by `retake.take` (review round 1, F1)."""
        try:
            asked = await board_reader.screens(self._units_root(cwd), unit, work)
        except Unavailable as e:
            raise Invalid(str(e)) from e
        if not asked.get("retake"):
            return ""
        old = asked.get("manifest") or {}
        addresses = [str(a) for a in old.get("addresses") or []]
        async with self._screens_lock:
            # Review round 1, F3. Asked again past the lock, which another retake may have held
            # for minutes; from here to the end of `take` a pending update waits for it.
            self._refuse_while_updating()
            rid = uuid.uuid4().hex
            self._retakes[rid] = {"workspace": key, "unit": unit, "started": _now()}
            started = datetime.now().timestamp()
            try:
                result = await retake.take(Path(work), addresses, data_dir=self.config.data_dir)
            except asyncio.CancelledError:
                # A client that went away, or the app going down: the group is killed, and
                # the record still says a retake was begun and did not finish.
                gone = {"code": None, "seconds": round(datetime.now().timestamp() - started, 1)}
                try:
                    journal.append(retake.record(key, unit, old, gone, False, "cancelled before it finished", started_by))
                except (BadRecord, Busy):
                    pass
                raise
            finally:
                self._retakes.pop(rid, None)
                self.updater.job_ended()
        ok, detail = retake.judge(result)
        try:
            journal.append(retake.record(key, unit, old, result, ok, detail, started_by))
        except (BadRecord, Busy) as e:
            # Review round 1, F2: only a retake that was taken may say it was.
            if not ok:
                raise Invalid(RETAKE_REFUSED) from e
            raise Invalid(f"the screenshots were taken again, but the run log could not record it: {e}") from e
        if not ok:
            raise Invalid(RETAKE_REFUSED)
        return retake.describe_for_review(old, result.get("manifest_after") or {})

    def _never_driven(self, running: steps_mod.Running, mark: steps_mod.Mark, rid: str) -> None:
        """`0050` review round 2, F2. A task cancelled before its first turn -- a Stop queued
        ahead of it, or "apply now" -- never enters `_drive`, so its `finally` never runs.
        That `finally` is the only thing that frees the mark once the step is handed over, so
        a mark still held when the task is done means the body never ran: give back what it
        would have, and tell the reader instead of leaving it waiting."""
        if self._active.get((running.workspace, running.unit)) is not mark:
            return
        self._release(running.workspace, running.unit, mark)
        self._running.pop(rid, None)
        # `0073`. Never started, so it wrote nothing and has nothing to say.
        self._recorders.pop(running.run, None)
        self.steps.release(running)
        self.updater.job_ended()
        for q in list(running.listeners):
            q.put_nowait(("raise", Invalid(
                f"{running.unit}'s {running.stage} step was cancelled before it began; nothing ran"
            )))

    async def _drive(
        self, running: steps_mod.Running, mark: steps_mod.Mark, runner: Runner, cwd: str, unit: str, stage: str,
        artifact: str, directory: Path, tree: dict[str, Any] | None, base: dict[str, Any] | None,
        rounds_before: set[Any] | None, rid: str, scratch: Path | None, kwargs: dict[str, Any],
        answers_before: bytes | None = None,
    ) -> None:
        """One board step, start to end, as its own task (`0034`).

        What `run_step` used to do inline, unchanged, except that every item goes to the
        step's listeners with `put_nowait` -- this never waits on a reader -- and that a
        `stopped` step records no transition, cleans nothing and posts nothing (R9).

        `answers_before` (`0054` R8) is `pr.md`'s `## Answers` as a `pr` rerun found it; a
        `done` that no longer ends with it says `answers_lost`.
        """

        def tell(item: tuple[str, Any]) -> None:
            for q in list(running.listeners):
                q.put_nowait(item)

        told_done = False
        recorder = running.handle.recorder
        # `0073`. A cancel with no Stop behind it is the app going down (spec C9).
        going_down = False
        try:
            if recorder is not None:
                recorder.start()
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
                scratch.mkdir(parents=True)
            async for item in runner.run(**kwargs, running=running):
                if item[0] == "done":
                    item = ("done", {**item[1], "base": base})
                    if item[1].get("outcome") != "stopped":
                        self._record_transition(cwd, unit, artifact, directory, item[1])
                    if stage == "ship" and tree is not None and item[1].get("outcome") == "done":
                        # R10. Only if `cos.mjs` now says `finished` and GitHub says merged;
                        # otherwise nothing is touched and the board tries again later.
                        item = ("done", {**item[1], "cleanup": await self._cleanup(cwd, unit)})
                    if rounds_before is not None and item[1].get("outcome") == "done":
                        # After `Runner` has written `review.md` (`runner.py:442`), never
                        # before: the artifact does not wait on GitHub (`0021` R6).
                        item = (
                            "done",
                            {**item[1], "comments": await self._post_new_rounds(cwd, unit, rounds_before)},
                        )
                    if stage == "pr" and item[1].get("outcome") != "stopped":
                        # `0055` R3. After `pr.md` is on disk, like the rounds above; a
                        # stopped step posts nothing (`0034` R9, `spec.md ## Answers, câu 2`).
                        item = (
                            "done",
                            {**item[1], "pr_sync": await self._sync_pr(cwd, unit, kwargs.get("pr_before"))},
                        )
                    if (
                        answers_before is not None and item[1].get("outcome") == "done"
                        and not _answers_kept(directory / artifact, answers_before)
                    ):
                        item = ("done", {**item[1], "answers_lost": True})
                    told_done = True
                tell(item)
        except RunError as e:
            tell(("raise", Invalid(str(e))))
            told_done = True
        except asyncio.CancelledError:
            if not running.stop_requested:
                going_down = True
                raise
            # A Stop's cancel that arrived after the runner had already ended.
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
        except Exception as e:  # noqa: BLE001 - the reader raises it, as it always did
            tell(("raise", e))
            told_done = True
        finally:
            if not told_done:
                tell(("raise", Invalid(f"{unit}'s {stage} step ended without an outcome; the app may be shutting down")))
            self._release(running.workspace, running.unit, mark)
            self._running.pop(rid, None)
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
            self.steps.release(running)
            self.updater.job_ended()
            # `0043` R5 a: after the mark is gone, so the pass sees the unit free.
            self._autopilot_nudge(running.workspace)
            if recorder is not None and not recorder.closed:
                # The runner closes it on every road that writes an `end`. Left open means the
                # app is going down -- what can be written is, with no `end` (C9) -- or the
                # runner raised before its own `finally`, which is an ending like any other.
                if going_down:
                    await recorder.abandon()
                else:
                    await recorder.close("failed", "the step ended without an outcome")
            if recorder is not None:
                self._recorders.pop(recorder.run, None)

    async def stop_step(self, cwd: str, unit: str, by: str) -> dict[str, Any]:
        """Stop one running board step (`0034` R2, R5, R6). The route and the page's
        button both call this, and nothing else.

        `by` is a name the person typed, not an identity: the password names nobody. What it
        leaves is an `end` record with `outcome: stopped` and `stopped_by` -- or nothing at
        all when the cancel lands before the step's first turn (`_never_driven`). It opens
        and closes no gate, and starts nothing.
        """
        self._workspace_or_refuse(cwd)
        name = (by or "").strip() or OWNER
        return await self._stop_running(self._journal_key(cwd), unit, name)

    async def _stop_running(self, key: str, unit: str, by: str) -> dict[str, Any]:
        """The Stop itself, shared with `0068`'s "apply now" so a step it cuts ends the
        same way: an `end` record with `stopped` and `stopped_by`, or none for a step
        cancelled before its first turn."""
        try:
            running = self.steps.request_stop(key, unit, by)
        except (steps_mod.NotRunning, steps_mod.Finishing) as e:
            raise Invalid(str(e)) from e
        await running.handle.close()
        if running.task is not None:
            running.task.cancel()
        return {"unit": running.unit, "stage": running.stage, "stopped_by": running.stopped_by}

    def running_steps(self, cwd: str) -> list[dict[str, Any]]:
        """The board steps running now in this workspace (`0034` R13). This process only."""
        self._workspace_or_refuse(cwd)
        return self.steps.listing(self._journal_key(cwd))

    # -- watching a step (`0073`) ---------------------------------------------
    #
    # Two reads and nothing else: no row, no transition, no artifact, no gate asked, and
    # nothing reaches the step (R15). Whoever holds the password or a live session reads
    # every command, path, thought and tool output a step saw (R16).

    def _run_of(self, cwd: str, unit: str, run: str) -> tuple[events.Recorder | None, dict[str, Any] | None]:
        """The recorder running `run`, or its index row -- refused unless it is `unit`'s, in
        this workspace. `(None, None)` for a `run` the run log names with no index row yet."""
        self._workspace_or_refuse(cwd)
        key = self._journal_key(cwd)
        if not run:
            raise Invalid("a run is required")
        recorder = self._recorders.get(run)
        if recorder is not None:
            if (recorder.workspace, recorder.unit) != (key, unit):
                raise Invalid(f"run {run} is not a step of {unit}")
            return recorder, None
        try:
            row = Data(self.config.data_dir).step_run(run)
        except Busy as e:
            raise Invalid(str(e)) from e
        if row is not None:
            if (row["workspace"], row["unit"]) != (key, unit):
                raise Invalid(f"run {run} is not a step of {unit}")
            return None, row
        journal = self._journal()
        try:
            started = journal.records(key, unit, kind="start") if journal is not None else []
        except Busy as e:
            raise Invalid(str(e)) from e
        if any(r.get("run") == run for r in started):
            return None, None
        raise Invalid(f"no such run of {unit}: {run}")

    def events_page(
        self, cwd: str, unit: str, run: str, before: int | None = None,
        limit: int = events.PAGE_DEFAULT, seq: int | None = None,
    ) -> dict[str, Any]:
        """R7. The last `limit` events of `run` below `before`, oldest first -- or, with `seq`,
        that one event whole as stored. The same answer while the step runs (from memory)
        and after it ended (from `step_events`).

        `status`: `running` while this process runs it; `purged` once R14 took its events;
        `ended` with an end; `ended-unknown` when its index row has none -- `0051`'s "a
        `start` with no `end`", read off the index row; `none` for a `run` the run log names
        that never got an index row, as when the app went down before the first write."""
        recorder, row = self._run_of(cwd, unit, run)
        limit = max(1, min(events.PAGE_MAX, int(limit)))
        out: dict[str, Any] = {
            "run": run, "unit": unit, "stage": "", "status": "none", "events": [],
            "first_seq": None, "has_older": False, "last_at": None, "events_lost": 0,
            "purged_at": None,
        }
        if recorder is not None:
            out.update(stage=recorder.stage, status="running", events_lost=recorder.lost)
            if recorder.events:
                out["last_at"] = recorder.events[-1]["at"]
            if seq is not None:
                found = [e for e in recorder.events if e["seq"] == int(seq)]
            else:
                below = [e for e in recorder.events if before is None or e["seq"] < int(before)]
                found = below[-limit:]
                out["has_older"] = len(below) > len(found)
        elif row is not None:
            out.update(
                stage=row["stage"], events_lost=int(row["lost"] or 0), purged_at=row["purged_at"],
                last_at=row["last_at"],
                status=(
                    "purged" if row["purged_at"] else "ended" if row["ended_at"] is not None
                    else "ended-unknown"
                ),
            )
            try:
                data = Data(self.config.data_dir)
                if seq is not None:
                    one = data.step_event(run, int(seq))
                    found = [one] if one is not None else []
                else:
                    found, out["has_older"] = data.step_events_page(run, before, limit)
            except Busy as e:
                raise Invalid(str(e)) from e
        else:
            found = []
        out["events"] = found
        out["first_seq"] = found[0]["seq"] if found else None
        return out

    async def follow_events(
        self, cwd: str, unit: str, run: str, after: int = 0, gather: float = 0.0,
    ) -> AsyncIterator[tuple[str, Any]]:
        """R8. `("events", [...])` for every event of a running `run` past `after`, in order,
        none twice, until its `end`; `("cut", n)` when this follower fell `SUB_LIMIT` behind
        (read again from `n`); one `("status", page)` when the `run` is not running here.

        Subscribes before it reads what is there (`spike.md ## U3`). `gather` > 0 holds each
        batch up to that many seconds, as the page does; an empty batch comes every
        `IDLE_WAKE` seconds with nothing new, so a caller can notice it should stop."""
        recorder, _ = self._run_of(cwd, unit, run)
        if recorder is None:
            yield ("status", self.events_page(cwd, unit, run, limit=1))
            return
        q, backlog = recorder.subscribe(int(after))
        try:
            seen = int(after)
            if backlog:
                seen = backlog[-1]["seq"]
                yield ("events", backlog)
                if backlog[-1]["kind"] == "end":
                    return
            if recorder.closed:
                yield ("status", self.events_page(cwd, unit, run, limit=1))
                return
            loop = asyncio.get_running_loop()
            last = loop.time()
            while True:
                try:
                    first = await asyncio.wait_for(q.get(), events.IDLE_WAKE)
                except asyncio.TimeoutError:
                    yield ("events", [])
                    continue
                wait = last + gather - loop.time()
                if gather > 0 and wait > 0:
                    await asyncio.sleep(wait)
                items = [first]
                while not q.empty():
                    items.append(q.get_nowait())
                batch: list[dict[str, Any]] = []
                cut: int | None = None
                for kind, value in items:
                    if kind == "cut":
                        cut = value
                        break
                    if value["seq"] > seen:
                        batch.append(value)
                        seen = value["seq"]
                last = loop.time()
                if batch:
                    yield ("events", batch)
                if cut is not None:
                    yield ("cut", cut)
                    return
                if batch and batch[-1]["kind"] == "end":
                    return
        finally:
            recorder.unsubscribe(q)

    async def purge_events(self) -> tuple[int, int]:
        """R14, for a caller that holds a `Service`; `coscc/run.py` calls `events.purge_on_start`."""
        return await events.purge(Data(self.config.data_dir), self._journal())

    # -- updating the app (`0068`) -------------------------------------------
    #
    # Every decision is `Updater`'s; these translate its refusals into `Invalid`, as the
    # rest of this file does, so a route maps one exception type.

    def _refuse_while_updating(self) -> None:
        try:
            self.updater.refuse_while_updating()
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    def _update_jobs(self) -> list[dict[str, Any]]:
        """R8. What this process is running now, besides the updater's own build."""
        jobs: list[dict[str, Any]] = []
        for r in self.steps.all():
            jobs.append({
                "kind": "step", "id": f"step:{r.workspace}:{r.unit}", "workspace": r.workspace,
                "unit": r.unit, "stage": r.stage, "started": r.started_at,
            })
        for entry in self._running.values():
            if entry["stage"] == "integrate":
                jobs.append({
                    "kind": "integration", "id": f"integration:{entry['workspace']}:{entry['unit']}",
                    "workspace": entry["workspace"], "unit": entry["unit"], "stage": "integrate",
                    "started": entry["started"],
                })
            elif entry["stage"] == "precedent":
                # `0044`. Waited for like an estimate, never cut: its money is spent either way.
                jobs.append({
                    "kind": "integration", "id": f"precedent:{entry['workspace']}:{entry['unit']}",
                    "workspace": entry["workspace"], "unit": entry["unit"], "stage": "precedent",
                    "started": entry["started"],
                })
            elif entry["stage"] == "estimate":
                # `0074`. Waited for like an integration, never cut: its money is spent either way.
                jobs.append({
                    "kind": "integration", "id": f"estimate:{entry['workspace']}",
                    "workspace": entry["workspace"], "unit": "", "stage": "estimate",
                    "started": entry["started"],
                })
        for entry in self._retakes.values():
            # `0111` review round 1, F3. Waited for like an integration, never cut: no Stop
            # reaches it, and `retake.take` puts `.screens/` back only if it gets to.
            jobs.append({
                "kind": "integration", "id": f"screens:{entry['workspace']}:{entry['unit']}",
                "workspace": entry["workspace"], "unit": entry["unit"], "stage": "screens",
                "started": entry["started"],
            })
        for turn in self.sessions.in_flight():
            jobs.append({"kind": "chat", "id": f"chat:{turn['id']}", "turn": turn["id"],
                         "session_id": turn["session_id"], "workspace": turn["workspace"],
                         "started": turn["started"]})
        return jobs

    async def _update_cut(self, job: dict[str, Any], by: str) -> bool:
        """R10. A step through Stop's own road; a chat turn closed. Never an integration."""
        if job["kind"] == "step":
            try:
                await self._stop_running(job["workspace"], job["unit"], by)
            except Invalid:
                return False  # already ended, or writing its artifact: it is waited for
            return True
        if job["kind"] == "chat":
            return await self.sessions.cut_turn(job["turn"])
        return False

    def update_status(self) -> dict[str, Any]:
        """`Updater.status`, unchanged, with `0082` R9's `line`, `local_line` and `actions`."""
        status = self.updater.status()
        return {**status, **update_words(status)}

    def update_cut_list(self) -> dict[str, Any]:
        try:
            return self.updater.cut_list()
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    async def update_apply(self, channel: str, mode: str, by: str, token: str) -> dict[str, Any]:
        try:
            return await self.updater.apply(channel, mode, (by or "").strip() or OWNER, token)
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    def update_cancel(self, by: str) -> dict[str, Any]:
        try:
            return self.updater.cancel((by or "").strip() or OWNER)
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    def update_build_local(self, by: str) -> dict[str, Any]:
        try:
            return self.updater.build_local((by or "").strip() or OWNER)
        except updater_mod.Refused as e:
            raise _as_invalid(e) from e

    async def shutdown(self) -> None:
        """Cancel every step still running, and wait for them, 10 seconds at most.

        No `end` is written for them (C6): a step with no `end` is what an app that went
        down in the middle of it looks like, and that is what happened.
        """
        # `0043`: the autopilot first, so no pass starts a step while the rest go down.
        for key in list(self._autopilot_tasks):
            self.autopilot_stop(key)
        for t in list(self._autopilot_pending):
            t.cancel()
        # `0100` R6. A CI ask holds nothing worth waiting for.
        for t in list(self._ci_asks.values()):
            t.cancel()
        tasks = [r.task for r in self.steps.all() if r.task is not None and not r.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=10)

    async def _post_new_rounds(
        self, cwd: str, unit: str, before: set[Any]
    ) -> list[dict[str, Any]]:
        """`0021` R2. Post every round the step just added. Never raises.

        What happened to each is in the run log whatever it was, and the board shows a
        round that did not make it as *not on the PR* with the reason.
        """
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            return [{"round": None, "state": "failed", "url": "", "reason": str(e)}]
        found = next((u for u in data["units"] if u["name"] == unit), None)
        if found is None:
            return []
        out = []
        for rnd in found.get("rounds") or []:
            if rnd.get("n") in before:
                continue
            async with self._comment_lock:
                out.append(await self._post_round(cwd, found, rnd))
        return out

    async def post_review_comment(self, cwd: str, unit: str, round_n: Any) -> dict[str, Any]:
        """`0021` R8, R9. Post one review round to the unit's pull request, or say it is there.

        The body is built from the round as `cos.mjs` read it out of `review.md`; nothing a
        caller sends reaches GitHub but the unit's name and the round's number. Not an
        approval, and it opens no gate: neither gate reads comments (R10).
        """
        self._workspace_or_refuse(cwd)
        try:
            n = int(round_n)
        except (TypeError, ValueError):
            raise Invalid(f"a round is named by its number, got {round_n!r}") from None
        async with self._comment_lock:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            rnd = next((r for r in found.get("rounds") or [] if r.get("n") == n), None)
            if rnd is None:
                have = ", ".join(str(r.get("n")) for r in found.get("rounds") or []) or "none"
                raise Invalid(f"review.md of {unit} has no round {n} (it has {have})")
            return await self._post_round(cwd, found, rnd)

    async def _post_round(
        self, cwd: str, found: dict[str, Any], rnd: dict[str, Any]
    ) -> dict[str, Any]:
        """Post one round and write one `pr-comment` row saying how it went (R13).

        The caller holds `_comment_lock`. A row that cannot be written is dropped rather
        than turned into a failure: the comment is on GitHub or it is not, and that is what
        the person asked about. The next board read then shows the round as not posted, and
        a second press finds the marker and says `already`.
        """
        unit = found["name"]
        n = rnd.get("n")
        pr_url = (found.get("pr") or {}).get("url") or ""
        result = await prcomment.post(
            unit, n, rnd.get("verdict"), rnd.get("text") or "", pr_url,
            str(Path(cwd).expanduser().resolve()),
        )
        record: dict[str, Any] = {
            "kind": "pr-comment",
            "workspace": self._journal_key(cwd),
            "unit": unit,
            "stage": "review",
            "round": n,
            "pr": pr_url,
            "outcome": result.state,
        }
        if result.state == "failed":
            record["detail"] = result.reason
        else:
            record["comment_url"] = result.url
        journal = self._journal()
        if journal is not None:
            try:
                journal.append(record)
            except (Busy, BadRecord, OSError):
                pass
        return {"unit": unit, "round": n, "pr": pr_url, **result.as_dict()}

    async def _sync_pr(self, cwd: str, unit: str, pr_before: str | None) -> dict[str, Any]:
        """`0055` R3, R5. Put `pr.md`'s title and body onto its pull request. Never raises.

        Called from `_drive` after a `pr` step that was not stopped, and from nowhere else
        (R4). The words are `cos.mjs pr-text`'s; `prsync` compares and writes. A `pr.md` that
        is not accepted or names no pull request is `skipped` with no `gh` call. One
        `pr-sync` row says how it went, `existed` from the lookup before the step -- `None`
        when that lookup could not answer, never a guess; a row
        that cannot be written is dropped, as `_post_round` drops one. `pr.md` is never
        touched.
        """
        url, outcome, detail = "", "failed", ""
        try:
            text = await board_reader.pr_text(self._units_root(cwd), unit)
            url = str(text.get("url") or "")
            if "error" in text:
                outcome, detail = "skipped", str(text["error"])
            elif text.get("status") != "accepted":
                outcome, detail = "skipped", f"pr.md is {text.get('status') or 'without a status'}, not accepted"
            elif not url or not prcomment.PR_URL_RE.match(url):
                outcome, detail = "skipped", f"pr.md names no pull request URL: {url!r}"
            else:
                result = await prsync.sync(
                    url, text.get("title"), str(text.get("body") or ""),
                    str(Path(cwd).expanduser().resolve()),
                )
                outcome, detail = result.state, result.reason
        except Unavailable as e:
            detail = str(e)
        except Exception as e:  # noqa: BLE001 — R5: the step is done whatever this does
            detail = str(e) or type(e).__name__
        record: dict[str, Any] = {
            "unit": unit,
            "stage": "pr",
            "pr": url,
            "existed": None if pr_before is None else bool(pr_before),
            "outcome": outcome,
        }
        if outcome in ("failed", "skipped"):
            record["detail"] = detail
        journal = self._journal()
        if journal is not None:
            try:
                journal.append({"kind": "pr-sync", "workspace": self._journal_key(cwd), **record})
            except (Busy, BadRecord, OSError):
                pass
        return record

    def _record_transition(
        self, cwd: str, unit: str, artifact: str, directory: Path, done: dict[str, Any]
    ) -> None:
        """`0014` R6. One transition per step that finished, written as it happens.

        This is the first writer into `0013`'s log that is not the git import.
        `.cos/0013_.../ship.md` said the loop would come back here: history imported from
        git carries no actor and no session, because git knows neither, so the provenance
        that unit built is only ever true of work done **after** it. This is that work.

        Never raises into the run. A step that did its job and then failed to be recorded
        has still done its job, and turning a bookkeeping failure into a failed step would
        cost real money for nothing. The failure is dropped rather than shown, and that is
        a cost `0014` `impl.md` states rather than hides.
        """
        if done.get("outcome") != "done":
            return
        history = self._history()
        if history is None:
            return
        try:
            text = (directory / artifact).read_text(encoding="utf-8", errors="replace")
            found = STATUS_RE.search(text)
            if not found:
                return
            history.record(
                self._journal_key(cwd),
                unit,
                artifact,
                found.group(1).lower(),
                actor=f"stage:{done.get('stage') or ''}",
                session=str(done.get("session_id") or "") or UNKNOWN,
                source=f"run:{done.get('stage') or ''}",
            )
        except (OSError, BadTransition, Busy):
            return

    def _create_lock(self, cwd: str) -> asyncio.Lock:
        """`0017` R8. One lock per workspace, held across numbering and making the tree."""
        return self._create_locks.setdefault(units.key(cwd), asyncio.Lock())

    async def create_unit(self, cwd: str, slug: str, brief: str = "") -> dict[str, Any]:
        """`0014` R1. Start a work unit, in the product's store rather than the repository.

        The number and the slug grammar are `cos.mjs`'s, through `coscc/units.py`. Nothing
        here is a second opinion about either — `.claude/CLAUDE.md` says that script is the
        one place the loop is defined.

        Since `0017` it also opens the unit's own worktree, detached at the workspace's
        `main`. A worktree that cannot be opened does not undo the unit: the result says
        why under `worktree.error`, and the next step that needs the tree tries again.
        """
        self._workspace_or_refuse(cwd)
        root = Path(cwd).expanduser().resolve()
        async with self._create_lock(cwd):
            reserve = [root]
            try:
                reserve += [
                    Path(t["path"]) for t in (await gitops.worktree_list(root))[1:]
                    if (Path(t["path"]) / units.COS_DIR).is_dir()
                ]
            except GitError:
                pass
            try:
                made = {
                    "cwd": cwd,
                    # The host repository's own `.cos/` counts toward the number, so a unit
                    # started here cannot take a number already used there
                    # (`0001_product-describes-a-state-it-is-not-in` R10), and since `0017`
                    # so does every worktree's. Counting is `cos.mjs`'s.
                    **units.create(cwd, slug, brief, self.config.data_dir, reserve_from=reserve),
                }
            except (CannotCreate, BadUnit) as e:
                raise Invalid(str(e)) from e
            try:
                made["worktree"] = await worktrees.ensure(
                    cwd, made["unit"], None, self.config.data_dir
                )
            except (GitError, BadUnit) as e:
                made["worktree"] = {"path": "", "error": str(e)}
        return made

    async def _worktree(self, cwd: str, unit: str, strict: bool = False) -> dict[str, Any] | None:
        """The unit's worktree, opened on its branch if the branch exists and it is not.

        None when there is none and none can be opened — the workspace is dirty on the
        unit's branch, or is not a git repository at all. `strict` turns the first of those
        into `Invalid`: a step must not run on a tree that is not on its unit's branch.
        """
        try:
            found = await worktrees.find(cwd, unit, self.config.data_dir)
            if found is not None and found["branch"]:
                return {"path": found["path"], "branch": found["branch"]}
            try:
                branch = units.branch_name(cwd, unit, self.config.data_dir)
                await gitops.rev_parse(Path(cwd).expanduser().resolve(), f"refs/heads/{branch}")
            except (CannotCreate, BadUnit, GitError):
                branch = None
            if branch is None:
                return {"path": found["path"], "branch": ""} if found else None
            # The branch exists and the tree is not on it: open it there (`worktrees.ensure`).
            made = await worktrees.ensure(cwd, unit, branch, self.config.data_dir)
            return {"path": made["path"], "branch": made["branch"], "base": made.get("base")}
        except (GitError, BadUnit) as e:
            if strict:
                raise Invalid(f"{unit}'s worktree could not be opened on its branch: {e}") from e
            return None

    async def answer(
        self,
        cwd: str,
        unit: str,
        artifact: str,
        question: Any,
        answer: str,
        answered_by: str,
    ) -> dict[str, Any]:
        """`0016` R2–R4. A person answers one item under an artifact's `## Open questions`.

        The only route in this app that writes into an artifact a stage wrote, and it only
        ever **appends**: the file is opened `"a"`, never `"w"`, so every byte above the
        `## Answers` block is the byte the stage left there (R4). What counts as a question
        and whether it is answered is `cos.mjs`'s decision, read through one board read;
        nothing here parses `## Open questions` a second time (R7).

        Not an approval, and it starts nothing itself; with the autopilot on, the pass it
        nudges may start the next stage (`0043`), or run again a draft this answer finished
        (`0106`). `answered_by` is whatever name the caller
        typed: no route in this app has a login, so it is a claim, not an identity.

        `0028`: `question` may be `"F<n>"`, a finding `cos.mjs` lists in the unit's
        `personFindings`; then `artifact` must be `review.md` and the block is `### F<n>`.
        Unlike a numbered answer, that block is read by `cos.mjs next` and the `ship` gate.
        """
        self._workspace_or_refuse(cwd)
        name = str(answered_by or "").strip() or OWNER
        # `0044` R11. Jera's name marks the road a block came by, not who typed it, so a
        # person may not take it.
        if precedent_mod.is_jera(name):
            raise Invalid(f"{precedent_mod.AGENT} is the agent that answers from precedent; answer under another name")
        done = await self._append_answers(
            cwd, unit, [(artifact, question, answer)], name, "product", f"human:{name}", "answer",
        )
        written = done["written"][0]
        # `0043` R5 b. The answer itself still starts nothing; a pass may, if the switch is on.
        self._autopilot_nudge(self._journal_key(cwd))
        return {
            "unit": unit,
            "artifact": written["artifact"],
            "question": written["question"],
            "answered_by": name,
            "date": done["date"],
        }

    async def _append_answers(
        self,
        cwd: str,
        unit: str,
        items: list[tuple[str, Any, str]],
        answered_by: str,
        via: str,
        actor: str,
        source: str,
    ) -> dict[str, Any]:
        """The one place that appends a block under `## Answers` (`0044` Design 3): a person's
        through `answer`, Jera's through `precedent`. `items` is `[(artifact, question, text)]`,
        all checked and written under one hold of `_answer_lock` and one board read.

        A person's refusal raises, as `answer` always has. With `via == "precedent"` every
        item is judged alone and a refused one is `skipped` with its reason, never raised
        (R7): `review.md` and any `F<n>` before a file is opened (R3), and a question the
        board read already shows answered — a person got there while Jera ran.
        Returns `{written: [{artifact, question}], skipped: [{artifact, question, reason}],
        date}`.
        """
        jera = via == precedent_mod.VIA
        name = answered_by
        today = date.today().isoformat()
        written: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        async with self._answer_lock:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            for artifact, question, answer in items:
                try:
                    number, finding = self._append_one(
                        cwd, unit, found, artifact, question, str(answer or "").strip("\n"), name, via,
                        today, jera,
                    )
                except Invalid as e:
                    if not jera:
                        raise
                    skipped.append({"artifact": artifact, "question": question, "reason": str(e)})
                    continue
                written.append({"artifact": artifact, "question": finding or number})

        # `0016` plan, in place of spec R9: the store is not a git repository, so there is
        # no commit to make. The provenance this app already keeps is a row in `outputs`.
        # Never raises: the answer is on disk, and failing the request now would tell the
        # person it was not.
        history = self._history()
        if history is not None:
            for w in written:
                try:
                    history.add_output(
                        self._journal_key(cwd),
                        unit,
                        w["artifact"].removesuffix(".md"),
                        "deliverable",
                        w["artifact"],
                        actor=actor,
                        source=source,
                    )
                except (OSError, BadTransition, Busy):
                    pass

        self._journal_answers(cwd, unit, found, written, via)
        return {"written": written, "skipped": skipped, "date": today}

    def _journal_answers(
        self, cwd: str, unit: str, found: dict[str, Any], written: list[dict[str, Any]], via: str,
    ) -> None:
        """`0106` R4. One `answer` record per block written, so the run log can tell which
        answer finished a draft's questions (`completes`) and what the autopilot then did.
        It starts nothing. Never raises, like the `outputs` row above it."""
        journal = self._journal()
        if journal is None or not written:
            return
        key = self._journal_key(cwd)
        try:
            listed, _ = backlog.shortlist_of(journal.records(workspace=key, kind="shortlist"))
            on = bool(self._autopilot_values(key)["autopilot"])
        except (Busy, OSError):
            return
        stages = {s["file"]: s for s in found.get("stages") or []}
        given: dict[str, set[Any]] = {}
        for w in written:
            artifact = w["artifact"]
            given.setdefault(artifact, set()).add(w["question"])
            row = stages.get(artifact) or {}
            try:
                journal.append({
                    "kind": "answer", "workspace": key, "unit": unit, "stage": row.get("stage", ""),
                    "artifact": artifact, "question": w["question"], "via": via,
                    "status": row.get("status", ""),
                    "completes": autopilot.answer_completes(found, artifact, given[artifact]),
                    "autopilot": on, "shortlisted": unit in ((listed or {}).get("units") or []),
                    "held": bool(found.get("hold")),
                })
            except (BadRecord, Busy):
                pass

    def _append_one(
        self, cwd: str, unit: str, found: dict[str, Any], artifact: str, question: Any, text: str,
        name: str, via: str, today: str, jera: bool,
    ) -> tuple[int | str, str]:
        """Check one answer against the board read `found` and append its block. Raises
        `Invalid` before a byte is written; returns `(number, finding)`."""
        if jera:
            # `0044` R3. Decided by the name of the file and the shape of the heading, never
            # by what the session said.
            if artifact == "review.md" or re.fullmatch(r"F\d+", str(question).strip()):
                raise Invalid(f"{precedent_mod.AGENT} never answers in review.md or a finding")
            if any(q.get("artifact") == artifact and q.get("n") == question and q.get("answered")
                   for q in found.get("questions") or []):
                raise Invalid(f"{artifact} question {question} was answered while {precedent_mod.AGENT} ran")
        # `0028`. A finding the last review round confirmed needs a person is answered
        # by its id, `F<n>`, into `review.md` -- and only while `cos.mjs` lists it in
        # `personFindings`, so what may be answered is its decision, not this route's.
        finding = str(question).strip() if isinstance(question, str) else ""
        finding = finding if re.fullmatch(r"F\d+", finding) else ""
        if finding:
            if artifact != "review.md":
                raise Invalid(f"a finding is answered in review.md, not {artifact}")
            awaited = [p["id"] for p in found.get("person_findings") or []]
            if finding not in awaited:
                raise Invalid(
                    f"{finding} is not a finding the last review round of {unit} "
                    "confirmed needs a person"
                    + (f" (those are {', '.join(awaited)})" if awaited else "")
                )
            number: int | str = finding
        else:
            asked = [q for q in found.get("questions") or [] if q.get("artifact") == artifact]
            if not asked:
                raise Invalid(f"{artifact} in {unit} has no numbered item under ## Open questions")
            try:
                number = int(question)
            except (TypeError, ValueError):
                raise Invalid(f"a question is named by its number, got {question!r}") from None
            if number not in {q["n"] for q in asked}:
                raise Invalid(
                    f"{artifact} has no question {number} "
                    f"(it has {', '.join(str(q['n']) for q in asked)})"
                )
        if not text.strip():
            raise Invalid("the answer is empty")
        if not name or "\n" in name or "\r" in name:
            raise Invalid("say who is answering, on one line")
        nxt = str(found.get("next") or "")
        if nxt == "finished" or nxt.startswith("closed"):
            raise Invalid(f"{unit} is {nxt}; its questions can no longer be answered")
        # A line that reads as a heading would end this block early or open another,
        # and `cos.mjs` would then read the answer wrongly. Refusing is cheaper and more
        # honest than escaping somebody's words.
        if any(line.lstrip().startswith("#") for line in text.splitlines()):
            raise Invalid("no line of an answer may start with #")

        path = self._unit_dir(cwd, unit) / artifact
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as e:
            raise Invalid(f"could not read {artifact}: {e}") from e
        lines = existing.splitlines()
        heading = next((i for i, l in enumerate(lines) if l.rstrip() == "## Answers"), None)
        if heading is not None and any(l.startswith("## ") for l in lines[heading + 1:]):
            raise Invalid(
                f"{artifact} has a section after its ## Answers, so a block appended at "
                "the end would not be read as an answer"
            )

        block = ""
        if existing and not existing.endswith("\n"):
            block += "\n"
        if heading is None:
            block += "\n## Answers\n"
        block += f"\n### {finding}\n" if finding else f"\n### Câu {number}\n"
        block += (
            f"Answered by: {name}. Date: {today}. Via: {via}.\n\n"
            f"{text}\n"
        )
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(block)
        except OSError as e:
            raise Invalid(f"could not write {artifact}: {e}") from e
        return number, finding

    async def record_outcome(
        self,
        cwd: str,
        unit: str,
        result: str,
        measured_by: str,
        source: str = "",
        reason: str = "",
        note: str = "",
        recorded_by: str = "",
    ) -> dict[str, Any]:
        """`0047` R1–R4, R7. Record whether a finished unit met its intent's outcome.

        Built on `answer()`: the same lock, the same one board read, the same refusal when a
        section follows `## Answers`, and the same `"a"` open, so every byte above the block
        stays the byte the stage left there. The block is `### Outcome` under `intent.md`'s
        `## Answers`; whether it is valid and which one is in force is `cos.mjs`'s reading.

        Not an approval, and it starts nothing: no gate reads the block, and a finished unit
        stays finished. `recorded_by` and `measured_by` are names somebody typed; no route
        has a login, so both are claims. `source` is not checked against anything.
        """
        self._workspace_or_refuse(cwd)
        name = str(recorded_by or "").strip() or OWNER
        measurer = str(measured_by or "").strip()
        word = str(result or "").strip()
        src = str(source or "").strip()
        why = str(reason or "").strip()
        text = str(note or "").strip("\n")
        async with self._answer_lock:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e

            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            nxt = str(found.get("next") or "")
            if nxt != "finished":
                raise Invalid(f"{unit} is {nxt or 'not finished'}; an outcome is recorded only on a finished unit")
            if word not in OUTCOME_RESULTS:
                raise Invalid(f"the result is one of {', '.join(OUTCOME_RESULTS)}, got {word!r}")
            kind = OUTCOME_RESULTS[word]
            if not name or "\n" in name or "\r" in name:
                raise Invalid("say who is recording, on one line")
            if not measurer or "\n" in measurer or "\r" in measurer:
                raise Invalid("say who measured it — agent, or a person's name — on one line")
            if "\n" in src or "\r" in src:
                raise Invalid("the source is one line")
            if "\n" in why or "\r" in why:
                raise Invalid("the reason is one line")
            if kind != "unmeasurable" and not src:
                raise Invalid("the result needs a source: where the figure it rests on came from")
            if kind == "unmeasurable" and not why:
                raise Invalid("the result needs a reason: why it could not be measured")
            # The same refusal as `answer()`, and for the same reason: a heading would end
            # this block early or open another, and `cos.mjs` would read it wrongly.
            if any(line.lstrip().startswith("#") for line in [src, why, *text.splitlines()]):
                raise Invalid("no line of an outcome may start with #")

            path = self._unit_dir(cwd, unit) / "intent.md"
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as e:
                raise Invalid(f"could not read intent.md: {e}") from e
            lines = existing.splitlines()
            heading = next((i for i, l in enumerate(lines) if l.rstrip() == "## Answers"), None)
            if heading is not None and any(l.startswith("## ") for l in lines[heading + 1:]):
                raise Invalid(
                    "intent.md has a section after its ## Answers, so a block appended at "
                    "the end would not be read as an outcome"
                )

            today = date.today().isoformat()
            block = ""
            if existing and not existing.endswith("\n"):
                block += "\n"
            if heading is None:
                block += "\n## Answers\n"
            block += (
                "\n### Outcome\n"
                f"Answered by: {name}. Date: {today}. Via: product.\n\n"
                f"Result: {word}\n"
                f"Measured by: {measurer}\n"
            )
            if src:
                block += f"Source: {src}\n"
            if why:
                block += f"Reason: {why}\n"
            if text.strip():
                block += f"\n{text}\n"
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(block)
            except OSError as e:
                raise Invalid(f"could not write intent.md: {e}") from e

        # As in `answer()`: the store has no git, so the provenance is a row in `outputs`,
        # and a failure to write it never fails a block already on disk.
        history = self._history()
        if history is not None:
            try:
                history.add_output(
                    self._journal_key(cwd),
                    unit,
                    "intent",
                    "deliverable",
                    "intent.md",
                    actor=f"human:{name}",
                    source="outcome",
                )
            except (OSError, BadTransition, Busy):
                pass

        return {
            "unit": unit,
            "result": word,
            "measured_by": measurer,
            "recorded_by": name,
            "date": today,
        }

    async def _append_to_answers(self, path: Path, block: str, what: str) -> None:
        """Append `block` to the end of `path`, under its `## Answers`, opening that section
        when the file has none -- the one way the app writes into an artifact a stage wrote,
        never rewriting a byte above it. `0045`'s hold wrote this way first; `0054`'s
        `### Rerun` block does too. Under `_answer_lock`, so two appends never interleave.
        `what` names the block in the refusal when a section follows `## Answers`, where an
        appended block would not be read at all."""
        name = path.name
        async with self._answer_lock:
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as e:
                raise Invalid(f"could not read {name}: {e}") from e
            lines = existing.splitlines()
            heading = next((i for i, l in enumerate(lines) if l.rstrip() == "## Answers"), None)
            if heading is not None and any(l.startswith("## ") for l in lines[heading + 1:]):
                raise Invalid(
                    f"{name} has a section after its ## Answers, so a block appended at "
                    f"the end would not be read as {what}"
                )
            text = ""
            if existing and not existing.endswith("\n"):
                text += "\n"
            if heading is None:
                text += "\n## Answers\n"
            text += block
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(text)
            except OSError as e:
                raise Invalid(f"could not write {name}: {e}") from e

    async def hold(self, cwd: str, unit: str, to: str, reason: str, by: str) -> dict[str, Any]:
        """`0045`. A person pauses, drops or resumes a unit (`to`: paused, dropped, active).

        Appends one `### Paused|Dropped|Resumed` block under `intent.md ## Answers` — the
        way `answer` appends, never rewriting a byte above it (R8) — and one `hold` row to
        the run log (R10). Which moves exist is `cos.mjs`'s `holdMoves`, read off the board;
        nothing here decides it (R6). Dropping also closes the unit's open pull request with
        this machine's `gh` login and removes its worktree (R12); a failure there is
        reported, never raised, and undoes nothing.

        Not an approval, and it starts nothing (R16): no step runs, no session opens, even on
        a resume. `by` is whatever name the caller typed. Refused while a step or an
        integration of this unit runs in this process (R13); it holds that same mark itself
        while it writes, so no step can begin halfway through.
        """
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so a hold cannot be recorded — set COS_WORKING_DIR")
        if not unit:
            raise Invalid("name a work unit")
        to = str(to or "").strip()
        reason = str(reason or "").strip()
        by = str(by or "").strip() or OWNER
        directory = self._unit_dir(cwd, unit)
        key = self._journal_key(cwd)
        # No `await` between the check and the take: the same mark `run_step` and
        # `integrate` take, so neither starts while this writes. When the unit is already
        # held, the board is still read, so a move refused for another reason says that one.
        held = self._active.get((key, unit))
        mark = self._take(key, unit, "hold") if held is None else None
        try:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            said = hold_rules.refusal(found, to, reason, by, steps_mod.describe(unit, held) if held else "")
            if said:
                raise Invalid(said)
            assert found is not None
            from_ = (found.get("hold") or {}).get("state") or "active"
            today = date.today().isoformat()
            await self._append_to_answers(
                directory / "intent.md", hold_rules.block(to, by, today, reason), "a hold"
            )

            effects: list[dict[str, str]] = []
            if to == "dropped":
                try:
                    branch = units.branch_name(cwd, unit, self.config.data_dir)
                except (CannotCreate, BadUnit):
                    branch = ""
                root = str(Path(cwd).expanduser().resolve())
                effects.append(await hold_rules.close_pr(root, branch))
                effects.append(await hold_rules.remove_tree(cwd, unit, self.config.data_dir))
            try:
                journal.append(hold_rules.record(
                    workspace=key, unit=unit, from_=from_, to=to, reason=reason, by=by, effects=effects,
                ))
            except (BadRecord, Busy):
                # The block is on disk and `cos.mjs` reads it; failing now would tell the
                # person their decision was not recorded when it was.
                pass
        finally:
            if mark is not None:
                self._release(key, unit, mark)
        return {"unit": unit, "from": from_, "to": to, "reason": reason, "by": by, "date": today, "effects": effects}

    # -- backlog (`0074`) -----------------------------------------------------

    async def _backlog_context(self, cwd: str) -> tuple[Journal, str, dict[str, Any]]:
        """The run log, its key and one board read. The read is `node`, so it happens before
        any transaction is opened (`plan.md` Risk 5): only the run log's part of a check is
        read inside one."""
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so nothing can be recorded — set COS_WORKING_DIR")
        try:
            data = await board_reader.read(self._units_root(cwd))
        except Unavailable as e:
            raise Invalid(str(e)) from e
        return journal, self._journal_key(cwd), data

    @staticmethod
    def _append_checked(journal: Journal, record: dict[str, Any], check: Any) -> dict[str, Any]:
        """`check(rows)` returns a refusal or `""`, on the rows read inside the transaction."""
        def refuse(rows: list[dict[str, Any]]) -> None:
            said = check(rows)
            if said:
                raise BadRecord(said)

        try:
            return journal.append_checked(record, backlog.KINDS, refuse)
        except (BadRecord, Busy) as e:
            raise Invalid(str(e)) from e

    async def record_estimate(
        self, cwd: str, unit: str, value: Any, effort: Any, basis: Any, by: Any,
    ) -> dict[str, Any]:
        """R2, R6, R7. A person's estimate: always a new record, never an edit of an old one."""
        by = str(by or "").strip() or OWNER
        journal, key, data = await self._backlog_context(cwd)
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if unit not in [u["name"] for u in data["units"] if backlog.in_backlog(u)]:
            raise Invalid(f"{unit or 'that unit'} is not in the backlog")
        said = backlog.check_estimate(value, effort, basis, by, agent=False)
        if said:
            raise Invalid(said)
        record = {
            "kind": "estimate-value", "workspace": key, "unit": unit, "value": value, "effort": effort,
            "effort_source": "person", "similar": [], "basis": basis, "effort_basis": "", "by": by,
        }
        return {"recorded": self._append_checked(journal, record, lambda rows: "")}

    async def record_relation(
        self, cwd: str, unit: str, other: str, rtype: str, op: str, reason: str, by: str,
    ) -> dict[str, Any]:
        """R8. Add or remove one relation; checked against the ones in effect inside the write."""
        by = str(by or "").strip() or OWNER
        journal, key, data = await self._backlog_context(cwd)
        names = [u["name"] for u in data["units"]]
        record = {"kind": "relation", "workspace": key, "unit": unit, "other": other, "type": rtype,
                  "op": op, "reason": reason, "by": by}
        return {"recorded": self._append_checked(journal, record, lambda rows: backlog.check_relation(
            unit, other, rtype, op, reason, by, names, backlog.relations_of(rows),
        ))}

    async def record_shortlist(self, cwd: str, names: Any, reason: str, by: str) -> dict[str, Any]:
        """R10, R12, R13. The whole list, by a person, as one new record."""
        by = str(by or "").strip() or OWNER
        journal, key, data = await self._backlog_context(cwd)
        for what, value in (("reason", reason), ("name", by)):
            said = hold_rules._line_problem(what, value)
            if said:
                raise Invalid(said)
        if backlog.is_agent(by):
            raise Invalid(f"a person's name may not start with {backlog.AGENT_PREFIX!r}")
        waiting = [u["name"] for u in data["units"] if backlog.in_backlog(u)]
        record = {"kind": "shortlist", "workspace": key, "unit": "", "units": names, "reason": reason, "by": by}
        return {"recorded": self._append_checked(journal, record, lambda rows: backlog.check_shortlist(
            names, waiting, backlog.estimates_of(rows),
        ))}

    async def propose_estimates(self, cwd: str) -> AsyncIterator[tuple[str, Any]]:
        """R17, R18. One paid session proposes estimates and relations for the whole backlog.

        Refused before anything is spent while another proposal of this workspace runs: the
        unit `""` in `_active`, which no real unit is ever called. Streams like `integrate`.
        Writes `start`/`end` (stage `estimate`, unit `""`) so Activity counts the money, one
        `estimate` record for the run, and each valid part of the reply through
        `append_checked`. A reply over a ceiling or not JSON writes no estimate.
        """
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so a proposal cannot be recorded — set COS_WORKING_DIR")
        key = self._journal_key(cwd)
        held = self._active.get((key, ""))
        if held is not None:
            raise Invalid(f"a proposal for this workspace is already running since {held.started_at}; wait for it to end")
        mark = steps_mod.Mark("estimate", "estimate", "")
        self._active[(key, "")] = mark
        rid = self._mark_running(key, "", "estimate", "estimate")
        started = ended = False
        try:
            try:
                data = await board_reader.read(self._units_root(cwd))
                rows = journal.records(key)
            except Unavailable as e:
                raise Invalid(str(e)) from e
            except Busy as e:
                raise Invalid(str(e)) from e
            unit_timelines = timelines_of(rows)
            found = backlog.measured(unit_timelines, data["units"])
            # `0092` R11: how many finished units are left out for a cost nobody knows.
            left_out = len(backlog.undetermined(unit_timelines, data["units"]))
            waiting = [u["name"] for u in data["units"] if backlog.in_backlog(u)]
            if not waiting:
                raise Invalid("the backlog is empty; there is nothing to estimate")
            root = self._units_root(cwd)

            def read(unit: str, name: str) -> str:
                try:
                    return (root / unit / name).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return ""

            texts = [
                {"unit": n, "idea": backlog.section(read(n, "idea.md"), "In their own words"),
                 "problem": backlog.section(read(n, "intent.md"), "Problem"),
                 "outcome": backlog.section(read(n, "intent.md"), "Proposed outcome")}
                for n in waiting
            ]
            finished = [
                {"unit": n, "title": backlog.title_of(read(n, "intent.md")), **f} for n, f in found.items()
            ]
            prompt = backlog.build_prompt(texts, finished, left_out)
            grant = grant_for("estimate")
            defaults, _ = models.load_defaults()
            model, model_source, effort, effort_source = models.resolve(
                models.ESTIMATE, None, self._model_overrides()[0], self._effort_overrides()[0], defaults,
                self.config.model,
            )
            try:
                journal.started(key, "", "estimate", "manual", started_by="person",
                                prompt_chars=len(prompt), granted=[],
                                max_turns=grant.max_turns, model=model, model_source=model_source,
                                effort=effort, effort_source=effort_source)
                started = True
            except (BadRecord, Busy):
                pass
            reply, end, failure = "", {}, ""
            try:
                async for kind, payload in self.sessions.stream(
                    cwd, prompt, None, max_turns=grant.max_turns, tools=[],
                    # `tools=[]` still lets MCP tools through (`sessions.py`); the gate refuses them.
                    can_use_tool=permission_gate(grant, cwd, Denials()),
                    max_budget_usd=grant.max_budget_usd, step=StepHandle(),
                    **({"model": model} if model is not None else {}),
                    **({"effort": effort} if effort is not None else {}),
                ):
                    if kind == "chunk":
                        reply += payload
                        yield ("chunk", payload)
                    elif kind == "session":
                        end["session_id"] = str(payload)
                    elif kind == "done":
                        end.update(session_id=payload.get("session_id", end.get("session_id", "")),
                                   cost=payload.get("cost") or {},
                                   terminal_reason=str(payload.get("terminal_reason") or ""))
            except Exception as e:  # noqa: BLE001 — recorded as the reason
                failure = f"the session failed: {e}"
            cost = end.get("cost") or {}
            terminal = end.get("terminal_reason", "")
            if not failure and any(m in terminal for m in CEILING_MARKERS):
                failure = f"the session stopped at a ceiling ({terminal}); nothing was recorded"
            session = end.get("session_id", "")
            names = [u["name"] for u in data["units"]]
            parsed = {"records": [], "rejected": [], "failed": failure or None}
            if not failure:
                parsed = backlog.parse_proposal(
                    reply, waiting, names, found, session, backlog.relations_of(rows), workspace=key,
                    undetermined=left_out,
                )
            written, rejected = 0, list(parsed["rejected"])
            for rec in parsed["records"]:
                if rec["kind"] == "relation":
                    check = (lambda rec: lambda live: backlog.check_relation(
                        rec["unit"], rec["other"], rec["type"], "add", rec["reason"], rec["by"], names,
                        backlog.relations_of(live), agent=True,
                    ))(rec)
                else:
                    check = lambda live: ""  # noqa: E731
                try:
                    self._append_checked(journal, rec, check)
                    written += 1
                except Invalid as e:
                    rejected.append({"unit": rec["unit"], "reason": str(e)})
            outcome = "failed" if parsed["failed"] else "done"
            try:
                journal.finished(key, "", "estimate", outcome, session_id=session,
                                 detail=parsed["failed"], **cost)
                ended = True
            except (BadRecord, Busy):
                pass
            summary = {
                "kind": "estimate", "workspace": key, "unit": "", "stage": "estimate", "session_id": session,
                "cost_usd": cost.get("cost_usd"), "turns": cost.get("turns"), "written": written,
                "rejected": rejected, "outcome": outcome, "detail": parsed["failed"],
            }
            try:
                summary = journal.append(summary)
            except (BadRecord, Busy):
                pass
            yield ("done", {"estimate": summary})
        finally:
            if started and not ended:
                try:
                    journal.finished(key, "", "estimate", "cancelled", detail="the proposal ended before its reply was read")
                except (BadRecord, Busy):
                    pass
            if self._active.get((key, "")) is mark:
                del self._active[(key, "")]
            self._running.pop(rid, None)
            self.updater.job_ended()

    async def precedent(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0044`. Jera answers this unit's open questions from precedent: one paid session.

        Started only by a person's press (`POST /api/units/precedent`, *Ask Jera*) — nothing
        else in the app calls this (R1). Holds the unit like a step (`_take`), so Jera, a
        step, an integration and a hold of one unit exclude each other in this process; a
        session at a terminal is not excluded (`spec.md` C6).

        Writes a `start` and an `end` (stage `precedent`, the unit's own), one `precedent`
        row per question (R9, R12), and each `answer` that survives `precedent.verdicts`
        through `_append_answers` as `Jera`, `Via: precedent`, `actor = agent:Jera` (R7). A
        `needs-person` verdict writes no byte of any artifact. A reply that cannot be read
        writes nothing either, and its tail is kept in the `end` row (R13).
        """
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        journal = self._journal()
        if journal is None:
            raise Invalid("no working folder is set, so nothing Jera says can be recorded — set COS_WORKING_DIR")
        key = self._journal_key(cwd)
        mark = self._take(key, unit, "precedent", "precedent")
        rid = self._mark_running(key, unit, "precedent", "precedent")
        started = ended = False
        try:
            try:
                data = await board_reader.read(self._units_root(cwd))
            except Unavailable as e:
                raise Invalid(str(e)) from e
            found = next((u for u in data["units"] if u["name"] == unit), None)
            if found is None:
                raise Invalid(f"no such work unit in this workspace: {unit}")
            questions = precedent_mod.asked(found)
            if not questions:
                raise Invalid(f"{unit} has no open question Jera may answer")
            prefs = str(self.preferences().get("decision_preferences") or "")
            store = precedent_mod.entries(data["units"], prefs, {unit})
            prompt = precedent_mod.build_prompt(questions, store)
            grant = grant_for("precedent")
            defaults, _ = models.load_defaults()
            model, model_source, effort, effort_source = models.resolve(
                models.PRECEDENT, None, self._model_overrides()[0], self._effort_overrides()[0], defaults,
                self.config.model,
            )
            try:
                journal.started(key, unit, "precedent", "manual", prompt_chars=len(prompt), granted=[],
                                max_turns=grant.max_turns, model=model, model_source=model_source,
                                effort=effort, effort_source=effort_source, questions=len(questions),
                                entries=len(store))
                started = True
            except (BadRecord, Busy):
                pass
            reply, end, failure = await precedent_mod.ask(self.sessions, cwd, prompt, grant, model, effort)
            cost = end.get("cost") or {}
            session = end.get("session_id", "")
            found_v = {"failed": failure or None, "verdicts": [], "ignored": []}
            if not failure:
                found_v = precedent_mod.verdicts(reply, questions, {e["id"] for e in store})
            if found_v["failed"]:
                try:
                    # R13. The tail is chosen at 2000 characters, not measured.
                    journal.finished(key, unit, "precedent", "failed", session_id=session,
                                     detail=f"{found_v['failed']}; the reply ended: {reply[-2000:]}", **cost)
                    ended = True
                except (BadRecord, Busy):
                    pass
                return {"unit": unit, "outcome": "failed", "detail": found_v["failed"], "written": [],
                        "needs_person": [], "skipped": [], "ignored": [], "cost_usd": cost.get("cost_usd")}

            answers = [v for v in found_v["verdicts"] if v["verdict"] == precedent_mod.ANSWER]
            done = await self._append_answers(
                cwd, unit, [(v["artifact"], v["n"], precedent_mod.block_text(v)) for v in answers],
                precedent_mod.AGENT, precedent_mod.VIA, precedent_mod.ACTOR, "precedent",
            ) if answers else {"written": [], "skipped": []}
            skipped = {(s["artifact"], s["question"]): s["reason"] for s in done["skipped"]}
            for v in found_v["verdicts"]:
                at = (v["artifact"], v["n"])
                row = {"kind": "precedent", "workspace": key, "unit": unit, "artifact": v["artifact"],
                       "n": v["n"], "verdict": v["verdict"], "category": v["category"], "text": v["text"],
                       "reason": v["reason"], "cites": v["cites"], "session_id": session,
                       "written": v["verdict"] == precedent_mod.ANSWER and at not in skipped}
                if at in skipped:
                    row.update(verdict="skipped", reason=skipped[at])
                try:
                    journal.append(row)
                except (BadRecord, Busy):
                    pass
            try:
                journal.finished(key, unit, "precedent", "done", session_id=session, **cost)
                ended = True
            except (BadRecord, Busy):
                pass
            return {
                "unit": unit, "outcome": "done", "detail": None,
                "written": [{"artifact": w["artifact"], "n": w["question"]} for w in done["written"]],
                "needs_person": [{"artifact": v["artifact"], "n": v["n"]} for v in found_v["verdicts"]
                                 if v["verdict"] == precedent_mod.PERSON],
                "skipped": [{"artifact": a, "n": n, "reason": r} for (a, n), r in skipped.items()],
                "ignored": found_v["ignored"], "cost_usd": cost.get("cost_usd"),
            }
        finally:
            if started and not ended:
                try:
                    journal.finished(key, unit, "precedent", "cancelled", detail="Jera ended before its reply was read")
                except (BadRecord, Busy):
                    pass
            self._release(key, unit, mark)
            self._running.pop(rid, None)
            self.updater.job_ended()

    async def start_branch(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0014` R4. Cut this unit's branch in the workspace and switch to it.

        The name is not chosen here and is not the caller's: `cos.mjs unit-branch` reads
        the `Type:` the intent declared and prints `<type>/<slug>`. `coscc/gitops.py`
        carries the list of what the app may do with it, which is this and nothing else.

        Since `0001_product-describes-a-state-it-is-not-in` it is cut from the trunk **as the
        remote has it**, not from whatever the local `main` last saw: fetch, read the SHA
        that fetch brought, cut from that SHA (R1). If the fetch fails nothing is cut and
        the refusal says so (R2) — cutting from a stale `main` with a warning would still
        open the pull request on the wrong base. The result names the ref and the commit
        (R3). The remote and the trunk are constants here, never taken from a request.
        """
        self._workspace_or_refuse(cwd)
        try:
            name = units.branch_name(cwd, unit, self.config.data_dir)
        except (CannotCreate, BadUnit) as e:
            raise Invalid(str(e)) from e
        # `0017`. Cut in the unit's own worktree, never in the workspace: cutting there is
        # what took one unit's branch away from another. The workspace stays on `main`.
        try:
            tree = await worktrees.ensure(cwd, unit, None, self.config.data_dir)
        except (GitError, BadUnit) as e:
            raise Invalid(f"Could not open {unit}'s worktree, so no branch was cut. {e}") from e
        repo = Path(tree["path"])
        # `0048`: through the coordinator, so a step starting beside this does not race it
        # for `refs/remotes/origin/main` — and a fetch under 30s old is reused here too
        # (`spec.md ## Answers, câu 2`).
        try:
            await fetches.fetch(repo, BRANCH_REMOTE, BRANCH_TRUNK)
        except GitError as e:
            raise Invalid(
                f"Could not update {BRANCH_TRUNK} from {BRANCH_REMOTE}, so no branch was cut. "
                f"Nothing in the repository changed. git said: {e}"
            ) from e
        try:
            sha = await gitops.rev_parse(repo, f"refs/remotes/{BRANCH_REMOTE}/{BRANCH_TRUNK}")
            output = await gitops.create_branch(repo, name, sha)
        except GitError as e:
            raise Invalid(str(e)) from e
        # Prepared here rather than when the tree was made (`0017` plan): the lockfiles an
        # `impl` works with are the ones at the commit just cut from, not the local `main`.
        # A failure is returned, not raised — the branch is cut either way — and `run_step`
        # refuses `impl` until preparing succeeds (R6).
        prepared = await worktrees.prepare(repo, cwd, data_dir=self.config.data_dir)
        return {
            "cwd": cwd,
            "unit": unit,
            "branch": name,
            "base": f"{BRANCH_REMOTE}/{BRANCH_TRUNK}",
            "sha": sha[:7],
            "output": output,
            "worktree": str(repo),
            "switched": bool(tree.get("switched")),
            "prepare": prepared,
        }

    async def branch_here(self, cwd: str) -> dict[str, Any]:
        """Which branch the workspace is on. A read, so the page can show it."""
        self._workspace_or_refuse(cwd)
        try:
            return {
                "cwd": cwd,
                "branch": await gitops.current_branch(Path(cwd).expanduser().resolve()),
            }
        except GitError as e:
            raise Invalid(str(e)) from e

    def timeline(self, cwd: str, unit: str) -> dict[str, Any]:
        """What has happened to one unit, oldest first (`spec.md` R15)."""
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            return {"cwd": cwd, "unit": unit, "runs": [], "cost": {}}
        key = self._journal_key(cwd)
        try:
            runs = journal.timeline(key, unit)
        except Busy as e:
            raise Invalid(str(e)) from e
        return {"cwd": cwd, "unit": unit, "runs": runs, "cost": totals_of(runs)}

    def _history(self) -> History | None:
        """The transition log, or `None` when there is no working folder to keep it in.

        Same shape and same reasoning as `_journal`: with nothing set, the app behaves as
        it did before, and the safe direction to fail in is read-only.
        """
        return (
            History(self.config.working_dir, self.config.data_dir)
            if self.config.working_dir
            else None
        )

    def unit_history(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0013` R8. Everything the log knows about one unit.

        **Beside the board, not instead of it.** `board()` still asks `cos.mjs` and still
        reads state out of the `Status:` line on disk (`intent.md` constraint 3); this
        answers from the transition log. Two sources during a transition is deliberate and
        has a cost, and `spec.md` C2 and C5 are where that cost is written down.

        `state` here is a projection over `transitions` and is computed, never stored —
        R1. It is returned alongside the transitions rather than instead of them precisely
        so a caller can check one against the other.

        `settled_edits` is carried because it is the unit of measure `intent.md` named:
        the number of times an artifact was rewritten after it had been settled, which
        was 0 before this and 43 in this repository on 2026-09-22.
        """
        self._workspace_or_refuse(cwd)
        history = self._history()
        key = self._journal_key(cwd)
        if history is None:
            return {
                "cwd": cwd,
                "unit": unit,
                "recording": False,
                "machine": "",
                "written_under": [],
                "mixed_state_sets": None,
                "transitions": [],
                "state": {},
                "settled_edits": 0,
                "sessions": [],
                "unknown_transitions": 0,
                "outputs": [],
                "output_counts": {},
            }
        try:
            rows = history.transitions(key, unit)
            state = history.state(key, unit)
            sessions = history.sessions_of(key, unit)
            outputs = history.outputs(key, unit)
            counts = history.output_counts(key, unit)
            written_under = history.machines_in(key, unit)
        except Busy as e:
            raise Invalid(str(e)) from e
        # `spec.md` C5. Rows written under one state set and read under another compare
        # words that never meant the same thing, and nothing about that failure looks like
        # a failure -- every query still returns rows. Said out loud in the payload rather
        # than refused, because refusing a *read* would hide the only evidence there is.
        # A caller that goes on to compare these against another source must stop here.
        foreign = [name for name in written_under if name != history.machine.name]
        return {
            "cwd": cwd,
            "unit": unit,
            "recording": True,
            "machine": history.machine.name,
            "written_under": written_under,
            "mixed_state_sets": (
                None if not foreign
                else f"this unit holds transitions written under {', '.join(foreign)}, "
                     f"but is being read under {history.machine.name} — the states in "
                     "those rows do not mean what they appear to mean here"
            ),
            "transitions": rows,
            "state": state,
            "settled_edits": len(settled_edits(rows, history.machine)),
            "sessions": sessions["sessions"],
            "unknown_transitions": sessions["unknown_transitions"],
            "outputs": outputs,
            "output_counts": counts,
        }

    def units_with_history(self, cwd: str) -> dict[str, Any]:
        """Every unit the log has a transition for, in the order they first appear.

        Not the same list as `board()`'s, and the difference is the point: a unit retired
        from the working tree still has a history, and this is the only place it can be
        seen. `.cos/` in this repository lost five units that way (`f506aae`).
        """
        self._workspace_or_refuse(cwd)
        history = self._history()
        if history is None:
            return {"cwd": cwd, "recording": False, "units": []}
        try:
            return {
                "cwd": cwd,
                "recording": True,
                "units": history.units(self._journal_key(cwd)),
            }
        except Busy as e:
            raise Invalid(str(e)) from e

    # -- sessions -----------------------------------------------------------

    def _is_member(self, cwd: str) -> bool:
        """The single membership question: env list, or a store entry under the root."""
        if self.config.is_workspace(cwd):
            return True
        return self.store is not None and self.store.resolves_to_entry(cwd)

    def _workspace_or_refuse(self, cwd: str) -> str:
        """The single gate. Every capability below goes through it.

        `spec.md` R21 wants this asked on every read rather than cached, because after
        the workspace list is no longer fixed for the life of the process.
        """
        # Recomputed from the working folder every time, so editing the store by hand
        # cannot widen what this accepts — the entry has to name a segment, and the
        # segment has to resolve back under the root.
        if self._is_member(cwd):
            return cwd
        raise Invalid(f"not a configured workspace: {cwd}")

    def sessions_for(self, cwd: str, limit: int | None = None) -> dict[str, Any]:
        self._workspace_or_refuse(cwd)
        rows = reader.list_for_directory(cwd, limit=limit)
        for row in rows:
            # Terminal sessions show up here too — the read layer sees them. This flag is
            # what tells a caller which of them it may write to (`spec.md` C1).
            row["resumable"] = self.config.may_resume(
                self.sessions.created_here(row["session_id"])
            )
        return {"cwd": cwd, "sessions": rows}

    def history(self, cwd: str, session_id: str) -> dict[str, Any]:
        self._workspace_or_refuse(cwd)
        if not session_id:
            raise Invalid("session_id is required")
        return {
            "session_id": session_id,
            "messages": reader.history(session_id, cwd),
        }

    def check_send(self, cwd: str, text: str) -> None:
        """Everything a caller can reject with a status code, decided before any output.

        Split out from `stream` on purpose. The design draws a hard line between two kinds of
        failure: an invalid request is a status code, while a refusal that surfaces once
        the reply is already streaming has to arrive as data, because the status line is
        long gone (`web.py` docstring on `post_send`). Validating inside an async
        generator would collapse that distinction, since the first item is only pulled
        after a caller has committed to streaming. A test in `web_test.py` holds the line.
        """
        self._workspace_or_refuse(cwd)
        self._refuse_while_updating()
        if not text.strip():
            raise Invalid("text is required")

    async def stream(
        self, cwd: str, text: str, session_id: str | None = None
    ) -> AsyncIterator[tuple[str, Any]]:
        """Yield `(kind, payload)` exactly as the session layer does.

        Re-runs `check_send` so the generator is safe on its own; the checks are pure, so
        doing them twice costs nothing and leaves no caller able to skip them.
        """
        self.check_send(cwd, text)
        # `0004_no-setting-says-which-model-runs-a-stage`. Chat is a row of the same table
        # as the stages.
        model, model_source = self._model_for(models.CHAT)
        async for item in self.sessions.stream(
            cwd, text, session_id, **({"model": model} if model is not None else {})
        ):
            if item[0] == "session":
                # `0019` plan step 2, risk 2. `api.py` treats every kind but `chunk` as
                # the terminal `done` row; forwarding this to chat would turn it into a
                # spurious one, mid-reply.
                continue
            if item[0] == "done":
                # Chat wrote nothing to the run log before this. Now one record per turn
                # says which model it asked for — the model a *new* client is created with.
                # A client already live keeps the model it was made with (plan Risk 7).
                journal = self._journal()
                if journal is not None:
                    try:
                        journal.append({
                            "kind": "chat",
                            "workspace": self._journal_key(cwd),
                            "unit": "",
                            "stage": "",
                            "model": model,
                            "model_source": model_source,
                            "session_id": (item[1] or {}).get("session_id", ""),
                        })
                    except (BadRecord, Busy):
                        pass  # a busy log must not cost the reply that was already paid for
            yield item

    # -- which model each stage runs on --------------------------------------
    #
    # `0004_no-setting-says-which-model-runs-a-stage`. The resolving is `coscc/models.py`;
    # this is where its three inputs are gathered: the stage list from `cos.mjs`, the
    # overrides from `prefs`, `COS_MODEL` from `Config`.

    def _model_overrides(self) -> tuple[dict[str, str], list[str]]:
        return models.overrides_from(Data(self.config.data_dir).pref_rows(models.PREFIX))

    def _effort_overrides(self) -> tuple[dict[str, str], list[str]]:
        return models.overrides_from(
            Data(self.config.data_dir).pref_rows(models.EFFORT_PREFIX), models.EFFORT_PREFIX
        )

    def _model_for(self, name: str) -> tuple[str | None, str]:
        """`(model, source)` for chat, and for Gebo on `impl`'s base row. Never raises on bad data.

        Takes no stage list: the caller has already checked `name` against `cos.mjs`
        (`run_step` found the row), and resolving one row does not need the others.
        A board step goes through `_stage_config` instead, which also reads the label.
        """
        overrides, _ = self._model_overrides()
        defaults, _ = models.load_defaults()
        return models.resolve(name, None, overrides, {}, defaults, self.config.model)[:2]

    def _stage_config(
        self, stage: str, stages: list[str], directory: Path, journal: Journal, key: str, unit: str
    ) -> dict[str, Any]:
        """`0033`. The label a step runs under, the model and effort it resolves to, and
        for `impl` which run of the unit's this is. Called after the gate, before any money
        is spent. The label chooses a configuration and nothing else (spec R11).

        `Busy` from the run log is left to the caller, as `failed_attempts` is.
        """
        try:
            plan_text: str | None = (Path(directory) / "plan.md").read_text(encoding="utf-8", errors="replace")
        except OSError:
            plan_text = None
        history = [r for r in journal.records(key, unit) if r.get("stage") == "impl"]
        label_declared, label, label_source = labels.label_for(stage, stages, plan_text, history)
        model, model_source, effort, effort_source = models.resolve(
            stage, label, self._model_overrides()[0], self._effort_overrides()[0],
            models.load_defaults()[0], self.config.model,
        )
        return {
            "model": model, "model_source": model_source,
            "effort": effort, "effort_source": effort_source,
            "label_declared": label_declared, "label": label, "label_source": label_source,
            # R10: every `start` of `impl` counts, the review-driven fixes included; the
            # reading "before the first `pr`" is done from the log (spec Answers, câu 2).
            "impl_run": (
                sum(1 for r in history if r.get("kind") == "start") + 1 if stage == "impl" else None
            ),
        }

    async def _findings_added(self, cwd: str, unit: str, before: set[Any]) -> dict[str, Any]:
        """`0033` R10. The findings in the rounds a `review` step added, off the board —
        `parseReview`'s count, read the way `_post_new_rounds` reads it. `0093` R9: and
        those rounds' verdicts, each a string, for *Changes-requested rounds*."""
        data = await board_reader.read(self._units_root(cwd))
        found = next((u for u in data["units"] if u["name"] == unit), None) or {}
        added = [r for r in found.get("rounds") or [] if r.get("n") not in before]
        return {
            "findings": sum(int(r.get("findings") or 0) for r in added),
            "findings_open": sum(int(r.get("findings_open") or 0) for r in added),
            "verdicts": [str(r.get("verdict") or "") for r in added],
        }

    async def stage_models(self) -> dict[str, Any]:
        """Every row Settings shows: stage, agents, model, effort, where each came from.

        When `node` cannot run there is no stage list, and inventing one here would be the
        second copy of the loop. So the table is empty and `problems` says why.
        """
        try:
            stages = await board_reader.stages()
        except Unavailable as e:
            return {"rows": [], "problems": [str(e)], "cos_model": self.config.model}
        overrides, bad_rows = self._model_overrides()
        efforts, bad_efforts = self._effort_overrides()
        defaults, bad_defaults = models.load_defaults()
        table = models.table(stages, overrides, efforts, defaults, self.config.model, SESSIONS_PER_STEP)
        for r in table["rows"]:
            r["overridden"] = r["name"] in overrides
            r["effort_overridden"] = r["name"] in efforts
        table["problems"] = bad_defaults + bad_rows + bad_efforts + table["problems"]
        table["cos_model"] = self.config.model
        return table

    async def _setting_row(self, name: Any, allow_chat: bool) -> str:
        """Check a Settings row name against `cos.mjs`: a stage, `<stage>:novel` for a
        stage after `plan`, or `chat` when the setting has one."""
        if not isinstance(name, str) or not name:
            raise Invalid("name is required")
        try:
            stages = await board_reader.stages()
        except Unavailable as e:
            raise Invalid(str(e)) from e
        allowed = [r for r in models.rows_for(stages) if allow_chat or r != models.CHAT]
        if name not in allowed:
            raise Invalid(f"no such stage: {name} (use one of {', '.join(allowed)})")
        return name

    def _log_setting(self, key: str, old: Any, new: Any) -> None:
        journal = self._journal()
        if journal is not None:
            try:
                journal.append({
                    "kind": "setting", "workspace": "", "unit": "", "stage": "",
                    "name": key, "old": old, "new": new,
                })
            except (BadRecord, Busy) as e:
                raise Invalid(f"the setting was saved but not logged: {e}") from e

    async def set_stage_model(self, name: Any, model: Any = None) -> dict[str, Any]:
        """Set one row's model, or remove the override when `model` is None.

        **Behind the password like every route here** (`0070`): whoever holds it or a live
        session can move `review` to a weaker model, or every stage to a dearer one. The one trace is the `setting`
        record appended below, with the old and new value. It chooses a model and nothing
        else: no gate reads it, and no stage starts because of it.

        The model name is not checked against the API — an unknown one fails at the next
        step of that stage, with the CLI's own error (spec Out of scope).
        """
        name = await self._setting_row(name, allow_chat=True)
        if model is not None:
            if not isinstance(model, str) or not model.strip():
                raise Invalid("model is required")
            model = model.strip()

        data = Data(self.config.data_dir)
        key = models.PREFIX + name
        old = self._model_overrides()[0].get(name)
        if model is None:
            data.delete_pref(key)
        else:
            data.set_pref(key, model)
        self._log_setting(key, old, model)
        return await self.stage_models()

    async def set_stage_effort(self, name: Any, effort: Any = None) -> dict[str, Any]:
        """`0033` R9. Set one row's effort, or remove the override when `effort` is None.

        The same exposure as `set_stage_model`, and the `setting` record is the
        trace. `max` is accepted here and only here — `models.json` may not ship it, so
        every `max` run traces back to one of these records (spec R7, C8). `chat` has no
        effort (spec Out of scope).
        """
        name = await self._setting_row(name, allow_chat=False)
        if effort is not None and effort not in models.EFFORTS:
            raise Invalid(f"effort must be one of {', '.join(models.EFFORTS)}")

        data = Data(self.config.data_dir)
        key = models.EFFORT_PREFIX + name
        old = self._effort_overrides()[0].get(name)
        if effort is None:
            data.delete_pref(key)
        else:
            data.set_pref(key, effort)
        self._log_setting(key, old, effort)
        return await self.stage_models()

    # -- the autopilot's settings (`0043` R1, R2) ----------------------------
    #
    # In the data root's `prefs`, not the workspace's repository. Three per workspace, keyed
    # by the journal key; the cap is one for the whole app, since the quota is the machine's
    # account (`intent.md ## Answers`, câu 9). Not in `PREFERENCES`: those are the page's.

    AUTOPILOT_SETTINGS = ("autopilot", "autopilot_may_ship", "max_parallel", "daily_cap_usd")
    CAP_PREF = "autopilot_daily_cap_usd"

    def _autopilot_pref(self, name: str, key: str) -> str:
        return self.CAP_PREF if name == "daily_cap_usd" else f"{name}:{key}"

    def _autopilot_values(self, key: str) -> dict[str, Any]:
        """The four values in effect. A hand-edited value of the wrong type reads as its
        default, and the default of both switches is off."""
        data = Data(self.config.data_dir)

        def read(name: str, ok: Any, default: Any) -> Any:
            value = data.pref(self._autopilot_pref(name, key), default)
            return value if ok(value) else default

        return {
            "autopilot": read("autopilot", lambda v: v is True or v is False, False),
            "autopilot_may_ship": read("autopilot_may_ship", lambda v: v is True or v is False, False),
            "max_parallel": read("max_parallel", _whole_at_least_one, autopilot.DEFAULT_MAX_PARALLEL),
            "daily_cap_usd": float(read("daily_cap_usd", _positive_number, autopilot.DEFAULT_DAILY_CAP_USD)),
        }

    def _off_loopback(self) -> str:
        """Why the autopilot may not run on this bind, or `""` (`spec.md ## Answers`, câu 3)."""
        if self.config.host in LOOPBACK:
            return ""
        # S3: no variable name here, the page shows it verbatim; `coscc-settings.md` names it.
        return f"The app listens on {self.config.host}, beyond this machine; restart it on 127.0.0.1 to use the autopilot."

    def autopilot_settings(self, cwd: str) -> dict[str, Any]:
        """The four settings of one workspace, and whether the bind lets the autopilot run."""
        self._workspace_or_refuse(cwd)
        return {
            "cwd": cwd,
            **self._autopilot_values(self._journal_key(cwd)),
            "refused_because": self._off_loopback(),
        }

    def set_autopilot(self, cwd: str, name: Any, value: Any) -> dict[str, Any]:
        """Set one of the four. A wrong value is refused and nothing is written (R2).

        Behind the password like every route: whoever holds it or a live session can turn
        the autopilot on, raise the cap, or let it ship. The trace is the `setting` record.
        Turning it on is refused while the app listens beyond loopback.
        """
        self._workspace_or_refuse(cwd)
        if name not in self.AUTOPILOT_SETTINGS:
            raise Invalid(f"no such setting: {name} (use one of {', '.join(self.AUTOPILOT_SETTINGS)})")
        if name in ("autopilot", "autopilot_may_ship"):
            if value is not True and value is not False:
                raise Invalid(f"{name} must be true or false")
        elif name == "max_parallel":
            if not _whole_at_least_one(value):
                raise Invalid("max_parallel must be a whole number, 1 or more")
        elif not _positive_number(value):
            raise Invalid("daily_cap_usd must be a number above 0")
        if name == "autopilot" and value and self._off_loopback():
            raise Invalid(f"the autopilot was not turned on: {self._off_loopback()}")
        key = self._journal_key(cwd)
        old = self._autopilot_values(key)[name]
        stored = float(value) if name == "daily_cap_usd" else value
        Data(self.config.data_dir).set_pref(self._autopilot_pref(name, key), stored)
        self._log_setting(self._autopilot_pref(name, key), old, stored)
        if name == "autopilot":
            if value:
                self.autopilot_start(cwd)
            else:
                self.autopilot_stop(key)
        return self.autopilot_settings(cwd)

    # -- the autopilot (`0043` R4–R10) ----------------------------------------
    #
    # It holds no rule of the loop. Each pass reads the board, the run log, the settings and
    # the workspace's last shortlist, and `next` for every unit on it and no other (`0104`);
    # with no shortlist it asks nothing. `coscc/autopilot.py` decides where to stop and what
    # to start; what it starts goes through `run_step` and `integrate`, which ask the gate
    # themselves. A refusal from them is a stop line, never a second way past the gate.

    def autopilot_start(self, cwd: str) -> None:
        """Run a pass now and every `POLL_SECONDS` after, for as long as the switch is on
        (R5 c, d). A second call for a workspace already running does nothing."""
        key = self._journal_key(cwd)
        self._autopilot_cwd[key] = cwd
        task = self._autopilot_tasks.get(key)
        if task is not None and not task.done():
            return
        self._autopilot_tasks[key] = asyncio.get_running_loop().create_task(self._autopilot_loop(key))

    def autopilot_stop(self, key: str) -> None:
        """Turned off: no more passes and no more `gh` calls for it (R1). A step it already
        started runs on to its end, as a person's would."""
        task = self._autopilot_tasks.pop(key, None)
        if task is not None:
            task.cancel()
        self._autopilot_stops.pop(key, None)

    def autopilot_resume(self) -> list[str]:
        """R5 c: at start-up, every workspace whose switch is on starts again. Returns them."""
        started = []
        for row in self.workspaces()["workspaces"]:
            if row["missing"]:
                continue
            if self._autopilot_values(self._journal_key(row["path"]))["autopilot"]:
                self.autopilot_start(row["path"])
                started.append(row["path"])
        return started

    def _autopilot_on(self, key: str) -> bool:
        task = self._autopilot_tasks.get(key)
        return task is not None and not task.done()

    def _autopilot_nudge(self, key: str) -> None:
        """R5 a, b: a step or an integration ended, or an answer was written. One pass is
        scheduled and not waited for; nothing happens when the switch is off."""
        if not self._autopilot_on(key):
            return
        task = asyncio.get_running_loop().create_task(self._autopilot_guarded(key))
        self._autopilot_pending.add(task)
        task.add_done_callback(self._autopilot_pending.discard)

    async def _autopilot_loop(self, key: str) -> None:
        while True:
            await self._autopilot_guarded(key)
            await asyncio.sleep(autopilot.POLL_SECONDS)

    async def _autopilot_guarded(self, key: str) -> None:
        """A pass that raises leaves a stop line saying so, not a dead loop."""
        try:
            await self._autopilot_pass(key)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — shown on the board, never swallowed
            self._autopilot_set_stops(key, {"": {"unit": "", "kind": "f", "reason": f"the autopilot's pass failed: {e}"}})

    def _autopilot_running(self, key: str) -> list[dict[str, Any]]:
        """What runs in this workspace now, by unit, a person's steps included (R8 a)."""
        out: dict[str, dict[str, Any]] = {}
        for (k, unit), mark in self._active.items():
            if k == key:
                out[unit] = {"unit": unit, "stage": "integrate" if mark.kind == "integrate" else mark.stage}
        for unit, (stage, task) in (self._autopilot_runs.get(key) or {}).items():
            if not task.done() and unit not in out:
                out[unit] = {"unit": unit, "stage": stage}
        return list(out.values())

    def _autopilot_files(self, cwd: str, unit: str) -> set[str] | None:
        try:
            return autopilot.files_of((self._unit_dir(cwd, unit) / "plan.md").read_text(encoding="utf-8"))
        except (Invalid, OSError):
            return None

    def _autopilot_cap(self, records: list[dict[str, Any]], limit: float) -> dict[str, Any]:
        """R7's figures, for a pass and for the board: every workspace, every starter."""
        now = datetime.now().astimezone()
        spent = autopilot.spent_today(records, now)
        active = {
            (k, unit): "integrate" if mark.kind == "integrate" else mark.stage
            for (k, unit), mark in self._active.items()
        }
        # A launch holds no mark until `run_step` or `integrate` takes one — `integrate` only
        # after its fetch and `gh` reads — and is counted from the moment it was chosen.
        for k, runs in self._autopilot_runs.items():
            for unit, (stage, task) in runs.items():
                if not task.done():
                    active.setdefault((k, unit), stage)
        running = autopilot.reserved(records, now, [(k, unit, stage) for (k, unit), stage in active.items()])
        return {
            "limit": limit, "spent": round(spent["known"] + spent["estimated"], 2),
            "known": round(spent["known"], 2), "estimated": round(spent["estimated"], 2),
            "estimated_count": spent["estimated_count"], "running": round(running, 2),
            "day": autopilot.today(now),
        }

    def _autopilot_set_stops(
        self, key: str, found: dict[str, dict[str, str]], asked: set[str] | None = None,
    ) -> None:
        """Keep the stops a pass found, and log each unit's that changed (R9, R11).

        `asked`: the units this pass looked at, when it did not look at all of them; the
        others keep what they had. The log is an `autopilot-stop` record per change, `stop`
        empty once it cleared — what `verify_0043` reads to tell a person's press at a stop
        from one outside them.
        """
        before = self._autopilot_stops.get(key, {})
        if asked is None:
            after = dict(found)
        else:
            after = {**{u: s for u, s in before.items() if u and u not in asked}, **found}
        self._autopilot_stops[key] = after
        journal = self._journal()
        if journal is None:
            return
        for unit in sorted(set(before) | set(after)):
            if not unit:
                continue
            old, new = before.get(unit), after.get(unit)
            if (old or {}).get("kind") == (new or {}).get("kind"):
                continue
            try:
                journal.append({
                    "kind": "autopilot-stop", "workspace": key, "unit": unit, "stage": "",
                    "stop": (new or {}).get("kind", ""), "reason": (new or {}).get("reason", ""),
                })
            except (BadRecord, Busy):
                pass

    async def _autopilot_pass(self, key: str) -> None:
        """One look at a workspace: follow its shortlist (`0104`), find each listed unit's stop
        or why it waits, then start what may start, highest first, each after its record."""
        cwd = self._autopilot_cwd.get(key)
        if cwd is None or not self._autopilot_on(key):
            return
        lock = self._autopilot_locks.setdefault(key, asyncio.Lock())
        async with lock:
            settings = self._autopilot_values(key)
            if not settings["autopilot"]:
                return
            refused = self._off_loopback()
            if refused:
                # `COS_HOST` can change after the switch was turned on.
                self._autopilot_set_stops(key, {"": {"unit": "", "kind": "f", "reason": refused}})
                return
            journal = self._journal()
            if journal is None:
                return
            data = await self.board(cwd)
            try:
                records = journal.records(kinds=("start", "end", "integration", "shortlist", "answer", "screens"))
            except Busy as e:
                self._autopilot_set_stops(key, {"": {"unit": "", "kind": "f", "reason": str(e)}})
                return
            # `0104` R1: the last well-formed shortlist, read again on every pass.
            listed, n = backlog.shortlist_of(r for r in records if r.get("workspace") == key)
            if listed is None or not listed["units"]:
                # R3: nothing is asked and nothing starts. R1 of `0043` as below.
                if not self._autopilot_on(key) or not self._autopilot_values(key)["autopilot"]:
                    return
                self._autopilot_set_stops(key, {"": {"unit": "", "kind": "shortlist", "reason": autopilot.NO_SHORTLIST}})
                return
            names = list(listed["units"])
            # `0112` R4: a listed unit at `ship` is decided on the `origin/main` the remote has
            # now — `behind` below and the `ship` gate `next` asks both read that ref, and
            # neither fetches. Through the coordinator, which reuses a fetch under
            # `REUSE_SECONDS`. A fetch that fails leaves the ref as it was, and each such
            # unit's stop says so.
            at_ship = {
                u["name"] for u in data["units"]
                if u["name"] in names and u.get("between_pr_and_ship") and u.get("at") == "ship"
            }
            unfetched: dict[str, Any] | None = None
            if at_ship:
                try:
                    await fetches.fetch(Path(cwd).expanduser().resolve(), BRANCH_REMOTE, BRANCH_TRUNK)
                except GitError as e:
                    unfetched = {"outcome": "failed", "detail": str(e)}
                else:
                    data = await self.board(cwd)
            last: dict[str, dict[str, Any]] = {}
            integrations: dict[str, dict[str, Any]] = {}
            for r in records:
                # `0111`: a retake of the screenshots that failed is the unit's last word too.
                if r.get("workspace") == key and r.get("kind") in ("end", "integration", "screens") and autopilot.is_step(r):
                    last[str(r.get("unit") or "")] = r
                    if r.get("kind") == "integration":
                        integrations[str(r.get("unit") or "")] = r

            running = self._autopilot_running(key)
            here = {r["unit"]: r["stage"] for r in running}
            board = {u["name"]: u for u in data["units"]}
            found: dict[str, dict[str, str]] = {}
            candidates: list[dict[str, Any]] = []
            # R6: why each unit that is no candidate waits, for the units ranked below it.
            reasons: dict[str, tuple[str, str]] = {}
            # R2, R5: every unit on the shortlist is asked, and no other.
            for rank, name in enumerate(names, 1):
                u = board.get(name)
                if u is None:
                    reasons[name] = ("missing", "")
                    continue
                try:
                    nxt = await self.next_step(cwd, name)
                except Invalid as e:
                    found[name] = {"unit": name, "kind": "f", "reason": str(e)}
                    reasons[name] = ("running", here[name]) if name in here else autopilot.reason_for({}, "", found[name])
                    continue
                stop = autopilot.stop_for(u, nxt, last.get(name), settings["autopilot_may_ship"])
                stage = nxt.get("stage") or ""
                # R10: a unit behind `main`, conflicting or red after integration is integrated
                # first, and not again when CI is red on what the autopilot's own integration
                # pushed. Since `0112` R1 also after a `pass`: GitHub would refuse the merge,
                # the rebase closes `ship`, and a new round opens it again — but only where the
                # autopilot may ship, since otherwise a person merges and the round is theirs.
                info = u.get("integration") or {}
                rounds = u.get("rounds") or []
                passed = bool(rounds) and rounds[-1].get("verdict") == "pass"
                if (
                    info.get("state") in integrate.BUTTON_STATES
                    and (not passed or settings["autopilot_may_ship"])
                    and (stop is None or stop["kind"] == "f")
                ):
                    stop, stage = autopilot.red_again(info, integrations.get(name)), "integrate"
                # `0106` R2, R3: a draft whose questions are all answered runs again, at most
                # `MAX_RERUNS` times, and only on an answer given since its last run; with none,
                # it is the stop `f` it was before `0106`. Before `reason`, which raises on no
                # stage and no stop.
                rerun = False
                if stop is None and not stage and nxt.get("rerun"):
                    if autopilot.reruns_of(records, key, name, nxt["rerun"]) >= autopilot.MAX_RERUNS:
                        artifact = next((s["file"] for s in u.get("stages") or [] if s["stage"] == nxt["rerun"]), nxt["rerun"])
                        stop = autopilot.rerun_stop(artifact)
                    elif not autopilot.answered_since_start(records, key, name, nxt["rerun"]):
                        stop = autopilot.stop_for(u, {**nxt, "rerun": ""}, last.get(name), settings["autopilot_may_ship"])
                    else:
                        stage, rerun = nxt["rerun"], True
                if stop is not None and unfetched is not None and name in at_ship:
                    note = integrate.origin_note(str(info.get("origin_sha") or ""), unfetched)
                    stop = {**stop, "reason": f"{stop['reason']}; {note}"}
                reason = ("running", here[name]) if name in here else autopilot.reason_for(nxt, stage, stop)
                if reason is not None:
                    reasons[name] = reason
                if stop is not None:
                    found[name] = {"unit": name, **stop}
                    continue
                if not stage:
                    continue
                files = self._autopilot_files(cwd, name) if stage in autopilot.CODE_STAGES else None
                candidates.append({
                    "unit": name, "stage": stage, "files": files, "need": autopilot.reservation(stage), "rank": rank,
                    "rerun": rerun,
                })

            for r in running:
                if r["stage"] in autopilot.CODE_STAGES:
                    r["files"] = self._autopilot_files(cwd, r["unit"])
            now = datetime.now().astimezone()
            # The spec's `## Design`: a `start` with no `end`, from a process before this one,
            # counts against N for 24 hours.
            elsewhere = sum(1 for (k, unit) in autopilot.open_starts(records, now) if k == key and unit not in here)
            cap = self._autopilot_cap(records, settings["daily_cap_usd"])
            room = cap["limit"] - cap["spent"] - cap["running"]
            picked = autopilot.pick(candidates, running, settings["max_parallel"] - elsewhere, room)
            reasons.update(picked["held"])
            est = f" ({cap['estimated']:.2f} estimated)" if cap["estimated_count"] else ""
            for c in picked["capped"]:
                found[c["unit"]] = {"unit": c["unit"], "kind": "cap", "reason": (
                    f"spent {cap['spent']:.2f}{est} + running {cap['running']:.2f} + {c['stage']} "
                    f"{c['need']:.2f} is over the cap of {cap['limit']:.2f} USD ({cap['day']})"
                )}
                reasons[c["unit"]] = autopilot.reason_for({}, c["stage"], found[c["unit"]])
            # `0106` R5: a run again that `max_parallel` alone held back says so. Any other
            # candidate held back that way still says nothing, as before.
            left = {c["unit"] for c in picked["chosen"] + picked["capped"]} | set(picked["held"])
            for c in candidates:
                if c["rerun"] and c["unit"] not in left:
                    found[c["unit"]] = {"unit": c["unit"], **autopilot.full_stop(c["stage"], settings["max_parallel"])}
                    reasons[c["unit"]] = autopilot.reason_for({}, c["stage"], found[c["unit"]])
            # Raises before anything is recorded or started when a unit above one chosen has no
            # reason; `_autopilot_guarded` shows it as a stop line.
            passed = autopilot.passed_for(names, [c["unit"] for c in picked["chosen"]], reasons)
            # R1: the switch may have been turned off while this pass read the board and
            # `next`. Nothing from here on awaits, so nothing starts once it is off.
            if not self._autopilot_on(key) or not self._autopilot_values(key)["autopilot"]:
                return
            self._autopilot_set_stops(key, found)
            run_id = uuid.uuid4().hex
            shortlist = {"n": n, "at": listed.get("at"), "units": names}
            for c, over in zip(picked["chosen"], passed):
                # R6: no record, no start — and nothing ranked below it either, since starting
                # one would pass over a unit chosen with no record of it.
                try:
                    journal.append({
                        "kind": "autopilot-pick", "workspace": key, "unit": c["unit"], "stage": c["stage"],
                        "pass": run_id, "rank": c["rank"], "shortlist": shortlist, "passed": over,
                    })
                except (BadRecord, Busy) as e:
                    self._autopilot_set_stops(key, {**found, "": {
                        "unit": "", "kind": "f",
                        "reason": f"could not record the autopilot's choice, so nothing more was started: {e}",
                    }})
                    return
                task = asyncio.get_running_loop().create_task(self._autopilot_launch(key, cwd, c["unit"], c["stage"]))
                self._autopilot_runs.setdefault(key, {})[c["unit"]] = (c["stage"], task)

    async def _autopilot_launch(self, key: str, cwd: str, unit: str, stage: str) -> None:
        """Start one step or integration and read it to its end, since no client will.

        A refusal is a stop line with the gate's words (R6 f) — unless it is CI still
        running, or the unit taken by someone else in the meantime, which are not stops.
        """
        stream = (
            self.integrate(cwd, unit, started_by="autopilot") if stage == "integrate"
            else self.run_step(cwd, unit, stage, started_by="autopilot")
        )
        try:
            # R1: turned off between the pass and this task's first turn.
            if not self._autopilot_on(key):
                return
            async for _ in stream:
                pass
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — the reason is shown, not swallowed
            said = str(e)
            if not autopilot.is_ci_pending(said) and not self._busy(key, unit):
                self._autopilot_set_stops(key, {unit: {"unit": unit, "kind": "f", "reason": said}}, {unit})
        finally:
            runs = self._autopilot_runs.get(key) or {}
            if runs.get(unit, ("", None))[1] is asyncio.current_task():
                del runs[unit]

    def _autopilot_block(self, key: str) -> dict[str, Any]:
        """What the board shows of the autopilot (R9). Display only; decides nothing."""
        values = self._autopilot_values(key)
        on = values["autopilot"]
        block: dict[str, Any] = {
            "on": on, "may_ship": values["autopilot_may_ship"], "max_parallel": values["max_parallel"],
            "cap": None, "stops": [], "refused_because": self._off_loopback() if on else "",
        }
        if not on:
            return block
        journal = self._journal()
        if journal is not None:
            try:
                block["cap"] = self._autopilot_cap(journal.records(kinds=("start", "end")), values["daily_cap_usd"])
            except Busy:
                block["cap"] = None
        block["stops"] = sorted(
            (self._autopilot_stops.get(key) or {}).values(),
            key=lambda s: (autopilot.unit_number(s["unit"]), s["unit"]),
        )
        return block

    # -- activity, usage and settings ---------------------------------------
    #
    # Three read-only methods. `spec.md` said `Service` would not change,
    # and this is the one place it does — recorded as a departure in `plan.md`. The
    # alternative was to let the new page read `Journal` and `policy` directly, and that
    # would break the rule this module exists for (see the module docstring), which is a
    # far worse trade than three methods that only read.

    def _records_or_none(self, cwd: str) -> list[dict[str, Any]] | None:
        """Every record for this workspace, or `None` when nothing is being recorded.

        `activity` and `usage` both want the same rows and are always called together by
        the Activity screen. Shared so the scan is written once — see `activity_and_usage`
        for why it is also *read* once.
        """
        self._workspace_or_refuse(cwd)
        journal = self._journal()
        if journal is None:
            return None
        try:
            return journal.records(self._journal_key(cwd))
        except Busy as e:
            raise Invalid(str(e)) from e

    def _events_of(self, cwd: str, rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
        events = [
            {
                "at": r.get("at") or "",
                "kind": r.get("kind") or "",
                "unit": r.get("unit") or "",
                "stage": r.get("stage") or "",
                "mode": r.get("mode") or "",
                "outcome": r.get("outcome") or "",
                "session_id": r.get("session_id") or "",
                "artifact": r.get("artifact") or "",
                "denials": int(r.get("denials") or 0),
                "cost": {f: r.get(f) for f in COST_FIELDS + (COST_USD,) if r.get(f)},
                # `0045` R10. A `hold` row's move, reason, name and side effects; empty on
                # every other kind.
                "from": r.get("from") or "",
                "to": r.get("to") or "",
                "reason": r.get("reason") or "",
                "by": r.get("by") or "",
                "effects": [e for e in r.get("effects") or [] if isinstance(e, dict)],
            }
            for r in rows
            # `0111` R6: a retake of the screenshots is recorded, and shown on no screen.
            if r.get("kind") != "screens"
        ]
        events.reverse()
        return {"cwd": cwd, "events": events[:limit], "recording": True}

    def _usage_of(self, cwd: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        # `0092` R7. Only known costs are added; `unknown` counts the `end` rows that carried
        # no `cost_usd`, so the sum is never shown as the whole of it.
        per_unit: dict[str, dict[str, Any]] = {}
        for record in rows:
            if record.get("kind") != "end":
                continue
            bucket = per_unit.setdefault(str(record.get("unit") or ""), {**zero_cost(), "unknown": 0})
            add_cost(bucket, record)
            bucket["unknown"] += int(COST_USD not in record)
        total = {**zero_cost(), "unknown": 0}
        for bucket in per_unit.values():
            add_cost(total, bucket)
            total["unknown"] += bucket["unknown"]
        return {"cwd": cwd, "total": total, "per_unit": per_unit, "recording": True}

    def activity(self, cwd: str, limit: int = 40) -> dict[str, Any]:
        """What has happened across the whole workspace, newest first.

        `timeline` answers the same question for one unit. This one exists because the
        Activity screen is workspace-wide, and building it by calling `timeline` once per
        unit would spawn one board read per unit to find out what the units are.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "events": [], "recording": False}
        return self._events_of(cwd, rows, limit)

    def usage(self, cwd: str) -> dict[str, Any]:
        """What this workspace has cost, added up from its records.

        Added rather than stored, for the reason `journal.totals` gives: a stored total is
        a second number that can disagree with the first.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "total": {}, "per_unit": {}, "recording": False}
        return self._usage_of(cwd, rows)

    def activity_and_usage(self, cwd: str, limit: int = 40) -> dict[str, Any]:
        """Both of the above, from one read.

        The Activity screen wants both at once. Calling the two public methods meant two
        connections and two full parses of the identical rows; they stay for the JSON API,
        and this is what the page calls.
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {
                "cwd": cwd, "events": [], "total": {}, "per_unit": {}, "recording": False,
            }
        return {**self._events_of(cwd, rows, limit), **self._usage_of(cwd, rows)}

    def cost(self, cwd: str, rounds: dict[str, list[str]] | None = None) -> dict[str, Any]:
        """`0093`. Where this workspace's money went, from one read of its run log.

        `rounds` is each unit's review verdicts, from the board read the page already has
        (R9). Read only; no figure here is read by a gate (R13).
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"cwd": cwd, "recording": False}
        return {**spend.model(rows, rounds), "recording": True}

    def unit_cost(self, cwd: str, unit: str) -> dict[str, Any]:
        """`0093` R11. One unit's cost by stage and its anomalies.

        The whole log is read, not the unit's rows: a token-per-turn median is the
        workspace's (spec `## Design` §1).
        """
        rows = self._records_or_none(cwd)
        if rows is None:
            return {"by_stage": [], "anomalies": [], "recording": False}
        found = spend.model(rows)
        return {
            "by_stage": found["unit_stages"].get(unit, []),
            "anomalies": [a for a in found["anomalies"] if a["unit"] == unit],
            "recording": True,
        }

    def settings(self) -> dict[str, Any]:
        """The safety posture, as something a screen can render. Read only.

        `spec.md` R18: this screen shows the four knobs and the grant table and can
        change neither. There is no setter here for the same reason there is none in
        `config.from_env` — a request that could turn a knob is a request that could turn
        it on.

        **The model per stage is the one thing Settings can change**, and it is not here:
        `stage_models` and `set_stage_model` are. `0004_no-setting-says-which-model-runs-
        a-stage` made it changeable on purpose — a model is a choice of cost, not of
        capability, and the originator asked for it without a release. The price is a
        route that decides what every step spends for whoever holds the password or a live
        session. `cos_model` below is only
        the fallback for a row nothing else answers.
        """
        c = self.config
        return {
            "working_dir": c.working_dir,
            "data_dir": str(Data(c.data_dir).root),
            "host": c.host,
            "port": c.port,
            "cos_model": c.model,
            "knobs": [
                {
                    "name": "tools",
                    "value": ", ".join(c.effective_tools()) or "none",
                    "on": bool(c.effective_tools()),
                    "detail": "The tools a chat session gets; none means chat only.",
                },
                {
                    "name": "allow_write_and_exec",
                    "value": "on" if c.allow_write_and_exec else "off",
                    "on": c.allow_write_and_exec,
                    "detail": "While off, no chat session can write files or run commands.",
                },
                {
                    "name": "bypass_permissions",
                    "value": "on" if c.bypass_permissions else "off",
                    "on": c.bypass_permissions,
                    "detail": "Set only by the environment the app started with.",
                },
                {
                    "name": "resume_foreign_sessions",
                    "value": "on" if c.resume_foreign_sessions else "off",
                    "on": c.resume_foreign_sessions,
                    "detail": "The app resumes only the sessions it created.",
                },
            ],
            # The board's own grants, from `policy.py` rather than from the config. They
            # are separate on purpose, and the screen has to show that they are. `0062`
            # R9: a stage with its own `novel` ceilings shows them as `<stage>:novel`,
            # right after its own row.
            "grants": [
                {
                    "stage": name,
                    "tools": ", ".join(grant.tools) or "none",
                    "commands": ", ".join(grant.commands) or "none",
                    # `0082` F2: the same, one item each, for the page to list (S5).
                    "tool_list": list(grant.tools),
                    "command_list": list(grant.commands),
                    "max_turns": grant.max_turns,
                    "max_budget_usd": grant.max_budget_usd,
                    "app_writes_artifact": grant.app_writes_artifact,
                    "warning": grant.warning,
                    "consequence": consequence(name),
                }
                for stage, own in sorted(GRANTS.items())
                if stage not in TERMINAL_ONLY
                for name, grant in (
                    [(stage, own)]
                    + ([(f"{stage}:{labels.NOVEL}", grant_for_step(stage, labels.NOVEL))]
                       if stage in NOVEL_CEILINGS else [])
                )
            ],
            "prose_stages": list(PROSE_STAGES),
        }

    # -- artifacts and preferences ------------------------------------------

    def artifact(self, cwd: str, unit: str, stage: str) -> dict[str, Any]:
        """The text of one stage's artifact, or why there is none.

        The path is built by `runner.unit_dir`, which validates the unit name against the
        `NNNN_slug` shape. That is the same function the runner uses, so a name this
        refuses is a name no step could run against either — one rule, not two.
        """
        self._workspace_or_refuse(cwd)
        if stage not in STAGE_FILES:
            raise Invalid(f"no such stage: {stage}")
        filename = f"{stage}.md"
        path = self._unit_dir(cwd, unit) / filename
        if not path.is_file():
            return {"unit": unit, "stage": stage, "file": filename, "text": "", "exists": False}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise Invalid(f"could not read {filename}: {e}") from e
        return {"unit": unit, "stage": stage, "file": filename, "text": text, "exists": True}

    # Which preferences the page may keep. An open key/value store reachable from a
    # request is a place to put anything; this is the list of things the Settings screen
    # actually remembers, and nothing else is writable.
    # `0044` R8a: `decision_preferences`, the text Jera reads as precedent, word for word.
    PREFERENCES = {"density": "comfortable", "screen": "overview", "board_view": "Board",
                   "decision_preferences": ""}

    def preferences(self) -> dict[str, Any]:
        data = Data(self.config.data_dir)
        stored = data.prefs()
        return {k: stored.get(k, default) for k, default in self.PREFERENCES.items()}

    def set_preference(self, key: str, value: Any) -> dict[str, Any]:
        if key not in self.PREFERENCES:
            raise Invalid(f"not a stored preference: {key}")
        if not isinstance(value, (str, int, float, bool)):
            raise Invalid("a preference must be a simple value")
        Data(self.config.data_dir).set_pref(key, value)
        return {"key": key, "value": value}
