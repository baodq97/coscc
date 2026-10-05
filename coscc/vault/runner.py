"""Running one command with secrets in it, and the line the run log gets.

The order is the point: the command the agent wrote is checked first, and a value is put in
only after that. Nothing runs when a single secret is refused. Whatever the command leaves
behind, the directory and the `ssh-agent` are gone when the call returns, and the output has
passed the filter before anyone reads it.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from coscc.agent.harness import child_env
from coscc.agent.policy import Grant, check_command, programs_of
from coscc.store.db import Busy
from coscc.store.journal import BadRecord, Journal
from coscc.vault.filters import mask
from coscc.vault.rules import policy, sentence
from coscc.vault.store import NAME, BadSecret, Store

log = logging.getLogger(__name__)

PLACEHOLDER = re.compile(r"\{\{secret:((?:global|ws):[a-z0-9][a-z0-9._-]{0,63})\}\}")
VAR = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Chosen, not measured.
MAX_TIMEOUT = 600
CUT = 30_000
CAPTURE_MAX = 64 * 1024
SSH_ADD_TIMEOUT = 10
AGENT_WAIT = 5.0


@dataclass(frozen=True)
class Use:
    name: str
    mode: str
    # The environment variable that gets the value (`env`) or the path of its file (`file`).
    var: str = ""


@dataclass(frozen=True)
class Result:
    exit_code: int | None
    stdout: str
    stderr: str
    masked: dict[str, int]
    # `(secret name, code, one sentence)` for each secret that could not be used.
    refusals: tuple[tuple[str, str, str], ...]
    # Why the command line itself was refused, else empty.
    refused: str
    # The bytes `capture` stored, else 0.
    captured: int


# The run log's kind for every line of the vault.
KIND = "vault"


def record(
    journal: Journal | None, action: str, name: str, workspace: str, actor: str, **fields: object
) -> None:
    """One run-log line of kind `KIND`. A log that will not take it does not stop the caller."""
    if journal is None:
        return
    line = {"kind": KIND, "action": action, "name": name, "workspace": workspace, "actor": actor}
    try:
        journal.append({**line, **fields})
    except BadRecord, Busy:
        log.warning("the run log did not take the vault line %s for %s", action, name)


def _var(use: Use) -> str:
    """The variable a use is passed in: its own, else one made from the secret's name."""
    return use.var or "SECRET_" + re.sub(r"[^A-Z0-9]", "_", use.name.split(":", 1)[-1].upper())


def _with_placeholders(command: str, uses: Sequence[Use]) -> tuple[Use, ...]:
    """`uses`, and a `placeholder` use for each `{{secret:<name>}}` in the line that has none."""
    have = {u.name for u in uses if u.mode == "placeholder"}
    more = [
        Use(n, "placeholder") for n in dict.fromkeys(PLACEHOLDER.findall(command)) if n not in have
    ]
    return (*uses, *more)


def _refusals(
    store: Store, uses: Sequence[Use], workspace: str, stage: str
) -> tuple[tuple[str, str, str], ...]:
    out = []
    for u in uses:
        code = policy(store.get(u.name, workspace), workspace, stage, u.mode)
        if code:
            out.append((u.name, code, sentence(code, u.name, workspace, stage, u.mode)))
    return tuple(out)


def _substitute(command: str, values: dict[str, bytes]) -> str:
    """`command` with each placeholder replaced by its quoted value."""

    def value(found: re.Match[str]) -> str:
        secret = values.get(found.group(1))
        if secret is None:
            return found.group(0)
        return shlex.quote(secret.decode("utf-8", "surrogateescape"))

    return PLACEHOLDER.sub(value, command)


def _tmpdir() -> Path:
    """A `0700` directory on tmpfs, which `mkdtemp` makes."""
    for base in (os.environ.get("XDG_RUNTIME_DIR", ""), "/dev/shm"):
        if base and os.path.isdir(base) and os.access(base, os.W_OK):
            return Path(tempfile.mkdtemp(prefix="vault-", dir=base))
    raise BadSecret("no tmpfs directory to hold a secret: XDG_RUNTIME_DIR is not set")


