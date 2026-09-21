"""Listing, creating and resuming sessions.

Two layers live here, and the split from `spec.md` matters:

- The **read layer** (`list_for_directory`, `history`) is pure disk. It needs no live
  client, which is what lets R1 and R6 hold across a restart of this app.
- The **session layer** (`Sessions`) owns one SDK client per live session. The client
  spawns its own CLI process, so the lifetime is this app's to decide.

A session is a transcript, not a process (`spec.md`). Resuming means continuing a record
on disk, not attaching to something still running.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import claude_agent_sdk as sdk
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock

from cos_baodo.config import Config


class Refused(Exception):
    """A request the config does not allow. Carries a reason the caller can show."""


# ---------------------------------------------------------------------------
# Read layer
# ---------------------------------------------------------------------------


def _text_of(content: Any) -> str:
    """Flatten one stored message's content to text.

    The transcript stores content either as a bare string or as a list of blocks. Only
    text blocks are kept: tool blocks have no reading in a chat-only app, and rendering
    them is explicitly a later intent.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def list_for_directory(directory: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Sessions belonging to one project directory (R1).

    `cwd` is carried through on every entry so a caller can check that nothing from
    another project leaked in — R1 asks for exactly that.
    """
    infos = sdk.list_sessions(directory=directory, limit=limit, include_worktrees=False)
    return [
        {
            "session_id": i.session_id,
            "summary": i.custom_title or i.summary or i.first_prompt or "(no summary)",
            "cwd": i.cwd,
            "last_modified": i.last_modified,
            "created_at": i.created_at,
            "git_branch": i.git_branch,
        }
        for i in infos
    ]


def history(session_id: str, directory: str | None = None) -> list[dict[str, Any]]:
    """Conversation read back from the SDK's session store (R6).

    The app keeps no copy. `spec.md` C6: a second store would be a second truth, and the
    one that counts is the one Claude actually reads.
    """
    messages = sdk.get_session_messages(session_id, directory=directory)
    out = []
    for m in messages:
        if m.parent_tool_use_id or m.parent_agent_id:
            continue  # subagent traffic, not this conversation
        text = _text_of((m.message or {}).get("content"))
        if not text.strip():
            continue
        out.append({"role": m.type, "text": text, "uuid": m.uuid})
    return out


def exists(session_id: str, directory: str | None = None) -> bool:
    return sdk.get_session_info(session_id, directory=directory) is not None


# ---------------------------------------------------------------------------
# Session layer
# ---------------------------------------------------------------------------


@dataclass
class Live:
    client: ClaudeSDKClient
    session_id: str
    cwd: str


def _options(config: Config, cwd: str, resume: str | None) -> ClaudeAgentOptions:
    """Map the four knobs onto the SDK.

    `fork_session=False` is the line `spec.md` C7 warns about: the forking flavour of
    resume returns a *new* id, every other part of the app keeps working, and R3 fails
    silently. It is written out rather than left to the default so that deleting it is a
    visible edit.
    """
    return ClaudeAgentOptions(
        cwd=cwd,
        tools=config.effective_tools(),
        permission_mode=config.permission_mode(),
        resume=resume,
        fork_session=False,  # spec.md C7 — R3 needs the same id back, not a branch
        model=config.model,
        max_turns=1,
        setting_sources=None,  # no project/user settings can widen the tool list
    )


def _resolve(directory: str) -> Path | None:
    """The directory as one comparable value, or `None` if it is not a path at all.

    `ValueError` is caught alongside `OSError` because an embedded null raises that one,
    not the other — and a crash here would turn a question about sessions into a 500.
    """
    try:
        return Path(directory).expanduser().resolve()
    except (OSError, ValueError):
        return None


class Sessions:
    """Holds the live clients. One per session id, created on demand."""

    def __init__(self, config: Config):
        self.config = config
        # Who counts as a workspace. Defaults to the env list, and `Service` replaces it
        # with the union of env and store (`spec.md` R21).
        #
        # `0003` found the reason this has to be injected rather than hardcoded: this
        # layer used to ask `config.is_workspace` directly, so a store-backed workspace
        # passed the service gate and was refused here — two implementations of one
        # question, which is exactly what R10 exists to prevent. The guard stays (it is
        # the last thing before a CLI process is spawned); only the answer is shared.
        self.membership = config.is_workspace
        self._live: dict[str, Live] = {}
        self._created_here: set[str] = set()
        self._lock = asyncio.Lock()

    def created_here(self, session_id: str) -> bool:
        return session_id in self._created_here

    def live_in(self, directory: str) -> list[str]:
        """Session ids with a live client in this directory, newest registration last.

        `0005` R6: `pull` rewrites files under a running turn, so the service asks this
        before it lets `git` near a workspace.

        **It sees this process only.** `_live` is a dict in memory, so a second app on the
        same working folder is invisible here and `pull` will proceed under its session.
        That is `0005` C2, recorded and not fixed — closing it needs a mark on disk, which
        `intent.md` did not authorise. Do not read an empty list as "nobody is working".

        Compared by resolved path, not by string: the caller builds the directory from the
        store and `stream` was given whatever the browser sent.
        """
        target = _resolve(directory)
        if target is None:
            return []
        return [sid for sid, live in self._live.items() if _resolve(live.cwd) == target]

    def adopt(self, session_id: str) -> None:
        """Record a session as this app's.

        The proof command creates a session, closes it, and resumes in the same process;
        without this the app's own session would look foreign to knob 4.
        """
        self._created_here.add(session_id)

    async def stream(self, cwd: str, text: str, session_id: str | None = None):
        """Send one prompt and yield the reply as it arrives.

        Yields ``("chunk", text)`` zero or more times, then exactly one
        ``("done", {...})``. The browser and the proof command both consume this, which
        is what keeps `spec.md`'s "no separate route for tests" true — a test-only path is
        a path nobody runs for real.

        Creates the session when `session_id` is None (R2), resumes it otherwise (R3).
        """
        if not self.membership(cwd):
            raise Refused(f"not a configured workspace: {cwd}")
        if session_id is not None and not self.config.may_resume(self.created_here(session_id)):
            # spec.md C1. The transcript is visible in the listing, but writing to it
            # would put a second process on a record another one may still hold open.
            raise Refused(
                f"session {session_id} was not created by this app; "
                "resuming it is off until spec.md open question 3 is tested"
            )

        async with self._lock:
            live = self._live.get(session_id) if session_id else None
            if live is None:
                client = ClaudeSDKClient(options=_options(self.config, cwd, session_id))
                await client.connect()
                live = Live(client=client, session_id=session_id or "", cwd=cwd)

        resolved = live.session_id
        collected: list[str] = []
        await live.client.query(text)
        async for message in live.client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        collected.append(block.text)
                        yield ("chunk", block.text)
                if message.session_id:
                    resolved = message.session_id
            elif isinstance(message, sdk.ResultMessage):
                resolved = message.session_id or resolved

        if session_id and resolved != session_id:
            # Never observed, but the failure C7 describes is silent, so it is checked
            # rather than assumed.
            await live.client.disconnect()
            self._live.pop(session_id, None)
            raise Refused(
                f"resume returned {resolved} instead of {session_id} — "
                "this is the fork branch spec.md C7 warns about"
            )

        live.session_id = resolved
        self._live[resolved] = live
        self._created_here.add(resolved)
        yield ("done", {"session_id": resolved, "text": "".join(collected), "cwd": cwd})

    async def send(self, cwd: str, text: str, session_id: str | None = None) -> dict[str, Any]:
        """`stream` collected into one result, for callers that do not want the pieces."""
        result: dict[str, Any] = {}
        async for kind, payload in self.stream(cwd, text, session_id):
            if kind == "done":
                result = payload
        return result

    async def close(self, session_id: str) -> None:
        live = self._live.pop(session_id, None)
        if live is not None:
            await live.client.disconnect()

    async def close_all(self) -> None:
        """Every client is a CLI process. The plan lists leaking them as a risk, so
        shutdown is explicit rather than left to the garbage collector.
        """
        for session_id in list(self._live):
            await self.close(session_id)
