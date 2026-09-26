"""Building the page, and the one place that answers "is the build current?".

The build step is manual, and `spec.md` C3 named what that costs: edit the page,
forget to rebuild, and every check opens the previous bundle, finds it healthy, and reports
success. That is the same failure this unit exists to stop — green evidence about something
that is not what is running — only harder to see.

So building writes a fingerprint beside the output, and anything that serves or measures
the page asks here first. `run.py` refuses to start on a stale build; `verify_0003.py`
refuses to measure one.

**This module is about a checkout, and only a checkout.** `0011` added a second
shape -- a wheel carrying its own bundle under `coscc/_web/` -- and nothing in that
shape reaches this file: `coscc/run.py` branches on `frontend.is_packaged()` before
asking anything here, so the only two callers of `check()` outside the tests
(`coscc/run.py` and `scripts/proof_harness.py`) are both checkout paths. The
question this module answers -- "is the bundle older than the source?" -- has no
meaning in a wheel, where the two travel in the same file and cannot drift. That is
why no packaged fingerprint was built; `plan.md` records the decision.

**What the fingerprint covers, and what it cannot.** The compiled bundle is decided by the
component tree in `coscc/screens.py`, its state in `coscc/state.py` (with the modules
each was split into, `0095`), the shared theme and presentation
modules, `rxconfig.py` (which bakes in the backend address), and the Reflex version that
compiled it. Those inputs are fingerprinted. Anything else
that could change the output — a plugin, an environment variable read during the build —
is **not** covered, and there is no automatic way to notice. If you change how the page is
produced, check that it lands in `_SOURCES` or the fingerprint will say "current" about a
bundle that is not.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from coscc.config import Config, from_env

REPO = Path(__file__).resolve().parent.parent
MARKER = ".coscc-build.json"

# Relative to the repo root. See the module docstring for the limits of this list.
_SOURCES = (
    "coscc/coscc.py",
    "coscc/ui.py",
    "coscc/studio.py",
    "coscc/screens.py",
    "coscc/state.py",
    # `0095`: the modules `state.py` was split into. `build_test` fails when one is missing.
    "coscc/state_views.py",
    "coscc/state_workspaces.py",
    "coscc/state_watch.py",
    "coscc/state_update.py",
    "coscc/state_answers.py",
    "coscc/state_backlog.py",
    "coscc/state_rerun.py",
    "rxconfig.py",
)

# States `check` can return. `unbuilt` and `missing` are different: one means no output at
# all, the other means output produced by something that did not leave a fingerprint.
OK = "ok"
STALE = "stale"
MISSING = "missing"
UNBUILT = "unbuilt"


def web_dir() -> Path:
    from reflex.utils import prerequisites

    return prerequisites.get_web_dir()


def _reflex_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("reflex")
    except PackageNotFoundError:  # pragma: no cover - reflex is a hard dependency
        return "unknown"


def source_digest(root: Path = REPO) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in _SOURCES:
        path = root / rel
        try:
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            out[rel] = "missing"
    return out


def expected(config: Config, root: Path = REPO) -> dict:
    """What a fingerprint written right now would say."""
    return {
        "sources": source_digest(root),
        "reflex": _reflex_version(),
        "host": config.host,
        "port": config.port,
    }


def marker_path(built: Path) -> Path:
    return built / MARKER


def read_marker(built: Path) -> dict | None:
    try:
        data = json.loads(marker_path(built).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_marker(built: Path, config: Config, root: Path = REPO) -> dict:
    built.mkdir(parents=True, exist_ok=True)
    data = expected(config, root)
    marker_path(built).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data


def check(config: Config, built: Path, root: Path = REPO) -> tuple[str, str]:
    """`(state, message)`. The message is written to be shown to a person as-is."""
    if not (built / "index.html").is_file():
        return UNBUILT, "the frontend is not built yet — run:\n    uv run coscc-build"
    found = read_marker(built)
    if found is None:
        return (
            MISSING,
            "the frontend was built without a fingerprint, so it cannot be checked "
            "against the source — rebuild with:\n    uv run coscc-build",
        )
    want = expected(config, root)
    if found == want:
        return OK, ""

    reasons = []
    if found.get("host") != want["host"] or found.get("port") != want["port"]:
        # The failure of 2026-09-21: in a checkout the bundle hardcodes the backend
        # address and cannot be moved, so a build
        # aimed elsewhere renders a page that never connects.
        reasons.append(
            f"built for {found.get('host')}:{found.get('port')}, "
            f"serving {want['host']}:{want['port']}"
        )
    if found.get("reflex") != want["reflex"]:
        reasons.append(f"built with reflex {found.get('reflex')}, now {want['reflex']}")
    changed = [
        rel
        for rel, digest in want["sources"].items()
        if (found.get("sources") or {}).get(rel) != digest
    ]
    if changed:
        reasons.append("changed since the build: " + ", ".join(changed))
    return STALE, "the build does not match the source — " + "; ".join(reasons) + (
        f"\n    COS_HOST={want['host']} COS_PORT={want['port']} uv run coscc-build"
    )


def main() -> None:
    """Build the frontend, then record what it was built from."""
    config = from_env()
    print(f"building the page for http://{config.host}:{config.port}")
    result = subprocess.run(
        [sys.executable, "-m", "reflex", "export", "--frontend-only", "--no-zip"],
        cwd=REPO,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    built = web_dir() / "build" / "client"
    data = write_marker(built, config)
    print(f"fingerprint written to {marker_path(built)}")
    print(f"  reflex {data['reflex']}, {len(data['sources'])} source files")


if __name__ == "__main__":
    main()
