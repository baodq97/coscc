#!/usr/bin/env python3
"""Proof, and measuring tool, for `0088_a-stage-session-inherits-the-users-claude-config`.

Every session the app opens now loads no settings source and no MCP server, and carries the
project's own instructions in its system prompt instead. Three modes:

    (default)  no real session but one, no quota, no network beyond 127.0.0.1.
               (a) R1, R2: the nine board stages through `Runner.run`, chat through
                   `Service.stream`, Gebo through `integrate.run_gebo`, and `estimate`
                   through `Service.propose_estimates`, with only the SDK client replaced;
                   each one's argv carries `--setting-sources=` with nothing after it, and
                   `--strict-mcp-config`.
               (b) R4: with `COS_TOOLS=Read,Bash`, `idea` and `intent` carry `--tools ""`, and
                   every other stage exactly its grant.
               (c) R5, R7: on a canary fixture and on this checkout, the block reaches the
                   file `--system-prompt-file` or `--append-system-prompt-file` names, scoped
                   rules as a line each, and none of it is in argv (review round 1, F1).
               (d) R6: `build_prompt`'s source is the same as at the merge-base with main.
               (e) R9 and F1: two real CLI sessions, one per file flag, `cwd` holding a
                   `CLAUDE.md` past the kernel's one-argument limit, `ANTHROPIC_BASE_URL`
                   pointed at a stand-in on 127.0.0.1 through this process's environment;
                   each reaches the stand-in with the block's last line in a request body.
                   The stand-in counts, asks each body that one question in memory, and
                   answers 400; it writes nothing anywhere.
               (f) nothing under `~/.claude/` this proof reads changed while it ran.
               (g) `--measure` on fixture databases: pass, a violation, a missing stage.
    --paid     **spends real money**: four sessions on this machine's login.
               R3 on the init of a tool-less, a read-only and an `impl` session; R8 at
               `cwd=$HOME` with a `Bash` rule `~/.claude/settings.json` allows; R10 on the
               model ids those sessions ran; C4, printed only: does the context hold an email.
    --measure  the intent's outcome, after the window: every board step between this unit's
               merge and the end of 2026-10-16 (+07:00), read from `step_events`.

    0  pass    1  a claim, or the outcome, did not hold    2  the environment cannot answer

`--measure` opens `cos.db` with `mode=ro`, never through `coscc.data.Data`, and writes only
`<COS_DATA_DIR>/measurements/0088-<stamp>.json`.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import http.server
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

EXIT_PASS, EXIT_BROKEN, EXIT_ENV = 0, 1, 2
UNIT = "0088_a-stage-session-inherits-the-users-claude-config"
STAGES = ["idea", "intent", "spec", "spike", "plan", "impl", "pr", "review", "ship"]
REQUIRED = [s for s in STAGES if s != "spike"]
TOOLLESS = ("idea", "intent")
PRESET = {"type": "preset", "preset": "claude_code"}
# The end of 2026-10-16, read in +07:00. The zone is chosen, not sourced (as `verify_0080`).
WINDOW_END = datetime(2026, 10, 17, tzinfo=timezone(timedelta(hours=7)))
# What (f) hashes. Only reads: this proof writes nothing under `~/.claude/`.
USER_FILES = ("settings.json", "settings.local.json", "plugins/installed_plugins.json")
# `--paid` (b): the first `Bash(...)` rule with one of these prefixes, run as this command.
READ_ONLY = {
    "systemctl is-enabled": "systemctl is-enabled ssh",
    "ls": "ls",
    "git status": "git status",
    "echo": "echo verify_0088",
}
HOOKS = ("hook_started", "hook_response")
ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(Z|[+-]\d{2}:?\d{2})?")


class Unanswerable(Exception):
    """The environment cannot answer a claim: exit 2, never a pass."""


def say(ok: bool, claim: str, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {claim}{': ' + detail if detail and not ok else ''}",
          flush=True)
    return ok


def data_root() -> Path:
    return Path(os.environ.get("COS_DATA_DIR") or "~/.cos").expanduser()


# ---------------------------------------------------------------------------
# The machine's own configuration: read, never written
# ---------------------------------------------------------------------------


def user_state(home: Path) -> dict[str, str]:
    """(f). A hash per file, `absent` when there is none, and the names under `skills/`."""
    claude = home / ".claude"
    out: dict[str, str] = {}
    for name in USER_FILES:
        try:
            out[name] = hashlib.sha256((claude / name).read_bytes()).hexdigest()
        except OSError:
            out[name] = "absent"
    skills = claude / "skills"
    out["skills/"] = ",".join(sorted(p.name for p in skills.iterdir())) if skills.is_dir() else "absent"
    return out


def user_skills(home: Path) -> set[str]:
    """Directories under `~/.claude/skills/` whose name does not open with `.` (`.trash`)."""
    skills = home / ".claude" / "skills"
    if not skills.is_dir():
        return set()
    return {p.name for p in skills.iterdir() if p.is_dir() and not p.name.startswith(".")}


def project_skills(cwd: str) -> set[str]:
    skills = Path(cwd) / ".claude" / "skills"
    if not skills.is_dir():
        return set()
    return {p.name for p in skills.iterdir() if p.is_dir()}


def user_plugins(home: Path) -> set[str]:
    """The plugin names `installed_plugins.json` lists, whatever its marketplace."""
    try:
        raw = json.loads((home / ".claude" / "plugins" / "installed_plugins.json").read_text())
    except (OSError, ValueError):
        return set()
    listed = raw.get("plugins", raw) if isinstance(raw, dict) else {}
    return {str(k).split("@", 1)[0] for k in listed} if isinstance(listed, dict) else set()


def user_mcp_servers(home: Path) -> dict:
    try:
        raw = json.loads((home / ".claude.json").read_text())
    except (OSError, ValueError):
        return {}
    return raw.get("mcpServers") or {}


# ---------------------------------------------------------------------------
# R3 on one init message
# ---------------------------------------------------------------------------


def r3(init: dict, granted: list[str], subtypes: list[str], home: Path) -> list[str]:
    """Every way `init` breaks R3 (a)-(f). Empty when it holds."""
    bad: list[str] = []
    if init.get("mcp_servers") != []:
        bad.append(f"(a) mcp_servers is {init.get('mcp_servers')!r}")
    tools = [str(t) for t in init.get("tools") or []]
    if set(tools) != set(granted):
        bad.append(f"(b) tools {sorted(tools)} are not the grant {sorted(granted)}")
    if any(t.startswith("mcp__") for t in tools):
        bad.append("(b) an mcp__ tool is listed")
    if "Skill" in tools and "Skill" not in granted:
        bad.append("(b) Skill is listed and not granted")
    plugins = init.get("plugins") or []
    foreign = [p for p in plugins if not str((p or {}).get("source", "")).endswith("@builtin")]
    if foreign:
        bad.append(f"(c) plugins not @builtin: {foreign}")
    # A skill is listed by its bare name; a plugin's skills and commands as `<plugin>:<name>`.
    # So a plugin is matched on the prefix only: the CLI's built-in `code-review` skill shares
    # its name with the plugin `code-review@claude-plugins-official`, measured on the first
    # `--paid` run with that plugin installed and not loaded.
    skills = user_skills(home) | project_skills(str(init.get("cwd") or ""))
    plugins = user_plugins(home) | {str(p.get("name")) for p in foreign if isinstance(p, dict)}
    names = [str(n) for n in (init.get("skills") or []) + (init.get("slash_commands") or [])]
    clash = sorted({n for n in names
                    if n in skills or (":" in n and n.split(":", 1)[0] in plugins)})
    if clash:
        bad.append(f"(d) skills or slash commands from this machine or project: {clash}")
    if init.get("output_style") != "default":
        bad.append(f"(e) output_style is {init.get('output_style')!r}")
    hooks = [s for s in subtypes if s in HOOKS]
    if hooks:
        bad.append(f"(f) hook messages: {hooks}")
    return bad


# ---------------------------------------------------------------------------
# --measure
# ---------------------------------------------------------------------------


# Copied from `scripts/verify_0074.py:55-80`; the proofs do not import each other.
def shipped_at(ship: Path) -> datetime | None:
    """The first ISO-8601 timestamp under `## What went out`, in UTC; `None` if there is none."""
    try:
        text = ship.read_text(encoding="utf-8")
    except OSError:
        return None
    body: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.strip() == "## What went out"
            continue
        if inside:
            body.append(line)
    m = ISO.search("\n".join(body))
    if not m:
        return None
    zone = m.group(2) or "Z"
    zone = "+00:00" if zone == "Z" else (zone if ":" in zone else f"{zone[:3]}:{zone[3:]}")
    try:
        at = datetime.fromisoformat(m.group(1) + zone)
    except ValueError:
        return None
    return at.astimezone(timezone.utc)


def _when(at: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def measure(root: Path, now: datetime | None = None, home: Path | None = None) -> int:
    """R12. Everything it prints, it also writes to one file under `measurements/`."""
    now = now or datetime.now(timezone.utc)
    home = home or Path.home()
    ships = sorted(root.glob(f"units/*/.cos/{UNIT}/ship.md"))
    installed = next((t for t in (shipped_at(p) for p in ships) if t is not None), None)
    if installed is None:
        print(f"no merge line in any {UNIT}/ship.md under {root}/units: nothing to measure yet")
        return EXIT_ENV
    if now < WINDOW_END:
        print(f"merged {installed.isoformat()}; the window closes {WINDOW_END.isoformat()}, not yet")
        return EXIT_ENV
    mcp, skills = user_mcp_servers(home), user_skills(home)
    if not mcp or not skills:
        print(f"this machine has {len(mcp)} MCP servers in ~/.claude.json and {len(skills)} "
              "personal skills; the outcome needs at least one of each")
        return EXIT_ENV
    db = root / "cos.db"
    if not db.is_file():
        print(f"no {db}")
        return EXIT_ENV
    steps: list[dict] = []
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        for (raw,) in conn.execute("SELECT record FROM runs WHERE kind = 'start' ORDER BY id"):
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            at = _when(r.get("at"))
            if r.get("stage") not in STAGES or not r.get("run") or at is None:
                continue
            if not (installed <= at < WINDOW_END):
                continue
            events = conn.execute(
                "SELECT event FROM step_events WHERE run = ? AND kind = 'system' ORDER BY seq",
                (r["run"],),
            ).fetchall()
            step = {"unit": r.get("unit"), "stage": r["stage"], "at": r.get("at"),
                    "run": r["run"], "problems": [], "unreadable": ""}
            subtypes: list[str] = []
            init = None
            for (text,) in events:
                try:
                    event = json.loads(text)
                except ValueError:
                    step["unreadable"] = "an event is not JSON"
                    continue
                subtypes.append(str(event.get("subtype") or ""))
                if event.get("subtype") == "init":
                    if event.get("truncated"):
                        step["unreadable"] = f"init cut at FIELD_MAX ({event.get('length')} chars)"
                        continue
                    try:
                        init = (json.loads(event.get("data") or "") or {}).get("data")
                    except ValueError:
                        step["unreadable"] = "init data is not JSON"
            if init is None and not step["unreadable"]:
                step["unreadable"] = "no init event"
            if init is not None:
                step["problems"] = r3(init, list(r.get("granted") or []), subtypes, home)
            steps.append(step)
    broken = [s for s in steps if s["problems"]]
    checked = {s["stage"] for s in steps if not s["unreadable"]}
    missing = [s for s in REQUIRED if s not in checked]
    unreadable = [s for s in steps if s["unreadable"]]
    for s in steps:
        state = "unreadable: " + s["unreadable"] if s["unreadable"] else (
            "; ".join(s["problems"]) or "holds")
        print(f"  {s['unit']} {s['stage']} {s['at']} run {s['run']}: {state}")
    if broken:
        code, result = EXIT_BROKEN, "trượt"
    elif missing or unreadable:
        code, result = EXIT_ENV, "chưa đo được"
    else:
        code, result = EXIT_PASS, "đạt"
    print(f"merged {installed.isoformat()}, window to {WINDOW_END.isoformat()}: {len(steps)} steps, "
          f"{len(broken)} breaking R3, {len(unreadable)} unreadable, stages missing {missing} → {result}")
    out = root / "measurements" / f"0088-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "merged": installed.isoformat(), "window_end": WINDOW_END.isoformat(), "result": result,
        "missing": missing, "steps": steps, "exit": code,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return code


# ---------------------------------------------------------------------------
# The proof (default mode)
# ---------------------------------------------------------------------------


def argv(options) -> list[str]:
    try:
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

        t = SubprocessCLITransport(prompt="", options=options)
        t._cli_path = "claude"
        return t._build_command()
    except Exception as e:  # noqa: BLE001 - private API; any failure is "cannot answer"
        raise Unanswerable(f"the SDK's command builder could not be called: {type(e).__name__}: {e}")


def value_after(cmd: list[str], flag: str) -> str | None:
    return cmd[cmd.index(flag) + 1] if flag in cmd and cmd.index(flag) + 1 < len(cmd) else None


PROMPT_FLAGS = ("--system-prompt-file", "--append-system-prompt-file")


def carried(cmd: list[str]) -> dict[str, str]:
    """What each file flag in `cmd` hands the CLI, read now: a step's data root goes with it."""
    out: dict[str, str] = {}
    for flag in PROMPT_FLAGS:
        path = value_after(cmd, flag)
        if path is not None:
            try:
                out[flag] = Path(path).read_text(encoding="utf-8")
            except OSError as e:
                out[flag] = f"<unreadable: {e}>"
    return out


