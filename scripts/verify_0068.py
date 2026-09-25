#!/usr/bin/env python3
"""Proof of `.cos/0068_updating-the-app-is-a-manual-reinstall`: updating from the board.

    uv run python scripts/verify_0068.py            # plain: claims (a) to (h)
    uv run python scripts/verify_0068.py --restart  # real uv, real processes, a real browser

Plain opens no session, spends no quota and goes to no network: a temporary data root, a
fake `uv` (a shell script) where one is run, and the app driven in-process over ASGI as
`scripts/verify_0034.py` drives it. The session is `verify_0034`'s stand-in.

`--restart` builds two wheels of `HEAD` with `scripts/build_wheel.sh --local` (the older
one with its version lowered to `0.0.0`), installs the older into a temporary uv tool
directory, writes a fake `coscc.service`, and plays systemd itself: it starts `coscc` with
`INVOCATION_ID` set and starts it again 2 s after every non-zero exit. A browser then
presses *Apply* and must see the newer version within 120 s without a reload. It needs
`uv`, `git`, `node`, chromium and the network (the trial install resolves dependencies).

Exit 0 every claim held, 1 one did not, 2 the environment could not answer. The intent's
outcome (R18) is not measured here: that is two real updates on a machine installed by
`install.sh`, recorded by a person as a `### Outcome` block.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proof_harness import EXIT_BROKEN, EXIT_ENV, EXIT_PASS, REPO, say  # noqa: E402

SHA = "0123456789abcdef" * 2 + "01234567"

# A `uv` for plain mode: `tool install --force <wheel>` writes a `coscc` into
# `$UV_TOOL_BIN_DIR` that prints the wheel's version for `--version`, exits 0 for
# `reset-password`, and otherwise plays the `0070` door until SIGTERM: it prints the setup
# token `fake` to stderr, answers `/api/health` 200, any other GET 401 without the cookie
# and 200 with it, and `POST /setup` carrying that token 303 with the cookie. A wheel named
# `*broken*` fails to install.
FAKE_UV = """#!/bin/sh
printf '%s\\n' "$*" >> "${UV_CALLS:-/dev/null}"
wheel="$4"
case "$wheel" in *broken*) echo "install failed" >&2; exit 1 ;; esac
v=$(basename "$wheel" | sed -e 's/^coscc-//' -e 's/-py3-none-any.whl$//')
mkdir -p "$UV_TOOL_BIN_DIR"
cat > "$UV_TOOL_BIN_DIR/coscc" <<EOF
#!/usr/bin/env python3
import http.server, os, sys
if sys.argv[1:] == ["--version"]:
    print("coscc $v"); sys.exit(0)
if sys.argv[1:] == ["reset-password"]:
    print("coscc: password and sessions removed"); sys.exit(0)
