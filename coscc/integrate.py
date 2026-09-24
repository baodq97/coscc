"""Integrating a unit whose pull request fell behind `main` (`0035`).

Not a stage. `.claude/scripts/cos.mjs` defines the loop and this module adds nothing to it:
it runs only on a unit `cos.mjs` says sits between `pr` and ship (`betweenPrAndShip`),
only when a person presses the button, and it writes no artifact. What it leaves behind is
one `integration` record in the run log per attempt (R9).

Two roads:

- **mechanical** (`behind`): `gh pr update-branch --rebase`, then the local branch follows
  the new head. No session, no quota (R4).
- **agent** (`conflicting`, `red-after-integration`): Gebo, a session under the
  `integrate` grant in `coscc/policy.py`, which may push only with a lease bound to the
  head it began at (R5, R6). Since `0052` also a `behind` unit whose `update-branch`
  exited non-zero: the press agreed to a Gebo session when the mechanical road cannot go
  (`.cos/0052_*/spec.md ## Answers, câu 1`).

Since `0052` a press fetches `origin/main` first, so a board that counted against a stale
ref no longer refuses a unit as `current`; the board read itself still does not fetch.

The pure functions come first; the `gh` calls after them; the session last.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, AsyncIterator

from coscc.harness import child_env

STATES = ("current", "behind", "conflicting", "red-after-integration", "unknown")
# R3: the three states with something to integrate. Since `0052` `current` carries a button
# too, and a press on it is refused unless the fetch it begins with finds it behind.
BUTTON_STATES = ("behind", "conflicting", "red-after-integration")
GEBO_STATES = ("conflicting", "red-after-integration")
OUTCOMES = ("pushed", "needs-person", "refused", "failed")

# Seconds. Chosen, not measured — the same figure as `board.GATE_TIMEOUT` and
# `prcomment.TIMEOUT`.
GH_TIMEOUT = 30.0
# R4: how long the app waits for GitHub's rebase to show as a new head. Chosen, not measured.
POLL_TRIES = 5
POLL_DELAY = 2.0

_RED = ("fail", "cancel")
_PR_NUMBER = re.compile(r"\(#(\d+)\)\s*$")
_NEEDS_PERSON = re.compile(r"^\s*(?:[-*]\s*)?\[needs-person\]\s*(.+?)\s*$")


class IntegrateError(Exception):
    """A `gh` call that failed, carrying `gh`'s own words."""


# --- pure --------------------------------------------------------------------


def needs_checks(pr_row: dict | str, last_record: dict | None) -> bool:
    """Whether `classify` needs the required checks: only when the pull request's head is
    the one the last integration pushed. Most board reads therefore cost no extra `gh`."""
    if not isinstance(pr_row, dict) or not last_record:
        return False
    after = str(last_record.get("head_after") or "")
    return bool(after) and after == str(pr_row.get("headRefOid") or "")


def classify(
    pr_row: dict | str,
    missing: int | str,
    origin_sha: str,
    last_record: dict | None,
    checks: list | str | None = None,
) -> dict[str, Any]:
    """R1: one of `STATES`, with the reason. Errors arrive as strings and become `unknown`.

    Order, as `plan.md` step 4 fixes it: an error; `CONFLICTING`; the integration's own
    head with red required checks; commits missing; otherwise current. `UNKNOWN`
    mergeability (GitHub still computing) goes by the count.
    """
    if isinstance(pr_row, str):
        return {"state": "unknown", "reason": pr_row}
    if isinstance(missing, str):
        return {"state": "unknown", "reason": missing}
    if str(pr_row.get("mergeable") or "") == "CONFLICTING":
        return {"state": "conflicting", "reason": "GitHub reports the pull request as CONFLICTING"}
    if needs_checks(pr_row, last_record):
        if isinstance(checks, str):
            return {"state": "unknown", "reason": checks}
        red = [str(c.get("name") or "?") for c in (checks or []) if str(c.get("bucket") or "") in _RED]
        if red:
            return {
                "state": "red-after-integration",
                "reason": "required checks red on the head the last integration pushed: " + ", ".join(red),
                "red": red,
            }
    if missing > 0:
        return {"state": "behind", "reason": f"{missing} commit(s) behind origin/main {origin_sha[:7]}"}
    return {"state": "current", "reason": ""}


