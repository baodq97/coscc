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
unless the aggregated token total is at most `0.7 * N` and no MCP server or `mcp__*` tool
reached any sampled session at all (R11, R12).

**Review round 1, F3.** A single session's token total is not stable: five identical runs
on the same commit, same flags, same prompt (`impl.md ## What was measured`, R12) came back
9150 three times and 10322 twice — a fixed ~1172-token gap whose source is `chưa biết`, not
noise that averages away with more samples of the *same* run. One sample is therefore not
enough to make `--baseline` mean anything; it happened to land on either side of the 30%
ceiling depending on which of the two values it drew. `--repeat N` (default 1, so a plain
run still costs one session) runs the session `N` times and combines the token totals with
`--agg` (`median`, the default, or `max`) before comparing to `--baseline`. Every sample is
still printed, so no individual run's number is lost to the aggregate.

Exit codes:

    0  every sampled session ran, reported exactly one turn and no tool call, and
       --baseline (if given) was met by the aggregate
    1  a session was not a clean first call (num_turns != 1, or it used a tool), or
       --baseline was given and was not met
    2  no `claude` on PATH, or the CLI reports it is not logged in
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
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
        help="exit 1 unless the aggregate tokens <= 0.7*N and no MCP server or mcp__* tool "
        "arrived in any sample",
    )
    parser.add_argument(
        "--repeat", type=int, default=1, metavar="N",
        help="run the session N times (default 1) and combine the token totals with --agg "
        "before checking --baseline — F3: one sample is not stable enough to trust alone",
    )
    parser.add_argument(
        "--agg", choices=("median", "max"), default="median",
        help="how --repeat samples are combined before checking --baseline (default: median)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.repeat < 1:
        print("--repeat must be at least 1", file=sys.stderr)
        return EXIT_ENV

    if shutil.which("claude") is None:
        print("no claude on PATH — this measurement cannot run without it", file=sys.stderr)
        return EXIT_ENV

    samples = []
    for _ in range(args.repeat):
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
        samples.append(data)

    if not args.json:
        for i, data in enumerate(samples, 1):
            prefix = f"sample {i}/{len(samples)}: " if len(samples) > 1 else ""
            for key in ("session_id", "model", "models_used", "num_turns", "tool_calls", "tokens"):
                print(f"{prefix}{key}: {data[key]}")
            print(f"{prefix}tools ({len(data['tools'])}): {', '.join(data['tools'])}")
            print(f"{prefix}mcp tools ({len(data['mcp_tools'])}): {', '.join(data['mcp_tools'])}")
            print(f"{prefix}mcp servers ({len(data['mcp_servers'])}): {', '.join(data['mcp_servers'])}")

    for i, data in enumerate(samples, 1):
        if data["num_turns"] != 1 or data["tool_calls"]:
            print(
                f"sample {i}/{len(samples)} was not a clean first call — num_turns="
                f"{data['num_turns']} tool_calls={data['tool_calls']}; the token total is "
                "not the first call's alone, so nothing is aggregated from it",
                file=sys.stderr,
            )
            return EXIT_BROKEN

    tokens_list = [d["tokens"] for d in samples]
    agg_tokens = statistics.median(tokens_list) if args.agg == "median" else max(tokens_list)
    mcp_servers = sorted({s for d in samples for s in d["mcp_servers"]})
    mcp_tools = sorted({t for d in samples for t in d["mcp_tools"]})

    result = {
        "samples": samples,
        "agg": args.agg,
        "tokens": tokens_list,
        "agg_tokens": agg_tokens,
        "mcp_servers": mcp_servers,
        "mcp_tools": mcp_tools,
    }
    if args.json:
        print(json.dumps(result))
    else:
        print(f"tokens, all samples: {tokens_list}")
        print(f"{args.agg} tokens: {agg_tokens}")
        print(f"mcp servers, union of all samples ({len(mcp_servers)}): {', '.join(mcp_servers)}")
        print(f"mcp tools, union of all samples ({len(mcp_tools)}): {', '.join(mcp_tools)}")

    if args.baseline is not None:
        ceiling = 0.7 * args.baseline
        ok = agg_tokens <= ceiling and not mcp_servers and not mcp_tools
        if not ok:
            print(
                f"--baseline {args.baseline}: {args.agg} tokens={agg_tokens} (ceiling "
                f"{ceiling:.0f}), mcp_servers={mcp_servers}, mcp_tools={mcp_tools}",
                file=sys.stderr,
            )
            return EXIT_BROKEN

    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
