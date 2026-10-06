"""The gate every session's tool calls go through, and the helpers one run started.

Every session runs Claude Code's `auto` mode. `Gate` is what the app puts in front of it:

- its `PreToolUse` hook asks `Helpers.refused` (where a `SendMessage` may go, and that it keeps
  `PROTOCOL`'s shape) and then
  `policy.critical` before `auto` sees a call, and turns any error of its own into a refusal: the
  CLI reads a hook that raised as having no opinion and runs the call;
- `can_use_tool`, which `auto` asks only after its classifier refused several times running,
  always refuses;
- `system` records the classifier's refusals, which reach neither of the two.

All three go into the run's `Denials`. `Helpers` is one run's ledger, fed by
`SubagentStart`/`SubagentStop` and the stream's `task_*` system messages; it backs `peers`, the
message filter and what `tell` hands the recorder (`worker_start`, `worker_end`,
`worker_write`). It is dropped with the run.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

import claude_agent_sdk as sdk
from claude_agent_sdk import HookMatcher, tool
from claude_agent_sdk.types import HookEvent, SyncHookJSONOutput

from coscc.agent.policy import (
    BACKGROUND_REFUSAL,
    PEERS_TOOL,
    SEND_MESSAGE,
    SUBAGENTS,
    GUARDED,
    WRITE_TOOLS,
    Grant,
    classified,
    critical,
    lacked,
    pushes,
)

log = logging.getLogger(__name__)

# The leading session, as `SendMessage` names it.
MAIN = "main"
# What the fallback answers: `auto` asks it only once its classifier stopped deciding.
NOT_APPROVED = "auto mode did not approve"
# `PEERS_TOOL` without its server's prefix.
PEERS = PEERS_TOOL.rsplit("__", 1)[-1]
# The first word of every message between the agents of a step.
KINDS = ("need", "changed", "done", "blocked")
# The kinds that point at code, each naming the files it means (`path:line` when it can).
POINTING = ("changed", "done")
# A message's most lines: the longest of 100 real ones ran 33, and 14 ran over 20.
MESSAGE_LINES = 40
_KIND = re.compile(r"\s*(\w+):")
_PATH = re.compile(r"[\w~-]*[/.][\w.~/-]*\w")

# In the leading session's prompt and in every `worker`'s, the same words.
PROTOCOL = """# Working with helpers

When the prompt's `# The plan's parallel steps` names two or more steps, first finish and commit
the plan's files no step names. Then start one `worker` per parallel step with `Agent`, all in the same
turn and never with `run_in_background`: the step's name as `description`, its paths and what it
reports in the prompt. Once all are done, read each one's diff, commit, and run
`## Verification` once on the whole worktree. Only the leading session commits: a helper runs
git only to read.

The agents of this step talk through `SendMessage`, to `"main"` (the leading session) or to an id
`mcp__cos__peers` lists; any other address is refused.
- A worker sends a message only when it needs something outside its own paths, changes an
  interface another step uses, or is done or blocked.
- A message is 1 to 40 lines. Its first line opens with one of `need:`, `changed:`, `done:`,
  `blocked:`; a `changed:` or `done:` names each file it means, as `path:line` when it can.
- No message for courtesy or to say one arrived. When agents disagree, the leading session
  decides.