def origin_note(origin_sha: str, fetch: dict | None) -> str:
    """`0052` R4: which `origin/main` a press counted against, and how it got there.

    `fetch` is what `fetches.fetch` returned (`{outcome, attempts, age}`), or
    `{"outcome": "failed", "detail": ...}`, or None when no fetch was tried.
    """
    ref = f"origin/main {origin_sha[:7] or 'unread'}"
    if not fetch:
        return ref
    outcome = str(fetch.get("outcome") or "")
    if outcome == "failed":
        how = f"fetch failed: {fetch.get('detail') or 'git said nothing'}"
    elif outcome == "joined":
        how = "joined a running fetch"
    elif outcome == "reused":
        how = f"reused {fetch.get('age', 0)}s ago"
    else:
        how = outcome or "fetched"
    return f"{ref} ({how})"


def refusal(
    *,
    in_window: bool,
    busy: str,
    clean: bool | None,
    branch_ok: bool | None,
    local_head: str,
    pr_head: str,
    state: str,
    origin: str = "",
) -> str:
    """R12: the first condition that does not hold, in the spec's order, or `""`.

    `origin` is `origin_note`'s sentence; a `current` unit is then said to be current
    against it (`0052` R4). The sentence still begins `the unit is current`.
    """
    if not in_window:
        return "this unit is not between pr and ship with an open pull request"
    if busy:
        # `steps.describe`'s sentence (`0050` R3): what holds the unit, and since when.
        return busy
    if clean is None:
        return "the unit has no worktree to integrate in"
    if clean is not True:
        return "the unit's worktree has uncommitted changes"
    if branch_ok is not True:
        return "the unit's worktree is not on the unit's branch"
    if not pr_head:
        # Nothing to compare the local head with: `gh` could not be read, so the state is
        # `unknown` too. Said as that, not as a head mismatch against "none".
        return f"the pull request's head could not be read, so the unit is {state or 'unknown'}: nothing to integrate"
    if not local_head or local_head != pr_head:
        return (
            f"the local head ({local_head[:7] or 'none'}) is not the pull request's head "
            f"({pr_head[:7] or 'none'})"
        )
    if state not in BUTTON_STATES:
        if state == "current" and origin:
            return f"the unit is current against {origin}, which has nothing to integrate"
        return f"the unit is {state}, which has nothing to integrate"
    return ""


def warnings(
    rounds: list[dict], review_status: str, gebo: bool, grant_warning: str, fallback: bool = False
) -> list[str]:
    """R13: what the page says before the button is pressed. `fallback`: the press goes the
    mechanical road, and Gebo opens if GitHub refuses it (`0052`)."""
    out: list[str] = []
    # `0061` R11.1: the app cannot tell "behind but mergeable" (spike U1, U2), so the page
    # says when integrating a passed unit is worth another round, and leaves it to a person.
    if rounds and str(rounds[-1].get("verdict") or "") == "pass":
        out.append(
            "The last review round passed. Integrating rewrites the reviewed commit: the ship "
            "gate closes and another review round is needed — it does not count toward "
            "COS_REVIEW_ROUNDS, but it is another paid session. Run ship first. Integrate "
            "only when GitHub reports a conflict or refuses the merge because the branch is "
            "behind main."
        )
    if review_status == "changes-requested":
        out.append(
            "review.md asks for changes. After integrating, cos.mjs next offers review, not "
            "impl, and that round counts toward COS_REVIEW_ROUNDS (spec C2)."
        )
    if fallback:
        out.append(
            "If GitHub refuses the rebase, the app opens Gebo, a paid agent session, to rebase "
            "this branch itself — pressing Integrate agrees to that session."
        )
    if gebo and grant_warning:
        out.append(grant_warning)
    return out


def pr_number_of(subject: str) -> int | None:
    """The `(#N)` GitHub's squash puts at the end of a subject, or None."""
    m = _PR_NUMBER.search(subject or "")
    return int(m.group(1)) if m else None


