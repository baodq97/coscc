"""Building the page, and the one place that answers "is the build current?".

Building writes a fingerprint beside the output; `run.py` refuses to start on a stale
build. Only a checkout is covered: a wheel carries its bundle and source together, so
they cannot drift. Anything that changes the output but is not in `_SOURCES` (a plugin, an
environment variable read during the build) goes unnoticed: add it there.
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

# Relative to the repo root.
_SOURCES = (
    "coscc/coscc.py",
    "coscc/web/ui.py",
    "coscc/web/studio.py",
    "coscc/screens/__init__.py",
    "coscc/state/__init__.py",
    # `build_test` fails when one is missing.
    "coscc/screens/common.py",
    "coscc/screens/chrome.py",
    "coscc/screens/overview.py",
    "coscc/screens/board.py",
    "coscc/screens/sessions.py",
    "coscc/screens/settings.py",
    "coscc/screens/unit.py",
    "coscc/screens/backlog.py",
    "coscc/screens/dialogs.py",
    "coscc/screens/idea.py",
    "coscc/state/views.py",
    "coscc/state/workspaces.py",
    "coscc/state/watch.py",
    "coscc/state/update.py",
    "coscc/state/answers.py",
    "coscc/state/backlog.py",
    "coscc/state/rerun.py",
    "coscc/state/ideas.py",
    "coscc/state/release.py",
    "rxconfig.py",
)

# `unbuilt`: no output at all. `missing`: output without a fingerprint.
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
        # The bundle hardcodes the backend address; a build aimed elsewhere never connects.
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
