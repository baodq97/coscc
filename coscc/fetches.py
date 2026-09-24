"""One fetch of `origin/main` per repository at a time, and the result shared.

`0048`. Two steps started together on one workspace each ran `git fetch`, both wrote
`refs/remotes/origin/main` in the one git dir a clone and its worktrees share, and one
lost: `error: fetching ref refs/remotes/origin/main failed: incorrect old value provided`
(observed 2026-09-24, `intent.md ## Problem`). The step went on with `base.sha` empty and
`fresh` false, and nothing said why.

So every fetch the app makes of the trunk goes through here (R1). Keyed by the shared git
dir, the remote and the branch:

- a fetch already running for the key is joined, not started again (R2);
- a fetch that succeeded and **started** under `REUSE_SECONDS` ago is reused (R3);
- otherwise this call fetches, and a ref-lock race is retried once after `RETRY_DELAY` (R4).

`gitops.fetch` itself is unchanged: this decides whether to call it, never what it runs
(R8). The table lives in this process's memory only — a second copy of the app, or a
`git fetch` from a terminal or a step with `Bash`, does not pass through it, and only the
retry covers those (`spec.md ## Out of scope`).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from coscc import gitops
from coscc.gitops import GitError

# Seconds. `intent.md ## Answers, câu 1`: a fetch under 30s old is reused and still counts
# as fresh. Chosen there, not measured. Callers use the same figure for `fresh` (R7).
REUSE_SECONDS = 30.0

# Seconds before the one retry. `spec.md ## Answers, câu 1`: "Chờ 1 giây rồi thử lại một
# lần." Chosen, not measured.
RETRY_DELAY = 1.0

# What git says when another writer held the ref. Only the first was observed here
# (`intent.md ## Problem`); `cannot lock ref` is inferred, not seen (`spec.md` C3) — a
# wrong guess costs one extra attempt on some other error.
RACE_MARKERS = ("incorrect old value provided", "cannot lock ref")

Run = Callable[[Path, str, str], Awaitable[str]]


class FetchFailed(GitError):
    """A coordinated fetch that did not succeed. Still a `GitError`, so every existing
    `except GitError` keeps catching it; `attempts` is how many `git fetch` it ran."""

    def __init__(self, message: str, attempts: int):
        super().__init__(message)
        self.outcome = "failed"
        self.attempts = attempts


def is_race(error: BaseException) -> bool:
    text = str(error)
    return any(marker in text for marker in RACE_MARKERS)


@dataclass
class _Entry:
    inflight: asyncio.Future | None = None
    last_ok_started: float | None = None


class Fetches:
    """The coordinator. `run`, `clock` and `sleep` are replaceable for tests only."""

    def __init__(
        self,
        run: Run | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
    ):
        self.run = run or gitops.fetch
        self.clock = clock or time.monotonic
        self.sleep = sleep or asyncio.sleep
        self._table: dict[tuple[str, str, str], _Entry] = {}

    async def fetch(
        self, path: Path, remote: str = "origin", branch: str = gitops.TRUNK
    ) -> dict[str, Any]:
        """`{outcome, attempts, age}` — how `refs/remotes/<remote>/<branch>` got current.

        `outcome` is `fetched`, `joined` or `reused`; `age` is seconds since the attempt
        whose result is used began. Raises `FetchFailed` instead of returning `failed`.
        The caller still reads the ref itself afterwards: another fetch may since have
        moved it, and then what it reads is only newer.
        """
        try:
            where = await gitops.common_dir(Path(path))
        except GitError as e:
            raise FetchFailed(str(e), 0) from e

        # From here until the future is registered there is no `await`: two calls must not
        # both find the table empty (`spec.md ## Design`).
        entry = self._table.setdefault((str(where), remote, branch), _Entry())
        loop = asyncio.get_running_loop()
        joined = entry.inflight
        if joined is not None and not joined.done() and joined.get_loop() is loop:
            try:
                started, attempts = await asyncio.shield(joined)
            except FetchFailed as e:
                raise FetchFailed(str(e), e.attempts) from e
            return {"outcome": "joined", "attempts": attempts, "age": self._age(started)}
        now = self.clock()
        if entry.last_ok_started is not None and now - entry.last_ok_started < REUSE_SECONDS:
            return {"outcome": "reused", "attempts": 0, "age": round(now - entry.last_ok_started, 1)}
        future: asyncio.Future = loop.create_future()
        entry.inflight = future

        attempts = 0
        try:
            while True:
                attempts += 1
                started = self.clock()
                try:
                    await self.run(Path(path), remote, branch)
                    break
                except GitError as e:
                    if attempts == 1 and is_race(e):
                        await self.sleep(RETRY_DELAY)
                        continue
                    message = str(e) if attempts == 1 else (
                        f"git fetch lost a ref-lock race, was retried once after "
                        f"{RETRY_DELAY:.0f}s, and failed again. git said: {e}"
                    )
                    failed = FetchFailed(message, attempts)
                    self._settle(future, failed)
                    raise failed from e
            entry.last_ok_started = started
            future.set_result((started, attempts))
            return {"outcome": "fetched", "attempts": attempts, "age": self._age(started)}
        except BaseException as e:
            # Cancelled, or something that is not git's error: whoever joined must not
            # wait forever on a future nobody will settle.
            if not future.done():
                reason = (
                    "the fetch this call joined was cancelled"
                    if isinstance(e, asyncio.CancelledError) else
                    f"the fetch this call joined did not finish: {e}"
                )
                self._settle(future, FetchFailed(reason, attempts))
            raise
        finally:
            if entry.inflight is future:
                entry.inflight = None

    def _age(self, started: float) -> float:
        return round(self.clock() - started, 1)

    @staticmethod
    def _settle(future: asyncio.Future, error: FetchFailed) -> None:
        future.set_exception(error)
        # Marks it retrieved, so asyncio prints nothing when no call joined.
        future.exception()


# One per process: the point is that every caller shares it.
shared = Fetches()


async def fetch(path: Path, remote: str = "origin", branch: str = gitops.TRUNK) -> dict[str, Any]:
    """`shared.fetch`, looked up at call time so a test can put another `Fetches` there."""
    return await shared.fetch(path, remote, branch)