- Every worker ends with exactly one `done:` or `blocked:` to `"main"`: that is the report its
  step names."""


# `policy.SUBAGENTS` as the session gets them: `worker`'s prompt carries `PROTOCOL`.
DEFINITIONS = {
    **SUBAGENTS,
    "worker": {**SUBAGENTS["worker"], "prompt": f"{SUBAGENTS['worker']['prompt']}\n\n{PROTOCOL}"},
}


def malformed(message: object) -> str:
    """Why a message between the agents of a step breaks `PROTOCOL`, or ""."""
    if not isinstance(message, str) or not message.strip():
        return "SendMessage takes its message as text"
    found = _KIND.match(message)
    kind = found.group(1) if found else ""
    if kind not in KINDS:
        opens = ", ".join(f"`{k}:`" for k in KINDS)
        return f"a message's first line opens with one of {opens}; this one opens {message.strip()[:30]!r}"
    lines = len(message.strip().splitlines())
    if lines > MESSAGE_LINES:
        return (
            f"a message is at most {MESSAGE_LINES} lines; this one is {lines}: keep what the "
            "receiver acts on and point at `path:line` for the rest"
        )
    if kind in POINTING and not _PATH.search(message.split(":", 1)[1]):
        return f"a `{kind}:` names each file it means (as `coscc/bus.py:42`); this one names none"
    return ""


class Told(TypedDict, total=False):
    """What `tell` hands the recorder with a `worker_*` kind."""

    agent_id: str
    helper: str
    step: str
    at_ms: int | None
    status: str
    total_tokens: int | None
    duration_ms: int | None
    tool: str
    path: str


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Helper:
    id: str
    kind: str = ""
    # The `description` of the `Agent` call that started it: a worker's step.
    step: str = ""
    started_ms: int | None = None
    ended_ms: int | None = None
    # From `task_notification`; the SDK gives no cost per helper.
    total_tokens: int | None = None
    duration_ms: int | None = None
    status: str = ""
    told_start: bool = False
    told_end: bool = False


class Helpers:
    """One run's helpers, by the `agent_id` the CLI gave each, which is also its task's id.

    `tell(kind, fields)` is the recorder's, or `None`: told once each of `worker_start` (when both
    its start and its step are known), `worker_end` (when both its end and its usage are) and
    every `worker_write`. `close` tells what is left.
    """

    def __init__(self, tell: Callable[[str, Told], None] | None = None):
        self.tell = tell
        self.seen: dict[str, Helper] = {}

    def _of(self, agent_id: str) -> Helper:
        return self.seen.setdefault(agent_id, Helper(agent_id))

    def helpers(self) -> list[Helper]:
        """The entries that are helpers: a kind came with `SubagentStart` or `task_started`.
        Any other task the stream names is kept out of `peers` and of who may be messaged."""
        return [h for h in self.seen.values() if h.kind]

    def _tell(self, kind: str, fields: Told) -> None:
        if self.tell is None:
            return
        try:
            self.tell(kind, fields)
        except Exception:
            # A measurement, never a reason to stop a helper.
            log.exception("%s was not recorded", kind)

    def _fields(self, h: Helper) -> Told:
        return {"agent_id": h.id, "helper": h.kind, "step": h.step}

    def _started(self, h: Helper, force: bool = False) -> None:
        if h.told_start or not h.kind or h.started_ms is None or (not h.step and not force):
            return
        h.told_start = True
        self._tell("worker_start", {**self._fields(h), "at_ms": h.started_ms})

    def _ended(self, h: Helper, force: bool = False) -> None:
        if h.told_end or (not force and (h.ended_ms is None or h.total_tokens is None)):
            return
        self._started(h, force=True)
        h.told_end = True
        self._tell(
            "worker_end",
            {
                **self._fields(h),
                "at_ms": h.ended_ms,
                "status": h.status,
                "total_tokens": h.total_tokens,
                "duration_ms": h.duration_ms,
            },
        )

    def refused(self, tool_name: str, tool_input: Mapping[str, object]) -> str:
        """Why a `SendMessage` is refused, or "": it goes to `"main"` or to a helper of this run,
        in `PROTOCOL`'s shape."""
        if tool_name != SEND_MESSAGE:
            return ""
        to = tool_input.get("to")
        if to != MAIN and to not in {h.id for h in self.helpers()}:
            return f'SendMessage goes only to "{MAIN}" or to a helper {PEERS_TOOL} lists: {to!r}'
        return malformed(tool_input.get("message"))

    def wrote(self, agent_id: str, tool_name: str, tool_input: Mapping[str, object]) -> None:
        """A helper's write the gate let through, told as `worker_write`."""
        h = self._of(agent_id)
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        self._tell(
            "worker_write",
            {**self._fields(h), "tool": tool_name, "path": str(path), "at_ms": _now_ms()},
        )

    async def subagent_start(
        self, hook_input: Any, _tool_use_id: str | None, _context: Any
    ) -> SyncHookJSONOutput:
        if not hook_input.get("agent_id"):
            return {}
        h = self._of(str(hook_input["agent_id"]))
        h.kind = h.kind or str(hook_input.get("agent_type") or "")
        h.started_ms = _now_ms()
        self._started(h)
        return {}

    async def subagent_stop(
        self, hook_input: Any, _tool_use_id: str | None, _context: Any
    ) -> SyncHookJSONOutput:
        if not hook_input.get("agent_id"):
            return {}
        h = self._of(str(hook_input["agent_id"]))
        h.ended_ms = _now_ms()
        self._ended(h)
        return {}

    def system(self, message: Any) -> None:
        """One of the stream's `SystemMessage`s; only `task_*` ones count."""
        subtype = str(getattr(message, "subtype", "") or "")
        data = getattr(message, "data", None) or {}
        if not subtype.startswith("task_") or not data.get("task_id"):
            return
        h = self._of(str(data["task_id"]))
        if subtype == "task_started":
            h.step = h.step or str(data.get("description") or "")
            h.kind = h.kind or str(data.get("subagent_type") or "")
            self._started(h)
        elif subtype == "task_notification":
            usage = data.get("usage") or {}
            h.status = str(data.get("status") or "")
            h.total_tokens = int(usage.get("total_tokens") or 0)
            h.duration_ms = int(usage.get("duration_ms") or 0)
            self._ended(h)
        elif subtype == "task_updated":
            h.status = str((data.get("patch") or {}).get("status") or h.status)

    def close(self) -> None:
        """Tells what the run never finished telling: a helper whose end or usage never came."""
        for h in self.helpers():
            if h.started_ms is not None or h.step:
                self._ended(h, force=True)

    def listing(self) -> str:
        lines = [
            f"{h.id} · {h.step or '(no step named)'} · "
            + ("done" if h.ended_ms is not None or h.told_end else "running")
            for h in self.helpers()
        ]
        return "\n".join(lines) or "no helper has started in this step"

    async def _peers(self, _args: object) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": self.listing()}]}

    def tool(self) -> Any:
        """`peers`, for the `cos` server."""
        return tool(
            PEERS,
            "The helpers of this step, one per line: id · step · running or done. SendMessage "
            f'goes only to "{MAIN}" or to one of these ids.',
            {},
        )(self._peers)


