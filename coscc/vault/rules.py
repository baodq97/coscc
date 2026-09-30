"""Whether one use of a secret is allowed, and the sentence that says why not."""

from __future__ import annotations

from typing import Literal, get_args

from coscc.vault.store import Secret

Refusal = Literal[
    "unknown-secret", "not-granted", "stage-not-allowed", "mode-not-allowed", "broker-ssh-only"
]
REFUSALS: tuple[str, ...] = get_args(Refusal)


def policy(meta: Secret | None, workspace: str, stage: str, mode: str) -> str:
    """`""` when `workspace` may use `meta` at `stage` in `mode`, else the code of the first rule
    it breaks. A `ws:` secret of another workspace is unknown, not refused, so a name says nothing
    about what another workspace keeps."""
    if meta is None or not meta.has_value:
        return "unknown-secret"
    if meta.tier == "ws" and meta.workspace != workspace:
        return "unknown-secret"
    if meta.tier == "global" and workspace not in meta.granted:
        return "not-granted"
    if stage not in meta.stages:
        return "stage-not-allowed"
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
        "stage-not-allowed": f"{name} may not be used in the {stage} stage.",
        "mode-not-allowed": f"{name} may not be passed as {mode}.",
        "broker-ssh-only": f"{name} is a broker secret, which only ssh may use.",
    }[code]
