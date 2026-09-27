"""Following the notices (`0113`): the run log's records a listener is told of, as they land.

A mixin with no fields, like the others `Service` inherits. It reads and writes nothing but
the lines it hands out (R13); `coscc/notices.py` decides what each record says.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from coscc import notices
from coscc.journal import BELL, Busy
from coscc.service_common import Invalid


class NoticesMixin:

    # -- notices (`0113` R1, R6–R8) -------------------------------------------
    #
    # One stream for every listener: the page's script, a terminal, an agent's session. It
    # holds a connection per listener for `notices.LIFETIME_SECONDS` at most, then ends, and
    # the listener comes back through the login door with `after`; one whose peer vanished
    # without closing (spec C6) holds it until then or until a `beat` fails to write.

    def notice_scope(self, workspace: str) -> str | None:
        """The journal key to narrow to, `None` for every workspace. A workspace the app does
        not have is refused, as is a stream with no run log to read."""
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
        """R6, R8. With no `after`, or one past every row, a `head` line first and nothing at
        or below it; with one, every notice past it first. Then each notice as it lands, in
        `id` order and none twice, and a `beat` after `beat` seconds without a line. Ends
        `lifetime` seconds in (`notices.LIFETIME_SECONDS`), once what has landed is sent.

        A record this process appends rings `BELL` and is read at once (R7, 5 s); one another
        process appends is read at the next wake, at most `beat` seconds on (R7, 20 s). The
        ticket is armed before each read, so a ring during the read is not missed."""
        journal = self._journal()
        if journal is None:
            raise Invalid("there is no working folder, so there is no run log to follow")
        loop = asyncio.get_running_loop()
        ends = loop.time() + (notices.LIFETIME_SECONDS if lifetime is None else lifetime)
        head = await asyncio.to_thread(journal.last_id)
        if after is None or after > head:
            # An `after` past every row is a cursor from a run log since deleted or replaced,
            # whose ids start again at 1: kept, it would hide every notice until the new log
            # passed it (review round 1, F2). The `head` line sets the listener's cursor.
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