def inline(cmd: list[str]) -> bool:
    """Whether the block went into argv itself, as it did before review round 1, F1."""
    return "--append-system-prompt" in cmd or bool(value_after(cmd, "--system-prompt"))


def isolated(cmd: list[str]) -> bool:
    sources = [a for a in cmd if a.startswith("--setting-sources")]
    return sources == ["--setting-sources="] and "--strict-mcp-config" in cmd


def plant(root: Path) -> None:
    """A canary in each kind of file the reader meets, and one it must leave alone."""
    rules = root / ".claude" / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    (root / "CLAUDE.md").write_text("CANARY-ROOT-0088\n", encoding="utf-8")
    (root / ".claude" / "CLAUDE.md").write_text("CANARY-DOTCLAUDE-0088\n", encoding="utf-8")
    (root / "CLAUDE.local.md").write_text("CANARY-LOCAL-0088\n", encoding="utf-8")
    (rules / "plain.md").write_text("CANARY-PLAIN-0088\n", encoding="utf-8")
    (rules / "scoped.md").write_text('---\npaths: ["src/**"]\n---\nCANARY-SCOPED-0088\n',
                                     encoding="utf-8")


def fake_client(seen: dict, current: dict, texts: dict):
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    class FakeClient:
        def __init__(self, options):
            seen[current["label"]] = options
            try:
                texts[current["label"]] = carried(argv(options))
            except Unanswerable:
                pass  # `claims_abc` builds the argv again and says so

        async def connect(self):
            return None

        async def query(self, text):
            return None

        async def disconnect(self):
            return None

        async def receive_response(self):
            label = current["label"]
            text = f"# {label}: proof\nStatus: accepted.\n"
            if label == "review":
                text = "# Review: proof\nStatus: accepted.\n\n## Round 1\n"
            if current.get("writes"):
                (current["dir"] / f"{label}.md").write_text(text, encoding="utf-8")
                text = "written"
            yield AssistantMessage(content=[TextBlock(text=text)], model="proof",
                                   session_id=f"proof-{label}")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                is_error=False, num_turns=1, session_id=f"proof-{label}",
                                total_cost_usd=0.0, model_usage={})

    return FakeClient


