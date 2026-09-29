"""One fetch of `origin/main` per repository at a time, and the result shared.

Two steps fetching at once race on `refs/remotes/origin/main` in the shared git dir
(`incorrect old value provided`). Every trunk fetch goes through here, keyed by git dir,
remote and branch:

- a fetch already running for the key is joined;
- a fetch that succeeded and **started** under `REUSE_SECONDS` ago is reused;
- otherwise this call fetches, and a ref-lock race is retried once after `RETRY_DELAY`.

The table is per process: a second app or a terminal `git fetch` bypasses it, and only the
retry covers those.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from coscc.git import gitops
from coscc.git.gitops import GitError

# Seconds. A fetch under this age is reused and still counts as fresh; callers use the same
# figure for `fresh`.
REUSE_SECONDS = 30.0

# Seconds before the one retry.
RETRY_DELAY = 1.0

# What git says when another writer held the ref. `cannot lock ref` is inferred, not seen; a
# wrong guess costs one extra attempt.
RACE_MARKERS = ("incorrect old value provided", "cannot lock ref")

Run = Callable[[Path, str, str], Awaitable[str]]


class FetchFailed(GitError):
    """A coordinated fetch that did not succeed. Still a `GitError`; `attempts` counts `git fetch` runs."""

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

        `outcome` is `fetched`, `joined` or `reused`; `age` is seconds since the attempt whose
        result is used began. Raises `FetchFailed` instead of returning `failed`. Callers still
        read the ref themselves afterwards.
        """
        try:
            where = await gitops.common_dir(Path(path))
        except GitError as e:
            raise FetchFailed(str(e), 0) from e

        # No `await` from here until the future is registered: two calls must not both find the
        # table empty.
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
            return {"outcome": "reused", "attempts": 0, "age": _tenths(now - entry.last_ok_started)}
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
            # Cancelled, or not git's error: joiners must not wait forever on a future nobody settles.
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
        return _tenths(self.clock() - started)

    @staticmethod
    def _settle(future: asyncio.Future, error: FetchFailed) -> None:
        future.set_exception(error)
        # Marks it retrieved, so asyncio prints nothing when no call joined.
        future.exception()


def _tenths(seconds: float) -> float:
    """`seconds` rounded **down** to 0.1, so a reuse at 29.97s still reads under `REUSE_SECONDS`.
    The inner `round` only absorbs float noise.
    """
    return math.floor(round(seconds, 6) * 10) / 10


# One per process: every caller shares it.
shared = Fetches()


async def fetch(path: Path, remote: str = "origin", branch: str = gitops.TRUNK) -> dict[str, Any]:
    """`shared.fetch`, looked up at call time so a test can put another `Fetches` there."""
    return await shared.fetch(path, remote, branch)
