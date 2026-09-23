#!/usr/bin/env python3
"""Proof for the store's `0004_no-setting-says-which-model-runs-a-stage`.

Not `verify_0004.py`: that name belongs to `.cos/0004_silent-concurrent-loss`. The same
precedent as `verify_state_it_describes.py`.

    0  every claim held
    1  a claim did not; the line says which, with what was seen
    2  the environment could not answer: no `node`, `uv` or `git`; or `--paid` with no
       Claude login that works

Without `--paid`: no session, no quota, no network. A temporary data root, `COS_MODEL`
removed from the environment, a fake session layer and a fake `gh` first on `PATH`. The
page's own handlers are driven through Reflex's event processor, as `verify_0024.py` does,
with no browser — so the compiled page is not exercised.

With `--paid`: one `claude -p --output-format json` call per distinct id `models.json`
ships (currently two, one per model family), asking each for `modelUsage.contextWindow`.
`0031_shipped-model-defaults-cap-every-stage-at-200k`: this is the outcome that intent
defines — every stage's shipped default must report 1000000, not 200000. **Spends real
money**; what it costs has not been measured.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, say  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SLUG = "a-stage-the-proof-invented"
OPUS, SONNET = "claude-opus-5-5[1m]", "claude-sonnet-5[1m]"
OPUS_STAGES = ("idea", "intent", "spec", "plan", "review")
SONNET_STAGES = ("impl", "pr", "ship")

FAKE_GH = r'''#!{python}
"""A fake gh for verify_stage_models: CI is green, the pull request is open at HEAD."""
import json, subprocess, sys
argv = sys.argv[1:]
if argv[:2] == ["pr", "checks"]:
    print(json.dumps([{{"name": "test", "bucket": "pass"}}]))
elif argv[:2] == ["pr", "view"]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    print(json.dumps({{"state": "OPEN", "headRefOid": head, "comments": []}}))
else:
    sys.stderr.write("the fake gh does not know: " + " ".join(argv) + "\n")
    sys.exit(2)
'''


def require_environment() -> None:
    for tool in ("node", "uv", "git"):
        if shutil.which(tool) is None:
            print(f"no {tool} on PATH — this proof cannot answer without it")
            raise SystemExit(EXIT_ENV)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=proof", "-c", "user.email=proof@example.invalid", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout.strip()


def accepted(title: str, extra: str = "") -> str:
    return f"# {title}: a stage the proof invented\nAuthor: verify_stage_models.{extra} Status: accepted.\n"


class FakeSession:
    """Records the model each session was asked for, and replies with an impl record."""

    def __init__(self) -> None:
        self.models: list[str | None] = []

    async def stream(self, cwd, text, session_id=None, max_turns=1, **kw):
        self.models.append(kw.get("model"))
        yield ("chunk", accepted("Impl") + "\n## What was built\n\nnothing\n")
        yield ("done", {"session_id": f"verify-models-{len(self.models)}", "cost": {}})


class Page:
    """One browser tab's `StudioState`, driven through Reflex's own event processor."""

    TOKEN = "verify-stage-models-tab"
    DRAIN = 120.0

    def __init__(self, processor, manager, studio, root_cls) -> None:
        self.processor, self.manager = processor, manager
        self.studio, self.root_cls = studio, root_cls

    async def fire(self, handler: str, **payload) -> bool:
        from reflex.event import Event
        from reflex_base.utils.format import format_event_handler

        name = format_event_handler(self.studio.event_handlers[handler])
        await self.processor.enqueue(self.TOKEN, Event(name=name, payload=payload))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.DRAIN
        while loop.time() < deadline:
            await asyncio.sleep(0.02)
            if not self.processor._tasks and self.processor._queue.empty():
                return True
        return False

    async def read(self) -> dict:
        from reflex.istate.manager.token import BaseStateToken

        async with self.manager.modify_state(
            BaseStateToken(ident=self.TOKEN, cls=self.root_cls)
        ) as root:
            s = await root.get_state(self.studio)
            return {
                "cwd": s.cwd, "unit": s.unit_id, "next_stage": s.next_stage,
                "notice": s.notice, "error": s.error, "running": s.running,
                "rows": {r.name: (r.model, r.source) for r in s.model_rows},
            }


def script_status_names() -> list[str]:
    out = subprocess.run(
        ["node", str(REPO / ".claude" / "scripts" / "cos.mjs"), "status", "--json"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    return [s["name"] for s in json.loads(out.stdout)["stages"]]


def coscc_status() -> str:
    """`git status` of this checkout's `coscc/`. Compared before and after, not required
    to be empty: a checkout with work in progress would make an emptiness check fail for a
    reason that has nothing to do with the setting."""
    return subprocess.run(
        ["git", "status", "--porcelain", "--", "coscc/"],
        cwd=REPO, capture_output=True, text=True,
    ).stdout


async def free(root: Path, workspace: Path) -> bool:
    import httpx
    from reflex.istate.manager.memory import StateManagerMemory
    from reflex.state import State
    from reflex_base.event.processor import BaseStateEventProcessor

    from coscc import harness
    from coscc.api import build
    from coscc.config import from_env
    from coscc.data import Data
    from coscc.service import Service
    from coscc.sessions import Sessions
    from coscc.state import SERVICE, StudioState

    ok = True
    cwd = str(workspace)
    session = FakeSession()
    SERVICE.sessions = session
    config = from_env()
    app = build(config)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    journal = SERVICE._journal()

    def settings_logged() -> list[dict]:
        return journal.records("", kind="setting")

    async def table() -> dict:
        r = await client.get("/api/settings/models")
        return {"status": r.status_code, **(r.json() if r.status_code == 200 else {})}

    def by_name(t: dict) -> dict:
        return {row["name"]: (row["model"], row["source"]) for row in t.get("rows") or []}

    async def post(body: dict) -> int:
        return (await client.post("/api/settings/models", json=body)).status_code

    # The unit the step runs on: intent, spec and plan accepted, so `impl` is next.
    made = await SERVICE.create_unit(cwd, SLUG, "verify_stage_models fixture")
    unit, directory = made["unit"], Path(made["path"])
    for name, body in {
        "intent.md": accepted("Intent", " Type: feat."),
        "spec.md": accepted("Spec"), "plan.md": accepted("Plan"),
    }.items():
        (directory / name).write_text(body, encoding="utf-8")
    units_root = SERVICE._units_root(cwd)

    def asked(*args: str) -> tuple[int, str]:
        from coscc import worktrees
        tree = worktrees.path(cwd, unit, SERVICE.config.data_dir)
        repo = tree if tree.exists() else workspace
        done = subprocess.run(
            ["node", str(harness.script()), "--root", str(units_root), *args,
             "--repo", str(repo)],
            capture_output=True, text=True, timeout=60, env=harness.child_env(),
        )
        return done.returncode, done.stdout

    def gate_and_next() -> tuple:
        return asked("gate", unit, "impl"), asked("next", unit)

    # --- a (R1) -----------------------------------------------------------------
    names = script_status_names()
    first = await table()
    got = [row["name"] for row in first.get("rows") or []]
    ok &= say(first["status"] == 200 and len(got) == 9 and got[:8] == names and got[8] == "chat",
              "a GET /api/settings/models lists the 8 stages cos.mjs names, in its order, then chat",
              f"status={first['status']}, rows={got}, script={names}")

    # --- b (R2, R3, R4) ---------------------------------------------------------------
    rows = by_name(first)
    agents = {row["agents"] for row in first.get("rows") or []}
    want = {**{s: (OPUS, "default") for s in OPUS_STAGES},
            **{s: (SONNET, "default") for s in SONNET_STAGES}}
    all_1m = all(rows.get(s, ("", ""))[0].endswith("[1m]") for s in names)
    ok &= say(agents == {1} and all(rows.get(s) == w for s, w in want.items())
              and rows.get("chat", ("", ""))[1] in ("none", "COS_MODEL") and all_1m,
              "b every row has 1 agent; the shipped defaults are as answered; chat has none "
              "of its own; all 8 stages default to a [1m] id",
              f"agents={agents}, rows={rows}")

    # --- c (R4) -------------------------------------------------------------------
    fallback = Service(from_env({**os.environ, "COS_MODEL": "proof-fallback"}), Sessions(config))
    fb = by_name(await fallback.stage_models())
    ok &= say(all(fb.get(s) == rows.get(s) for s in names)
              and fb.get("chat") == ("proof-fallback", "COS_MODEL"),
              "c COS_MODEL changes only chat, the one row with no default",
              f"rows={fb}")

    # --- d (R6, R8) and i, before ---------------------------------------------------
    before_gate = gate_and_next()
    before_tree = coscc_status()
    logged = len(settings_logged())
    status = await post({"name": "impl", "model": "proof-model-x"})
    after = by_name(await table())
    changed = sorted(k for k in set(rows) | set(after) if rows.get(k) != after.get(k))
    ok &= say(status == 200 and changed == ["impl"]
              and after["impl"] == ("proof-model-x", "override"),
              "d a POST changes exactly one row, impl, to an override",
              f"status={status}, changed={changed}, impl={after.get('impl')}")
    ok &= say(coscc_status() == before_tree,
              "d no file under coscc/ changed", coscc_status())
    after_d_gate = gate_and_next()

    # --- e (R6, a restart) ----------------------------------------------------------
    again = by_name(await Service(from_env(), Sessions(config)).stage_models())
    ok &= say(again.get("impl") == ("proof-model-x", "override"),
              "e a new Service on the same data root still has the override",
              f"impl={again.get('impl')}")

    # --- f (R9, outcomes 3 and 4): through the page's own handlers -------------------
    manager = StateManagerMemory()
    processor = BaseStateEventProcessor().configure(state_manager=manager)
    async with processor:
        page = Page(processor, manager, StudioState, State)
        drained = await page.fire("load") and await page.fire("choose_workspace", path=cwd)
        drained &= await page.fire("edit_model", name="impl", value=SONNET)
        drained &= await page.fire("save_model", name="impl")
        seen = await page.read()
        ok &= say(drained and seen["rows"].get("impl") == (SONNET, "override") and not seen["error"],
                  f"f Settings saved impl as {SONNET} through the page's handlers",
                  f"rows={seen['rows']}, notice={seen['notice']!r}, error={seen['error']!r}")
        drained = await page.fire("open_unit", unit=unit)
        seen = await page.read()
        offered = seen["next_stage"]
        drained &= await page.fire("run_step")
        seen = await page.read()
    start = [r for r in journal.records(SERVICE._journal_key(cwd), unit, kind="start")
             if r.get("stage") == "impl"]
    ok &= say(drained and offered == "impl" and session.models == [SONNET]
              and bool(start) and start[-1].get("model") == SONNET
              and start[-1].get("model_source") == "override",
              f"f the impl step pressed on the board ran on {SONNET}, and its start record says so",
              f"offered={offered!r}, asked={session.models}, start={start[-1:]}, "
              f"notice={seen['notice']!r}, error={seen['error']!r}")
    now = by_name(await table())
    ok &= say(all(now.get(s) == rows.get(s) for s in names if s != "impl")
              and now.get("chat") == rows.get("chat"),
              "f every other row is as it was in b", f"rows={now}")

    # --- g (R7) and i, after ---------------------------------------------------------
    status = await post({"name": "impl"})
    ok &= say(status == 200 and by_name(await table()).get("impl") == (SONNET, "default"),
              "g removing the override puts impl back on its default",
              f"status={status}, impl={by_name(await table()).get('impl')}")
    after_g_gate = gate_and_next()
    ok &= say(before_gate == after_d_gate,
              "i gate and next say the same before and after d",
              f"before={before_gate}, after={after_d_gate}")
    # After f the unit has an impl.md, so what `next` says moves on; that is the step, not
    # the setting. So the comparison for g is across g alone.
    after_g_again = gate_and_next()
    status = await post({"name": "impl", "model": "proof-model-y"})
    after_set = gate_and_next()
    await post({"name": "impl"})
    ok &= say(after_g_gate == after_g_again == after_set,
              "i gate and next say the same across a set and a removal",
              f"{after_g_gate} / {after_set}")

    # --- j (a trace) -----------------------------------------------------------------
    records = settings_logged()[logged:]
    ok &= say(len(records) == 5 and all("old" in r and "new" in r for r in records)
              and [r["new"] for r in records] == ["proof-model-x", SONNET, None, "proof-model-y", None],
              "j each successful POST left exactly one setting record with old and new",
              f"records={records}")

    # --- k (R2): an override set before the [1m] defaults shipped still wins, at its
    # old id. This does not go through POST, so it leaves no `setting` record — j still
    # counts 5.
    data = Data(config.data_dir)
    with data.write() as conn:
        conn.execute("INSERT INTO prefs (key, value) VALUES ('model:pr', '\"claude-sonnet-5\"')")
    old_override = await table()
    by_k = by_name(old_override)
    rest_unchanged = all(by_k.get(s) == rows.get(s) for s in names if s != "pr")
    ok &= say(old_override["status"] == 200
              and by_k.get("pr") == ("claude-sonnet-5", "override") and rest_unchanged,
              "k an override saved before the [1m] defaults shipped still wins, at the old id",
              f"pr={by_k.get('pr')}, rest_unchanged={rest_unchanged}, rows={by_k}")
    with data.write() as conn:
        conn.execute("DELETE FROM prefs WHERE key = 'model:pr'")

    # --- h (R11) ---------------------------------------------------------------------
    data = Data(config.data_dir)
    with data.write() as conn:
        conn.execute("INSERT INTO prefs (key, value) VALUES ('model:plan', '{')")
        conn.execute("INSERT INTO prefs (key, value) VALUES ('model:bogus', '\"x\"')")
    broken = await table()
    problems = " ".join(broken.get("problems") or [])
    ok &= say(broken["status"] == 200 and by_name(broken).get("plan") == (OPUS, "default")
              and "model:plan" in problems and "bogus" in problems,
              "h a broken and an unknown override are named in problems and change nothing",
              f"status={broken['status']}, plan={by_name(broken).get('plan')}, problems={problems!r}")
    bad = await post({"name": "plan", "model": "  "})
    ok &= say(bad == 400, "h an empty model is refused with 400", f"status={bad}")

    await client.aclose()
    return bool(ok)


async def paid() -> int:
    """One `claude -p --output-format json` call per distinct id `coscc/models.json`
    ships (currently two: `OPUS` and `SONNET`, both `[1m]`), reading what it reports for
    `modelUsage[...].contextWindow` — the measurement
    `0031_shipped-model-defaults-cap-every-stage-at-200k`'s outcome is defined by: every
    stage's shipped default must resolve to a model reporting 1000000, not 200000.

    This calls the `claude` on `PATH` directly, not through `Sessions.stream` (R5: a key
    the SDK strips the `[1m]` suffix from would fail a `model in used` check that has
    nothing to do with the outcome, and `Sessions.stream` never surfaces `contextWindow`
    in the first place — `coscc/sessions.py` keeps only the keys of `model_usage`). It
    therefore measures the CLI on `PATH`, which may not be the SDK build the app loads —
    a deliberate trade-off (plan Risk 6)."""
    from coscc import models

    if shutil.which("claude") is None:
        print("no claude on PATH — this proof cannot answer without it")
        return EXIT_ENV

    defaults, problems = models.load_defaults()
    names = script_status_names()
    if problems or set(defaults) != set(names):
        print(f"models.json did not resolve cleanly: problems={problems}, "
              f"defaults={sorted(defaults)}, stages={sorted(names)}")
        return EXIT_ENV

    ids = sorted(set(defaults.values()))
    ok = True
    passed_ids: set[str] = set()
    for model_id in ids:
        try:
            result = subprocess.run(
                ["claude", "-p", "Reply with the single word: ok", "--model", model_id,
                 "--output-format", "json"],
                cwd=REPO, capture_output=True, text=True, timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as e:  # a login or a network that
            # does not work is the environment, not an outcome the proof measures
            print(f"{model_id}: claude could not be run: {type(e).__name__}: {e}")
            return EXIT_ENV
        # A nonzero exit is read the same way as a zero one below: the CLI reporting
        # `is_error` in its own JSON, on either exit code, is the outcome failing
        # (Risk 1), not the environment refusing to run. Only stdout that carries no
        # such message — on any exit code — is read as the environment (review round 1,
        # F1: a nonzero exit used to short-circuit to EXIT_ENV before this JSON was ever
        # read, so an account that cannot use a `[1m]` id by exiting nonzero would have
        # been misreported as an environment problem instead of Risk 1).
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = None
        # `claude_code_version` 2.1.280 (measured 2026-09-23) wraps `--output-format json`
        # in a JSON array of messages rather than the single result object earlier
        # versions documented; the one carrying `modelUsage` is `type: "result"`.
        outcome = payload
        if isinstance(payload, list):
            outcome = next(
                (item for item in reversed(payload)
                 if isinstance(item, dict) and item.get("type") == "result"), None,
            )
        if not isinstance(outcome, dict):
            if result.returncode != 0:
                print(f"{model_id}: claude exited {result.returncode}, and stdout "
                      f"carried no result message\n"
                      f"stderr: {result.stderr[:500]}\nstdout: {result.stdout[:500]}")
            else:
                print(f"{model_id}: no result message in the CLI's JSON output\n"
                      f"stdout: {result.stdout[:500]}")
            return EXIT_ENV
        if outcome.get("is_error"):
            # The CLI itself reported an error for this model id — that is the outcome
            # failing, not the environment (Risk 1).
            print(f"{model_id}: the CLI reported an error for this model: "
                  f"{outcome.get('result')}")
            return EXIT_BROKEN
        if result.returncode != 0:
            # Exited nonzero, parsed to a result message, but that message did not say
            # `is_error` — an exit/JSON combination nobody has measured. Not classified
            # as either outcome; read as the environment so it does not silently pass.
            print(f"{model_id}: claude exited {result.returncode} but its result "
                  f"message did not report is_error: {outcome.get('result')!r}\n"
                  f"stderr: {result.stderr[:500]}")
            return EXIT_ENV
        usage = outcome.get("modelUsage") or {}
        bare = model_id.removesuffix("[1m]")
        windows = {key: (stats or {}).get("contextWindow") for key, stats in usage.items()}
        matched = any(k in (model_id, bare) and w == 1000000 for k, w in windows.items())
        ok &= say(matched, f"paid {model_id} resolves to a model reporting contextWindow 1000000")
        print(f"      {model_id}: modelUsage keys and contextWindow: {windows}")
        if matched:
            passed_ids.add(model_id)

    n = sum(1 for stage in names if defaults.get(stage) in passed_ids)
    ok &= n == len(names)
    print(f"paid {n}/{len(names)} stages resolve to a model reporting contextWindow 1000000")
    return EXIT_PASS if ok else EXIT_BROKEN


def main() -> int:
    require_environment()
    if "--paid" in sys.argv[1:]:
        return asyncio.run(paid())
    with tempfile.TemporaryDirectory(prefix="verify-stage-models-") as d:
        root = Path(d)
        fakebin = root / "fakebin"
        fakebin.mkdir()
        gh = fakebin / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
        gh.chmod(0o755)
        workspace = root / "work" / "proj"
        workspace.mkdir(parents=True)
        git(workspace, "init", "-q", "-b", "main")
        git(workspace, "commit", "-q", "--allow-empty", "-m", "the only commit")
        # Before `coscc` is imported: `coscc/state.py` builds its `SERVICE` at import.
        os.environ["PATH"] = f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"
        os.environ["COS_DATA_DIR"] = str(root / "data")
        os.environ["COS_WORKING_DIR"] = str(root / "work")
        os.environ["COS_WORKSPACES"] = str(workspace)
        os.environ.pop("COS_MODEL", None)
        for name, fallback in (("COS_PORT", "8790"), ("COS_HOST", "127.0.0.1")):
            if not os.environ.get(name, "").strip():
                os.environ[name] = fallback
        print(f"temporary data root: {root}")
        ok = asyncio.run(free(root, workspace))
        return EXIT_PASS if ok else EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