class Denials:
    """Counts what a run was refused, keeps the first few reasons, and counts the calls `auto`
    is estimated to have sent to its classifier (`policy.classified`).

    The count matters: a step told no fifty times worked around it, and the journal is the
    only place that shows it.
    """

    KEEP = 5

    def __init__(self) -> None:
        self.count = 0
        # Of `count`, the refusals of a run in the background.
        self.background = 0
        self.reasons: list[str] = []
        # The calls let through that `auto` is estimated to have classified: a time signal.
        self.classified = 0
        # Told of every refusal, with what was asked, when a step has a recorder. `KEEP` bounds only
        # `reasons`.
        self.listener: Any = None

    def record(self, tool: str, reason: str, tool_input: Any = None) -> None:
        self.count += 1
        if BACKGROUND_REFUSAL in reason:
            self.background += 1
        if len(self.reasons) < self.KEEP:
            self.reasons.append(f"{tool}: {reason}")
        if self.listener is not None:
            try:
                self.listener(tool, tool_input, reason, lacked(reason))
            except Exception:
                # The recorder never reaches the gate.
                log.exception("a refused tool was not recorded")


class Gate:
    """What stands in front of one session's `auto` mode: the grant issued for its run, the run's
    `Denials`, and the run's `Helpers` when it may start them. Built by the app before the
    session opens, and dropped with it; a grant without its secrets is refused here.
    `before_push` is the features' guards (`kernel.Hooks.refusal`), asked again before a `git push`
    the grant lets through: its words refuse the push."""

    def __init__(
        self,
        grant: Grant,
        denials: Denials | None = None,
        helpers: Helpers | None = None,
        before_push: Callable[[], str] | None = None,
    ):
        if not grant.secrets:
            raise ValueError("a gate needs a grant that names the secrets it denies")
        self.grant = grant
        self.denials = denials if denials is not None else Denials()
        self.helpers = helpers
        self.before_push = before_push

    def refused(self, tool_name: str, tool_input: dict, agent_id: str | None) -> str:
        """Why the hook denies this call, or ""."""
        reason = (self.helpers or Helpers()).refused(tool_name, tool_input) or critical(
            self.grant, tool_name, tool_input, agent_id
        )
        if reason or self.before_push is None or not pushes(self.grant, tool_name, tool_input):
            return reason
        said = self.before_push()
        return f"{GUARDED}: {said}" if said else ""

    async def pre_tool_use(
        self, hook_input: Any, _tool_use_id: str | None, _context: Any
    ) -> SyncHookJSONOutput:
        """Asked before every call, a helper's included. Anything this raises is a refusal."""
        tool_name, tool_input = "", {}
        try:
            tool_name = str(hook_input.get("tool_name") or "")
            tool_input = hook_input.get("tool_input") or {}
            agent_id = hook_input.get("agent_id")
            reason = self.refused(tool_name, tool_input, agent_id)
            if not reason:
                self._let_through(tool_name, tool_input, agent_id)
        except Exception as e:
            log.exception("the gate failed on a call to %s", tool_name or "a tool")
            reason = f"the app's own check failed, so the call is refused: {type(e).__name__}: {e}"
        if not reason:
            # No opinion: `auto` decides.
            return {}
        self.denials.record(tool_name, reason, tool_input)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    def _let_through(self, tool_name: str, tool_input: dict, agent_id: str | None) -> None:
        if classified(self.grant, tool_name, tool_input):
            self.denials.classified += 1
        if self.helpers is not None and agent_id is not None and tool_name in WRITE_TOOLS:
            self.helpers.wrote(agent_id, tool_name, tool_input)

    async def can_use_tool(self, tool_name: str, tool_input: dict, context: Any):
        """`auto`'s fallback once its classifier stopped deciding: always a refusal."""
        why = getattr(context, "decision_reason", None)
        self.denials.record(tool_name, f"{NOT_APPROVED}{f' ({why})' if why else ''}", tool_input)
        return sdk.PermissionResultDeny(message=NOT_APPROVED)

    def system(self, message: Any) -> None:
        """One of the stream's `SystemMessage`s: a refusal `auto` made itself is recorded, and the
        rest goes to the run's helpers."""
        if getattr(message, "subtype", "") == "permission_denied":
            data = getattr(message, "data", None) or {}
            kind = str(data.get("decision_reason_type") or "")
            said = str(data.get("decision_reason") or data.get("message") or "")
            who = "the auto mode classifier" if kind == "classifier" else f"auto mode ({kind})"
            self.denials.record(str(data.get("tool_name") or ""), f"{who} refused it: {said}")
            return
        if self.helpers is not None:
            self.helpers.system(message)

    def allowed(self) -> list[str]:
        """The app's own MCP tools this session holds, allowed by name so they skip the classifier."""
        return list(self.grant.mcp)

    def hooks(self) -> dict[HookEvent, list[HookMatcher]]:
        """For `ClaudeAgentOptions.hooks`: every tool call and, with helpers, each one's start and
        stop."""
        out: dict[HookEvent, list[HookMatcher]] = {
            "PreToolUse": [HookMatcher(hooks=[self.pre_tool_use])]
        }
        if self.helpers is not None:
            out["SubagentStart"] = [HookMatcher(hooks=[self.helpers.subagent_start])]
            out["SubagentStop"] = [HookMatcher(hooks=[self.helpers.subagent_stop])]
        return out