def related(
    main_commits: list[dict],
    branch_files: list[str],
    units: list[dict],
    others: list[dict],
    self_unit: str,
) -> dict[str, list[dict]]:
    """R7, both groups, computed by the app and never by Gebo.

    `main_commits`: `{sha, subject, files}` on `origin/main` since the merge-base.
    `units`: board unit dicts (`name`, `pr`). `others`: `{unit, files}` for the other
    units in the window, `files` None when their head is not here to compare.
    """
    mine = set(branch_files)
    by_pr = {
        int(u["pr"]["number"]): u["name"]
        for u in units
        if isinstance(u.get("pr"), dict) and u["pr"].get("number") is not None
    }
    merged = []
    for c in main_commits:
        shared = sorted(mine & set(c.get("files") or []))
        if not shared:
            continue
        n = pr_number_of(str(c.get("subject") or ""))
        merged.append({
            "sha": c.get("sha", ""),
            "subject": c.get("subject", ""),
            "unit": by_pr.get(n) if n is not None else None,
            "files": shared,
        })
    open_ = []
    for o in others:
        if o.get("unit") == self_unit:
            continue
        files = o.get("files")
        if files is None:
            open_.append({"unit": o["unit"], "files": None})
            continue
        shared = sorted(mine & set(files))
        if shared:
            open_.append({"unit": o["unit"], "files": shared})
    return {"merged": merged, "open": open_}


def related_units(rel: dict[str, list[dict]]) -> list[str]:
    """Every unit named by either group, once, in order."""
    out: list[str] = []
    for item in rel.get("merged", []) + rel.get("open", []):
        name = item.get("unit")
        if name and name not in out:
            out.append(name)
    return out


def read_paths(units_root: Path, own: str, rel: dict[str, list[dict]]) -> tuple[str, ...]:
    """R7: Gebo's own unit folder, and intent/spec/plan of the related units — nothing else."""
    paths = [str(units_root / own)]
    for name in related_units(rel):
        for f in ("intent.md", "spec.md", "plan.md"):
            paths.append(str(units_root / name / f))
    return tuple(paths)


def parse_needs_person(reply: str) -> list[str]:
    """Every `[needs-person] …` line in a reply, the text after the marker."""
    out = []
    for line in (reply or "").splitlines():
        m = _NEEDS_PERSON.match(line)
        if m:
            out.append(m.group(1))
    return out


