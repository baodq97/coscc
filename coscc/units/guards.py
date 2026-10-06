"""Every guard of the three machines (unit, run, pull request), as pure functions.

Each transition is decided by exactly one guard, with a fixed id and a one-sentence English
label; the lane config chooses a guard for each transition from `TRANSITIONS`, never switches
one off. A guard reads structured input (rows of `cos.db`, a read of git or `gh`) and never
opens a file an agent wrote. `REASONS` is the one definition of the reason codes: a guard
that answers a code not in it is refused at the answer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

# The closed table. First group: the `why` column the loop's `next` writes; second: codes the loop
# hands out as `reasons` beside its words (`test_guards.py` reads them back out of it); third:
# what the guards below refuse with; fourth: what the app refuses a step with before any spend.
REASONS = (
    # The `why` the loop's `next` gives, and a hold's move.
    "dependency",
    "unreadable",
    "finished",
    "paused",
    "dropped",
    "needs-person",
    "spike-fails",
    "spike-missing",
    "missing",
    "rejected",
    "stale",
    "review-incomplete",
    "ship-refused",
    "ship-merging",
    "draft",
    "awaits-person",
    "person-answered",
    "changes-requested",
    # `gate-closed`: a gate closed for a reason with no code of its own; its words say which.
    "ci-pending",
    "ci-red",
    "ci-unfixable",
    "waiting-on",
    "recording-ship",
    "closed",
    "overlap-pr",
    "gate-closed",
    "not-in-lane",
    # The guards' own refusals.
    "wrong-run",
    "stale-revision",
    "no-head",
    "head-moved",
    "not-open-finding",
    "agent-cannot-skip",
    "no-submission",
    "bad-branch",
    "not-merged",
    "no-refusal",
    "not-closed",
    "no-brief",
    "no-round",
    # A step refused before any spend.
    "unit-busy",
    "updating",
    "unavailable",
    "held",
    "budget-reached",
    "no-unit",
    "no-stage",
    "rerun-by-person",
    "no-worktree",
    "no-branch",
    "no-git",
    "no-run-log",
    # A required part of the stage's declared input (`agents.json` `input`) is missing.
    "input-missing",
    # An integration refused because the unit's state has nothing to integrate.
    "nothing-to-integrate",
    # A feature refused the step; the words name the feature, and its reason follows.
    "feature-refused",
)

# Who may skip a stage. `agent` and `code` never may.
DECIDERS = ("person",)


class BadVerdict(ValueError):
    """A guard answered a reason code `REASONS` does not hold."""


@dataclass(frozen=True)
class Verdict:
    """`open`, or closed with the codes that closed it. Never closed with none."""

    open: bool
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        unknown = [r for r in self.reasons if r not in REASONS]
        if unknown:
            raise BadVerdict(f"not a reason code: {', '.join(unknown)}")
        if not self.open and not self.reasons:
            raise BadVerdict("a closed verdict names at least one reason")


OPEN = Verdict(True)


def _closed(*reasons: str) -> Verdict:
    return Verdict(False, tuple(dict.fromkeys(reasons)))


@dataclass(frozen=True)
class Guard:
    id: str
    label: str
    check: Callable[[Mapping[str, Any]], Verdict]


def _same_run(inputs: Mapping[str, Any]) -> bool:
    """The object came from the run the app has open for this unit and stage."""
    run = str(inputs.get("run") or "")
    return bool(run) and run == str(inputs.get("open_run") or "")


def stage_result(inputs: Mapping[str, Any]) -> Verdict:
    """`run`, `open_run`; `revision` as submitted and `computed_revision` as the app took it."""
    reasons = []
    if not _same_run(inputs):
        reasons.append("wrong-run")
    revision = str(inputs.get("revision") or "")
    if not revision or revision != str(inputs.get("computed_revision") or ""):
        reasons.append("stale-revision")
    return _closed(*reasons) if reasons else OPEN


def review_round(inputs: Mapping[str, Any]) -> Verdict:
    """`run`, `open_run`; `head`, the SHA the app recorded when the review run opened."""
    reasons = []
    if not _same_run(inputs):
        reasons.append("wrong-run")
    if not str(inputs.get("head") or ""):
        reasons.append("no-head")
    return _closed(*reasons) if reasons else OPEN


def impl_claim(inputs: Mapping[str, Any]) -> Verdict:
    """`claims`, the `F<k>` ids; `open_ids`, the open ids of the last round."""
    open_ids = set(inputs.get("open_ids") or ())
    if any(c not in open_ids for c in inputs.get("claims") or ()):
        return _closed("not-open-finding")
    return OPEN


def unit_created(inputs: Mapping[str, Any]) -> Verdict:
    """`brief`, whether the press that opened the unit carried one."""
    return OPEN if inputs.get("brief") else _closed("no-brief")


def incomplete_round(inputs: Mapping[str, Any]) -> Verdict:
    """`round`, the number of the incomplete round the closing turn wrote into `review.md`."""
    return OPEN if inputs.get("round") else _closed("no-round")


def skip_decision(inputs: Mapping[str, Any]) -> Verdict:
    """`authority` of the decision to skip spec or plan."""
    return OPEN if inputs.get("authority") in DECIDERS else _closed("agent-cannot-skip")


def spike_holds(inputs: Mapping[str, Any]) -> Verdict:
    """`unmeasured`, the spec's `U<n>`; `verdicts`, `{U<n>: "holds" | "fails"}` from the spike."""
    verdicts = inputs.get("verdicts") or {}
    reasons = []
    for u in inputs.get("unmeasured") or ():
        if u not in verdicts:
            reasons.append("spike-missing")
        elif verdicts[u] != "holds":
            reasons.append("spike-fails")
    return _closed(*reasons) if reasons else OPEN


