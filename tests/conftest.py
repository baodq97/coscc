"""What every test runs inside.

- A data root of its own, never the machine's `~/.cos`: a test that builds `Config()` or `Data()`
  with no root would otherwise reach the real one, and behave differently wherever `TMPDIR`
  lies (inside an app step it lies under `~/.cos`).
- The loop answered in this process (`tests/inprocess.py`), unless the test replaced the
  command it starts or is marked `real_loop` (it kills or waits on the child itself).
- A new `cos.db` is a copy of one this worker created, not the schema run again, unless the
  test is marked `real_schema`; and no database or `git` in a test waits for the disk (fsync).
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import pytest

from coscc import config
from coscc.agent import pack
from coscc.agent.harness import child_env
from coscc.loop import run
from coscc.store import db
from tests.inprocess import in_process

# Every `git` a test starts, its children's included: nothing it writes needs to survive a crash.
for _n, (_key, _value) in enumerate((("core.fsync", "none"), ("gc.auto", "0"))):
    os.environ[f"GIT_CONFIG_KEY_{_n}"], os.environ[f"GIT_CONFIG_VALUE_{_n}"] = _key, _value
os.environ["GIT_CONFIG_COUNT"] = "2"


@pytest.fixture(autouse=True)
def _own_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DEFAULT_DIR", str(tmp_path / "cos"))
    # The owner's layer of the agents' rows lives under the data root an app set; none here.
    monkeypatch.setattr(pack, "ROOT", None)


@pytest.fixture(scope="session")
def _empty_db(tmp_path_factory) -> bytes:
    """A database this worker created through `Data`, as bytes: WAL mode and the schema."""
    made = db.Data(tmp_path_factory.mktemp("empty-db"))
    made.version()
    return made.db_path.read_bytes()


@pytest.fixture(autouse=True)
def _db_without_disk_waits(request, monkeypatch, _empty_db):
    real = db.Data.connect
    copy = not request.node.get_closest_marker("real_schema")

    @contextmanager
    def connect(self, timeout=None):
        target = self.db_path
        if copy and not target.exists() and target.resolve() not in config.protected_databases():
            self.ensure_dir()
            part = target.with_name(f".{target.name}.{uuid.uuid4().hex}")
            part.write_bytes(_empty_db)
            # A link fails on a file that is there: two threads opening at once share one copy.
            try:
                os.link(part, target)
            except FileExistsError:
                pass
            finally:
                part.unlink()
        with real(self, timeout) as conn:
            conn.execute("PRAGMA synchronous=OFF")
            yield conn

    monkeypatch.setattr(db.Data, "connect", connect)


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