async def every_kind_of_session(tmp: Path) -> tuple[dict[str, object], dict[str, dict]]:
    """Options per label -- the nine stages, `chat`, `gebo` and `estimate` -- and what each
    one's prompt files held when its client was built."""
    from coscc import integrate, policy
    from coscc import sessions as sessions_mod
    from coscc.config import Config
    from coscc.journal import Journal
    from coscc.runner import Runner
    from coscc.service import Service

    ws = tmp / "ws"
    plant(ws)
    unit = "0001_proof"
    directory = ws / ".cos" / unit
    directory.mkdir(parents=True)
    (directory / "intent.md").write_text("# Intent: proof\nStatus: accepted.\n", encoding="utf-8")
    config = Config(workspaces=(str(ws),), tools=("Read", "Bash"), working_dir=str(tmp),
                    data_dir=str(tmp / "data"))
    seen: dict[str, object] = {}
    texts: dict[str, dict] = {}
    current: dict = {"label": "", "dir": directory}
    live = sessions_mod.Sessions(config)
    live.membership = lambda _d: True
    runner = Runner(sessions=live, journal=Journal(str(ws), str(tmp / "data")))
    original = sessions_mod.ClaudeSDKClient
    sessions_mod.ClaudeSDKClient = fake_client(seen, current, texts)
    try:
        for stage in STAGES:
            current.update(label=stage, writes=not policy.grant_for(stage).app_writes_artifact)
            try:
                async for _ in runner.run(
                    workspace=str(ws), directory=directory, journal_key=str(ws), unit=unit,
                    stage=stage, artifact=f"{stage}.md", stages=STAGES, mode="manual",
                ):
                    pass
            except Exception as e:  # noqa: BLE001 - only the options are claimed here
                print(f"  ({stage} ended with {type(e).__name__}: {e})")
        current.update(label="chat", writes=False)
        service = Service(config, sessions_mod.Sessions(config))
        async for _ in service.stream(str(ws), "hello"):
            pass
        current.update(label="gebo")
        async for _ in integrate.run_gebo(
            live, tree=str(ws), workspace=str(ws), prompt="proof",
            grant=policy.grant_for("integrate"), read_also=(), lease=("fix/proof", "0" * 40),
            model=None,
        ):
            pass
        current.update(label="estimate")
        await service.create_unit(str(ws), "proof-estimate", brief="words")
        async for _ in service.propose_estimates(str(ws)):
            pass
    finally:
        sessions_mod.ClaudeSDKClient = original
    return seen, texts