def dependency_merged(inputs: Mapping[str, Any]) -> Verdict:
    """`depends`, `[{ref, merged}]` with `merged` read from the PR/CI machine."""
    if any(not d.get("merged") for d in inputs.get("depends") or ()):
        return _closed("waiting-on")
    return OPEN


def _same_commit(a: str, b: str) -> bool:
    """One commit named twice, the shorter a prefix of the longer (a round from prose may name a short SHA)."""
    return (
        bool(a)
        and bool(b)
        and (a == b or (min(len(a), len(b)) >= 7 and (a.startswith(b) or b.startswith(a))))
    )


def ship_ready(inputs: Mapping[str, Any]) -> Verdict:
    """`ci` at `head`; `reviewed_head`, the head the last passing round recorded; `verdict` of
    that round; `head`, the one this guard read, which the merge is pinned to.

    `rebased`, `{reviewed, head}`: the gate's read that `head` is a clean rebase of `reviewed`,
    which stands in for a round of `head`. The guard only matches the two commits it names."""
    ci = inputs.get("ci")
    reasons = []
    if ci == "pending" or ci is None:
        reasons.append("ci-pending")
    elif ci == "red":
        reasons.append("ci-red")
    elif ci == "unfixable":
        reasons.append("ci-unfixable")
    verdict = inputs.get("verdict")
    if verdict == "changes-requested":
        reasons.append("changes-requested")
    elif verdict == "needs-person":
        reasons.append("needs-person")
    elif verdict != "pass":
        reasons.append("review-incomplete")
    head = str(inputs.get("head") or "")
    reviewed = str(inputs.get("reviewed_head") or "")
    rebased = inputs.get("rebased") or {}
    clean = (
        isinstance(rebased, Mapping)
        and _same_commit(str(rebased.get("head") or ""), head)
        and _same_commit(str(rebased.get("reviewed") or ""), reviewed)
    )
    if not head:
        reasons.append("no-head")
    elif not _same_commit(head, reviewed) and not clean:
        reasons.append("head-moved")
    return _closed(*reasons) if reasons else OPEN


def run_submitted(inputs: Mapping[str, Any]) -> Verdict:
    """`submitted`: whether the `submit` handler accepted an object during the run."""
    return OPEN if inputs.get("submitted") else _closed("no-submission")


def branch_named(inputs: Mapping[str, Any]) -> Verdict:
    """`branch_ok`: what the loop's `check-branch` said of the unit's branch."""
    return OPEN if inputs.get("branch_ok") else _closed("bad-branch")


