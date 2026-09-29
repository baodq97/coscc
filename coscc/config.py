"""The one place configuration is read; nothing else in `coscc/` may read the environment.

Defaults are the safe posture: each capability is off. `host` is the exception (`0.0.0.0`):
the master password in `coscc/auth.py` stands in front, and `coscc/run.py` warns at
startup whenever the address is not loopback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Tools that write to disk or run commands: knob 2 filters on this set.
WRITE_AND_EXEC_TOOLS = frozenset(
    {"Bash", "BashOutput", "KillShell", "Edit", "Write", "NotebookEdit"}
)

_ENV_PREFIX = "COS_"

# The `cos.db` files a child of this app must not open, separated by `os.pathsep`. Not a
# `COS_*` name: those are blanked for every child (`child_env`), and this one is written for it.
PROTECTED_DB_VAR = "COSCC_PROTECTED_DB"

# Addresses that reach this machine and nowhere else. `0.0.0.0` is absent on purpose.
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


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
    """The app's entire local state: workspaces plus four knobs."""

    # Knob 1. Empty means chat only — no tools at all, not even read.
    tools: tuple[str, ...] = ()
    # Knob 2. While off, no write or exec tool survives `effective_tools`.
    allow_write_and_exec: bool = False
    # Knob 3. Off, and not settable over HTTP — see `from_env`.
    bypass_permissions: bool = False
    # Knob 4. Off because resuming a foreign session is untested.
    resume_foreign_sessions: bool = False

    workspaces: tuple[str, ...] = field(default_factory=tuple)
    # The one root under which workspaces may be created. Only `from_env` sets it; a request
    # has no path to it. Unset means the store is off.
    working_dir: str | None = None
    # Where the app keeps its own state (SQLite database, object folder); unset means
    # `~/.cos`. Kept apart from `working_dir`, so backing up one is not backing up the other.
    data_dir: str | None = None
    host: str = "0.0.0.0"
    port: int = 8790
    model: str | None = None

    # `COS_UPDATE_CHECK=0` stops the release channel asking GitHub; the local button still works.
    update_check: bool = True
    # The workspace whose `origin/main` the *Build local* button builds. Unset means no
    # button. Set in the env file, never by a request.
    update_local_from: str | None = None
    # The next five are read without the `COS_` prefix: systemd and a login shell hand
    # them to every process. `invocation_id` is systemd's `INVOCATION_ID`.
    invocation_id: str | None = None
    # `${XDG_CONFIG_HOME:-$HOME/.config}`, where `install.sh` writes the unit file.
    config_home: str = ""
    # Where `uv` may be, in the order `scripts/install.sh` looks: every `PATH` entry, then
    # `UV_INSTALL_DIR`, `XDG_BIN_HOME`, `~/.local/bin`, `~/.cargo/bin`.
    uv_candidates: tuple[str, ...] = ()
    # The updater's subprocess environment is built from these, not inherited, so a trial
    # run of the new version sees no `INVOCATION_ID` or `COS_WORKING_DIR`.
    path_env: str = ""
    home: str = ""

    def effective_tools(self) -> list[str]:
        """The tool list a session is created with; knob 2 filters here for every path."""
        if self.allow_write_and_exec:
            return list(self.tools)
        return [t for t in self.tools if t not in WRITE_AND_EXEC_TOOLS]

    def permission_mode(self) -> str:
        """`bypassPermissions` only when knob 3 is on."""
        return "bypassPermissions" if self.bypass_permissions else "default"

    def may_resume(self, session_created_here: bool) -> bool:
        """The app resumes only what it created, until knob 4 is turned on."""
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
    """The databases `PROTECTED_DB_VAR` names, each resolved; `coscc/data.py` refuses them.

    Read on every call, so a test can set the variable around one `Data`.
    """
    e = os.environ if env is None else env
    raw = e.get(PROTECTED_DB_VAR) or ""
    return tuple(
        Path(part).expanduser().resolve() for part in raw.split(os.pathsep) if part.strip()
    )


def protect(db_path: str | os.PathLike[str], env: dict[str, str] | None = None) -> str:
    """The value of `PROTECTED_DB_VAR` for a child that must not open `db_path`.

    Appended, never overwritten: an app inside a step already carries the outer database.
    """
    e = os.environ if env is None else env
    kept = [part for part in (e.get(PROTECTED_DB_VAR) or "").split(os.pathsep) if part.strip()]
    mine = Path(db_path).expanduser().resolve()
    if mine not in protected_databases(e):
        kept.append(str(mine))
    return os.pathsep.join(kept)


def from_env(env: dict[str, str] | None = None) -> Config:
    """Build the config; the only reader of the environment.

    Knob 3 and `data_dir` are reachable only from here: a request has no path to this
    function, and there is deliberately no setter.
    """
    e = dict(os.environ if env is None else env)
    working_dir = _dir(e, "WORKING_DIR")
    declared = _list(e, "WORKSPACES")
    # With no working folder and nothing declared, the app is about the cwd. Once a working
    # folder exists, an undeclared `COS_WORKSPACES` means none.
    fallback = () if working_dir else (str(Path.cwd()),)
    return Config(
        tools=_list(e, "TOOLS"),
        allow_write_and_exec=_flag(e, "ALLOW_WRITE_AND_EXEC", False),
        bypass_permissions=_flag(e, "BYPASS_PERMISSIONS", False),
        resume_foreign_sessions=_flag(e, "RESUME_FOREIGN_SESSIONS", False),
        workspaces=declared or fallback,
        working_dir=working_dir,
        data_dir=_dir(e, "DATA_DIR"),
        # Empty is unset, for every setting: `child_env` can only blank a `COS_*` name, and
        # `int("")` would raise.
        host=(e.get(_ENV_PREFIX + "HOST") or "").strip() or "0.0.0.0",
        port=int((e.get(_ENV_PREFIX + "PORT") or "").strip() or "8790"),
        model=e.get(_ENV_PREFIX + "MODEL") or None,
        update_check=_flag(e, "UPDATE_CHECK", True),
        update_local_from=_dir(e, "UPDATE_LOCAL_FROM"),
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
