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
here: this app has no authentication of any kind, so every machine that can route to this
port can use all of it, including the two controls that spend real Claude quota. The
startup banner in `coscc/run.py` says so out loud every time it is not loopback, which is
the only thing standing where a login would be.
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


def _flag(env: dict[str, str], name: str, default: bool) -> bool:
    raw = env.get(_ENV_PREFIX + name)
    if raw is None:
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


def from_env(env: dict[str, str] | None = None) -> Config:
    """Build the config. The only reader of the environment in this app.

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
        host=e.get(_ENV_PREFIX + "HOST", "0.0.0.0"),
        port=int(e.get(_ENV_PREFIX + "PORT", "8790")),
        model=e.get(_ENV_PREFIX + "MODEL") or None,
    )