def _place(
    uses: Sequence[Use], values: dict[str, bytes], tmp: Path | None, env: dict[str, str]
) -> None:
    """Put each `env` value in the environment and each `file` value in a `0600` file."""
    for i, u in enumerate(uses):
        if u.mode == "env":
            env[_var(u)] = values[u.name].decode("utf-8", "surrogateescape")
        elif u.mode == "file" and tmp is not None:
            path = tmp / f"s{i}"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(values[u.name])
            env[_var(u)] = str(path)


def _load(
    agent: subprocess.Popen[bytes],
    sock: str,
    uses: Sequence[Use],
    values: dict[str, bytes],
    env: dict[str, str],
) -> None:
    """Wait for the agent's socket, then hand it each key through `ssh-add -`, on stdin."""
    deadline = time.monotonic() + AGENT_WAIT
    while not os.path.exists(sock):
        if agent.poll() is not None or time.monotonic() > deadline:
            raise OSError("ssh-agent did not open its socket")
        time.sleep(0.05)
    for u in uses:
        done = subprocess.run(
            ["ssh-add", "-"],
            input=values[u.name],
            env=env,
            capture_output=True,
            timeout=SSH_ADD_TIMEOUT,
        )
        if done.returncode:
            raise OSError(f"ssh-add did not take the key of {u.name}")


