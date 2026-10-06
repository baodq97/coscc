"""What a step left: the branch it may push, the snapshot of a failed attempt, and writing the
artifact.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk.types import SystemPromptPreset

from coscc.git import gitops
from coscc.agent import sessions as sessions_mod
from coscc.runner.reply import (
    ATTEMPT_EXCERPT,
    OpeningError,
    RunError,
    _unfence,
    from_title,
    opening_problem,
    opening_reason,
)
from coscc.runner.prompt import answers_section, strip_answers, with_answers
from coscc.runner.review import merge_review

log = logging.getLogger(__name__)


# # What a board step holding any tool runs on, instead of the empty system prompt the SDK
# # sends when none is set; without it a step with `Read` and `Grep` searches with `grep`
# # through Bash. Bare on purpose: it grants nothing (the gate and the grant's tool list still
# # hold every call), and tool-less steps and chat never get it. A copy goes out.
CLAUDE_CODE_PRESET: SystemPromptPreset = {"type": "preset", "preset": "claude_code"}


async def branch_of(cwd: str) -> str:
    """The branch the worktree at `cwd` stands on, the one its session may push; "" for the
    trunk, a detached HEAD or no checkout. A failure costs the push, never the step."""
    path = Path(cwd)
    if not (path / ".git").exists():
        return ""
    try:
        _, branch = await gitops.head_and_branch(path)
    except gitops.GitError:
        return ""
    return "" if branch in (gitops.TRUNK, "detached") else branch


async def snapshot(cwd: str, session_id: str) -> tuple[dict[str, Any], BaseException | None]:
    """What a stopped step left behind: git state and a transcript excerpt, read-only.

    Every field is attempted independently; a failure is recorded under `snapshot_errors`.
    Returns `(fields, pending)`: `pending` is a `CancelledError` this was interrupted by, for
    the caller to re-raise once it has written what it has.
    """
    fields: dict[str, Any] = {
        "head": None,
        "branch": None,
        "base": None,
        "base_ref": None,
        "commits": None,
        "status": None,
        "excerpt": None,
        "excerpt_total_chars": None,
    }
    errors: list[str] = []
    path = Path(cwd)

    if not (path / ".git").exists():
        errors.append(f"git: {cwd} is not a git checkout")
    else:
        try:
            fields["head"], fields["branch"] = await gitops.head_and_branch(path)
        except asyncio.CancelledError:
            errors.append("head/branch: cancelled while reading")
            fields["snapshot_errors"] = errors
            return fields, asyncio.CancelledError("head/branch")
        except (gitops.GitError, OSError) as e:
            errors.append(f"head/branch: {e}")

        try:
            fields["base"], fields["base_ref"] = await gitops.merge_base(path)
        except asyncio.CancelledError:
            errors.append("base: cancelled while reading")
            fields["snapshot_errors"] = errors
            return fields, asyncio.CancelledError("base")
        except (gitops.GitError, OSError) as e:
            errors.append(f"base: {e}")

        if fields["base"] and fields["head"]:
            try:
                fields["commits"] = await gitops.log_range(path, fields["base"], fields["head"])
            except asyncio.CancelledError:
                errors.append("commits: cancelled while reading")
                fields["snapshot_errors"] = errors
                return fields, asyncio.CancelledError("commits")
            except (gitops.GitError, OSError, ValueError) as e:
                errors.append(f"commits: {e}")

        try:
            fields["status"] = await gitops.status_porcelain(path)
        except asyncio.CancelledError:
            errors.append("status: cancelled while reading")
            fields["snapshot_errors"] = errors
            return fields, asyncio.CancelledError("status")
        except (gitops.GitError, OSError) as e:
            errors.append(f"status: {e}")

    try:
        if session_id:
            excerpt, total = await asyncio.to_thread(
                sessions_mod.transcript_excerpt, session_id, cwd, ATTEMPT_EXCERPT
            )
            fields["excerpt"], fields["excerpt_total_chars"] = excerpt, total
        else:
            errors.append("excerpt: no session id was resolved before the step stopped")
    except asyncio.CancelledError:
        errors.append("excerpt: cancelled while reading")
        fields["snapshot_errors"] = errors
        return fields, asyncio.CancelledError("excerpt")
    except (ValueError, OSError) as e:
        errors.append(f"excerpt: {e}")

    if errors:
        fields["snapshot_errors"] = errors
    return fields, None


def _fmt_num(value: Any, suffix: str = "") -> str:
    return f"{value}{suffix}" if value is not None else "unknown — the session returned no result"


def describe_attempt(found: dict[str, Any]) -> str:
    """The `# The attempt before this one` section, in English (instructions to the model).

    `found` is `Journal.failed_attempts`'s return value.
    """
    attempt = found.get("attempt")
    latest = found.get("latest") or {}
    earlier = found.get("earlier") or []

    lines: list[str] = [
        "This is the state of the tree and the session at the moment the previous "
        "attempt at this stage stopped. The tree may have changed since then.",
        "",
    ]

    if attempt is None:
        lines.append(
            "No snapshot record was captured for that attempt (the capture itself may "
            "have failed, or ran before this app could take one). What is known comes "
            "only from the run log's own end-of-run record:"
        )
        lines.append(f"Outcome: {latest.get('outcome')}")
        lines.append(f"Turns: {_fmt_num(latest.get('turns'))}")
        lines.append(f"Cost: {_fmt_num(latest.get('cost_usd'), ' USD')}")
    else:
        lines.append(f"Outcome: {attempt.get('outcome')}")
        lines.append(f"Terminal reason: {attempt.get('terminal') or '(none)'}")
        err = attempt.get("error")
        lines.append(f"Error: {err['type']}: {err['message']}" if err else "Error: (none)")
        lines.append(f"Turns: {_fmt_num(attempt.get('turns'))}")
        lines.append(f"Cost: {_fmt_num(attempt.get('cost_usd'), ' USD')}")
        lines.append(f"Session: {attempt.get('session_id') or '(none resolved)'}")
        if attempt.get("head"):
            lines.append(
                f"Head: {attempt['head']} on {attempt.get('branch') or '(unknown branch)'}"
            )
        if attempt.get("base"):
            lines.append(f"Base: {attempt['base']} ({attempt.get('base_ref') or '(unknown ref)'})")
        for c in attempt.get("commits") or []:
            lines.append(f"{c['sha']} {c['subject']}")
        for s in attempt.get("status") or []:
            lines.append(s)
        errs = attempt.get("snapshot_errors") or []
        if errs:
            lines.append("Could not read: " + "; ".join(errs))
        excerpt = attempt.get("excerpt")
        if excerpt:
            total = attempt.get("excerpt_total_chars") or len(excerpt)
            lines.append(f"--- excerpt: last {len(excerpt)} of {total} characters, verbatim ---")
            lines.append(excerpt)
            lines.append("--- end of excerpt ---")

    if earlier:
        lines.append("")
        lines.append("Earlier attempts before that one, oldest first:")
        for e in earlier:
            lines.append(
                f"at {e.get('at')}: {e.get('outcome')}, "
                f"{_fmt_num(e.get('turns'), ' turns')}, cost {_fmt_num(e.get('cost_usd'), ' USD')}"
            )

    # A review that ran out of turns and left no round: what it had opened, read from its events
    # by `Journal.failed_attempts`. Nothing of it is in `review.md`.
    opened = found.get("opened")
    if opened is not None:
        lines.append("")
        lines.append(
            "That review ran out of turns, and "
            + (
                "the closing turn the app gave it wrote no round"
                if opened.get("closing")
                else "the app could not give it a closing turn"
            )
            + ": `review.md` holds nothing from it."
        )
        if opened.get("purged"):
            lines.append(
                "Which files it opened is not known: its recorded events have been purged."
            )
        elif opened.get("error"):
            lines.append(
                f"Which files it opened could not be read from its events: {opened['error']}"
            )
        elif opened.get("paths"):
            lines.append(
                "It opened these files, but no conclusion about any of them was written "
                "down. Read them again where you need to; do not take them as reviewed:"
            )
            lines.extend(f"- {p}" for p in opened["paths"])
        else:
            lines.append("Its recorded events name no file it opened.")

    return "\n".join(lines)


async def _head_of(cwd: str) -> str:
    """The commit a step ran on, for its `start` record, `""` when there is none to name.

    A failure costs the record one field; it never stops the step.
    """
    path = Path(cwd)
    if not (path / ".git").exists():
        return ""
    try:
        return await gitops.rev_parse(path, "HEAD")
    except gitops.GitError:
        return ""


async def _tree_state(path: str) -> tuple[str, str]:
    """`gitops.tree_state`, with a git failure turned into a reason the step stops for."""
    try:
        return await gitops.tree_state(Path(path))
    except gitops.GitError as e:
        raise RunError(f"could not read the worktree's state: {e}") from e


def describe_tree_change(before: tuple[str, str], after: tuple[str, str]) -> str:
    """`""` when the two `(HEAD, porcelain)` readings agree, else what moved.

    Lists the porcelain lines on one side only, and a `HEAD` that moved as `HEAD <a>→<b>`.
    """
    parts: list[str] = []
    if before[0] != after[0]:
        parts.append(f"HEAD {before[0][:12]}→{after[0][:12]}")
    old, new = before[1].splitlines(), after[1].splitlines()
    moved = [line for line in new if line not in old] + [
        f"{line} (no longer)" for line in old if line not in new
    ]
    parts.extend(line.strip() for line in moved)
    return ", ".join(parts)


def _write_artifact(directory: Path, artifact: str, text: str, blocks: int | None = None) -> None:
    """Write an artifact the app writes, from `text`, or raise the reason it is not one.

    One check and one write, for a step's reply, a spike's progress file and a review's closing
    turn. `text` is everything the session said, or at its ceiling what it said after its last
    tool call; the artifact is what follows its last title line. `blocks`, when given, is how
    many pieces the session said it in, for the reason. Synchronous on purpose: see the comment
    where `Runner.run` calls it.
    """
    # The reply's own `## Answers` never reaches disk; only the section already there does, read
    # as late as possible (after every `await` of the step, with no yield before the write), so a
    # block a person appended while the step ran is carried through untouched.
    body = strip_answers(from_title(_unfence(text), artifact) + "\n")
    # Asked of what will be written, below the reply's own `## Answers` cut, and before the file
    # is read: a refusal leaves it byte for byte.
    problem = opening_problem(body, artifact)
    if problem:
        # Typed, so `Runner.run` can tell this refusal from the others by its class.
        raise OpeningError(opening_reason(artifact, problem, blocks), problem)
    target = directory / artifact
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        raw = b""
    section = answers_section(raw)
    above = raw[: len(raw) - len(section)] if section is not None else raw
    if artifact == "review.md":
        # `merge_review` never sees the Answers section. A reply that rewrites an earlier round, or
        # adds none, still raises before anything below is written.
        body = merge_review(above.decode("utf-8", errors="replace"), body)
        # Asked of the merged text so what reaches disk is what was checked.
        problem = opening_problem(body, artifact)
        if problem:
            raise OpeningError(opening_reason(artifact, problem, blocks), problem)
    target.write_bytes(with_answers(body, section))