sys.stderr.write("coscc setup token: fake\\n"); sys.stderr.flush()
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/health" or "coscc_session=t" in (self.headers.get("Cookie") or ""):
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        else:
            self.send_response(401); self.end_headers()
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if self.path == "/setup" and "token=fake&" in body:
            self.send_response(303); self.send_header("Location", "/")
            self.send_header("Set-Cookie", "coscc_session=t; Path=/"); self.end_headers()
        else:
            self.send_response(400); self.end_headers()
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(os.environ["COS_PORT"])), H).serve_forever()
EOF
chmod +x "$UV_TOOL_BIN_DIR/coscc"
"""


def _write_exe(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _wheel(directory: Path, version: str, body: bytes = b"wheel") -> Path:
    from coscc import update

    directory.mkdir(parents=True, exist_ok=True)
    w = directory / update.wheel_name(version)
    w.write_bytes(body)
    (directory / update.SUMS).write_text(f"{hashlib.sha256(body).hexdigest()}  {w.name}\n")
    return w


class _Server:
    should_exit = False


# --- plain ------------------------------------------------------------------------------


async def run_plain(root: Path) -> bool:
    import httpx

    from coscc import board as board_reader
    from coscc import update
    from coscc.api import build
    from coscc.config import Config, from_env
    from scripts.verify_0034 import _Sessions

    ok = True
    workspace = root / "work" / "proj"
    workspace.mkdir(parents=True)
    bindir = root / "bin"
    bindir.mkdir()
    uv = _write_exe(bindir / "uv", FAKE_UV)
    config = Config(
        workspaces=(str(workspace),), working_dir=str(root / "work"), data_dir=str(root / "data"),
        update_check=False, path_env=os.environ.get("PATH", ""), home=str(root),
    )
    app = build(config)
    service = app.state.service
    u = service.updater
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proof")
    ws = str(workspace)

    # (a) R1, R2 --------------------------------------------------------------------
    body = (await client.get("/api/update")).json()
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    ok &= say(body["shape"] == "unavailable" and "checkout" in body["reason"]
              and update.UNAVAILABLE in body["reason"] and body["commit"] == head
              and body["version"] == update.running_version(),
              "(a) R1 R2 a checkout shows its version and HEAD, and why it has no button", json.dumps(body))
    shape_root = root / "shape"
    (shape_root / "cfg" / "systemd" / "user").mkdir(parents=True)
    prefix = shape_root / "tools" / "coscc"
    prefix.mkdir(parents=True)
    (prefix / "uv-receipt.toml").write_text("")
    exe = _write_exe(shape_root / "coscc", "#!/bin/sh\n")
    unit_file = shape_root / "cfg" / "systemd" / "user" / "coscc.service"

    def shape(env_extra=None, packaged=True, unit=f"ExecStart={exe}\nRestart=on-failure\n", pfx=prefix):
        unit_file.write_text(unit)
        env = {"INVOCATION_ID": "x", "XDG_CONFIG_HOME": str(shape_root / "cfg"), "PATH": str(bindir),
               "HOME": str(root), **(env_extra or {})}
        env = {k: v for k, v in env.items() if v is not None}
        return update.service_shape(from_env(env), packaged, str(pfx), str(exe))

    reasons = [
        shape(packaged=False)[1], shape({"INVOCATION_ID": None})[1],
        shape(unit="ExecStart=/elsewhere\nRestart=on-failure\n")[1],
        shape(unit=f"ExecStart={exe}\nRestart=always\n")[1],
        shape(pfx=shape_root)[1], shape({"PATH": "/nonexistent", "HOME": "/nonexistent"})[1],
    ]
    wanted = ["checkout", "INVOCATION_ID", "ExecStart", "Restart=on-failure", "uv-receipt", "uv"]
    ok &= say(all(w in r for w, r in zip(wanted, reasons)) and shape()[0] == "service",
              "(a) R2 each of the six conditions, false alone, is the reason named", str(reasons))

    # From here the app believes it is the `install.sh` shape.
    u._me = {"version": "0.12.0", "commit": SHA, "commit_label": SHA, "install": "package",
             "shape": "service", "reason": "", "build_id": f"0.12.0+{SHA}", "uv": str(uv),
             "tool_dir": str(root / "tools"), "bin_dir": str(root / "livebin")}
    body = (await client.get("/api/update")).json()
    ok &= say(body["shape"] == "service" and body["build_id"] == f"0.12.0+{SHA}" and "release" in body,
              "(a) R1 the service shape answers with its build id and both channels", json.dumps(body)[:300])

    # (b) R4, R5 --------------------------------------------------------------------
    tag, name = "v0.13.0", update.wheel_name("0.13.0")
    base = f"{update.DOWNLOAD_PREFIX}{tag}/"
    assets = [{"name": name, "browser_download_url": base + name},
              {"name": "install.sh", "browser_download_url": base + "install.sh"},
              {"name": "SHA256SUMS", "browser_download_url": base + "SHA256SUMS"}]
    yes = lambda t: "release"  # noqa: E731
    good = update.candidate({"tag_name": tag, "assets": assets}, "0.12.0", yes)
    refused = [
        update.candidate({"tag_name": tag, "assets": assets}, "0.12.0", lambda t: "prerelease"),
        update.candidate({"tag_name": tag, "assets": assets}, "0.13.0", yes),
        update.candidate({"tag_name": tag, "assets": assets[1:]}, "0.12.0", yes),
        update.candidate({"tag_name": tag, "assets": [{**assets[0], "browser_download_url": "https://evil.example/" + name}, *assets[1:]]}, "0.12.0", yes),
    ]
    ok &= say(good is not None and refused == [None] * 4,
              "(b) R4 a release is taken only when all four conditions hold", str(refused))
    cur_wheel, cur_sums = update.release_urls("v0.12.0")
    files = {
        update.LATEST_API: json.dumps({"tag_name": tag, "assets": assets}).encode(),
        base + name: b"swapped bytes",
        base + "SHA256SUMS": f"{hashlib.sha256(b'real bytes').hexdigest()}  {name}\n".encode(),
        cur_wheel: b"old", cur_sums: f"{hashlib.sha256(b'old').hexdigest()}  {update.wheel_name('0.12.0')}\n".encode(),
    }
    u._opener = lambda url: io.BytesIO(files[url])
    u._check_tag = yes
    u.check_once()
    release_dir = u.root / "release"
    ok &= say(u.release.get("reason") == "checksum mismatch" and not list(release_dir.glob("*.whl")),
              "(b) R5 a checksum that does not match is deleted and the channel says checksum mismatch", str(u.release))
    files[base + name] = b"real bytes"
    u.check_once()
    ok &= say(u.release.get("state") == "ready" and u.release.get("version") == "0.13.0"
              and update.verified_wheel(u.root / "current") is not None,
              "(b) R5 a matching one is ready, and the running release's wheel is kept in current/", str(u.release))

    # (c) R15 -----------------------------------------------------------------------
    seen = []
    real_apply = u.apply

    async def spy(channel, mode, by, token=""):
        seen.append((channel, mode, by, token))
        return {"spied": True}

    u.apply = spy
    plain = {"channel": "release", "mode": "wait", "by": "Proof", "token": ""}
    a = await client.post("/api/update/apply", json=plain)
    b = await client.post("/api/update/apply", json={**plain, "url": "https://evil.example/x.whl",
                                                       "path": "/tmp/x.whl", "version": "9.9.9", "ref": "evil"})
    u.apply = real_apply
    ok &= say(a.json() == b.json() and seen == [("release", "wait", "Proof", "")] * 2,
              "(c) R15 a URL, path, version or ref in the body changes nothing", str(seen))

    # (d) R7 to R10 -----------------------------------------------------------------
    fake = _Sessions()
    service.sessions = fake
    fake.in_flight = lambda: []
    made = await service.create_unit(ws, "is-cut-by-an-update", "words for the proof")
    (Path(made["path"]) / "intent.md").write_text("# Intent: x\nAuthor: proof. Type: fix. Status: accepted.\n")
    unit = made["unit"]
    fake.units = [unit]
    units_root = service._units_root(ws)
    gate_before = await board_reader.gate(units_root, unit, "plan", repo=None)
    begun: list[tuple[str, str]] = []

    async def recorded_apply(channel, by):
        begun.append((channel, by))
        u.state = "idle"

    u._apply = recorded_apply
    run = asyncio.create_task(client.post("/api/board/run", json={"cwd": ws, "unit": unit, "stage": "spec"}))
    for _ in range(400):
        if service.steps.all():
            break
        await asyncio.sleep(0.01)
    r = (await client.post("/api/update/apply", json={**plain})).json()
    ok &= say(r.get("state") == "pending" and len(r["pending"]["waiting"]) == 1 and not begun,
              "(d) R9 with a step running, apply waits and lists it", json.dumps(r)[:300])
    c = (await client.post("/api/update/cancel", json={"by": "Canceller"})).json()
    ok &= say(c.get("state") == "idle", "(d) R9 Huỷ chờ ends the wait")
    await client.post("/api/update/apply", json={**plain})
    service._running["i1"] = {"workspace": ws, "unit": "0099_other", "stage": "integrate", "started": "t",
                              "kind": "rebase", "turns": None, "cost_usd": None}
    listing = (await client.get("/api/update/cut-list")).json()
    actions = sorted((i["kind"], i["action"]) for i in listing["items"])
    ok &= say(actions == [("integration", "will wait"), ("step", "will be stopped")],
              "(d) R10 the cut list names the step to stop and the integration to wait for", str(actions))
    stale = await client.post("/api/update/apply", json={**plain, "mode": "now", "token": "stale"})
    ok &= say(stale.status_code == 409 and stale.json()["cut_list"]["token"] == listing["token"]
              and bool(service.steps.all()), "(d) R10 an old token is refused and nothing is cut", stale.text[:200])
    now = await client.post("/api/update/apply", json={**plain, "mode": "now", "token": listing["token"]})
    await run
    ok &= say(now.status_code == 200 and not begun, "(d) R10 the integration is still waited for", now.text[:200])
    del service._running["i1"]
    service.updater.job_ended()
    t0 = time.monotonic()
    while not begun and time.monotonic() - t0 < 10:
        await asyncio.sleep(0.05)
    ok &= say(bool(begun), "(d) R9 the apply begins within 10 s of the last job ending",
              f"{time.monotonic() - t0:.2f}s")
    rows = service._journal().records()
    ends = [x for x in rows if x["kind"] == "end" and x["unit"] == unit]
    updates = [x for x in rows if x["kind"] == "update"]
    ok &= say(len(ends) == 1 and ends[0]["outcome"] == "stopped" and ends[0].get("stopped_by") == "Proof",
              "(d) R10 the cut step has one end: stopped, stopped_by the person", str(ends))
    events = [x["event"] for x in updates]
    ok &= say(events.count("pending") >= 2 and "cancelled" in events and "cut" in events,
              "(d) R9 R10 pending, cancelled and cut are update rows", str(events))

    # (e) R11 -----------------------------------------------------------------------
    u.window = True
    codes = []
    for path, body in (("/api/board/run", {"cwd": ws, "unit": unit, "stage": "spec"}),
                       ("/api/units/integrate", {"cwd": ws, "unit": unit}),
                       ("/api/send", {"cwd": ws, "text": "hi"}),
                       ("/api/update/build-local", {"by": "Proof"})):
        codes.append((await client.post(path, json=body)).status_code)
    u.window = False
    ok &= say(codes == [503] * 4, "(e) R11 run, integrate, send and build-local are 503 in the window", str(codes))

    # (f) R12 steps 1 to 3 ----------------------------------------------------------
    del u._apply  # the real sequence from here
    update.SERVER.register(server := _Server())
    shutil.rmtree(u.root / "current")
    u.release = {"state": "ready", "version": "0.13.0"}
    served = u._opener

    def offline(url):
        raise OSError("offline")

    u._opener = offline  # step 1 fetches the running release's wheel itself; here it cannot
    await u.apply("release", "wait", "Proof")
    await u._apply_task
    u._opener = served
    ok &= say(bool(u.error) and "current" in u.error["message"] and not server.should_exit,
              "(f) R12 step 1 an empty current/ it cannot fill stops before anything changes", str(u.error))
    _wheel(u.root / "current", "0.12.0", b"old")
    broken = u.root / "release" / update.wheel_name("0.13.0")
    broken.write_bytes(b"torn")
    await u.apply("release", "wait", "Proof")
    await u._apply_task
    ok &= say(bool(u.error) and "checksum" in u.error["message"] and not server.should_exit,
              "(f) R12 step 1 a target that no longer matches its checksum stops", str(u.error))
    shutil.rmtree(u.root / "release")
    _wheel(u.root / "release", "broken-0.13.0")
    u._me["uv"] = str(uv)
    await u.apply("release", "wait", "Proof")
    await u._apply_task
    ok &= say(bool(u.error) and "trial install" in u.error["message"] and "install failed" in u.error["log_tail"]
              and not server.should_exit, "(f) R12 step 2 a trial that fails stops and shows its log", str(u.error)[:300])
    shutil.rmtree(u.root / "release")
    _wheel(u.root / "release", "0.13.0")
    real_trial = u._trial

    async def trial_then_a_job(target, log):
        problem = await real_trial(target, log)
        service.steps.claim(ws, "0098_new", "spec")
        return problem

    u._trial = trial_then_a_job
    await u.apply("release", "wait", "Proof")
    await u._apply_task
    ok &= say(u.state == "pending" and "during the trial" in (u.pending or {}).get("reason", "")
              and not server.should_exit and not u.window,
              "(f) R12 step 2 passes on a server answering 200, and step 3 goes back to waiting when work began",
              f"{u.state} {u.error}")
    service.steps.release(service.steps.get(ws, "0098_new"))
    u.cancel("Proof")

    # (g) R12 steps 8 to 10 -------------------------------------------------------
    results = {}
    for label, target, current in (("applied", "coscc-0.13.0-py3-none-any.whl", "coscc-0.12.0-py3-none-any.whl"),
                                   ("failed", "coscc-broken-py3-none-any.whl", "coscc-0.12.0-py3-none-any.whl")):
        g = root / f"finish-{label}"
        live = g / "bin"
        live.mkdir(parents=True)
        _write_exe(live / "coscc", '#!/bin/sh\necho "coscc 0.12.0"\n')
        h = update.Handoff(target_wheel=str(g / target), target_version="0.13.0", current_wheel=str(g / current),
                           current_version="0.12.0", from_version="0.12.0", uv=str(uv), tool_dir=str(g / "tools"),
                           bin_dir=str(live), log=str(g / "log"), last=str(g / "last.json"),
                           env={"PATH": os.environ.get("PATH", "")})
        child = ("import json, sys\nfrom coscc import update\n"
                 f"h = update.Handoff(**json.loads({json.dumps(json.dumps(h.__dict__))}))\n"
                 "before = set(sys.modules)\ncode = update.finish(h)\n"
                 "print(json.dumps({'code': code, 'new': sorted(set(sys.modules) - before)}))\n")
        out = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, cwd=str(REPO))
        got = json.loads(out.stdout.strip().splitlines()[-1]) if out.returncode == 0 else {}
        last = update.read_json(g / "last.json") or {}
        results[label] = (got, last.get("result"), sorted(last))
    ok &= say(all(r[0] == {"code": 75, "new": []} and r[1] == label
                  and r[2] == ["finished_at", "from", "log", "result", "to"] for label, r in results.items()),
              "(g) R12 finish: applied and failed, last.json's five keys, exit 75, nothing imported", str(results))

    # (h) R13, R16 ----------------------------------------------------------------
    (u.root / "last.json").write_text(json.dumps({"from": "0.12.0", "to": "0.13.0", "result": "applied",
                                                  "log": str(u.root / "x.log"), "finished_at": "t"}))
    from coscc.updater import Updater

    for _ in range(2):
        Updater(config, service, me=dict(u._me), start=True)
    results_rows = [x for x in service._journal().records() if x["kind"] == "update" and x["event"] == "result"]
    ok &= say(len(results_rows) == 1 and (update.verified_wheel(u.root / "current") or {}).get("version") == "0.13.0",
              "(h) R13 one result row across two starts, and the applied wheel is current/", str(results_rows))
    gate_after = await board_reader.gate(units_root, unit, "plan", repo=None)
    starts = [x for x in service._journal().records() if x["kind"] == "start"]
    ok &= say(gate_before == gate_after and len(starts) == 1,
              "(h) R16 no gate moved and no step began but the one the proof ran",
              f"{gate_before} -> {gate_after}, {len(starts)} starts")
    update.SERVER.server = None
    await client.aclose()
    return ok


# --- --restart ------------------------------------------------------------------------


def _build(tree: Path, out: Path, env: dict[str, str]) -> Path:
    done = subprocess.run(["bash", str(tree / "scripts" / "build_wheel.sh"), "--local", "--out", str(out)],
                          cwd=str(tree), capture_output=True, text=True, env=env, timeout=900)
    if done.returncode != 0:
        raise RuntimeError(f"build_wheel.sh failed in {tree}:\n{done.stderr[-3000:]}")
    return Path(done.stdout.strip().splitlines()[-1])


def run_restart(root: Path) -> int:
    from scripts.proof_harness import require_browser

    for tool in ("uv", "git", "node"):
        if shutil.which(tool) is None:
            print(f"no `{tool}` on PATH")
            return EXIT_ENV
    from coscc import update

    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
    wheels = {}
    for label in ("vA", "vB"):
        tree = root / f"tree-{label}"
        subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach", str(tree), "HEAD"],
                       check=True, capture_output=True)
        try:
            if label == "vA":
                py = tree / "pyproject.toml"
                text = py.read_text()
                version = update.running_version().split("+")[0]
                py.write_text(text.replace(f'version = "{version}"', 'version = "0.0.0"', 1))
                subprocess.run(["git", "-C", str(tree), "-c", "user.name=proof", "-c", "user.email=p@e.invalid",
                                "commit", "-qam", "proof: lower the version"], check=True)
            print(f"building {label} …")
            wheels[label] = _build(tree, root / f"out-{label}", env)
        finally:
            subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(tree)], capture_output=True)
    print(f"built {wheels['vA'].name} and {wheels['vB'].name}")

    tools, bindir, cfg, data, work = (root / n for n in ("tools", "bin", "cfg", "data", "work"))
    for d in (cfg / "systemd" / "user", data, work):
        d.mkdir(parents=True)
    uv_env = {**env, "UV_TOOL_DIR": str(tools), "UV_TOOL_BIN_DIR": str(bindir)}
    subprocess.run(["uv", "tool", "install", "--force", str(wheels["vA"])], env=uv_env, check=True, capture_output=True)
    exe = bindir / "coscc"
    (cfg / "systemd" / "user" / "coscc.service").write_text(
        f"[Service]\nType=simple\nExecStart={exe}\nRestart=on-failure\nRestartSec=2\n")
    port = 18790
    app_env = {**env, "INVOCATION_ID": "proof", "XDG_CONFIG_HOME": str(cfg), "COS_HOST": "127.0.0.1",
               "COS_PORT": str(port), "COS_DATA_DIR": str(data), "COS_WORKING_DIR": str(work),
               "COS_UPDATE_CHECK": "0", "PATH": f"{shutil.which('uv') and Path(shutil.which('uv')).parent}:{env['PATH']}"}
    local = data / "updates" / "local"
    local.mkdir(parents=True)
    shutil.copy(wheels["vB"], local / wheels["vB"].name)
    vb_version = wheels["vB"].name[len("coscc-"):-len("-py3-none-any.whl")]
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    update.write_json(local / update.MANIFEST, {"version": vb_version, "commit": commit,
                                                "sha256": update.sha256_of(local / wheels["vB"].name),
                                                "built_at": update.now()})
    # `current/` holds vA, as `_ensure_current` would have for a release.
    current = data / "updates" / "current"
    current.mkdir(parents=True)
    shutil.copy(wheels["vA"], current / wheels["vA"].name)
    va_version = wheels["vA"].name[len("coscc-"):-len("-py3-none-any.whl")]
    update.write_json(current / update.MANIFEST, {"version": va_version, "commit": "",
                                                  "sha256": update.sha256_of(wheels["vA"]), "built_at": update.now()})

    exits: list[int] = []
    stop = False
    proc = {"p": None}

    def supervise():
        while not stop:
            p = subprocess.Popen([str(exe)], env={**app_env, "COS_UPDATE_LOCAL_FROM": "proj"},
                                 stdout=open(root / "app.log", "a"), stderr=subprocess.STDOUT)
            proc["p"] = p
            code = p.wait()
            exits.append(code)
            if stop or code == 0:
                return
            time.sleep(2)

    import threading

    watcher = threading.Thread(target=supervise, daemon=True)
    watcher.start()
    ok = True
    try:
        import httpx

        for _ in range(120):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        pw, browser = require_browser()
        try:
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.click("#nav-settings")  # `0082` R9: the panel moved to Settings
            panel = page.locator("#update-panel")
            panel.wait_for(timeout=30000)
            text = panel.inner_text()
            va_sha7 = va_version.split("+g", 1)[1]
            shown_commit = va_sha7[:7] in text.split()  # `0082` D4: seven characters
            ok &= say(va_version in text and shown_commit and vb_version in text,
                      "--restart the panel shows vA with its commit, and the local channel ready", text[:400])
            page.click("#update-apply-local")
            deadline = time.monotonic() + 120
            seen = ""
            while time.monotonic() < deadline:
                try:
                    seen = page.locator("#update-running").inner_text(timeout=2000)
                    if vb_version in seen:
                        break
                except Exception:  # noqa: BLE001 - the page is reloading
                    pass
                time.sleep(2)
            if vb_version not in seen:
                try:
                    seen += " | " + page.locator("#update-panel").inner_text(timeout=2000)
                except Exception:  # noqa: BLE001
                    pass
            ok &= say(vb_version in seen, "--restart without a manual reload the panel shows vB within 120 s", seen)
        finally:
            browser.close()
            pw.stop()
        last = update.read_json(data / "updates" / "last-reported.json") or {}
        ok &= say(75 in exits and last.get("result") == "applied",
                  "--restart the process exited 75 and last-reported.json says applied", f"{exits} {last}")
    finally:
        stop = True
        if proc["p"] is not None and proc["p"].poll() is None:
            proc["p"].send_signal(signal.SIGTERM)
            try:
                proc["p"].wait(10)
            except subprocess.TimeoutExpired:
                proc["p"].kill()
    return EXIT_PASS if ok else EXIT_BROKEN


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    for tool in ("node", "git"):
        if shutil.which(tool) is None:
            print(f"no `{tool}` on PATH")
            return EXIT_ENV
    with tempfile.TemporaryDirectory(prefix="verify-0068-") as d:
        print(f"temporary root: {d}")
        if args.restart:
            code = run_restart(Path(d))
        else:
            code = EXIT_PASS if asyncio.run(run_plain(Path(d))) else EXIT_BROKEN
    if code == EXIT_PASS:
        print("verify_0068: all claims hold")
    return code


if __name__ == "__main__":
    sys.exit(main())
