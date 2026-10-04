"""The studio: the app's new page, served under `/next` beside the old one until it replaces it.

`ui/` builds it into `coscc/_studio/` (`npm --prefix ui run build`), inside the package, so a
wheel carries it like the rest of `coscc/`. Every path below `/next` that is not a built file
gets `index.html`: the page routes by `location.pathname`. The login guard in front of the app
covers these routes like every other.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, Response

BUILT = Path(__file__).resolve().parent / "_studio"
BASE = "/next"

router = APIRouter()

_NOT_BUILT = (
    "<!doctype html><title>cos studio</title><p>The studio is not built. "
    "Run <code>npm --prefix ui ci && npm --prefix ui run build</code>.</p>"
)


def _file(rel: str) -> Path | None:
    """A built file at `rel`, never one outside the build."""
    if not rel:
        return None
    path = (BUILT / rel).resolve()
    if not path.is_relative_to(BUILT) or not path.is_file():
        return None
    return path


@router.get(BASE)
@router.get(BASE + "/{rel:path}")
async def studio(rel: str = "") -> Response:
    """A built asset, or the page itself for any other path below `/next`."""
    found = _file(rel)
    if found is not None:
        # Asset names carry their content hash, so a browser may keep them for good.
        cache = "public, max-age=31536000, immutable" if rel.startswith("assets/") else "no-cache"
        return FileResponse(found, headers={"Cache-Control": cache})
    index = BUILT / "index.html"
    if not index.is_file():
        return HTMLResponse(_NOT_BUILT, status_code=503)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