def claims_abc(tmp: Path) -> bool:
    from coscc import instructions, policy
    from coscc import sessions as sessions_mod
    from coscc.config import Config

    seen, texts = asyncio.run(every_kind_of_session(tmp))
    ok = True
    labels = STAGES + ["chat", "gebo", "estimate"]
    cmds: dict[str, list[str]] = {}
    for label in labels:
        if label not in seen:
            ok &= say(False, f"(a) {label}: a session was built", "no options were recorded")
            continue
        cmds[label] = argv(seen[label])
        ok &= say(isolated(cmds[label]),
                  f"(a) {label}: argv carries --setting-sources= empty and --strict-mcp-config",
                  " ".join(a for a in cmds[label] if a.startswith("--s")))

    for stage in STAGES:
        if stage not in cmds:
            continue
        got = value_after(cmds[stage], "--tools")
        if stage in TOOLLESS:
            ok &= say(got == "", f'(b) {stage}: --tools "" although COS_TOOLS=Read,Bash', repr(got))
        else:
            want = set(policy.grant_for_step(stage, None).tools)
            ok &= say(set((got or "").split(",")) - {""} == want,
                      f"(b) {stage}: --tools is exactly its grant", f"{got!r} vs {sorted(want)}")

    block = instructions.read(tmp / "ws").text
    for stage, flag in (("idea", "--system-prompt-file"), ("impl", "--append-system-prompt-file")):
        text = texts.get(stage, {}).get(flag, "")
        ok &= say(
            text == block and all(c in text for c in ("CANARY-ROOT-0088", "CANARY-DOTCLAUDE-0088",
                                                      "CANARY-PLAIN-0088"))
            and "CANARY-SCOPED-0088" not in text and "CANARY-LOCAL-0088" not in text
            and "- .claude/rules/scoped.md (paths: src/**)" in text
            and not inline(cmds.get(stage, [])),
            f"(c) fixture, {stage}: {flag} holds the block, the scoped rule a line, the local "
            "file absent, and argv none of it",
            text[:200],
        )
    for name, prompt, flag in (("tool-less", None, "--system-prompt-file"),
                               ("preset", PRESET, "--append-system-prompt-file")):
        opts = sessions_mod._options(Config(), str(REPO), None, tools=[], system_prompt=prompt,
                                     data_dir=str(tmp))
        cmd = argv(opts)
        text = carried(cmd).get(flag, "")
        ok &= say(
            "- .claude/rules/coscc-app.md (paths: " in text
            and "- .claude/rules/ui-standard.md (paths: " in text
            and "# The coscc app" not in text and "# The UI standard" not in text
            and "## .claude/CLAUDE.md" in text and not inline(cmd),
            f"(c) this checkout, {name}: {flag} holds .claude/CLAUDE.md and the two rules as "
            f"lines only ({len(text.encode())} bytes)",
            text[:200],
        )
    return ok


