"""The update state machine behind the Updates section of Settings.

`.cos/0068_updating-the-app-is-a-manual-reinstall` R3 to R13. `Service` holds one
`Updater`; the page and the routes only ever reach it through `Service`, and neither decides
anything (`.claude/rules/coscc-app.md`, "A handler that decides anything is a bug").

`idle → pending → applying → (the process exits 75)`. Three parts:

- **Checker** (R3, R4, R5): a daemon thread, started only when this install is the
  `install.sh` shape and `COS_UPDATE_CHECK` is not `0`. One `releases/latest` call at start
  and every six hours; offline or rate-limited keeps the old state and says nothing.
- **LocalBuilder** (R6): `scripts/build_wheel.sh --local` of the configured workspace's
  `origin/main`, in a throwaway worktree, only when someone presses the button.
- **Apply** (R7 to R12): wait for what is running, or cut it when the person chose to; try
  the new version beside the old one; then hand the install to `run.main` and stop uvicorn.

**It is not an approval and it starts nothing** (R16): no path here asks a gate, reads
`next` or runs a step. It stops running work only when a person chose "apply now".
"""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from coscc import auth, fetches, frontend, harness, update
from coscc.data import Data

CHECK_EVERY = 6 * 60 * 60
CHECK_TAG_TIMEOUT = 10
TRIAL_INSTALL_TIMEOUT = 300  # chosen by the plan; the spec sets none
TRIAL_HEALTHY_WITHIN = 60
TRIAL_STOP_GRACE = 5
# Chosen. The longest line of the trial's output read whole; Reflex draws progress bars.
OUTPUT_LINE_LIMIT = 1 << 20
BUILD_TIMEOUT = 15 * 60
LOG_TAIL = 40
HTTP_TIMEOUT = 30
FETCH_LOCK_WAIT = 120  # chosen: how long an apply waits for a download already under way

CHANNELS = ("release", "local")
MODES = ("wait", "now")

# `origin` of the local channel's workspace must be this repository, https or ssh (R6).
_ORIGIN = re.compile(
    r"(https://github\.com/|git@github\.com:|ssh://git@github\.com/)baodq97/coscc(\.git)?/?"
)

UPDATING = "an update is being applied"
WAITING_WARNING = "An update is waiting; starting work delays it."


class Refused(Exception):
    """A request the updater will not act on. `status` is what a route answers."""

    status = 400


class NotHere(Refused):
    """R2: this install is not the shape an update can be applied to."""

    status = 409


class Updating(Refused):
    """R11: the few seconds between the trial run and the exit."""

    status = 503


class Stale(Refused):
    """R10: the cut list changed since it was shown."""

    def __init__(self, message: str, listing: dict[str, Any]):
        super().__init__(message)
        self.listing = listing


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _urlopen(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "coscc-updater"})
    return urllib.request.urlopen(request, timeout=HTTP_TIMEOUT)  # noqa: S310 - constant URLs only


def backup_db(src: Path, dst: Path) -> bool:
    """SQLite online backup of `src` into `dst`. `False` when there is no database yet.

    `busy_timeout` is the first statement on both connections
    (`.claude/rules/coscc-app.md`, "SQLite settings are ordered").
    """
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(f".{dst.name}.part")
    a = sqlite3.connect(src, timeout=10)
    b = sqlite3.connect(part, timeout=10)
    try:
        a.execute("PRAGMA busy_timeout=10000")
        b.execute("PRAGMA busy_timeout=10000")
        a.backup(b)
    finally:
        b.close()
        a.close()
    os.replace(part, dst)
    return True


