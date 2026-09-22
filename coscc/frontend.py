"""Where the compiled frontend is, and making it agree with the address it is served on.

Two jobs live here because they are the same question asked twice, and `0011`'s `spec.md`
R2 exists because the repository used to answer it in two places that could disagree.

**Job one: which bundle.** A checkout builds into `.web/` and `.gitignore` keeps it out of
git. A wheel cannot use that path -- there is no checkout -- so the release copies the same
tree into `coscc/_web/`, and `_LAYOUT` below is why the copy keeps `build/client` rather
than flattening it: one layout means `REFLEX_WEB_WORKDIR` points at either root and
everything downstream, including Reflex's own static mount, is unchanged.

**Job two: which address.** The compiled bundle bakes in the URL it opens `/_event`
against; `rxconfig.py` records the failure of 2026-09-21 that taught this repository so,
and records that `api_url="/"` is refused by Reflex with `TypeError: Invalid URL`. A wheel
is built once and served wherever it lands, so the address has to be written after the
build rather than during it.

Three measurements, all taken 2026-09-22, decide the shape of `rewrite_address`. None of
them is a Reflex API and `spec.md` C4 says so out loud -- only a browser opening the real
page can tell you they still hold.

1.  The address occupies exactly one file, `assets/reflex-env-<hash>.js`, 306 bytes, six
    occurrences, holding a flat object of absolute URLs. Every other chunk imports it by
    filename, so rewriting its contents in place keeps all seven importers valid.
        grep -rl 127.0.0.1:8790 .web/build/client

2.  That filename carries a content hash, so it changes on every build. Hence `_ENV_GLOB`
    and never a literal; and hence `NoEnvChunk`, because a glob that silently matches
    nothing would serve a page that renders perfectly and never connects.

3.  **The `.gz` sidecar is the copy a browser actually reads.** Reflex's
    `frontend_compression_formats` defaults to `['gzip']` and `PrecompressedStaticFiles`
    prefers the sidecar whenever the request carries `Accept-Encoding: gzip`, which every
    browser sends and `curl` does not. Rewriting the `.js` alone is therefore correct under
    every cheap check and wrong under every real one -- `plan.md` risk 1. `_rewrite_one`
    regenerates the sidecar from the new bytes instead of editing it.

One more measurement explains why the packaged bundle is built for `0.0.0.0` and not for
`127.0.0.1`: the bundle carries a runtime substitution that replaces the baked hostname
with the one the page was loaded from, but only for hosts in its own allowlist --
`[localhost, 0.0.0.0, ::, 0:0:0:0:0:0:0:0]`. `127.0.0.1` is not in it, which is the whole
of the 2026-09-21 failure. Building for `0.0.0.0` therefore makes one wheel serve every
hostname, and leaves only the port for this module to write.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path

# `reflex.constants.Dirs.STATIC`, read 2026-09-22. Kept as our own constant rather than
# imported so that this module can answer where a bundle is without importing Reflex --
# `run.py` needs the answer *before* the app is imported.
_LAYOUT = Path("build") / "client"

# Relative to the bundle root. Measurement 2 above: never a literal filename.
_ENV_GLOB = "assets/reflex-env-*.js"

# The variable Reflex reads to find its web directory (`prerequisites.get_web_dir`). Set
# it before importing the app or the static mount is composed against the wrong root.
WEB_WORKDIR_VAR = "REFLEX_WEB_WORKDIR"

# Reflex re-runs its whole compile on every start unless told not to, and that compile
# ends in `install_frontend_packages`, which requires Bun or npm. A packaged install has
# neither. Measured 2026-09-22 on a clean Debian 13 VM: the service crash-looped on
# `FileNotFoundError: Bun or npm not found`, while `systemctl --user is-active` still
# answered `active` -- `Type=simple` reports a process that spawned, not one that serves.
# Every HTTP check against it returned nothing at all, so this is a failure no amount of
# reading the unit's state would have found.
#
# The variable's name is not its attribute name:
# `reflex_base.environment.environment.REFLEX_SKIP_COMPILE.name` is `__REFLEX_SKIP_COMPILE`,
# read 2026-09-22. Using the attribute name would set a variable nothing reads.
SKIP_COMPILE_VAR = "__REFLEX_SKIP_COMPILE"

# Skipping the compile is only half the answer, and the missing half is what made the
# first fix look complete. `compiler.compile_app` asks `app._should_compile()` and then
# **still falls through to the full compile** unless a marker from a previous build is
# there to take the short path instead -- reflex/compiler/compiler.py:1254-1267, read
# 2026-09-22. The marker and the library registry beside it are written into
# `.web/backend/`, which is outside `build/client`, so a release that copies only the
# static tree ships a wheel that sets the variable and crashes anyway.
_BACKEND = Path("backend")
MARKER = _BACKEND / "stateful_pages.json"
BUNDLED_LIBRARIES = _BACKEND / "bundled_libraries.json"

# Where the release puts the bundle inside the wheel. `pyproject.toml` ships everything
# under `coscc/`, so this is the one place a packaged tree can live.
PACKAGE_WEB = Path(__file__).resolve().parent / "_web"

# Only the authority of an absolute URL is replaced, and the scheme is preserved: the
# chunk holds both `http://` and `ws://` forms of the same address, and turning one into
# the other would break the event socket while leaving every HTTP route healthy.
_AUTHORITY = re.compile(r"\b(?P<scheme>wss?|https?)://[^/`'\"\s)]+")


class NoEnvChunk(RuntimeError):
    """No file matched `_ENV_GLOB`, so the address could not be written.

    Raised rather than returned, and never caught here. Measurement 2: the alternative to
    stopping is a page that looks healthy and cannot reach its backend.
    """


def web_dir(repo: Path) -> Path:
    """The directory `REFLEX_WEB_WORKDIR` should point at.

    The packaged bundle wins when it exists. A wheel has no checkout to fall back to, and
    a checkout has no `coscc/_web/` unless someone built one by hand -- in which case they
    asked for it.
    """
    return PACKAGE_WEB if is_packaged() else repo / ".web"


def is_packaged() -> bool:
    """Whether this install carries its own bundle."""
    return (PACKAGE_WEB / _LAYOUT / "index.html").is_file()


def static_dir(repo: Path) -> Path:
    """The bundle root: the directory holding `index.html` and `assets/`."""
    return web_dir(repo) / _LAYOUT


def missing_compile_marker(repo: Path) -> Path | None:
    """The marker Reflex needs to skip its compile, if the bundle does not carry it.

    Returns the path that should have existed, or `None` when it is there. A caller that
    ignores this gets the 2026-09-22 failure back: `SKIP_COMPILE_VAR` set, the compile
    entered anyway, and `FileNotFoundError: Bun or npm not found` on a machine that was
    never going to have either.

    Only `MARKER` is reported. `BUNDLED_LIBRARIES` is read through a `try` that swallows a
    missing file (reflex/compiler/utils.py:286-292, read 2026-09-22), so its absence
    degrades rather than stops -- the release still ships it, but it is not worth refusing
    to start over.
    """
    marker = web_dir(repo) / MARKER
    return None if marker.is_file() else marker


def rewrite_address(static: Path, host: str, port: int) -> int:
    """Point the bundle at `host:port`. Returns the number of files written.

    Raises `NoEnvChunk` when nothing matched, which is the only outcome that must stop a
    caller: every other failure mode here raises `OSError` on its own.
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

    # Measurement 3. Regenerated from the new bytes rather than edited, and only when the
    # build produced one -- creating a sidecar that was never there would start serving
    # a compressed variant nobody measured.
    sidecar = chunk.with_name(chunk.name + ".gz")
    if sidecar.is_file():
        sidecar.write_bytes(gzip.compress(fixed.encode("utf-8")))
        written += 1
    return written