def claim_d() -> bool:
    def git(*args: str) -> str:
        r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
        if r.returncode != 0:
            raise Unanswerable(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout

    if shutil.which("git") is None:
        raise Unanswerable("no git")
    base = git("merge-base", "HEAD", "origin/main").strip()

    def source(text: str) -> str:
        tree = ast.parse(text)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_prompt")
        return ast.get_source_segment(text, node) or ""

    before = source(git("show", f"{base}:coscc/runner.py"))
    now = source((REPO / "coscc" / "runner.py").read_text(encoding="utf-8"))
    return say(bool(before) and before == now,
               f"(d) build_prompt is the same as at the merge-base {base[:10]}")


# (e)'s block: past Linux's `MAX_ARG_STRLEN` (32 pages, 128 KiB at 4 KiB pages), ending in a
# word the stand-in looks for. Review round 1, F1.
BIG = 256 * 1024
BIG_CANARY = b"CANARY-BIG-0088"


class _Counting(http.server.BaseHTTPRequestHandler):
    """Counts and refuses. Never logs: the CLI sends this machine's token in a header.

    The body is read, as HTTP needs, and asked one question in memory -- does it carry
    `BIG_CANARY` -- then dropped. Nothing of a request is kept past that boolean.
    """

    hits = 0
    carrying = 0

    def _refuse(self) -> None:
        type(self).hits += 1
        length = int(self.headers.get("content-length") or 0)
        if length and BIG_CANARY in self.rfile.read(length):
            type(self).carrying += 1
        body = b'{"type":"error","error":{"type":"invalid_request_error","message":"verify_0088"}}'
        self.send_response(400)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_HEAD = _refuse

    def log_message(self, *args) -> None:  # noqa: D401 - silence, on purpose
        return None


def drop_session_env(also: tuple[str, ...] = ()) -> dict[str, str]:
    """This process runs inside an agent session; its markers are not the app's environment."""
    dropped = {k: os.environ.pop(k) for k in list(os.environ)
               if k == "CLAUDECODE" or k.startswith("CLAUDE_CODE_") or k in also}
    print(f"dropped: {sorted(dropped)}")
    return dropped


def bundled_cli() -> str:
    try:
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
        from claude_agent_sdk import ClaudeAgentOptions

        return SubprocessCLITransport(prompt="", options=ClaudeAgentOptions())._find_cli()
    except Exception as e:  # noqa: BLE001
        raise Unanswerable(f"no Claude Code CLI: {type(e).__name__}: {e}")


def argument_limit() -> str:
    """Printed, not claimed: what this machine's `execve` does with one `BIG` argument --
    the shape every session in a workspace this large had before review round 1, F1."""
    try:
        subprocess.run([sys.executable, "-c", "pass", "x" * BIG], check=False)
    except OSError as e:
        return f"refused, errno {e.errno} ({os.strerror(e.errno or 0)})"
    return "accepted"


def claim_e(tmp: Path) -> bool:
    """R9, and F1: two real CLI sessions, one per way the block is carried, each with
    `CLAUDE.md` past the argument limit, reach the stand-in with the block's last word."""
    from coscc import policy
    from coscc import sessions as sessions_mod
    from coscc.config import Config

    bundled_cli()
    print(f"  one {BIG // 1024} KiB argument to execve on this machine: {argument_limit()}")
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Counting)
    except OSError as e:
        raise Unanswerable(f"cannot bind 127.0.0.1: {e}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    saved = dict(os.environ)
    cwd = tmp / "proxy"
    cwd.mkdir()
    lines = [f"Filler line {i} of verify_0088, claim (e).\n" for i in range(BIG // 32)]
    (cwd / "CLAUDE.md").write_text("".join(lines) + BIG_CANARY.decode() + "\n", encoding="utf-8")
    size = (cwd / "CLAUDE.md").stat().st_size

    async def deny(name, data, ctx):
        from claude_agent_sdk import PermissionResultDeny

        return PermissionResultDeny(message="verify_0088 refuses every call")

    ok = True
    try:
        drop_session_env(("ANTHROPIC_BASE_URL",))
        os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
        for name, extra in (
            ("--system-prompt-file", {"tools": []}),
            ("--append-system-prompt-file", {"tools": list(policy.READ_TOOLS),
                                             "system_prompt": dict(PRESET), "can_use_tool": deny}),
        ):
            _Counting.hits = _Counting.carrying = 0
            # One per session: each runs under its own `asyncio.run`.
            live = sessions_mod.Sessions(
                Config(workspaces=(str(cwd),), data_dir=str(tmp / "data"))
            )
            live.membership = lambda _d: True

            async def go():
                async for _ in live.stream(str(cwd), "Reply with OK.", None,
                                           step=sessions_mod.StepHandle(), **extra):
                    pass

            try:
                asyncio.run(asyncio.wait_for(go(), 120))
                ended = "the session ended"
            except Exception as e:  # noqa: BLE001 - how it ends is not the claim
                ended = f"the session ended with {type(e).__name__}"
            ok &= say(_Counting.hits >= 1 and _Counting.carrying >= 1,
                      f"(e) {name}, CLAUDE.md of {size} bytes: ANTHROPIC_BASE_URL from the app's "
                      f"environment was used, {_Counting.hits} requests reached the stand-in, "
                      f"{_Counting.carrying} carrying the block's last line ({ended})",
                      f"{_Counting.hits} requests, {_Counting.carrying} carrying it ({ended})")
    finally:
        os.environ.clear()
        os.environ.update(saved)
        server.shutdown()
        server.server_close()
    return ok


def claim_g(tmp: Path) -> bool:
    """`--measure` against fixture databases the app's own journal and recorder wrote."""
    from claude_agent_sdk import SystemMessage

    from coscc import events, policy
    from coscc.data import Data
    from coscc.journal import Journal

    home = tmp / "home"
    (home / ".claude" / "skills" / "mine").mkdir(parents=True)
    (home / ".claude" / "plugins").mkdir(parents=True)
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"code-review@market": []}}), encoding="utf-8")
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"x": {}}}), encoding="utf-8")

    def init_for(stage: str, cwd: str) -> dict:
        # `code-review` is the CLI's built-in skill, named like the installed plugin: not a clash.
        return {"cwd": cwd, "mcp_servers": [], "tools": list(policy.grant_for(stage).tools),
                "plugins": [{"name": "agents-md", "path": "builtin", "source": "agents-md@builtin"}],
                "skills": ["deep-research", "code-review"], "slash_commands": ["help", "code-review"],
                "output_style": "default"}

    async def fixture(name: str, stages: list[str], bad: str = "") -> Path:
        root = tmp / name
        ship = root / "units" / "slot" / ".cos" / UNIT / "ship.md"
        ship.parent.mkdir(parents=True)
        ship.write_text("# Ship\nStatus: accepted.\n\n## What went out\n\nMerged 2026-09-26T10:00:00Z.\n",
                        encoding="utf-8")
        j = Journal(root / "work", root)
        data = Data(root)
        for stage in stages:
            run = f"run-{stage}"
            j.append({"kind": "start", "workspace": "/w", "unit": "0100_x", "stage": stage,
                      "mode": "manual", "at": "2026-10-01T00:00:00+00:00", "run": run,
                      "granted": list(policy.grant_for(stage).tools)})
            init = init_for(stage, str(root))
            if stage == bad and name == "violation":
                init["mcp_servers"] = [{"name": "microsoft-learn", "status": "connected"}]
            elif stage == bad:
                # What a loaded plugin, or a personal skill, puts into the init.
                init["slash_commands"] += ["code-review:review", "mine"]
            rec = events.Recorder(run, data, str(root), "/w", "0100_x", stage)
            rec.message(SystemMessage(subtype="init", data=init))
            await rec.close("done", None)
        return root

    after = datetime(2026, 10, 20, tzinfo=timezone.utc)
    ok = True
    for name, stages, bad, want in (
        ("pass", STAGES, "", EXIT_PASS),
        ("violation", STAGES, "review", EXIT_BROKEN),
        ("plugin-and-skill", STAGES, "plan", EXIT_BROKEN),
        ("no-ship-step", [s for s in STAGES if s != "ship"], "", EXIT_ENV),
    ):
        root = asyncio.run(fixture(name, stages, bad))
        code = measure(root, after, home)
        ok &= say(code == want, f"(g) --measure on a fixture cos.db, case {name}: exit {want}",
                  f"exit {code}")
    early = measure(tmp / "pass", datetime(2026, 10, 10, tzinfo=timezone.utc), home)
    ok &= say(early == EXIT_ENV, "(g) --measure before the window closes: exit 2", f"exit {early}")
    return ok