class Updater:
    def __init__(
        self,
        config: Any,
        service: Any,
        *,
        me: dict[str, Any] | None = None,
        opener: Callable[[str], Any] | None = None,
        check_tag: Callable[[str], str] | None = None,
        start: bool = True,
    ):
        self.config = config
        self.service = service
        self.root = Data(config.data_dir).root / "updates"
        self.db = Data(config.data_dir).db_path
        self._me = me
        self._opener = opener or _urlopen
        self._check_tag = check_tag or self._check_tag_with_node
        self.state = "idle"
        self.window = False
        self.pending: dict[str, Any] | None = None
        self.release: dict[str, Any] = {"state": "up-to-date"}
        self.local: dict[str, Any] = {"state": "unconfigured"}
        self.checked_at = ""
        self.error: dict[str, Any] | None = None
        self.last: dict[str, Any] | None = None
        self.log = ""
        self._build_task: asyncio.Task | None = None
        self._apply_task: asyncio.Task | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Held by a check while it writes `release/` or `current/`, and by an apply from the
        # moment it reads them until it hands off: a wheel handed to `finish` is never
        # replaced underneath it (review round 1, F3).
        self._fetch_lock = threading.Lock()
        if start:
            self.start()

    # -- who is running ------------------------------------------------------

    def me(self) -> dict[str, Any]:
        if self._me is None:
            self._me = update.identity(self.config, frontend.is_packaged())
        return self._me

    def available(self) -> bool:
        return self.me()["shape"] == "service"

    def node(self) -> str | None:
        return shutil.which("node", path=self.config.path_env or None)

    def env(self, **extra: str) -> dict[str, str]:
        """What every subprocess here gets: built from `Config`, never inherited, so a
        trial run sees no `INVOCATION_ID` and no `COS_WORKING_DIR` (R12 step 2)."""
        uv_dir = str(Path(self.me().get("uv", "uv")).parent)
        path = os.pathsep.join(p for p in (uv_dir, self.config.path_env) if p)
        return {"PATH": path, "HOME": self.config.home, **extra}

    # -- start ---------------------------------------------------------------

    def start(self) -> None:
        # A checkout never gets past the first line: no git call, no thread, no files.
        if not frontend.is_packaged() and self._me is None:
            return
        if not self.available():
            return
        self._report_last()
        self._read_channels()
        if self.config.update_check and self.node() is not None:
            self._thread = threading.Thread(target=self._check_loop, name="coscc-update-check", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _read_channels(self) -> None:
        me = self.me()
        if not self.config.update_check:
            self.release = {"state": "off", "reason": "COS_UPDATE_CHECK=0"}
        elif self.node() is None:
            self.release = {"state": "unavailable", "reason": "node cannot run, so no release can be checked"}
        else:
            found = update.verified_wheel(self.root / "release")
            if found and self._newer(found["version"]):
                self.release = {"state": "ready", **found}
        name = self.config.update_local_from
        if not name:
            self.local = {"state": "unconfigured", "reason": "no workspace is set to build from"}
        elif self.service.store is None:
            self.local = {"state": "unconfigured", "reason": "no working folder is set"}
        else:
            found = update.verified_wheel(self.root / "local")
            if found and self._local_ready(found, me):
                self.local = {"state": "ready", **found}
            else:
                self.local = {"state": "idle", "workspace": name}

    def _newer(self, version: str) -> bool:
        mine, theirs = update.public(self.me()["version"]), update.public(version)
        return bool(mine and theirs and theirs > mine)

    @staticmethod
    def _local_ready(found: dict[str, Any], me: dict[str, Any]) -> bool:
        """R6: a different `(version, commit)`, and a public version not below the running one."""
        mine, theirs = update.public(me["version"]), update.public(found["version"])
        if not mine or not theirs or theirs < mine:
            return False
        return (found["version"], found.get("commit", "")) != (me["version"], me.get("commit", ""))

    # -- R13: what happened last time ----------------------------------------

    def _report_last(self) -> None:
        last_path, reported = self.root / "last.json", self.root / "last-reported.json"
        last = update.read_json(last_path)
        if last is not None:
            self._record("result", "", **{k: last.get(k) for k in ("from", "to", "result", "log")})
            if last.get("result") == "applied":
                self._promote(str(last.get("to") or ""))
            os.replace(last_path, reported)
        shown = update.read_json(reported)
        if shown is not None:
            if shown.get("result") != "applied":
                shown = {**shown, "log_tail": update.tail(shown.get("log") or "", LOG_TAIL)}
            self.last = shown

    def _promote(self, version: str) -> None:
        """R13: the wheel just applied becomes `current/`, and its channel is emptied."""
        for channel in CHANNELS:
            found = update.verified_wheel(self.root / channel)
            if found and found["version"] == version:
                current = self.root / "current"
                shutil.rmtree(current, ignore_errors=True)
                shutil.move(str(self.root / channel), str(current))
                return

    # -- the run log ---------------------------------------------------------

    def _record(self, event: str, by: str, **extra: Any) -> None:
        """One `update` row, workspace `""` like `setting`. No working folder, no row: the
        log file and `last.json` still say what happened."""
        journal = self.service._journal()
        if journal is None:
            return
        try:
            journal.append({
                "kind": "update", "workspace": "", "unit": "", "stage": "",
                "event": event, "by": by, **extra,
            })
        except Exception:  # noqa: BLE001 - a busy log must not stop an update or a check
            pass

    # -- Checker (R3 to R5) --------------------------------------------------

    def _check_loop(self) -> None:
        while not self._stop.is_set():
            self.check_once()
            self._stop.wait(CHECK_EVERY)

    def _check_tag_with_node(self, tag: str) -> str:
        node = self.node()
        if node is None:
            raise OSError("no node")
        out = subprocess.run(
            [node, str(harness.script()), "check-tag", tag],
            capture_output=True, text=True, timeout=CHECK_TAG_TIMEOUT, env=self.env(),
        )
        return out.stdout.strip() if out.returncode == 0 else ""

    def check_once(self) -> None:
        """One check. Silent on every failure: the old state stands until the next one."""
        try:
            with self._opener(update.LATEST_API) as response:
                raw = json.loads(response.read(update.MAX_BYTES))
        except Exception:  # noqa: BLE001 - offline, 403/429, a body that is not JSON
            return
        self.checked_at = update.now()
        # Waiting or applying: the channels stay as the person saw them until the next check.
        if self.state != "idle" or not self._fetch_lock.acquire(blocking=False):
            return
        try:
            self._ensure_current()
            self._take(raw)
        finally:
            self._fetch_lock.release()

    def _take(self, raw: Any) -> None:
        cand = update.candidate(raw, self.me()["version"], self._check_tag)
        if cand is None:
            if self.release.get("state") != "ready":
                self.release = {"state": "up-to-date"}
            return
        found = update.verified_wheel(self.root / "release")
        if found and found["version"] == cand["version"]:
            self.release = {"state": "ready", **found}
            return
        self._record("found", "", to=cand["version"], channel="release")
        before = self.release
        self.release = {"state": "downloading", "version": cand["version"]}
        got = update.fetch_into(self.root / "release", cand, self._opener)
        if got["state"] == "ready":
            self.release = {"state": "ready", **(update.verified_wheel(self.root / "release") or got)}
            self._record("downloaded", "", to=cand["version"], channel="release")
        elif got["state"] == "checksum":
            self.release = {"state": "error", "reason": "checksum mismatch", "version": cand["version"]}
            self._record("checksum-failed", "", to=cand["version"], channel="release")
        else:
            self.release = before

    def _ensure_current(self) -> None:
        """R5: a release keeps its own wheel in `current/`, so R12 has something to go back to.

        The caller holds `_fetch_lock`.
        """
        version = self.me()["version"]
        if self._current_matches():
            return
        if not self._refetchable(version):
            return
        wheel_url, sums_url = update.release_urls(f"v{version}")
        cand = {"version": version, "wheel_name": update.wheel_name(version),
                "wheel_url": wheel_url, "sums_url": sums_url}
        update.fetch_into(self.root / "current", cand, self._opener)

    def _current_matches(self) -> dict[str, Any] | None:
        found = update.verified_wheel(self.root / "current")
        return found if found and found["version"] == self.me()["version"] else None

    @staticmethod
    def _refetchable(version: str) -> bool:
        """A plain `X.Y.Z` has a release to fetch its wheel from; a local build has none."""
        return "+" not in version and update.public(version) is not None

    def rollback(self) -> str:
        """`""` when R12 step 1 will find a way back, else the reason it will refuse.

        Step 1 fills an empty `current/` itself when the running version is a release, so
        the Checker is not the only road there: with `COS_UPDATE_CHECK=0` or no `node` it
        never runs (review round 1, F1). A local build running with no `current/` has no
        road at all, and the panel says so instead of offering a press step 1 refuses.
        """
        version = self.me()["version"]
        if self._refetchable(version) or self._current_matches():
            return ""
        return f"no way back: no current wheel of the running version ({version}), and a local build cannot be fetched again"

    @staticmethod
    def _offered(channel: dict[str, Any], blocked: str) -> dict[str, Any]:
        if channel.get("state") == "ready" and blocked:
            return {**channel, "state": "blocked", "reason": blocked}
        return dict(channel)
    # -- what is running (R8) ------------------------------------------------

    def jobs(self) -> list[dict[str, Any]]:
        jobs = list(self.service._update_jobs())
        if self._build_task is not None and not self._build_task.done():
            jobs.append({"kind": "build", "id": "build", "started": self.local.get("started", "")})
        return jobs

    def cut_list(self) -> dict[str, Any]:
        """R10. What "apply now" would cut, and a token for exactly this list."""
        self._require_service()
        items = [
            {**j, "action": "will wait" if j["kind"] == "integration" else "will be stopped"}
            for j in self.jobs()
        ]
        # A step's id names only its unit: the stage and the start time make another run
        # of that unit another list (review round 1, F2).
        token = hashlib.sha256(json.dumps(sorted(
            [j["id"], j.get("stage", ""), j.get("started", "")] for j in items
        )).encode()).hexdigest()[:16]
        return {"items": items, "token": token}

    def job_ended(self) -> None:
        """Told by `Service` and `Sessions` whenever a step, integration or chat turn ends."""
        if self.state != "pending":
            return
        try:
            asyncio.get_running_loop().call_soon(self._maybe_apply)
        except RuntimeError:
            pass

    def _maybe_apply(self) -> None:
        if self.state != "pending" or self.pending is None or self.jobs():
            return
        channel, by = self.pending["channel"], self.pending["by"]
        self._begin(channel, by)

    # -- the page's questions ------------------------------------------------

    def status(self) -> dict[str, Any]:
        me = self.me()
        out = {k: me.get(k, "") for k in ("version", "commit", "commit_label", "install", "shape", "reason", "build_id")}
        if me["shape"] != "service":
            out["reason"] = f"{update.UNAVAILABLE}: {me['reason']}"
            return out
        # R9's ten seconds, belt and braces: a job whose end was not told still clears here.
        self._maybe_apply()
        jobs = self.jobs() if self.state == "pending" else []
        blocked = self.rollback()
        return {
            **out,
            "state": self.state,
            "window": self.window,
            "pending": ({**self.pending, "waiting": jobs} if self.pending else None),
            "release": self._offered(self.release, blocked),
            "local": self._offered(self.local, blocked),
            "checked_at": self.checked_at,
            "error": self.error,
            "last": self.last,
            "log": self.log,
            "warning": WAITING_WARNING if self.state == "pending" else "",
        }

    def _require_service(self) -> None:
        me = self.me()
        if me["shape"] != "service":
            raise NotHere(f"{update.UNAVAILABLE}: {me['reason']}")

    def refuse_while_updating(self) -> None:
        """R11. Called before a step, an integration, a chat turn or a build begins."""
        if self.window:
            raise Updating(UPDATING)

    # -- R7, R9, R10 ---------------------------------------------------------

    async def apply(self, channel: str, mode: str, by: str, token: str = "") -> dict[str, Any]:
        self._require_service()
        self.refuse_while_updating()
        name = (by or "").strip()
        if not name:
            raise Refused("a name is required to apply an update")
        if channel not in CHANNELS:
            raise Refused(f"channel must be one of {', '.join(CHANNELS)}")
        if mode not in MODES:
            raise Refused(f"mode must be one of {', '.join(MODES)}")
        if self.state == "applying":
            raise Updating(UPDATING)
        if getattr(self, channel).get("state") != "ready":
            raise Refused(f"the {channel} channel has no version ready")
        blocked = self.rollback()
        if blocked:
            raise Refused(blocked)
        if mode == "now":
            listing = self.cut_list()
            if token != listing["token"]:
                raise Stale("the work to be stopped has changed; review it and confirm again", listing)
            for job in listing["items"]:
                if job["action"] != "will be stopped":
                    continue
                if await self._cut(job, name):
                    self._record("cut", name, cut=_describe(job), stopped_by=name)
        self.error = None
        if self.jobs():
            if self.state != "pending":
                self.state = "pending"
                self.pending = {"channel": channel, "by": name, "since": update.now()}
                self._record("pending", name, channel=channel, to=getattr(self, channel).get("version"))
            else:
                self.pending = {**(self.pending or {}), "channel": channel, "by": name}
        else:
            self._begin(channel, name)
        return self.status()

    async def _cut(self, job: dict[str, Any], by: str) -> bool:
        if job["kind"] == "build":
            if self._build_task is not None and not self._build_task.done():
                self._build_task.cancel()
                return True
            return False
        return await self.service._update_cut(job, by)

    def cancel(self, by: str) -> dict[str, Any]:
        self._require_service()
        name = (by or "").strip()
        if not name:
            raise Refused("a name is required to cancel the wait")
        if self.state != "pending":
            raise Refused("no update is waiting")
        channel = (self.pending or {}).get("channel")
        self.state, self.pending = "idle", None
        self._record("cancelled", name, channel=channel)
        return self.status()

    def _begin(self, channel: str, by: str) -> None:
        self.state = "applying"
        self.pending = None
        self._apply_task = asyncio.get_running_loop().create_task(self._apply(channel, by))

    # -- R12 -----------------------------------------------------------------

    def _fail(self, message: str, log: Path | None = None) -> None:
        self.state, self.window = "idle", False
        self.error = {"message": message, "log": str(log or ""), "log_tail": update.tail(log, LOG_TAIL) if log else ""}

    async def _apply(self, channel: str, by: str) -> None:
        me = self.me()
        logs = self.root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"{_stamp()}-update.log"
        self.log = str(log)
        if not await asyncio.to_thread(self._fetch_lock.acquire, True, FETCH_LOCK_WAIT):
            return self._fail("another download is still under way; try again later")
        handed = False
        try:
            # Step 1: from disk, right now, before anything changes.
            if update.SERVER.server is None:
                return self._fail("no uvicorn.Server to stop (the app was not started with `coscc`)")
            target = update.verified_wheel(self.root / channel)
            if target is None:
                return self._fail(f"the {channel} wheel does not match its saved checksum")
            current = self._current_matches()
            if current is None and self._refetchable(me["version"]):
                await asyncio.to_thread(self._ensure_current)
                current = self._current_matches()
            if current is None:
                return self._fail("no way back: no current wheel matches the running version")
            # Step 2: a trial run, while this one keeps serving.
            log.write_text(f"trial of {target['version']} from {channel}, pressed by {by}\n", encoding="utf-8")
            problem = await self._trial(target, log)
            if problem:
                return self._fail(problem, log)
            # Step 3: the window of R11, unless something began during the trial.
            if self.jobs():
                self.state = "pending"
                self.pending = {"channel": channel, "by": by, "since": update.now(),
                                "reason": "new work started during the trial"}
                self._record("pending", by, channel=channel, to=target["version"])
                return
            self.window = True
            # Step 4: the lifespan does not run on the real stack (`spike.md ## U5` part 2).
            await self.service.shutdown()
            await self.service.sessions.close_all()
            # Step 5.
            backup = self.root / "cos.db.bak"
            backup_db(self.db, backup)
            # Step 6.
            rollback = update.rollback_command(
                me["uv"], me["tool_dir"], me["bin_dir"], current["wheel"], str(self.db), str(backup),
            )
            with open(log, "a", encoding="utf-8") as f:
                f.write(
                    f"\nfrom: {me['version']} ({me['commit_label']})\nto: {target['version']}\n"
                    f"channel: {channel}\nby: {by}\ntarget wheel: {target['wheel']}\n"
                    f"current wheel: {current['wheel']}\n"
                    f"\nto go back by hand:\n{rollback}\n"
                )
            self._record("applying", by, channel=channel, **{"from": me["version"], "to": target["version"]})
            # Step 7.
            handoff = update.Handoff(
                target_wheel=target["wheel"], target_version=target["version"],
                current_wheel=current["wheel"], current_version=current["version"],
                from_version=me["version"], uv=me["uv"], tool_dir=me["tool_dir"],
                bin_dir=me["bin_dir"], log=str(log), last=str(self.root / "last.json"),
                env=self.env(),
            )
            handed = update.SERVER.hand_off(handoff)
            if not handed:
                self._fail("uvicorn.Server was gone before it was asked to stop", log)
        except Exception as e:  # noqa: BLE001 - the panel says what went wrong
            self._fail(f"{type(e).__name__}: {e}", log)
        finally:
            # Handed off, the lock stays held: this process exits with the wheels as they were.
            if not handed:
                self._fetch_lock.release()

    async def _trial(self, target: dict[str, Any], log: Path) -> str:
        """R12 step 2. `""` when the new version installed, answered and was stopped."""
        tmp = self.root / "tmp" / f"trial-{_stamp()}"
        tools, bin_dir, data = tmp / "tools", tmp / "bin", tmp / "data"
        proc = None
        copier = None
        try:
            data.mkdir(parents=True)
            code = await self._run(
                [self.me()["uv"], "tool", "install", "--force", target["wheel"]], log,
                TRIAL_INSTALL_TIMEOUT, UV_TOOL_DIR=str(tools), UV_TOOL_BIN_DIR=str(bin_dir),
            )
            if code != 0:
                return "the trial install failed"
            code = await self._run([str(bin_dir / "coscc"), "--version"], log, 30)
            seen = update.tail(log, 3)
            if code != 0 or f"coscc {target['version']}" not in seen:
                return f"the trial did not answer --version with {target['version']}"
            await asyncio.to_thread(backup_db, self.db, data / "cos.db")
            # `0070` step 6. The copy of the database carries this machine's password; the
            # trial clears it on the copy — never on `cos.db` — so it can set its own and
            # log in the way a person would.
            code = await self._run(
                [str(bin_dir / "coscc"), "reset-password"], log, 30, COS_DATA_DIR=str(data)
            )
            if code != 0:
                return "the trial could not run coscc reset-password on its copy of cos.db"
            port = _free_port()
            proc = await asyncio.create_subprocess_exec(
                str(bin_dir / "coscc"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=self.env(COS_HOST="127.0.0.1", COS_PORT=str(port), COS_DATA_DIR=str(data),
                             COS_UPDATE_CHECK="0"),
                start_new_session=True, limit=OUTPUT_LINE_LIMIT,
            )
            token: list[str] = []
            copier = asyncio.ensure_future(_copy_output(proc.stdout, log, token))
            failed = await _healthy(port, TRIAL_HEALTHY_WITHIN, lambda: token[-1] if token else None)
            if failed:
                return f"the trial failed at {failed} within {TRIAL_HEALTHY_WITHIN}s"
            return ""
        finally:
            if proc is not None and proc.returncode is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), TRIAL_STOP_GRACE)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            if copier is not None:
                try:
                    await asyncio.wait_for(copier, TRIAL_STOP_GRACE)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - the log is best-effort
                    copier.cancel()
            await asyncio.to_thread(shutil.rmtree, tmp, True)

    async def _run(self, cmd: list[str], log: Path, timeout: float, cwd: Path | None = None, **env: str) -> int:
        with open(log, "a", encoding="utf-8") as out:
            out.write(f"\n$ {' '.join(cmd)}\n")
            out.flush()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=out, stderr=subprocess.STDOUT, env=self.env(**env),
                    cwd=str(cwd) if cwd else None,
                )
            except OSError as e:
                out.write(f"{e}\n[exit 127]\n")
                return 127
            try:
                code = await asyncio.wait_for(proc.wait(), timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                proc.kill()
                await proc.wait()
                raise
            out.write(f"[exit {code}]\n")
        return code

    # -- LocalBuilder (R6) ---------------------------------------------------

    def build_local(self, by: str) -> dict[str, Any]:
        self._require_service()
        self.refuse_while_updating()
        name = (by or "").strip()
        if not name:
            raise Refused("a name is required to start a build")
        if self.local.get("state") == "unconfigured":
            raise Refused(f"the local channel is not configured: {self.local.get('reason', '')}")
        if self._build_task is not None and not self._build_task.done():
            raise Refused("a build is already running")
        self.local = {"state": "building", "started": update.now(), "by": name,
                      "workspace": self.config.update_local_from}
        self._build_task = asyncio.get_running_loop().create_task(self._build(name))
        return self.status()

    async def _build(self, by: str) -> None:
        logs = self.root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"{_stamp()}-build.log"
        log.write_text(f"build of origin/main, pressed by {by}\n", encoding="utf-8")
        self._record("build-start", by, channel="local")
        tmp = self.root / "tmp" / f"build-{_stamp()}"
        workspace: Path | None = None
        result = "error"
        try:
            name = self.config.update_local_from or ""
            try:
                workspace = self.service.store.path_of(name)
            except Exception as e:  # noqa: BLE001 - a bad name is a reason, not a crash
                raise _BuildFailed(f"workspace {name!r}: {e}") from e
            if not (workspace / ".git").exists():
                raise _BuildFailed(f"workspace {name!r} does not exist or is not a git checkout")
            url = await self._git(workspace, ["remote", "get-url", "origin"], log)
            if url is None or not _ORIGIN.fullmatch(url.strip()):
                raise _BuildFailed(f"the origin of {name!r} is not github.com/baodq97/coscc")
            try:
                await fetches.fetch(workspace)
            except Exception as e:  # noqa: BLE001
                raise _BuildFailed(f"fetching origin/main failed: {e}") from e
            tmp.parent.mkdir(parents=True, exist_ok=True)
            if await self._git(workspace, ["worktree", "add", "--detach", str(tmp), "origin/main"], log) is None:
                raise _BuildFailed("could not create a temporary worktree at origin/main")
            script = tmp / "scripts" / "build_wheel.sh"
            if not script.is_file():
                raise _BuildFailed("origin/main has no scripts/build_wheel.sh to build with")
            code = await self._run(["bash", str(script), "--local", "--out", str(tmp / "out")], log, BUILD_TIMEOUT, cwd=tmp)
            wheels = sorted((tmp / "out").glob("*.whl"))
            if code != 0 or len(wheels) != 1:
                raise _BuildFailed(f"the build failed (exit {code})")
            commit = (await self._git(tmp, ["rev-parse", "HEAD"], log) or "").strip()
            wheel = wheels[0]
            m = re.fullmatch(r"coscc-(.+)-py3-none-any\.whl", wheel.name)
            manifest = {
                "version": m.group(1) if m else "", "commit": commit,
                "sha256": update.sha256_of(wheel), "built_at": update.now(),
            }
            local = self.root / "local"
            local.mkdir(parents=True, exist_ok=True)
            update.free_copy(wheel, local / wheel.name)
            update.write_json(local / update.MANIFEST, manifest)
            for old in local.glob("*.whl"):
                if old.name != wheel.name:
                    old.unlink()
            found = update.verified_wheel(local)
            if found and self._local_ready(found, self.me()):
                self.local = {"state": "ready", **found, "log": str(log)}
            else:
                self.local = {"state": "idle", "workspace": name, "log": str(log),
                              "reason": "the new build is not newer than the running version"}
            result = "ok"
        except _BuildFailed as e:
            self.local = {"state": "error", "reason": str(e), "log": str(log), "log_tail": update.tail(log, LOG_TAIL)}
        except asyncio.CancelledError:
            self.local = {"state": "error", "reason": "the build was stopped", "log": str(log), "log_tail": update.tail(log, LOG_TAIL)}
            result = "cut"
        except Exception as e:  # noqa: BLE001
            self.local = {"state": "error", "reason": f"{type(e).__name__}: {e}", "log": str(log),
                          "log_tail": update.tail(log, LOG_TAIL)}
        finally:
            if workspace is not None and tmp.exists():
                await self._git(workspace, ["worktree", "remove", "--force", str(tmp)], log)
            await asyncio.to_thread(shutil.rmtree, tmp, True)
            self._record("build-end", by, channel="local", result=result, to=self.local.get("version"))
            self.job_ended()

    async def _git(self, where: Path, args: list[str], log: Path) -> str | None:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(where), *args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=self.env(),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 120)
        text = out.decode(errors="replace")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n$ git {' '.join(args)}\n{text}[exit {proc.returncode}]\n")
        return text if proc.returncode == 0 else None