def _bash(
    line: str, cwd: str, env: dict[str, str], timeout: int
) -> tuple[int | None, bytes, bytes]:
    proc = subprocess.Popen(
        ["bash", "-c", line],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        try:
            out, err = proc.communicate(timeout=AGENT_WAIT)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
        return None, out, err + f"\n[timed out after {timeout} s]".encode()
    return proc.returncode, out, err


def _execute(
    line: str, uses: Sequence[Use], values: dict[str, bytes], cwd: str, timeout: int
) -> tuple[int | None, bytes, bytes]:
    """`(exit code, stdout, stderr)` of `bash -c line`, killed after `timeout` seconds. The agent and
    the directory are gone when this returns, however it ends."""
    env = child_env()
    tmp: Path | None = None
    agent: subprocess.Popen[bytes] | None = None
    keys = [u for u in uses if u.mode == "ssh"]
    try:
        if keys or any(u.mode == "file" for u in uses):
            tmp = _tmpdir()
        _place(uses, values, tmp, env)
        if keys and tmp is not None:
            sock = str(tmp / "agent.sock")
            agent = subprocess.Popen(
                ["ssh-agent", "-D", "-a", sock],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            env["SSH_AUTH_SOCK"] = sock
            _load(agent, sock, keys, values, env)
        return _bash(line, cwd, env, timeout)
    except (OSError, ValueError, subprocess.SubprocessError, BadSecret) as e:
        return None, b"", f"the command could not be started: {e}".encode()
    finally:
        if agent is not None:
            agent.kill()
            agent.wait()
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def _capture(
    store: Store, journal: Journal | None, name: str, workspace: str, actor: str, stdout: bytes
) -> tuple[bytes, str]:
    """Keep `stdout` as the value of the new secret `name`, and write its `create` line: `(the
    value kept, a note when none)`."""
    value = stdout.removesuffix(b"\n")
    if not value or len(value) > CAPTURE_MAX:
        return b"", f"capture: stdout is empty or over {CAPTURE_MAX} bytes, so nothing was kept"
    try:
        store.create(name, workspace, "captured from a command", actor=actor)
    except BadSecret as e:
        return b"", f"capture failed: {e}"
    try:
        store.put(name, workspace, value)
    except BadSecret as e:
        store.delete(name, workspace)
        return b"", f"capture failed: {e}"
    record(journal, "create", name, workspace, actor, via="capture")
    return value, ""


def _checked_capture(store: Store, name: str, workspace: str) -> None:
    """Refuse a `capture` target that is no new `ws:` name, before anything runs."""
    if not name.startswith("ws:") or not NAME.fullmatch(name):
        raise BadSecret(f"capture keeps a ws:<name> secret, not {name!r}")
    if store.get(name, workspace) is not None:
        raise BadSecret(f"{name} already exists, and capture never overwrites")


def _preflight(
    store: Store,
    grant: Grant,
    command: str,
    uses: Sequence[Use],
    workspace: str,
    stage: str,
) -> tuple[str, tuple[tuple[str, str, str], ...]]:
    """`(why the line is refused, the refused secrets)`; both empty when the call may go on. The
    store's own paths are refused on top of what `grant` holds."""
    protected = tuple(dict.fromkeys((*grant.protected, *store.protected())))
    refused = check_command(replace(grant, protected=protected), command)
    if refused:
        return refused, ()
    refusals = _refusals(store, uses, workspace, stage)
    if refusals:
        return "", refusals
    for u in uses:
        if u.mode in ("env", "file") and not VAR.fullmatch(_var(u)):
            return f"{_var(u)!r} is not an environment variable name", ()
    return "", ()


def run(
    store: Store,
    *,
    command: str,
    uses: Sequence[Use],
    grant: Grant,
    workspace: str,
    stage: str,
    unit: str,
    run: str,
    cwd: str,
    journal: Journal | None,
    timeout: int = 120,
    capture: str = "",
    actor: str = "",
) -> Result:
    """Check `command`, ask the policy about each use, and only then run it with the secrets in.

    The line is checked as written, with the store's own paths refused on top of what `grant`
    holds. Nothing runs, and no value is read, while a check or a secret says no. Blocks until the
    command ends, so a caller in the event loop hands it to a thread. Every call, refused ones
    too, leaves one run-log line with the names, modes, codes and programs and never a value.
    """
    actor = actor or f"agent:{stage}"
    uses = _with_placeholders(command, uses)
    if capture:
        _checked_capture(store, capture, workspace)

    def logged(**fields: object) -> None:
        record(
            journal,
            "use",
            ",".join(u.name for u in uses),
            workspace,
            actor,
            names=[u.name for u in uses],
            unit=unit,
            stage=stage,
            run=run,
            modes=[u.mode for u in uses],
            programs=list(programs_of(command)),
            capture=capture,
            **fields,
        )

    refused, refusals = _preflight(store, grant, command, uses, workspace, stage)
    if refused or refusals:
        logged(refused=refused, codes=[c for _, c, _ in refusals])
        return Result(None, "", "", {}, refusals, refused, 0)
    values = store.values_for(workspace)
    line = _substitute(command, values)
    if line != command and programs_of(line) != programs_of(command):
        refused = "a secret's value would change which programs this line runs"
        logged(refused=refused, codes=[])
        return Result(None, "", "", {}, (), refused, 0)

    code, out, err = _execute(line, uses, values, cwd, min(max(int(timeout), 1), MAX_TIMEOUT))
    kept, note = (b"", "")
    if capture and code == 0:
        kept, note = _capture(store, journal, capture, workspace, actor, out)
    captured = len(kept)
    if kept:
        # Read before the command ran, `values` lacks what it printed; stderr may hold it too.
        values = {**values, capture: kept}
    shown_out, in_out = mask(values, out)
    shown_err, in_err = mask(values, err)
    masked = dict(Counter(in_out) + Counter(in_err))
    logged(exit_code=code, codes=[], masked=masked, captured=captured)
    stdout = "" if capture else shown_out.decode(errors="replace")[:CUT]
    stderr = shown_err.decode(errors="replace")[:CUT] + (f"\n{note}" if note else "")
    return Result(code, stdout, stderr, masked, (), "", captured)
