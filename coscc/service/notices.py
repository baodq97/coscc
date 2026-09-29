"""Following the notices: the run log's records a listener is told of, as they land.

A mixin with no fields; it reads and writes nothing but the lines it hands out.
`coscc/runlog/notices.py` decides what each record says.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from coscc.runlog import notices
from coscc.runlog.journal import BELL, Busy
from coscc.service.common import Invalid


class NoticesMixin:

# -- notices -------------------------------------------------------------
#
# One stream for every listener. A connection lasts `notices.LIFETIME_SECONDS` at most, then
# ends and the listener comes back with `after`; a peer that vanished holds it until then or
# until a `beat` fails to write.

    def notice_scope(self, workspace: str) -> str | None:
        """The journal key to narrow to, `None` for every workspace; an unknown workspace or a stream with no run log is refused."""
        if self._journal() is None:
            raise Invalid("there is no working folder, so there is no run log to follow")
        if not workspace:
            return None
        self._workspace_or_refuse(workspace)
        return self._journal_key(workspace)

    async def follow_notices(
        self, scope: str | None, after: int | None, beat: float = notices.BEAT_SECONDS,
        lifetime: float | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """With no `after`, or one past every row, a `head` line first; with one, every notice
        past it first. Then each notice as it lands, in `id` order and none twice, and a `beat`
        after `beat` seconds without a line. Ends `lifetime` seconds in.

        A record this process appends rings `BELL` and is read at once; one another process
        appends is read at the next wake. The ticket is armed before each read, so a ring
        during the read is not missed."""
        journal = self._journal()
        if journal is None:
            raise Invalid("there is no working folder, so there is no run log to follow")
        loop = asyncio.get_running_loop()
        ends = loop.time() + (notices.LIFETIME_SECONDS if lifetime is None else lifetime)
        head = await asyncio.to_thread(journal.last_id)
        if after is None or after > head:
# An `after` past every row is a cursor from a run log since replaced, whose ids start
# again at 1: kept, it would hide every notice. The `head` line sets the listener's cursor.
            last = head
            yield {"type": "head", "id": head}
        else:
            last = after
        said = loop.time()
        while True:
            ticket = BELL.arm()
            try:
                while True:
                    try:
                        rows = await asyncio.to_thread(
                            journal.notice_rows, last, notices.SOURCE_KINDS, scope, notices.PAGE,
                        )
                    except Busy:
                        # Read again at the next wake; nothing is skipped past.
                        rows = []
                    for rid, record in rows:
                        last = rid
                        found = notices.notice_of(rid, record)
                        if found is not None:
                            yield found
                            said = loop.time()
                    if len(rows) < notices.PAGE:
                        break
                if loop.time() >= ends:
                    return
                left = said + beat - loop.time()
                if left <= 0:
                    try:
                        head = await asyncio.to_thread(journal.last_id)
                    except Busy:
                        head = last
                    yield {"type": "beat", "id": head}
                    said = loop.time()
                    left = beat
                await BELL.wait(ticket, min(left, ends - loop.time()))
            finally:
                BELL.disarm(ticket)
