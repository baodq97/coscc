"""The one place configuration is read.

`spec.md` C8 argues for a central config store later and against building it now: four
knobs do not justify a schema. What this module buys is that changing the source later is
one edit here, not a search through the app. Nothing else in `coscc/` may read the
environment.

The defaults are the safe posture from `spec.md` C2, not suggestions. Each is off because
turning it on hands a loopback port a capability it does not need to reach the outcome in
`intent.md`.

**`host` is the one exception, and it stopped being part of that posture on 2026-09-22.**
`0001` set it to `127.0.0.1` deliberately, against Reflex's own `0.0.0.0` default
(`rxconfig.py` records that). `0011` changed it to `0.0.0.0` because the app now ships to
a VM that people reach from elsewhere -- the originator decided it that day, after being
shown what it costs. What it costs is written in `0011`'s `spec.md` C1 and is not softened
here. Until `0070` nothing asked for a password, so every machine that could route to
this port could use all of it, including the two controls that spend real Claude quota.
Since `0070` the master password in `coscc/auth.py` stands in front; what binding every
interface still costs is the wire, and the startup banner in `coscc/run.py` says that out
loud every time the address is not loopback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Tools that write to disk or run commands. Kept as a named set so that knob 2 has teeth:
# without it, a tool list could quietly grant exec while the knob still read "off".
WRITE_AND_EXEC_TOOLS = frozenset(
    {"Bash", "BashOutput", "KillShell", "Edit", "Write", "NotebookEdit"}
)

_ENV_PREFIX = "COS_"

# `.cos/0076_a-step-can-migrate-the-running-apps-database`. The `cos.db` files a child of
# this app must not open, separated by `os.pathsep`. Deliberately not a `COS_*` name: those
# describe this app and are blanked for every child (`coscc/sessions.py` `child_env`),
# while this one is written *for* the child and read by `coscc/data.py` in it.
PROTECTED_DB_VAR = "COSCC_PROTECTED_DB"


def _flag(env: dict[str, str], name: str, default: bool) -> bool:
    raw = env.get(_ENV_PREFIX + name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(env: dict[str, str], name: str) -> tuple[str, ...]:
    raw = env.get(_ENV_PREFIX + name, "")
    return tuple(p for p in (part.strip() for part in raw.split(",")) if p)


@dataclass(frozen=True)
class Config:
    """The app's entire local state: workspaces plus the four knobs of `spec.md` C2."""

    # Knob 1. Empty means chat only — no tools at all, not even read.
    tools: tuple[str, ...] = ()
    # Knob 2. While off, no write or exec tool survives `effective_tools`.
    allow_write_and_exec: bool = False
    # Knob 3. Off, and not settable over HTTP — see `from_env`.
    bypass_permissions: bool = False
    # Knob 4. Off because `spec.md` open question 3 is untested, not because it is
    # dangerous. Testing it is what turns this on.
    resume_foreign_sessions: bool = False

    workspaces: tuple[str, ...] = field(default_factory=tuple)
    # The one root under which workspaces may be created, read here and nowhere
    # else. `spec.md` R11: no route, event handler or component can set it, because the
    # only way in is `from_env`, and a request has no path to that function. Unset means
    # the store is off and the app behaves as it did before there was one.
    working_dir: str | None = None
    # Where the app keeps its *own* state -- the SQLite database and the object
    # folder. Unset means `~/.cos` (`coscc/data.py`). It is a separate setting from
    # `working_dir` on purpose: `spec.md` R4 keeps workspaces out of it, so backing one up
    # is not backing up the other, and `spec.md` C1 says that out loud because it is the
    # kind of thing that loses somebody a directory.
    data_dir: str | None = None
    # See the module docstring. `0.0.0.0` since 0011; it was `127.0.0.1` before.
    host: str = "0.0.0.0"
    port: int = 8790
    model: str | None = None

    # `.cos/0068_updating-the-app-is-a-manual-reinstall`. `COS_UPDATE_CHECK=0` stops the
    # release channel asking GitHub and downloading (R3); the local button still works.
    update_check: bool = True
    # The workspace whose `origin/main` the *Build local* button builds (R6). Unset means
    # no button. Set in the env file, never by a request.
    update_local_from: str | None = None
    # `.cos/0090_agents-relearn-what-earlier-units-already-knew` R1. On, `spec`, `spike`
    # and `plan` carry what earlier units measured (`coscc/knowledge.py`). Off, every
    # prompt and every `start` record is what it was before (R2). Only the env file sets it.
    knowledge: bool = False
    # The next five are read without the `COS_` prefix, because they are not this app's
    # settings: they are what systemd and a login shell hand every process. `invocation_id`
    # is systemd's `INVOCATION_ID`, the second of R2's six conditions.
    invocation_id: str | None = None
    # `${XDG_CONFIG_HOME:-$HOME/.config}`, where `install.sh` writes the unit file.
    config_home: str = ""
    # Where `uv` may be, in the order `scripts/install.sh:130-138` looks: every `PATH`
    # entry, then `UV_INSTALL_DIR`, `XDG_BIN_HOME`, `~/.local/bin`, `~/.cargo/bin`. A
    # service's `PATH` rarely has `~/.local/bin`.
    uv_candidates: tuple[str, ...] = ()
    # The environment the updater's own subprocesses get, built from these rather than
    # inherited, so a trial run of the new version does not see `INVOCATION_ID` or a
    # `COS_WORKING_DIR` (R12 step 2).
    path_env: str = ""
    home: str = ""

    def effective_tools(self) -> list[str]:
        """The tool list a session is actually created with.

        Knob 2 filters here rather than at the call site so that every path to a session
        goes through the same subtraction.
        """
        if self.allow_write_and_exec:
            return list(self.tools)
        return [t for t in self.tools if t not in WRITE_AND_EXEC_TOOLS]

    def permission_mode(self) -> str:
        """`bypassPermissions` only when knob 3 is on.

        `default` is the right partner for chat only: with no tools there is nothing to
        prompt about, so the mode never comes up — and if a later profile adds tools, it
        prompts rather than silently proceeding.
        """
        return "bypassPermissions" if self.bypass_permissions else "default"

    def may_resume(self, session_created_here: bool) -> bool:
        """`spec.md` C1: the app resumes only what it created, until knob 4 is turned on."""
        return session_created_here or self.resume_foreign_sessions

    def is_workspace(self, directory: str) -> bool:
        """Membership by resolved path, so `.`, `..` and symlinks cannot smuggle a path in."""
        try:
            target = Path(directory).expanduser().resolve()
        except OSError:
            return False
        for w in self.workspaces:
            try:
                if Path(w).expanduser().resolve() == target:
                    return True
            except OSError:
                continue
        return False