def addresses(static: Path) -> set[str]:
    """Every absolute URL authority the bundle carries, `.gz` sidecars included.

    The decompression is the point. A scan that reads only the plain files reports the
    bundle clean while every browser is served the stale copy -- `plan.md` risk 1. Used by
    the tests here and by `scripts/verify_0011.py`.
    """
    found: set[str] = set()
    for path in static.rglob("*"):
        if not path.is_file():
            continue
        try:
            raw = (
                gzip.decompress(path.read_bytes())
                if path.suffix == ".gz"
                else path.read_bytes()
            )
        except (OSError, gzip.BadGzipFile):
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        found.update(m.group(0) for m in _AUTHORITY.finditer(text))
    return found


def event_addresses(static: Path) -> set[str]:
    """The `ws://` authorities the bundle carries. `.gz` sidecars included.

    `addresses()` is too blunt to assert against: measured on the real bundle 2026-09-22,
    it returns thirteen authorities, eleven of which are other people's -- `react.dev`,
    `github.com`, and a `http://localhost:3000` left over from Reflex's dev default. A
    check phrased as "no address but ours" would fail on all of them.

    The websocket form is exact instead. The only thing the page opens a socket to is its
    own backend, so after a rewrite this set must hold exactly one entry and that entry
    must be the address being served. `scripts/verify_0011.py` asserts precisely that.
    """
    return {a for a in addresses(static) if a.startswith(("ws://", "wss://"))}
