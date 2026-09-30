"""Stand-ins for `age`, `age-keygen`, `ssh-agent` and `ssh-add`, so no test needs them installed.

Each is a small script in a directory. The fake `age` keeps a value as base64 behind a header
naming the recipient, so no plain text lands on disk, and it refuses an identity that is not the
one the value was made for. Real `age` is not run by any test here.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from coscc.data import Data
from coscc.vault.store import Store

SHEBANG = f"#!{sys.executable}\n"

AGE = """
import base64, os, sys

prog, args = os.path.basename(sys.argv[0]), sys.argv[1:]


def public(identity):
    return open(identity).read().split("# public key: ")[1].split()[0]


if prog == "age-keygen" and args[0] == "-o":
    pub = "age1fake" + os.urandom(6).hex()
    fd = os.open(args[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, f"# public key: {pub}\\nAGE-SECRET-KEY-FAKE\\n".encode())
    print("Public key:", pub, file=sys.stderr)
elif prog == "age-keygen":
    print(public(args[1]))
elif args[0] == "-e":
    who = args[args.index("-r") + 1].encode()
    sys.stdout.buffer.write(b"FAKEAGE:" + who + b":" + base64.b64encode(sys.stdin.buffer.read()))
else:
    head = b"FAKEAGE:" + public(args[args.index("-i") + 1]).encode() + b":"
    data = sys.stdin.buffer.read()
    if not data.startswith(head):
        print("no identity matched any of the recipients", file=sys.stderr)
        sys.exit(1)
    sys.stdout.buffer.write(base64.b64decode(data[len(head):]))
"""

# Opens the socket it is told to, names its pid beside it and waits to be killed.
SSH_AGENT = """
import os, socket, sys, time

sock = sys.argv[sys.argv.index("-a") + 1]
socket.socket(socket.AF_UNIX).bind(sock)
open(sock + ".pid", "w").write(str(os.getpid()))
time.sleep(60)
"""

# Takes a key on stdin and records only its length, next to the socket.
SSH_ADD = """
import os, sys

assert sys.argv[1:] == ["-"], sys.argv
size = len(sys.stdin.buffer.read())
open(os.environ["SSH_AUTH_SOCK"] + ".key", "a").write(f"{size}\\n")
"""


def _script(path: Path, body: str) -> None:
    path.write_text(SHEBANG + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def install(directory: Path, ssh: bool = False) -> None:
    """`age` and `age-keygen` in `directory`, and with `ssh` a fake `ssh-agent` and `ssh-add`."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("age", "age-keygen"):
        _script(directory / name, AGE)
    if ssh:
        _script(directory / "ssh-agent", SSH_AGENT)
        _script(directory / "ssh-add", SSH_ADD)


def make_store(test: unittest.TestCase, ssh: bool = False) -> tuple[Store, Path]:
    """A store on a fresh data directory that uses the fakes, and the temporary root it lives in.
    `<root>/bin` holds the fakes: a test that runs a command puts it first on `PATH`."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    install(root / "bin", ssh)
    store = Store(
        Data(root / "data"),
        config_home=str(root / "config"),
        home=str(root / "home"),
        age=str(root / "bin" / "age"),
        age_keygen=str(root / "bin" / "age-keygen"),
    )
    return store, root


def on_path(root: Path) -> dict[str, str]:
    """The environment that finds the fakes of `make_store` first."""
    return {"PATH": f"{root / 'bin'}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}"}
