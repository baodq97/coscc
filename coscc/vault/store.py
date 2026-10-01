"""The vault's store: a row per secret in `cos.db`, and a value per secret in an `age` file.

The app writes no cryptography: `age` encrypts and decrypts through pipes, so a value never
lands on disk as plain text and never enters this process's environment. The identity is a file
under the config home, made by `age-keygen` the first time a value is put.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from coscc.agent import policy
from coscc.config import from_env
from coscc.data import Data, now

MODES = ("env", "file", "placeholder", "ssh")
VAULT_STAGES = ("impl", "spike")
NAME = re.compile(r"(global|ws):[a-z0-9][a-z0-9._-]{0,63}")

# Chosen, not measured: turns a hung `age` into an error.
AGE_TIMEOUT = 30

TABLES = (
    """CREATE TABLE IF NOT EXISTS vault_secrets (
    name        TEXT NOT NULL,
    workspace   TEXT NOT NULL,
    description TEXT NOT NULL,
    stages      TEXT NOT NULL,
    modes       TEXT NOT NULL,
    broker      INTEGER NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    has_value   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (name, workspace)
)""",
    """CREATE TABLE IF NOT EXISTS vault_grants (
    name      TEXT NOT NULL,
    workspace TEXT NOT NULL,
    PRIMARY KEY (name, workspace)
)""",
)

# Two first puts must not both make the identity.
_IDENTITY = threading.Lock()


class BadSecret(Exception):
    """A refusal to show a person: a bad name, a duplicate, an unknown secret, a failed `age`."""


@dataclass(frozen=True)
class Secret:
    name: str
    # The workspace key of a `ws:` secret; empty for a `global:` one.
    workspace: str
    description: str
    stages: tuple[str, ...]
    modes: tuple[str, ...]
    broker: bool
    # The workspace keys a `global:` secret is granted to; empty for a `ws:` one.
    granted: tuple[str, ...]
    created_by: str
    created_at: str
    has_value: bool

    @property
    def tier(self) -> str:
        return "ws" if self.name.startswith("ws:") else "global"


def _subset(what: str, given: tuple[str, ...], allowed: tuple[str, ...]) -> tuple[str, ...]:
    """`given` in the order of `allowed`; a member outside it is refused by name."""
    unknown = [g for g in given if g not in allowed]
    if unknown:
        raise BadSecret(f"not a {what}: {', '.join(unknown)}; one of {', '.join(allowed)}")
    return tuple(a for a in allowed if a in given)


class Store:
    def __init__(
        self,
        data: Data,
        config_home: str | None = None,
        home: str | None = None,
        age: str = "age",
        age_keygen: str = "age-keygen",
    ):
        if config_home is None or home is None:
            config = from_env()
            config_home = config.config_home if config_home is None else config_home
            home = config.home if home is None else home
        if not config_home:
            raise ValueError("the vault key needs a config home: set HOME or XDG_CONFIG_HOME")
        self.data = data
        self.config_home = config_home
        self.home = home
        self.age = age
        self.age_keygen = age_keygen
        self.dir = data.root / "vault"
        self.identity = Path(config_home) / "coscc" / "vault.key"
        self._ready = False

    def can_encrypt(self) -> bool:
        """Whether `age` and `age-keygen` are on the `PATH` a call runs with."""
        return all(shutil.which(tool) for tool in (self.age, self.age_keygen))

    def protected(self) -> tuple[str, ...]:
        """The paths no command word may point into (`policy.protected_paths`)."""
        return policy.protected_paths(str(self.data.root), self.config_home, self.home)

    # -- metadata -----------------------------------------------------------

    def _tables(self) -> None:
        if self._ready:
            return
        with self.data.write() as conn:
            for statement in TABLES:
                conn.execute(statement)
        self._ready = True

    @staticmethod
    def _row_key(name: str, workspace: str) -> tuple[str, str]:
        """`(name, workspace column)`, or `BadSecret` saying why the name will not do."""
        if not NAME.fullmatch(name or ""):
            raise BadSecret(
                f"{name!r} is not a secret name: global:<name> or ws:<name>, where <name> is "
                "1-64 of a-z, 0-9, '.', '_' or '-' and starts with a letter or digit"
            )
        if name.startswith("global:"):
            return name, ""
        if not workspace:
            raise BadSecret(f"{name} belongs to a workspace, and none was given")
        return name, workspace

    def _select(self, where: str = "", args: tuple[str, ...] = ()) -> list[Secret]:
        self._tables()
        with self.data.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM vault_secrets {where} ORDER BY name, workspace", args
            ).fetchall()
            granted: dict[str, list[str]] = {}
            for g in conn.execute("SELECT name, workspace FROM vault_grants ORDER BY workspace"):
                granted.setdefault(g["name"], []).append(g["workspace"])
        return [
            Secret(
                name=r["name"],
                workspace=r["workspace"],
                description=r["description"],
                stages=tuple(json.loads(r["stages"])),
                modes=tuple(json.loads(r["modes"])),
                broker=bool(r["broker"]),
                granted=tuple(granted.get(r["name"], ())) if r["workspace"] == "" else (),
                created_by=r["created_by"],
                created_at=r["created_at"],
                has_value=bool(r["has_value"]),
            )
            for r in rows
        ]

    def create(
        self,
        name: str,
        workspace: str,
        description: str = "",
        stages: tuple[str, ...] = ("impl",),
        modes: tuple[str, ...] = ("env", "file"),
        broker: bool = False,
        actor: str = "human:owner",
    ) -> Secret:
        """A secret with no value yet. A name in use is refused, never overwritten."""
        name, column = self._row_key(name, workspace)
        stages = _subset("stage", stages, VAULT_STAGES)
        modes = ("ssh",) if broker else _subset("mode", modes, MODES)
        self._tables()
        try:
            with self.data.write() as conn:
                conn.execute(
                    "INSERT INTO vault_secrets (name, workspace, description, stages, modes, "
                    "broker, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        name,
                        column,
                        description.strip(),
                        json.dumps(stages),
                        json.dumps(modes),
                        int(broker),
                        actor,
                        now(),
                    ),
                )
        except sqlite3.IntegrityError as e:
            raise BadSecret(f"{name} already exists") from e
        return self.known(name, workspace)

    def get(self, name: str, workspace: str) -> Secret | None:
        try:
            name, column = self._row_key(name, workspace)
        except BadSecret:
            return None
        found = self._select("WHERE name = ? AND workspace = ?", (name, column))
        return found[0] if found else None

    def known(self, name: str, workspace: str) -> Secret:
        """`get`, or `BadSecret` when there is no such secret."""
        found = self.get(name, workspace)
        if found is None:
            raise BadSecret(f"there is no secret {name}")
        return found

    def all(self) -> list[Secret]:
        return self._select()

    def visible(self, workspace: str) -> list[Secret]:
        """Its own `ws:` secrets and the `global:` ones granted to it."""
        return [
            s
            for s in self.all()
            if (s.tier == "ws" and s.workspace == workspace)
            or (s.tier == "global" and workspace in s.granted)
        ]

    def set_policy(
        self, name: str, workspace: str, stages: tuple[str, ...], modes: tuple[str, ...]
    ) -> Secret:
        """A broker secret keeps `ssh` as its only mode, whatever `modes` says."""
        secret = self.known(name, workspace)
        stages = _subset("stage", stages, VAULT_STAGES)
        modes = ("ssh",) if secret.broker else _subset("mode", modes, MODES)
        with self.data.write() as conn:
            conn.execute(
                "UPDATE vault_secrets SET stages = ?, modes = ? WHERE name = ? AND workspace = ?",
                (json.dumps(stages), json.dumps(modes), secret.name, secret.workspace),
            )
        return self.known(name, workspace)

    def grant(self, name: str, workspace: str) -> Secret:
        """Let `workspace` use the `global:` secret `name`."""
        if not name.startswith("global:") or not workspace:
            raise BadSecret("only a global: secret is granted, and to a workspace")
        secret = self.known(name, "")
        with self.data.write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO vault_grants (name, workspace) VALUES (?, ?)",
                (secret.name, workspace),
            )
        return self.known(name, "")

    def revoke(self, name: str, workspace: str) -> Secret:
        if not name.startswith("global:"):
            raise BadSecret("only a global: secret is granted")
        secret = self.known(name, "")
        with self.data.write() as conn:
            conn.execute(
                "DELETE FROM vault_grants WHERE name = ? AND workspace = ?",
                (secret.name, workspace),
            )
        return self.known(name, "")

    def delete(self, name: str, workspace: str) -> None:
        """The row, its grants and its file."""
        secret = self.known(name, workspace)
        with self.data.write() as conn:
            conn.execute("DELETE FROM vault_grants WHERE name = ?", (secret.name,))
            conn.execute(
                "DELETE FROM vault_secrets WHERE name = ? AND workspace = ?",
                (secret.name, secret.workspace),
            )
        self._file(secret).unlink(missing_ok=True)

    # -- values -------------------------------------------------------------

    def _file(self, secret: Secret) -> Path:
        digest = hashlib.sha256(f"{secret.workspace}\0{secret.name}".encode()).hexdigest()
        return self.dir / f"{digest[:32]}.age"

    def _run(self, argv: list[str], stdin: bytes) -> bytes:
        """One `age` call with a bare environment, or `BadSecret` with what it said."""
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        try:
            done = subprocess.run(
                argv, input=stdin, capture_output=True, timeout=AGE_TIMEOUT, env=env, cwd="/"
            )
        except FileNotFoundError as e:
            raise BadSecret(f"{argv[0]} is not installed: install age") from e
        except subprocess.TimeoutExpired as e:
            raise BadSecret(f"{Path(argv[0]).name} did not finish in {AGE_TIMEOUT}s") from e
        if done.returncode:
            said = done.stderr.decode(errors="replace").strip()[:200]
            raise BadSecret(f"{Path(argv[0]).name} failed: {said}")
        return done.stdout

    def _recipient(self) -> str:
        with _IDENTITY:
            if not self.identity.exists():
                self.identity.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self._run([self.age_keygen, "-o", str(self.identity)], b"")
                os.chmod(self.identity, 0o600)
        out = self._run([self.age_keygen, "-y", str(self.identity)], b"")
        return out.decode().strip()

    def put(self, name: str, workspace: str, value: bytes) -> None:
        """Encrypt `value` for the identity and keep it; a second put replaces the first."""
        secret = self.known(name, workspace)
        if not value:
            raise BadSecret("a value cannot be empty")
        cipher = self._run([self.age, "-e", "-r", self._recipient()], value)
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        target = self._file(secret)
        partial = target.with_suffix(".part")
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(cipher)
        os.replace(partial, target)
        with self.data.write() as conn:
            conn.execute(
                "UPDATE vault_secrets SET has_value = 1 WHERE name = ? AND workspace = ?",
                (secret.name, secret.workspace),
            )

    def open(self, name: str, workspace: str) -> bytes:
        secret = self.known(name, workspace)
        if not secret.has_value:
            raise BadSecret(f"{name} has no value yet")
        try:
            cipher = self._file(secret).read_bytes()
        except OSError as e:
            raise BadSecret(f"the value of {name} could not be read: {e.strerror}") from e
        return self._run([self.age, "-d", "-i", str(self.identity)], cipher)

    def values_for(self, workspace: str) -> dict[str, bytes]:
        """Every visible secret that has a value, decrypted."""
        return {
            s.name: self.open(s.name, s.workspace) for s in self.visible(workspace) if s.has_value
        }


def generate(
    store: Store, name: str, workspace: str, description: str = "", actor: str = "agent:impl"
) -> Secret:
    """A `ws:` secret of 32 random bytes, base64 URL-safe without padding. No agent names a value."""
    if not name.startswith("ws:"):
        raise BadSecret("an agent makes ws: secrets only")
    store.create(name, workspace, description, actor=actor)
    try:
        store.put(name, workspace, base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"="))
    except BadSecret:
        store.delete(name, workspace)
        raise
    return store.known(name, workspace)  # the row as `put` left it
