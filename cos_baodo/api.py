"""The JSON surface, as a FastAPI app that stands on its own.

Standing alone is the point. Reflex mounts this exact object via `api_transformer`
(`spec.md` R4), so the browser and the proof command reach the same routes in the same
process — but because it is also a plain ASGI app, the proof can drive it in-process
through `httpx.ASGITransport` with no compiled frontend and no Node. That is what keeps
`npm test` free of a JavaScript toolchain (`plan.md` step 1, check d).

Reflex reserves `/ping/`, `/_event` and `/_upload`. Nothing here may use them.
"""

from __future__ import annotations

from fastapi import FastAPI


def build() -> FastAPI:
    api = FastAPI(title="cos-baodo")

    @api.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return api