def _dir(env: dict[str, str], name: str) -> str | None:
    raw = (env.get(_ENV_PREFIX + name) or "").strip()
    return raw or None


def protected_databases(env: dict[str, str] | None = None) -> tuple[Path, ...]:
    """The databases `PROTECTED_DB_VAR` names, each resolved. Unset or empty is none.

    `0076` R5: `coscc/data.py` refuses to open any of these. Read on every call, not once,
    so a test can set the variable around one `Data` and not the next.
    """
    e = os.environ if env is None else env
    raw = e.get(PROTECTED_DB_VAR) or ""
    return tuple(
        Path(part).expanduser().resolve() for part in raw.split(os.pathsep) if part.strip()
    )


def protect(db_path: str | os.PathLike[str], env: dict[str, str] | None = None) -> str:
    """The value of `PROTECTED_DB_VAR` for a child that must not open `db_path`.

    `0076` R4: appended, never overwritten. An app running inside a step already carries
    the outer app's database here, and a child of the inner one must not lose it.
    """
    e = os.environ if env is None else env
    kept = [part for part in (e.get(PROTECTED_DB_VAR) or "").split(os.pathsep) if part.strip()]
    mine = Path(db_path).expanduser().resolve()
    if mine not in protected_databases(e):
        kept.append(str(mine))
    return os.pathsep.join(kept)


def from_env(env: dict[str, str] | None = None) -> Config:
    """Build the config. This module is the only reader of the environment in this app.

    Knob 3 is reachable from here and from nowhere else, which is the whole mechanism
    behind "not settable over HTTP" (`spec.md` C2): a request has no path to this
    function. There is deliberately no setter.

    `data_dir` is here on the same terms and for the same reason. A request that could
    move the data directory could point the app at a database somebody else wrote.
    """
    e = dict(os.environ if env is None else env)
    working_dir = _dir(e, "WORKING_DIR")
    declared = _list(e, "WORKSPACES")
    # The cwd fallback predates the store and stays for the sessions that rely on it:
    # with no working folder and
    # nothing declared, the app is about the directory it was started in. But once a
    # working folder exists, an undeclared `COS_WORKSPACES` means *none* — silently adding
    # cwd would put the repo in the list and make a count of "2" read as "3".
    fallback = () if working_dir else (str(Path.cwd()),)
    return Config(
        tools=_list(e, "TOOLS"),
        allow_write_and_exec=_flag(e, "ALLOW_WRITE_AND_EXEC", False),
        bypass_permissions=_flag(e, "BYPASS_PERMISSIONS", False),
        resume_foreign_sessions=_flag(e, "RESUME_FOREIGN_SESSIONS", False),
        workspaces=declared or fallback,
        working_dir=working_dir,
        data_dir=_dir(e, "DATA_DIR"),
        # Empty is unset, for every setting. `coscc/sessions.py` `child_env` cannot remove
        # a `COS_*` name from a session, only override it with "", so a session started
        # from an app launched with `COS_PORT` set reads `COS_PORT=""` -- and `int("")`
        # errored seven tests in a unit's worktree (`0017` review, F1).
        host=(e.get(_ENV_PREFIX + "HOST") or "").strip() or "0.0.0.0",
        port=int((e.get(_ENV_PREFIX + "PORT") or "").strip() or "8790"),
        model=e.get(_ENV_PREFIX + "MODEL") or None,
        update_check=_flag(e, "UPDATE_CHECK", True),
        update_local_from=_dir(e, "UPDATE_LOCAL_FROM"),
        knowledge=_flag(e, "KNOWLEDGE", False),
        invocation_id=(e.get("INVOCATION_ID") or "").strip() or None,
        config_home=_config_home(e),
        uv_candidates=_uv_candidates(e),
        path_env=e.get("PATH", ""),
        home=e.get("HOME", ""),
    )


def _config_home(e: dict[str, str]) -> str:
    raw = (e.get("XDG_CONFIG_HOME") or "").strip()
    if raw:
        return raw
    home = (e.get("HOME") or "").strip()
    return str(Path(home) / ".config") if home else ""


def _uv_candidates(e: dict[str, str]) -> tuple[str, ...]:
    home = (e.get("HOME") or "").strip()
    dirs = [p for p in (e.get("PATH") or "").split(os.pathsep) if p]
    for name in ("UV_INSTALL_DIR", "XDG_BIN_HOME"):
        raw = (e.get(name) or "").strip()
        if raw:
            dirs.append(raw)
    if home:
        dirs += [str(Path(home) / ".local" / "bin"), str(Path(home) / ".cargo" / "bin")]
    return tuple(dirs)
