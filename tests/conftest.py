"""What every test runs inside.

- A data root of its own, never the machine's `~/.cos`: a test that builds `Config()` or `Data()`
  with no root would otherwise reach the real one, and behave differently wherever `TMPDIR`
  lies (inside an app step it lies under `~/.cos`).
- The loop answered in this process (`tests/inprocess.py`), unless the test replaced the
  command it starts or is marked `real_loop` (it kills or waits on the child itself).
"""

from __future__ import annotations

import pytest

from coscc.agent import pack
from coscc.agent.harness import child_env
from coscc.loop import run
from coscc.store import db
from tests.inprocess import in_process


@pytest.fixture(autouse=True)
def _own_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DEFAULT_DIR", str(tmp_path / "cos"))
    # The owner's layer of the agents' rows lives under the data root an app set; none here.
    monkeypatch.setattr(pack, "ROOT", None)


@pytest.fixture(autouse=True)
def _loop_in_process(request, monkeypatch):
    if request.node.get_closest_marker("real_loop"):
        return
    real_ask, real_sync = run.ask, run.ask_sync

    def ours(args) -> bool:
        return run.argv(args)[1:4] == ["-P", "-m", "coscc.loop"]

    def answer(args, stdin, cwd) -> run.Answer:
        data = None if stdin is None else stdin.encode()
        return run.Answer(*in_process(args, data, cwd, child_env()))

    async def ask(args, *, stdin=None, cwd=None, timeout=run.TIMEOUT):
        if not ours(args):
            return await real_ask(args, stdin=stdin, cwd=cwd, timeout=timeout)
        return answer(args, stdin, cwd)

    def ask_sync(args, *, stdin=None, cwd=None, timeout=run.TIMEOUT):
        if not ours(args):
            return real_sync(args, stdin=stdin, cwd=cwd, timeout=timeout)
        return answer(args, stdin, cwd)

    monkeypatch.setattr(run, "ask", ask)
    monkeypatch.setattr(run, "ask_sync", ask_sync)
