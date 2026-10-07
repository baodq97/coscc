"""Whether one use of a secret is allowed, and the sentence that says why not."""

from __future__ import annotations

from collections.abc import Collection
from typing import Literal, get_args

from coscc.vault.store import Secret

Refusal = Literal[
    "unknown-secret",
    "not-granted",
    "stage-not-allowed",
    "not-in-grant",
    "mode-not-allowed",
    "broker-ssh-only",
]
REFUSALS: tuple[str, ...] = get_args(Refusal)


def policy(
    meta: Secret | None,
    workspace: str,
    stage: str,
    mode: str,
    granted: Collection[str] | None = None,
) -> str:
    """`""` when `workspace` may use `meta` for the agent `stage` in `mode`, and the run's grant
    names it (`granted`, the secrets of its `use`; `None` asks no grant), else the code of the first
    rule it breaks. A `ws:` secret of another workspace is unknown, not refused, so a name says
    nothing about what another workspace keeps."""
    if meta is None or not meta.has_value:
        return "unknown-secret"
    if meta.tier == "ws" and meta.workspace != workspace:
        return "unknown-secret"
    if meta.tier == "global" and workspace not in meta.granted:
        return "not-granted"
    if stage not in meta.agents:
        return "stage-not-allowed"
    if granted is not None and meta.name not in granted:
        return "not-in-grant"
    if meta.broker and mode != "ssh":
        return "broker-ssh-only"
    if mode not in meta.modes:
        return "mode-not-allowed"
    return ""


def sentence(code: str, name: str, workspace: str, stage: str, mode: str) -> str:
    """One sentence naming the secret, and the workspace, stage or mode the code is about."""
    return {
        "unknown-secret": f"{name} is not a secret with a value that {workspace} can see.",
        "not-granted": f"{name} is not granted to {workspace}.",
        "stage-not-allowed": f"{name} may not be used by the {stage} agent.",
        "not-in-grant": f"{name} is not in this run's grant.",
        "mode-not-allowed": f"{name} may not be passed as {mode}.",
        "broker-ssh-only": f"{name} is a broker secret, which only ssh may use.",
    }[code]
