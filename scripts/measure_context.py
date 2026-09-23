#!/usr/bin/env python3
"""R1 (`spec.md`): what an `impl` session's context costs before it does anything.

Opens exactly one real session, with the app's own construction path — `policy.grant_for
("impl")` for the grant and `sessions._options` for the SDK options, copying no flag by
hand — and a short fixed prompt, so the figure is not any one unit's own prompt (`intent.md
## Answers`, câu 4). Prints the session id, the model, the tools and MCP servers the `init`
message reported, and the first call's own token total (`input + cache_creation +
cache_read`, read off `ResultMessage.model_usage` the way `coscc/sessions.py` `_cumulative`
does — a session with exactly one call has no earlier total to subtract).

**It spends real quota.** Every run opens one session on `claude-sonnet-5[1m]`, billed to
whatever account is logged in.

`--strict-mcp` locks the MCP configuration on top of `_options`, the way `coscc/
sessions.py` `_options` itself will once R11 lands — used only to measure `main` *before*
that change (plan.md step 2). `--baseline N` turns the run into a pass/fail check: exit 1
unless the token total is at most `0.7 * N` and no MCP server or `mcp__*` tool reached the
session at all (R11, R12).

Exit codes:

    0  the session ran, reported exactly one turn and no tool call, and --baseline (if
       given) was met
    1  the session ran but was not a clean first call (num_turns != 1, or it used a tool),
       or --baseline was given and was not met
    2  no `claude` on PATH, or the CLI reports it is not logged in
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import claude_agent_sdk as sdk  # noqa: E402

from coscc import policy  # noqa: E402
from coscc.config import Config  # noqa: E402
from coscc.runner import Denials, permission_gate  # noqa: E402
from coscc.sessions import COST_FIELDS, _cumulative, _options  # noqa: E402

EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2

MODEL = "claude-sonnet-5[1m]"
PROMPT = "Reply with exactly: READY"
REPO = Path(__file__).resolve().parent.parent


async def _run(strict_mcp: bool) -> dict:
    grant = policy.grant_for("impl")
    config = Config()
    cwd = str(REPO)
    options = _options(
        config, cwd, None,
        max_turns=grant.max_turns,
        can_use_tool=permission_gate(grant, cwd, Denials()),
        tools=list(grant.tools),
        max_budget_usd=grant.max_budget_usd,
        model=MODEL,
    )
    if strict_mcp:
        # Ahead of R11 landing in `_options` itself (plan.md step 2): the same lock, laid
        # on top here so `main`, before the fix, can still be measured with and without it.
        options.strict_mcp_config = True

    client = sdk.ClaudeSDKClient(options=options)
    await client.connect()
    tools: list[str] = []
    servers: list[str] = []
    session_id = ""
    models_used: list[str] = []
    num_turns = 0
    tool_calls = 0
    total = {name: 0.0 for name in COST_FIELDS}
    try:
        await client.query(PROMPT)
        async for message in client.receive_response():
            if isinstance(message, sdk.SystemMessage) and message.subtype == "init":
                tools = [str(t) for t in (message.data.get("tools") or [])]
                servers = [
                    str(s.get("name", s)) if isinstance(s, dict) else str(s)
                    for s in (message.data.get("mcp_servers") or [])
                ]
                session_id = str(message.data.get("session_id") or session_id)
            elif isinstance(message, sdk.AssistantMessage):
                for block in message.content:
                    if isinstance(block, (sdk.ToolUseBlock, sdk.ServerToolUseBlock)):
                        tool_calls += 1
                if message.session_id:
                    session_id = message.session_id
            elif isinstance(message, sdk.ResultMessage):
                session_id = message.session_id or session_id
                num_turns = int(getattr(message, "num_turns", 0) or 0)
                total = _cumulative(message)
                models_used = sorted(str(k) for k in (getattr(message, "model_usage", None) or {}))
    finally:
        await client.disconnect()

    tokens = int(
        total.get("input_tokens", 0.0)
        + total.get("cache_creation_tokens", 0.0)
        + total.get("cache_read_tokens", 0.0)
    )
    return {
        "session_id": session_id,
        "model": MODEL,
        "models_used": models_used,
        "tools": tools,
        "mcp_tools": [t for t in tools if t.startswith("mcp__")],
        "mcp_servers": servers,
        "num_turns": num_turns,
        "tool_calls": tool_calls,
        "tokens": tokens,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--strict-mcp", action="store_true",
        help="lock MCP config on top of _options; only for measuring main before R11 lands",
    )
    parser.add_argument(
        "--baseline", type=float, default=None, metavar="N",
        help="exit 1 unless tokens <= 0.7*N and no MCP server or mcp__* tool arrived",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if shutil.which("claude") is None:
        print("no claude on PATH — this measurement cannot run without it", file=sys.stderr)
        return EXIT_ENV

    try:
        data = asyncio.run(_run(args.strict_mcp))
    except sdk.CLIConnectionError as e:
        print(f"claude could not be reached: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_ENV
    except sdk.ProcessError as e:
        text = str(e).lower()
        if "login" in text or "auth" in text:
            print(f"claude is not logged in: {e}", file=sys.stderr)
            return EXIT_ENV
        raise

    if args.json:
        print(json.dumps(data))
    else:
        for key in ("session_id", "model", "models_used", "num_turns", "tool_calls", "tokens"):
            print(f"{key}: {data[key]}")
        print(f"tools ({len(data['tools'])}): {', '.join(data['tools'])}")
        print(f"mcp tools ({len(data['mcp_tools'])}): {', '.join(data['mcp_tools'])}")
        print(f"mcp servers ({len(data['mcp_servers'])}): {', '.join(data['mcp_servers'])}")

    if data["num_turns"] != 1 or data["tool_calls"]:
        print(
            "not a clean first call — num_turns="
            f"{data['num_turns']} tool_calls={data['tool_calls']}; the token total is not "
            "the first call's alone, so it is not reported as one",
            file=sys.stderr,
        )
        return EXIT_BROKEN

    if args.baseline is not None:
        ceiling = 0.7 * args.baseline
        ok = (
            data["tokens"] <= ceiling
            and not data["mcp_servers"]
            and not data["mcp_tools"]
        )
        if not ok:
            print(
                f"--baseline {args.baseline}: tokens={data['tokens']} (ceiling "
                f"{ceiling:.0f}), mcp_servers={data['mcp_servers']}, "
                f"mcp_tools={data['mcp_tools']}",
                file=sys.stderr,
            )
            return EXIT_BROKEN

    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
