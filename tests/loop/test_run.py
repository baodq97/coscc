"""`coscc.loop.run`: the app's one way to ask the loop, in the env the app gives a child."""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

from coscc.loop import run
from tests.loop.conftest import UnitStore, entry, env, expect, header

UNIT = "0001_mot-don-vi"
PROSE = "Đơn vị này chờ người trả lời: “câu 1” — xong."


def _vietnamese(store: UnitStore) -> None:
    hold = {"state": "paused", "reason": PROSE, "by": "person", "date": "2026-10-01"}
    store.unit(
        UNIT,
        {"intent.md": header("Một đơn vị tiếng Việt", "accepted") + f"\n{PROSE}\n"},
        entry({"intent.md": "accepted"}, holds=[hold]),
    )


def test_status_answers_the_same_bytes_under_the_app_env_with_vietnamese_prose(store):
    # `child_env` sets `LC_ALL=C`; the snapshot goes in on stdin, the answer comes back
    # byte for byte what the golden holds.
    _vietnamese(store)
    argv = ["status", "--json", "--root", str(store.root), "--state", "-"]
    snapshot = store.state().read_text()
    said = expect(argv, stdin=snapshot)
    assert PROSE in said.out
    assert expect(argv, stdin=snapshot, environ=env(LC_ALL="C")).out == said.out
    got = asyncio.run(run.ask(argv, stdin=snapshot))
    assert run.child_env()["LC_ALL"] == "C"
    assert (got.code, got.out, got.err) == (said.code, said.out, said.err)
    assert run.ask_sync(argv, stdin=snapshot) == got


def test_a_refusal_comes_back_with_its_exit_code(tmp_path):
    got = run.ask_sync(["gate", UNIT, "impl", "--root", str(tmp_path)])
    assert got.code == 2
    assert "needs the coscc app" in got.err


def test_the_cwd_is_where_a_checkout_command_looks(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "9.9.9"\n')
    got = asyncio.run(run.ask(["check-version"], cwd=tmp_path))
    assert got.code == 1
    assert "pyproject.toml says 9.9.9" in got.err


def test_a_coscc_in_the_cwd_is_never_what_runs(tmp_path):
    # A workspace is a cloned repository: its own `coscc/loop/` must not shadow this app's.
    fake = tmp_path / "coscc" / "loop"
    fake.mkdir(parents=True)
    for d in (tmp_path / "coscc", fake):
        (d / "__init__.py").write_text("")
    (fake / "__main__.py").write_text("print('the workspace ran')\n")
    got = run.ask_sync(["check-tag", "v1.2.3"], cwd=tmp_path)
    assert (got.code, got.out) == (0, "release\n")
    assert asyncio.run(run.ask(["check-tag", "v1.2.3"], cwd=tmp_path)) == got


def _slow(monkeypatch, tmp_path):
    """`argv` swapped for a child that starts a grandchild and hangs; the grandchild's pid file."""
    pid = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(run, "argv", lambda args: [sys.executable, "-c", code])
    return pid


def _gone(pid_file) -> bool:
    import os

    pid = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        # A zombie still answers `kill 0`; its state says it is dead.
        try:
            with open(f"/proc/{pid}/stat") as f:
                if f.read().split()[2] == "Z":
                    return True
        except OSError:
            return True
        time.sleep(0.1)
    return False


def _started(pid_file) -> None:
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text():
            return
        time.sleep(0.05)


def test_a_hung_child_and_what_it_started_are_killed_past_the_timeout_sync(monkeypatch, tmp_path):
    pid = _slow(monkeypatch, tmp_path)
    with pytest.raises(TimeoutError):
        run.ask_sync(["status"], timeout=2)
    _started(pid)
    assert _gone(pid)


def test_a_hung_child_and_what_it_started_are_killed_past_the_timeout_async(monkeypatch, tmp_path):
    pid = _slow(monkeypatch, tmp_path)
    with pytest.raises(TimeoutError):
        asyncio.run(run.ask(["status"], timeout=2))
    _started(pid)
    assert _gone(pid)
