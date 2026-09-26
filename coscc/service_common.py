"""What more than one part of `Service` uses, and what code outside it imports: the errors a
request is refused with, and the words and states the board shows beside a unit. Split
from `coscc/service.py` (`0095`), which re-exports every name.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from coscc import gitops
from coscc import present


# The eight stage names, in stage order. Taken from the stage list the board reports rather
# than written again here would be better; the board read is async and this method is not,
# so the names are repeated and this comment is the warning.
STAGE_FILES = ("idea", "intent", "spec", "plan", "impl", "pr", "review", "ship")

# Where a unit's branch is cut from: the trunk as this remote has it. Constants, not
# request fields — a caller cannot point the fetch at another remote or another branch.
BRANCH_REMOTE = "origin"
BRANCH_TRUNK = gitops.TRUNK


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


def _younger_than(at: str, oldest: datetime) -> bool:
    """Whether a run-log `at` is after `oldest`. One that will not parse is not shown."""
    try:
        when = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when > oldest


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
        # `0112` review F1: a `ship.md` a refused merge left is worked by `next`, not
        # accepted; accepting it reads the unit as finished with its pull request open.
        if unit.get("why") == "ship-refused":
            return ""
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