def ci_at_head(inputs: Mapping[str, Any]) -> Verdict:
    """`head` the machine holds; `read_head`, the head the checks were read at."""
    head = str(inputs.get("head") or "")
    return OPEN if head and head == str(inputs.get("read_head") or "") else _closed("head-moved")


def merge_read(inputs: Mapping[str, Any]) -> Verdict:
    """`merge_commit`, as `gh pr view --json state,mergeCommit` gave it."""
    return OPEN if str(inputs.get("merge_commit") or "") else _closed("not-merged")


def merge_refused(inputs: Mapping[str, Any]) -> Verdict:
    """`refused`, what `gh pr merge` or the read after it said when GitHub made no merge."""
    return OPEN if str(inputs.get("refused") or "") else _closed("no-refusal")


def close_read(inputs: Mapping[str, Any]) -> Verdict:
    """`state`, as `gh pr view` gave it."""
    return OPEN if inputs.get("state") == "CLOSED" else _closed("not-closed")


GUARDS: dict[str, Guard] = {
    g.id: g
    for g in (
        Guard(
            "stage-result",
            "A stage's artifact takes the judgement its own run submitted about the revision the app read.",
            stage_result,
        ),
        Guard(
            "review-round",
            "A review round counts only from the run that reviewed the head the app recorded.",
            review_round,
        ),
        Guard(
            "impl-claim",
            "Impl may claim a person is needed only for an open finding of the last round.",
            impl_claim,
        ),
        Guard(
            "unit-created",
            "A unit opens accepted only from a brief a person gave.",
            unit_created,
        ),
        Guard(
            "incomplete-round",
            "A review goes back to draft only when its closing turn wrote an incomplete round.",
            incomplete_round,
        ),
        Guard(
            "skip-decision",
            "Spec or plan is skipped only on a person's decision.",
            skip_decision,
        ),
        Guard(
            "spike-holds",
            "Plan opens only once every unmeasured item of the spec has a spike verdict of holds.",
            spike_holds,
        ),
        Guard(
            "dependency-merged",
            "Impl opens only once every unit it depends on has merged.",
            dependency_merged,
        ),
        Guard(
            "ship-ready",
            "Ship opens only on green CI and a passing review of the head being merged, or of one it is a clean rebase of.",
            ship_ready,
        ),
        Guard(
            "run-submitted",
            "A run ends done only once the app has received its object.",
            run_submitted,
        ),
        Guard(
            "branch-named",
            "A pull request is opened only from a branch the harness's grammar accepts.",
            branch_named,
        ),
        Guard(
            "ci-at-head",
            "CI moves only on a read of the required checks at the head the machine holds.",
            ci_at_head,
        ),
        Guard(
            "merge-read",
            "A merge is recorded only from a read that names its merge commit.",
            merge_read,
        ),
        Guard(
            "merge-refused",
            "A merge GitHub did not make is recorded only with what refused it.",
            merge_refused,
        ),
        Guard(
            "close-read",
            "A pull request is closed only on a read that says it is closed.",
            close_read,
        ),
    )
}

# Each machine's transitions and the guards the lane config may choose from; a config must name one for every transition.
TRANSITIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "unit": {
        "create": ("unit-created",),
        "result": ("stage-result",),
        "round": ("review-round",),
        "claim": ("impl-claim",),
        "incomplete": ("incomplete-round",),
        "skip": ("skip-decision",),
        "plan": ("spike-holds",),
        "impl": ("dependency-merged",),
        "ship": ("ship-ready",),
    },
    "run": {
        "submitted": ("run-submitted",),
    },
    "pr": {
        "open": ("branch-named",),
        "ci": ("ci-at-head",),
        "merge-requested": ("ship-ready",),
        "merged": ("merge-read",),
        "refused": ("merge-refused",),
        "closed": ("close-read",),
    },
}


def guard(guard_id: str) -> Guard:
    try:
        return GUARDS[guard_id]
    except KeyError:
        raise KeyError(f"no guard {guard_id!r}; there are {', '.join(GUARDS)}") from None