def run_proof() -> int:
    home = Path.home()
    before = user_state(home)
    tmp = Path(tempfile.mkdtemp(prefix="verify_0088-"))
    saved = {k: os.environ.get(k) for k in ("COS_DATA_DIR", "COS_WORKING_DIR")}
    os.environ["COS_DATA_DIR"] = str(tmp / "data")
    os.environ["COS_WORKING_DIR"] = str(tmp)
    ok = True
    try:
        ok &= claims_abc(tmp)
        ok &= claim_d()
        ok &= claim_e(tmp)
        ok &= claim_g(tmp)
    except Unanswerable as e:
        print(f"cannot answer: {e} — not a pass")
        return EXIT_ENV
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    after = user_state(home)
    ok &= say(before == after, "(f) ~/.claude settings, plugins and skills are as they were",
              json.dumps({k: (before[k], after[k]) for k in before if before[k] != after[k]}))
    print("\nall claims held" if ok else "\nat least one claim did not hold")
    return EXIT_PASS if ok else EXIT_BROKEN


# ---------------------------------------------------------------------------
# --paid
# ---------------------------------------------------------------------------


class _Keep:
    """A recorder stand-in: every message `Sessions._stream` hands it."""

    def __init__(self) -> None:
        self.messages: list = []

    def message(self, msg) -> None:
        self.messages.append(msg)