def record(
    *,
    workspace: str,
    unit: str,
    pr: int | None,
    mode: str,
    head_before: str,
    head_after: str,
    origin_sha: str,
    outcome: str,
    related_: dict | None = None,
    report: str = "",
    needs_person: list[str] | None = None,
    detail: str = "",
    fetch: dict | None = None,
    merge_state: str = "",
    update_branch: dict | None = None,
) -> dict[str, Any]:
    """R9: the one record every integration leaves, whatever happened.

    `0052` R5: `fetch` is how the press got its `origin/main` and `merge_state` what GitHub
    said of the pull request then — observed, never decided on. `update_branch` is the exit
    code and words of a refused `gh pr update-branch` that sent the press to Gebo. All three
    keys are always written; a record from before `0052` has none of them.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}, got {outcome!r}")
    return {
        "kind": "integration",
        "workspace": workspace,
        "unit": unit,
        "stage": "integrate",
        "pr": pr,
        "mode": mode,
        "head_before": head_before,
        "head_after": head_after if outcome == "pushed" else "",
        "origin_sha": origin_sha,
        "outcome": outcome,
        "related": related_ or {"merged": [], "open": []},
        "report": report,
        "needs_person": list(needs_person or []),
        "detail": detail,
        "fetch": _fetch_of(fetch),
        "merge_state": merge_state,
        "update_branch": (
            {"code": update_branch.get("code"), "said": str(update_branch.get("said") or "")}
            if update_branch else None
        ),
    }


def _fetch_of(fetch: dict | None) -> dict | None:
    if not fetch:
        return None
    if fetch.get("outcome") == "failed":
        return {"outcome": "failed", "detail": str(fetch.get("detail") or "")}
    return {"outcome": fetch.get("outcome"), "age": fetch.get("age")}


def outcome_of_session(head_before: str, head_now: str, reply: str) -> str:
    """What a Gebo session did, read from git and not from what it said (spec, design 4)."""
    if head_now and head_now != head_before:
        return "pushed"
    if parse_needs_person(reply):
        return "needs-person"
    return "failed"


def describe_for_review(rec: dict[str, Any]) -> str:
    """R10: the section the next `review` prompt carries."""
    who = "the app, mechanically" if rec.get("mode") == "mechanical" else "an agent session (Gebo)"
    body = json.dumps(rec, ensure_ascii=False, indent=2)
    return (
        "# An integration since the last round\n\n"
        f"The branch was rebased onto `origin/main` by {who} — not by a person, and no "
        "person approved how any conflict was resolved. The head you review is the one it "
        "pushed. Read what it changed with the same care as any other change; a conflict "
        "resolved by dropping one side can leave the tests green. Its record, verbatim:\n\n"
        f"```json\n{body}\n```\n"
    )


def build_prompt(
    *,
    skill: str,
    unit: str,
    branch: str,
    pr: int,
    state: str,
    reason: str,
    head_before: str,
    origin_sha: str,
    rel: dict[str, list[dict]],
    units_root: Path,
    own_artifacts: dict[str, str],
    refused_update: dict | None = None,
) -> str:
    """Gebo's prompt: its rules, what is wrong, where to start, and whose intent to read.

    `refused_update` (`0052`): the `{code, said}` of the app's own `update-branch`, when
    that refusal is why this session was opened.

    The app does not rebase to find the conflicting files first (`plan.md` step 7): that
    would write to the tree before the session began, and R12 wants it clean.
    """
    parts = [skill.strip(), ""]
    parts.append(f"# This integration\n\nUnit: `{unit}`. Branch: `{branch}`. Pull request: #{pr}.")
    parts.append(f"State: `{state}` — {reason}")
    parts.append(f"Head at start: `{head_before}`. `origin/main` at start: `{origin_sha}`.")
    parts.append(
        f"The only push allowed: `git push --force-with-lease={branch}:{head_before} origin {branch}`."
    )
    if refused_update is not None:
        parts.append("\n# The mechanical rebase was refused\n")
        parts.append(
            f"The app ran `gh pr update-branch {pr} --rebase` first. It exited "
            f"{refused_update.get('code')}, and gh said:\n\n"
            f"    {refused_update.get('said') or '(nothing)'}\n\n"
            "That may be a conflict that shows only when rebasing, or something else: a "
            "login, the network, a permission. The app cannot tell which from the exit code."
        )
    parts.append("\n# Units merged into main since the branch was cut, touching the same files\n")
    if rel.get("merged"):
        for m in rel["merged"]:
            who = m.get("unit") or "no unit found"
            parts.append(f"- `{str(m.get('sha'))[:7]}` {m.get('subject')} — unit: {who}; files: {', '.join(m['files'])}")
    else:
        parts.append("- none")
    parts.append("\n# Other open units touching the same files (read only, never change them)\n")
    if rel.get("open"):
        for o in rel["open"]:
            files = ", ".join(o["files"]) if o.get("files") is not None else "no local commit, files not compared"
            parts.append(f"- {o['unit']}: {files}")
    else:
        parts.append("- none")
    names = related_units(rel)
    if names:
        parts.append("\n# Artifacts you may read for their intent\n")
        for name in names:
            for f in ("intent.md", "spec.md", "plan.md"):
                parts.append(f"- {units_root / name / f}")
    parts.append("\n# This unit's own artifacts\n")
    for name, text in own_artifacts.items():
        parts.append(f"## {name}\n\n{text.strip()}\n")
    return "\n".join(parts) + "\n"


# --- gh ----------------------------------------------------------------------


async def _gh(argv: list[str], cwd: str) -> tuple[int, str, str]:
    """One `gh` call, as `prcomment._gh` makes it: exit code and both streams."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", *argv, cwd=cwd, env=child_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        raise IntegrateError(f"gh could not be started: {e}") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=GH_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise IntegrateError(f"gh {' '.join(argv[:2])} did not answer within {GH_TIMEOUT:.0f}s") from None
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def _said(out: str, err: str) -> str:
    return (err.strip() or out.strip() or "gh failed and said nothing").splitlines()[-1]


