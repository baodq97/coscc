"""Where the compiled frontend is, and making it agree with the address it is served on.

A checkout builds into `.web/`; a wheel carries the same tree in `coscc/_web/` (keeping
`build/client`, so `REFLEX_WEB_WORKDIR` points at either root). The bundle bakes in the URL
it opens `/_event` against, so a wheel, built once and served anywhere, has its address
rewritten after the build.

The address lives in one hashed file, `assets/reflex-env-<hash>.js` (hence `_ENV_GLOB`, and
`NoEnvChunk` when nothing matches: a page that renders and never connects is worse than a
stop). Rewriting the `.js` alone is not enough: browsers send `Accept-Encoding: gzip` and
get the `.gz` sidecar, so `_rewrite_one` regenerates it. The packaged bundle is built for
`0.0.0.0` because the page substitutes the loaded hostname only for `localhost`, `0.0.0.0`
and `::`, so one wheel then serves every hostname and only the port is written here.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path

# `reflex.constants.Dirs.STATIC`, kept as our own constant so this module can answer where a
# bundle is without importing Reflex; `run.py` needs the answer before the app is imported.
_LAYOUT = Path("build") / "client"

# Relative to the bundle root; the hash changes on every build, so never a literal filename.
_ENV_GLOB = "assets/reflex-env-*.js"

# The variable Reflex reads to find its web directory. Set it before importing the app.
WEB_WORKDIR_VAR = "REFLEX_WEB_WORKDIR"

# Reflex re-runs its whole compile on every start unless told not to, and that compile
# needs Bun or npm, which a packaged install lacks; the service crash-loops while `systemctl`
# still says `active`. The variable's name is not its attribute name
# (`REFLEX_SKIP_COMPILE.name` is `__REFLEX_SKIP_COMPILE`).
SKIP_COMPILE_VAR = "__REFLEX_SKIP_COMPILE"

# `compile_app` still falls through to the full compile unless a marker from a previous
# build is present. The marker is written into `.web/backend/`, outside `build/client`, so
# a release must copy it too.
_BACKEND = Path("backend")
MARKER = _BACKEND / "stateful_pages.json"
BUNDLED_LIBRARIES = _BACKEND / "bundled_libraries.json"

# Where the release puts the bundle inside the wheel: the package root, one up from `web/`.
PACKAGE_WEB = Path(__file__).resolve().parents[1] / "_web"

# Only the authority of an absolute URL is replaced and the scheme is kept: the chunk holds
# both `http://` and `ws://` forms, and swapping one for the other would break the socket.
_AUTHORITY = re.compile(r"\b(?P<scheme>wss?|https?)://[^/`'\"\s)]+")


class NoEnvChunk(RuntimeError):
    """No file matched `_ENV_GLOB`, so the address could not be written.

    Raised and never caught here: the alternative is a page that looks healthy and cannot
    reach its backend.
    """


def web_dir(repo: Path) -> Path:
    """The directory `REFLEX_WEB_WORKDIR` should point at; the packaged bundle wins when it exists."""
    return PACKAGE_WEB if is_packaged() else repo / ".web"


def is_packaged() -> bool:
    """Whether this install carries its own bundle."""
    return (PACKAGE_WEB / _LAYOUT / "index.html").is_file()


def static_dir(repo: Path) -> Path:
    """The bundle root: the directory holding `index.html` and `assets/`."""
    return web_dir(repo) / _LAYOUT


def missing_compile_marker(repo: Path) -> Path | None:
    """The marker Reflex needs to skip its compile, if the bundle does not carry it.

    Returns the path that should have existed, or `None`. Without it Reflex enters the
    compile anyway and fails on a missing Bun or npm. `BUNDLED_LIBRARIES` is not reported:
    Reflex tolerates its absence.
    """
    marker = web_dir(repo) / MARKER
    return None if marker.is_file() else marker


def rewrite_address(static: Path, host: str, port: int) -> int:
    """Point the bundle at `host:port`. Returns the number of files written.

    Raises `NoEnvChunk` when nothing matched; every other failure raises `OSError`.
    """
    chunks = sorted(static.glob(_ENV_GLOB))
    if not chunks:
        raise NoEnvChunk(
            f"no file matched {_ENV_GLOB!r} under {static} -- the bundle does not carry "
            "its address where this version of coscc knows how to write it, so it would "
            "be served pointing somewhere else"
        )
    return sum(_rewrite_one(chunk, host, port) for chunk in chunks)


def _rewrite_one(chunk: Path, host: str, port: int) -> int:
    text = chunk.read_text(encoding="utf-8")
    fixed = _AUTHORITY.sub(lambda m: f"{m.group('scheme')}://{host}:{port}", text)
    chunk.write_text(fixed, encoding="utf-8")
    written = 1

    # Regenerated from the new bytes, and only when the build produced one.
    sidecar = chunk.with_name(chunk.name + ".gz")
    if sidecar.is_file():
        sidecar.write_bytes(gzip.compress(fixed.encode("utf-8")))
        written += 1
    return written


def addresses(static: Path) -> set[str]:
    """Every absolute URL authority the bundle carries, `.gz` sidecars included.

    A scan of the plain files alone would report a bundle clean while browsers are served
    the stale sidecar.
    """
    found: set[str] = set()
    for path in static.rglob("*"):
        if not path.is_file():
            continue
        try:
            raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
        except OSError, gzip.BadGzipFile:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        found.update(m.group(0) for m in _AUTHORITY.finditer(text))
    return found


def event_addresses(static: Path) -> set[str]:
    """The `ws://` authorities the bundle carries. `.gz` sidecars included.

    `addresses()` is too blunt to assert against (the bundle holds other hosts' URLs). The
    page opens a socket only to its own backend, so after a rewrite this set must hold
    exactly the address being served.
    """
    return {a for a in addresses(static) if a.startswith(("ws://", "wss://"))}