async def _paid_session(cwd: str, prompt: str, tools: list[str], model: str,
                        preset: bool, gate=None, max_turns: int = 1) -> dict:
    from claude_agent_sdk import ResultMessage, SystemMessage

    from coscc import sessions as sessions_mod
    from coscc.config import Config

    tmp = tempfile.mkdtemp(prefix="verify_0088-paid-")
    live = sessions_mod.Sessions(Config(workspaces=(cwd,), data_dir=tmp))
    live.membership = lambda _d: True
    handle = sessions_mod.StepHandle()
    handle.recorder = _Keep()
    reply = ""
    try:
        async for kind, payload in live.stream(
            cwd, prompt, None, max_turns=max_turns, tools=tools, model=model,
            **({"system_prompt": dict(PRESET)} if preset else {}),
            **({"can_use_tool": gate} if gate is not None else {}), step=handle,
        ):
            if kind == "chunk":
                reply += payload
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    msgs = handle.recorder.messages
    system = [m for m in msgs if isinstance(m, SystemMessage)]
    init = next((m.data for m in system if m.subtype == "init"), None)
    result = next((m for m in msgs if isinstance(m, ResultMessage)), None)
    return {"init": init, "subtypes": [m.subtype for m in system], "reply": reply,
            "is_error": getattr(result, "is_error", None)}