async def open_prs(root: str) -> list[dict]:
    """Every open pull request in one call: number, head, head branch, mergeable."""
    code, out, err = await _gh(
        ["pr", "list", "--state", "open", "--json", "number,headRefOid,headRefName,mergeable",
         "--limit", "200"],
        root,
    )
    if code != 0:
        raise IntegrateError(_said(out, err))
    try:
        rows = json.loads(out or "[]")
    except ValueError as e:
        raise IntegrateError(f"gh pr list did not return JSON: {e}") from e
    return [r for r in rows if isinstance(r, dict)]


async def required_checks(tree: str, n: int) -> list[dict]:
    """The same call the `review` gate makes (`cos.mjs`, `pr checks --required`)."""
    code, out, err = await _gh(["pr", "checks", str(int(n)), "--required", "--json", "name,bucket"], tree)
    # `gh pr checks` exits 8 while checks are pending and 1 when one failed; both still
    # print the JSON, which is what is read.
    try:
        rows = json.loads(out or "null")
    except ValueError:
        rows = None
    if not isinstance(rows, list):
        raise IntegrateError(_said(out, err) if code else "gh pr checks returned no list")
    return [r for r in rows if isinstance(r, dict)]


async def update_branch(tree: str, n: int) -> tuple[int, str]:
    """`gh pr update-branch <n> --rebase`. `(exit code, what gh said)`; never raises on
    refusal. `0052`: the code goes into the record and Gebo's prompt."""
    code, out, err = await _gh(["pr", "update-branch", str(int(n)), "--rebase"], tree)
    return code, (out.strip() or err.strip())


async def merge_state(tree: str, n: int) -> str:
    """`0052` R5: GitHub's `mergeStateStatus` for one pull request, or `""`. Observed only —
    nothing decides on it — so it never raises (`spike.md ## U1` did not measure it)."""
    try:
        code, out, _ = await _gh(["pr", "view", str(int(n)), "--json", "mergeStateStatus"], tree)
    except IntegrateError:
        return ""
    if code != 0:
        return ""
    try:
        return str(json.loads(out).get("mergeStateStatus") or "")
    except (ValueError, AttributeError):
        return ""


async def pr_head(tree: str, n: int) -> str:
    """The pull request's head as GitHub has it now."""
    code, out, err = await _gh(["pr", "view", str(int(n)), "--json", "headRefOid"], tree)
    if code != 0:
        raise IntegrateError(_said(out, err))
    try:
        return str(json.loads(out).get("headRefOid") or "")
    except (ValueError, AttributeError) as e:
        raise IntegrateError(f"gh pr view did not return JSON: {e}") from e


async def pr_for_branch(tree: str, branch: str) -> dict:
    """`0041` R2: the open pull request of one branch, asked before a `pr` step starts.

    One of `{"state": "found", "url", "number", "mergeable", "head"}`, `{"state": "none",
    "branch"}` or `{"state": "unknown", "reason"}`. Never raises: a lookup that fails
    must not stop the step, only be said in its prompt. Not `open_prs`, which carries no
    `url` — widening it would change every board read for this one caller.
    """
    if not branch:
        return {"state": "unknown", "reason": "this checkout is on no branch"}
    try:
        code, out, err = await _gh(
            ["pr", "list", "--head", branch, "--state", "open",
             "--json", "url,number,mergeable,headRefOid", "--limit", "5"],
            tree,
        )
    except IntegrateError as e:
        return {"state": "unknown", "reason": str(e)}
    if code != 0:
        return {"state": "unknown", "reason": _said(out, err)}
    try:
        rows = json.loads(out or "[]")
    except ValueError as e:
        return {"state": "unknown", "reason": f"gh pr list did not return JSON: {e}"}
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    if not rows:
        return {"state": "none", "branch": branch}
    row = rows[0]
    return {
        "state": "found",
        "url": str(row.get("url") or ""),
        "number": row.get("number"),
        "mergeable": str(row.get("mergeable") or ""),
        "head": str(row.get("headRefOid") or ""),
    }


