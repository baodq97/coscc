"""The agent sessions of a workspace, and sending one a message.

Split from `coscc/service.py` (`0095`), whose `Service` inherits it; a mixin with no fields.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from coscc import sessions as reader
from coscc.journal import BadRecord, Busy
from coscc import models
from coscc.service_common import Invalid


class SessionsMixin:

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
