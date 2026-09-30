"""What a feature hands the agent's steps: tools, guards and prompt blocks.

The kernel builds one `Facts` per run and asks each part what it makes of it. A feature never
writes a granted tool name: `granted` derives `mcp__<server>__<name>`, and `Grant` refuses any
other spelling. Nothing here is kept between runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from claude_agent_sdk import McpServerConfig

SERVER = re.compile(r"[a-z][a-z0-9-]*")
LOCAL = re.compile(r"[a-z][a-z0-9_]*")
# The kernel's own server, `submit`'s.
KERNEL_SERVER = "cos"


@dataclass(frozen=True)
class Facts:
    """One run, as the kernel knows it."""

    workspace: str
    workspace_key: str
    unit: str
    stage: str
    run: str
    # The unit's worktree; for a spike, the worktree it watches.
    tree: str
    directory: Path
    # A spike's throwaway directory, else `None`.
    scratch: str | None
    # The effective `grant.commands`.
    commands: tuple[str, ...]
    resumed: bool


@dataclass(frozen=True)
class Tool:
    """MCP tools of one server. `make` is called once per run, so a server is never shared."""

    server: str
    names: tuple[str, ...]
    stages: tuple[str, ...]
    make: Callable[[Facts], McpServerConfig]

    def __post_init__(self) -> None:
        if not SERVER.fullmatch(self.server) or self.server == KERNEL_SERVER:
            raise ValueError(
                f"an MCP server name is [a-z][a-z0-9-]* and not 'cos': {self.server!r}"
            )
        for name in self.names:
            if not LOCAL.fullmatch(name) or "__" in name:
                raise ValueError(f"an MCP tool name is [a-z][a-z0-9_]* without '__': {name!r}")


@dataclass(frozen=True)
class Guard:
    """Words to deny a run, or `None` to abstain. It cannot allow."""

    name: str
    check: Callable[[Facts], str | None]


@dataclass(frozen=True)
class Block:
    """A named block of the prompt. An empty string adds nothing."""

    name: str
    render: Callable[[Facts], str]


@dataclass(frozen=True)
class Parts:
    tools: tuple[Tool, ...] = ()
    guards: tuple[Guard, ...] = ()
    blocks: tuple[Block, ...] = ()


def _always(_feature: str, _workspace: str) -> bool:
    return True


@dataclass(frozen=True)
class Hooks:
    """The parts, each tagged with its feature, and whether a feature is on for a workspace."""

    parts: tuple[tuple[str, Parts], ...] = ()
    enabled: Callable[[str, str], bool] = _always

    def for_step(self, stage: str, workspace: str) -> Parts:
        """Only what is on for this workspace; a tool only for the stages it names."""
        on = [p for feature, p in self.parts if self.enabled(feature, workspace)]
        return Parts(
            tools=tuple(t for p in on for t in p.tools if stage in t.stages),
            guards=tuple(g for p in on for g in p.guards),
            blocks=tuple(b for p in on for b in p.blocks),
        )


def granted(tools: tuple[Tool, ...]) -> tuple[str, ...]:
    """The names the session sees, in order."""
    return tuple(f"mcp__{t.server}__{n}" for t in tools for n in t.names)


def facts(
    *,
    workspace: str,
    workspace_key: str,
    unit: str,
    stage: str,
    run: str,
    cwd: str,
    watch: str | None,
    directory: Path,
    commands: tuple[str, ...],
    resumed: bool,
) -> Facts:
    """`watch` is set when the run is a spike, whose `cwd` is its throwaway directory."""
    return Facts(
        workspace=workspace,
        workspace_key=workspace_key,
        unit=unit,
        stage=stage,
        run=run,
        tree=watch or cwd,
        directory=directory,
        scratch=cwd if watch else None,
        commands=commands,
        resumed=resumed,
    )