def run_paid() -> int:
    from coscc import policy

    home = Path.home()
    before = user_state(home)
    try:
        bundled_cli()
    except Unanswerable as e:
        print(e)
        return EXIT_ENV
    drop_session_env(("ANTHROPIC_BASE_URL",))
    defaults = json.loads((REPO / "coscc" / "models.json").read_text())["models"]
    try:
        settings = json.loads((home / ".claude" / "settings.json").read_text())
    except (OSError, ValueError):
        settings = {}
    rule = command = ""
    for r in (settings.get("permissions") or {}).get("allow") or []:
        inner = r[5:-1] if r.startswith("Bash(") and r.endswith(")") else ""
        hit = next((p for p in READ_ONLY if inner == p or inner.startswith((p + " ", p + ":"))), None)
        if hit:
            rule, command = r, READ_ONLY[hit]
            break
    if not rule:
        print("no Bash(...) rule in ~/.claude/settings.json permissions.allow with a read-only "
              f"prefix of {sorted(READ_ONLY)}: R8 cannot be tried")
        return EXIT_ENV

    shapes = (
        ("tool-less", "idea", [], False,
         "Does your context (system prompt, instructions, reminders, anything you were given) "
         "contain an email address? Reply with every address you see, or NONE."),
        ("read-only", "spec", list(policy.READ_TOOLS), True, "Reply with the single word OK."),
        ("impl", "impl", list(policy.grant_for("impl").tools), True, "Reply with the single word OK."),
    )
    ok = True
    ran: dict[str, list[dict]] = {}

    async def deny(name, data, ctx):
        from claude_agent_sdk import PermissionResultDeny

        return PermissionResultDeny(message="verify_0088 refuses every call")

    for name, stage, tools, preset, prompt in shapes:
        model = defaults[stage]["model"]
        try:
            got = asyncio.run(_paid_session(str(REPO), prompt, tools, model, preset,
                                            gate=deny if tools else None))
        except Exception as e:  # noqa: BLE001
            print(f"{name}: the session failed: {type(e).__name__}: {e}")
            return EXIT_ENV
        if got["init"] is None:
            print(f"{name}: no init message; is the login usable?")
            return EXIT_ENV
        ran.setdefault(model, []).append(got)
        problems = r3(got["init"], tools, got["subtypes"], home)
        ok &= say(not problems, f"(a) R3 {name} ({model}): init holds (a)-(f)", "; ".join(problems))
        if name == "tool-less":
            print(f"(d) C4, printed only — asked whether its context holds an email: {got['reply']!r}")

    asked: list[tuple[str, str]] = []

    async def record_and_deny(tool, data, ctx):
        from claude_agent_sdk import PermissionResultDeny

        asked.append((tool, str((data or {}).get("command", ""))))
        return PermissionResultDeny(message="verify_0088 refuses every call")

    marker_dir = Path(tempfile.mkdtemp(prefix="verify_0088-marker-"))
    marker = marker_dir / "marker"
    prompt = ("Use the Bash tool twice, one call each, and nothing else. "
              f"First run exactly: {command}\nThen run exactly: touch {marker}")
    try:
        got = asyncio.run(_paid_session(str(home), prompt, ["Bash"], defaults["impl"]["model"],
                                        True, gate=record_and_deny, max_turns=4))
        commands = [c for t, c in asked if t == "Bash"]
        hooks = [s for s in got["subtypes"] if s in HOOKS]
        ok &= say(command in commands and f"touch {marker}" in commands and not marker.exists()
                  and not hooks,
                  f"(b) R8 at cwd=$HOME: can_use_tool was asked for `{command}` ({rule}) and the "
                  "touch, nothing ran, no hook fired",
                  f"asked {asked}, marker {marker.exists()}, hooks {hooks}")
    except Exception as e:  # noqa: BLE001
        print(f"R8: the session failed: {type(e).__name__}: {e}")
        return EXIT_ENV
    finally:
        shutil.rmtree(marker_dir, ignore_errors=True)

    for model in sorted({v["model"] for v in defaults.values()}):
        runs = ran.get(model) or []
        good = [r for r in runs if r["init"].get("apiKeySource") == "none"
                and r["is_error"] is False and r["init"].get("model") == model]
        ok &= say(bool(good), f"(c) R10 {model}: a session ran on OAuth (apiKeySource none), "
                  "is_error False, init model the id asked for",
                  json.dumps([{k: r["init"].get(k) for k in ("apiKeySource", "model")}
                              | {"is_error": r["is_error"]} for r in runs]))
    after = user_state(home)
    ok &= say(before == after, "(f) ~/.claude settings, plugins and skills are as they were")
    print("\nall claims held" if ok else "\nat least one claim did not hold")
    return EXIT_PASS if ok else EXIT_BROKEN


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--measure", action="store_true")
    g.add_argument("--paid", action="store_true")
    args = p.parse_args()
    if args.measure:
        return measure(data_root())
    if args.paid:
        return run_paid()
    return run_proof()


if __name__ == "__main__":
    raise SystemExit(main())
