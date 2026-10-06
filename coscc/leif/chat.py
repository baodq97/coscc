"""The agent sessions of a workspace, and sending one a message."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, AsyncIterator, TypedDict

from coscc.agent import models
from coscc.agent import sessions as reader
from coscc.agent.policy import Grant
from coscc.agent.sessions import Sessions
from coscc.config import Config
from coscc.kernel import Invalid, Run
from coscc.runner import run as run_mod
from coscc.units.workspaces import Workspaces

# A chat turn's ceiling: `Sessions.stream`'s default, since chat names none, and no budget.
CHAT_TURNS = 1


class ChatSession(TypedDict):
    """One Claude session started in the workspace's folder: by the app's chat, or in a
    terminal (`resumable` false: read only, unless the app may resume foreign sessions)."""

    session_id: str
    summary: str
    last_modified: int
    created_at: int | None
    git_branch: str | None
    resumable: bool


class ChatSessions(TypedDict):
    cwd: str
    sessions: list[ChatSession]


class ChatMessage(TypedDict):
    role: str
    text: str
    uuid: str


class ChatHistory(TypedDict):
    session_id: str
    messages: list[ChatMessage]


class Chat:
    def __init__(
        self,
        config: Config,
        ws: Workspaces,
        sessions: Sessions,
        refuse_updating: Callable[[], None],
        agent_for: Callable[[str, Grant], run_mod.Agent],
    ) -> None:
        self.config = config
        self.ws = ws
        self.sessions = sessions
        self.refuse_updating = refuse_updating
        self.agent_for = agent_for

    # -- sessions -----------------------------------------------------------

    def sessions_for(self, cwd: str, limit: int | None = None) -> dict[str, Any]:
        self.ws.check(cwd)
        rows = reader.list_for_directory(cwd, limit=limit)
        for row in rows:
            # Terminal sessions show up here too; this flag says which may be written to.
            row["resumable"] = self.config.may_resume(self.sessions.created_here(row["session_id"]))
        return {"cwd": cwd, "sessions": rows}

    def history(self, cwd: str, session_id: str) -> dict[str, Any]:
        self.ws.check(cwd)
        if not session_id:
            raise Invalid("session_id is required")
        return {
            "session_id": session_id,
            "messages": reader.history(session_id, cwd),
        }

    def check_send(self, cwd: str, text: str) -> None:
        """Everything a caller can reject with a status code, decided before any output.

        Separate from `stream` because a generator's first item is pulled only after the
        caller has committed to streaming, when the status line is gone.
        """
        self.ws.check(cwd)
        self.refuse_updating()
        if not text.strip():
            raise Invalid("text is required")

    async def stream(
        self,
        cwd: str,
        text: str,
        session_id: str | None = None,
        resume: dict[str, Any] | None = None,
    ) -> AsyncIterator[tuple[str, Any]]:
        """`chunk` and `tool` as the reply arrives, then one `done` with the `session_id`: one
        run of the `chat` agent (`run_mod.run`), with its `start` and `end` like any agent's.

        Re-runs the pure `check_send` so no caller can skip it. `resume` is a `suspend` row
        of a chat turn an update paused: the turn goes on from its safe point, on its
        model, with what is left of its ceiling, and opens nothing when none is. A turn refused
        or failed is `Invalid` once its `end` is written.
        """
        self.check_send(cwd, text)
        agent = self.agent_for(
            models.CHAT, Grant(tools=tuple(self.config.effective_tools()), max_turns=CHAT_TURNS)
        )
        if resume is not None and resume.get("model"):
            agent = replace(
                agent,
                model=str(resume["model"]),
                sources={**agent.sources, "model_source": "resumed"},
            )
        got: Run | None = None
        async for kind, payload in run_mod.run(
            agent,
            run_mod.Input(
                cwd,
                text,
                self.ws.key(cwd),
                session_id=session_id,
                keep=True,
                resume=resume,
            ),
            ctx=run_mod.Ctx(self.sessions, self.ws.journal(), self.config.data_dir),
        ):
            if kind == "done":
                got = payload
            else:
                yield (kind, payload)
        if got is None:
            return
        if got.status in ("refused", "failed"):
            raise Invalid(got.detail)
        yield (
            "done",
            {"session_id": got.session, "run": got.run, "status": got.status, "cost": got.cost},
        )