class _BuildFailed(Exception):
    pass


def _describe(job: dict[str, Any]) -> dict[str, Any]:
    return {k: job[k] for k in ("kind", "unit", "stage", "session_id", "workspace", "started") if k in job}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _copy_output(stream, log: Path, token: list[str]) -> None:
    """The trial's output, line by line, into the update's log — minus the setup token.

    `0070` R4: the token lives in memory and in the process's own log, never in a file
    this app writes. The line is kept, redacted, so the log still shows it was printed.
    """
    with open(log, "a", encoding="utf-8") as out:
        while True:
            try:
                line = await stream.readline()
            except ValueError:
                # Longer than `OUTPUT_LINE_LIMIT`: take what is buffered and go on.
                line = await stream.read(OUTPUT_LINE_LIMIT)
            if not line:
                return
            text = line.decode(errors="replace").rstrip("\n")
            found = auth.SETUP_LINE.match(text)
            if found:
                token.append(found.group(1))
                text = "coscc setup token: <redacted>"
            out.write(text + "\n")
            out.flush()


def _request(port: int, method: str, path: str, body: bytes | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, Any]:
    """One request to the trial, redirects not followed. `(0, None)` when nothing answered."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        reply = conn.getresponse()
        reply.read()
        return reply.status, reply.headers
    except Exception:  # noqa: BLE001
        return 0, None
    finally:
        conn.close()


async def _healthy(port: int, within: float, token: Callable[[], str | None]) -> str:
    """`""` when the trial logged in like a person and served; else the step that failed.

    Since `0070` a build that answers `/api/health` but breaks the login would lock the
    owner out of an app with no board left to go back to, so the trial goes through the
    door: health, a refusal without a cookie, `POST /setup` with the token it printed and
    a password nobody keeps, then `/api/workspaces` and `/` with the cookie that gave.
    The password and the cookie live in this call only. `POST /setup` is sent once: a
    second would be refused, the password being set.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + within
    held = {"cookie": ""}
    failed = "/api/health"
    while loop.time() < deadline:
        failed = await _trial_step(port, token, held)
        if not failed:
            return ""
        if failed == "POST /setup":
            return failed
        await asyncio.sleep(1)
    return failed


async def _trial_step(port: int, token: Callable[[], str | None], held: dict[str, str]) -> str:
    def call(*args, **kw):
        return asyncio.to_thread(_request, port, *args, **kw)

    status, _ = await call("GET", "/api/health")
    if status != 200:
        return "/api/health"
    if not held["cookie"]:
        given = token()
        if not given:
            return "setup token"
        status, _ = await call("GET", "/api/workspaces")
        if status != 401:
            return "/api/workspaces without a cookie (not 401)"
        password = secrets.token_urlsafe(24)
        form = urllib.parse.urlencode(
            {"token": given, "password": password, "password_confirm": password}
        ).encode()
        status, headers = await call(
            "POST", "/setup", body=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        set_cookie = (headers.get("Set-Cookie") if headers is not None else None) or ""
        value = set_cookie.split(";")[0].partition("=")[2]
        if status != 303 or not value:
            return "POST /setup"
        held["cookie"] = value
    for path in ("/api/workspaces", "/"):
        status, _ = await call("GET", path, headers={"Cookie": f"{auth.COOKIE}={held['cookie']}"})
        if status != 200:
            return f"{path} with a cookie"
    return ""
