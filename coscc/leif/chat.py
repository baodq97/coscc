"""The agent sessions of a workspace, and sending one a message."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, AsyncIterator, TypedDict

from coscc.agent import models
from coscc.agent import sessions as reader
from coscc.agent import transcript
from coscc.agent.sessions import Sessions
from coscc.config import Config
from coscc.kernel import Invalid
from coscc.store.db import Busy
from coscc.store.journal import BadRecord
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
        model_for: Callable[[str], tuple[str | None, str]],
    ) -> None:
        self.config = config
        self.ws = ws
        self.sessions = sessions
        self.refuse_updating = refuse_updating
        self.model_for = model_for

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
        """Yield `(kind, payload)` exactly as the session layer does.

        Re-runs the pure `check_send` so no caller can skip it. `resume` is a `suspend` row
        of a chat turn an update paused: the turn goes on from its safe point, on its
        model, with what is left of its ceiling, and opens nothing when none is.
        """
        self.check_send(cwd, text)
        # Chat is a row of the same table as the stages.
        model, model_source = self.model_for(models.CHAT)
        extra: dict[str, Any] = {
            "owner": {
                "kind": "chat",
                "workspace": self.ws.key(cwd),
                "workspace_dir": cwd,
                "unit": "",
                "stage": "",
            }
        }
        if resume is not None:
            model, model_source = resume.get("model") or model, "resumed"
            turns, _, used_up = transcript.ceilings_left(CHAT_TURNS, None, resume)
            if used_up:
                return
            extra.update(resume_at=resume.get("safe_uuid"), max_turns=turns)
        async for item in self.sessions.stream(
            cwd,
            text,
            session_id,
            **({"model": model} if model is not None else {}),
            **extra,
        ):
            if item[0] == "session":
                # `api.py` treats every kind but `chunk` as the terminal `done` row;
                # forwarding this would end the reply early.
                continue
            if item[0] == "done":
                # One record per turn: the model a *new* client is created with. A client
                # already live keeps the model it was made with.
                journal = self.ws.journal()
                if journal is not None:
                    try:
                        journal.append(
                            {
                                "kind": "chat",
                                "workspace": self.ws.key(cwd),
                                "unit": "",
                                "stage": "",
                                "model": model,
                                "model_source": model_source,
                                "session_id": (item[1] or {}).get("session_id", ""),
                            }
                        )
                    except BadRecord, Busy:
                        pass  # a busy log must not cost the reply that was already paid for
            yield item