# `0055` R7. Said in the two branches where the step ends with a pull request to put
# pr.md onto; `coscc/service.py` `_sync_pr` is what does it.
PR_SYNC_NOTE = (
    "After this step ends, the app puts pr.md's title and body onto the pull request "
    "itself; do not run `gh pr edit`."
)


def describe_pr_lookup(rec: dict) -> str:
    """The prompt block `pr_for_branch`'s answer becomes. Pure."""
    heading = "# The pull request, already looked up\n\n"
    state = rec.get("state")
    if state == "found":
        text = (
            f"The app asked `gh` before this step started. This unit's branch already has an "
            f"open pull request:\n\n"
            f"    {rec.get('url')}\n\n"
            f"Number {rec.get('number')}, mergeable `{rec.get('mergeable') or 'UNKNOWN'}`, "
            f"head `{rec.get('head')}`. Use this URL for `PR:` in pr.md. Do not run "
            "`gh pr create` again: a second pull request for one branch is not what this "
            "step is for.\n\n" + PR_SYNC_NOTE
        )
        if rec.get("mergeable") == "CONFLICTING":
            text += (
                "\n\nIt conflicts with `main`. Do not rebase, merge or pull — the grant "
                "refuses it and it is not this step's work. Write the conflict under "
                "`## Where` in pr.md, set `Status: accepted`, and stop: a person resolves "
                "it with *Integrate* on the board."
            )
        return heading + text
    if state == "none":
        return heading + (
            f"The app asked `gh` before this step started. There is no open pull request "
            f"for the branch `{rec.get('branch')}`. Open one, as the rules above say.\n\n"
            + PR_SYNC_NOTE
        )
    return heading + (
        f"The app could not ask `gh` before this step started: {rec.get('reason') or 'no reason given'}. "
        "Ask once yourself with `gh pr view --json url,number,mergeable` before opening one."
    )


# --- Gebo --------------------------------------------------------------------


async def run_gebo(
    sessions: Any,
    *,
    tree: str,
    workspace: str,
    prompt: str,
    grant: Any,
    read_also: tuple[str, ...],
    lease: tuple[str, str],
    model: str | None,
) -> AsyncIterator[tuple[str, Any]]:
    """One Gebo session, streamed. Not `Runner.run`: that requires an artifact written, and
    Gebo writes none. Yields `("chunk", text)` and finally `("end", {reply, cost, ...})`."""
    from coscc.runner import CLAUDE_CODE_PRESET, Denials, permission_gate

    denials = Denials()
    reply = ""
    end: dict[str, Any] = {}
    kwargs: dict[str, Any] = {"system_prompt": dict(CLAUDE_CODE_PRESET)}
    if tree != workspace:
        kwargs["workspace"] = workspace
    if model is not None:
        kwargs["model"] = model
    async for kind, payload in sessions.stream(
        tree,
        prompt,
        None,
        max_turns=grant.max_turns,
        can_use_tool=permission_gate(grant, tree, denials, None, read_also=read_also, lease=lease),
        tools=list(grant.tools),
        max_budget_usd=grant.max_budget_usd or None,
        **kwargs,
    ):
        if kind == "chunk":
            reply += payload
            yield ("chunk", payload)
        elif kind == "tool":
            continue
        elif kind == "session":
            end["session_id"] = str(payload)
        else:  # `done`, as `Runner.run` reads it
            end.update(
                session_id=payload.get("session_id", end.get("session_id", "")),
                cost=payload.get("cost", {}) or {},
                terminal_reason=str(payload.get("terminal_reason") or ""),
                models_used=list(payload.get("models_used") or []),
            )
    end["reply"] = reply
    end["denials"] = denials.count
    end["denied"] = denials.reasons or None
    yield ("end", end)
